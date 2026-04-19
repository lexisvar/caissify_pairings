#!/usr/bin/env python3
"""
Edge-weight diff diagnostic — Phase 3.6 hypothesis test.

For a single divergent (tournament, round, score-group) case, computes
edge weights two ways:

    OURS  — our engine's _compute_bracket_edge_weight (production code)
    BBP   — a faithful Python port of bbpPairings'
            swisssystems/dutch.cpp::computeEdgeWeight (lines 232-482)

Both implementations are forced to use the SAME bit widths
(scoreGroupSizeBits, scoreGroupsShift, scoreGroupShifts) so that any
discrepancy reflects a *value* difference, not a *width* difference.

Output is a per-edge, per-tier table that pinpoints exactly which
criterion (T1/T2/T3/T4/C9/c_imb/c_absP/c_compat/c_strong/C12..C19)
disagrees. This is the diagnostic that tells us whether the remaining
odd-player divergences live in:

    (a) the edge-weight encoding itself (fixable by patching the right
        tier), or

    (b) the maximum-weight matching's tie-breaking (fixable by adding
        deterministic LSB tiebreaker bits in the reserved area), or

    (c) somewhere outside the per-edge weights (bracket-loop logic,
        finalize sequence, etc.).

USAGE
    python scripts/compare_edge_weights.py <num_players> <num_rounds> \\
                                            <seed> <round> [score]

EXAMPLE
    # The 9p/5r seed-90 round-4 divergence at score 1.0:
    python scripts/compare_edge_weights.py 9 5 90 4 1.0

    # If <score> omitted, scans every score group in <round> and reports
    # only those whose pairings diverge from bbpPairings' TRF output.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from caissify_pairings.engines.dutch import (  # noqa: E402
    ColorPref,
    DutchEngine,
    DutchPlayer,
    FloatDir,
)
from caissify_pairings.fpc import (  # noqa: E402
    _build_engine_players,
    _build_previous_pairings,
    _infer_initial_color,
)
from caissify_pairings.trf import TRFParser  # noqa: E402

BBP_BINARY = PROJECT_ROOT / "vendor" / "bbpPairings" / "bbpPairings.exe"


# ---------------------------------------------------------------------------
# 1. Faithful Python port of bbpPairings' computeEdgeWeight()
#    Source: vendor/bbpPairings/src/swisssystems/dutch.cpp lines 232-482
# ---------------------------------------------------------------------------

def _bbp_get_float(p: DutchPlayer, k: int) -> FloatDir:
    """Mirror of getFloat(player, roundsBack). p.float_hist is appended
    once per round; index -k is "k rounds back"."""
    idx = len(p.float_hist) - k
    return p.float_hist[idx] if idx >= 0 else FloatDir.NONE


def _bbp_repeated_color(p: DutchPlayer) -> Optional[str]:
    ch = p.color_hist
    if len(ch) >= 2 and ch[-1] == ch[-2]:
        return ch[-1]
    return None


def _bbp_color_imbalance(p: DutchPlayer) -> int:
    """bbp's signed colorImbalance == white_played - black_played.
    Python's color_diff has the same definition."""
    return p.color_diff


def _bbp_absolute_color_imbalance(p: DutchPlayer) -> bool:
    return abs(_bbp_color_imbalance(p)) >= 2


def _bbp_absolute_color_preference(p: DutchPlayer) -> bool:
    return p.preference_strength == 3


def _bbp_strong_color_preference(p: DutchPlayer) -> bool:
    """C++ field. True iff player just played 2-in-a-row of same color
    (regardless of overall imbalance)."""
    return _bbp_repeated_color(p) is not None


def _bbp_color_prefs_compatible(a: ColorPref, b: ColorPref) -> bool:
    return a == ColorPref.NONE or b == ColorPref.NONE or a != b


def _bbp_is_bye_candidate(p: DutchPlayer, bye_assignee_score: float) -> bool:
    # We approximate eligibleForBye via membership in bye_candidates set
    # passed in — this is enforced by the caller, so here we only do the
    # score check.
    return p.score <= bye_assignee_score


def bbp_compute_edge_weight(
    higher: DutchPlayer,
    lower: DutchPlayer,
    in_current_bracket: bool,
    in_next_bracket: bool,
    bye_assignee_score: float,
    rounds_played: int,
    score_group_size_bits: int,
    score_groups_shift: int,
    score_group_shifts: Dict[float, int],
    is_single_downfloater_bye_assignee: bool,
    unplayed_game_ranks: Dict[int, int],
    eligible_for_bye: Dict[int, bool],
    compatible: bool,
) -> Tuple[int, "OrderedDict[str, Tuple[int, int]]"]:
    """Faithful port of bbpPairings::dutch::computeEdgeWeight (max=false).

    Returns (final_weight, breakdown) where breakdown is an OrderedDict
    of tier_name -> (value_inside_tier, tier_width_in_bits) listed in
    MSB-first order (i.e. shift order during construction). The first
    entry is the highest-priority tier (completion).
    """
    breakdown: "OrderedDict[str, Tuple[int, int]]" = OrderedDict()
    if not compatible:
        breakdown["INCOMPATIBLE"] = (0, 0)
        return 0, breakdown

    SB = score_groups_shift
    B = score_group_size_bits

    higher_is_bc = (eligible_for_bye.get(higher.id, True)
                    and _bbp_is_bye_candidate(higher, bye_assignee_score))
    lower_is_bc = (eligible_for_bye.get(lower.id, True)
                   and _bbp_is_bye_candidate(lower, bye_assignee_score))

    completion = 1 + (not higher_is_bc) + (not lower_is_bc)
    breakdown["completion"] = (completion, 2)
    result = completion

    # T1: maximize pairs in current bracket (B bits)
    result <<= B
    t1 = 1 if in_current_bracket else 0
    result |= t1
    breakdown["T1_pairs_current"] = (t1, B)

    # T2: maximize scores in current bracket (SB bits)
    result <<= SB
    t2 = 0
    if in_current_bracket:
        t2 = 1 << score_group_shifts.get(higher.score, 0)
    result |= t2
    breakdown["T2_scores_current"] = (t2, SB)

    # T3: pairs in next bracket
    result <<= B
    t3 = 1 if in_next_bracket else 0
    result |= t3
    breakdown["T3_pairs_next"] = (t3, B)

    # T4: scores in next bracket
    result <<= SB
    t4 = 0
    if in_next_bracket:
        t4 = 1 << score_group_shifts.get(higher.score, 0)
    result |= t4
    breakdown["T4_scores_next"] = (t4, SB)

    # C9 (2*B bits)
    result <<= B
    result <<= B
    c9 = 0
    if is_single_downfloater_bye_assignee:
        if higher.score == bye_assignee_score:
            c9 |= unplayed_game_ranks.get(len(higher.opponents), 0)
        if lower.score == bye_assignee_score:
            c9 += unplayed_game_ranks.get(len(lower.opponents), 0)
    result |= c9
    breakdown["C9_bye_unplayed"] = (c9, 2 * B)

    # insertColorBits (4 bits, each B wide). bbp passes
    # (player=lower, opponent=higher).
    pref_l, pref_h = lower.color_preference, higher.color_preference
    mask = in_current_bracket

    # Bit 1: c_imb — !(both absolute imbalance && same pref)
    result <<= B
    c_imb = 1 if (mask and (
        not _bbp_absolute_color_imbalance(lower)
        or not _bbp_absolute_color_imbalance(higher)
        or pref_l != pref_h
    )) else 0
    result |= c_imb
    breakdown["c_imb"] = (c_imb, B)

    # Bit 2: c_absP — complex sub-case
    result <<= B
    c_absp = 0
    if mask:
        if (not _bbp_absolute_color_preference(lower)
                or not _bbp_absolute_color_preference(higher)
                or pref_l != pref_h):
            c_absp = 1
        else:
            li = abs(_bbp_color_imbalance(lower))
            hi = abs(_bbp_color_imbalance(higher))
            l_rep = _bbp_repeated_color(lower)
            h_rep = _bbp_repeated_color(higher)
            if li == hi:
                if l_rep is None or l_rep != h_rep:
                    c_absp = 1
            else:
                lower_imb_player_rep = h_rep if li > hi else l_rep
                inv_pref = ('black' if pref_l == ColorPref.WHITE
                            else 'white' if pref_l == ColorPref.BLACK
                            else None)
                if (lower_imb_player_rep is None
                        or lower_imb_player_rep != inv_pref):
                    c_absp = 1
    result |= c_absp
    breakdown["c_absP"] = (c_absp, B)

    # Bit 3: c_compat
    result <<= B
    c_compat = 1 if (mask and _bbp_color_prefs_compatible(pref_l, pref_h)) else 0
    result |= c_compat
    breakdown["c_compat"] = (c_compat, B)

    # Bit 4: c_strong — high (best) bit of color tier.
    # 1 unless BOTH have strong-or-absolute pref AND same pref AND not
    # (both absolute).
    result <<= B
    c_strong = 0
    if mask:
        l_so = _bbp_strong_color_preference(lower) or _bbp_absolute_color_preference(lower)
        h_so = _bbp_strong_color_preference(higher) or _bbp_absolute_color_preference(higher)
        l_abs = _bbp_absolute_color_preference(lower)
        h_abs = _bbp_absolute_color_preference(higher)
        if (not l_so or not h_so
                or (l_abs and h_abs)
                or pref_l != pref_h):
            c_strong = 1
    result |= c_strong
    breakdown["c_strong"] = (c_strong, B)

    # C12/C13 (rounds_played >= 1)
    if rounds_played >= 1:
        # C12 (B): repeated downfloaters from previous round
        result <<= B
        c12 = 0
        if mask:
            if _bbp_get_float(lower, 1) == FloatDir.DOWN:
                c12 |= 1
            if (higher.score <= lower.score
                    and _bbp_get_float(higher, 1) == FloatDir.DOWN):
                c12 += 1
        result |= c12
        breakdown["C12_down_R-1"] = (c12, B)

        # C13 (B): repeated upfloaters from previous round
        result <<= B
        c13 = 0
        if mask:
            c13 = 0 if (
                _bbp_get_float(lower, 1) == FloatDir.UP
                and higher.score > lower.score
            ) else 1
        result |= c13
        breakdown["C13_up_R-1"] = (c13, B)

    # C14/C15 (rounds_played >= 2)
    if rounds_played >= 2:
        result <<= B
        c14 = 0
        if mask:
            if _bbp_get_float(lower, 2) == FloatDir.DOWN:
                c14 |= 1
            if (higher.score <= lower.score
                    and _bbp_get_float(higher, 2) == FloatDir.DOWN):
                c14 += 1
        result |= c14
        breakdown["C14_down_R-2"] = (c14, B)

        result <<= B
        c15 = 0
        if mask:
            c15 = 0 if (
                _bbp_get_float(lower, 2) == FloatDir.UP
                and higher.score > lower.score
            ) else 1
        result |= c15
        breakdown["C15_up_R-2"] = (c15, B)

    # C16/C17 (rounds_played >= 1)
    if rounds_played >= 1:
        # C16 (SB): scores of repeated downfloaters from R-1
        result <<= SB
        c16 = 0
        if mask:
            if _bbp_get_float(lower, 1) == FloatDir.DOWN:
                c16 += 1 << score_group_shifts.get(lower.score, 0)
            if _bbp_get_float(higher, 1) == FloatDir.DOWN:
                c16 += 1 << score_group_shifts.get(higher.score, 0)
        result |= c16
        breakdown["C16_dscore_R-1"] = (c16, SB)

        # C17 (SB): opponent scores of upfloaters from R-1
        result <<= SB
        c17 = 0
        if mask:
            if not (_bbp_get_float(lower, 1) == FloatDir.UP
                    and higher.score > lower.score):
                c17 |= 1 << score_group_shifts.get(higher.score, 0)
        result |= c17
        breakdown["C17_uscore_R-1"] = (c17, SB)

    # C18/C19 (rounds_played >= 2)
    if rounds_played >= 2:
        result <<= SB
        c18 = 0
        if mask:
            if _bbp_get_float(lower, 2) == FloatDir.DOWN:
                c18 += 1 << score_group_shifts.get(lower.score, 0)
            if _bbp_get_float(higher, 2) == FloatDir.DOWN:
                c18 += 1 << score_group_shifts.get(higher.score, 0)
        result |= c18
        breakdown["C18_dscore_R-2"] = (c18, SB)

        result <<= SB
        c19 = 0
        if mask:
            if not (_bbp_get_float(lower, 2) == FloatDir.UP
                    and higher.score > lower.score):
                c19 |= 1 << score_group_shifts.get(higher.score, 0)
        result |= c19
        breakdown["C19_uscore_R-2"] = (c19, SB)

    # Reserved bits: 3*B + 1
    result <<= B
    result <<= B
    result <<= B
    result <<= 1
    breakdown["RESERVED"] = (0, 3 * B + 1)

    return result, breakdown


# ---------------------------------------------------------------------------
# 2. Mirrored "ours" implementation, parameterized to use the SAME widths
#    as the bbp port. Mirrors src/.../dutch.py::_compute_bracket_edge_weight
#    so we can diff the per-tier values directly.
# ---------------------------------------------------------------------------

def ours_compute_edge_weight(
    p1: DutchPlayer,  # higher
    p2: DutchPlayer,  # lower
    in_current_bracket: bool,
    in_next_bracket: bool,
    bye_assignee_score: float,
    rounds_played: int,
    B: int,
    SB: int,
    score_group_shifts: Dict[float, int],
    is_single_downfloater_bye_assignee: bool,
    unplayed_game_ranks: Dict[int, int],
    bye_candidates: set,
    compatible: bool,
) -> Tuple[int, "OrderedDict[str, Tuple[int, int]]"]:
    """Re-derivation of our engine's _compute_bracket_edge_weight, using
    the SAME bit widths (B, SB) as the bbp port so that any per-tier
    delta is a real value disagreement, not a width disagreement."""
    breakdown: "OrderedDict[str, Tuple[int, int]]" = OrderedDict()
    if not compatible:
        breakdown["INCOMPATIBLE"] = (0, 0)
        return 0, breakdown

    pref1, pref2 = p1.color_preference, p2.color_preference
    str1, str2 = p1.preference_strength, p2.preference_strength
    mask = in_current_bracket

    def _score_bits(score: float) -> int:
        if score in score_group_shifts:
            return 1 << score_group_shifts[score]
        return 0

    def _fb(p: DutchPlayer, k: int) -> FloatDir:
        idx = len(p.float_hist) - k
        return p.float_hist[idx] if idx >= 0 else FloatDir.NONE

    def _rep(p: DutchPlayer) -> Optional[str]:
        ch = p.color_hist
        if len(ch) >= 2 and ch[-1] == ch[-2]:
            return ch[-1]
        return None

    p1_bye = p1.id in bye_candidates
    p2_bye = p2.id in bye_candidates
    completion = 1 + (not p1_bye) + (not p2_bye)
    breakdown["completion"] = (completion, 2)
    result = completion

    result <<= B
    t1 = 1 if in_current_bracket else 0
    result |= t1
    breakdown["T1_pairs_current"] = (t1, B)

    result <<= SB
    t2 = _score_bits(p1.score) if in_current_bracket else 0
    result |= t2
    breakdown["T2_scores_current"] = (t2, SB)

    result <<= B
    t3 = 1 if in_next_bracket else 0
    result |= t3
    breakdown["T3_pairs_next"] = (t3, B)

    result <<= SB
    t4 = _score_bits(p1.score) if in_next_bracket else 0
    result |= t4
    breakdown["T4_scores_next"] = (t4, SB)

    result <<= 2 * B
    c9 = 0
    if is_single_downfloater_bye_assignee:
        if p1.score == bye_assignee_score:
            c9 |= unplayed_game_ranks.get(len(p1.opponents), 0)
        if p2.score == bye_assignee_score:
            c9 += unplayed_game_ranks.get(len(p2.opponents), 0)
    result |= c9
    breakdown["C9_bye_unplayed"] = (c9, 2 * B)

    # Color tier (LSB->MSB inside this section): c_imb, c_absP, c_compat, c_strong
    result <<= B
    c_imb = 1 if (mask and (
        abs(p1.color_diff) < 2 or abs(p2.color_diff) < 2
        or pref1 != pref2 or pref1 == ColorPref.NONE
    )) else 0
    result |= c_imb
    breakdown["c_imb"] = (c_imb, B)

    result <<= B
    c_absp = 0
    if mask:
        p1_abs = (str1 == 3)
        p2_abs = (str2 == 3)
        if not (p2_abs and p1_abs and pref1 == pref2):
            c_absp = 1
        else:
            li = abs(p2.color_diff)
            hi = abs(p1.color_diff)
            l_rep = _rep(p2)
            h_rep = _rep(p1)
            if li == hi:
                if l_rep is None or l_rep != h_rep:
                    c_absp = 1
            else:
                lower_rep = h_rep if li > hi else l_rep
                inv_pref = ('black' if pref2 == ColorPref.WHITE
                            else 'white' if pref2 == ColorPref.BLACK
                            else None)
                if lower_rep is None or lower_rep != inv_pref:
                    c_absp = 1
    result |= c_absp
    breakdown["c_absP"] = (c_absp, B)

    result <<= B
    c_compat = 1 if (mask and (
        pref1 == ColorPref.NONE or pref2 == ColorPref.NONE or pref1 != pref2
    )) else 0
    result |= c_compat
    breakdown["c_compat"] = (c_compat, B)

    result <<= B
    c_strong = 1 if mask else 0
    if mask and pref1 == pref2 and pref1 != ColorPref.NONE \
            and str1 >= 2 and str2 >= 2 and not (str1 == 3 and str2 == 3):
        c_strong = 0
    result |= c_strong
    breakdown["c_strong"] = (c_strong, B)

    if rounds_played >= 1:
        result <<= B
        c12 = 0
        if mask:
            if _fb(p2, 1) == FloatDir.DOWN:
                c12 |= 1
            if (p1.score <= p2.score
                    and _fb(p1, 1) == FloatDir.DOWN):
                c12 += 1
        result |= c12
        breakdown["C12_down_R-1"] = (c12, B)

        result <<= B
        c13 = 0
        if mask:
            c13 = 0 if (_fb(p2, 1) == FloatDir.UP and p1.score > p2.score) else 1
        result |= c13
        breakdown["C13_up_R-1"] = (c13, B)

    if rounds_played >= 2:
        result <<= B
        c14 = 0
        if mask:
            if _fb(p2, 2) == FloatDir.DOWN:
                c14 |= 1
            if (p1.score <= p2.score
                    and _fb(p1, 2) == FloatDir.DOWN):
                c14 += 1
        result |= c14
        breakdown["C14_down_R-2"] = (c14, B)

        result <<= B
        c15 = 0
        if mask:
            c15 = 0 if (_fb(p2, 2) == FloatDir.UP and p1.score > p2.score) else 1
        result |= c15
        breakdown["C15_up_R-2"] = (c15, B)

    if rounds_played >= 1:
        result <<= SB
        c16 = 0
        if mask:
            if _fb(p2, 1) == FloatDir.DOWN:
                c16 += _score_bits(p2.score)
            if _fb(p1, 1) == FloatDir.DOWN:
                c16 += _score_bits(p1.score)
        result |= c16
        breakdown["C16_dscore_R-1"] = (c16, SB)

        result <<= SB
        c17 = 0
        if mask:
            if not (_fb(p2, 1) == FloatDir.UP and p1.score > p2.score):
                c17 |= _score_bits(p1.score)
        result |= c17
        breakdown["C17_uscore_R-1"] = (c17, SB)

    if rounds_played >= 2:
        result <<= SB
        c18 = 0
        if mask:
            if _fb(p2, 2) == FloatDir.DOWN:
                c18 += _score_bits(p2.score)
            if _fb(p1, 2) == FloatDir.DOWN:
                c18 += _score_bits(p1.score)
        result |= c18
        breakdown["C18_dscore_R-2"] = (c18, SB)

        result <<= SB
        c19 = 0
        if mask:
            if not (_fb(p2, 2) == FloatDir.UP and p1.score > p2.score):
                c19 |= _score_bits(p1.score)
        result |= c19
        breakdown["C19_uscore_R-2"] = (c19, SB)

    result <<= 3 * B + 1
    breakdown["RESERVED"] = (0, 3 * B + 1)

    return result, breakdown


# ---------------------------------------------------------------------------
# 3. Driver: replay state through round R-1, build engine, build bracket,
#    compute weights both ways for every candidate pair, print diff table.
# ---------------------------------------------------------------------------

def bbp_generate(num_players: int, num_rounds: int, seed: int, output_path: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as cfg:
        cfg.write(f"PlayersNumber={num_players}\nRoundsNumber={num_rounds}\n")
        cfg_path = cfg.name
    try:
        subprocess.run(
            [str(BBP_BINARY), "--dutch", "-g", cfg_path, "-o", output_path,
             "-s", str(seed)],
            check=True, capture_output=True, timeout=30,
        )
    finally:
        os.unlink(cfg_path)


def extract_trf_round(player_map: Dict[int, dict], rnd: int) -> List[dict]:
    pairings: List[dict] = []
    seen: set = set()
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


def _compute_bbp_widths(
    sorted_players: Sequence[DutchPlayer],
) -> Tuple[int, int, Dict[float, int]]:
    """Mirror of dutch.cpp lines 685-715. Returns (scoreGroupSizeBits,
    scoreGroupsShift, scoreGroupShifts)."""
    score_group_shifts: Dict[float, int] = {}
    score_groups_shift = 0
    max_size = 0
    rev = list(reversed(sorted_players))
    i = 0
    while i < len(rev):
        score = rev[i].score
        j = i
        while j < len(rev) and rev[j].score == score:
            j += 1
        size = j - i
        score_group_shifts[score] = score_groups_shift
        bits = max(1, size.bit_length())
        score_groups_shift += bits
        max_size = max(max_size, size)
        i = j
    score_group_size_bits = max(1, max_size.bit_length())
    return score_group_size_bits, score_groups_shift, score_group_shifts


def _round_pairs_full(round_pairings: Sequence[dict]) -> List[Tuple[int, int]]:
    """Return list of (a,b) pairs (normalized) for all non-bye games."""
    out = []
    for p in round_pairings:
        if p.get("bye"):
            continue
        b = p.get("black") or p.get("black_id")
        w = p.get("white") or p.get("white_id")
        if b is None or w is None:
            continue
        out.append(_normalize_pair(int(w), int(b)))
    return out


def _round_bye(round_pairings: Sequence[dict]) -> Optional[int]:
    for p in round_pairings:
        if p.get("bye") or p.get("black") is None and p.get("black_id") is None:
            w = p.get("white") or p.get("white_id")
            if w is not None:
                return int(w)
    return None


def _build_state_for_round(
    trf_content: str,
    rnd: int,
) -> Tuple[DutchEngine, List[DutchPlayer], List[dict], List[dict]]:
    """Replay TRF state through round rnd-1, return engine + sorted players
    + the bbp-output pairings for round rnd + the engine-produced pairings
    for round rnd."""
    parsed = TRFParser(trf_content).parse()
    players_list = parsed["players"]
    tournament = parsed["tournament"]
    total_rounds = tournament.get("total_rounds") or max(
        max(p.get("results", {}).keys(), default=0) for p in players_list
    )
    player_map = {p["starting_number"]: p for p in players_list}
    initial_color = _infer_initial_color(player_map)

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
    trf_round_pairs = extract_trf_round(player_map, rnd)

    sorted_players = sorted(
        engine._players,
        key=lambda p: (-p.score, p.pairing_number),
    )
    return engine, sorted_players, trf_round_pairs, engine_output


def _format_breakdown_diff(
    ours: "OrderedDict[str, Tuple[int, int]]",
    bbp: "OrderedDict[str, Tuple[int, int]]",
) -> str:
    keys = list(ours.keys()) if len(ours) >= len(bbp) else list(bbp.keys())
    lines = []
    for k in keys:
        ov, ow = ours.get(k, (0, 0))
        bv, bw = bbp.get(k, (0, 0))
        marker = "  " if ov == bv and ow == bw else "* "
        lines.append(f"    {marker}{k:<20s}  ours={ov:>6d}/{ow:<3d}b   bbp={bv:>6d}/{bw:<3d}b")
    return "\n".join(lines)


def _normalize_pair(a: int, b: int) -> Tuple[int, int]:
    return (min(a, b), max(a, b))


def _round_pair_set(round_pairings: Sequence[dict]) -> set:
    out = set()
    for p in round_pairings:
        if p.get("bye"):
            continue
        b = p.get("black") or p.get("black_id")
        w = p.get("white") or p.get("white_id")
        if b is None or w is None:
            continue
        out.add(_normalize_pair(int(w), int(b)))
    return out


def _bracket_for_score(
    sorted_players: List[DutchPlayer],
    target_score: float,
) -> Tuple[List[DutchPlayer], List[DutchPlayer]]:
    """Return (mdps, current_bracket_players) where mdps are players with
    higher score than target_score (the moved-down players for this
    bracket) and current_bracket_players are exactly at target_score.
    """
    mdps = [p for p in sorted_players if p.score > target_score]
    current = [p for p in sorted_players if p.score == target_score]
    return mdps, current


def _print_pair_table(
    title: str,
    pairs: List[Tuple[DutchPlayer, DutchPlayer, int, int,
                      "OrderedDict[str, Tuple[int, int]]",
                      "OrderedDict[str, Tuple[int, int]]"]],
    highlight: set,
) -> None:
    print(f"\n  {title}")
    print(f"  {'pair':<16s}  {'ours_w':>20s}  {'bbp_w':>20s}  diff")
    print(f"  {'-' * 16}  {'-' * 20}  {'-' * 20}  ----")
    for ph, pl, ow, bw, _ob, _bb in pairs:
        pair_str = f"({ph.id:>2d},{pl.id:>2d})"
        diff = "EQ" if ow == bw else f"Δ={ow - bw:+d}"
        marker = " <- div" if _normalize_pair(ph.id, pl.id) in highlight else ""
        print(f"  {pair_str:<16s}  {ow:>20d}  {bw:>20d}  {diff}{marker}")


def diagnose_score_group(
    engine: DutchEngine,
    sorted_players: List[DutchPlayer],
    target_score: float,
    diverging_pairs: set,
    rnd: int,
) -> None:
    mdps, current = _bracket_for_score(sorted_players, target_score)
    bracket = mdps + current
    n = len(bracket)
    if n < 2:
        print(f"  (score group {target_score} has fewer than 2 players, skipping)")
        return

    print(f"\n=== Score group {target_score} (round {rnd}) — "
          f"{len(mdps)} MDP(s) + {len(current)} resident players ===")
    for p in bracket:
        cls = "MDP" if p.score > target_score else "RES"
        print(f"  [{cls}] P{p.id:<2d} pn={p.pairing_number:<2d} "
              f"score={p.score} pref={p.color_preference.value}({p.preference_strength}) "
              f"diff={p.color_diff:+d} hist={p.color_hist} "
              f"floats={[f.value for f in p.float_hist]} "
              f"opps={sorted(p.opponents)}")

    # Compute bbp-style widths from FULL sorted player list (not bracket),
    # because that's how bbp does it (whole-tournament shifts).
    score_group_size_bits, score_groups_shift, score_group_shifts = \
        _compute_bbp_widths(sorted_players)
    B_bbp = score_group_size_bits
    SB_bbp = score_groups_shift

    rounds_played = rnd - 1
    bye_assignee_score = target_score  # approximation for the bracket view
    is_single_downfloater_bye_assignee = False
    unplayed_game_ranks: Dict[int, int] = {}
    # Match bbp semantics: a player is a bye candidate iff score <=
    # byeAssigneeScore AND eligibleForBye. We approximate eligibleForBye
    # as "has not received a bye yet" — sufficient for most rounds.
    eligible_for_bye: Dict[int, bool] = {
        p.id: True for p in sorted_players  # TODO: refine if needed
    }
    bye_candidates_set: set = {
        p.id for p in sorted_players
        if p.score <= bye_assignee_score and eligible_for_bye[p.id]
    }

    rows = []
    for i in range(n):
        for j in range(i + 1, n):
            ph = bracket[i]  # higher-ranked
            pl = bracket[j]  # lower-ranked
            in_current = (pl.score == target_score)
            in_next = False
            compatible = engine._can_pair(ph, pl)
            ow, ob = ours_compute_edge_weight(
                ph, pl, in_current, in_next, bye_assignee_score,
                rounds_played, B_bbp, SB_bbp, score_group_shifts,
                is_single_downfloater_bye_assignee, unplayed_game_ranks,
                bye_candidates_set, compatible,
            )
            bw, bb = bbp_compute_edge_weight(
                ph, pl, in_current, in_next, bye_assignee_score,
                rounds_played, score_group_size_bits, score_groups_shift,
                score_group_shifts, is_single_downfloater_bye_assignee,
                unplayed_game_ranks, eligible_for_bye, compatible,
            )
            rows.append((ph, pl, ow, bw, ob, bb))

    rows.sort(key=lambda r: -r[3])

    _print_pair_table("All candidate pairs in bracket (sorted by bbp weight desc):",
                      rows, diverging_pairs)

    # Detail: any pair whose ours/bbp values disagree
    print("\n  Per-tier breakdown for disagreeing pairs (and divergent pairs):")
    any_shown = False
    for ph, pl, ow, bw, ob, bb in rows:
        pair_norm = _normalize_pair(ph.id, pl.id)
        is_div = pair_norm in diverging_pairs
        if ow == bw and not is_div:
            continue
        any_shown = True
        marker = " <- DIVERGENT PAIR" if is_div else ""
        print(f"\n  Pair ({ph.id},{pl.id})  "
              f"ours={ow}  bbp={bw}  delta={ow - bw:+d}{marker}")
        print(_format_breakdown_diff(ob, bb))
    if not any_shown:
        print("    (all per-tier values agree across this bracket)")


def main() -> int:
    if len(sys.argv) < 5:
        print(__doc__)
        return 1
    num_players = int(sys.argv[1])
    num_rounds = int(sys.argv[2])
    seed = int(sys.argv[3])
    rnd = int(sys.argv[4])
    target_score = float(sys.argv[5]) if len(sys.argv) > 5 else None

    print(f"Comparing edge weights: {num_players}p/{num_rounds}r seed={seed} round={rnd}")
    print("=" * 72)

    with tempfile.TemporaryDirectory() as tmpdir:
        trf_path = os.path.join(tmpdir, f"bbp_{seed}.trf")
        bbp_generate(num_players, num_rounds, seed, trf_path)
        with open(trf_path) as f:
            trf_content = f.read()

    engine, sorted_players, trf_pairs, engine_pairs = \
        _build_state_for_round(trf_content, rnd)

    bbp_pair_set = _round_pair_set(trf_pairs)
    ours_pair_set = _round_pair_set(engine_pairs)
    only_ours = ours_pair_set - bbp_pair_set
    only_bbp = bbp_pair_set - ours_pair_set
    diverging = only_ours | only_bbp

    if not diverging and target_score is None:
        print("No divergence in this round — round-level pairings match bbpPairings.")
        return 0

    print(f"Round {rnd} divergence summary:")
    print(f"  pairs only in ours: {sorted(only_ours)}")
    print(f"  pairs only in bbp:  {sorted(only_bbp)}")
    print(f"  bye in ours: {_round_bye(engine_pairs)}  "
          f"bye in bbp: {_round_bye(trf_pairs)}")

    # === Preliminary MWM trace (mirrors _pair_iterative_mwm lines 1689-1736)
    # The unmatched player from the preliminary MWM determines
    # byeAssigneeScore, and is the first divergence point if bbp's
    # preliminary picks a different player.
    import networkx as nx  # type: ignore
    bbp_size_bits_p, bbp_groups_shift_p, bbp_shifts_p = \
        _compute_bbp_widths(sorted_players)
    n = len(sorted_players)
    if n % 2 == 1:
        top_score = sorted_players[0].score
        bye_eligible = {p.id for p in sorted_players}  # approximation
        prelim_G = nx.Graph()
        for vi in range(n):
            prelim_G.add_node(vi)
        for vi in range(n):
            for vj in range(vi + 1, n):
                pi, pj = sorted_players[vi], sorted_players[vj]
                if not engine._can_pair(pi, pj):
                    continue
                pw = (1 + (pi.id not in bye_eligible)
                      + (pj.id not in bye_eligible))
                pw <<= bbp_groups_shift_p
                pw |= (bbp_shifts_p.get(pi.score, 0)
                       + bbp_shifts_p.get(pj.score, 0))
                pw <<= bbp_size_bits_p
                pw |= int(pj.score >= top_score)
                prelim_G.add_edge(vi, vj, weight=pw)
        prelim_m = nx.max_weight_matching(prelim_G, maxcardinality=True)
        matched_idx: set = set()
        for u, v in prelim_m:
            matched_idx.add(u)
            matched_idx.add(v)
        prelim_unmatched = [
            sorted_players[vi].id for vi in range(n) if vi not in matched_idx
        ]
        print(f"  preliminary MWM unmatched (= bye assignee score donor): "
              f"{prelim_unmatched}")
        if prelim_unmatched:
            uid = prelim_unmatched[0]
            up = next(p for p in sorted_players if p.id == uid)
            print(f"    -> byeAssigneeScore = {up.score}")
        prelim_pairs = sorted(
            (min(sorted_players[u].id, sorted_players[v].id),
             max(sorted_players[u].id, sorted_players[v].id))
            for u, v in prelim_m
        )
        print(f"  preliminary MWM full pairing: {prelim_pairs}")
    print(f"  full ours pairing: {sorted(_round_pairs_full(engine_pairs))}")
    print(f"  full bbp pairing:  {sorted(_round_pairs_full(trf_pairs))}")

    # Compute the total weights of both matchings using the bbp port —
    # this directly answers whether bbp finds a higher-weight matching
    # under the same edge weights.
    bbp_size_bits, bbp_groups_shift, bbp_shifts = \
        _compute_bbp_widths(sorted_players)
    p_by_id = {p.id: p for p in sorted_players}

    def _pair_weight(a: int, b: int) -> Tuple[int, int]:
        pa, pb = p_by_id[a], p_by_id[b]
        ph, pl = (pa, pb) if (pa.score, -pa.pairing_number) > \
                              (pb.score, -pb.pairing_number) else (pb, pa)
        compatible = engine._can_pair(ph, pl)
        ow, _ = ours_compute_edge_weight(
            ph, pl, True, False, ph.score, rnd - 1,
            bbp_size_bits, bbp_groups_shift, bbp_shifts,
            False, {}, {x.id for x in sorted_players}, compatible,
        )
        bw, _ = bbp_compute_edge_weight(
            ph, pl, True, False, ph.score, rnd - 1,
            bbp_size_bits, bbp_groups_shift, bbp_shifts,
            False, {}, {x.id: True for x in sorted_players}, compatible,
        )
        return ow, bw

    ours_total_o = ours_total_b = 0
    for a, b in _round_pairs_full(engine_pairs):
        ow, bw = _pair_weight(a, b)
        ours_total_o += ow
        ours_total_b += bw
    bbp_total_o = bbp_total_b = 0
    for a, b in _round_pairs_full(trf_pairs):
        ow, bw = _pair_weight(a, b)
        bbp_total_o += ow
        bbp_total_b += bw
    print()
    print(f"  GLOBAL MATCHING TOTALS (using bbp-style widths, all in-bracket):")
    print(f"    sum(ours pairs) under ours_w = {ours_total_o}")
    print(f"    sum(bbp  pairs) under ours_w = {bbp_total_o}  "
          f"(delta vs ours: {bbp_total_o - ours_total_o:+d})")
    print(f"    sum(ours pairs) under bbp_w  = {ours_total_b}")
    print(f"    sum(bbp  pairs) under bbp_w  = {bbp_total_b}  "
          f"(delta vs ours: {bbp_total_b - ours_total_b:+d})")
    if bbp_total_o > ours_total_o:
        print("    ==> bbp's matching has HIGHER total weight under our weights.")
        print("        Our MWM is failing to find the maximum — investigate "
              "matching algorithm, not edge weights.")
    elif bbp_total_o < ours_total_o:
        print("    ==> Our matching has HIGHER total weight under our weights.")
        print("        Our MWM is correct given our weights; bbp uses different")
        print("        weights or different tie-breaking. Compare per-edge bbp_w "
              "carefully.")
    else:
        print("    ==> Total weights are EQUAL — pure MWM tie-breaking case.")

    if target_score is not None:
        diagnose_score_group(engine, sorted_players, target_score, diverging, rnd)
    else:
        unique_scores = sorted({p.score for p in sorted_players}, reverse=True)
        for score in unique_scores:
            sg_ids = {p.id for p in sorted_players if p.score == score}
            sg_diverging = {pair for pair in diverging
                            if pair[0] in sg_ids or pair[1] in sg_ids}
            if not sg_diverging:
                continue
            diagnose_score_group(engine, sorted_players, score, sg_diverging, rnd)

    return 0


if __name__ == "__main__":
    sys.exit(main())
