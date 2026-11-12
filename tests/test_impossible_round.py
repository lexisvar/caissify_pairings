"""
A round with no legal pairing (C.04.3: every pairing breaks C1 or C3).

JaVaFo answers "Pairing impossible under the current rules - intervene
manually". The engine still returns a complete round for the arbiter, but
with at most the one bye an odd field needs (never two byes for an even
field) and every board that breaks an absolute criterion flagged
(``violations``: C1 rematch, C3 colour), so the tournament manager shows it.

The tournament is the 8-player, 7-round JaVaFo cross-validation run with
seed 77: in round 7 players 7 and 8 have met everyone else and both must
have White.
"""
import random
import warnings

from tests.test_dutch_javafo import _apply_results, _make_players, _simulate_results
from caissify_pairings.engines.dutch import dutch_pairings


def _play(num_players=8, num_rounds=7, seed=77):
    random.seed(seed)
    players = _make_players(num_players)
    pmap = {p["id"]: p for p in players}
    prev = set()
    rounds = []
    for rnd in range(1, num_rounds + 1):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pairings = dutch_pairings(players, prev, rnd, num_rounds)
        rounds.append(pairings)
        results = _simulate_results(pairings, pmap)
        _apply_results(players, pairings, results)
        for p in pairings:
            if not p.get("bye") and p.get("black_id") is not None:
                prev.add(tuple(sorted((p["white_id"], p["black_id"]))))
    return rounds


def test_an_impossible_round_keeps_everyone_paired_and_flags_the_break():
    last = _play()[-1]
    byes = [p for p in last if p.get("bye")]
    assert byes == [], "an even field gets no bye, even when no legal pairing exists"
    flagged = [p for p in last if p.get("violations")]
    assert flagged, "the board that breaks a rule is flagged for the arbiter"
    assert {"C3"} <= {v for p in flagged for v in p["violations"]}


def test_legal_rounds_are_not_flagged():
    for rnd in _play()[:-1]:
        assert not [p for p in rnd if p.get("violations")]
