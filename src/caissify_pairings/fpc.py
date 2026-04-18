"""
Free Pairings Checker (FPC) — FIDE C.04.A §A.4

Reads a completed TRF file, rebuilds the tournament round by round,
re-pairs each round with the embedded Dutch engine, and reports
which pairings are consistent / inconsistent with the engine output.

CLI usage::

    caissify-pairings-check tournament.trf
    caissify-pairings --check tournament.trf

Programmatic usage::

    from caissify_pairings.fpc import check_trf
    report = check_trf(trf_text)
"""

from __future__ import annotations

import json
import sys
from typing import Dict, List, Optional, Set, Tuple

from caissify_pairings.engines.dutch import DutchEngine
from caissify_pairings.trf import TRFParser, TRFError


# ---------------------------------------------------------------------------
# Core checker
# ---------------------------------------------------------------------------

def check_trf(trf_content: str) -> Dict:
    """
    Check a TRF file against the embedded Dutch engine.

    Returns a report dict::

        {
            "tournament_name": str,
            "total_rounds": int,
            "num_players": int,
            "rounds": [
                {
                    "round": 1,
                    "match": True/False,
                    "engine_pairings": [...],
                    "trf_pairings": [...],
                    "discrepancies": [...],
                }
            ],
            "summary": {
                "rounds_checked": int,
                "rounds_matched": int,
                "rounds_mismatched": int,
                "total_discrepancies": int,
            },
        }
    """
    parsed = TRFParser(trf_content).parse()
    tournament = parsed["tournament"]
    players = parsed["players"]

    total_rounds = tournament.get("total_rounds") or _max_round(players)
    num_players = len(players)

    # Build a player-map keyed by starting_number
    player_map: Dict[int, Dict] = {p["starting_number"]: p for p in players}

    rounds_report: List[Dict] = []
    summary_matched = 0
    summary_mismatched = 0
    total_discrepancies = 0

    for rnd in range(1, total_rounds + 1):
        # --- 1. Extract TRF pairings for this round ---
        trf_pairings = _extract_trf_round(player_map, rnd)
        if not trf_pairings:
            # Round not present in TRF — skip
            continue

        # --- 2. Build engine input from rounds 1..(rnd-1) ---
        engine_players = _build_engine_players(player_map, rnd)
        previous_pairings = _build_previous_pairings(player_map, rnd)

        # --- 3. Run our engine ---
        try:
            engine = DutchEngine(
                players=engine_players,
                previous_pairings=previous_pairings,
                round_number=rnd,
                total_rounds=total_rounds,
            )
            engine_output = engine.generate_pairings()
        except Exception as exc:
            rounds_report.append({
                "round": rnd,
                "match": False,
                "engine_error": str(exc),
                "trf_pairings": trf_pairings,
                "engine_pairings": [],
                "discrepancies": [f"Engine error: {exc}"],
            })
            summary_mismatched += 1
            total_discrepancies += 1
            continue

        engine_pairings = _normalise_engine_output(engine_output)

        # --- 4. Compare ---
        discrepancies = _compare(trf_pairings, engine_pairings)
        match = len(discrepancies) == 0

        rounds_report.append({
            "round": rnd,
            "match": match,
            "trf_pairings": trf_pairings,
            "engine_pairings": engine_pairings,
            "discrepancies": discrepancies,
        })
        if match:
            summary_matched += 1
        else:
            summary_mismatched += 1
            total_discrepancies += len(discrepancies)

    return {
        "tournament_name": tournament.get("name", "(unknown)"),
        "total_rounds": total_rounds,
        "num_players": num_players,
        "rounds": rounds_report,
        "summary": {
            "rounds_checked": len(rounds_report),
            "rounds_matched": summary_matched,
            "rounds_mismatched": summary_mismatched,
            "total_discrepancies": total_discrepancies,
        },
    }


# ---------------------------------------------------------------------------
# Helpers — extract TRF pairings
# ---------------------------------------------------------------------------

def _max_round(players: List[Dict]) -> int:
    mx = 0
    for p in players:
        for r in p.get("results", {}):
            if r > mx:
                mx = r
    return mx


def _extract_trf_round(
    player_map: Dict[int, Dict], rnd: int,
) -> List[Dict]:
    """
    Extract pairings for *rnd* from TRF player results.

    Returns a sorted list of ``{"white": sn, "black": sn}`` or
    ``{"white": sn, "black": None, "bye": True}`` dicts.
    """
    seen_pairs: Set[Tuple[int, int]] = set()
    pairings: List[Dict] = []

    for sn, p in player_map.items():
        r = p.get("results", {}).get(rnd)
        if r is None:
            continue
        opp = r.get("opponent")
        color = r.get("color")

        if opp is None:
            # Bye
            pairings.append({"white": sn, "black": None, "bye": True})
            continue

        pair_key = (min(sn, opp), max(sn, opp))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        if color == "w":
            pairings.append({"white": sn, "black": opp})
        else:
            pairings.append({"white": opp, "black": sn})

    pairings.sort(key=lambda p: (p.get("bye", False), min(p["white"], p.get("black") or 9999)))
    return pairings


# ---------------------------------------------------------------------------
# Helpers — build engine input
# ---------------------------------------------------------------------------

def _build_engine_players(
    player_map: Dict[int, Dict], target_round: int,
) -> List[Dict]:
    """
    Build the player-dicts that DutchEngine expects, using state
    *before* ``target_round`` (i.e. rounds 1..target_round-1).
    """
    engine_players: List[Dict] = []

    # Pre-compute cumulative scores at the END of each round (score_at[sn][rnd])
    # so we can derive float directions. score_at[sn][0] = 0 (before round 1).
    score_at: Dict[int, Dict[int, float]] = {}
    for sn, p in player_map.items():
        score_at[sn] = {0: 0.0}
        cum = 0.0
        max_rnd = max(p.get("results", {}).keys()) if p.get("results") else 0
        for rnd in range(1, max_rnd + 1):
            r = p.get("results", {}).get(rnd)
            if r is None:
                score_at[sn][rnd] = cum
                continue
            opp = r.get("opponent")
            res = r.get("result", "-")
            if opp is None:
                if res in ("1", "+", "F", "U"):
                    cum += 1.0
                elif res in ("=", "H"):
                    cum += 0.5
            else:
                if res in ("1", "+"):
                    cum += 1.0
                elif res in ("=", "D"):
                    cum += 0.5
            score_at[sn][rnd] = cum

    for sn, p in player_map.items():
        # A player is active for this round if they have a result entry
        # for target_round in the TRF.  For round 1 everyone participates.
        has_this_round = target_round in p.get("results", {})
        if target_round == 1:
            # Round 1 — include everyone who has any result at all
            if not p.get("results"):
                continue
        elif not has_this_round:
            # Later rounds — skip players who don't appear in this round
            # (withdrawn or absent)
            continue

        score = 0.0
        color_hist: List[str] = []
        float_history: List[str] = []
        bye_count = 0

        for rnd in range(1, target_round):
            r = p.get("results", {}).get(rnd)
            if r is None:
                continue
            opp = r.get("opponent")
            res = r.get("result", "-")

            if opp is None:
                # Bye — counts as downfloat per A.4.b
                bye_count += 1
                float_history.append("down")
                if res in ("1", "+", "F", "U"):
                    score += 1.0
                elif res in ("=", "H"):
                    score += 0.5
                # Z/- → 0
            else:
                color = r.get("color", "w")
                color_hist.append("white" if color == "w" else "black")
                if res == "1":
                    score += 1.0
                elif res in ("=", "D"):
                    score += 0.5
                elif res == "+":
                    score += 1.0
                # 0, -, L → 0

                # Compute float direction: compare pre-round scores
                my_pre_score = score_at[sn].get(rnd - 1, 0.0)
                opp_pre_score = score_at[opp].get(rnd - 1, 0.0)
                if my_pre_score > opp_pre_score:
                    float_history.append("down")
                elif my_pre_score < opp_pre_score:
                    float_history.append("up")
                else:
                    float_history.append("none")

        engine_players.append({
            "id": sn,
            "name": p.get("name", ""),
            "score": score,
            "rating": p.get("rating", 0),
            "starting_number": sn,
            "pairing_number": sn,
            "title": p.get("title", ""),
            "color_hist": color_hist,
            "float_history": float_history,
            "bye_count": bye_count,
        })

    return engine_players


def _build_previous_pairings(
    player_map: Dict[int, Dict], target_round: int,
) -> Set[Tuple[int, int]]:
    """Collect all (id_a, id_b) pairs from rounds 1..target_round-1."""
    pairs: Set[Tuple[int, int]] = set()
    for sn, p in player_map.items():
        for rnd in range(1, target_round):
            r = p.get("results", {}).get(rnd)
            if r and r.get("opponent"):
                a, b = sn, r["opponent"]
                pairs.add((min(a, b), max(a, b)))
    return pairs


# ---------------------------------------------------------------------------
# Helpers — normalise engine output
# ---------------------------------------------------------------------------

def _normalise_engine_output(engine_output: List[Dict]) -> List[Dict]:
    """Convert engine output to the same shape as TRF pairings for comparison."""
    pairings: List[Dict] = []
    for p in engine_output:
        w = p["white_id"]
        b = p.get("black_id")
        if b is None or p.get("bye"):
            pairings.append({"white": w, "black": None, "bye": True})
        else:
            pairings.append({"white": w, "black": b})
    pairings.sort(key=lambda p: (p.get("bye", False), min(p["white"], p.get("black") or 9999)))
    return pairings


# ---------------------------------------------------------------------------
# Helpers — compare
# ---------------------------------------------------------------------------

def _compare(
    trf_pairings: List[Dict],
    engine_pairings: List[Dict],
) -> List[str]:
    """Return a list of human-readable discrepancy descriptions."""
    discrepancies: List[str] = []

    trf_set = _pairing_set(trf_pairings)
    eng_set = _pairing_set(engine_pairings)

    only_trf = trf_set - eng_set
    only_eng = eng_set - trf_set

    for pair in sorted(only_trf):
        discrepancies.append(f"TRF only: {_fmt_pair(pair)}")
    for pair in sorted(only_eng):
        discrepancies.append(f"Engine only: {_fmt_pair(pair)}")

    return discrepancies


def _pairing_set(pairings: List[Dict]) -> Set[Tuple[int, Optional[int]]]:
    """Convert pairings list to a set of (white, black) tuples."""
    s: Set[Tuple[int, Optional[int]]] = set()
    for p in pairings:
        w = p["white"]
        b = p.get("black")
        if b is None:
            s.add((w, None))
        else:
            # Normalise so (min, max) regardless of colour
            s.add((min(w, b), max(w, b)))
    return s


def _fmt_pair(pair: Tuple[int, Optional[int]]) -> str:
    a, b = pair
    if b is None:
        return f"Player {a} (bye)"
    return f"{a} vs {b}"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI: ``caissify-pairings-check <trf_file>``"""
    if len(sys.argv) < 2:
        print("Usage: caissify-pairings-check <trf_file>", file=sys.stderr)
        sys.exit(1)

    trf_path = sys.argv[1]
    try:
        with open(trf_path, "r", encoding="utf-8") as f:
            trf_content = f.read()
    except FileNotFoundError:
        print(f"File not found: {trf_path}", file=sys.stderr)
        sys.exit(1)

    report = check_trf(trf_content)
    _print_report(report)

    # Exit code: 0 if all rounds match, 1 otherwise
    if report["summary"]["rounds_mismatched"] > 0:
        sys.exit(1)


def _print_report(report: Dict) -> None:
    """Pretty-print the checker report to stdout."""
    print(f"Tournament: {report['tournament_name']}")
    print(f"Players: {report['num_players']}, Rounds: {report['total_rounds']}")
    print()

    for rnd_info in report["rounds"]:
        rnd = rnd_info["round"]
        if rnd_info["match"]:
            print(f"  Round {rnd}: OK")
        else:
            print(f"  Round {rnd}: MISMATCH")
            for d in rnd_info["discrepancies"]:
                print(f"    - {d}")

    s = report["summary"]
    print()
    print(
        f"Summary: {s['rounds_matched']}/{s['rounds_checked']} rounds match, "
        f"{s['total_discrepancies']} discrepancies"
    )


if __name__ == "__main__":
    main()
