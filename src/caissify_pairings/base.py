"""
Abstract base class for all pairing engines.

Every engine in caissify_pairings must subclass BasePairingEngine and
implement generate_pairings().  This guarantees a uniform I/O contract
across Dutch, Swiss, Burstein, and any future system.
"""

from __future__ import annotations

import abc
from typing import Dict, List, Set, Tuple


class BasePairingEngine(abc.ABC):
    """
    Abstract base for chess tournament pairing engines.

    Input contract (constructor):
        players:            List[dict] — each dict must contain at minimum:
                            id, name, score, rating, starting_number,
                            color_hist, float_history, bye_count
        previous_pairings:  Set[Tuple[int, int]] — all (id_a, id_b) pairs
                            from earlier rounds (order-insensitive)
        round_number:       int — 1-based round to pair
        total_rounds:       int — total rounds in the tournament

    Output contract (generate_pairings):
        List[dict] where each dict has:
            white_id:  int          — player ID assigned white
            black_id:  int | None   — player ID assigned black (None = bye)
            table:     int          — 1-based table number
            bye:       bool         — (optional) True for bye pairings
            bye_type:  str          — (optional) FIDE bye code, e.g. "U"
    """

    #: Override in subclass — must match the key used in the engine registry.
    name: str = ""

    def __init__(
        self,
        players: List[dict],
        previous_pairings: Set[Tuple[int, int]],
        round_number: int,
        total_rounds: int,
        **kwargs,
    ):
        self.players = players
        self.previous_pairings = previous_pairings
        self.round_number = round_number
        self.total_rounds = total_rounds
        self.options = kwargs

    @abc.abstractmethod
    def generate_pairings(self) -> List[dict]:
        """Generate pairings for the current round.  Must be implemented."""
        ...
