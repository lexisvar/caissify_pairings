"""
caissify-pairings — Pluggable chess tournament pairing engines.

Public API:
    generate_pairings(system, players, previous_pairings, round_number,
                      total_rounds, **kwargs) -> List[dict]

Engines:
    "dutch"        — FIDE Dutch System (C.04.3, Feb 2026).
                     Optional ``accelerated=True`` enables Baku
                     Acceleration (FIDE C.04.5.1) for rounds 1-2.
    "round_robin"  — FIDE Berger Tables (FIDE Handbook §C.05).
                     Optional ``cycles=2`` for double round-robin.
    "casual"       — Simple Swiss for non-rated tournaments.
"""

from __future__ import annotations

__version__ = "0.4.7"

import warnings
from typing import List, Set, Tuple

from caissify_pairings.engines import available_systems, get_engine


class MissingFloatHistoryWarning(UserWarning):
    """
    Raised when ``generate_pairings`` is called from round >= 2 with
    ``previous_pairings`` non-empty but every player carries an empty
    ``float_history``.

    From R2 onward FIDE Dutch (C.04.A §C.5, C.6, C.10–C.13) requires
    float history to be tracked across rounds; without it the engine
    cannot honour the "no two consecutive same-direction floats" rules
    and will silently produce non-conformant pairings.

    Callers must compute and pass ``float_history`` for every player
    (one entry per played round, value in {"up", "down", "none"}).
    See README §"Caller responsibilities" for the contract.
    """


# Engines for which float_history is part of the FIDE algorithm.
_FLOAT_AWARE_SYSTEMS = frozenset({"dutch"})


def _warn_if_floats_missing(
    system: str,
    players: List[dict],
    previous_pairings,
    round_number: int,
) -> None:
    """
    Defensive warning for the most common downstream bug: caller forgot
    to populate float_history from R2 onward. We do not modify input
    or output — just flag the smoking-gun pattern so the bug is
    detectable instead of silent.
    """
    if system not in _FLOAT_AWARE_SYSTEMS:
        return
    if round_number < 2:
        return
    if not previous_pairings:
        return
    if not players:
        return
    if any(p.get("float_history") for p in players):
        return
    warnings.warn(
        (
            "generate_pairings(system=%r, round_number=%d) was called with "
            "previous_pairings non-empty but every player has an empty "
            "float_history. From R2 onward FIDE Dutch (C.04.A §C.5/C.6) "
            "requires float history; the engine will produce "
            "non-FIDE-conformant pairings without it. See README "
            "'Caller responsibilities' for the contract."
        ) % (system, round_number),
        MissingFloatHistoryWarning,
        stacklevel=3,
    )


def generate_pairings(
    system: str,
    players: List[dict],
    previous_pairings: Set[Tuple[int, int]],
    round_number: int,
    total_rounds: int,
    **kwargs,
) -> List[dict]:
    """
    Generate pairings using the specified pairing system.

    Args:
        system:             Engine name — "dutch", "swiss", etc.
        players:            List of player dicts (see BasePairingEngine).
        previous_pairings:  Set of (id_a, id_b) tuples from prior rounds.
        round_number:       1-based round number to pair.
        total_rounds:       Total rounds in the tournament.
        **kwargs:           Engine-specific options (bye_value, max_byes_per_player, …).

    Returns:
        List of pairing dicts with white_id, black_id, table, and optional
        bye/bye_type fields.

    Warns:
        MissingFloatHistoryWarning: if ``system="dutch"``, ``round_number >= 2``,
        ``previous_pairings`` is non-empty, and every player has an empty
        ``float_history``. This pattern silently produces non-FIDE-conformant
        pairings — see README "Caller responsibilities".
    """
    _warn_if_floats_missing(system, players, previous_pairings, round_number)
    engine_cls = get_engine(system)
    engine = engine_cls(
        players=players,
        previous_pairings=previous_pairings,
        round_number=round_number,
        total_rounds=total_rounds,
        **kwargs,
    )
    return engine.generate_pairings()
