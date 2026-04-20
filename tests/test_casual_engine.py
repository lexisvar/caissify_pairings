"""
Unit tests for the Casual Swiss Pairing Engine.

The casual engine is intentionally simpler than the Dutch (FIDE) engine,
so these tests focus on the *contract* it advertises:

- Round-1 uses the Dutch half-split.
- Rounds 2+ pair within score groups and float down when needed.
- No rematches of previous-round pairings.
- Odd fields get exactly one bye, awarded to the lowest-scored
  eligible player.
- The engine never mutates the caller's player dicts.
- The engine is registered in the engine registry under ``"casual"``.
"""

from __future__ import annotations

import copy
import unittest
from typing import Dict, List, Set, Tuple

from caissify_pairings.engines import available_systems, get_engine
from caissify_pairings.engines.casual import CasualSwissEngine


def _players(count: int, *, score: float | None = None) -> List[dict]:
    """Build `count` players with descending ratings."""
    base_rating = 2400
    out: List[dict] = []
    for i in range(1, count + 1):
        out.append({
            "id": i,
            "name": f"P{i}",
            "score": 0.0 if score is None else score,
            "rating": base_rating - (i - 1) * 50,
            "starting_number": i,
            "color_hist": [],
            "float_history": [],
            "bye_count": 0,
        })
    return out


def _pair(engine_players: List[dict], prev: Set[Tuple[int, int]],
          round_number: int, total_rounds: int = 7, **kwargs) -> List[dict]:
    eng = CasualSwissEngine(
        players=engine_players,
        previous_pairings=prev,
        round_number=round_number,
        total_rounds=total_rounds,
        **kwargs,
    )
    return eng.generate_pairings()


def _all_ids_accounted_for(result: List[dict], players: List[dict]) -> bool:
    """Every player must appear in exactly one pairing (regular or bye)."""
    seen: Dict[int, int] = {}
    for pr in result:
        seen[pr["white_id"]] = seen.get(pr["white_id"], 0) + 1
        if pr.get("black_id") is not None:
            seen[pr["black_id"]] = seen.get(pr["black_id"], 0) + 1
    if set(seen.keys()) != {p["id"] for p in players}:
        return False
    return all(v == 1 for v in seen.values())


class TestCasualEngineRegistry(unittest.TestCase):
    def test_registered(self):
        self.assertIn("casual", available_systems())

    def test_get_engine_returns_class(self):
        cls = get_engine("casual")
        self.assertIs(cls, CasualSwissEngine)


class TestCasualEngineRoundOne(unittest.TestCase):
    def test_even_field_uses_dutch_split(self):
        players = _players(8)
        result = _pair(players, set(), round_number=1)
        self.assertEqual(len(result), 4)
        self.assertTrue(_all_ids_accounted_for(result, players))

        # Dutch split: seed 1 vs seed 5, 2 vs 6, 3 vs 7, 4 vs 8.
        pair_ids = {tuple(sorted((p["white_id"], p["black_id"]))) for p in result}
        self.assertEqual(pair_ids, {(1, 5), (2, 6), (3, 7), (4, 8)})

    def test_odd_field_assigns_one_bye_to_lowest_rated(self):
        players = _players(7)
        result = _pair(players, set(), round_number=1)
        byes = [p for p in result if p.get("bye")]
        self.assertEqual(len(byes), 1)
        self.assertEqual(byes[0]["white_id"], 7)  # lowest-rated
        self.assertIsNone(byes[0]["black_id"])
        self.assertTrue(_all_ids_accounted_for(result, players))

    def test_round_one_bye_type_configurable(self):
        players = _players(5)
        result = _pair(players, set(), round_number=1, bye_type="U")
        byes = [p for p in result if p.get("bye")]
        self.assertEqual(byes[0]["bye_type"], "U")


class TestCasualEngineNoRematches(unittest.TestCase):
    def test_second_round_does_not_repeat_round_one_pairings(self):
        players = _players(8)
        r1 = _pair(players, set(), round_number=1)
        r1_pairs = {
            tuple(sorted((p["white_id"], p["black_id"]))) for p in r1
        }

        # Simulate scores after round 1: the player listed first on each
        # table wins.
        for p in r1:
            for player in players:
                if player["id"] == p["white_id"]:
                    player["score"] = 1.0

        r2 = _pair(players, r1_pairs, round_number=2)
        r2_pairs = {
            tuple(sorted((p["white_id"], p["black_id"])))
            for p in r2
            if p.get("black_id") is not None
        }
        self.assertTrue(r1_pairs.isdisjoint(r2_pairs))
        self.assertTrue(_all_ids_accounted_for(r2, players))


class TestCasualEngineByes(unittest.TestCase):
    def test_lowest_score_gets_bye_in_round_two(self):
        players = _players(5)
        # Give player 5 score 0, player 4 score 0.5, rest 1.0.
        scores = {1: 1.0, 2: 1.0, 3: 1.0, 4: 0.5, 5: 0.0}
        for p in players:
            p["score"] = scores[p["id"]]

        result = _pair(players, set(), round_number=2)
        bye = next(p for p in result if p.get("bye"))
        self.assertEqual(bye["white_id"], 5)

    def test_bye_cap_is_respected(self):
        players = _players(5)
        # Player 5 already had a bye — should not receive another.
        for p in players:
            if p["id"] == 5:
                p["bye_count"] = 1
            p["score"] = 0.0
        result = _pair(players, set(), round_number=2)
        bye = next(p for p in result if p.get("bye"))
        self.assertNotEqual(bye["white_id"], 5)

    def test_everyone_at_cap_pairs_all_players_if_possible(self):
        # Even count, nobody eligible for a bye — no byes should appear.
        players = _players(4)
        for p in players:
            p["bye_count"] = 1
        result = _pair(players, set(), round_number=3)
        self.assertFalse(any(p.get("bye") for p in result))
        self.assertTrue(_all_ids_accounted_for(result, players))


class TestCasualEngineFloats(unittest.TestCase):
    def test_downfloat_when_score_group_is_odd(self):
        players = _players(6)
        players[0]["score"] = 2.0  # lone leader, will need to float down
        for p in players[1:]:
            p["score"] = 1.0
        result = _pair(players, set(), round_number=3)

        # Leader must be paired (not given a bye) and the pairing that
        # contains id=1 must be flagged as a cross-group float.
        leader_pairing = next(
            p for p in result
            if p["white_id"] == 1 or p.get("black_id") == 1
        )
        self.assertFalse(leader_pairing.get("bye", False))
        self.assertEqual(leader_pairing.get("float_type"), "down")
        self.assertTrue(_all_ids_accounted_for(result, players))


class TestCasualEngineColors(unittest.TestCase):
    def test_alternates_colors_when_possible(self):
        players = _players(2)
        players[0]["color_hist"] = ["white"]
        players[1]["color_hist"] = ["black"]
        # Pretend they've already played each other — this round is a
        # standalone colour test, not a pairing test.
        result = _pair(players, set(), round_number=2)
        pr = result[0]
        # Player 1 had white last, Player 2 had black last → player 1
        # should now be black, player 2 white.
        self.assertEqual(pr["white_id"], 2)
        self.assertEqual(pr["black_id"], 1)

    def test_avoids_three_in_a_row(self):
        players = _players(2)
        # Player 1 has had white twice; giving them white again would be
        # 3 in a row.
        players[0]["color_hist"] = ["white", "white"]
        players[1]["color_hist"] = ["black", "black"]
        result = _pair(players, set(), round_number=3)
        pr = result[0]
        self.assertEqual(pr["white_id"], 2)
        self.assertEqual(pr["black_id"], 1)


class TestCasualEngineNoMutation(unittest.TestCase):
    def test_caller_dicts_are_not_mutated(self):
        players = _players(6)
        snapshot = copy.deepcopy(players)
        prev: Set[Tuple[int, int]] = set()
        _pair(players, prev, round_number=1)
        self.assertEqual(players, snapshot)

    def test_caller_previous_pairings_not_mutated(self):
        players = _players(6)
        prev: Set[Tuple[int, int]] = {(1, 2), (3, 4), (5, 6)}
        prev_snapshot = set(prev)
        _pair(players, prev, round_number=2)
        self.assertEqual(prev, prev_snapshot)


class TestCasualEngineDeterminism(unittest.TestCase):
    def test_two_runs_produce_identical_pairings(self):
        p1 = _players(10)
        p2 = copy.deepcopy(p1)
        prev: Set[Tuple[int, int]] = set()
        r1 = _pair(p1, prev, round_number=1)
        r2 = _pair(p2, set(prev), round_number=1)
        self.assertEqual(r1, r2)


class TestCasualEngineTableNumbering(unittest.TestCase):
    def test_tables_are_1_indexed_and_unique(self):
        players = _players(9)
        result = _pair(players, set(), round_number=1)
        tables = [p["table"] for p in result]
        self.assertEqual(sorted(tables), list(range(1, len(result) + 1)))

    def test_byes_appear_after_regular_games(self):
        players = _players(5)
        result = _pair(players, set(), round_number=1)
        regular = [p for p in result if not p.get("bye")]
        byes = [p for p in result if p.get("bye")]
        if regular and byes:
            self.assertLess(
                max(p["table"] for p in regular),
                min(p["table"] for p in byes),
            )


class TestCasualEngineMultiRound(unittest.TestCase):
    def test_full_5_round_tournament_has_no_rematches(self):
        """Smoke test: run 5 rounds and check no pair meets twice."""
        players = _players(8)
        prev: Set[Tuple[int, int]] = set()

        for rnd in range(1, 6):
            result = _pair(players, prev, round_number=rnd, total_rounds=5)
            self.assertTrue(_all_ids_accounted_for(result, players))

            for pr in result:
                if pr.get("bye"):
                    for p in players:
                        if p["id"] == pr["white_id"]:
                            p["bye_count"] = p.get("bye_count", 0) + 1
                            p["score"] += 1.0  # full-point bye
                            p["color_hist"] = p["color_hist"] + ["bye"]
                    continue

                pair = tuple(sorted((pr["white_id"], pr["black_id"])))
                self.assertNotIn(
                    pair, prev,
                    f"Round {rnd} repeated the pairing {pair}",
                )
                prev.add(pair)

                # Award a point to white for variety; update colour history.
                for p in players:
                    if p["id"] == pr["white_id"]:
                        p["score"] += 1.0
                        p["color_hist"] = p["color_hist"] + ["white"]
                    elif p["id"] == pr["black_id"]:
                        p["color_hist"] = p["color_hist"] + ["black"]


if __name__ == "__main__":
    unittest.main()
