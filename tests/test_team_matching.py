"""
Property tests for the team engine's matching helpers
(:mod:`caissify_pairings.engines._team_matching`) against brute force on
random graphs: perfect-matching existence, minimum cost, and the
lexicographically first optimal pairing of C.04.6 Article 3.6.
"""

from __future__ import annotations

import random

import pytest

from caissify_pairings.engines import _team_matching as tm
from caissify_pairings.engines.team_swiss import pairing_identifier


def _random_graph(rng, n, density):
    nodes = list(range(1, n + 1))
    adj = {v: set() for v in nodes}
    for a in nodes:
        for b in nodes:
            if a < b and rng.random() < density:
                adj[a].add(b)
                adj[b].add(a)
    costs = {}
    for a in nodes:
        for b in nodes:
            if a < b:
                costs[(a, b)] = rng.choice([0, 0, 0, 1, 1, 2, 5])
    return nodes, adj, lambda a, b: costs[(min(a, b), max(a, b))]


def _all_matchings(nodes, adj):
    if not nodes:
        yield []
        return
    a, rest = nodes[0], nodes[1:]
    for i, b in enumerate(rest):
        if b in adj[a]:
            for m in _all_matchings(rest[:i] + rest[i + 1:], adj):
                yield [(a, b)] + m


@pytest.mark.parametrize("seed", range(400))
def test_lex_first_optimal_against_brute_force(seed):
    rng = random.Random(seed)
    n = rng.choice([2, 4, 6, 8, 10])
    nodes, adj, cost = _random_graph(rng, n, rng.choice([0.4, 0.7, 0.9, 1.0]))
    matchings = list(_all_matchings(nodes, adj))
    assert tm.has_perfect_matching(nodes, adj) is bool(matchings)
    res = tm.lex_first_optimal_matching(nodes, adj, cost)
    if not matchings:
        assert res is None
        return
    best = min(matchings, key=lambda m: (sum(cost(a, b) for a, b in m), pairing_identifier(m)))
    opt, pairs = res
    assert opt == sum(cost(a, b) for a, b in best)
    assert pairing_identifier(pairs) == pairing_identifier(best)


@pytest.mark.parametrize("seed", range(40))
def test_larger_graphs_cost_matches_blossom(seed):
    # Beyond brute force: the lexicographic search must keep the optimum.
    rng = random.Random(1000 + seed)
    n = rng.choice([20, 30, 40])
    nodes, adj, cost = _random_graph(rng, n, rng.choice([0.3, 0.8]))
    full = tm.min_cost_perfect_matching(nodes, adj, cost)
    res = tm.lex_first_optimal_matching(nodes, adj, cost)
    if full is None:
        assert res is None
        return
    opt, pairs = res
    assert opt == full[0]
    assert sum(cost(a, b) for a, b in pairs) == opt
    assert sorted(x for p in pairs for x in p) == nodes
    assert all(b in adj[a] and a < b for a, b in pairs)


def test_dirac_shortcut_and_odd_sets():
    nodes = list(range(10))
    adj = {v: set(nodes) - {v} for v in nodes}
    assert tm.has_perfect_matching(nodes, adj)
    assert not tm.has_perfect_matching(nodes[:9], adj)
    adj[0] = set()
    for v in nodes[1:]:
        adj[v].discard(0)
    assert not tm.has_perfect_matching(nodes, adj)
