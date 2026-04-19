#!/usr/bin/env python3
"""
Debug a specific divergence case by comparing edge weights
and tracing through the MWM algorithm step by step.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from caissify_pairings.engines.dutch import DutchEngine, DutchPlayer, ColorPref, FloatDir
from caissify_pairings.fpc import _build_engine_players, _build_previous_pairings, _infer_initial_color
from caissify_pairings.trf import TRFParser

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BBP_BINARY = PROJECT_ROOT / "vendor" / "bbpPairings" / "bbpPairings.exe"


def bbp_generate(num_players, num_rounds, seed):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as cfg:
        cfg.write(f"PlayersNumber={num_players}\nRoundsNumber={num_rounds}\n")
        cfg_path = cfg.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".trf", delete=False) as out:
        out_path = out.name
    try:
        subprocess.run(
            [str(BBP_BINARY), "--dutch", "-g", cfg_path, "-o", out_path, "-s", str(seed)],
            check=True, capture_output=True, timeout=30,
        )
        with open(out_path) as f:
            return f.read()
    finally:
        os.unlink(cfg_path)
        os.unlink(out_path)


def debug_round(trf_content, target_round):
    parsed = TRFParser(trf_content).parse()
    players_list = parsed["players"]
    tournament = parsed["tournament"]
    total_rounds = tournament.get("total_rounds") or 5
    player_map = {p["starting_number"]: p for p in players_list}
    initial_color = _infer_initial_color(player_map)

    engine_players = _build_engine_players(player_map, target_round)
    previous_pairings = _build_previous_pairings(player_map, target_round)

    engine = DutchEngine(
        players=engine_players,
        previous_pairings=previous_pairings,
        round_number=target_round,
        total_rounds=total_rounds,
        initial_color=initial_color,
    )

    # Print player state
    print(f"\n{'='*70}")
    print(f"ROUND {target_round} — {len(engine._players)} players")
    print(f"{'='*70}")

    sorted_players = sorted(engine._players, key=lambda p: (-p.score, p.pairing_number))
    for p in sorted_players:
        pref_name = "none" if p.color_preference == ColorPref.NONE else (
            "white" if p.color_preference == ColorPref.WHITE else "black")
        fh = [f.name.lower() for f in p.float_hist]
        print(f"  P{p.pairing_number:2d} score={p.score:.1f} colors={p.color_hist} "
              f"pref={pref_name}({p.preference_strength}) diff={p.color_diff} "
              f"floats={fh} opps={sorted(p.opponents)} bye_count={p.bye_count}")

    # Generate pairings and capture internal state
    output = engine.generate_pairings()

    print(f"\nOur pairings:")
    for p in output:
        w = p["white_id"]
        b = p.get("black_id")
        if b is not None and not p.get("bye"):
            print(f"  Table {p['table']}: {w} vs {b}")
        elif p.get("bye"):
            print(f"  BYE: {w}")

    # Extract TRF pairings
    print(f"\nbbpPairings reference:")
    seen = set()
    for sn, pl in sorted(player_map.items()):
        if sn in seen:
            continue
        r = pl.get("results", {}).get(target_round)
        if r is None:
            continue
        opp = r.get("opponent")
        color = r.get("color", "w")
        if opp is None:
            print(f"  BYE: {sn}")
            seen.add(sn)
        else:
            if color == "w":
                print(f"  {sn} vs {opp}")
            else:
                print(f"  {opp} vs {sn}")
            seen.add(sn)
            seen.add(opp)

    # Now trace edge weights for divergent pairs
    print(f"\n{'='*70}")
    print("EDGE WEIGHT ANALYSIS")
    print(f"{'='*70}")

    # Build the same structures the engine builds
    import networkx as nx
    sp = sorted(engine._players, key=lambda p: (-p.score, p.pairing_number))
    n = len(sp)
    idx_of = {p.id: i for i, p in enumerate(sp)}

    # Score group boundaries
    sg_bounds = []
    i = 0
    while i < n:
        j = i + 1
        while j < n and sp[j].score == sp[i].score:
            j += 1
        sg_bounds.append((i, j))
        i = j

    # scoreGroupShifts
    score_group_shifts = {}
    score_groups_shift = 0
    max_sg_size = 0
    for sg_start, sg_end in reversed(sg_bounds):
        group_size = sg_end - sg_start
        score = sp[sg_start].score
        new_bits = max(1, group_size.bit_length())
        score_group_shifts[score] = score_groups_shift
        max_sg_size = max(max_sg_size, group_size)
        score_groups_shift += new_bits
    score_group_size_bits = max(1, max_sg_size.bit_length())

    B = max(8, n.bit_length() + 3)

    # Check bye candidates
    bye_candidates = None
    if n % 2 == 1 and target_round >= 2:
        bye_eligible = [p for p in sp if p.bye_count < engine.max_byes_per_player]
        if not bye_eligible:
            bye_eligible = list(sp)
        bye_candidates = {p.id for p in bye_eligible}
        # Run preliminary MWM to narrow
        top_score = sp[0].score
        prelim_G = nx.Graph()
        for vi in range(n):
            prelim_G.add_node(vi)
        for vi in range(n):
            for vj in range(vi + 1, n):
                pi, pj = sp[vi], sp[vj]
                if not engine._can_pair(pi, pj):
                    continue
                pw = 0
                pw |= (1 + (pi.id not in bye_candidates) + (pj.id not in bye_candidates))
                pw <<= score_groups_shift
                pw |= (score_group_shifts.get(pi.score, 0) + score_group_shifts.get(pj.score, 0))
                pw <<= score_group_size_bits
                pw |= int(pj.score >= top_score)
                prelim_G.add_edge(vi, vj, weight=pw)
        prelim_m = nx.max_weight_matching(prelim_G, maxcardinality=True)
        prelim_matched = set()
        for u, v in prelim_m:
            prelim_matched.add(u)
            prelim_matched.add(v)
        bye_assignee_score = sp[0].score
        for vi in range(n):
            if vi not in prelim_matched:
                bye_assignee_score = sp[vi].score
                print(f"\nPreliminary MWM bye assignee: P{sp[vi].pairing_number} (score={sp[vi].score})")
                break
        bye_candidates = {
            p.id for p in sp
            if p.id in bye_candidates and p.score <= bye_assignee_score
        }
        print(f"Bye candidates (score <= {bye_assignee_score}): {sorted([p.pairing_number for p in sp if p.id in bye_candidates])}")

    # Print edge weights for all in-current-bracket pairs
    print(f"\nScore groups: {[(sp[s].score, e-s) for s, e in sg_bounds]}")
    print(f"B={B}, SB={score_groups_shift}, sgSizeBits={score_group_size_bits}")

    for sg_start, sg_end in sg_bounds:
        sg_score = sp[sg_start].score
        sg_size = sg_end - sg_start
        if sg_size < 2:
            continue
        print(f"\n--- Score group {sg_score} (size {sg_size}) ---")
        for i in range(sg_start, sg_end):
            for j in range(i + 1, sg_end):
                pi, pj = sp[i], sp[j]
                w = engine._compute_bracket_edge_weight(
                    pi, pj, bracket_score=sg_score, n=n,
                    s1_ids=set(), s2_ids=set(),
                    s1_pos={}, s2_pos={}, s_len=0,
                    in_current_bracket=True, in_next_bracket=False,
                    score_group_shifts=score_group_shifts,
                    score_groups_shift=score_groups_shift,
                    score_group_size_bits=score_group_size_bits,
                    bye_candidates=bye_candidates,
                )
                # Decode the weight
                can_pair = engine._can_pair(pi, pj)
                print(f"  P{pi.pairing_number} vs P{pj.pairing_number}: "
                      f"w={w} (0x{w:x}) can_pair={can_pair}")
                if w > 0:
                    _decode_weight(w, B, score_groups_shift)

    # Also print cross-bracket weights for interesting pairs
    print(f"\n--- Cross-bracket pairs (far-bracket) ---")
    for sg_idx, (sg_start, sg_end) in enumerate(sg_bounds):
        for next_idx in range(sg_idx + 1, len(sg_bounds)):
            ns, ne = sg_bounds[next_idx]
            for i in range(sg_start, sg_end):
                for j in range(ns, ne):
                    pi, pj = sp[i], sp[j]
                    if not engine._can_pair(pi, pj):
                        continue
                    w = engine._compute_bracket_edge_weight(
                        pi, pj, bracket_score=min(pi.score, pj.score), n=n,
                        s1_ids=set(), s2_ids=set(),
                        s1_pos={}, s2_pos={}, s_len=0,
                        in_current_bracket=False, in_next_bracket=False,
                        score_group_shifts=score_group_shifts,
                        score_groups_shift=score_groups_shift,
                        score_group_size_bits=score_group_size_bits,
                        bye_candidates=bye_candidates,
                    )
                    if w > 0:
                        print(f"  P{pi.pairing_number} vs P{pj.pairing_number}: "
                              f"w={w} (0x{w:x})")


def _decode_weight(w, B, SB):
    """Decode a weight showing each criterion's contribution."""
    s = 0
    reserved = (w >> s) & ((1 << (3*B+1)) - 1)
    s += 3*B + 1
    # Note: cannot fully decode without knowing rounds_played
    # Just show the raw hex at each layer
    print(f"    reserved={reserved:#x}", end="")
    # Print remaining bits
    rest = w >> s
    print(f" rest={rest:#x}")


if __name__ == "__main__":
    # Default: 9p/5r seed 47, round 5
    num_players = int(sys.argv[1]) if len(sys.argv) > 1 else 9
    num_rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 47
    target_round = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    trf = bbp_generate(num_players, num_rounds, seed)
    debug_round(trf, target_round)
