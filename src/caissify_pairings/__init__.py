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

__version__ = "0.4.1"

from typing import List, Set, Tuple

from caissify_pairings.engines import available_systems, get_engine


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
    """
    engine_cls = get_engine(system)
    engine = engine_cls(
        players=players,
        previous_pairings=previous_pairings,
        round_number=round_number,
        total_rounds=total_rounds,
        **kwargs,
    )
    return engine.generate_pairings()
