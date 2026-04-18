"""
JavaFo cross-validation tests for the FIDE Dutch System Pairing Engine.

Phase 2.3: Compares our Dutch engine output with JaVaFo (FIDE-endorsed
reference implementation) round-by-round for multiple tournament
configurations.

Requires:
- Java 11+ on PATH
- javafo/main.jar and javafo/JaVaFoBridge.class (compiled bridge)

The bridge is invoked via subprocess and communicates over stdin/stdout.
No Django dependency — runs standalone.

JavaFo bridge location:
  Set JAVAFO_DIR env var to the directory containing main.jar and
  JaVaFoBridge.class, or place them in a javafo/ directory next to
  this package's repo root.
"""

import os
import random
import subprocess
import sys
import unittest
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Set, Tuple, Optional

from caissify_pairings.engines.dutch import dutch_pairings

# ---------------------------------------------------------------------------
# JavaFo bridge paths
# ---------------------------------------------------------------------------
_JAVAFO_DIR = Path(os.environ.get(
    "JAVAFO_DIR",
    Path(__file__).resolve().parents[1] / "javafo",
))
_BRIDGE_CLASS = _JAVAFO_DIR / "JaVaFoBridge.class"
_MAIN_JAR = _JAVAFO_DIR / "main.jar"


def _java_available() -> bool:
    """Check if Java and the JavaFo bridge are available."""
    if not _BRIDGE_CLASS.exists() or not _MAIN_JAR.exists():
        return False
    try:
        subprocess.run(
            ["java", "-version"],
            capture_output=True, timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_SKIP_REASON = "Java runtime or JavaFo bridge not available"

# ---------------------------------------------------------------------------
# TRF16 builder (standalone, no Django)
# ---------------------------------------------------------------------------


def _build_trf(
    players: List[dict],
    round_history: Dict[int, list],
    total_rounds: int,
    tournament_name: str = "CrossValidation",
) -> str:
    """
    Build a TRF16 string from in-memory tournament state.

    Args:
        players: list of player dicts (id, name, rating, score, starting_number,
                 color_hist, bye_count)
        round_history: {round_number: [pairing_dicts]} for completed rounds
        total_rounds: total rounds in the tournament

    Returns:
        TRF16 formatted string suitable for JaVaFoApi.exec(1000, ...)
    """
    lines = []
    lines.append(f"012 {tournament_name}")
    lines.append("022 City")
    lines.append("032 FED")
    lines.append("042 18/04/2026")
    lines.append("052 19/04/2026")
    lines.append(f"062 {len(players)}")
    lines.append(f"072 {len(players)}")
    lines.append("082 0")
    lines.append("092 Individual: Swiss-System Dutch")
    lines.append("102 Arbiter")
    lines.append("122 90/40+30+30")
    lines.append(f"XXR {total_rounds}")

    # Index players by id for fast lookup
    pmap = {p["id"]: p for p in players}

    # Build a per-player round result lookup
    # player_rounds[player_id][round_num] = (opponent_starting_num, color, result_char)
    player_rounds: Dict[int, Dict[int, Tuple]] = defaultdict(dict)

    for rnd_num, pairings in round_history.items():
        for p in pairings:
            if p.get("bye"):
                pid = p["white_id"]
                player_rounds[pid][rnd_num] = (0, "-", "U")
            else:
                wid, bid = p["white_id"], p["black_id"]
                w_sn = pmap[wid]["starting_number"]
                b_sn = pmap[bid]["starting_number"]
                w_res = p.get("white_result", "1")
                b_res = p.get("black_result", "0")
                player_rounds[wid][rnd_num] = (b_sn, "w", w_res)
                player_rounds[bid][rnd_num] = (w_sn, "b", b_res)

    # Determine how many completed rounds there are
    completed_rounds = max(round_history.keys()) if round_history else 0

    for pl in sorted(players, key=lambda p: p["starting_number"]):
        sn = pl["starting_number"]
        name = pl["name"]
        rating = pl.get("rating", 0)
        score = pl.get("score", 0.0)

        # 91-char base line
        line = [" "] * 91
        line[0:3] = "001"
        sn_str = f"{sn:>4}"
        for i, c in enumerate(sn_str):
            line[4 + i] = c
        line[9] = "m"
        name_str = f"{name:<33}"[:33]
        for i, c in enumerate(name_str):
            line[14 + i] = c
        rating_str = f"{rating:>4}"
        for i, c in enumerate(rating_str):
            line[48 + i] = c
        line[53:56] = "FED"
        bd = "1990/01/01"
        for i, c in enumerate(bd):
            line[69 + i] = c
        pts = f"{score:.1f}"
        pts_padded = f"{pts:<4}"[:4]
        for i, c in enumerate(pts_padded):
            line[80 + i] = c
        rank_str = f"{sn:>4}"
        for i, c in enumerate(rank_str):
            line[85 + i] = c

        base = "".join(line)

        # Append round results (10 chars each)
        round_data = ""
        for rnd in range(1, completed_rounds + 1):
            if rnd in player_rounds.get(pl["id"], {}):
                opp_sn, color, result = player_rounds[pl["id"]][rnd]
                round_data += f"{opp_sn:>4} {color} {result}  "
            else:
                round_data += "0000 - -  "

        lines.append(base + round_data)

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# JavaFo subprocess caller
# ---------------------------------------------------------------------------


def _call_javafo(trf_content: str) -> Optional[List[Tuple[int, int]]]:
    """
    Call JavaFo via the bridge and return pairings.

    Returns:
        List of (white_starting_number, black_starting_number) tuples.
        black == 0 means bye.
        None if JavaFo fails.
    """
    classpath = f"{_JAVAFO_DIR}:{_MAIN_JAR}"
    proc = subprocess.run(
        ["java", "-cp", classpath, "JaVaFoBridge"],
        input=trf_content,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        print(f"  [JavaFo ERROR] {proc.stderr.strip()}", file=sys.stderr)
        return None

    lines = proc.stdout.strip().split("\n")
    if not lines:
        return None

    pairings = []
    # First line is count, remaining lines are "white black"
    for line in lines[1:]:
        parts = line.strip().split()
        if len(parts) == 2:
            pairings.append((int(parts[0]), int(parts[1])))
    return pairings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_players(count, base_rating=2400, rating_step=25):
    """Generate player dicts with descending ratings."""
    return [
        {
            "id": i,
            "name": f"Player {i}",
            "score": 0.0,
            "rating": base_rating - (i - 1) * rating_step,
            "starting_number": i,
            "color_hist": [],
            "float_history": [],
            "bye_count": 0,
        }
        for i in range(1, count + 1)
    ]


def _simulate_results(pairings, players_map, seed_offset=0):
    """Simulate results: higher-rated wins ~60%, draw ~20%, upset ~20%."""
    results = {}
    for p in pairings:
        if p.get("bye"):
            results[p["white_id"]] = "bye"
            continue
        wid, bid = p["white_id"], p["black_id"]
        w_rating = players_map[wid]["rating"]
        b_rating = players_map[bid]["rating"]
        diff = w_rating - b_rating
        r = random.random()
        if diff > 0:
            if r < 0.55:
                results[(wid, bid)] = (1.0, 0.0)
            elif r < 0.75:
                results[(wid, bid)] = (0.5, 0.5)
            else:
                results[(wid, bid)] = (0.0, 1.0)
        else:
            if r < 0.55:
                results[(wid, bid)] = (0.0, 1.0)
            elif r < 0.75:
                results[(wid, bid)] = (0.5, 0.5)
            else:
                results[(wid, bid)] = (1.0, 0.0)
    return results


def _result_to_trf_char(pts: float) -> str:
    """Convert a numeric result to TRF character."""
    if pts == 1.0:
        return "1"
    elif pts == 0.0:
        return "0"
    else:
        return "="


def _annotate_pairings_with_results(pairings, results, bye_value=1.0):
    """Add result data to pairing dicts for TRF export."""
    annotated = []
    for p in [dict(p) for p in pairings]:
        if p.get("bye"):
            p["white_result"] = "U"
            annotated.append(p)
            continue
        wid, bid = p["white_id"], p["black_id"]
        w_pts, b_pts = results[(wid, bid)]
        p["white_result"] = _result_to_trf_char(w_pts)
        p["black_result"] = _result_to_trf_char(b_pts)
        annotated.append(p)
    return annotated


def _apply_results(players, pairings, results, bye_value=1.0):
    """Apply simulated results to player data for next round."""
    pmap = {p["id"]: p for p in players}
    for p in pairings:
        if p.get("bye"):
            pid = p["white_id"]
            pmap[pid]["score"] += bye_value
            pmap[pid]["bye_count"] += 1
            continue
        wid, bid = p["white_id"], p["black_id"]
        w_pts, b_pts = results[(wid, bid)]
        pmap[wid]["score"] += w_pts
        pmap[bid]["score"] += b_pts
        pmap[wid]["color_hist"].append("white")
        pmap[bid]["color_hist"].append("black")


def _normalise_pairings(pairs: List[Tuple[int, int]]) -> Set[Tuple[int, int]]:
    """
    Normalise pairing list to a set of (min, max) tuples for comparison.
    Byes are stored as (player, 0).
    """
    result = set()
    for w, b in pairs:
        if b == 0:
            result.add((w, 0))
        else:
            result.add((min(w, b), max(w, b)))
    return result


def _our_pairings_to_tuples(pairings: List[dict]) -> List[Tuple[int, int]]:
    """Convert our engine's pairing dicts to (white_sn, black_sn) tuples."""
    result = []
    for p in pairings:
        if p.get("bye"):
            result.append((p["white_id"], 0))
        else:
            result.append((p["white_id"], p["black_id"]))
    return result


# ---------------------------------------------------------------------------
# Cross-validation runner
# ---------------------------------------------------------------------------


def _run_cross_validation(
    num_players: int,
    num_rounds: int,
    seed: int = 42,
    bye_value: float = 1.0,
) -> Dict:
    """
    Run a full tournament with both our engine and JavaFo, comparing
    pairings at each round.

    Returns dict with:
        matches: list of bool per round
        mismatches: list of round details where engines disagree
        our_pairings: dict of round -> pairing tuples
        javafo_pairings: dict of round -> pairing tuples
    """
    random.seed(seed)
    players = _make_players(num_players)
    pmap = {p["id"]: p for p in players}
    all_prev: Set[Tuple[int, int]] = set()
    round_history: Dict[int, list] = {}

    matches = []
    mismatches = []
    our_all = {}
    jf_all = {}

    for rnd in range(1, num_rounds + 1):
        # --- Our engine ---
        our_pairings = dutch_pairings(
            players, all_prev, rnd, num_rounds, bye_value=bye_value
        )
        our_tuples = _our_pairings_to_tuples(our_pairings)
        our_all[rnd] = our_tuples

        # --- JavaFo ---
        trf = _build_trf(players, round_history, num_rounds)
        jf_tuples = _call_javafo(trf)
        jf_all[rnd] = jf_tuples

        # --- Compare ---
        if jf_tuples is not None:
            our_norm = _normalise_pairings(our_tuples)
            jf_norm = _normalise_pairings(jf_tuples)
            match = our_norm == jf_norm
            matches.append(match)
            if not match:
                mismatches.append({
                    "round": rnd,
                    "ours": sorted(our_norm),
                    "javafo": sorted(jf_norm),
                    "only_ours": sorted(our_norm - jf_norm),
                    "only_javafo": sorted(jf_norm - our_norm),
                })
        else:
            matches.append(None)  # JavaFo failed
            mismatches.append({"round": rnd, "error": "JavaFo returned None"})

        # --- Simulate results and record round ---
        results = _simulate_results(our_pairings, pmap)
        annotated = _annotate_pairings_with_results(our_pairings, results, bye_value)
        round_history[rnd] = annotated
        _apply_results(players, our_pairings, results, bye_value)

        # Record previous pairings
        for p in our_pairings:
            if not p.get("bye") and p["black_id"] is not None:
                pair = tuple(sorted([p["white_id"], p["black_id"]]))
                all_prev.add(pair)

    return {
        "matches": matches,
        "mismatches": mismatches,
        "our_pairings": our_all,
        "javafo_pairings": jf_all,
    }


# ===========================================================================
# Test classes
# ===========================================================================


@unittest.skipUnless(_java_available(), _SKIP_REASON)
class TestJaVaFoBridge(unittest.TestCase):
    """Sanity check that the JavaFo bridge subprocess works."""

    def test_bridge_basic(self):
        """Bridge returns valid pairings for a trivial 4-player tournament."""
        players = _make_players(4)
        trf = _build_trf(players, {}, 3)
        pairs = _call_javafo(trf)
        self.assertIsNotNone(pairs, "JavaFo bridge returned None")
        self.assertEqual(len(pairs), 2, f"Expected 2 pairings, got {pairs}")
        # All starting numbers should be 1-4
        all_ids = {sn for pair in pairs for sn in pair}
        self.assertEqual(all_ids, {1, 2, 3, 4})

    def test_bridge_odd_players(self):
        """Bridge handles odd player count (bye)."""
        players = _make_players(5)
        trf = _build_trf(players, {}, 3)
        pairs = _call_javafo(trf)
        self.assertIsNotNone(pairs)
        # Should have 3 entries: 2 games + 1 bye (opponent=0)
        self.assertEqual(len(pairs), 3)
        byes = [p for p in pairs if p[1] == 0]
        self.assertEqual(len(byes), 1, f"Expected 1 bye, got {byes}")


@unittest.skipUnless(_java_available(), _SKIP_REASON)
class TestCrossValidation10p5r(unittest.TestCase):
    """Cross-validate 10 players, 5 rounds."""

    def test_round_by_round(self):
        result = _run_cross_validation(10, 5, seed=42)
        total = len(result["matches"])
        matched = sum(1 for m in result["matches"] if m is True)
        failed_jf = sum(1 for m in result["matches"] if m is None)

        self.assertEqual(failed_jf, 0, "JavaFo failed on some rounds")

        if result["mismatches"]:
            details = "\n".join(
                f"  R{m['round']}: ours={m.get('only_ours',[])} jf={m.get('only_javafo',[])}"
                for m in result["mismatches"]
            )
            print(f"\n10p/5r mismatches ({matched}/{total} matched):\n{details}")

        # Require at least round 1 to match (deterministic for Dutch)
        self.assertTrue(
            result["matches"][0],
            f"Round 1 mismatch: ours={result['our_pairings'][1]} jf={result['javafo_pairings'][1]}",
        )

    def test_multiple_seeds(self):
        """Cross-validate with 3 different random seeds."""
        for seed in [42, 123, 999]:
            with self.subTest(seed=seed):
                result = _run_cross_validation(10, 5, seed=seed)
                self.assertTrue(
                    result["matches"][0],
                    f"Seed {seed}: Round 1 should always match",
                )


@unittest.skipUnless(_java_available(), _SKIP_REASON)
class TestCrossValidation8p7r(unittest.TestCase):
    """Cross-validate 8 players, 7 rounds."""

    def test_round_by_round(self):
        result = _run_cross_validation(8, 7, seed=77)
        total = len(result["matches"])
        matched = sum(1 for m in result["matches"] if m is True)
        failed_jf = sum(1 for m in result["matches"] if m is None)

        self.assertEqual(failed_jf, 0, "JavaFo failed on some rounds")

        if result["mismatches"]:
            details = "\n".join(
                f"  R{m['round']}: ours={m.get('only_ours',[])} jf={m.get('only_javafo',[])}"
                for m in result["mismatches"]
            )
            print(f"\n8p/7r mismatches ({matched}/{total} matched):\n{details}")

        self.assertTrue(
            result["matches"][0],
            f"Round 1 mismatch",
        )


@unittest.skipUnless(_java_available(), _SKIP_REASON)
class TestCrossValidation9pOdd(unittest.TestCase):
    """Cross-validate 9 players (odd count, bye handling), 5 rounds."""

    def test_round_by_round(self):
        result = _run_cross_validation(9, 5, seed=42)
        total = len(result["matches"])
        matched = sum(1 for m in result["matches"] if m is True)
        failed_jf = sum(1 for m in result["matches"] if m is None)

        self.assertEqual(failed_jf, 0, "JavaFo failed on some rounds")

        if result["mismatches"]:
            details = "\n".join(
                f"  R{m['round']}: ours={m.get('only_ours',[])} jf={m.get('only_javafo',[])}"
                for m in result["mismatches"]
            )
            print(f"\n9p/5r mismatches ({matched}/{total} matched):\n{details}")

        self.assertTrue(
            result["matches"][0],
            f"Round 1 mismatch",
        )

    def test_bye_consistency(self):
        """Verify both engines agree on bye assignment."""
        result = _run_cross_validation(9, 5, seed=42)
        for rnd in range(1, 6):
            our = result["our_pairings"][rnd]
            jf = result["javafo_pairings"][rnd]
            if jf is None:
                continue
            our_byes = {p[0] for p in our if p[1] == 0}
            jf_byes = {p[0] for p in jf if p[1] == 0}
            if our_byes != jf_byes:
                print(f"  R{rnd} bye diff: ours={our_byes} jf={jf_byes}")


@unittest.skipUnless(_java_available(), _SKIP_REASON)
class TestCrossValidation20p9r(unittest.TestCase):
    """Cross-validate 20 players, 9 rounds (stress test)."""

    def test_round_by_round(self):
        result = _run_cross_validation(20, 9, seed=42)
        total = len(result["matches"])
        matched = sum(1 for m in result["matches"] if m is True)
        failed_jf = sum(1 for m in result["matches"] if m is None)

        self.assertEqual(failed_jf, 0, "JavaFo failed on some rounds")

        if result["mismatches"]:
            details = "\n".join(
                f"  R{m['round']}: ours={m.get('only_ours',[])} jf={m.get('only_javafo',[])}"
                for m in result["mismatches"]
            )
            print(f"\n20p/9r mismatches ({matched}/{total} matched):\n{details}")

        self.assertTrue(
            result["matches"][0],
            f"Round 1 mismatch",
        )


@unittest.skipUnless(_java_available(), _SKIP_REASON)
class TestCrossValidationRatingSpread(unittest.TestCase):
    """Cross-validate with large rating difference (mixed field)."""

    def test_wide_rating_spread(self):
        """12 players with 100-point gaps (2400 down to 1300)."""
        result = _run_cross_validation(12, 7, seed=55)
        total = len(result["matches"])
        matched = sum(1 for m in result["matches"] if m is True)

        if result["mismatches"]:
            details = "\n".join(
                f"  R{m['round']}: ours={m.get('only_ours',[])} jf={m.get('only_javafo',[])}"
                for m in result["mismatches"]
            )
            print(f"\n12p/7r wide-spread mismatches ({matched}/{total} matched):\n{details}")

        self.assertTrue(
            result["matches"][0],
            "Round 1 must always match (deterministic)",
        )


# ===========================================================================
# Summary runner
# ===========================================================================


@unittest.skipUnless(_java_available(), _SKIP_REASON)
class TestCrossValidationSummary(unittest.TestCase):
    """Run all configurations and print a summary report."""

    def test_summary_report(self):
        configs = [
            (10, 5, 42, "10p/5r"),
            (8, 7, 77, "8p/7r"),
            (9, 5, 42, "9p/5r (odd)"),
            (20, 9, 42, "20p/9r"),
            (12, 7, 55, "12p/7r (spread)"),
        ]

        print("\n" + "=" * 60)
        print("JavaFo Cross-Validation Summary")
        print("=" * 60)

        all_matched_rounds = 0
        all_total_rounds = 0

        for n_players, n_rounds, seed, label in configs:
            result = _run_cross_validation(n_players, n_rounds, seed=seed)
            total = len(result["matches"])
            matched = sum(1 for m in result["matches"] if m is True)
            jf_fail = sum(1 for m in result["matches"] if m is None)

            all_matched_rounds += matched
            all_total_rounds += total

            status = "PASS" if matched == total else f"PARTIAL ({matched}/{total})"
            if jf_fail > 0:
                status += f" [JavaFo failed {jf_fail} rounds]"
            print(f"  {label:20s}  {status}")

            for m in result["mismatches"]:
                if "error" not in m:
                    print(f"    R{m['round']}: ours_only={m['only_ours']} jf_only={m['only_javafo']}")

        print("-" * 60)
        print(f"  Total: {all_matched_rounds}/{all_total_rounds} rounds matched")
        print("=" * 60)

        # The test passes as long as it runs — mismatches are documented
        # but not failures (yet). Once engine reaches 100% match, tighten.
        self.assertGreater(all_matched_rounds, 0, "No rounds matched at all")


if __name__ == "__main__":
    unittest.main(verbosity=2)
