"""
Round-robin pairing engine — FIDE Berger Tables (Handbook C.05).

Round-robin tournaments are scheduled in advance: every player meets every
other player exactly once over ``n - 1`` rounds (or twice over ``2(n - 1)``
rounds for a double round-robin). Pairings are not score-driven and never
change based on results, so this engine ignores ``previous_pairings``
(they are accepted only for API compatibility).

The schedule is generated using the **Berger Tables algorithm** specified
in the FIDE Handbook §C.05. The algorithm matches the published FIDE
tables exactly for any even ``n`` — see ``tests/test_round_robin_engine.py``
for the cross-checks against n = 4, 6, 8.

Odd player counts are handled by inserting a *phantom player* (number
``n + 1``); whoever is paired with the phantom in a given round receives
a bye for that round.

Double round-robin (``cycles=2``) plays the same Berger schedule a second
time with every pair's colours reversed, which is the FIDE convention.

Pairing-number assignment
-------------------------
Berger position ``1..n`` is taken from each player's
``starting_number`` (ascending). If two players share a starting number,
the lower ``id`` wins the lower position. This matches the typical
arbiter workflow: pairing numbers are fixed at the start of the
tournament (by drawing of lots or by rating), and every round of the
round-robin uses those numbers.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from caissify_pairings.base import BasePairingEngine


# ----------------------------------------------------------------------- core


def berger_round(n: int, round_number: int) -> List[Tuple[int, int]]:
    """
    Return the FIDE Berger pairings for round ``round_number`` of an
    ``n``-player round-robin (``n`` must be even).

    Pairings are returned as ``(white_pairing_number, black_pairing_number)``
    tuples. Pairing numbers are 1-based; player ``n`` is the "fixed" player
    in the Berger rotation.

    Verified against the FIDE Handbook §C.05 Berger Tables for
    ``n ∈ {4, 6, 8}``; the same algorithm extends to all even ``n``.
    """
    if n < 2 or n % 2 != 0:
        raise ValueError(f"berger_round requires even n >= 2 (got {n})")
    if not 1 <= round_number <= n - 1:
        raise ValueError(
            f"round_number must be in 1..{n - 1} for n={n} (got {round_number})"
        )

    pairs: List[Tuple[int, int]] = []

    # First pair always involves the fixed player (n).
    if round_number % 2 == 1:
        anchor = (round_number + 1) // 2
        pairs.append((anchor, n))  # anchor gets White
    else:
        anchor = n // 2 + round_number // 2
        pairs.append((n, anchor))  # fixed player gets White

    # Remaining (n/2 - 1) pairs come from the rotation around (1..n-1).
    for i in range(1, n // 2):
        a = ((anchor - 1 + i) % (n - 1)) + 1
        b = ((anchor - 1 - i) % (n - 1)) + 1
        pairs.append((a, b))  # `a` (the +i side) gets White

    return pairs


def berger_schedule(n: int) -> List[List[Tuple[int, int]]]:
    """Full ``n - 1`` round Berger schedule for ``n`` players (even ``n``)."""
    return [berger_round(n, r) for r in range(1, n)]


# ------------------------------------------------------------------- engine


class RoundRobinEngine(BasePairingEngine):
    """
    FIDE Berger round-robin engine.

    Constructor options (in addition to the base contract):

    cycles : int, default ``1``
        Number of full round-robin cycles. ``cycles=2`` plays a double
        round-robin (each pair meets twice with reversed colours).

    bye_type : str, default ``"U"``
        FIDE bye code applied when an odd player count produces a
        phantom-pair bye. Defaults to ``"U"`` (Pairing-Allocated Bye —
        the natural choice for a phantom partner).
    """

    name = "round_robin"

    def __init__(
        self,
        players: List[dict],
        previous_pairings: Set[Tuple[int, int]],
        round_number: int,
        total_rounds: int,
        *,
        cycles: int = 1,
        bye_type: str = "U",
        **kwargs,
    ):
        super().__init__(
            players=players,
            previous_pairings=previous_pairings,
            round_number=round_number,
            total_rounds=total_rounds,
            **kwargs,
        )
        if cycles < 1:
            raise ValueError(f"cycles must be >= 1 (got {cycles})")
        self.cycles = cycles
        self.bye_type = bye_type

    # ----------------------------------------------- public

    def generate_pairings(self) -> List[dict]:
        """Return this round's pairings."""
        n_real = len(self.players)
        if n_real < 2:
            raise ValueError("Round-robin requires at least 2 players")

        n = n_real if n_real % 2 == 0 else n_real + 1  # phantom adjustment
        rounds_per_cycle = n - 1
        max_rounds = rounds_per_cycle * self.cycles

        if not 1 <= self.round_number <= max_rounds:
            raise ValueError(
                f"round_number {self.round_number} out of range "
                f"1..{max_rounds} for {n_real} players × {self.cycles} cycle(s)"
            )

        cycle_index = (self.round_number - 1) // rounds_per_cycle  # 0 or 1
        round_in_cycle = ((self.round_number - 1) % rounds_per_cycle) + 1

        position_to_player = self._assign_pairing_numbers(n_real, n)

        raw_pairs = berger_round(n, round_in_cycle)
        if cycle_index % 2 == 1:
            raw_pairs = [(b, w) for (w, b) in raw_pairs]

        return self._materialise(raw_pairs, position_to_player)

    # ----------------------------------------------- helpers

    def _assign_pairing_numbers(
        self, n_real: int, n: int
    ) -> Dict[int, Optional[dict]]:
        """
        Map Berger pairing positions ``1..n`` to player dicts (or ``None``
        for the phantom slot when ``n_real`` is odd).
        """
        ordered = sorted(
            self.players,
            key=lambda p: (
                p.get("starting_number", 10**9),
                p["id"],
            ),
        )
        mapping: Dict[int, Optional[dict]] = {
            i + 1: ordered[i] for i in range(n_real)
        }
        if n_real < n:
            mapping[n] = None  # phantom occupies the last position
        return mapping

    def _materialise(
        self,
        raw_pairs: List[Tuple[int, int]],
        position_to_player: Dict[int, Optional[dict]],
    ) -> List[dict]:
        """Convert (white_pos, black_pos) pairs into the public output dict."""
        regular: List[dict] = []
        byes: List[dict] = []

        for white_pos, black_pos in raw_pairs:
            white = position_to_player.get(white_pos)
            black = position_to_player.get(black_pos)

            if white is None and black is None:
                # Should not happen with one phantom, but be defensive.
                continue

            if white is None:
                # Phantom would have played White → real player gets a bye.
                byes.append(self._bye_pairing(black))  # type: ignore[arg-type]
                continue
            if black is None:
                byes.append(self._bye_pairing(white))
                continue

            regular.append({
                "white_id": white["id"],
                "black_id": black["id"],
                "table": 0,
            })

        return self._number_tables(regular, byes, position_to_player)

    def _bye_pairing(self, player: dict) -> dict:
        return {
            "white_id": player["id"],
            "black_id": None,
            "table": 0,
            "bye": True,
            "bye_type": self.bye_type,
        }

    @staticmethod
    def _number_tables(
        regular: List[dict],
        byes: List[dict],
        position_to_player: Dict[int, Optional[dict]],
    ) -> List[dict]:
        """
        Number tables 1..k for regular games (in pairing-number order),
        then byes.
        """
        id_to_position = {
            p["id"]: pos
            for pos, p in position_to_player.items()
            if p is not None
        }

        def table_key(pairing: dict) -> int:
            wid = pairing["white_id"]
            bid = pairing.get("black_id")
            wpos = id_to_position.get(wid, 10**9)
            if bid is None:
                return wpos
            bpos = id_to_position.get(bid, 10**9)
            return min(wpos, bpos)

        regular_sorted = sorted(regular, key=table_key)
        byes_sorted = sorted(byes, key=table_key)

        table = 1
        for pairing in regular_sorted:
            pairing["table"] = table
            table += 1
        for pairing in byes_sorted:
            pairing["table"] = table
            table += 1

        return regular_sorted + byes_sorted


# Convention expected by the engine registry.
Engine = RoundRobinEngine
