"""
Cross-surface parity tests: ``generate_pairings`` and ``fpc.check_trf``
must produce the same Dutch pairings for the same logical state.

Backstory
---------
``caissify_pairings`` exposes two pairing surfaces that internally route
through the same :class:`DutchEngine`:

* ``generate_pairings(system="dutch", players=[...], previous_pairings=...)``
  — caller must pre-compute per-player ``float_history`` (and the rest of
  the per-round derived state).
* ``fpc.check_trf(trf_text)`` — receives a full TRF and infers
  ``float_history`` from per-round score progression before feeding the
  engine.

If a downstream caller forgets to populate ``float_history`` from R2
onward, the two surfaces silently disagree on bracket-internal pairings
that depend on FIDE C.04.A §C.5/C.6 (no two consecutive same-direction
floats). A user-visible report of this exact failure mode is captured in
``doc/issue_0_4_3/CAISSIFY_PAIRINGS_DIVERGENCE.md`` from the desktop
team.

What this file guarantees
-------------------------
1. The two surfaces ARE the same engine: when both receive identical
   per-player float history, they MUST produce identical pairings as a
   multiset of ``frozenset({white, black})``.
2. The "smoking-gun" pattern (``previous_pairings`` non-empty, every
   ``float_history`` empty, ``round_number >= 2``) emits a
   :class:`MissingFloatHistoryWarning` so the bug is detectable instead
   of silent.
3. The warning does NOT fire for round 1 or for systems other than
   ``dutch``.
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Tuple

import pytest

from caissify_pairings import (
    MissingFloatHistoryWarning,
    generate_pairings,
)
from caissify_pairings.fpc import check_trf

# --------------------------------------------------------------------------- #
# Shared fixture: the exact 10p/2r tournament from the desktop divergence
# report (doc/issue_0_4_3). 10 players, R1+R2 played, R3 to be paired.
# --------------------------------------------------------------------------- #

PLAYERS = [
    (1, "Carlsen, Magnus", 2840, 1503014),
    (2, "Kasparov, Garry", 2812, 4100018),
    (3, "Nakamura, Hikaru", 2810, 2016192),
    (4, "Kramnik, Vladimir", 2753, 4101588),
    (5, "Gukesh D", 2732, 46616543),
    (6, "Nepomniachtchi, Ian", 2729, 4168119),
    (7, "Topalov, Veselin", 2717, 2900084),
    (8, "Svidler, Peter", 2682, 4102142),
    (9, "Karpov, Anatoly", 2617, 4100026),
    (10, "Shirov, Alexei", 2604, 2209390),
]

R1 = [
    (1, 6, "1-0"),
    (7, 2, "0-1"),
    (3, 8, "0.5-0.5"),
    (9, 4, "1-0"),
    (5, 10, "0.5-0.5"),
]
R2 = [
    (2, 1, "1-0"),
    (10, 3, "0-1"),
    (4, 5, "0.5-0.5"),
    (6, 7, "1-0"),
    (8, 9, "1-0"),
]
# R3 as produced by generate_pairings when properly fed float_history
# (the FIDE-correct answer; matches what fpc.check_trf produces).
R3_TRF = [
    (3, 2, "1-0"),
    (1, 10, "0.5-0.5"),
    (7, 4, "1-0"),
    (5, 8, "0-1"),
    (9, 6, "1-0"),
]


def _score(result: str, side: str) -> float:
    if result == "1-0":
        return 1.0 if side == "w" else 0.0
    if result == "0-1":
        return 0.0 if side == "w" else 1.0
    if result == "0.5-0.5":
        return 0.5
    return 0.0


def _state_after(rounds: List[List[Tuple[int, int, str]]]):
    """Reduce played rounds into (scores, color_hist, prev_pairings)."""
    scores: Dict[int, float] = {sn: 0.0 for sn, *_ in PLAYERS}
    chist: Dict[int, list] = {sn: [] for sn, *_ in PLAYERS}
    prev = set()
    for played in rounds:
        for w, b, r in played:
            scores[w] += _score(r, "w")
            scores[b] += _score(r, "b")
            chist[w].append("white")
            chist[b].append("black")
            prev.add((min(w, b), max(w, b)))
    return scores, chist, prev


def _infer_float_history(rounds: List[List[Tuple[int, int, str]]]) -> Dict[int, list]:
    """Mirror fpc's float-direction inference from pre-round score deltas."""
    floats: Dict[int, list] = {sn: [] for sn, *_ in PLAYERS}
    pre: Dict[int, float] = {sn: 0.0 for sn, *_ in PLAYERS}
    for played in rounds:
        snapshot = dict(pre)
        for w, b, _r in played:
            ws, bs = snapshot[w], snapshot[b]
            if ws > bs:
                floats[w].append("down")
                floats[b].append("up")
            elif ws < bs:
                floats[w].append("up")
                floats[b].append("down")
            else:
                floats[w].append("none")
                floats[b].append("none")
        for w, b, r in played:
            pre[w] += _score(r, "w")
            pre[b] += _score(r, "b")
    return floats


def _build_players(
    scores, chist, floats=None
) -> List[dict]:
    if floats is None:
        floats = {sn: [] for sn, *_ in PLAYERS}
    return [
        {
            "id": sn,
            "name": name,
            "score": scores[sn],
            "rating": rating,
            "starting_number": sn,
            "color_hist": chist[sn],
            "float_history": floats[sn],
            "bye_count": 0,
        }
        for sn, name, rating, _ in PLAYERS
    ]


def _build_trf(include_r3: bool = True) -> str:
    """TRF in the exact shape `caissify_tm/src/lib/trf.ts` emits."""
    rounds_played = [R1, R2] + ([R3_TRF] if include_r3 else [])
    points_after: Dict[int, float] = {sn: 0.0 for sn, *_ in PLAYERS}
    for played in rounds_played:
        for w, b, r in played:
            points_after[w] += _score(r, "w")
            points_after[b] += _score(r, "b")

    lines = [
        "012 Parity Test Tournament",
        "022 Sydney",
        "032 AUS",
        "042 2026/04/24",
        "052 2026/04/29",
        "062 10",
        "072 10",
        "082 0",
        "092 Individual: Swiss-System (FIDE Dutch)",
        "122 90+30",
        "XXR 9",
    ]
    ranked = sorted(PLAYERS, key=lambda p: (-points_after[p[0]], -p[2], p[1]))
    rank_of = {p[0]: i + 1 for i, p in enumerate(ranked)}

    def fmt_block(sn: int, played: List[Tuple[int, int, str]]) -> str:
        for w, b, r in played:
            if w == sn or b == sn:
                opp = b if w == sn else w
                color = "w" if w == sn else "b"
                if r == "1-0":
                    code = "1" if color == "w" else "0"
                elif r == "0-1":
                    code = "0" if color == "w" else "1"
                elif r == "0.5-0.5":
                    code = "="
                else:
                    code = " "
                return f" {opp:>4} {color} {code}"
        return f" {0:>4} - {' '}"

    for sn, name, rating, fide in PLAYERS:
        line = (
            f"001 {sn:>4}      "
            f"{name:<33}"
            f"{rating:>4} "
            f"AUS "
            f"{fide:>11} "
            f"            "
            f"{points_after[sn]:>4.1f} "
            f"{rank_of[sn]:>4}"
        )
        for round_idx in range(1, 4):
            played = (
                rounds_played[round_idx - 1]
                if round_idx - 1 < len(rounds_played)
                else []
            )
            line += " " + fmt_block(sn, played)
        lines.append(line)

    return "\r\n".join(lines) + "\r\n"


def _as_pair_set(pairings) -> set:
    """Normalise either engine output (list of dicts) or fpc output
    (list of {'white','black'}) into a set of frozensets."""
    out = set()
    for p in pairings:
        w = p.get("white_id") if "white_id" in p else p.get("white")
        b = p.get("black_id") if "black_id" in p else p.get("black")
        if b is None:
            out.add(frozenset({w}))  # bye
        else:
            out.add(frozenset({w, b}))
    return out


# --------------------------------------------------------------------------- #
# 1) Surface parity — given the same input, the two paths agree.
# --------------------------------------------------------------------------- #


def test_generate_pairings_with_inferred_floats_matches_fpc_check_trf():
    """
    The two surfaces are the same DutchEngine. Given identical per-player
    float_history (the FIDE-conformant value), they must produce identical
    R3 pairings as a multiset of frozenset({white, black}).
    """
    scores, chist, _prev = _state_after([R1, R2])
    _, _, prev = _state_after([R1, R2])
    floats = _infer_float_history([R1, R2])
    players = _build_players(scores, chist, floats=floats)

    # Surface A: explicit generate_pairings, properly fed.
    a = generate_pairings(
        system="dutch",
        players=players,
        previous_pairings=prev,
        round_number=3,
        total_rounds=9,
    )

    # Surface B: full TRF round-trip via fpc.
    report = check_trf(_build_trf(include_r3=True))
    r3 = next(r for r in report["rounds"] if r["round"] == 3)
    b = r3["engine_pairings"]

    assert _as_pair_set(a) == _as_pair_set(b), (
        "generate_pairings (with inferred float_history) and fpc.check_trf "
        "disagree on R3 — the two pairing surfaces have drifted apart. "
        f"A={_as_pair_set(a)} B={_as_pair_set(b)}"
    )


def test_fpc_round_trips_its_own_engine_output():
    """
    The FIDE-conformant R3 in the fixture (R3_TRF) MUST round-trip
    through fpc.check_trf with zero discrepancies. If this regresses,
    either the engine or the TRF parser changed in an algorithm-affecting
    way and we want a loud red CI before shipping.
    """
    report = check_trf(_build_trf(include_r3=True))
    r3 = next(r for r in report["rounds"] if r["round"] == 3)
    assert r3["match"] is True, (
        f"R3 should round-trip cleanly through fpc; got discrepancies: "
        f"{r3.get('discrepancies')}"
    )


# --------------------------------------------------------------------------- #
# 2) Smoking-gun warning — empty float_history from R2 onward must warn.
# --------------------------------------------------------------------------- #


def test_warns_when_float_history_empty_from_round_2():
    """
    The desktop team's bug class: pass float_history=[] for everyone with
    previous_pairings non-empty. We must surface a warning naming the
    contract so future downstream callers don't silently ship
    non-FIDE-conformant pairings.
    """
    scores, chist, prev = _state_after([R1, R2])
    players = _build_players(scores, chist)  # no floats

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        generate_pairings(
            system="dutch",
            players=players,
            previous_pairings=prev,
            round_number=3,
            total_rounds=9,
        )

    msgs = [w for w in caught if issubclass(w.category, MissingFloatHistoryWarning)]
    assert msgs, (
        "Expected a MissingFloatHistoryWarning when calling generate_pairings "
        "with previous_pairings non-empty and every float_history empty"
    )
    assert "float_history" in str(msgs[0].message)


def test_no_warning_for_round_one():
    """Round 1 has no prior rounds, so empty float_history is correct."""
    players = _build_players(
        scores={sn: 0.0 for sn, *_ in PLAYERS},
        chist={sn: [] for sn, *_ in PLAYERS},
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        generate_pairings(
            system="dutch",
            players=players,
            previous_pairings=set(),
            round_number=1,
            total_rounds=9,
        )

    msgs = [w for w in caught if issubclass(w.category, MissingFloatHistoryWarning)]
    assert not msgs, (
        f"Round 1 must not trigger MissingFloatHistoryWarning; got {msgs}"
    )


def test_no_warning_when_any_player_has_floats():
    """
    Even partial float_history (e.g. one player with one float) is
    enough to assume the caller is tracking it. The warning is only
    for the all-empty smoking-gun pattern.
    """
    scores, chist, prev = _state_after([R1, R2])
    floats = {sn: [] for sn, *_ in PLAYERS}
    floats[5] = ["none", "down"]  # at least one player is being tracked
    players = _build_players(scores, chist, floats=floats)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        generate_pairings(
            system="dutch",
            players=players,
            previous_pairings=prev,
            round_number=3,
            total_rounds=9,
        )

    msgs = [w for w in caught if issubclass(w.category, MissingFloatHistoryWarning)]
    assert not msgs


def test_no_warning_for_non_dutch_systems():
    """
    The float_history contract is FIDE Dutch-specific (C.04.A). Other
    engines (round_robin, casual) must not be flagged.
    """
    scores, chist, prev = _state_after([R1, R2])
    players = _build_players(scores, chist)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            generate_pairings(
                system="casual",
                players=players,
                previous_pairings=prev,
                round_number=3,
                total_rounds=9,
            )
        except Exception:
            # Casual engine may have its own input requirements; we only
            # care that the warning does NOT fire for non-dutch systems.
            pass

    msgs = [w for w in caught if issubclass(w.category, MissingFloatHistoryWarning)]
    assert not msgs


@pytest.mark.parametrize("system", ["dutch"])
def test_warning_class_is_user_warning_subclass(system):
    """
    MissingFloatHistoryWarning is a UserWarning so it's visible by
    default in Python. If someone "fixes" it down to DeprecationWarning
    (silent by default) or generic Warning, this test catches it.
    """
    assert issubclass(MissingFloatHistoryWarning, UserWarning)
