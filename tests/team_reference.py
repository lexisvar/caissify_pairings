"""
Brute-force reference pairer for the FIDE Swiss Team Pairing System
(C.04.6, 2026), used only by the tests.

It shares no code with the engine: it re-derives every team's state from
the history and then follows the regulation's own ordering literally:

* Article 3.4: bye candidates in order, the first that leaves a legal
  pairing (checked by exhaustive dynamic programming over subsets).
* Article 3.5: *every* subset of the potential upfloaters is generated;
  the legal ones are ranked by C4, C5, C6, C7 and then by the sorted TPN
  sequence (3.5.3/3.5.4).
* Article 3.6: pairings are visited in identifier order (top members as
  combinations in lexicographic order, then their partners in
  lexicographic order) and the first one with the minimum (C8, C9, C10)
  wins. The minimum itself comes from an exhaustive subset DP.
* Article 4: the colour rules 4.3.1-4.3.9 in order.

Interpretations are the engine defaults documented in doc/TEAM_SWISS.md
(quality criteria are minimised; floaters come from played matches only;
the bye is not a floater; Type B keeps a strong preference in the last
round). Small fields only: the subset enumerations are exponential.
"""

from __future__ import annotations

from functools import lru_cache
from itertools import combinations
from typing import List, Optional


def _letter(c):
    if c is None:
        return None
    c = str(c).lower()
    return {"w": "W", "white": "W", "b": "B", "black": "B"}.get(c)


def _status(e):
    s = str(e.get("type", e.get("status", "played"))).lower()
    return {
        "u": "pab", "bye": "pab", "+": "forfeit_win", "-": "forfeit_loss", "--": "double_forfeit",
        "f": "fpb", "h": "hpb", "z": "zpb", "absent": "zpb",
    }.get(s, s)


def _baku_virtual(rnd, total, win):
    acc = (total + 1) // 2          # accelerated rounds: first half, rounded up
    full = (acc + 1) // 2           # first half of them, rounded up: a win's points
    if rnd <= full:
        return win
    if rnd <= acc:
        return win / 2
    return 0.0


class Ref:
    def __init__(self, teams: List[dict], round_number: int, total_rounds: int, **opt):
        self.r = round_number
        self.total = total_rounds
        self.primary = opt.get("primary_score", "mp")
        self.use_secondary = opt.get("use_secondary_for_colours", True)
        self.ctype = opt.get("colour_preferences", "A") or "none"
        if self.ctype not in ("A", "B"):
            self.ctype = "none"
        self.initial_white = opt.get("initial_color", "white") == "white"
        self.c6_parity = opt.get("c6_mode", "minimise") == "parity"
        self.accelerated = opt.get("accelerated", False)
        self.win_mp = opt.get("match_points_win", 2.0)
        self.bye_is_floater = opt.get("bye_is_floater", False)
        self.forbidden = set()
        for item in opt.get("forbidden_pairs") or []:
            for a, b in combinations(item, 2):
                self.forbidden.add(frozenset((a, b)))
        n_teams = max([len(teams)] + [t.get("tpn", t["id"]) for t in teams])
        self.ga = 2 * -(-n_teams // 4)

        self.tpn = {t["id"]: t.get("tpn", t["id"]) for t in teams}
        self.hist = {}
        for t in teams:
            h = {}
            for i, e in enumerate(t.get("history") or []):
                rnd = e.get("round", i + 1)
                if rnd < round_number:
                    h[rnd] = dict(e, status=_status(e), colour=_letter(e.get("color", e.get("colour"))))
            self.hist[t["id"]] = h
        self.active = sorted(
            [t["id"] for t in teams if not t.get("absent") and not t.get("withdrawn")],
            key=lambda i: self.tpn[i],
        )

    # -- state ------------------------------------------------------------

    def virtual(self, tid, rnd):
        if not self.accelerated or self.tpn[tid] > self.ga:
            return 0.0
        return _baku_virtual(rnd, self.total, self.win_mp)

    def score_before(self, tid, rnd):
        key = self.primary
        return sum(e.get(key, 0.0) for r, e in self.hist[tid].items() if r < rnd) + self.virtual(tid, rnd)

    def score(self, tid):
        return self.score_before(tid, self.r)

    def secondary(self, tid):
        key = "gp" if self.primary == "mp" else "mp"
        return sum(e.get(key, 0.0) for e in self.hist[tid].values())

    def played(self, tid):
        return [self.hist[tid][r] for r in sorted(self.hist[tid]) if self.hist[tid][r]["status"] == "played"]

    def colours(self, tid):
        return [e["colour"] for e in self.played(tid) if e["colour"]]

    def met(self, a, b):
        return any(e.get("opponent") == b for e in self.played(a)) or \
            any(e.get("opponent") == a for e in self.played(b))

    def legal(self, a, b):
        return not self.met(a, b) and frozenset((a, b)) not in self.forbidden

    def bye_ok(self, tid):
        return not any(e["status"] in ("pab", "forfeit_win", "fpb") for e in self.hist[tid].values())

    def floater_prev(self, tid):
        e = self.hist[tid].get(self.r - 1)
        if e is None:
            return False
        if e["status"] == "pab":
            return self.bye_is_floater
        if e["status"] != "played" or e.get("opponent") is None or e["opponent"] not in self.hist:
            return False
        return self.score_before(tid, self.r - 1) != self.score_before(e["opponent"], self.r - 1)

    def pref(self, tid):
        cols = self.colours(tid)
        if self.ctype == "none" or not cols:
            return None, 0
        cd = cols.count("W") - cols.count("B")
        last2 = cols[-2:]
        strong = None
        if cd < -1 or (cd in (0, -1) and last2 == ["B", "B"]):
            strong = "W"
        elif cd > 1 or (cd in (0, 1) and last2 == ["W", "W"]):
            strong = "B"
        if self.ctype == "A":
            return strong, (2 if strong else 0)
        if strong:
            return strong, 2
        last = self.r == self.total
        if cd == -1:
            return "W", 1
        if cd == 1:
            return "B", 1
        if cd == 0 and not last:
            return ("W", 1) if cols[-1] == "B" else ("B", 1)
        return None, 0

    # -- exhaustive helpers -----------------------------------------------

    def matchable(self, ids) -> bool:
        ids = tuple(sorted(ids, key=lambda i: self.tpn[i]))
        return self._matchable(ids)

    @lru_cache(maxsize=None)
    def _matchable(self, ids) -> bool:
        if not ids:
            return True
        if len(ids) % 2:
            return False
        a = ids[0]
        for j in range(1, len(ids)):
            if self.legal(a, ids[j]) and self._matchable(ids[1:j] + ids[j + 1:]):
                return True
        return False

    def min_cost(self, ids, cost) -> Optional[int]:
        ids = tuple(sorted(ids, key=lambda i: self.tpn[i]))

        @lru_cache(maxsize=None)
        def f(rest):
            if not rest:
                return 0
            a = rest[0]
            best = None
            for j in range(1, len(rest)):
                b = rest[j]
                if not self.legal(a, b):
                    continue
                sub = f(rest[1:j] + rest[j + 1:])
                if sub is None:
                    continue
                v = cost(a, b) + sub
                if best is None or v < best:
                    best = v
            return best

        return f(ids)

    # -- Article 3.4 ------------------------------------------------------

    def choose_bye(self, ids):
        order = sorted(ids, key=lambda i: (self.score(i), -len(self.played(i)), -self.tpn[i]))
        for c in order:
            if self.bye_ok(c) and self.matchable([x for x in ids if x != c]):
                return c
        return None

    # -- Article 3.5 ------------------------------------------------------

    def choose_upfloaters(self, unpaired, top):
        lower = [i for i in unpaired if i not in top]
        lower.sort(key=lambda i: (-self.score(i), self.tpn[i]))
        if not lower:
            return []
        next_score = self.score(lower[0])
        next_group = {i for i in lower if self.score(i) == next_score}
        last_two = self.r >= self.total - 1
        best = None
        for size in range(0, len(lower) + 1):
            if (len(top) + size) % 2:
                continue
            for s in combinations(lower, size):
                bracket = set(top) | set(s)
                rest = [i for i in unpaired if i not in bracket]
                if not self.matchable(bracket) or not self.matchable(rest):
                    continue
                c5 = tuple(-x for x in sorted(self.score(i) for i in s))
                group = next_group - set(s)
                if group:
                    need = self.min_cost(rest, lambda a, b: 1 if (a in group) != (b in group) else 0)
                    c6 = need if not self.c6_parity else (0 if need == len(group) % 2 else 1)
                else:
                    c6 = 0
                c7 = 0 if last_two else sum(1 for i in s if self.floater_prev(i))
                seq = tuple(self.tpn[i] for i in sorted(s, key=lambda i: (-self.score(i), self.tpn[i])))
                key = (size, c5, c6, c7, seq)
                if best is None or key < best[0]:
                    best = (key, list(s))
            if best is not None:
                break  # C4: the smallest size with a legal set
        return best[1]

    # -- Article 3.6 ------------------------------------------------------

    def pair_cost(self, a, b, ups):
        pa, sa = self.pref(a)
        pb, sb = self.pref(b)
        c8 = 1 if pa and pa == pb else 0
        c9 = 1 if (self.ctype == "B" and pa and pa == pb and sa == 2 and sb == 2) else 0
        c10 = 0
        if self.r < self.total - 1:
            c10 = (1 if a in ups and self.floater_prev(b) else 0) + (1 if b in ups and self.floater_prev(a) else 0)
        return (c8, c9, c10)

    def pair_bracket(self, bracket, ups):
        ids = sorted(bracket, key=lambda i: self.tpn[i])
        n = len(ids)
        base = n + 1

        def cost(a, b):
            c8, c9, c10 = self.pair_cost(a, b, ups)
            return (c8 * base + c9) * base + c10

        opt = self.min_cost(ids, cost)

        def bip_min(tops, bottoms):
            @lru_cache(maxsize=None)
            def g(k, used):
                if k == len(tops):
                    return 0
                best = None
                for j, b in enumerate(bottoms):
                    if used >> j & 1:
                        continue
                    a = tops[k]
                    if self.tpn[b] < self.tpn[a] or not self.legal(a, b):
                        continue
                    sub = g(k + 1, used | (1 << j))
                    if sub is None:
                        continue
                    v = cost(a, b) + sub
                    if best is None or v < best:
                        best = v
                return best
            return g(0, 0)

        for tops in combinations(ids, n // 2):            # identifier order: tops first
            bottoms = tuple(i for i in ids if i not in tops)
            if bip_min(tops, bottoms) != opt:
                continue
            pairs = []
            used = set()
            acc = 0
            for k, a in enumerate(tops):                  # then partners, in order
                for b in bottoms:
                    if b in used or self.tpn[b] < self.tpn[a] or not self.legal(a, b):
                        continue
                    rest_b = tuple(x for x in bottoms if x not in used and x != b)
                    sub = bip_min(tops[k + 1:], rest_b)
                    if sub is not None and acc + cost(a, b) + sub == opt:
                        pairs.append((a, b))
                        used.add(b)
                        acc += cost(a, b)
                        break
            return pairs
        raise AssertionError("no optimal pairing found")

    # -- Article 4 --------------------------------------------------------

    def colours_for(self, a, b):
        """Return (white, black)."""
        def first_key(t):
            sec = self.secondary(t) if self.use_secondary else 0
            return (-self.score(t), -sec, self.tpn[t])

        f, o = sorted((a, b), key=first_key)
        cf, co = self.colours(f), self.colours(o)
        pf, sf = self.pref(f)
        po, so = self.pref(o)

        def res(first_white):
            return (f, o) if first_white else (o, f)

        if not cf and not co:
            return res(self.initial_white if self.tpn[f] % 2 else not self.initial_white)
        if pf and not po:
            return res(pf == "W")
        if po and not pf:
            return res(po == "B")
        if pf and po and pf != po:
            return res(pf == "W")
        if self.ctype == "B" and pf and po and (sf == 2) != (so == 2):
            return res(pf == "W") if sf == 2 else res(po == "B")
        cdf = cf.count("W") - cf.count("B")
        cdo = co.count("W") - co.count("B")
        if cdf != cdo:
            return res(cdf < cdo)
        for k in range(1, min(len(cf), len(co)) + 1):
            if cf[-k] != co[-k]:
                return res(cf[-k] == "B")
        if pf:
            return res(pf == "W")
        if cf:
            return res(cf[-1] == "B")
        return res(co[-1] == "W")

    # -- round ------------------------------------------------------------

    def pair_round(self):
        """Return (list of (white, black), bye) or None if the round cannot be paired legally."""
        unpaired = list(self.active)
        bye = None
        if len(unpaired) % 2:
            bye = self.choose_bye(unpaired)
            if bye is None:
                return None
            unpaired.remove(bye)
        if not self.matchable(unpaired):
            return None
        pairs = []
        while unpaired:
            top_score = max(self.score(i) for i in unpaired)
            top = [i for i in unpaired if self.score(i) == top_score]
            ups = self.choose_upfloaters(unpaired, top)
            bracket = set(top) | set(ups)
            pairs.extend(self.pair_bracket(bracket, set(ups)))
            unpaired = [i for i in unpaired if i not in bracket]
        return [self.colours_for(a, b) for a, b in pairs], bye


def reference_pairing(teams, round_number, total_rounds, **opt):
    return Ref(teams, round_number, total_rounds, **opt).pair_round()
