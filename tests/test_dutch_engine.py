"""
Unit tests for the FIDE Dutch System Pairing Engine (C.04.3).

Tests cover:
- Data structures & initial ordering (Phase 1.1)
- Absolute criteria / can_pair (Phase 1.2)
- S1/S2 splitting (Phase 1.3)
- Transpositions (Phase 1.4)
- Exchanges (Phase 1.5)
- Scoregroup pairing (Phase 1.3-1.6)
- Color allocation (Phase 1.8)
- Bye assignment (Phase 1.9)
- Quality metric (Phase 1.10)
- Full tournament simulations (integration)
"""

import unittest

from caissify_pairings.engines.dutch import (
    ColorPref,
    DutchEngine,
    DutchPlayer,
    FloatDir,
    dutch_pairings,
)


def _make_players(count, **overrides):
    """Helper: generate count player dicts with sequential ids and descending ratings."""
    base_rating = 2400
    players = []
    for i in range(1, count + 1):
        p = {
            "id": i,
            "name": f"Player{i}",
            "score": 0.0,
            "rating": base_rating - (i - 1) * 100,
            "starting_number": i,
        }
        p.update(overrides)
        players.append(p)
    return players


class TestDutchPlayerDataclass(unittest.TestCase):
    """Phase 1.1 — DutchPlayer properties."""

    def test_color_diff(self):
        p = DutchPlayer(id=1, name="A", score=0, rating=2000, pairing_number=1,
                        starting_number=1, color_hist=["white", "black", "white"])
        self.assertEqual(p.color_diff, 1)

    def test_color_diff_empty(self):
        p = DutchPlayer(id=1, name="A", score=0, rating=2000, pairing_number=1,
                        starting_number=1, color_hist=[])
        self.assertEqual(p.color_diff, 0)

    def test_color_preference_none(self):
        p = DutchPlayer(id=1, name="A", score=0, rating=2000, pairing_number=1,
                        starting_number=1, color_hist=[])
        self.assertEqual(p.color_preference, ColorPref.NONE)
        self.assertEqual(p.preference_strength, 0)

    def test_color_preference_mild(self):
        p = DutchPlayer(id=1, name="A", score=0, rating=2000, pairing_number=1,
                        starting_number=1, color_hist=["white"])
        self.assertEqual(p.color_preference, ColorPref.BLACK)
        self.assertEqual(p.preference_strength, 1)

    def test_color_preference_strong(self):
        p = DutchPlayer(id=1, name="A", score=0, rating=2000, pairing_number=1,
                        starting_number=1, color_hist=["white", "black", "white", "black", "white"])
        # diff = +1, last was white → mild for black

        p2 = DutchPlayer(id=2, name="B", score=0, rating=2000, pairing_number=2,
                         starting_number=2, color_hist=["white", "white", "black", "white"])
        # diff = +2 → strong for black
        self.assertEqual(p2.color_preference, ColorPref.BLACK)
        self.assertEqual(p2.preference_strength, 2)

    def test_color_preference_absolute(self):
        p = DutchPlayer(id=1, name="A", score=0, rating=2000, pairing_number=1,
                        starting_number=1, color_hist=["white", "white"])
        # Two whites in a row → absolute need for black
        self.assertEqual(p.color_preference, ColorPref.BLACK)
        self.assertEqual(p.preference_strength, 3)

    def test_would_violate_absolute_color_three_in_row(self):
        p = DutchPlayer(id=1, name="A", score=0, rating=2000, pairing_number=1,
                        starting_number=1, color_hist=["white", "white"])
        self.assertTrue(p.would_violate_absolute_color("white"))
        self.assertFalse(p.would_violate_absolute_color("black"))

    def test_would_violate_absolute_color_diff(self):
        p = DutchPlayer(id=1, name="A", score=0, rating=2000, pairing_number=1,
                        starting_number=1, color_hist=["white", "black", "white", "black", "white"])
        # diff = +1, adding white → +2 (ok), adding another white after that → +3 (violates)
        self.assertFalse(p.would_violate_absolute_color("white"))  # diff becomes +2

        p2 = DutchPlayer(id=2, name="B", score=0, rating=2000, pairing_number=2,
                         starting_number=2, color_hist=["white", "white", "black", "white"])
        # diff = +2, adding white → +3 (violates)
        self.assertTrue(p2.would_violate_absolute_color("white"))


class TestInitialOrdering(unittest.TestCase):
    """Phase 1.1 — Pairing number assignment."""

    def test_pairing_numbers_by_rating(self):
        players = _make_players(4)
        engine = DutchEngine(players, set(), 1, 5)
        pns = [(p.id, p.pairing_number) for p in engine._players]
        # Player 1 (2400) should be PN 1, Player 4 (2100) should be PN 4
        self.assertEqual(pns, [(1, 1), (2, 2), (3, 3), (4, 4)])

    def test_pairing_numbers_reverse_rating(self):
        """If players are given in reverse rating order, PNs still follow rating desc."""
        players = [
            {"id": 10, "name": "Low", "score": 0.0, "rating": 1500, "starting_number": 1},
            {"id": 20, "name": "High", "score": 0.0, "rating": 2500, "starting_number": 2},
        ]
        engine = DutchEngine(players, set(), 1, 5)
        # Player 20 (2500) → PN 1, Player 10 (1500) → PN 2
        pm = {p.id: p.pairing_number for p in engine._players}
        self.assertEqual(pm[20], 1)
        self.assertEqual(pm[10], 2)


class TestScoregroups(unittest.TestCase):
    """Phase 1.1 — Scoregroup building."""

    def test_two_scoregroups(self):
        players = [
            {"id": 1, "name": "A", "score": 1.0, "rating": 2400, "starting_number": 1},
            {"id": 2, "name": "B", "score": 1.0, "rating": 2300, "starting_number": 2},
            {"id": 3, "name": "C", "score": 0.0, "rating": 2200, "starting_number": 3},
            {"id": 4, "name": "D", "score": 0.0, "rating": 2100, "starting_number": 4},
        ]
        engine = DutchEngine(players, set(), 2, 5)
        sgs = engine._build_scoregroups(engine._players)
        self.assertEqual(len(sgs), 2)
        self.assertEqual(len(sgs[0]), 2)  # score 1.0
        self.assertEqual(len(sgs[1]), 2)  # score 0.0
        self.assertTrue(all(p.score == 1.0 for p in sgs[0]))
        self.assertTrue(all(p.score == 0.0 for p in sgs[1]))

    def test_scoregroup_internal_order(self):
        """Within a scoregroup, players sorted by pairing number ascending."""
        players = [
            {"id": 3, "name": "C", "score": 1.0, "rating": 2000, "starting_number": 3},
            {"id": 1, "name": "A", "score": 1.0, "rating": 2400, "starting_number": 1},
            {"id": 2, "name": "B", "score": 1.0, "rating": 2200, "starting_number": 2},
        ]
        engine = DutchEngine(players, set(), 2, 5)
        sgs = engine._build_scoregroups(engine._players)
        pns = [p.pairing_number for p in sgs[0]]
        self.assertEqual(pns, sorted(pns))


class TestCanPair(unittest.TestCase):
    """Phase 1.2 — Absolute criteria."""

    def test_no_repeat_opponents(self):
        players = _make_players(4)
        prev = {(1, 2)}  # 1 and 2 already played
        engine = DutchEngine(players, prev, 2, 5)
        p1 = engine._player_map[1]
        p2 = engine._player_map[2]
        p3 = engine._player_map[3]
        self.assertFalse(engine._can_pair(p1, p2))
        self.assertTrue(engine._can_pair(p1, p3))

    def test_color_absolute_violation(self):
        """Two players who both need the same color (absolute) cannot pair."""
        players = [
            {"id": 1, "name": "A", "score": 0.0, "rating": 2400, "starting_number": 1,
             "color_hist": ["white", "white"]},  # MUST get black
            {"id": 2, "name": "B", "score": 0.0, "rating": 2300, "starting_number": 2,
             "color_hist": ["white", "white"]},  # MUST get black
        ]
        engine = DutchEngine(players, set(), 3, 5)
        p1 = engine._player_map[1]
        p2 = engine._player_map[2]
        # Both absolutely need black → no legal color assignment
        self.assertFalse(engine._can_pair(p1, p2))


class TestS1S2Splitting(unittest.TestCase):
    """Phase 1.3 — Split scoregroup."""

    def test_even_split(self):
        players = _make_players(4)
        engine = DutchEngine(players, set(), 1, 5)
        group = sorted(engine._players, key=lambda p: p.pairing_number)
        s1, s2 = engine._split_scoregroup(group)
        self.assertEqual(len(s1), 2)
        self.assertEqual(len(s2), 2)
        self.assertEqual([p.pairing_number for p in s1], [1, 2])
        self.assertEqual([p.pairing_number for p in s2], [3, 4])

    def test_odd_split(self):
        players = _make_players(5)
        engine = DutchEngine(players, set(), 1, 5)
        group = sorted(engine._players, key=lambda p: p.pairing_number)
        s1, s2 = engine._split_scoregroup(group)
        self.assertEqual(len(s1), 2)  # S2 gets extra
        self.assertEqual(len(s2), 3)


class TestRound1Pairing(unittest.TestCase):
    """Round 1: top half vs bottom half."""

    def test_6_players_round1(self):
        players = _make_players(6)
        pairings = dutch_pairings(players, set(), 1, 5)
        self.assertEqual(len(pairings), 3)
        # Expected: 1v4, 2v5, 3v6 (by pairing number, not by id necessarily)
        paired = set()
        for p in pairings:
            self.assertFalse(p.get("bye"))
            paired.add(p["white_id"])
            paired.add(p["black_id"])
        self.assertEqual(paired, {1, 2, 3, 4, 5, 6})

    def test_table_numbers_sequential(self):
        players = _make_players(6)
        pairings = dutch_pairings(players, set(), 1, 5)
        tables = [p["table"] for p in pairings]
        self.assertEqual(tables, [1, 2, 3])


class TestByeAssignment(unittest.TestCase):
    """Phase 1.9 — Bye for odd player count."""

    def test_odd_players_get_bye(self):
        players = _make_players(5)
        pairings = dutch_pairings(players, set(), 1, 5)
        byes = [p for p in pairings if p.get("bye")]
        regular = [p for p in pairings if not p.get("bye")]
        self.assertEqual(len(byes), 1)
        self.assertEqual(len(regular), 2)
        self.assertEqual(byes[0]["bye_type"], "U")
        self.assertIsNone(byes[0]["black_id"])

    def test_bye_goes_to_lowest_rated(self):
        players = _make_players(5)
        pairings = dutch_pairings(players, set(), 1, 5)
        byes = [p for p in pairings if p.get("bye")]
        # Lowest-rated (highest pairing number, lowest score) gets bye
        self.assertEqual(byes[0]["white_id"], 5)

    def test_no_double_bye(self):
        """Player who already had a bye should not get another."""
        players = [
            {"id": 1, "name": "A", "score": 1.0, "rating": 2400, "starting_number": 1},
            {"id": 2, "name": "B", "score": 1.0, "rating": 2300, "starting_number": 2},
            {"id": 3, "name": "C", "score": 0.5, "rating": 2200, "starting_number": 3},
            {"id": 4, "name": "D", "score": 0.0, "rating": 2100, "starting_number": 4},
            {"id": 5, "name": "E", "score": 0.0, "rating": 2000, "starting_number": 5, "bye_count": 1},
        ]
        pairings = dutch_pairings(players, set(), 2, 5)
        byes = [p for p in pairings if p.get("bye")]
        self.assertEqual(len(byes), 1)
        # Player 5 already had bye → player 4 should get it
        self.assertEqual(byes[0]["white_id"], 4)


class TestColorAllocation(unittest.TestCase):
    """Phase 1.8 — Color assignment."""

    def test_round1_colors(self):
        """In round 1, higher-ranked player (lower PN) should get white."""
        players = _make_players(2)
        pairings = dutch_pairings(players, set(), 1, 5)
        self.assertEqual(len(pairings), 1)
        self.assertEqual(pairings[0]["white_id"], 1)
        self.assertEqual(pairings[0]["black_id"], 2)

    def test_alternation_preference(self):
        """Player who had white should prefer black in next round."""
        p1 = DutchPlayer(id=1, name="A", score=0, rating=2000, pairing_number=1,
                         starting_number=1, color_hist=["white"])
        p2 = DutchPlayer(id=2, name="B", score=0, rating=1900, pairing_number=2,
                         starting_number=2, color_hist=["black"])
        # p1 wants black, p2 wants white → compatible
        engine = DutchEngine([], set(), 2, 5)
        white, black = engine._assign_colors(p1, p2)
        self.assertEqual(white.id, 2)
        self.assertEqual(black.id, 1)


class TestNoRepeatPairings(unittest.TestCase):
    """Phase 1.2/1.4 — No repeat opponents across rounds."""

    def test_round2_avoids_repeats(self):
        players = [
            {"id": 1, "name": "A", "score": 1.0, "rating": 2400, "starting_number": 1,
             "color_hist": ["white"]},
            {"id": 2, "name": "B", "score": 1.0, "rating": 2300, "starting_number": 2,
             "color_hist": ["white"]},
            {"id": 3, "name": "C", "score": 0.0, "rating": 2200, "starting_number": 3,
             "color_hist": ["black"]},
            {"id": 4, "name": "D", "score": 0.0, "rating": 2100, "starting_number": 4,
             "color_hist": ["black"]},
        ]
        prev = {(1, 3), (2, 4)}
        pairings = dutch_pairings(players, prev, 2, 5)
        for p in pairings:
            if not p.get("bye"):
                pair = tuple(sorted([p["white_id"], p["black_id"]]))
                self.assertNotIn(pair, prev, f"Repeat pairing: {pair}")


class TestTranspositions(unittest.TestCase):
    """Phase 1.4 — Transposition when default pairing fails."""

    def test_transposition_needed(self):
        """When default S1-S2 fails due to repeat, transposition should fix it."""
        players = [
            {"id": 1, "name": "A", "score": 2.0, "rating": 2400, "starting_number": 1,
             "color_hist": ["white", "black"]},
            {"id": 2, "name": "B", "score": 2.0, "rating": 2300, "starting_number": 2,
             "color_hist": ["black", "white"]},
            {"id": 3, "name": "C", "score": 2.0, "rating": 2200, "starting_number": 3,
             "color_hist": ["white", "black"]},
            {"id": 4, "name": "D", "score": 2.0, "rating": 2100, "starting_number": 4,
             "color_hist": ["black", "white"]},
        ]
        # Default would try 1v3, 2v4 — but those were already played
        prev = {(1, 3), (2, 4)}
        pairings = dutch_pairings(players, prev, 3, 5)
        self.assertEqual(len(pairings), 2)
        for p in pairings:
            pair = tuple(sorted([p["white_id"], p["black_id"]]))
            self.assertNotIn(pair, prev)


class TestExchanges(unittest.TestCase):
    """Phase 1.5 — Exchange when no transposition works."""

    def test_exchange_needed(self):
        """When all S2 transpositions fail, an exchange between S1/S2 should work."""
        players = [
            {"id": 1, "name": "A", "score": 2.0, "rating": 2400, "starting_number": 1},
            {"id": 2, "name": "B", "score": 2.0, "rating": 2300, "starting_number": 2},
            {"id": 3, "name": "C", "score": 2.0, "rating": 2200, "starting_number": 3},
            {"id": 4, "name": "D", "score": 2.0, "rating": 2100, "starting_number": 4},
        ]
        # 1 played both S2 players (3 & 4), 2 played both S2 players (3 & 4)
        # Only solution: exchange so 1v2, 3v4 (effectively swapping S1/S2 membership)
        prev = {(1, 3), (1, 4), (2, 3), (2, 4)}
        pairings = dutch_pairings(players, prev, 5, 7)
        self.assertEqual(len(pairings), 2)
        ids_paired = set()
        for p in pairings:
            pair = tuple(sorted([p["white_id"], p["black_id"]]))
            self.assertNotIn(pair, prev)
            ids_paired.add(p["white_id"])
            ids_paired.add(p["black_id"])
        self.assertEqual(ids_paired, {1, 2, 3, 4})


class TestQualityMetric(unittest.TestCase):
    """Phase 1.10 — Quality metric."""

    def test_perfect_quality(self):
        p1 = DutchPlayer(id=1, name="A", score=1.0, rating=2000, pairing_number=1, starting_number=1)
        p2 = DutchPlayer(id=2, name="B", score=1.0, rating=1900, pairing_number=2, starting_number=2)
        self.assertEqual(DutchEngine._pairing_quality([(p1, p2)]), 0.0)

    def test_nonzero_quality(self):
        p1 = DutchPlayer(id=1, name="A", score=2.0, rating=2000, pairing_number=1, starting_number=1)
        p2 = DutchPlayer(id=2, name="B", score=0.0, rating=1900, pairing_number=2, starting_number=2)
        self.assertEqual(DutchEngine._pairing_quality([(p1, p2)]), 2.0)


class TestMultiRoundSimulation(unittest.TestCase):
    """Integration: full 3-round simulation with 6 players."""

    def test_three_rounds_no_repeats(self):
        """Run 3 rounds and verify no repeat pairings."""
        players = _make_players(6)
        all_prev = set()

        for rnd in range(1, 4):
            # Update scores after each round (simulate all draws)
            for p in players:
                p["score"] = (rnd - 1) * 0.5

            pairings = dutch_pairings(players, all_prev, rnd, 5)
            regular = [p for p in pairings if not p.get("bye")]

            for p in regular:
                pair = tuple(sorted([p["white_id"], p["black_id"]]))
                self.assertNotIn(pair, all_prev,
                                 f"Round {rnd}: repeat pairing {pair}")
                all_prev.add(pair)

    def test_seven_players_five_rounds(self):
        """7 players, 5 rounds: one bye per round, no double byes within limit."""
        players = _make_players(7)
        all_prev = set()
        bye_count = {i: 0 for i in range(1, 8)}

        for rnd in range(1, 6):
            for p in players:
                p["score"] = (rnd - 1) * 0.5
                p["bye_count"] = bye_count[p["id"]]

            pairings = dutch_pairings(players, all_prev, rnd, 5)
            byes = [p for p in pairings if p.get("bye")]
            regular = [p for p in pairings if not p.get("bye")]

            self.assertEqual(len(byes), 1, f"Round {rnd}: expected 1 bye")
            self.assertEqual(len(regular), 3, f"Round {rnd}: expected 3 pairs")

            bye_id = byes[0]["white_id"]
            bye_count[bye_id] += 1

            for p in regular:
                pair = tuple(sorted([p["white_id"], p["black_id"]]))
                self.assertNotIn(pair, all_prev,
                                 f"Round {rnd}: repeat pairing {pair}")
                all_prev.add(pair)


if __name__ == "__main__":
    unittest.main()
