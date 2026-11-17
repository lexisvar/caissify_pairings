"""
Matching helpers for the team Swiss engine (C.04.6).

The team rules choose, among all legal pairings, the *lexicographically
first* one that is optimal for a list of quality criteria. This module
holds the graph routines that make that choice exact and fast:

* :func:`has_perfect_matching` - legality and completion checks (C1, C3),
  with Dirac's theorem as a shortcut for the dense graphs of early rounds.
* :func:`min_cost_perfect_matching` - the optimum of additive pair costs
  (Edmonds' blossom algorithm, via networkx).
* :func:`lex_first_optimal_matching` - the first pairing, in the order of
  C.04.6 Article 3.6.2/3.6.3 (pairing identifiers), among those with the
  optimal cost.

Nodes are hashable ids; ``rank`` maps every node to its position in the
TPN order. ``adj`` maps every node to the set of nodes it may be paired
with (absolute criterion C1 and forbidden pairings already applied).
"""

from __future__ import annotations

from typing import Callable, Dict, FrozenSet, Hashable, Iterable, List, Optional, Sequence, Set, Tuple

import networkx as nx

Node = Hashable
Pair = Tuple[Node, Node]


# ---------------------------------------------------------------------------
# Perfect-matching existence (C1 legality, C3 completion)
# ---------------------------------------------------------------------------


def maximum_matching(nodes: Iterable[Node], adj: Dict[Node, Set[Node]]) -> Dict[Node, Node]:
    """Maximum-cardinality matching of the subgraph induced by ``nodes``."""
    node_set = set(nodes)
    g = nx.Graph()
    g.add_nodes_from(node_set)
    for a in node_set:
        for b in adj.get(a, ()):
            if b in node_set and a != b:
                g.add_edge(a, b)
    mate: Dict[Node, Node] = {}
    for a, b in nx.max_weight_matching(g, maxcardinality=True):
        mate[a] = b
        mate[b] = a
    return mate


def has_perfect_matching(
    nodes: Iterable[Node],
    adj: Dict[Node, Set[Node]],
    cache: Optional[Dict[FrozenSet[Node], bool]] = None,
) -> bool:
    """
    True when the teams in ``nodes`` can all be paired with each other.

    Shortcuts before the blossom algorithm: an odd count or an isolated
    team fails at once; when every team may meet at least half of the
    others the graph is Hamiltonian (Dirac's theorem), so an even count
    always has a perfect matching.
    """
    node_set = frozenset(nodes)
    if cache is not None and node_set in cache:
        return cache[node_set]
    result = _has_perfect_matching(node_set, adj)
    if cache is not None:
        cache[node_set] = result
    return result


def _has_perfect_matching(node_set: FrozenSet[Node], adj: Dict[Node, Set[Node]]) -> bool:
    n = len(node_set)
    if n == 0:
        return True
    if n % 2:
        return False
    min_degree = n
    for a in node_set:
        d = len(adj.get(a, set()) & node_set)
        if d == 0:
            return False
        if d < min_degree:
            min_degree = d
    if n == 2:
        return True
    if 2 * min_degree >= n:
        return True
    return len(maximum_matching(node_set, adj)) == n


# ---------------------------------------------------------------------------
# Minimum-cost perfect matching
# ---------------------------------------------------------------------------


def min_cost_perfect_matching(
    nodes: Sequence[Node],
    adj: Dict[Node, Set[Node]],
    cost: Callable[[Node, Node], int],
    allowed: Optional[Callable[[Node, Node], bool]] = None,
) -> Optional[Tuple[int, Dict[Node, Node]]]:
    """
    Perfect matching of ``nodes`` with the smallest total of ``cost``
    (non-negative integers), or ``None`` when no perfect matching exists.

    ``allowed`` filters edges further (used for the role constraints of
    the lexicographic search).
    """
    node_list = list(nodes)
    index = {n: i for i, n in enumerate(node_list)}
    if not node_list:
        return 0, {}
    if len(node_list) % 2:
        return None
    edges: List[Tuple[Node, Node, int]] = []
    max_cost = 0
    for a in node_list:
        ia = index[a]
        for b in adj.get(a, ()):
            ib = index.get(b)
            if ib is None or ib <= ia:
                continue
            if allowed is not None and not allowed(a, b):
                continue
            c = cost(a, b)
            edges.append((a, b, c))
            if c > max_cost:
                max_cost = c
    if not edges:
        return None
    if max_cost == 0:
        mate = maximum_matching(node_list, _adjacency(node_list, edges))
        if len(mate) != len(node_list):
            return None
        return 0, mate
    g = nx.Graph()
    g.add_nodes_from(node_list)
    big = max_cost + 1
    for a, b, c in edges:
        g.add_edge(a, b, weight=big - c)
    mate = {}
    total = 0
    for a, b in nx.max_weight_matching(g, maxcardinality=True):
        mate[a] = b
        mate[b] = a
        total += cost(a, b)
    if len(mate) != len(node_list):
        return None
    return total, mate


def _adjacency(nodes: Sequence[Node], edges: Iterable[Tuple[Node, Node, int]]) -> Dict[Node, Set[Node]]:
    out: Dict[Node, Set[Node]] = {n: set() for n in nodes}
    for a, b, _ in edges:
        out[a].add(b)
        out[b].add(a)
    return out


# ---------------------------------------------------------------------------
# Lexicographically first optimal pairing (C.04.6 Article 3.6)
# ---------------------------------------------------------------------------

TOP = 1
BOTTOM = 2


class LexFirstMatcher:
    """
    Find the first pairing, in pairing-identifier order (C.04.6 3.6.2),
    among the perfect matchings with minimum total cost.

    The identifier lists the top members (the smaller TPN of each pair)
    in ascending order, then their partners. So the search first fixes,
    team by team in TPN order, whether a team can be a top member, then
    gives every top member, in order, the smallest partner that still
    leaves an optimal completion.

    Every decision is checked against an *incumbent*: an optimal matching
    that respects the decisions so far. When the incumbent already agrees,
    or a two-pair swap makes it agree at no extra cost, no new matching is
    computed; otherwise the blossom algorithm decides (an exact check).
    """

    def __init__(
        self,
        order: Sequence[Node],
        adj: Dict[Node, Set[Node]],
        cost: Callable[[Node, Node], int],
    ):
        self.order = list(order)
        self.rank = {n: i for i, n in enumerate(self.order)}
        node_set = set(self.order)
        self.adj = {n: (adj.get(n, set()) & node_set) - {n} for n in self.order}
        self._cost = cost
        self._cost_cache: Dict[Tuple[Node, Node], int] = {}
        self.oracle_calls = 0

    # -- helpers -----------------------------------------------------------

    def cost(self, a: Node, b: Node) -> int:
        key = (a, b) if self.rank[a] < self.rank[b] else (b, a)
        c = self._cost_cache.get(key)
        if c is None:
            c = self._cost(*key)
            self._cost_cache[key] = c
        return c

    def _ok(self, a: Node, b: Node, role: Dict[Node, int]) -> bool:
        """Edge a-b is usable under the role decisions (top = smaller TPN)."""
        if b not in self.adj[a]:
            return False
        lo, hi = (a, b) if self.rank[a] < self.rank[b] else (b, a)
        return role.get(lo) != BOTTOM and role.get(hi) != TOP

    def _oracle(
        self,
        nodes: Sequence[Node],
        role: Dict[Node, int],
        target: int,
    ) -> Optional[Dict[Node, Node]]:
        """An optimal (cost == target) matching of ``nodes`` under ``role``, if any."""
        self.oracle_calls += 1
        res = min_cost_perfect_matching(
            sorted(nodes, key=self.rank.__getitem__),
            self.adj,
            self.cost,
            allowed=lambda a, b: self._ok(a, b, role),
        )
        if res is None or res[0] != target:
            return None
        return res[1]

    # -- main --------------------------------------------------------------

    def solve(self) -> Optional[Tuple[int, List[Pair]]]:
        """Return (optimal cost, pairs as (top, bottom)) or None if no perfect matching."""
        if not self.order:
            return 0, []
        res = min_cost_perfect_matching(self.order, self.adj, self.cost)
        if res is None:
            return None
        opt, mate = res
        role: Dict[Node, int] = {}

        # Phase 1: the set of top members, smallest TPNs first.
        # Scanning in TPN order, every bottom member closes a top member
        # opened before it, so "open" tops never go negative and must all
        # be closed by the teams still to come.
        n = len(self.order)
        open_tops = 0
        for t in self.order:
            rt = self.rank[t]
            later = n - rt - 1
            if self.rank[mate[t]] > rt:
                role[t] = TOP
                open_tops += 1
                continue
            if open_tops + 1 > later or not any(self.rank[u] > rt for u in self.adj[t]):
                role[t] = BOTTOM
                open_tops -= 1
                continue
            role[t] = TOP
            fixed = self._swap_for_top(mate, t, role)
            if fixed is None:
                fixed = self._oracle(self.order, role, opt)
            if fixed is None:
                role[t] = BOTTOM
                open_tops -= 1
            else:
                mate = fixed
                open_tops += 1

        tops = [n for n in self.order if role[n] == TOP]
        bottoms = [n for n in self.order if role[n] == BOTTOM]

        # Phase 2: partners of the top members, in order.
        pairs: List[Pair] = []
        free = set(self.order)
        remaining = opt
        for s in tops:
            free.discard(s)
            chosen = None
            for j in bottoms:
                if j not in free or self.rank[j] < self.rank[s] or j not in self.adj[s]:
                    continue
                if mate[s] == j:
                    chosen = j
                    break
                c = self.cost(s, j)
                if c > remaining:
                    continue
                swapped = self._swap_for_pair(mate, s, j, role)
                if swapped is not None:
                    mate = swapped
                    chosen = j
                    break
                rest = [n for n in free if n != j]
                sub = self._oracle(rest, role, remaining - c)
                if sub is not None:
                    mate = dict(sub)
                    for a, b in pairs:
                        mate[a], mate[b] = b, a
                    mate[s], mate[j] = j, s
                    chosen = j
                    break
            if chosen is None:  # pragma: no cover - the incumbent is always a witness
                raise RuntimeError("lexicographic search lost its optimal witness")
            pairs.append((s, chosen))
            free.discard(chosen)
            remaining -= self.cost(s, chosen)
        return opt, pairs

    # -- incumbent repairs -------------------------------------------------

    def _swap_for_top(self, mate: Dict[Node, Node], t: Node, role: Dict[Node, int]) -> Optional[Dict[Node, Node]]:
        """Make t a top member by exchanging partners with one other pair, at no extra cost."""
        u = mate[t]
        base = self.cost(t, u)
        seen = set()
        for a in self.order:
            if a in seen or a == t or a == u:
                continue
            b = mate[a]
            seen.add(a)
            seen.add(b)
            old = base + self.cost(a, b)
            for x, y in ((a, b), (b, a)):
                # new pairs: t-x and u-y, with t the smaller of t-x
                if self.rank[x] <= self.rank[t]:
                    continue
                if not self._ok(t, x, role) or not self._ok(u, y, role):
                    continue
                if self.cost(t, x) + self.cost(u, y) <= old:
                    new = dict(mate)
                    new[t], new[x] = x, t
                    new[u], new[y] = y, u
                    return new
        return None

    def _swap_for_pair(self, mate: Dict[Node, Node], s: Node, j: Node, role: Dict[Node, int]) -> Optional[Dict[Node, Node]]:
        """Pair s with j by exchanging partners with j's pair, at no extra cost."""
        b0 = mate[s]
        t0 = mate[j]
        if not self._ok(s, j, role) or not self._ok(t0, b0, role):
            return None
        if self.cost(s, j) + self.cost(t0, b0) <= self.cost(s, b0) + self.cost(t0, j):
            new = dict(mate)
            new[s], new[j] = j, s
            new[t0], new[b0] = b0, t0
            return new
        return None


def lex_first_optimal_matching(
    order: Sequence[Node],
    adj: Dict[Node, Set[Node]],
    cost: Callable[[Node, Node], int],
) -> Optional[Tuple[int, List[Pair]]]:
    """Convenience wrapper around :class:`LexFirstMatcher`."""
    return LexFirstMatcher(order, adj, cost).solve()
