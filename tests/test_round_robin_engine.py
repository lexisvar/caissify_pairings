"""
Unit tests for the FIDE Berger Round-Robin engine.

Two layers of testing:

1. **Algorithm-level** — ``berger_round`` / ``berger_schedule`` are
   compared against the published FIDE Berger Tables (Handbook §C.05)
   for ``n = 4, 6, 8``. These are the canonical reference values; if
   they pass, the algorithm matches FIDE for any even ``n``.

2. **Engine-level** — ``RoundRobinEngine`` is exercised through the
   public ``BasePairingEngine`` contract: registry lookup, multi-cycle
   schedules, odd-player byes, table numbering, immutability, etc.
"""

from __future__ import annotations

import copy
import unittest
from typing import Dict, List, Set, Tuple

from caissify_pairings.engines import available_systems, get_engine
from caissify_pairings.engines.round_robin import (
    RoundRobinEngine,
    berger_round,
    berger_schedule,
)


# ----------------------------------------------------------------- helpers


def _players(count: int) -> List[dict]:
    """Build ``count`` players with sequential starting numbers."""
    base_rating = 2400
    return [
        {
            "id": 100 + i,
            "name": f"P{i}",
            "score": 0.0,
            "rating": base_rating - (i - 1) * 50,
            "starting_number": i,
            "color_hist": [],
            "float_history": [],
            "bye_count": 0,
        }
        for i in range(1, count + 1)
    ]


def _pair(players: List[dict], round_number: int, *, total_rounds: int = 0,
          **kwargs) -> List[dict]:
    if total_rounds == 0:
        total_rounds = max(1, len(players) - 1)
    eng = RoundRobinEngine(
        players=players,
        previous_pairings=set(),
        round_number=round_number,
        total_rounds=total_rounds,
        **kwargs,
    )
    return eng.generate_pairings()


# ---------------------------------------------------- algorithm reference

# Reference values from FIDE Handbook §C.05 (Berger Tables).
# Each entry is round → list of (white_pairing_no, black_pairing_no).

FIDE_BERGER_N4 = {
    1: [(1, 4), (2, 3)],
    2: [(4, 3), (1, 2)],
    3: [(2, 4), (3, 1)],
}

FIDE_BERGER_N6 = {
    1: [(1, 6), (2, 5), (3, 4)],
    2: [(6, 4), (5, 3), (1, 2)],
    3: [(2, 6), (3, 1), (4, 5)],
    4: [(6, 5), (1, 4), (2, 3)],
    5: [(3, 6), (4, 2), (5, 1)],
}

FIDE_BERGER_N8 = {
    1: [(1, 8), (2, 7), (3, 6), (4, 5)],
    2: [(8, 5), (6, 4), (7, 3), (1, 2)],
    3: [(2, 8), (3, 1), (4, 7), (5, 6)],
    4: [(8, 6), (7, 5), (1, 4), (2, 3)],
    5: [(3, 8), (4, 2), (5, 1), (6, 7)],
    6: [(8, 7), (1, 6), (2, 5), (3, 4)],
    7: [(4, 8), (5, 3), (6, 2), (7, 1)],
}


class TestBergerAgainstFIDETables(unittest.TestCase):
    def test_n4_matches_fide(self):
        for r, expected in FIDE_BERGER_N4.items():
            self.assertEqual(berger_round(4, r), expected, f"n=4 round {r}")

    def test_n6_matches_fide(self):
        for r, expected in FIDE_BERGER_N6.items():
            self.assertEqual(berger_round(6, r), expected, f"n=6 round {r}")

    def test_n8_matches_fide(self):
        for r, expected in FIDE_BERGER_N8.items():
            self.assertEqual(berger_round(8, r), expected, f"n=8 round {r}")

    def test_schedule_returns_all_rounds(self):
        sched = berger_schedule(8)
        self.assertEqual(len(sched), 7)
        for r in range(1, 8):
            self.assertEqual(sched[r - 1], FIDE_BERGER_N8[r])


class TestBergerInvariants(unittest.TestCase):
    """Invariants that must hold for any even n."""

    def _check_invariants(self, n: int) -> None:
        sched = berger_schedule(n)
        all_pairs: Set[frozenset[int]] = set()
        for r, round_pairs in enumerate(sched, start=1):
            self.assertEqual(
                len(round_pairs), n // 2,
                f"n={n} round {r}: expected {n//2} pairs, got {len(round_pairs)}",
            )
            seen_in_round: Set[int] = set()
            for w, b in round_pairs:
                self.assertNotIn(w, seen_in_round, f"n={n} R{r}: {w} paired twice")
                self.assertNotIn(b, seen_in_round, f"n={n} R{r}: {b} paired twice")
                seen_in_round.update((w, b))
                key = frozenset((w, b))
                self.assertNotIn(
                    key, all_pairs,
                    f"n={n}: pair {{{w}, {b}}} repeated across rounds",
                )
                all_pairs.add(key)
            self.assertEqual(seen_in_round, set(range(1, n + 1)))
        # After n-1 rounds, every unordered pair must have appeared.
        self.assertEqual(len(all_pairs), n * (n - 1) // 2)

    def test_invariants_n4(self):
        self._check_invariants(4)

    def test_invariants_n6(self):
        self._check_invariants(6)

    def test_invariants_n8(self):
        self._check_invariants(8)

    def test_invariants_n10(self):
        self._check_invariants(10)

    def test_invariants_n14(self):
        self._check_invariants(14)

    def test_invariants_n20(self):
        self._check_invariants(20)


class TestBergerColourBalance(unittest.TestCase):
    """Each player's white/black count over a single cycle must be balanced
    to within 1, which is the optimal bound for n-1 games."""

    def _check_colour_balance(self, n: int) -> None:
        sched = berger_schedule(n)
        whites = {i: 0 for i in range(1, n + 1)}
        blacks = {i: 0 for i in range(1, n + 1)}
        for round_pairs in sched:
            for w, b in round_pairs:
                whites[w] += 1
                blacks[b] += 1
        for pid in range(1, n + 1):
            diff = abs(whites[pid] - blacks[pid])
            self.assertLessEqual(
                diff, 1,
                f"n={n} player {pid}: W={whites[pid]} B={blacks[pid]} "
                f"(diff {diff} > 1)",
            )

    def test_balance_n4(self):
        self._check_colour_balance(4)

    def test_balance_n6(self):
        self._check_colour_balance(6)

    def test_balance_n8(self):
        self._check_colour_balance(8)

    def test_balance_n10(self):
        self._check_colour_balance(10)

    def test_balance_n16(self):
        self._check_colour_balance(16)


class TestBergerInputValidation(unittest.TestCase):
    def test_odd_n_raises(self):
        with self.assertRaises(ValueError):
            berger_round(5, 1)

    def test_round_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            berger_round(6, 0)
        with self.assertRaises(ValueError):
            berger_round(6, 6)


# ------------------------------------------------------ engine integration


class TestEngineRegistry(unittest.TestCase):
    def test_registered(self):
        self.assertIn("round_robin", available_systems())

    def test_get_engine(self):
        self.assertIs(get_engine("round_robin"), RoundRobinEngine)


class TestEngineEvenField(unittest.TestCase):
    def test_round1_pairings_match_berger(self):
        players = _players(6)  # ids 101..106, starting_number 1..6
        result = _pair(players, round_number=1)
        # Expected: 1-6, 2-5, 3-4 → 101-106, 102-105, 103-104.
        pairs = [(r["white_id"], r["black_id"]) for r in result]
        self.assertEqual(pairs, [(101, 106), (102, 105), (103, 104)])

    def test_no_byes_for_even_count(self):
        players = _players(8)
        for r in range(1, 8):
            result = _pair(players, round_number=r)
            self.assertFalse(any(p.get("bye") for p in result))
            self.assertEqual(len(result), 4)


class TestEngineOddField(unittest.TestCase):
    def test_one_bye_per_round(self):
        players = _players(5)  # phantom = position 6
        for r in range(1, 6):  # 5 rounds for 5 players (phantom adds 1)
            result = _pair(players, round_number=r)
            byes = [p for p in result if p.get("bye")]
            self.assertEqual(len(byes), 1, f"R{r}: expected 1 bye")
            self.assertEqual(byes[0]["bye_type"], "U")
            self.assertIsNone(byes[0]["black_id"])

    def test_every_player_byes_exactly_once(self):
        players = _players(7)  # 7 rounds (phantom → n=8)
        bye_recipients: List[int] = []
        for r in range(1, 8):
            result = _pair(players, round_number=r)
            byes = [p for p in result if p.get("bye")]
            self.assertEqual(len(byes), 1)
            bye_recipients.append(byes[0]["white_id"])
        self.assertEqual(sorted(bye_recipients), [101, 102, 103, 104, 105, 106, 107])


class TestEngineFullCycleCoverage(unittest.TestCase):
    def test_each_pair_meets_exactly_once_over_full_cycle(self):
        players = _players(8)
        seen: Set[frozenset[int]] = set()
        for r in range(1, 8):
            result = _pair(players, round_number=r)
            for pr in result:
                if pr.get("black_id") is None:
                    continue
                key = frozenset((pr["white_id"], pr["black_id"]))
                self.assertNotIn(key, seen, f"Pair {key} repeated in R{r}")
                seen.add(key)
        self.assertEqual(len(seen), 8 * 7 // 2)


class TestEngineDoubleRoundRobin(unittest.TestCase):
    def test_total_rounds_is_2_x_n_minus_1(self):
        players = _players(6)
        # Cycle 1: rounds 1-5; Cycle 2: rounds 6-10.
        # Round 6 should have the same pairs as round 1 with colours flipped.
        r1 = _pair(players, round_number=1, total_rounds=10, cycles=2)
        r6 = _pair(players, round_number=6, total_rounds=10, cycles=2)

        r1_pairs = {(p["white_id"], p["black_id"]) for p in r1}
        r6_pairs_flipped = {(p["black_id"], p["white_id"]) for p in r6}
        self.assertEqual(r1_pairs, r6_pairs_flipped)

    def test_each_pair_meets_exactly_twice_over_double_cycle(self):
        players = _players(6)
        meetings: Dict[frozenset[int], List[Tuple[int, int]]] = {}
        for r in range(1, 11):
            result = _pair(players, round_number=r, total_rounds=10, cycles=2)
            for pr in result:
                if pr.get("black_id") is None:
                    continue
                key = frozenset((pr["white_id"], pr["black_id"]))
                meetings.setdefault(key, []).append(
                    (pr["white_id"], pr["black_id"])
                )
        for key, games in meetings.items():
            self.assertEqual(
                len(games), 2,
                f"Pair {key} met {len(games)} times in 2 cycles, expected 2",
            )
            # Colours must be swapped between the two meetings.
            self.assertEqual(games[0], tuple(reversed(games[1])))


class TestEngineErrors(unittest.TestCase):
    def test_too_few_players(self):
        with self.assertRaises(ValueError):
            _pair(_players(1), round_number=1)

    def test_round_out_of_range(self):
        with self.assertRaises(ValueError):
            _pair(_players(6), round_number=6)  # only 5 rounds in a single cycle

    def test_invalid_cycles(self):
        with self.assertRaises(ValueError):
            RoundRobinEngine(
                players=_players(4), previous_pairings=set(),
                round_number=1, total_rounds=3, cycles=0,
            )


class TestEngineImmutability(unittest.TestCase):
    def test_does_not_mutate_input(self):
        players = _players(6)
        snapshot = copy.deepcopy(players)
        _pair(players, round_number=1)
        self.assertEqual(players, snapshot)

    def test_ignores_previous_pairings(self):
        # Round-robin schedule is deterministic; previous_pairings must
        # not influence the result.
        players = _players(6)
        eng_a = RoundRobinEngine(
            players=players, previous_pairings=set(),
            round_number=2, total_rounds=5,
        )
        eng_b = RoundRobinEngine(
            players=players, previous_pairings={(101, 106), (102, 105)},
            round_number=2, total_rounds=5,
        )
        self.assertEqual(eng_a.generate_pairings(), eng_b.generate_pairings())


class TestEngineTableNumbering(unittest.TestCase):
    def test_tables_are_sequential(self):
        players = _players(7)
        result = _pair(players, round_number=3)
        tables = [p["table"] for p in result]
        self.assertEqual(sorted(tables), list(range(1, len(result) + 1)))

    def test_byes_come_after_regular_games(self):
        players = _players(7)
        result = _pair(players, round_number=1)
        regular = [p for p in result if not p.get("bye")]
        byes = [p for p in result if p.get("bye")]
        if regular and byes:
            self.assertLess(
                max(p["table"] for p in regular),
                min(p["table"] for p in byes),
            )


class TestEngineDeterminism(unittest.TestCase):
    def test_two_calls_produce_identical_pairings(self):
        a = _players(8)
        b = copy.deepcopy(a)
        self.assertEqual(_pair(a, round_number=4), _pair(b, round_number=4))


class TestEnginePairingNumberAssignment(unittest.TestCase):
    def test_starting_number_drives_berger_position(self):
        # Shuffle the order in which players are passed in; the engine
        # must use starting_number to assign Berger positions.
        players = _players(4)
        shuffled = [players[2], players[0], players[3], players[1]]
        result = _pair(shuffled, round_number=1)
        # Same as canonical R1 for n=4: (1,4), (2,3) → ids 101-104, 102-103.
        pairs = [(r["white_id"], r["black_id"]) for r in result]
        self.assertEqual(pairs, [(101, 104), (102, 103)])


if __name__ == "__main__":
    unittest.main()
