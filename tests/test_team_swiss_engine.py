"""
Unit tests for the FIDE Swiss Team Pairing System engine (C.04.6, 2026).

Each test builds the smallest state that makes one rule decide, and
cites the article. States are built with :func:`team`: explicit match
points, and a colour string for the history of played matches
("W"/"B" = played with that colour on board 1, "u" = not paired,
"+"/"-" = forfeit won/lost, "p" = pairing-allocated bye, "F"/"H" =
full/half-point bye).
"""

from __future__ import annotations

import pytest

from caissify_pairings import generate_pairings
from caissify_pairings.engines.team_swiss import (
    TeamSwissEngine,
    TeamSwissSettings,
    baku_group_size,
    baku_records,
    baku_virtual_points,
    pairing_identifier,
    type_a_preference,
    type_b_preference,
    upfloater_sets,
)

_CODES = {"W": "played", "B": "played", "u": "zpb", "+": "forfeit_win", "-": "forfeit_loss",
          "p": "pab", "F": "fpb", "H": "hpb"}


def team(tid, mp=0.0, colours="", opps=(), gp=None, **extra):
    """A team with ``match_points`` = mp and a history made from ``colours``."""
    history = []
    opps = list(opps)
    for k, c in enumerate(colours, 1):
        e = {"round": k, "type": _CODES[c], "mp": 0.0, "gp": 0.0}
        if c in "WB+-":
            e["opponent"] = opps[k - 1] if k - 1 < len(opps) and opps[k - 1] is not None else 900 + 10 * tid + k
            e["color"] = {"W": "white", "B": "black"}.get(c, "white")
        else:
            e["opponent"] = None
        history.append(e)
    d = {"id": tid, "name": f"T{tid}", "history": history, "match_points": float(mp)}
    d["game_points"] = float(gp if gp is not None else mp)
    d.update(extra)
    return d


def pair(teams, rnd=None, total=9, **kw):
    if rnd is None:
        rnd = 1 + max((len(t["history"]) for t in teams), default=0)
    eng = TeamSwissEngine(players=teams, round_number=rnd, total_rounds=total, **kw)
    rows = eng.generate_pairings()
    return rows, eng


def pairs_of(rows):
    """{(white, black)} and the bye team."""
    return (
        sorted((r["white_id"], r["black_id"]) for r in rows if not r.get("bye")),
        next((r["white_id"] for r in rows if r.get("bye")), None),
    )


def unordered(rows):
    return sorted(tuple(sorted((r["white_id"], r["black_id"]))) for r in rows if not r.get("bye"))


# ---------------------------------------------------------------------------
# Regulation examples
# ---------------------------------------------------------------------------


class TestRegulationExamples:
    def test_3_5_4_upfloater_set_order(self):
        # "2,6,8 have 3 points, and 1,3,5 have 2.5 points ... two upfloaters
        # must have 3 points and the other 2.5 points".
        sets = [tuple(s) for s in upfloater_sets([[2, 6, 8], [1, 3, 5]], [2, 1])]
        assert sets == [(2, 6, 1), (2, 6, 3), (2, 6, 5), (2, 8, 1), (2, 8, 3),
                        (2, 8, 5), (6, 8, 1), (6, 8, 3), (6, 8, 5)]

    def test_3_6_2_pairing_identifier(self):
        assert pairing_identifier([(11, 24), (16, 6), (10, 9), (8, 4)]) == [4, 6, 9, 11, 8, 16, 10, 24]

    @staticmethod
    def _example_3_5_4_field(blocked=()):
        """
        Top scoregroup {4,7,9} (4 MP, all met each other) needs three
        upfloaters. All three from {2,6,8} (3 MP) would leave {1,3,5,10}
        unpairable (10 met 1, 3 and 5): C3. So C4/C5 give two at 3 MP and
        one at 2.5 MP, exactly the 3.5.4 example. ``blocked`` teams met the
        whole top scoregroup.
        """
        prev = {(4, 7), (4, 9), (7, 9), (10, 1), (10, 3), (10, 5)}
        for b in blocked:
            prev |= {(b, 4), (b, 7), (b, 9)}
        teams = [team(i, mp) for i, mp in
                 ((1, 2.5), (2, 3), (3, 2.5), (4, 4), (5, 2.5), (6, 3), (7, 4), (8, 3), (9, 4), (10, 0))]
        return teams, prev

    @pytest.mark.parametrize("blocked, expected", [
        ((), [2, 6, 1]),
        ((1,), [2, 6, 3]),
        ((1, 3, 5), None),          # no 2.5 team can go up: composition changes
        ((6,), [2, 8, 1]),
        ((2,), [6, 8, 1]),
    ])
    def test_3_5_4_first_legal_set_is_chosen(self, blocked, expected):
        teams, prev = self._example_3_5_4_field(blocked)
        rows, eng = pair(teams, rnd=5, total=9, previous_pairings=prev)
        first = eng.trace[0]
        assert first["residents"] == [4, 7, 9]
        if expected is None:
            assert len(first["upfloaters"]) == 3 and set(first["upfloaters"]) & {1, 3, 5} == set()
        else:
            assert first["upfloaters"] == expected


# ---------------------------------------------------------------------------
# Hand-checked tournament: 6 teams, 3 rounds
# ---------------------------------------------------------------------------


def _h(r, opp, col, mp, gp):
    return {"round": r, "opponent": opp, "color": col, "type": "played", "mp": mp, "gp": gp}


class TestHandCheckedSixTeams:
    """
    Round 1 (4.3.1, initial-colour White): 1-4, 5-2, 3-6.
    Results: 1 beats 4 3-1, 5-2 2-2, 3 beats 6 2.5-1.5.
    Round 2: top scoregroup {1,3} pairs 1-3; first-team 1 (more GP), no
    preferences, equal CD, same last colours -> 4.3.8: 1 alternates to
    Black, 3 White. {2,5} met: C4 brings {4,6} up; identifiers
    "2 4 6 5" < "2 5 4 6" -> 2-6 and 4-5. Colours 2-6: 4.3.8 (2 had
    Black) -> 2 White; 4-5: 4.3.5 (CD -1 < +1) -> 4 White.
    Results: 1 beats 3, 2-6 draw, 5 beats 4.
    Round 3 (last): {1} takes 5 (C5); 1-5 by 4.3.8 -> 1 White. {2,3}: 3
    has CD +2 (Type A Black) -> 2 White (4.3.2). {6} takes 4: 6 has CD -2
    -> 6 White.
    """

    R1 = {1: _h(1, 4, "white", 2, 3), 4: _h(1, 1, "black", 0, 1), 5: _h(1, 2, "white", 1, 2),
          2: _h(1, 5, "black", 1, 2), 3: _h(1, 6, "white", 2, 2.5), 6: _h(1, 3, "black", 0, 1.5)}
    R2 = {3: _h(2, 1, "white", 0, 1.5), 1: _h(2, 3, "black", 2, 2.5), 2: _h(2, 6, "white", 1, 2),
          6: _h(2, 2, "black", 1, 2), 4: _h(2, 5, "white", 0, 1), 5: _h(2, 4, "black", 2, 3)}

    def _teams(self, rounds):
        return [{"id": i, "name": f"T{i}", "history": [r[i] for r in rounds]} for i in range(1, 7)]

    def test_round_1(self):
        rows, _ = pair(self._teams([]), rnd=1, total=3)
        assert [(r["white_id"], r["black_id"]) for r in rows] == [(1, 4), (5, 2), (3, 6)]

    def test_round_2(self):
        rows, _ = pair(self._teams([self.R1]), rnd=2, total=3)
        assert [(r["white_id"], r["black_id"]) for r in rows] == [(3, 1), (2, 6), (4, 5)]

    def test_round_3(self):
        rows, _ = pair(self._teams([self.R1, self.R2]), rnd=3, total=3)
        assert [(r["white_id"], r["black_id"]) for r in rows] == [(1, 5), (2, 3), (6, 4)]

    def test_round_1_initial_colour_black(self):
        rows, _ = pair(self._teams([]), rnd=1, total=3, initial_color="black")
        assert [(r["white_id"], r["black_id"]) for r in rows] == [(4, 1), (2, 5), (6, 3)]

    def test_generate_pairings_dispatch(self):
        rows = generate_pairings("team_swiss", self._teams([]), set(), 1, 3)
        assert [(r["white_id"], r["black_id"]) for r in rows] == [(1, 4), (5, 2), (3, 6)]


# ---------------------------------------------------------------------------
# Absolute and completion criteria
# ---------------------------------------------------------------------------


class TestAbsoluteCriteria:
    def test_c1_no_rematch(self):
        teams = [team(1, 2, "W", [2]), team(2, 0, "B", [1]), team(3, 2, "W", [4]), team(4, 0, "B", [3])]
        rows, _ = pair(teams)
        assert (1, 2) not in unordered(rows) and (3, 4) not in unordered(rows)

    def test_c1_previous_pairings_argument(self):
        teams = [team(i) for i in range(1, 5)]
        rows, _ = pair(teams, rnd=1, previous_pairings={(1, 3)})
        assert (1, 3) not in unordered(rows)

    def test_c1_forfeited_match_may_be_repeated(self):
        # C.04.2 Article 3.5: paired but not played -> may meet again.
        teams = [team(1, 2, "+", [2]), team(2, 0, "-", [1])]
        rows, _ = pair(teams)
        assert unordered(rows) == [(1, 2)]

    def test_forbidden_pairs_are_absolute(self):
        teams = [team(i) for i in range(1, 5)]
        rows, _ = pair(teams, rnd=1, forbidden_pairs=[{"teams": [1, 3], "first_round": 1, "last_round": 1}])
        assert (1, 3) not in unordered(rows)
        rows, _ = pair(teams, rnd=1, total=3, forbidden_pairs=[{"teams": [1, 3], "first_round": 2, "last_round": 3}])
        assert (1, 3) in unordered(rows)

    def test_c2_no_second_pab(self):
        teams = [team(1, 1, "p"), team(2, 2, "W", [3]), team(3, 2, "B", [2])]
        teams[0]["match_points"] = 0.0  # lowest score, but already had the PAB
        _, bye = pairs_of(pair(teams)[0])
        assert bye != 1

    @pytest.mark.parametrize("code, eligible", [("+", False), ("F", False), ("p", False), ("H", True), ("-", True), ("u", True)])
    def test_c2_what_blocks_the_pab(self, code, eligible):
        teams = [team(1, 0, code), team(2, 3, "W"), team(3, 3, "B")]
        _, bye = pairs_of(pair(teams)[0])
        assert (bye == 1) is eligible

    def test_c3_completion_forces_upfloaters(self):
        # {1,2,3,4} could pair alone, but then 5-6 (who met) would be left.
        teams = [team(1, 2), team(2, 2), team(3, 2), team(4, 2), team(5, 0, "W", [6]), team(6, 0, "B", [5])]
        rows, eng = pair(teams, rnd=2)
        assert eng.trace[0]["upfloaters"] == [5, 6]
        assert (5, 6) not in unordered(rows)


class TestPairingAllocatedBye:
    def test_lowest_score(self):
        teams = [team(1, 4), team(2, 2), team(3, 0)]
        assert pairs_of(pair(teams, rnd=3)[0])[1] == 3

    def test_most_matches_played_then_largest_tpn(self):
        # 3.4.3: equal scores, team 2 played more matches.
        teams = [team(1, 4, "WB"), team(2, 0, "WB"), team(3, 0, "uu")]
        assert pairs_of(pair(teams, rnd=3)[0])[1] == 2
        # 3.4.4: equal score and matches -> largest TPN.
        teams = [team(1, 4), team(2, 0), team(3, 0)]
        assert pairs_of(pair(teams, rnd=3)[0])[1] == 3

    def test_must_leave_a_legal_pairing(self):
        # 3.4.2/3.4.3 put 5 first (0 MP, two matches played), but without 5
        # team 1 (who met 2, 3 and 4) has nobody to play: 3.4.1 -> 4.
        teams = [team(1, 6, "WBW", [2, 3, 4]), team(2, 2, "B", [1]), team(3, 2, "uW", [None, 1]),
                 team(4, 0, "uuB", [None, None, 1]), team(5, 0, "WWu")]
        rows, eng = pair(teams, rnd=4)
        assert eng.bye_order(eng.teams)[:2] == [5, 4]
        assert pairs_of(rows)[1] == 4
        assert (1, 5) in unordered(rows)

    def test_bye_row_carries_points(self):
        teams = [team(1), team(2), team(3)]
        rows, _ = pair(teams, rnd=1, boards=6)
        bye = [r for r in rows if r.get("bye")][0]
        assert bye["bye_type"] == "U" and bye["match_points"] == 1.0 and bye["game_points"] == 3.0
        rows, _ = pair(teams, rnd=1, bye_match_points=2, bye_game_points=2.5)
        bye = [r for r in rows if r.get("bye")][0]
        assert bye["match_points"] == 2.0 and bye["game_points"] == 2.5

    def test_no_legal_bye_is_flagged(self):
        # Every team already had the PAB: 3.3.3, the arbiter decides.
        teams = [team(i, 0, "p") for i in (1, 2, 3)]
        rows, _ = pair(teams)
        bye = [r for r in rows if r.get("bye")][0]
        assert bye["violations"] == ["C2"]


# ---------------------------------------------------------------------------
# Quality criteria
# ---------------------------------------------------------------------------


class TestQualityCriteria:
    def test_c4_fewest_upfloaters(self):
        teams = [team(1, 4), team(2, 4), team(3, 4), team(4, 2), team(5, 2), team(6, 0)]
        _, eng = pair(teams, rnd=3)
        assert eng.trace[0]["residents"] == [1, 2, 3] and len(eng.trace[0]["upfloaters"]) == 1

    def test_c5_highest_scores_first(self):
        # One upfloater from the 2-MP group, not the 0-MP group.
        teams = [team(1, 4), team(2, 4), team(3, 4), team(4, 0), team(5, 2), team(6, 2)]
        _, eng = pair(teams, rnd=3)
        assert eng.trace[0]["upfloaters"] == [5]

    def test_c5_when_the_next_group_is_blocked(self):
        # 1 met every 2-MP team: the upfloater comes from the 0-MP group.
        teams = [team(1, 4, "WB", [2, 3]), team(2, 2), team(3, 2), team(4, 0), team(5, 0), team(6, 0)]
        rows, eng = pair(teams, rnd=3)
        assert eng.trace[0]["upfloaters"] == [4]

    def test_c6_keeps_the_next_scoregroup_pairable(self):
        # {1} needs one upfloater from {2,3,4}. Taking 2 (lexicographically
        # first) leaves 3-4, who met: the next bracket would need two
        # upfloaters. Taking 3 leaves 2-4, pairable: C6 picks 3.
        teams = [team(1, 4), team(2, 2), team(3, 2, "W", [4]), team(4, 2, "B", [3]), team(5, 0), team(6, 0)]
        _, eng = pair(teams, rnd=2)
        assert eng.trace[0]["upfloaters"] == [3]
        assert eng.trace[0]["c6"] == 0

    def test_c6_parity_mode_agrees_when_parity_is_reachable(self):
        teams = [team(1, 4), team(2, 2), team(3, 2, "W", [4]), team(4, 2, "B", [3]), team(5, 0), team(6, 0)]
        _, eng = pair(teams, rnd=2, c6_mode="parity")
        assert eng.trace[0]["upfloaters"] == [3]

    def test_c6_does_not_apply_when_the_next_group_empties(self):
        teams = [team(1, 4), team(2, 4), team(3, 4), team(4, 2), team(5, 0), team(6, 0)]
        _, eng = pair(teams, rnd=3)
        assert eng.trace[0]["upfloaters"] == [4] and eng.trace[0]["c6"] == 0

    def test_c7_avoids_previous_floaters(self):
        teams = [team(1, 4), team(2, 2, floater_last_round=True), team(3, 2), team(4, 2), team(5, 0)]
        teams.append(team(6, 0))
        _, eng = pair(teams, rnd=3, total=9)
        # 2 floated last round: 3 goes up instead (C6 equal: two of {2,3,4} remain).
        assert eng.trace[0]["upfloaters"] == [3] and eng.trace[0]["c7"] == 0

    def test_c7_does_not_apply_in_the_last_two_rounds(self):
        teams = [team(1, 4), team(2, 2, floater_last_round=True), team(3, 2), team(4, 2), team(5, 0), team(6, 0)]
        for rnd, total in ((3, 4), (3, 3)):
            _, eng = pair(teams, rnd=rnd, total=total)
            assert eng.trace[0]["upfloaters"] == [2]

    def test_floater_derived_from_history(self):
        # Round 1: 1 beat 3, 4 beat 2. Round 2: 1 (2 MP) met 2 (0 MP) and
        # 3 (0 MP) met 4 (2 MP): Article 1.5, all four floated (both teams
        # of a cross-score match). Had 1 met 4 and 2 met 3, none would.
        h = lambda r, o, c, mp: {"round": r, "opponent": o, "color": c, "type": "played", "mp": mp, "gp": mp}
        r1 = {1: h(1, 3, "white", 2), 3: h(1, 1, "black", 0), 4: h(1, 2, "white", 2), 2: h(1, 4, "black", 0)}
        cross = {1: h(2, 2, "black", 2), 2: h(2, 1, "white", 0), 3: h(2, 4, "white", 2), 4: h(2, 3, "black", 0)}
        same = {1: h(2, 4, "black", 2), 4: h(2, 1, "white", 0), 2: h(2, 3, "white", 2), 3: h(2, 2, "black", 0)}
        for r2, floated in ((cross, True), (same, False)):
            teams = [{"id": i, "history": [r1[i], r2[i]]} for i in range(1, 5)]
            eng = TeamSwissEngine(players=teams, round_number=3, total_rounds=9)
            assert all(eng.teams[i].prev_floater is floated for i in range(1, 5))

    def test_bye_and_forfeit_floaters_options(self):
        # A2/A3 (doc/TEAM_SWISS.md): the PAB team and a forfeit are no
        # floaters unless the options say so.
        teams = [team(1, 1, "Wp"), team(2, 2, "W+", [None, 3]), team(3, 0, "B-", [None, 2]), team(4, 2, "BW")]
        eng = TeamSwissEngine(players=teams, round_number=3, total_rounds=9)
        assert not eng.teams[1].prev_floater and not eng.teams[2].prev_floater
        eng = TeamSwissEngine(players=teams, round_number=3, total_rounds=9, bye_is_floater=True, unplayed_is_floater=True)
        assert eng.teams[1].prev_floater
        teams[1]["history"][0]["mp"] = 2.0  # 2 led 3 before round 2 -> different scores
        eng = TeamSwissEngine(players=teams, round_number=3, total_rounds=9, unplayed_is_floater=True)
        assert eng.teams[2].prev_floater and eng.teams[3].prev_floater

    def test_c8_colour_preferences(self):
        # Lexicographic first would be 1-3, 2-4 ("1 2 3 4"); but 1 and 3 both
        # want White (Type A: BB) and 2 and 4 both want Black (WW). The first
        # pairing meeting every preference is 1-4, 2-3 ("1 2 4 3").
        teams = [team(1, 2, "BB"), team(2, 2, "WW"), team(3, 2, "BB"), team(4, 2, "WW")]
        rows, eng = pair(teams)
        assert unordered(rows) == [(1, 4), (2, 3)] and eng.trace[0]["c8"] == 0
        assert pairs_of(rows)[0] == [(1, 4), (3, 2)]
        rows, eng = pair(teams, colour_preferences="none")
        assert unordered(rows) == [(1, 3), (2, 4)]

    def test_c9_type_b_strong_preferences(self):
        # Type B, all four want White. Every pairing leaves two
        # preferences unmet (C8 = 2); C9 prefers strong vs mild pairs.
        # 1, 2 strong (BB); 3, 4 mild (CD -1).
        teams = [team(1, 2, "BB"), team(2, 2, "BB"), team(3, 2, "WBB"[1:]) , team(4, 2, "B")]
        teams[2] = team(3, 2, "B")
        rows, eng = pair(teams, rnd=3, colour_preferences="B")
        assert eng.teams[1].pref_strength == 2 and eng.teams[3].pref_strength == 1
        assert unordered(rows) == [(1, 3), (2, 4)]
        assert eng.trace[0]["c8"] == 2 and eng.trace[0]["c9"] == 0

    def test_c9_only_for_type_b(self):
        # Everybody strongly wants White: C8 = 2 whatever the pairing; C9
        # counts the strong ones only under Type B.
        teams = [team(i, 2, "BB") for i in range(1, 5)]
        rows, eng = pair(teams)
        assert eng.trace[0]["c8"] == 2 and eng.trace[0]["c9"] == 0
        rows, eng = pair(teams, colour_preferences="B")
        assert eng.trace[0]["c8"] == 2 and eng.trace[0]["c9"] == 2
        assert unordered(rows) == [(1, 3), (2, 4)]

    def test_c10_upfloaters_opponent_not_a_previous_floater(self):
        # {1,2,3} + one upfloater (4). Lexicographic first: 1-3, 2-4; but 2
        # floated last round, so 4 meets 1 instead: 1-4, 2-3 ("1 2 4 3").
        teams = [team(1, 4), team(2, 4, floater_last_round=True), team(3, 4), team(4, 2), team(5, 2), team(6, 2)]
        rows, eng = pair(teams, rnd=3)
        assert eng.trace[0]["upfloaters"] == [4]
        assert unordered(rows)[:2] == [(1, 4), (2, 3)] and eng.trace[0]["c10"] == 0
        # Not in the last two rounds:
        rows, _ = pair(teams, rnd=3, total=4)
        assert unordered(rows)[:2] == [(1, 3), (2, 4)]

    def test_upfloater_in_lexicographic_identifier_order(self):
        teams = [team(i, 2) for i in range(1, 9)]
        rows, _ = pair(teams, rnd=2)
        assert unordered(rows) == [(1, 5), (2, 6), (3, 7), (4, 8)]


# ---------------------------------------------------------------------------
# Colour preferences
# ---------------------------------------------------------------------------


class TestColourPreferences:
    @pytest.mark.parametrize("colours, expected", [
        ("", None), ("W", None), ("B", None), ("WB", None), ("BB", "W"), ("WW", "B"),
        ("WBB", "W"), ("BWW", "B"), ("WWB", None), ("BBW", None), ("BBWWBB", "W"),
        ("BBB", "W"), ("WWWB", "B"), ("WBBB", "W"),
    ])
    def test_type_a(self, colours, expected):
        assert type_a_preference(list(colours)) == expected

    @pytest.mark.parametrize("colours, last, expected", [
        ("", False, (None, 0)),
        ("B", False, ("W", 1)), ("W", False, ("B", 1)),
        ("BB", False, ("W", 2)), ("WW", False, ("B", 2)),
        ("WB", False, ("W", 1)), ("BW", False, ("B", 1)),
        ("WB", True, (None, 0)), ("BW", True, (None, 0)),
        ("B", True, ("W", 1)),
        ("WWBB", True, ("W", 2)),
        ("WBB", False, ("W", 2)), ("BWW", True, ("B", 2)),
        ("WWWBB", False, ("B", 1)),
    ])
    def test_type_b(self, colours, last, expected):
        assert type_b_preference(list(colours), last) == expected

    def test_type_b_literal_last_round_option(self):
        assert type_b_preference(list("WWBB"), True, keep_strong_in_last_round=False) == (None, 0)

    def test_none_type_has_no_preferences(self):
        teams = [team(1, 2, "BB"), team(2, 2, "BB")]
        _, eng = pair(teams, colour_preferences="none")
        assert eng.teams[1].pref is None


# ---------------------------------------------------------------------------
# Colour allocation, Article 4.3
# ---------------------------------------------------------------------------


def _states(a, b, **kw):
    eng = TeamSwissEngine(players=[a, b], round_number=1 + max(len(a["history"]), len(b["history"])),
                          total_rounds=kw.pop("total", 9), **kw)
    return eng, eng.teams[a["id"]], eng.teams[b["id"]]


class TestColourRules:
    def _check(self, a, b, rule, white, **kw):
        eng, ta, tb = _states(a, b, **kw)
        assert eng.colour_rule(ta, tb) == rule
        assert eng.allocate_colours(ta, tb)[0].id == white

    def test_4_3_1_initial_colour_by_odd_tpn(self):
        self._check(team(1), team(2), "4.3.1", 1)
        self._check(team(1), team(2), "4.3.1", 2, initial_color="black")
        self._check(team(2), team(3), "4.3.1", 3)          # first-team 2 (even) -> opposite
        self._check(team(2, 0, "u"), team(3, 0, "+"), "4.3.1", 3)   # no match played yet

    def test_4_3_2_only_one_preference(self):
        self._check(team(1, 2, "WW"), team(2, 2, "WB"), "4.3.2", 2)
        self._check(team(1, 2, "WB"), team(2, 2, "BB"), "4.3.2", 2)

    def test_4_3_3_opposite_preferences(self):
        self._check(team(1, 2, "WW"), team(2, 2, "BB"), "4.3.3", 2)

    def test_4_3_4_type_b_one_strong(self):
        # Both want White: 2 strongly (BB), 1 mildly (CD -1 after WBB? no: B only).
        self._check(team(1, 2, "WBB"[:1] + "B"), team(2, 2, "BB"), "4.3.4", 2, colour_preferences="B")

    def test_4_3_5_lower_colour_difference(self):
        self._check(team(1, 2, "W"), team(2, 2, "B"), "4.3.5", 2)
        # Type A, both want White (CD -2 vs -3): the lower CD gets it.
        self._check(team(1, 2, "BB"), team(2, 2, "BBB"), "4.3.5", 2)

    def test_4_3_6_alternate_from_last_difference(self):
        # Equal CD 0: last colours equal (W), before that 1 had B, 2 had W.
        self._check(team(1, 2, "WBBW"), team(2, 2, "BWBW"), "4.3.6", 1)

    def test_4_3_6_uses_c0402_3_4_alignment(self):
        # 1: W B u B W -> played W B B W; 2: u B W B W -> played B W B W.
        # Aligned at the end (C.04.2 3.4): W/W, B/B, then 1 had B and 2 had W
        # -> 1 White by 4.3.6. Aligned by round it would fall to 4.3.8.
        self._check(team(1, 3, "WBuBW"), team(2, 2, "uBWBW"), "4.3.6", 1)

    def test_4_3_7_first_team_preference(self):
        # Both want White with equal strength, equal CD and equal history.
        self._check(team(1, 4, "BB"), team(2, 2, "BB"), "4.3.7", 1)
        self._check(team(1, 2, "BB"), team(2, 4, "BB"), "4.3.7", 2)

    def test_4_3_8_alternate_first_team(self):
        self._check(team(1, 2, "W"), team(2, 2, "W"), "4.3.8", 2)
        self._check(team(1, 2, "WB"), team(2, 4, "WB"), "4.3.8", 2)  # first-team 2 alternates B -> W

    def test_4_3_9_alternate_other_team(self):
        # The first-team (more MP) has played nothing (CD 0); the other
        # played W then B (CD 0, no preference): it alternates to White.
        self._check(team(1, 4, "+"), team(2, 2, "WB"), "4.3.9", 2)

    def test_first_team_order(self):
        eng, a, b = _states(team(1, 2, "W", gp=2), team(2, 2, "W", gp=3))
        assert eng.first_team(a, b)[0].id == 2         # 4.2.2 secondary score
        eng, a, b = _states(team(1, 2, "W", gp=2), team(2, 2, "W", gp=3), use_secondary_for_colours=False)
        assert eng.first_team(a, b)[0].id == 1         # 4.2.3 TPN
        eng, a, b = _states(team(1, 2, "W"), team(2, 3, "W"))
        assert eng.first_team(a, b)[0].id == 2         # 4.2.1 primary score

    def test_forfeits_and_byes_do_not_count_for_colours(self):
        eng, a, _ = _states(team(1, 2, "W+-pB"), team(2))
        assert a.colours == ["W", "B"] and a.cd == 0


class TestBoardColours:
    def test_default_alternates_from_board_one(self):
        rows, _ = pair([team(1), team(2)], rnd=1)
        assert [b["white_id"] for b in rows[0]["boards"]] == [1, 2, 1, 2]

    def test_sequence_from_record_352(self):
        rows, _ = pair([team(1), team(2)], rnd=1, board_colours="WBBW")
        assert [b["white_id"] for b in rows[0]["boards"]] == [1, 2, 2, 1]
        rows, _ = pair([team(1), team(2)], rnd=1, board_colours="BWWB")   # read from the other side
        assert [b["white_id"] for b in rows[0]["boards"]] == [1, 2, 2, 1]

    def test_odd_number_of_boards(self):
        rows, _ = pair([team(1), team(2)], rnd=1, boards=5)
        assert [b["white_id"] for b in rows[0]["boards"]] == [1, 2, 1, 2, 1]


# ---------------------------------------------------------------------------
# Scores: MP / GP primary, Baku
# ---------------------------------------------------------------------------


class TestScores:
    def test_gp_primary_changes_the_scoregroups(self):
        teams = [team(1, mp=2, gp=2.5), team(2, mp=2, gp=4), team(3, mp=0, gp=1.5), team(4, mp=1, gp=2)]
        rows, eng = pair(teams, rnd=2)
        assert eng.trace[0]["residents"] == [1, 2]
        rows, eng = pair(teams, rnd=2, primary_score="gp")
        assert eng.trace[0]["residents"] == [2]

    def test_scores_from_history(self):
        h = [{"round": 1, "opponent": 9, "color": "white", "type": "played", "mp": 2, "gp": 3},
             {"round": 2, "opponent": None, "type": "pab", "mp": 1, "gp": 2}]
        eng = TeamSwissEngine(players=[{"id": 1, "history": h}, {"id": 2}], round_number=3, total_rounds=9)
        assert eng.teams[1].mp == 3 and eng.teams[1].gp == 5
        eng = TeamSwissEngine(players=[{"id": 1, "history": h}, {"id": 2}], round_number=3, total_rounds=9,
                              primary_score="gp")
        assert eng.teams[1].score == 5

    def test_adjourned_is_a_draw_for_pairing(self):
        h = [{"round": 1, "opponent": 2, "color": "white", "type": "adjourned"}]
        eng = TeamSwissEngine(players=[{"id": 1, "history": h}, {"id": 2}], round_number=2, total_rounds=9)
        assert eng.teams[1].mp == 1 and eng.teams[1].gp == 2 and eng.teams[1].colours == ["W"]


class TestBaku:
    def test_schedule_example_2(self):
        # C.04.7 1.4.4 Example 2: 11 rounds, MP: 2 in rounds 1-3, 1 in 4-6.
        assert [baku_virtual_points(r, 11, 2) for r in range(1, 12)] == [2, 2, 2, 1, 1, 1, 0, 0, 0, 0, 0]
        assert [baku_virtual_points(r, 9, 2) for r in range(1, 10)] == [2, 2, 2, 1, 1, 0, 0, 0, 0]

    def test_group_size(self):
        assert baku_group_size(161) == 82 and baku_group_size(178) == 90 and baku_group_size(8) == 4

    def test_records_match_the_trf26_example(self):
        # "11 round Swiss team tournament based on matchpoints with 178 teams".
        assert baku_records(11, 178) == [
            {"mp": 2.0, "gp": 0.0, "first_round": 1, "last_round": 3, "first_team": 1, "last_team": 90},
            {"mp": 1.0, "gp": 0.0, "first_round": 4, "last_round": 6, "first_team": 1, "last_team": 90},
        ]

    def test_round_one_splits_ga_and_gb(self):
        rows, _ = pair([team(i) for i in range(1, 9)], rnd=1, total=9, accelerated=True)
        assert unordered(rows) == [(1, 3), (2, 4), (5, 7), (6, 8)]
        rows, _ = pair([team(i) for i in range(1, 9)], rnd=1, total=9)
        assert unordered(rows) == [(1, 5), (2, 6), (3, 7), (4, 8)]

    def test_virtual_points_never_touch_the_real_scores(self):
        eng = TeamSwissEngine(players=[team(i) for i in range(1, 9)], round_number=1, total_rounds=9, accelerated=True)
        assert eng.teams[1].score == 2 and eng.teams[1].mp == 0 and eng.teams[5].score == 0

    def test_explicit_records_override(self):
        recs = [{"mp": 2, "gp": 0, "first_round": 1, "last_round": 1, "first_team": 1, "last_team": 2}]
        eng = TeamSwissEngine(players=[team(i) for i in range(1, 9)], round_number=1, total_rounds=9,
                              acceleration_records=recs)
        assert [eng.teams[i].score for i in range(1, 5)] == [2, 2, 0, 0]

    def test_gp_primary_rejects_baku(self):
        with pytest.raises(ValueError):
            TeamSwissSettings(primary_score="gp", accelerated=True)


# ---------------------------------------------------------------------------
# Participation
# ---------------------------------------------------------------------------


class TestParticipation:
    def test_absent_and_withdrawn_teams_are_not_paired(self):
        teams = [team(1), team(2), team(3, absent=True), team(4, withdrawn=True), team(5), team(6)]
        rows, _ = pair(teams, rnd=1)
        ids = {i for r in rows for i in (r["white_id"], r["black_id"]) if i}
        assert ids == {1, 2, 5, 6}
        rows, _ = pair([team(i) for i in range(1, 7)], rnd=1, absent_teams=[3], withdrawn_teams=[4])
        ids = {i for r in rows for i in (r["white_id"], r["black_id"]) if i}
        assert ids == {1, 2, 5, 6}

    def test_impossible_round_is_flagged_not_broken_silently(self):
        teams = [team(1, 2, "W", [2]), team(2, 0, "B", [1])]
        rows, _ = pair(teams)
        assert rows[0]["violations"] == ["C1"]

    def test_duplicate_tpn_rejected(self):
        with pytest.raises(ValueError):
            TeamSwissEngine(players=[{"id": 1, "tpn": 1}, {"id": 2, "tpn": 1}], round_number=1, total_rounds=5)

    def test_input_not_mutated(self):
        teams = [team(i, 2, "WB") for i in range(1, 5)]
        import copy
        snapshot = copy.deepcopy(teams)
        pair(teams)
        assert teams == snapshot

    def test_table_order_c0402_3_6(self):
        teams = [team(1, 0), team(2, 4), team(3, 2), team(4, 4)]
        rows, _ = pair(teams, rnd=3)
        assert [sorted((r["white_id"], r["black_id"])) for r in rows] == [[2, 4], [1, 3]]
        assert [r["table"] for r in rows] == [1, 2]


def test_output_rows_follow_the_engine_output_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from caissify_pairings.schemas import engine_output_schema

    teams = [team(1, 1, "p"), team(2, 2, "W", [3]), team(3, 0, "B", [2]), team(4, 2), team(5, 0)]
    rows, _ = pair(teams)
    jsonschema.validate(rows, engine_output_schema())
    rows, _ = pair([team(1, 2, "W", [2]), team(2, 0, "B", [1])])      # flagged C1 row
    jsonschema.validate(rows, engine_output_schema())
