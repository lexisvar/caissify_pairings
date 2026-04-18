#!/usr/bin/env python3
"""
Profile engine divergences against bbpPairings.

Generates bbpPairings RTG tournaments and runs our FPC,
then for each mismatched round, dumps detailed player state
and scoring information to understand WHY the pairing differs.
"""

import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from caissify_pairings.engines.dutch import DutchEngine, DutchPlayer, ColorPref
from caissify_pairings.fpc import check_trf, _build_engine_players, _build_previous_pairings, _infer_initial_color
from caissify_pairings.trf import TRFParser

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BBP_BINARY = PROJECT_ROOT / "vendor" / "bbpPairings" / "bbpPairings.exe"


def bbp_generate(num_players, num_rounds, seed, output_path):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as cfg:
        cfg.write(f"PlayersNumber={num_players}\nRoundsNumber={num_rounds}\n")
        cfg_path = cfg.name
    try:
        subprocess.run(
            [str(BBP_BINARY), "--dutch", "-g", cfg_path, "-o", output_path, "-s", str(seed)],
            check=True, capture_output=True, timeout=30,
        )
    finally:
        os.unlink(cfg_path)


def extract_trf_round(player_map, rnd):
    """Extract pairings from TRF for a specific round."""
    pairings = []
    seen = set()
    for sn, p in sorted(player_map.items()):
        if sn in seen:
            continue
        r = p.get("results", {}).get(rnd)
        if r is None:
            continue
        opp = r.get("opponent")
        color = r.get("color", "w")
        if opp is None:
            pairings.append({"white": sn, "black": None, "bye": True})
            seen.add(sn)
        else:
            if color == "w":
                pairings.append({"white": sn, "black": opp})
            else:
                pairings.append({"white": opp, "black": sn})
            seen.add(sn)
            seen.add(opp)
    return pairings


def profile_tournament(trf_content, verbose=False):
    """Run FPC and profile each mismatched round."""
    parsed = TRFParser(trf_content).parse()
    players_list = parsed["players"]
    tournament = parsed["tournament"]
    total_rounds = tournament.get("total_rounds") or max(
        max(p.get("results", {}).keys(), default=0) for p in players_list
    )
    player_map = {p["starting_number"]: p for p in players_list}
    initial_color = _infer_initial_color(player_map)

    divergences = []

    for rnd in range(1, total_rounds + 1):
        trf_pairings = extract_trf_round(player_map, rnd)
        if not trf_pairings:
            continue

        engine_players = _build_engine_players(player_map, rnd)
        previous_pairings = _build_previous_pairings(player_map, rnd)

        engine = DutchEngine(
            players=engine_players,
            previous_pairings=previous_pairings,
            round_number=rnd,
            total_rounds=total_rounds,
            initial_color=initial_color,
        )
        engine_output = engine.generate_pairings()

        # Normalize engine output
        engine_pairs = set()
        for p in engine_output:
            w = p["white_id"]
            b = p.get("black_id")
            if b is not None and not p.get("bye"):
                engine_pairs.add((min(w, b), max(w, b)))

        trf_pairs = set()
        for p in trf_pairings:
            if p.get("black") is not None and not p.get("bye"):
                w, b = p["white"], p["black"]
                trf_pairs.add((min(w, b), max(w, b)))

        if engine_pairs == trf_pairs:
            continue

        # Mismatch! Profile it.
        only_engine = engine_pairs - trf_pairs
        only_trf = trf_pairs - engine_pairs

        # Get engine's internal state
        dp_map = {dp.id: dp for dp in engine._players}
        scoregroups = engine._build_scoregroups(engine._players)

        info = {
            "round": rnd,
            "only_engine": only_engine,
            "only_trf": only_trf,
            "scoregroups": [],
        }

        # Find which scoregroup the divergence is in
        for sg_players in scoregroups:
            sg_score = sg_players[0].score if sg_players else 0
            sg_ids = {p.id for p in sg_players}
            divergent_players = set()
            for a, b in only_engine | only_trf:
                if a in sg_ids:
                    divergent_players.add(a)
                if b in sg_ids:
                    divergent_players.add(b)

            if divergent_players:
                player_details = []
                for pid in sorted(divergent_players):
                    dp = dp_map.get(pid)
                    if dp:
                        player_details.append({
                            "id": pid,
                            "pn": dp.pairing_number,
                            "score": dp.score,
                            "rating": dp.rating,
                            "color_hist": dp.color_hist,
                            "color_pref": dp.color_preference.value,
                            "pref_strength": dp.preference_strength,
                            "color_diff": dp.color_diff,
                            "float_hist": [f.value if hasattr(f, 'value') else str(f) for f in dp.float_hist],
                            "last_float": dp.last_float.value if hasattr(dp.last_float, 'value') else str(dp.last_float),
                            "opponents": sorted(dp.opponents),
                        })

                info["scoregroups"].append({
                    "score": sg_score,
                    "size": len(sg_players),
                    "divergent_players": player_details,
                })

        # Also compare colour assignments
        engine_colors = {}
        for p in engine_output:
            w = p["white_id"]
            b = p.get("black_id")
            if b is not None and not p.get("bye"):
                engine_colors[(min(w,b), max(w,b))] = {"white": w, "black": b}

        trf_colors = {}
        for p in trf_pairings:
            if p.get("black") is not None and not p.get("bye"):
                w, b = p["white"], p["black"]
                trf_colors[(min(w,b), max(w,b))] = {"white": w, "black": b}

        # Check if it's just a color swap on the same pairs
        same_pairs_diff_colors = set()
        for pair in engine_pairs & trf_pairs:
            if engine_colors.get(pair) != trf_colors.get(pair):
                same_pairs_diff_colors.add(pair)

        info["same_pairs_diff_colors"] = same_pairs_diff_colors
        info["num_pair_diffs"] = len(only_engine)
        info["num_color_only_diffs"] = len(same_pairs_diff_colors)

        divergences.append(info)

    return divergences


def categorize_divergences(all_divergences):
    """Summarize divergence patterns across all tournaments."""
    stats = {
        "total_mismatched_rounds": 0,
        "total_pair_diffs": 0,
        "total_color_only_diffs": 0,
        "scoregroup_sizes": Counter(),
        "divergent_pref_strengths": Counter(),
        "divergent_float_patterns": Counter(),
        "rounds_with_only_color_diffs": 0,
    }

    for divs in all_divergences:
        for d in divs:
            stats["total_mismatched_rounds"] += 1
            stats["total_pair_diffs"] += d["num_pair_diffs"]
            stats["total_color_only_diffs"] += d["num_color_only_diffs"]

            if d["num_pair_diffs"] == 0 and d["num_color_only_diffs"] > 0:
                stats["rounds_with_only_color_diffs"] += 1

            for sg in d["scoregroups"]:
                stats["scoregroup_sizes"][sg["size"]] += 1
                for p in sg["divergent_players"]:
                    stats["divergent_pref_strengths"][p["pref_strength"]] += 1
                    lf = p["last_float"]
                    stats["divergent_float_patterns"][lf] += 1

    return stats


def main():
    num_players = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    num_rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    seed_offset = int(sys.argv[4]) if len(sys.argv) > 4 else 42

    print(f"Profiling {count} tournaments ({num_players}p/{num_rounds}r), seeds {seed_offset}..{seed_offset+count-1}")
    print("=" * 70)

    all_divergences = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(count):
            seed = seed_offset + i
            trf_path = os.path.join(tmpdir, f"bbp_{seed}.trf")
            bbp_generate(num_players, num_rounds, seed, trf_path)

            with open(trf_path) as f:
                trf_content = f.read()

            divs = profile_tournament(trf_content)
            all_divergences.append(divs)

            if divs:
                print(f"\n--- Seed {seed}: {len(divs)} mismatched rounds ---")
                for d in divs:
                    print(f"  Round {d['round']}: {d['num_pair_diffs']} pair diffs, "
                          f"{d['num_color_only_diffs']} color-only diffs")
                    print(f"    Our pairs only:  {d['only_engine']}")
                    print(f"    Ref pairs only:  {d['only_trf']}")
                    if d['same_pairs_diff_colors']:
                        print(f"    Color swaps:     {d['same_pairs_diff_colors']}")
                    for sg in d["scoregroups"]:
                        print(f"    Scoregroup {sg['score']} (size {sg['size']}):")
                        for p in sg["divergent_players"]:
                            print(f"      P{p['id']:2d} (pn={p['pn']}) score={p['score']} "
                                  f"colors={p['color_hist']} pref={p['color_pref']}({p['pref_strength']}) "
                                  f"diff={p['color_diff']} floats={p['float_hist']} "
                                  f"opps={p['opponents']}")

    print("\n" + "=" * 70)
    stats = categorize_divergences(all_divergences)
    print(f"SUMMARY ({count} tournaments):")
    print(f"  Mismatched rounds: {stats['total_mismatched_rounds']}")
    print(f"  Total pair diffs: {stats['total_pair_diffs']}")
    print(f"  Color-only diffs: {stats['total_color_only_diffs']}")
    print(f"  Rounds with ONLY color diffs: {stats['rounds_with_only_color_diffs']}")
    print(f"  Scoregroup sizes involved: {dict(stats['scoregroup_sizes'].most_common())}")
    print(f"  Pref strengths of divergent players: {dict(stats['divergent_pref_strengths'].most_common())}")
    print(f"  Float patterns of divergent players: {dict(stats['divergent_float_patterns'].most_common())}")


if __name__ == "__main__":
    main()
