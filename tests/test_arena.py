"""Rush (arena) pairing, scoring and standings."""

import copy
import random
import time

import pytest

from caissify_pairings.arena import (
    BERSERK_MIN_MOVES,
    SHORT_DRAW_MOVES,
    pair_waiting,
    performance_rating,
    score_game,
    standings,
)


def P(pid, rank, rating=1500, last=None, colors=()):
    return {"id": pid, "rank": rank, "rating": rating, "last_opponent": last, "colors": list(colors)}


def ids(pairs):
    return {frozenset((p["white_id"], p["black_id"])) for p in pairs}


# ── pairing ──────────────────────────────────────────────────────────────────

class TestPairing:
    def test_nobody_or_one_player_waits(self):
        assert pair_waiting([]) == []
        assert pair_waiting([P(1, 1)]) == []

    def test_everyone_paired_when_possible_and_odd_one_waits(self):
        pairs = pair_waiting([P(i, i) for i in range(1, 8)])
        assert len(pairs) == 3
        seen = [x for p in pairs for x in (p["white_id"], p["black_id"])]
        assert len(seen) == len(set(seen)) == 6

    def test_neighbours_in_the_standings_meet(self):
        pairs = pair_waiting([P(i, i) for i in range(1, 9)])
        assert ids(pairs) == {frozenset(s) for s in ((1, 2), (3, 4), (5, 6), (7, 8))}
        # best pair first
        assert {pairs[0]["white_id"], pairs[0]["black_id"]} == {1, 2}

    def test_rating_breaks_ties_between_equal_rank_gaps(self):
        # 2 is equally far in rank from 1 and 3; rating decides.
        pairs = pair_waiting([P(1, 1, 2000), P(2, 2, 1990), P(3, 3, 1500), P(4, 4, 1490)])
        assert ids(pairs) == {frozenset((1, 2)), frozenset((3, 4))}

    def test_no_immediate_rematch(self):
        pairs = pair_waiting([P(1, 1, last=2), P(2, 2, last=1), P(3, 3), P(4, 4)])
        assert frozenset((1, 2)) not in ids(pairs)
        assert len(pairs) == 2

    def test_rematch_rule_leaves_a_player_waiting_rather_than_breaking_it(self):
        assert pair_waiting([P(1, 1, last=2), P(2, 2, last=1)]) == []

    def test_last_opponent_on_one_side_is_enough(self):
        assert pair_waiting([P(1, 1, last=2), P(2, 2, last=9)]) == []

    def test_never_three_in_a_row_with_the_same_colour(self):
        pairs = pair_waiting([P(1, 1, colors="ww"), P(2, 2, colors="bb")])
        assert pairs == [{"white_id": 2, "black_id": 1}]

    def test_two_players_who_both_need_the_same_colour_are_not_paired(self):
        assert pair_waiting([P(1, 1, colors="ww"), P(2, 2, colors="bww")]) == []

    def test_colour_balance_then_last_colour(self):
        # 1 has had White more often -> Black.
        assert pair_waiting([P(1, 1, colors="wbw"), P(2, 2, colors="b")]) == [{"white_id": 2, "black_id": 1}]
        # Balanced: whoever had White last gets Black.
        assert pair_waiting([P(1, 1, colors="bw"), P(2, 2, colors="wb")]) == [{"white_id": 2, "black_id": 1}]
        # No history: higher-ranked player gets White.
        assert pair_waiting([P(5, 2), P(9, 1)]) == [{"white_id": 9, "black_id": 5}]

    def test_deterministic_and_does_not_mutate_input(self):
        rng = random.Random(7)
        players = [P(i, i, rng.randint(1200, 2400), colors=rng.choice(["", "w", "b", "wb", "bw"])) for i in range(1, 31)]
        before = copy.deepcopy(players)
        a = pair_waiting(players)
        b = pair_waiting(list(reversed(players)))
        assert players == before
        assert a == b

    def test_large_pool_is_chunked_and_still_pairs_everyone(self):
        rng = random.Random(1)
        players = [P(i, i, rng.randint(1000, 2600)) for i in range(1, 202)]
        t = time.perf_counter()
        pairs = pair_waiting(players, max_pool=60)
        assert time.perf_counter() - t < 5
        assert len(pairs) == 100  # 201 players: one waits
        # chunking keeps pairs local in the standings
        by = {p["id"]: p["rank"] for p in players}
        assert max(abs(by[p["white_id"]] - by[p["black_id"]]) for p in pairs) < 60

    def test_field_size_weights_gaps_near_the_top(self):
        # Equal rank gaps: the pair nearer the top costs more to split, so the
        # leaders are kept together.
        pairs = pair_waiting([P(1, 1), P(2, 3), P(3, 50), P(4, 52)], field_size=60)
        assert ids(pairs) == {frozenset((1, 2)), frozenset((3, 4))}


# ── scoring ──────────────────────────────────────────────────────────────────

class TestScoring:
    def test_basic_points(self):
        assert score_game("win", game_moves=30)["points"] == 2
        assert score_game("draw", game_moves=30)["points"] == 1
        assert score_game("loss", game_moves=30)["points"] == 0

    def test_streak_counts_and_resets(self):
        assert score_game("win", streak=0)["streak"] == 1
        assert score_game("win", streak=1)["streak"] == 2
        assert score_game("draw", streak=3, game_moves=40)["streak"] == 0
        assert score_game("loss", streak=3)["streak"] == 0

    def test_on_fire_after_two_wins_doubles(self):
        third = score_game("win", streak=2, game_moves=30)
        assert third["on_fire"] and third["points"] == 4
        assert score_game("draw", streak=2, game_moves=30)["points"] == 2
        assert score_game("loss", streak=2)["points"] == 0
        second = score_game("win", streak=1, game_moves=30)
        assert not second["on_fire"] and second["points"] == 2

    def test_berserk_bonus_needs_a_win_and_enough_moves(self):
        win = score_game("win", berserk=True, own_moves=BERSERK_MIN_MOVES)
        assert win["berserk_bonus"] and win["points"] == 3
        quick = score_game("win", berserk=True, own_moves=BERSERK_MIN_MOVES - 1)
        assert not quick["berserk_bonus"] and quick["points"] == 2
        assert score_game("draw", berserk=True, own_moves=40, game_moves=40)["points"] == 1
        assert score_game("win", streak=2, berserk=True, own_moves=20)["points"] == 5

    def test_short_draw_scores_nothing(self):
        short = score_game("draw", game_moves=SHORT_DRAW_MOVES - 1)
        assert short["short_draw"] and short["points"] == 0
        assert score_game("draw", game_moves=SHORT_DRAW_MOVES)["points"] == 1
        assert score_game("draw", streak=2, game_moves=3)["points"] == 0

    def test_rejects_unknown_outcome(self):
        with pytest.raises(ValueError):
            score_game("resign")


# ── standings ────────────────────────────────────────────────────────────────

class TestStandings:
    def test_performance_rating(self):
        assert performance_rating([], 0) is None
        assert performance_rating([2000, 2000], 1) == 2000  # one win, one loss
        assert performance_rating([2000], 1) == 2400
        assert performance_rating([2000], 0) == 1600

    def test_order_points_then_performance_then_fewer_games(self):
        rows = standings([
            {"id": "a", "points": 10, "performance": 1900, "games": 6},
            {"id": "b", "points": 12, "performance": 1800, "games": 7},
            {"id": "c", "points": 10, "performance": 2000, "games": 6},
            {"id": "d", "points": 10, "performance": 2000, "games": 5},
            {"id": "e", "points": 0, "performance": None, "games": 0},
        ])
        assert [r["id"] for r in rows] == ["b", "d", "c", "a", "e"]
        assert [r["rank"] for r in rows] == [1, 2, 3, 4, 5]

    def test_identical_rows_share_a_rank(self):
        rows = standings([
            {"id": 1, "points": 4, "performance": 1700, "games": 3},
            {"id": 2, "points": 4, "performance": 1700, "games": 3},
            {"id": 3, "points": 2, "performance": 1600, "games": 3},
        ])
        assert [r["rank"] for r in rows] == [1, 1, 3]
