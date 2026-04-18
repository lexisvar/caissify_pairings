"""
FIDE Dutch System Pairing Engine (C.04.3)

Implements the FIDE Dutch System for Swiss tournaments as specified in
FIDE Handbook C.04.3 (effective from 1 February 2026).

Reference:
- C.04.1: Basic rules for Swiss Systems
- C.04.3: FIDE (Dutch) System
- C.04.A: Software Endorsement requirements
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict

from caissify_pairings.base import BasePairingEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ColorPref(Enum):
    """Player colour preference strength (C.04.3 §E)."""
    WHITE = "white"
    BLACK = "black"
    NONE = "none"


class FloatDir(Enum):
    """Float direction for a player within a round."""
    UP = "up"
    DOWN = "down"
    NONE = "none"


# ---------------------------------------------------------------------------
# Player Data Class
# ---------------------------------------------------------------------------


@dataclass
class DutchPlayer:
    """
    Internal representation of a tournament player for the Dutch engine.

    Attributes populated from the input dict and enriched with derived state.
    """
    id: int
    name: str
    score: float
    rating: int
    pairing_number: int  # Assigned during initial ordering (1-based)
    starting_number: int
    color_hist: list = field(default_factory=list)
    float_hist: list = field(default_factory=list)  # List of FloatDir per round
    bye_count: int = 0
    opponents: set = field(default_factory=set)  # IDs of previous opponents

    # --- Derived properties ---

    @property
    def color_diff(self) -> int:
        """Number of whites minus number of blacks played."""
        return self.color_hist.count("white") - self.color_hist.count("black")

    @property
    def last_color(self) -> Optional[str]:
        """Color played in the most recent round, or None."""
        return self.color_hist[-1] if self.color_hist else None

    @property
    def color_preference(self) -> ColorPref:
        """
        Determine colour preference per C.04.3 §E.

        Priority:
        1. Absolute: avoid 3 same in a row → must get opposite
        2. Strong: colour diff is ±2 → must equalise
        3. Mild: alternate from last colour
        4. None: no history
        """
        if len(self.color_hist) == 0:
            return ColorPref.NONE

        # Absolute: would be 3rd same colour in a row
        if len(self.color_hist) >= 2 and self.color_hist[-1] == self.color_hist[-2]:
            return ColorPref.WHITE if self.color_hist[-1] == "black" else ColorPref.BLACK

        # Strong: colour difference is ±2
        diff = self.color_diff
        if diff >= 2:
            return ColorPref.BLACK
        if diff <= -2:
            return ColorPref.WHITE

        # Mild: alternate from last colour
        if self.last_color == "white":
            return ColorPref.BLACK
        if self.last_color == "black":
            return ColorPref.WHITE

        return ColorPref.NONE

    @property
    def preference_strength(self) -> int:
        """
        Numeric strength of colour preference (higher = stronger).
        3 = absolute (avoid 3x streak), 2 = strong (±2 balance), 1 = mild (alternate), 0 = none.
        """
        if len(self.color_hist) == 0:
            return 0
        if len(self.color_hist) >= 2 and self.color_hist[-1] == self.color_hist[-2]:
            return 3  # Absolute
        diff = self.color_diff
        if abs(diff) >= 2:
            return 2  # Strong
        if self.last_color is not None:
            return 1  # Mild
        return 0

    @property
    def had_bye(self) -> bool:
        return self.bye_count > 0

    @property
    def last_float(self) -> FloatDir:
        return self.float_hist[-1] if self.float_hist else FloatDir.NONE

    def would_violate_absolute_color(self, color: str) -> bool:
        """Check if assigning this color would violate absolute criteria."""
        # 3 same colours in a row
        if len(self.color_hist) >= 2:
            if self.color_hist[-1] == self.color_hist[-2] == color:
                return True
        # Colour diff would exceed ±2
        new_diff = self.color_diff + (1 if color == "white" else -1)
        if abs(new_diff) > 2:
            return True
        return False


# ---------------------------------------------------------------------------
# Dutch Engine
# ---------------------------------------------------------------------------


class DutchEngine(BasePairingEngine):
    """
    FIDE Dutch System pairing engine (C.04.3).

    Implements the deterministic Dutch System algorithm with:
    - Scoregroup-based pairing with S1/S2 splitting
    - Systematic transpositions and exchanges
    - Absolute and relative criteria separation
    - FIDE-compliant color allocation
    - Float tracking and limits
    - Quality metric optimization
    - Last round relaxation
    """

    name = "dutch"

    # Maximum transpositions to attempt before giving up on a scoregroup.
    # Prevents combinatorial explosion in large groups.
    MAX_TRANSPOSITIONS = 5000

    def __init__(
        self,
        players: List[dict],
        previous_pairings: Set[Tuple[int, int]],
        round_number: int,
        total_rounds: int,
        bye_value: float = 1.0,
        max_byes_per_player: int = 1,
        **kwargs,
    ):
        super().__init__(
            players=players,
            previous_pairings=previous_pairings,
            round_number=round_number,
            total_rounds=total_rounds,
            **kwargs,
        )
        self.bye_value = bye_value
        self.max_byes_per_player = max_byes_per_player

        # Build internal DutchPlayer objects and assign pairing numbers
        self._players: List[DutchPlayer] = self._build_players(players)
        self._player_map: Dict[int, DutchPlayer] = {p.id: p for p in self._players}

    # ------------------------------------------------------------------
    # Phase 1.1 — Initial ordering & data structures (C.04.3 §A1-A3)
    # ------------------------------------------------------------------

    def _build_players(self, raw: List[dict]) -> List[DutchPlayer]:
        """
        Build DutchPlayer objects and assign *pairing numbers*.

        Pairing numbers are assigned once (before round 1) and remain fixed.
        Ordering: higher rating → higher title → lower starting_number → alphabetical.
        """
        title_priority = {
            "GM": 9, "IM": 8, "FM": 7, "CM": 6,
            "WGM": 5, "WIM": 4, "WFM": 3, "WCM": 2,
        }

        def sort_key(p: dict):
            return (
                -p.get("rating", 0),
                -title_priority.get(p.get("title", ""), 0),
                p.get("starting_number", 999),
                p.get("name", "").lower(),
            )

        sorted_raw = sorted(raw, key=sort_key)

        players: List[DutchPlayer] = []
        for idx, p in enumerate(sorted_raw, start=1):
            pid = p["id"]
            opponents = set()
            for a, b in self.previous_pairings:
                if a == pid:
                    opponents.add(b)
                elif b == pid:
                    opponents.add(a)

            # Use existing pairing_number if provided (persisted from round 1),
            # otherwise assign from sorting order.
            pairing_number = p.get("pairing_number", idx)

            players.append(DutchPlayer(
                id=pid,
                name=p.get("name", ""),
                score=p.get("score", 0.0),
                rating=p.get("rating", 0),
                pairing_number=pairing_number,
                starting_number=p.get("starting_number", idx),
                color_hist=list(p.get("color_hist", [])),
                float_hist=[FloatDir(f) if isinstance(f, str) else f
                            for f in p.get("float_history", [])],
                bye_count=p.get("bye_count", 0),
                opponents=opponents,
            ))
        return players

    def _build_scoregroups(self, eligible: List[DutchPlayer]) -> List[List[DutchPlayer]]:
        """
        Divide players into scoregroups sorted by descending score.
        Within each scoregroup, players are sorted by ascending pairing number.
        """
        groups: Dict[float, List[DutchPlayer]] = defaultdict(list)
        for p in eligible:
            groups[p.score].append(p)

        result = []
        for score in sorted(groups.keys(), reverse=True):
            group = sorted(groups[score], key=lambda p: p.pairing_number)
            result.append(group)
        return result

    # ------------------------------------------------------------------
    # Phase 1.2 — Absolute & relative criteria (C.04.3 §B1-B6)
    # ------------------------------------------------------------------

    def _can_pair(self, p1: DutchPlayer, p2: DutchPlayer) -> bool:
        """
        Check *absolute* criteria — if False, this pairing is forbidden.

        B1: No repeat opponents
        B5/B6: Colour constraints (unless last-round relaxation applies)
        """
        # B1 — no repeat
        if p2.id in p1.opponents:
            return False

        # B5/B6 — colour constraints
        if not self._is_last_round:
            if not self._has_legal_color_assignment(p1, p2):
                return False

        return True

    def _has_legal_color_assignment(self, p1: DutchPlayer, p2: DutchPlayer) -> bool:
        """Check that at least one colour assignment satisfies absolute colour rules."""
        ok1 = (not p1.would_violate_absolute_color("white")
               and not p2.would_violate_absolute_color("black"))
        ok2 = (not p1.would_violate_absolute_color("black")
               and not p2.would_violate_absolute_color("white"))
        return ok1 or ok2

    # ------------------------------------------------------------------
    # Phase 1.9 — Bye assignment (C.04.1 §3-4)
    # ------------------------------------------------------------------

    def _select_bye_player(self, players: List[DutchPlayer]) -> Optional[DutchPlayer]:
        """
        Select the player to receive the pairing-allocated bye.

        Rules:
        - Lowest scoregroup first
        - Within scoregroup, highest pairing number (lowest rated) gets bye
        - Must not have already received a bye (up to max_byes_per_player)
        - Candidate must leave remaining players fully pairable
        """
        candidates = [
            p for p in players
            if p.bye_count < self.max_byes_per_player
        ]
        if not candidates:
            candidates = list(players)

        # Sort: lowest score first, then highest pairing number (weakest)
        candidates.sort(key=lambda p: (p.score, -p.pairing_number))

        # Verify each candidate leaves a pairable group (for late rounds
        # in small tournaments where the choice of bye player matters).
        if self.round_number > 1 and len(players) <= 30:
            for candidate in candidates:
                remaining = [p for p in players if p.id != candidate.id]
                match = self._backtrack_match(remaining)
                if match is not None:
                    return candidate

            # No candidate with bye_count < max allows full pairing.
            # Expand search to ALL players (allow double byes if necessary).
            all_sorted = sorted(
                players,
                key=lambda p: (p.bye_count, p.score, -p.pairing_number),
            )
            for candidate in all_sorted:
                remaining = [p for p in players if p.id != candidate.id]
                match = self._backtrack_match(remaining)
                if match is not None:
                    return candidate

        return candidates[0] if candidates else None

    # ------------------------------------------------------------------
    # Phase 1.8 — Color allocation (C.04.3 §E1-E6)
    # ------------------------------------------------------------------

    def _assign_colors(
        self, p1: DutchPlayer, p2: DutchPlayer
    ) -> Tuple[DutchPlayer, DutchPlayer]:
        """
        Assign white/black per C.04.3 §E colour allocation rules.

        Returns (white_player, black_player).
        """
        pref1 = p1.color_preference
        pref2 = p2.color_preference
        str1 = p1.preference_strength
        str2 = p2.preference_strength

        # E1 — grant the colour to whoever has a stronger preference
        if str1 > str2:
            if pref1 == ColorPref.WHITE:
                return p1, p2
            elif pref1 == ColorPref.BLACK:
                return p2, p1
        elif str2 > str1:
            if pref2 == ColorPref.WHITE:
                return p2, p1
            elif pref2 == ColorPref.BLACK:
                return p1, p2

        # Equal preference strength — both want same colour?
        if pref1 == pref2 and pref1 != ColorPref.NONE:
            if pref1 == ColorPref.WHITE:
                if p1.color_diff < p2.color_diff:
                    return p1, p2
                elif p2.color_diff < p1.color_diff:
                    return p2, p1
                else:
                    return (p1, p2) if p1.pairing_number < p2.pairing_number else (p2, p1)
            else:  # Both want black
                if p1.color_diff > p2.color_diff:
                    return p2, p1
                elif p2.color_diff > p1.color_diff:
                    return p1, p2
                else:
                    return (p1, p2) if p1.pairing_number < p2.pairing_number else (p2, p1)

        # Compatible preferences
        if pref1 == ColorPref.WHITE and pref2 != ColorPref.WHITE:
            return p1, p2
        if pref2 == ColorPref.WHITE and pref1 != ColorPref.WHITE:
            return p2, p1
        if pref1 == ColorPref.BLACK and pref2 != ColorPref.BLACK:
            return p2, p1
        if pref2 == ColorPref.BLACK and pref1 != ColorPref.BLACK:
            return p1, p2

        # E3 — equalise colour balance
        if p1.color_diff < p2.color_diff:
            return p1, p2
        elif p2.color_diff < p1.color_diff:
            return p2, p1

        # E4 — higher ranked (lower pairing number) gets white
        if p1.pairing_number < p2.pairing_number:
            return p1, p2
        return p2, p1

    # ------------------------------------------------------------------
    # Phase 1.3 — S1/S2 splitting (C.04.3 §C1-C4)
    # ------------------------------------------------------------------

    @staticmethod
    def _split_scoregroup(
        group: List[DutchPlayer],
    ) -> Tuple[List[DutchPlayer], List[DutchPlayer]]:
        """
        Split a scoregroup into S1 (top half) and S2 (bottom half).

        If the group has an odd number of players, S2 gets the extra player.
        Players within each half are sorted by ascending pairing number.
        """
        n = len(group)
        half = n // 2
        s1 = group[:half]
        s2 = group[half:]
        return s1, s2

    # ------------------------------------------------------------------
    # Phase 1.4 — Transpositions (C.04.3 §C5-C8)
    # ------------------------------------------------------------------

    def _generate_transpositions(
        self, s2: List[DutchPlayer], count: int
    ) -> List[List[DutchPlayer]]:
        """
        Generate permutations of S2 in lexicographic order of pairing numbers.

        Limited to MAX_TRANSPOSITIONS to prevent combinatorial explosion.
        """
        if count <= 0:
            return [[]]
        if count >= len(s2):
            perms = itertools.permutations(s2)
        else:
            perms = itertools.chain.from_iterable(
                itertools.permutations(combo)
                for combo in itertools.combinations(s2, count)
            )

        result = []
        for perm in perms:
            result.append(list(perm))
            if len(result) >= self.MAX_TRANSPOSITIONS:
                break

        # Sort lexicographically by pairing number tuple
        result.sort(key=lambda perm: tuple(p.pairing_number for p in perm))
        return result

    # ------------------------------------------------------------------
    # Phase 1.5 — Exchanges (C.04.3 §C9-C12)
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_exchanges(
        s1: List[DutchPlayer], s2: List[DutchPlayer]
    ) -> List[Tuple[List[DutchPlayer], List[DutchPlayer]]]:
        """
        Generate all single-player exchanges between S1 and S2.

        Ordered by minimising the difference in pairing numbers of the
        swapped pair (closest swaps first).
        """
        exchanges = []
        for i, p1 in enumerate(s1):
            for j, p2 in enumerate(s2):
                new_s1 = list(s1)
                new_s2 = list(s2)
                new_s1[i] = p2
                new_s2[j] = p1
                new_s1.sort(key=lambda p: p.pairing_number)
                new_s2.sort(key=lambda p: p.pairing_number)
                diff = abs(p1.pairing_number - p2.pairing_number)
                exchanges.append((diff, new_s1, new_s2))

        exchanges.sort(key=lambda x: x[0])
        return [(s1_new, s2_new) for _, s1_new, s2_new in exchanges]

    # ------------------------------------------------------------------
    # Phase 1.10 — Quality metric (C.04.3 §C13-C14)
    # ------------------------------------------------------------------

    @staticmethod
    def _pairing_quality(pairings: List[Tuple[DutchPlayer, DutchPlayer]]) -> float:
        """
        Pairing quality = sum of |score difference| for each pair.
        Lower is better.
        """
        return sum(abs(p1.score - p2.score) for p1, p2 in pairings)

    @staticmethod
    def _colour_violations(pairings: List[Tuple[DutchPlayer, DutchPlayer]]) -> int:
        """
        Count the number of unsatisfied colour preferences (C.04.3 C6).

        For each pair, check if there exists a colour assignment that
        satisfies both players' colour preferences.  If not, count the
        pair as 1 violation (one of the two will not get their preference).
        """
        violations = 0
        for p1, p2 in pairings:
            pref1 = p1.color_preference
            pref2 = p2.color_preference
            if pref1 == ColorPref.NONE or pref2 == ColorPref.NONE:
                continue  # at least one has no preference → satisfied
            if pref1 != pref2:
                continue  # different preferences → both satisfied
            # Both want the same colour → one will be violated
            violations += 1
        return violations

    # ------------------------------------------------------------------
    # Core pairing logic for one scoregroup
    # ------------------------------------------------------------------

    def _try_pair_s1_s2(
        self, s1: List[DutchPlayer], s2_perm: List[DutchPlayer]
    ) -> Optional[List[Tuple[DutchPlayer, DutchPlayer]]]:
        """
        Try to pair S1 vs the given S2 permutation.
        Returns list of (p1, p2) tuples if all valid, else None.
        """
        if len(s2_perm) < len(s1):
            return None
        pairs = []
        for i, p1 in enumerate(s1):
            p2 = s2_perm[i]
            if not self._can_pair(p1, p2):
                return None
            pairs.append((p1, p2))
        return pairs

    def _pair_scoregroup(
        self, group: List[DutchPlayer]
    ) -> Tuple[List[Tuple[DutchPlayer, DutchPlayer]], List[DutchPlayer]]:
        """
        Pair a homogeneous scoregroup using the Dutch method (C.04.3 §B/C).

        Steps:
        1. Split into S1 and S2
        2. Try default S1[i] vs S2[i]
        3. Try all transpositions of S2 (pick one with best colour quality)
        4. Try all exchanges between S1 and S2 with transpositions
        5. Return (pairs, remainder)
        """
        if len(group) == 0:
            return [], []
        if len(group) == 1:
            return [], list(group)

        s1, s2 = self._split_scoregroup(group)

        if len(s1) == 0:
            return [], list(group)

        # --- Step 1+2: Try default pairing and transpositions of S2,
        #     pick the candidate with lowest colour violations (C6),
        #     breaking ties by transposition order (lexicographic = default
        #     first). ---
        best_pairs = None
        best_cv = float("inf")
        best_quality = float("inf")
        best_remainder: List[DutchPlayer] = []

        for s2_perm in self._generate_transpositions(s2, len(s1)):
            result = self._try_pair_s1_s2(s1, s2_perm)
            if result is not None:
                cv = self._colour_violations(result)
                q = self._pairing_quality(result)
                used = set(id(p) for p in s2_perm[:len(s1)])
                remainder = [p for p in s2 if id(p) not in used]
                if cv < best_cv or (cv == best_cv and q < best_quality):
                    best_cv = cv
                    best_quality = q
                    best_pairs = result
                    best_remainder = remainder
                if cv == 0 and q == 0:
                    break  # Perfect pairing, no need to continue

        if best_pairs is not None:
            return best_pairs, best_remainder

        # --- Step 3: Try exchanges ---
        best_cv = float("inf")
        best_quality = float("inf")

        for new_s1, new_s2 in self._generate_exchanges(s1, s2):
            for s2_perm in self._generate_transpositions(new_s2, len(new_s1)):
                result = self._try_pair_s1_s2(new_s1, s2_perm)
                if result is not None:
                    cv = self._colour_violations(result)
                    q = self._pairing_quality(result)
                    used = set(id(p) for p in s2_perm[:len(new_s1)])
                    remainder = [p for p in new_s2 if id(p) not in used]
                    if cv < best_cv or (cv == best_cv and q < best_quality):
                        best_cv = cv
                        best_quality = q
                        best_pairs = result
                        best_remainder = remainder
                    break  # Take first valid transposition for this exchange

        if best_pairs is not None:
            return best_pairs, best_remainder

        # --- Step 4: Cannot pair via structured Dutch → try backtracking ---
        bt_pairs = self._backtrack_match(group)
        if bt_pairs:
            paired_ids = set()
            for a, b in bt_pairs:
                paired_ids.add(a.id)
                paired_ids.add(b.id)
            bt_remainder = [p for p in group if p.id not in paired_ids]
            return bt_pairs, bt_remainder

        # --- Step 5: Truly cannot pair → all become remainder ---
        return [], list(group)

    def _pair_heterogeneous_bracket(
        self,
        mdps: List[DutchPlayer],
        residents: List[DutchPlayer],
    ) -> Tuple[List[Tuple[DutchPlayer, DutchPlayer]], List[DutchPlayer]]:
        """
        Pair a heterogeneous bracket (C.04.3 §B.2-B.3, B.7).

        A heterogeneous bracket contains MDPs (moved-down players from the
        previous bracket) and resident players.  The pairing proceeds in
        two phases:

        1. MDP-Pairing: S1 = MDPs (highest first), S2 = residents.
           Pair each MDP against a resident using transpositions/exchanges.
        2. Remainder: the unpaired residents form a new homogeneous bracket
           and are paired using ``_pair_scoregroup``.
        """
        all_pairs: List[Tuple[DutchPlayer, DutchPlayer]] = []

        # Sort MDPs by pairing order (highest rank first = lowest PN)
        mdps_sorted = sorted(mdps, key=lambda p: p.pairing_number)
        residents_sorted = sorted(residents, key=lambda p: p.pairing_number)

        m1 = min(len(mdps_sorted), len(residents_sorted))
        if m1 == 0:
            # No MDPs can be paired — all go to remainder
            all_remainder = mdps_sorted + residents_sorted
            all_remainder.sort(key=lambda p: (-p.score, p.pairing_number))
            return self._pair_scoregroup(all_remainder)

        s1 = mdps_sorted[:m1]
        limbo = mdps_sorted[m1:]  # MDPs that can't be paired (double-float)
        s2 = residents_sorted

        # --- Phase 1: MDP-Pairing ---
        mdp_pairs, mdp_remainder = self._pair_mdp_phase(s1, s2)
        all_pairs.extend(mdp_pairs)

        # Unpaired residents after MDP pairing
        paired_resident_ids = set()
        for _, r in mdp_pairs:
            paired_resident_ids.add(r.id)
        unpaired_residents = [p for p in residents_sorted
                              if p.id not in paired_resident_ids]

        # --- Phase 2: Remainder (homogeneous pairing of leftover residents) ---
        if unpaired_residents:
            rem_pairs, rem_remainder = self._pair_scoregroup(unpaired_residents)
            all_pairs.extend(rem_pairs)
        else:
            rem_remainder = []

        # Final remainder = limbo MDPs + unpaired MDPs + unpaired residents
        final_remainder = limbo + mdp_remainder + rem_remainder
        final_remainder.sort(key=lambda p: (-p.score, p.pairing_number))

        return all_pairs, final_remainder

    def _pair_mdp_phase(
        self,
        s1: List[DutchPlayer],
        s2: List[DutchPlayer],
    ) -> Tuple[List[Tuple[DutchPlayer, DutchPlayer]], List[DutchPlayer]]:
        """
        Try to pair MDPs (S1) against residents (S2).

        Uses the same transposition/exchange logic as homogeneous pairing
        but S1 = MDPs, S2 = all residents.
        Returns (pairs, unpaired_mdps).
        """
        if not s1 or not s2:
            return [], list(s1)

        # --- Try all transpositions of S2, pick best colour quality ---
        best_pairs = None
        best_cv = float("inf")
        best_quality = float("inf")

        for s2_perm in self._generate_transpositions(s2, len(s1)):
            result = self._try_pair_s1_s2(s1, s2_perm)
            if result is not None:
                cv = self._colour_violations(result)
                q = self._pairing_quality(result)
                if cv < best_cv or (cv == best_cv and q < best_quality):
                    best_cv = cv
                    best_quality = q
                    best_pairs = result
                if cv == 0 and q == 0:
                    break

        if best_pairs is not None:
            return best_pairs, []

        # --- Try reducing M1 (pair fewer MDPs) ---
        for reduce in range(1, len(s1)):
            fewer_s1 = s1[:len(s1) - reduce]
            for s2_perm in self._generate_transpositions(s2, len(fewer_s1)):
                result = self._try_pair_s1_s2(fewer_s1, s2_perm)
                if result is not None:
                    unpaired = s1[len(s1) - reduce:]
                    return result, unpaired

        return [], list(s1)

    def _backtrack_match(
        self, players: List[DutchPlayer], relaxed: bool = False,
    ) -> Optional[List[Tuple[DutchPlayer, DutchPlayer]]]:
        """
        Last-resort backtracking matcher.

        Tries to find a complete matching where all absolute criteria hold.
        Used when the structured S1/S2 + transpositions + exchanges fail.

        If *relaxed* is True, only B1 (no repeat) is checked — colour
        constraints are dropped.  This mirrors the FIDE progressive
        relaxation rule: colour rules yield when the alternative is an
        illegal pairing (forced bye for someone who should play).
        """
        n = len(players)
        if n < 2:
            return None

        pairs: List[Tuple[DutchPlayer, DutchPlayer]] = []
        used = [False] * n

        def can_pair(p1: DutchPlayer, p2: DutchPlayer) -> bool:
            if relaxed:
                return p2.id not in p1.opponents
            return self._can_pair(p1, p2)

        def backtrack() -> bool:
            # Find first unused player
            first = -1
            for i in range(n):
                if not used[i]:
                    first = i
                    break
            if first == -1:
                return True  # Everyone paired
            # Only 1 left unpaired — acceptable if odd group
            remaining = sum(1 for u in used if not u)
            if remaining == 1:
                return True

            used[first] = True
            for j in range(first + 1, n):
                if used[j]:
                    continue
                if can_pair(players[first], players[j]):
                    used[j] = True
                    pairs.append((players[first], players[j]))
                    if backtrack():
                        return True
                    pairs.pop()
                    used[j] = False
            used[first] = False
            return False

        if backtrack():
            return pairs
        return None

    def _greedy_match(
        self, players: List[DutchPlayer], relaxed: bool = False,
    ) -> Optional[List[Tuple[DutchPlayer, DutchPlayer]]]:
        """
        Greedy maximum-effort matcher — O(n²).

        Iterates players in order and pairs each with the first available
        legal partner.  Does not guarantee a true maximum matching, but
        is fast, deterministic, and sufficient for the fallback case where
        the opponent graph prevents a complete matching (e.g. two-triangle
        degeneration in small round-robin-like tournaments).
        """
        pairs: List[Tuple[DutchPlayer, DutchPlayer]] = []
        used: set = set()

        for i, p1 in enumerate(players):
            if p1.id in used:
                continue
            for j in range(i + 1, len(players)):
                p2 = players[j]
                if p2.id in used:
                    continue
                if relaxed:
                    ok = p2.id not in p1.opponents
                else:
                    ok = self._can_pair(p1, p2)
                if ok:
                    pairs.append((p1, p2))
                    used.add(p1.id)
                    used.add(p2.id)
                    break

        return pairs if pairs else None

    # ------------------------------------------------------------------
    # Phase 1.7 — Float management (C.04.3 §A5-A7)
    # ------------------------------------------------------------------

    def _record_floats(
        self,
        paired: List[Tuple[DutchPlayer, DutchPlayer]],
        scoregroup_score: float,
    ):
        """Record float directions for players paired across scoregroups."""
        for p1, p2 in paired:
            if p1.score > scoregroup_score:
                p1.float_hist.append(FloatDir.DOWN)
            elif p1.score < scoregroup_score:
                p1.float_hist.append(FloatDir.UP)
            if p2.score > scoregroup_score:
                p2.float_hist.append(FloatDir.DOWN)
            elif p2.score < scoregroup_score:
                p2.float_hist.append(FloatDir.UP)

    # ------------------------------------------------------------------
    # Phase 1.11 — Last round relaxation (C.04.1 §6-7)
    # ------------------------------------------------------------------

    @property
    def _is_last_round(self) -> bool:
        """Check if this is the final round (relaxes colour absolute criteria)."""
        return self.round_number >= self.total_rounds

    # ------------------------------------------------------------------
    # First round (special case)
    # ------------------------------------------------------------------

    def _generate_first_round_pairings(
        self, eligible: List[DutchPlayer]
    ) -> List[Tuple[DutchPlayer, DutchPlayer]]:
        """
        Round 1: split sorted list into top/bottom half, pair in order.
        S1[0] vs S2[0], S1[1] vs S2[1], etc.
        """
        n = len(eligible)
        half = n // 2
        s1 = eligible[:half]
        s2 = eligible[half:]
        pairs = []
        for i in range(len(s1)):
            pairs.append((s1[i], s2[i]))
        return pairs

    # ------------------------------------------------------------------
    # Main entry: generate_pairings
    # ------------------------------------------------------------------

    def generate_pairings(self) -> List[dict]:
        """
        Generate FIDE Dutch System pairings for the current round.

        Algorithm:
        1. Assign bye if odd number of players
        2. Build scoregroups
        3. For each scoregroup (top → bottom):
           a. Merge in remainder from previous group
           b. Split into S1/S2
           c. Try transpositions then exchanges
           d. Unpaired players become remainder for next group
        4. Assign colours
        5. Assign table numbers
        """
        all_players = list(self._players)
        output: List[dict] = []

        # --- Step 1: Bye assignment for odd player count ---
        bye_player: Optional[DutchPlayer] = None
        if len(all_players) % 2 == 1:
            bye_player = self._select_bye_player(all_players)
            if bye_player:
                all_players = [p for p in all_players if p.id != bye_player.id]

        # --- Step 2: Build paired output ---
        if self.round_number == 1:
            eligible = sorted(all_players, key=lambda p: p.pairing_number)
            paired = self._generate_first_round_pairings(eligible)
            remainder: List[DutchPlayer] = []
            if len(eligible) % 2 == 1:
                remainder = [eligible[-1]]
        else:
            scoregroups = self._build_scoregroups(all_players)
            paired: List[Tuple[DutchPlayer, DutchPlayer]] = []
            remainder: List[DutchPlayer] = []

            for sg in scoregroups:
                sg_score = sg[0].score if sg else 0.0

                if remainder:
                    # Heterogeneous bracket: MDPs + residents
                    sg_pairs, remainder = self._pair_heterogeneous_bracket(
                        remainder, list(sg)
                    )
                else:
                    sg_pairs, remainder = self._pair_scoregroup(list(sg))

                paired.extend(sg_pairs)
                self._record_floats(sg_pairs, sg_score)

            # Handle final remainder — try backtracking on all unpaired
            if len(remainder) >= 2:
                bt_pairs = self._backtrack_match(remainder)
                if bt_pairs:
                    paired.extend(bt_pairs)
                    paired_ids = set()
                    for a, b in bt_pairs:
                        paired_ids.add(a.id)
                        paired_ids.add(b.id)
                    remainder = [p for p in remainder if p.id not in paired_ids]

            # If there are STILL unpaired players (besides 0–1 remainder),
            # the greedy scoregroup approach failed — fall back to global
            # backtracking on ALL players to find a complete valid matching.
            if len(remainder) >= 2:
                logger.info(
                    "Round %d: scoregroup pairing left %d unpaired, "
                    "retrying with global backtracking",
                    self.round_number, len(remainder),
                )
                global_pairs = self._backtrack_match(all_players)
                if global_pairs:
                    paired = global_pairs
                    paired_ids = set()
                    for a, b in global_pairs:
                        paired_ids.add(a.id)
                        paired_ids.add(b.id)
                    remainder = [p for p in all_players if p.id not in paired_ids]

            # Final greedy fallback: when no complete matching exists
            # (e.g. two-triangle degeneration in small round-robin-like
            # tournaments), find as many valid pairs as possible.
            # Uses strict colour constraints first; relaxes only if needed.
            if len(remainder) >= 2:
                logger.info(
                    "Round %d: no complete matching possible for %d players, "
                    "using greedy maximum-effort matching",
                    self.round_number, len(remainder),
                )
                greedy = self._greedy_match(all_players)
                if greedy is None:
                    greedy = self._greedy_match(all_players, relaxed=True)
                if greedy:
                    paired = greedy
                    paired_ids = set()
                    for a, b in greedy:
                        paired_ids.add(a.id)
                        paired_ids.add(b.id)
                    remainder = [p for p in all_players if p.id not in paired_ids]

            # Assign bye to remaining unpaired player (at most 1 for even count)
            for p in remainder:
                if bye_player is None:
                    bye_player = p
                else:
                    logger.warning(
                        "Multiple forced byes in round %d — players %s, %s",
                        self.round_number, bye_player.id, p.id,
                    )
                    output.append({
                        "white_id": p.id,
                        "black_id": None,
                        "table": 0,
                        "bye": True,
                        "bye_type": "U",
                    })

        # --- Step 3: Assign colours and build output ---
        for p1, p2 in paired:
            white, black = self._assign_colors(p1, p2)
            output.append({
                "white_id": white.id,
                "black_id": black.id,
                "table": 0,
            })

        # --- Step 4: Bye pairing ---
        if bye_player:
            output.append({
                "white_id": bye_player.id,
                "black_id": None,
                "table": 0,
                "bye": True,
                "bye_type": "U",
            })

        # --- Step 5: Assign table numbers ---
        output = self._assign_table_numbers(output)

        return output

    # ------------------------------------------------------------------
    # Table number assignment
    # ------------------------------------------------------------------

    def _assign_table_numbers(self, pairings: List[dict]) -> List[dict]:
        """
        Assign table numbers: regular games first (highest score/rating pair),
        then byes at the end.
        """
        regular = [p for p in pairings if not p.get("bye")]
        byes = [p for p in pairings if p.get("bye")]

        def pairing_sort_key(p: dict) -> tuple:
            w = self._player_map.get(p["white_id"])
            b = self._player_map.get(p["black_id"]) if p.get("black_id") else None
            best_score = max(
                w.score if w else 0,
                b.score if b else 0,
            )
            best_rating = max(
                w.rating if w else 0,
                b.rating if b else 0,
            )
            best_pn = min(
                w.pairing_number if w else 999,
                b.pairing_number if b else 999,
            )
            return (-best_score, -best_rating, best_pn)

        regular.sort(key=pairing_sort_key)
        byes.sort(key=pairing_sort_key)

        table = 1
        for p in regular:
            p["table"] = table
            table += 1
        for p in byes:
            p["table"] = table
            table += 1

        return regular + byes


# ---------------------------------------------------------------------------
# Public entry point (convenience function)
# ---------------------------------------------------------------------------


def dutch_pairings(
    players: List[dict],
    previous_pairings: Set[Tuple[int, int]],
    round_number: int,
    total_rounds: int,
    bye_value: float = 1.0,
    max_byes_per_player: int = 1,
) -> List[dict]:
    """
    FIDE Dutch System pairing entry point.

    Convenience wrapper around DutchEngine for direct use.
    """
    engine = DutchEngine(
        players=players,
        previous_pairings=previous_pairings,
        round_number=round_number,
        total_rounds=total_rounds,
        bye_value=bye_value,
        max_byes_per_player=max_byes_per_player,
    )
    return engine.generate_pairings()


# Engine registry alias — engines/__init__.py looks for `Engine`
Engine = DutchEngine
