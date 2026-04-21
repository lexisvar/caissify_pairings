"""
Unit tests for Baku Acceleration on the FIDE Dutch engine.

Baku Acceleration (FIDE Handbook §C.04.5.1) is an opt-in modifier on the
Dutch engine: for rounds 1 and 2, the top half of the field (by initial
pairing number / rating) receives a +1 *virtual point* added to its
score for pairing purposes only. From round 3 onwards no virtual point
is added.

These tests verify the *contract* of the modifier:

- Off by default — no behavioural change to the existing Dutch engine.
- Round 1 with N players splits cleanly into two independent halves:
  top half plays top half, bottom half plays bottom half.
- Round 2 keeps the virtual point applied.
- Round 3 onwards: virtual point is gone — pairings match a baseline
  non-accelerated Dutch run from the same state.
- Odd N — the extra slot goes to the *top* half (FIDE convention).
- Caller's player dicts are never mutated.
- The internal helper ``_apply_baku_virtual_scores`` is deterministic
  and never mutates its input.
"""

from __future__ import annotations

import copy
import unittest
from typing import Dict, List, Set, Tuple

from caissify_pairings import generate_pairings
from caissify_pairings.engines.dutch import DutchEngine


def _players(count: int) -> List[dict]:
    """Build `count` players, descending rating, R0 state."""
    base_rating = 2600
    out: List[dict] = []
    for i in range(1, count + 1):
        out.append({
            "id": i,
            "name": f"P{i}",
            "score": 0.0,
            "rating": base_rating - (i - 1) * 25,
            "starting_number": i,
            "color_hist": [],
            "float_history": [],
            "bye_count": 0,
            "forfeit_win_count": 0,
        })
    return out


def _all_ids_accounted_for(result: List[dict], players: List[dict]) -> bool:
    seen: Dict[int, int] = {}
    for pr in result:
        seen[pr["white_id"]] = seen.get(pr["white_id"], 0) + 1
        if pr.get("black_id") is not None:
            seen[pr["black_id"]] = seen.get(pr["black_id"], 0) + 1
    if set(seen.keys()) != {p["id"] for p in players}:
        return False
    return all(v == 1 for v in seen.values())


# ----------------------------------------------------------------------
# Helper: _apply_baku_virtual_scores
# ----------------------------------------------------------------------


class TestBakuVirtualScoreHelper(unittest.TestCase):
    def test_top_half_gets_plus_one_even_n(self):
        players = _players(8)
        out = DutchEngine._apply_baku_virtual_scores(players)

        score_by_id = {p["id"]: p["score"] for p in out}
        self.assertEqual(score_by_id[1], 1.0)
        self.assertEqual(score_by_id[2], 1.0)
        self.assertEqual(score_by_id[3], 1.0)
        self.assertEqual(score_by_id[4], 1.0)
        self.assertEqual(score_by_id[5], 0.0)
        self.assertEqual(score_by_id[6], 0.0)
        self.assertEqual(score_by_id[7], 0.0)
        self.assertEqual(score_by_id[8], 0.0)

    def test_top_half_is_ceiling_for_odd_n(self):
        players = _players(9)
        out = DutchEngine._apply_baku_virtual_scores(players)

        score_by_id = {p["id"]: p["score"] for p in out}
        # Top half = ceil(9/2) = 5 players → ids 1..5
        for pid in (1, 2, 3, 4, 5):
            self.assertEqual(score_by_id[pid], 1.0,
                             f"player {pid} should be in top half")
        for pid in (6, 7, 8, 9):
            self.assertEqual(score_by_id[pid], 0.0,
                             f"player {pid} should be in bottom half")

    def test_does_not_mutate_input(self):
        players = _players(6)
        snapshot = copy.deepcopy(players)
        DutchEngine._apply_baku_virtual_scores(players)
        self.assertEqual(players, snapshot)

    def test_empty_input_returns_empty(self):
        self.assertEqual(DutchEngine._apply_baku_virtual_scores([]), [])

    def test_adds_to_existing_score(self):
        players = _players(4)
        for p in players:
            p["score"] = 0.5  # mid-tournament-style state
        out = DutchEngine._apply_baku_virtual_scores(players)
        score_by_id = {p["id"]: p["score"] for p in out}
        self.assertEqual(score_by_id[1], 1.5)
        self.assertEqual(score_by_id[2], 1.5)
        self.assertEqual(score_by_id[3], 0.5)
        self.assertEqual(score_by_id[4], 0.5)


# ----------------------------------------------------------------------
# Round 1 — half-field separation
# ----------------------------------------------------------------------


class TestBakuRoundOne(unittest.TestCase):
    def test_round_one_pairs_top_half_against_top_half(self):
        players = _players(8)
        result = generate_pairings(
            "dutch",
            players=players,
            previous_pairings=set(),
            round_number=1,
            total_rounds=9,
            accelerated=True,
        )

        self.assertTrue(_all_ids_accounted_for(result, players))

        top_half = {1, 2, 3, 4}
        bottom_half = {5, 6, 7, 8}
        for pr in result:
            w, b = pr["white_id"], pr["black_id"]
            both_top = w in top_half and b in top_half
            both_bot = w in bottom_half and b in bottom_half
            self.assertTrue(
                both_top or both_bot,
                f"Baku R1 pairing crossed halves: {w} vs {b}",
            )

    def test_round_one_baseline_does_cross_halves(self):
        """Sanity check: without acceleration, Dutch R1 pairs top vs bottom."""
        players = _players(8)
        result = generate_pairings(
            "dutch",
            players=players,
            previous_pairings=set(),
            round_number=1,
            total_rounds=9,
        )

        top_half = {1, 2, 3, 4}
        bottom_half = {5, 6, 7, 8}
        crossed = sum(
            1 for pr in result
            if (pr["white_id"] in top_half) != (pr["black_id"] in top_half)
            and pr["black_id"] is not None
        )
        same_half = sum(
            1 for pr in result
            if pr["black_id"] is not None
            and (pr["white_id"] in top_half) == (pr["black_id"] in top_half)
        )
        # Standard Dutch R1: every game crosses halves (1v5, 2v6, 3v7, 4v8).
        self.assertEqual(crossed, 4)
        self.assertEqual(same_half, 0)

    def test_round_one_odd_field_top_half_keeps_extra(self):
        """With 9 players, top half = {1..5}, bottom half = {6..9}.

        Top scoregroup has 5 players (odd); one floats down to the
        bottom group. Bottom group then has 5 (odd); one becomes the
        bye. So we should observe at most ONE cross-half pairing (the
        downfloater) and the bye must come from the original
        bottom-half pool.
        """
        players = _players(9)
        result = generate_pairings(
            "dutch",
            players=players,
            previous_pairings=set(),
            round_number=1,
            total_rounds=9,
            accelerated=True,
        )

        self.assertTrue(_all_ids_accounted_for(result, players))

        top_half = {1, 2, 3, 4, 5}
        bottom_half = {6, 7, 8, 9}

        crossed = 0
        bye_player = None
        for pr in result:
            if pr.get("bye"):
                bye_player = pr["white_id"]
                continue
            w, b = pr["white_id"], pr["black_id"]
            if (w in top_half) != (b in top_half):
                crossed += 1

        self.assertLessEqual(
            crossed, 1,
            "At most one cross-half pairing expected (the downfloater)",
        )
        self.assertIn(
            bye_player, bottom_half,
            "Bye should come from the bottom half (lowest virtual score)",
        )


# ----------------------------------------------------------------------
# Round 2 — virtual points still apply
# ----------------------------------------------------------------------


class TestBakuRoundTwo(unittest.TestCase):
    def test_round_two_keeps_virtual_points(self):
        """After R1 (Baku-paired), R2 virtual scores group:

        - Score 2: top-half winners
        - Score 1: top-half losers + bottom-half winners
        - Score 0: bottom-half losers

        Pure top-vs-top in score 2, pure bot-vs-bot in score 0.
        Score-1 group is mixed (this is the desired Baku behaviour).
        """
        players = _players(8)

        # Simulate R1 outcomes (top half plays itself; bottom half plays itself)
        # R1 pairings under Baku: top {1,2,3,4} → 1v3, 2v4; bot {5,6,7,8} → 5v7, 6v8
        # Let higher rating win in each half: 1, 2 win; 5, 6 win.
        prev = {(1, 3), (2, 4), (5, 7), (6, 8)}
        for p in players:
            if p["id"] in {1, 2, 5, 6}:
                p["score"] = 1.0
                p["color_hist"] = ["white"]
            else:
                p["score"] = 0.0
                p["color_hist"] = ["black"]

        result = generate_pairings(
            "dutch",
            players=players,
            previous_pairings=prev,
            round_number=2,
            total_rounds=9,
            accelerated=True,
        )

        self.assertTrue(_all_ids_accounted_for(result, players))

        top_half = {1, 2, 3, 4}
        bottom_half = {5, 6, 7, 8}

        # The top-half winners (1, 2) have virtual score 2 and must play
        # each other. The bottom-half losers (7, 8) have virtual score 0
        # and must play each other.
        partners = {}
        for pr in result:
            if pr.get("bye"):
                continue
            partners[pr["white_id"]] = pr["black_id"]
            partners[pr["black_id"]] = pr["white_id"]

        self.assertIn(1, partners)
        self.assertIn(partners[1], top_half,
                      "P1 (top-half R1 winner) must play another top-half player")
        self.assertIn(2, partners)
        self.assertIn(partners[2], top_half,
                      "P2 (top-half R1 winner) must play another top-half player")

        self.assertIn(7, partners)
        self.assertIn(partners[7], bottom_half,
                      "P7 (bottom-half R1 loser) must play another bottom-half player")
        self.assertIn(8, partners)
        self.assertIn(partners[8], bottom_half,
                      "P8 (bottom-half R1 loser) must play another bottom-half player")


# ----------------------------------------------------------------------
# Round 3 — virtual points removed
# ----------------------------------------------------------------------


class TestBakuRoundThreeOnwards(unittest.TestCase):
    def test_round_three_matches_unaccelerated(self):
        """
        Build identical R3 input states for two engines — one with
        ``accelerated=True``, one without — and assert pairings match.
        Because R3 is past the Baku window, the virtual point must not
        be applied and the output must be identical.
        """
        players = _players(8)
        # Plausible R3 state: scores spread between 0 and 2.
        scores = {1: 2.0, 2: 1.5, 3: 1.5, 4: 1.0, 5: 1.0, 6: 1.0, 7: 0.5, 8: 0.0}
        for p in players:
            p["score"] = scores[p["id"]]
            # Even number of whites and blacks across two rounds.
            p["color_hist"] = ["white", "black"] if p["id"] % 2 else ["black", "white"]

        # Plausible (non-overlapping) prior pairings: any 8 disjoint pairs
        # spanning 2 rounds.
        prev = {(1, 5), (2, 6), (3, 7), (4, 8),  # R1
                (1, 2), (3, 4), (5, 6), (7, 8)}  # R2

        baseline = generate_pairings(
            "dutch",
            players=copy.deepcopy(players),
            previous_pairings=set(prev),
            round_number=3,
            total_rounds=9,
        )
        accelerated = generate_pairings(
            "dutch",
            players=copy.deepcopy(players),
            previous_pairings=set(prev),
            round_number=3,
            total_rounds=9,
            accelerated=True,
        )

        def _key(p):
            return (p.get("bye", False),
                    p["white_id"],
                    p.get("black_id") if p.get("black_id") is not None else -1)

        self.assertEqual(
            sorted(baseline, key=_key),
            sorted(accelerated, key=_key),
            "R3 with accelerated=True must equal R3 without (no virtual point)",
        )


# ----------------------------------------------------------------------
# Defaults & input safety
# ----------------------------------------------------------------------


class TestBakuDefaultsAndImmutability(unittest.TestCase):
    def test_default_is_not_accelerated(self):
        """No-arg construction must not change existing Dutch behaviour."""
        players = _players(8)
        baseline = generate_pairings(
            "dutch",
            players=copy.deepcopy(players),
            previous_pairings=set(),
            round_number=1,
            total_rounds=9,
        )
        explicit_off = generate_pairings(
            "dutch",
            players=copy.deepcopy(players),
            previous_pairings=set(),
            round_number=1,
            total_rounds=9,
            accelerated=False,
        )
        self.assertEqual(baseline, explicit_off)

    def test_input_players_not_mutated(self):
        players = _players(8)
        snapshot = copy.deepcopy(players)

        generate_pairings(
            "dutch",
            players=players,
            previous_pairings=set(),
            round_number=1,
            total_rounds=9,
            accelerated=True,
        )

        self.assertEqual(players, snapshot,
                         "Caller's player dicts must never be mutated")

    def test_engine_attribute_records_acceleration_flag(self):
        eng = DutchEngine(
            players=_players(4),
            previous_pairings=set(),
            round_number=1,
            total_rounds=5,
            accelerated=True,
        )
        self.assertTrue(eng.accelerated)


# ----------------------------------------------------------------------
# Smoke — multi-round accelerated tournament completes cleanly
# ----------------------------------------------------------------------


class TestBakuSmoke(unittest.TestCase):
    def test_three_round_accelerated_tournament_completes(self):
        """
        Run a deterministic 3-round accelerated tournament with 12
        players. Verify each round produces a valid pairing covering
        every player exactly once and never repeats opponents.
        """
        players = _players(12)
        prev: Set[Tuple[int, int]] = set()

        for rnd in range(1, 4):
            result = generate_pairings(
                "dutch",
                players=players,
                previous_pairings=prev,
                round_number=rnd,
                total_rounds=9,
                accelerated=True,
            )
            self.assertTrue(
                _all_ids_accounted_for(result, players),
                f"R{rnd} pairing missed players or double-paired someone",
            )

            # Award fixed results: white always wins (deterministic).
            for pr in result:
                if pr.get("bye"):
                    continue
                pair = tuple(sorted((pr["white_id"], pr["black_id"])))
                self.assertNotIn(pair, prev,
                                 f"R{rnd} repeated pairing {pair}")
                prev.add(pair)

                for p in players:
                    if p["id"] == pr["white_id"]:
                        p["score"] += 1.0
                        p["color_hist"].append("white")
                    elif p["id"] == pr["black_id"]:
                        p["color_hist"].append("black")

            # Award the bye.
            for pr in result:
                if pr.get("bye"):
                    for p in players:
                        if p["id"] == pr["white_id"]:
                            p["score"] += 1.0
                            p["bye_count"] += 1


if __name__ == "__main__":
    unittest.main()
