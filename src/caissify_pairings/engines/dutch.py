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

        # Absolute / Strong: equalise when colour difference is non-zero.
        # bbpPairings: |diff| >= 2 → absolute, |diff| == 1 → strong.
        # In both cases the preferred colour is the less-played one.
        diff = self.color_diff
        if diff >= 1:
            return ColorPref.BLACK
        if diff <= -1:
            return ColorPref.WHITE

        # Mild: alternate from last colour (diff == 0)
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
        # Absolute: colour imbalance ≥ 2 OR 2+ same colour in a row
        diff = self.color_diff
        if abs(diff) >= 2:
            return 3  # Absolute (imbalance)
        if len(self.color_hist) >= 2 and self.color_hist[-1] == self.color_hist[-2]:
            return 3  # Absolute (consecutive)
        # Strong: colour imbalance of exactly 1
        if abs(diff) == 1:
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

    # Maximum joint MDP+remainder evaluations in heterogeneous brackets.
    # Each evaluation runs _pair_scoregroup on the remainder, so this
    # controls the cost of the two-phase joint optimisation.
    MAX_JOINT_EVALS = 200

    def __init__(
        self,
        players: List[dict],
        previous_pairings: Set[Tuple[int, int]],
        round_number: int,
        total_rounds: int,
        bye_value: float = 1.0,
        max_byes_per_player: int = 1,
        initial_color: str = "white",
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
        self.initial_color = initial_color  # C.04.3 §E: "Initial-colour"

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
        B5/B6: Colour constraints (unless last-round relaxation applies
               for top scorers — matching bbpPairings' compatible())
        """
        # Self-pairing is always forbidden
        if p1.id == p2.id:
            return False

        # B1 — no repeat
        if p2.id in p1.opponents:
            return False

        # B5/B6 — colour constraints
        if not self._has_legal_color_assignment(p1, p2):
            # Last-round relaxation: only for top scorers per bbpPairings.
            # Top scorer threshold = (rounds_played * pointsForWin) / 2.
            if self._is_last_round:
                rounds_played = self.round_number - 1
                top_threshold = rounds_played / 2.0
                if p1.score > top_threshold or p2.score > top_threshold:
                    return True
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

        E.1  Grant both colour preferences (if compatible).
        E.2  Grant the stronger colour preference.
             If both are absolute (topscorers), grant the wider colour diff.
        E.3  Alternate colours to the most recent time one had white
             and the other had black.
        E.4  Grant the colour preference of the higher ranked player.
        E.5  If the higher ranked player has an odd pairing number,
             give him the initial-colour; otherwise give him the opposite.

        Returns (white_player, black_player).
        """
        pref1 = p1.color_preference
        pref2 = p2.color_preference
        str1 = p1.preference_strength
        str2 = p2.preference_strength

        # Determine who is "higher ranked" per A.2 (lower pairing number).
        if p1.pairing_number < p2.pairing_number:
            higher, lower = p1, p2
        else:
            higher, lower = p2, p1

        # --- E.1 — Grant both colour preferences (if compatible) ---
        if pref1 != ColorPref.NONE and pref2 != ColorPref.NONE and pref1 != pref2:
            # Compatible: each gets what they want.
            if pref1 == ColorPref.WHITE:
                return p1, p2
            else:
                return p2, p1

        # --- E.2 — Grant the stronger colour preference ---
        if str1 != str2:
            stronger = p1 if str1 > str2 else p2
            pref = stronger.color_preference
            if pref == ColorPref.WHITE:
                return (stronger, p2 if stronger is p1 else p1)
            elif pref == ColorPref.BLACK:
                other = p2 if stronger is p1 else p1
                return other, stronger
        elif str1 > 0 and pref1 == pref2:
            # Both have equal non-zero strength and SAME preference.
            # E.2 fallback: grant the wider colour difference.
            if pref1 == ColorPref.WHITE:
                # Player with more negative diff (played more blacks) gets white.
                if p1.color_diff < p2.color_diff:
                    return p1, p2
                elif p2.color_diff < p1.color_diff:
                    return p2, p1
                # If equal diff, fall through to E.3.
            else:  # Both want black
                if p1.color_diff > p2.color_diff:
                    return p2, p1
                elif p2.color_diff > p1.color_diff:
                    return p1, p2
                # If equal diff, fall through to E.3.

        # --- E.3 — Alternate colours to the most recent time
        #           one had white and the other had black ---
        h1 = p1.color_hist
        h2 = p2.color_hist
        min_len = min(len(h1), len(h2))
        if min_len > 0:
            # Scan backwards through history
            for k in range(1, min_len + 1):
                c1 = h1[-k]
                c2 = h2[-k]
                if c1 != c2:
                    # One had white, the other had black — give the opposite.
                    if c1 == "white":
                        # p1 had white → now p1 gets black
                        return p2, p1
                    else:
                        return p1, p2

        # --- E.4 — Grant the colour preference of the higher ranked player ---
        h_pref = higher.color_preference
        if h_pref == ColorPref.WHITE:
            return (higher, lower)
        elif h_pref == ColorPref.BLACK:
            return (lower, higher)

        # --- E.5 — Odd pairing number → initial-colour, even → opposite ---
        if higher.pairing_number % 2 == 1:
            # Odd: give initial-colour to higher ranked player.
            if self.initial_color == "white":
                return higher, lower
            else:
                return lower, higher
        else:
            # Even: give opposite of initial-colour to higher ranked player.
            if self.initial_color == "white":
                return lower, higher
            else:
                return higher, lower

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
    # Phase 1.10 — Quality metric & multi-criteria scoring (C.04.3 §C5-C19)
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
        Count the number of unsatisfied colour preferences (C.04.3 C10).

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

    def _score_candidate(
        self,
        pairs: List[Tuple[DutchPlayer, DutchPlayer]],
        downfloaters: List[DutchPlayer],
        bracket_score: float,
    ) -> tuple:
        """
        Score a bracket candidate per criteria C5-C19 (lower = better).

        Returns a tuple for lexicographic comparison where lower is better.
        Criteria order: C5, C6, CA1, CA2, C10, C11, C12, C13, C14, C15,
                        C16, C17, C18, C19.
        CA1/CA2 are sub-levels of the colour criteria above C10, matching
        bbpPairings' 4-level colour bit encoding:
          CA1 = absolute colour imbalance conflict (both |diff|>=2, same pref)
          CA2 = absolute colour preference conflict (both absolute, same pref)
        """
        # C5: maximize pairs → minimize negative
        c5 = -len(pairs)

        # C6: minimize PSD (lexicographic, descending SDs)
        sds: List[float] = []
        for p1, p2 in pairs:
            sds.append(abs(p1.score - p2.score))
        # Downfloater SDs per A.8
        artificial = bracket_score - 1.0
        for df in downfloaters:
            sds.append(df.score - artificial)
        sds.sort(reverse=True)
        c6 = tuple(sds)

        # CA1: absolute colour imbalance conflict
        # Penalty when BOTH players have |color_diff| >= 2 AND same preference.
        ca1 = 0
        for p1, p2 in pairs:
            if (abs(p1.color_diff) >= 2 and abs(p2.color_diff) >= 2
                    and p1.color_preference == p2.color_preference
                    and p1.color_preference != ColorPref.NONE):
                ca1 += 1

        # CA2: absolute colour preference conflict
        # Penalty when BOTH have absoluteColorPreference (strength 3) AND same pref.
        ca2 = 0
        for p1, p2 in pairs:
            if (p1.preference_strength == 3 and p2.preference_strength == 3
                    and p1.color_preference == p2.color_preference
                    and p1.color_preference != ColorPref.NONE):
                ca2 += 1

        # C10: minimize players not getting colour preference
        c10 = 0
        for p1, p2 in pairs:
            pref1 = p1.color_preference
            pref2 = p2.color_preference
            if pref1 != ColorPref.NONE and pref2 != ColorPref.NONE:
                if pref1 == pref2:
                    c10 += 1

        # C11: minimize players with strong pref (strength ≥ 2) not satisfied
        c11 = 0
        for p1, p2 in pairs:
            pref1 = p1.color_preference
            pref2 = p2.color_preference
            if pref1 != ColorPref.NONE and pref2 != ColorPref.NONE:
                if pref1 == pref2:
                    # The violated player has the weaker preference
                    violated_strength = min(
                        p1.preference_strength, p2.preference_strength
                    )
                    if violated_strength >= 2:
                        c11 += 1

        # Helper: float N rounds back
        def _float_back(p: DutchPlayer, n: int) -> FloatDir:
            idx = len(p.float_hist) - n
            return p.float_hist[idx] if idx >= 0 else FloatDir.NONE

        # C12: minimize repeat downfloats from previous round.
        # Only count REMAINDER players (downfloaters) — paired MDPs are
        # successfully matched and are NOT downfloating further.
        c12 = 0
        for df in downfloaters:
            if _float_back(df, 1) == FloatDir.DOWN:
                c12 += 1

        # C13: minimize repeat upfloats from previous round
        c13 = 0
        for p1, p2 in pairs:
            if p1.score < p2.score and _float_back(p1, 1) == FloatDir.UP:
                c13 += 1
            elif p2.score < p1.score and _float_back(p2, 1) == FloatDir.UP:
                c13 += 1

        # C14: minimize repeat downfloats from 2 rounds ago
        c14 = 0
        for df in downfloaters:
            if _float_back(df, 2) == FloatDir.DOWN:
                c14 += 1

        # C15: minimize repeat upfloats from 2 rounds ago
        c15 = 0
        for p1, p2 in pairs:
            if p1.score < p2.score and _float_back(p1, 2) == FloatDir.UP:
                c15 += 1
            elif p2.score < p1.score and _float_back(p2, 2) == FloatDir.UP:
                c15 += 1

        # C16: minimize score-sum of repeat downfloaters (prev round)
        c16 = 0.0
        for df in downfloaters:
            if _float_back(df, 1) == FloatDir.DOWN:
                c16 += df.score

        # C17: minimize opponent-score of repeat upfloaters (prev round)
        c17 = 0.0
        for p1, p2 in pairs:
            if p1.score < p2.score and _float_back(p1, 1) == FloatDir.UP:
                c17 += p2.score
            elif p2.score < p1.score and _float_back(p2, 1) == FloatDir.UP:
                c17 += p1.score

        # C18: minimize score-sum of repeat downfloaters (2 rounds ago)
        c18 = 0.0
        for df in downfloaters:
            if _float_back(df, 2) == FloatDir.DOWN:
                c18 += df.score

        # C19: minimize opponent-score of repeat upfloaters (2 rounds ago)
        c19 = 0.0
        for p1, p2 in pairs:
            if p1.score < p2.score and _float_back(p1, 2) == FloatDir.UP:
                c19 += p2.score
            elif p2.score < p1.score and _float_back(p2, 2) == FloatDir.UP:
                c19 += p1.score

        return (c5, c6, ca1, ca2, c10, c11, c12, c13, c14, c15, c16, c17, c18, c19)

    # ------------------------------------------------------------------
    # Maximum Weight Matching (Blossom algorithm)
    # ------------------------------------------------------------------

    def _compute_mwm_edge_weight(
        self,
        p1: DutchPlayer,
        p2: DutchPlayer,
        bracket_score: float,
        n: int,
        s1_ids: Set[int],
        s2_ids: Set[int],
        s1_pos: Dict[int, int],
        s2_pos: Dict[int, int],
        s_len: int,
    ) -> int:
        """
        Compute per-edge weight encoding C.04.3 criteria C5–C19
        plus S1/S2 ordering preference.

        Higher weight = better pairing.  Returns 0 for incompatible pairs.

        Bit layout (MSB → LSB, each level separated by *B* or *SB* bits):

            compatible | C6 | color_compat | color_abs | C10 | C11
                       | C12 | C13 | C14 | C15
                       | C16 | C17 | C18 | C19
                       | s1s2_cross | s1s2_closeness
        """
        if not self._can_pair(p1, p2):
            return 0

        # Bit widths — must be wide enough so that the sum over all pairs
        # in a matching cannot overflow into the bits of a higher criterion.
        B = max(8, n.bit_length() + 3)
        max_score_int = max(1, int(self.total_rounds * 2))
        SB = max(16, (n * max_score_int).bit_length() + 3)

        pref1, pref2 = p1.color_preference, p2.color_preference
        str1, str2 = p1.preference_strength, p2.preference_strength

        def _fb(p: DutchPlayer, k: int) -> FloatDir:
            idx = len(p.float_hist) - k
            return p.float_hist[idx] if idx >= 0 else FloatDir.NONE

        # ------- build weight from LSB (lowest priority) upward -------
        w = 0
        s = 0

        # == S1/S2 ordering preference (lowest priority, tie-breaker) ==

        # Position closeness within S1-S2 pairs
        is_cross = ((p1.id in s1_ids and p2.id in s2_ids)
                     or (p2.id in s1_ids and p1.id in s2_ids))
        if is_cross and s_len > 0:
            s1p_id = p1.id if p1.id in s1_ids else p2.id
            s2p_id = p2.id if p2.id in s2_ids else p1.id
            pos1 = s1_pos.get(s1p_id, 0)
            pos2 = s2_pos.get(s2p_id, 0)
            closeness = max(0, s_len - abs(pos1 - pos2))
        else:
            closeness = 0
        w |= closeness << s
        s += B

        # Cross-half bonus (S1 paired with S2 preferred over intra-half)
        w |= int(is_cross) << s
        s += B

        # == Float criteria C12–C19 ==

        # C19: minimize opponent-score of repeat upfloaters (2 ago)
        penalty = 0
        if _fb(p1, 2) == FloatDir.UP and p1.score < p2.score:
            penalty += int(p2.score * 2)
        if _fb(p2, 2) == FloatDir.UP and p2.score < p1.score:
            penalty += int(p1.score * 2)
        w |= max(0, max_score_int - penalty) << s
        s += SB

        # C18: reward pairing repeat downfloaters by score-sum (2 ago).
        # Pairing a repeat downfloater removes them from the remainder,
        # reducing the C18 score-sum.  Reward = their score.
        c18_reward = 0
        if _fb(p1, 2) == FloatDir.DOWN and p1.score > bracket_score:
            c18_reward += int(p1.score * 2)
        if _fb(p2, 2) == FloatDir.DOWN and p2.score > bracket_score:
            c18_reward += int(p2.score * 2)
        w |= c18_reward << s
        s += SB

        # C17: minimize opponent-score of repeat upfloaters (prev)
        penalty = 0
        if _fb(p1, 1) == FloatDir.UP and p1.score < p2.score:
            penalty += int(p2.score * 2)
        if _fb(p2, 1) == FloatDir.UP and p2.score < p1.score:
            penalty += int(p1.score * 2)
        w |= max(0, max_score_int - penalty) << s
        s += SB

        # C16: reward pairing repeat downfloaters by score-sum (prev round).
        # Pairing a repeat downfloater removes them from the remainder,
        # reducing the C16 score-sum.  Reward = their score.
        c16_reward = 0
        if _fb(p1, 1) == FloatDir.DOWN and p1.score > bracket_score:
            c16_reward += int(p1.score * 2)
        if _fb(p2, 1) == FloatDir.DOWN and p2.score > bracket_score:
            c16_reward += int(p2.score * 2)
        w |= c16_reward << s
        s += SB

        # C15: repeat upfloaters (2 ago)
        c15 = 1
        for p, opp in [(p1, p2), (p2, p1)]:
            if _fb(p, 2) == FloatDir.UP and p.score < opp.score:
                c15 = 0
        w |= c15 << s
        s += B

        # C14: reward pairing repeat downfloaters (2 rounds ago).
        # Each paired repeat downfloater reduces the remainder C14 count.
        c14 = 0
        for p in (p1, p2):
            if _fb(p, 2) == FloatDir.DOWN and p.score > bracket_score:
                c14 += 1
        w |= c14 << s
        s += B

        # C13: repeat upfloaters (prev)
        c13 = 1
        for p, opp in [(p1, p2), (p2, p1)]:
            if _fb(p, 1) == FloatDir.UP and p.score < opp.score:
                c13 = 0
        w |= c13 << s
        s += B

        # C12: reward pairing repeat downfloaters (prev round).
        # Each paired repeat downfloater reduces the remainder C12 count.
        c12 = 0
        for p in (p1, p2):
            if _fb(p, 1) == FloatDir.DOWN and p.score > bracket_score:
                c12 += 1
        w |= c12 << s
        s += B

        # == Colour criteria ==

        # C11: strong colour preference violated
        c11 = 1
        if (pref1 != ColorPref.NONE and pref2 != ColorPref.NONE
                and pref1 == pref2 and min(str1, str2) >= 2):
            c11 = 0
        w |= c11 << s
        s += B

        # C10: any colour preference violated
        c10 = 1
        if (pref1 != ColorPref.NONE and pref2 != ColorPref.NONE
                and pref1 == pref2):
            c10 = 0
        w |= c10 << s
        s += B

        # Absolute colour preferences compatible
        c_abs = 1
        if (str1 == 3 and str2 == 3 and pref1 == pref2
                and not self._is_last_round):
            c_abs = 0
        w |= c_abs << s
        s += B

        # Colour preferences broadly compatible
        c_compat = 1
        if (pref1 != ColorPref.NONE and pref2 != ColorPref.NONE
                and pref1 == pref2 and str1 >= 2 and str2 >= 2):
            c_compat = 0
        w |= c_compat << s
        s += B

        # == C6: minimize PSD ==
        score_diff_int = int(abs(p1.score - p2.score) * 2)
        c6_val = max(0, max_score_int * 2 - score_diff_int)
        w |= c6_val << s
        s += SB

        # == Bracket pairing (above C6): prefer MDP–resident pairings ==
        # Matches bbpPairings' TIER 1 (lowerPlayerInCurrentBracket).
        # When bracket has MDPs, edges involving ≥1 MDP with ≥1 resident
        # get a bonus.  This prevents the MWM from leaving MDPs unpaired
        # in favour of lower-PSD resident–resident pairs.
        involves_mdp = (p1.score > bracket_score) or (p2.score > bracket_score)
        involves_res = (p1.score <= bracket_score) or (p2.score <= bracket_score)
        mdp_bonus = 1 if (involves_mdp and involves_res) else 0
        w |= mdp_bonus << s
        s += B

        # Top bit: valid compatible pair
        w |= 1 << s

        return w

    def _pair_bracket_mwm(
        self,
        players: List[DutchPlayer],
        bracket_score: float,
    ) -> Tuple[List[Tuple[DutchPlayer, DutchPlayer]], List[DutchPlayer]]:
        """
        Pair a bracket using Maximum Weight Matching (Edmonds' Blossom).

        Replaces the S1/S2 + transposition + exchange pipeline with a
        single call to ``networkx.max_weight_matching`` whose edge weights
        encode the full C.04.3 C5–C19 priority, plus S1/S2 ordering
        preference as a tie-breaker.

        Returns ``(pairs, remainder)`` exactly like the legacy methods.
        """
        import networkx as nx

        n = len(players)
        if n < 2:
            return [], list(players)

        # --- Determine S1/S2 split ---
        sorted_by_pn = sorted(players, key=lambda p: p.pairing_number)
        mdps = [p for p in sorted_by_pn if p.score > bracket_score]
        residents = [p for p in sorted_by_pn if p.score <= bracket_score]

        if mdps:
            s1 = mdps
            s2 = residents
        else:
            half = n // 2
            s1 = sorted_by_pn[:half]
            s2 = sorted_by_pn[half:]

        s1_ids: Set[int] = {p.id for p in s1}
        s2_ids: Set[int] = {p.id for p in s2}
        s1_pos: Dict[int, int] = {p.id: i for i, p in enumerate(s1)}
        s2_pos: Dict[int, int] = {p.id: i for i, p in enumerate(s2)}
        s_len = max(len(s1), len(s2))

        # --- Build weighted graph ---
        G = nx.Graph()
        id_to_player = {p.id: p for p in players}
        for p in players:
            G.add_node(p.id)

        for i in range(n):
            for j in range(i + 1, n):
                w = self._compute_mwm_edge_weight(
                    players[i], players[j], bracket_score, n,
                    s1_ids, s2_ids, s1_pos, s2_pos, s_len,
                )
                if w > 0:
                    G.add_edge(players[i].id, players[j].id, weight=w)

        matching = nx.max_weight_matching(G, maxcardinality=True)

        matched_ids: set = set()
        pairs: List[Tuple[DutchPlayer, DutchPlayer]] = []
        for u, v in matching:
            pu, pv = id_to_player[u], id_to_player[v]
            if pu.pairing_number < pv.pairing_number:
                pairs.append((pu, pv))
            else:
                pairs.append((pv, pu))
            matched_ids.add(u)
            matched_ids.add(v)

        pairs.sort(key=lambda p: p[0].pairing_number)
        remainder = [p for p in players if p.id not in matched_ids]
        return pairs, remainder

    # ------------------------------------------------------------------
    # Global Maximum Weight Matching (replaces bracket-by-bracket greedy)
    # ------------------------------------------------------------------

    def _build_score_group_info(
        self, players: List[DutchPlayer],
    ) -> dict:
        """
        Build score-group metadata for global MWM edge weight computation.

        Returns a dict with:
        - ranks: score → rank (0=lowest, num_groups-1=highest)
        - indices: score → index (0=highest, for adjacency checks)
        - num_groups: number of distinct score groups
        - s1s2: per-score (s1_ids, s2_ids, s1_pos, s2_pos, half_size)
        - unique_scores: sorted descending
        """
        groups: Dict[float, List[DutchPlayer]] = defaultdict(list)
        for p in players:
            groups[p.score].append(p)

        unique_scores = sorted(groups.keys(), reverse=True)
        num_groups = len(unique_scores)

        score_ranks: Dict[float, int] = {}
        for i, score in enumerate(reversed(unique_scores)):
            score_ranks[score] = i

        score_indices: Dict[float, int] = {}
        for i, score in enumerate(unique_scores):
            score_indices[score] = i

        s1s2: Dict[float, tuple] = {}
        for score, group in groups.items():
            sorted_group = sorted(group, key=lambda p: p.pairing_number)
            half = len(sorted_group) // 2
            s1 = sorted_group[:half]
            s2 = sorted_group[half:]
            s1s2[score] = (
                {p.id for p in s1},
                {p.id for p in s2},
                {p.id: i for i, p in enumerate(s1)},
                {p.id: i for i, p in enumerate(s2)},
                max(len(s1), len(s2)),
            )

        return {
            'ranks': score_ranks,
            'indices': score_indices,
            'num_groups': num_groups,
            's1s2': s1s2,
            'unique_scores': unique_scores,
        }

    def _compute_global_edge_weight(
        self,
        p1: DutchPlayer,
        p2: DutchPlayer,
        sg_info: dict,
        n: int,
    ) -> int:
        """
        Compute edge weight for the global MWM graph.

        Encodes FIDE Dutch System criteria C5–C19 as a bit-packed integer.
        Higher weight = better pair.  Returns 0 for incompatible pairs.

        The bit layout (MSB → LSB) follows the FIDE criterion hierarchy:

            valid | bracket_closeness
                  | C6_psd | C10 | C11
                  | C12 | C13 | C14 | C15
                  | C16 | C17 | C18 | C19
                  | color_abs | color_compat
                  | s1s2_cross | s1s2_closeness
        """
        if not self._can_pair(p1, p2):
            return 0

        score_indices = sg_info['indices']
        s1s2 = sg_info['s1s2']

        # Bit widths
        B = max(8, n.bit_length() + 3)
        max_score_int = max(1, int(self.total_rounds * 2))
        SB = max(16, (n * max_score_int).bit_length() + 3)

        higher = p1 if p1.score >= p2.score else p2
        lower = p2 if p1.score >= p2.score else p1
        bracket_score = lower.score

        pref1, pref2 = p1.color_preference, p2.color_preference
        str1, str2 = p1.preference_strength, p2.preference_strength

        def _fb(p: DutchPlayer, k: int) -> FloatDir:
            idx = len(p.float_hist) - k
            return p.float_hist[idx] if idx >= 0 else FloatDir.NONE

        w = 0
        s = 0

        # === S1/S2 ordering tiebreaker (lowest priority) ===
        # Closeness encodes Dutch transposition order: prefer natural
        # partner (same index), then lower S2 index as tiebreaker.
        same_group = (p1.score == p2.score)
        if same_group and p1.score in s1s2:
            s1_ids, s2_ids, s1_pos, s2_pos, half = s1s2[p1.score]
            is_cross = ((p1.id in s1_ids and p2.id in s2_ids)
                        or (p2.id in s1_ids and p1.id in s2_ids))
            if is_cross and half > 0:
                s1p_id = p1.id if p1.id in s1_ids else p2.id
                s2p_id = p2.id if p2.id in s2_ids else p1.id
                pos1 = s1_pos.get(s1p_id, 0)
                pos2 = s2_pos.get(s2p_id, 0)
                dist = abs(pos1 - pos2)
                closeness = 2 * max(0, half - dist) + (1 if pos2 <= pos1 else 0)
            else:
                closeness = 0
            w |= closeness << s
            s += B
            w |= int(is_cross) << s
            s += 1
        else:
            # Cross-bracket: use pairing number closeness as tiebreaker
            pn_diff = abs(p1.pairing_number - p2.pairing_number)
            closeness = max(0, n - pn_diff)
            w |= closeness << s
            s += B
            s += 1  # Reserve space for cross bit (always 0 here)

        # === Color tiebreakers ===
        c_compat = 1
        if (pref1 != ColorPref.NONE and pref2 != ColorPref.NONE
                and pref1 == pref2 and str1 >= 2 and str2 >= 2):
            c_compat = 0
        w |= c_compat << s
        s += 1

        c_abs = 1
        if (str1 == 3 and str2 == 3 and pref1 == pref2
                and not self._is_last_round):
            c_abs = 0
        w |= c_abs << s
        s += 1

        # === Float criteria C19 → C12 (ascending FIDE priority) ===

        # C19: minimize opponent-score of repeat upfloaters (2 ago)
        penalty = 0
        if _fb(p1, 2) == FloatDir.UP and p1.score < p2.score:
            penalty += int(p2.score * 2)
        if _fb(p2, 2) == FloatDir.UP and p2.score < p1.score:
            penalty += int(p1.score * 2)
        w |= max(0, max_score_int - penalty) << s
        s += SB

        # C18: reward pairing repeat downfloaters (2 ago)
        c18 = 0
        if _fb(p1, 2) == FloatDir.DOWN and p1.score > bracket_score:
            c18 += int(p1.score * 2)
        if _fb(p2, 2) == FloatDir.DOWN and p2.score > bracket_score:
            c18 += int(p2.score * 2)
        w |= c18 << s
        s += SB

        # C17: minimize opponent-score of repeat upfloaters (prev)
        penalty = 0
        if _fb(p1, 1) == FloatDir.UP and p1.score < p2.score:
            penalty += int(p2.score * 2)
        if _fb(p2, 1) == FloatDir.UP and p2.score < p1.score:
            penalty += int(p1.score * 2)
        w |= max(0, max_score_int - penalty) << s
        s += SB

        # C16: reward pairing repeat downfloaters (prev)
        c16 = 0
        if _fb(p1, 1) == FloatDir.DOWN and p1.score > bracket_score:
            c16 += int(p1.score * 2)
        if _fb(p2, 1) == FloatDir.DOWN and p2.score > bracket_score:
            c16 += int(p2.score * 2)
        w |= c16 << s
        s += SB

        # C15: avoid repeat upfloaters (2 ago)
        c15 = 1
        for p, opp in [(p1, p2), (p2, p1)]:
            if _fb(p, 2) == FloatDir.UP and p.score < opp.score:
                c15 = 0
        w |= c15 << s
        s += B

        # C14: reward pairing repeat downfloaters (2 ago)
        c14 = 0
        for p in (p1, p2):
            if _fb(p, 2) == FloatDir.DOWN and p.score > bracket_score:
                c14 += 1
        w |= c14 << s
        s += B

        # C13: avoid repeat upfloaters (prev)
        c13 = 1
        for p, opp in [(p1, p2), (p2, p1)]:
            if _fb(p, 1) == FloatDir.UP and p.score < opp.score:
                c13 = 0
        w |= c13 << s
        s += B

        # C12: reward pairing repeat downfloaters (prev)
        c12 = 0
        for p in (p1, p2):
            if _fb(p, 1) == FloatDir.DOWN and p.score > bracket_score:
                c12 += 1
        w |= c12 << s
        s += B

        # === C11: strong colour preference ===
        c11 = 1
        if (pref1 != ColorPref.NONE and pref2 != ColorPref.NONE
                and pref1 == pref2 and min(str1, str2) >= 2):
            c11 = 0
        w |= c11 << s
        s += B

        # === C10: any colour preference ===
        c10 = 1
        if (pref1 != ColorPref.NONE and pref2 != ColorPref.NONE
                and pref1 == pref2):
            c10 = 0
        w |= c10 << s
        s += B

        # === C6: PSD minimization ===
        psd = int(abs(p1.score - p2.score) * 2)
        c6_val = max(0, max_score_int * 2 - psd)
        w |= c6_val << s
        s += SB

        # === Bracket closeness (C3/C4 structural) ===
        hi_idx = score_indices[higher.score]
        lo_idx = score_indices[lower.score]
        gap = abs(hi_idx - lo_idx)
        if gap == 0:
            bracket_close = 2
        elif gap == 1:
            bracket_close = 1
        else:
            bracket_close = 0
        w |= bracket_close << s
        s += B

        # Top bit: valid pair
        w |= 1 << s

        return w

    def _pair_global_mwm(
        self,
        players: List[DutchPlayer],
    ) -> Tuple[List[Tuple[DutchPlayer, DutchPlayer]], List[DutchPlayer]]:
        """
        Pair all players using a single global Maximum Weight Matching.

        Builds one graph with all eligible players, computes edge weights
        encoding the full FIDE C.04.3 criteria hierarchy, and runs
        Edmonds' Blossom algorithm via networkx.max_weight_matching.

        Returns (pairs, remainder).
        """
        import networkx as nx

        n = len(players)
        if n < 2:
            return [], list(players)

        sg_info = self._build_score_group_info(players)

        G = nx.Graph()
        id_to_player = {p.id: p for p in players}
        for p in players:
            G.add_node(p.id)

        for i in range(n):
            for j in range(i + 1, n):
                w = self._compute_global_edge_weight(
                    players[i], players[j], sg_info, n,
                )
                if w > 0:
                    G.add_edge(players[i].id, players[j].id, weight=w)

        matching = nx.max_weight_matching(G, maxcardinality=True)

        matched_ids: set = set()
        pairs: List[Tuple[DutchPlayer, DutchPlayer]] = []
        for u, v in matching:
            pu, pv = id_to_player[u], id_to_player[v]
            if pu.pairing_number < pv.pairing_number:
                pairs.append((pu, pv))
            else:
                pairs.append((pv, pu))
            matched_ids.add(u)
            matched_ids.add(v)

        pairs.sort(key=lambda p: p[0].pairing_number)
        remainder = [p for p in players if p.id not in matched_ids]
        return pairs, remainder

    def _record_global_floats(
        self,
        paired: List[Tuple[DutchPlayer, DutchPlayer]],
    ):
        """Record float directions for cross-bracket pairs from global MWM."""
        for p1, p2 in paired:
            if p1.score != p2.score:
                bracket_score = min(p1.score, p2.score)
                if p1.score > bracket_score:
                    p1.float_hist.append(FloatDir.DOWN)
                if p2.score > bracket_score:
                    p2.float_hist.append(FloatDir.DOWN)

    def _compute_bracket_edge_weight(
        self,
        p1: DutchPlayer,
        p2: DutchPlayer,
        bracket_score: float,
        n: int,
        s1_ids: Set[int],
        s2_ids: Set[int],
        s1_pos: Dict[int, int],
        s2_pos: Dict[int, int],
        s_len: int,
        in_current_bracket: bool,
        in_next_bracket: bool,
        score_group_shifts: Optional[Dict[float, int]] = None,
        score_groups_shift: int = 0,
        score_group_size_bits: int = 8,
        bye_candidates: Optional[Set[int]] = None,
        bye_assignee_score: Optional[float] = None,
        is_single_downfloater_bye_assignee: bool = False,
        unplayed_game_ranks: Optional[Dict[int, int]] = None,
    ) -> int:
        """
        Compute base edge weight for iterative bracket MWM.

        Faithfully models bbpPairings' computeEdgeWeight + insertColorBits.
        Detail criteria (color, float C12-C19) are ONLY evaluated for
        current-bracket pairs (in_current_bracket=True).

        p1 = higher-ranked player, p2 = lower-ranked player.

        Bit layout (MSB → LSB), matching bbpPairings exactly:

            completion(2b) | TIER1(B) | TIER2(SB) | TIER3(B) | TIER4(SB)
            | bye(2*B) | c_imb(B) | c_absP(B) | c_compat(B) | c_strong(B)
            | [C12(B) | C13(B)]  (if rounds_played >= 1)
            | [C14(B) | C15(B)]  (if rounds_played >= 2)
            | [C16(SB) | C17(SB)]  (if rounds_played >= 1)
            | [C18(SB) | C19(SB)]  (if rounds_played >= 2)
            | RESERVED(3*B + 1)
        """
        if not self._can_pair(p1, p2):
            return 0

        B = max(8, n.bit_length() + 3)
        max_score_int = max(1, int(self.total_rounds * 2))
        SB = score_groups_shift if score_group_shifts is not None else max(16, (n * max_score_int).bit_length() + 3)

        pref1, pref2 = p1.color_preference, p2.color_preference
        str1, str2 = p1.preference_strength, p2.preference_strength
        rounds_played = self.round_number - 1

        def _fb(p: DutchPlayer, k: int) -> FloatDir:
            idx = len(p.float_hist) - k
            return p.float_hist[idx] if idx >= 0 else FloatDir.NONE

        mask = in_current_bracket

        def _score_bits(score: float) -> int:
            if score_group_shifts is not None and score in score_group_shifts:
                return 1 << score_group_shifts[score]
            return int(score * 2) + 1

        def _repeated_color(p: DutchPlayer) -> Optional[str]:
            """Trailing streak of >=2 same-color played games, or None."""
            ch = p.color_hist
            if len(ch) >= 2 and ch[-1] == ch[-2]:
                return ch[-1]
            return None

        w = 0
        s = 0

        # === RESERVED bits (3*B + 1) for exchange/ordering mechanism ===
        s += 3 * B + 1

        # === Float criteria (conditionally allocated by rounds_played) ===
        # C18/C19: two rounds back (only if rounds_played >= 2)
        if rounds_played >= 2:
            # C19: opponent scores of repeated upfloaters (2 rounds back)
            if mask:
                c19 = 0
                if not (_fb(p2, 2) == FloatDir.UP and p1.score > p2.score):
                    c19 = _score_bits(p1.score)
                w |= c19 << s
            s += SB

            # C18: downfloater scores (2 rounds back)
            if mask:
                c18 = 0
                if _fb(p2, 2) == FloatDir.DOWN:
                    c18 += _score_bits(p2.score)
                if _fb(p1, 2) == FloatDir.DOWN:
                    c18 += _score_bits(p1.score)
                w |= c18 << s
            s += SB

        # C16/C17: previous round (only if rounds_played >= 1)
        if rounds_played >= 1:
            # C17: opponent scores of repeated upfloaters (previous round)
            if mask:
                c17 = 0
                if not (_fb(p2, 1) == FloatDir.UP and p1.score > p2.score):
                    c17 = _score_bits(p1.score)
                w |= c17 << s
            s += SB

            # C16: downfloater scores (previous round)
            if mask:
                c16 = 0
                if _fb(p2, 1) == FloatDir.DOWN:
                    c16 += _score_bits(p2.score)
                if _fb(p1, 1) == FloatDir.DOWN:
                    c16 += _score_bits(p1.score)
                w |= c16 << s
            s += SB

        # C14/C15: two rounds back (only if rounds_played >= 2)
        if rounds_played >= 2:
            # C15: repeated upfloat (2 rounds back)
            if mask:
                c15 = 1
                if _fb(p2, 2) == FloatDir.UP and p1.score > p2.score:
                    c15 = 0
                w |= c15 << s
            s += B

            # C14: repeated downfloat count (2 rounds back)
            if mask:
                c14 = 0
                if _fb(p2, 2) == FloatDir.DOWN:
                    c14 += 1
                if _fb(p1, 2) == FloatDir.DOWN and p1.score <= p2.score:
                    c14 += 1
                w |= c14 << s
            s += B

        # C12/C13: previous round (only if rounds_played >= 1)
        if rounds_played >= 1:
            # C13: repeated upfloat (previous round)
            if mask:
                c13 = 1
                if _fb(p2, 1) == FloatDir.UP and p1.score > p2.score:
                    c13 = 0
                w |= c13 << s
            s += B

            # C12: repeated downfloat count (previous round)
            if mask:
                c12 = 0
                if _fb(p2, 1) == FloatDir.DOWN:
                    c12 += 1
                if _fb(p1, 1) == FloatDir.DOWN and p1.score <= p2.score:
                    c12 += 1
                w |= c12 << s
            s += B

        # === Color criteria (4 levels matching insertColorBits exactly) ===
        # bbpPairings calls insertColorBits(lowerPlayer, higherPlayer)
        # so "player" = p2 (lower), "opponent" = p1 (higher)

        # Bit 4 (highest color): c_strong
        # Penalty when BOTH have >= strong pref, NOT both absolute, same pref
        if mask:
            c_strong = 1
            if (pref1 == pref2 and pref1 != ColorPref.NONE
                    and str1 >= 2 and str2 >= 2
                    and not (str1 == 3 and str2 == 3)):
                c_strong = 0
            w |= c_strong << s
        s += B

        # Bit 3: c_compat = colorPreferencesAreCompatible
        if mask:
            c_compat = 1
            if (pref1 != ColorPref.NONE and pref2 != ColorPref.NONE
                    and pref1 == pref2):
                c_compat = 0
            w |= c_compat << s
        s += B

        # Bit 2: c_absP (absolute color preference with sub-conditions)
        if mask:
            c_absp = 1
            p2_abs = (str2 == 3)
            p1_abs = (str1 == 3)
            if p2_abs and p1_abs and pref1 == pref2:
                p2_imb = abs(p2.color_diff)
                p1_imb = abs(p1.color_diff)
                p2_rep = _repeated_color(p2)
                p1_rep = _repeated_color(p1)
                if p2_imb == p1_imb:
                    if not (p2_rep is None or p2_rep != p1_rep):
                        c_absp = 0
                else:
                    lower_rep = p1_rep if p2_imb > p1_imb else p2_rep
                    inv_pref = ('black' if pref2 == ColorPref.WHITE
                                else 'white')
                    if lower_rep is not None and lower_rep == inv_pref:
                        c_absp = 0
            w |= c_absp << s
        s += B

        # Bit 1 (lowest color): c_imb (absoluteColorImbalance)
        if mask:
            c_imb = 1
            if (abs(p1.color_diff) >= 2 and abs(p2.color_diff) >= 2
                    and pref1 == pref2 and pref1 != ColorPref.NONE):
                c_imb = 0
            w |= c_imb << s
        s += B

        # === C9: minimize unplayed games of bye assignee (2*B bits) ===
        # Only fires when the bye assignee is in the top bracket and is the
        # sole "downfloater". Among players at byeAssigneeScore, those with
        # FEWER played games get higher rank → more weight when paired →
        # less likely to be the unmatched (bye) player.
        # Width matches our existing layout (2*B); C++ uses 2*scoreGroupSizeBits.
        if (is_single_downfloater_bye_assignee
                and unplayed_game_ranks is not None
                and bye_assignee_score is not None):
            c9 = 0
            if p1.score == bye_assignee_score:
                c9 |= unplayed_game_ranks.get(len(p1.opponents), 0)
            if p2.score == bye_assignee_score:
                c9 += unplayed_game_ranks.get(len(p2.opponents), 0)
            w |= c9 << s
        s += 2 * B

        # === TIER 3: Next bracket pair ===
        if in_next_bracket:
            w |= 1 << s
        s += B

        # === TIER 4: Next bracket scores ===
        if in_next_bracket:
            w |= _score_bits(p1.score) << s
        s += SB

        # === TIER 1: Current bracket pair (highest priority) ===
        if in_current_bracket:
            w |= 1 << s
        s += B

        # === TIER 2: Current bracket scores ===
        if in_current_bracket:
            w |= _score_bits(p1.score) << s
        s += SB

        # === Completion (top 2 bits): bye candidacy encoding ===
        # 3 = both non-bye-candidates (best)
        # 2 = one non-bye-candidate
        # 1 = both bye candidates (MWM will prefer to leave one unmatched)
        if bye_candidates is not None:
            p1_bye = p1.id in bye_candidates
            p2_bye = p2.id in bye_candidates
            completion = 1 + (not p1_bye) + (not p2_bye)
        else:
            completion = 3
        w |= completion << s

        return w

    def _pair_iterative_mwm(
        self,
        all_players: List[DutchPlayer],
        bye_candidates: Optional[Set[int]] = None,
    ) -> Tuple[List[Tuple[DutchPlayer, DutchPlayer]], List[DutchPlayer]]:
        """
        Pair using iterative bracket-by-bracket MWM with exchange mechanism.

        Closely follows bbpPairings' computeMatching() algorithm:
        - Global graph with all players as vertices
        - Bracket-by-bracket processing from top to bottom
        - Iterative MWM with weight adjustments for MDP selection,
          exchange selection, and opponent prioritization
        - finalizePair() locks in pairings by zeroing alternative edges
        """
        import networkx as nx

        # Sort all players by descending score, ascending pairing number
        sorted_players = sorted(
            all_players, key=lambda p: (-p.score, p.pairing_number)
        )
        n = len(sorted_players)
        if n < 2:
            return [], list(sorted_players)

        id_to_player = {p.id: p for p in sorted_players}
        # Map player id → index in sorted_players (= vertex index)
        idx_of = {p.id: i for i, p in enumerate(sorted_players)}

        # ---------------------------------------------------------------
        # Edge weight storage: {(i, j): weight} where i < j
        # These are indices into sorted_players
        # ---------------------------------------------------------------
        edge_weights: Dict[Tuple[int, int], int] = {}

        def _ek(i: int, j: int) -> Tuple[int, int]:
            return (min(i, j), max(i, j))

        def _set_w(i: int, j: int, w: int):
            edge_weights[_ek(i, j)] = w

        def _get_w(i: int, j: int) -> int:
            return edge_weights.get(_ek(i, j), 0)

        def _run_mwm() -> List[int]:
            """
            Run MWM on the current graph.
            Returns matching[idx] = partner_idx (self if unmatched).
            """
            G = nx.Graph()
            for i in range(n):
                G.add_node(i)
            for (i, j), w in edge_weights.items():
                if w > 0:
                    G.add_edge(i, j, weight=w)
            m = nx.max_weight_matching(G, maxcardinality=True)
            result = list(range(n))  # self-matched = unmatched
            for u, v in m:
                result[u] = v
                result[v] = u
            return result

        def _finalize_pair(idx1: int, idx2: int):
            """Lock in a pairing by zeroing all alternative edges."""
            for k in range(n):
                if k != idx2:
                    _set_w(idx1, k, 0)
                if k != idx1:
                    _set_w(idx2, k, 0)
            _set_w(idx1, idx2, 1)

        # ---------------------------------------------------------------
        # Build score group boundaries (indices into sorted_players)
        # ---------------------------------------------------------------
        sg_bounds: List[Tuple[int, int]] = []
        i = 0
        while i < n:
            j = i + 1
            while j < n and sorted_players[j].score == sorted_players[i].score:
                j += 1
            sg_bounds.append((i, j))
            i = j

        # ---------------------------------------------------------------
        # Compute scoreGroupShifts matching bbpPairings exactly.
        # Iterate score groups from HIGHEST to LOWEST (matching bbpPairings).
        # The highest score group gets shift=0 (least significant bits).
        # Each group gets bitsToRepresent(groupSize) bits (minimum 1).
        # ---------------------------------------------------------------
        score_group_shifts: Dict[float, int] = {}
        score_groups_shift = 0
        max_score_group_size = 0
        for sg_start, sg_end in reversed(sg_bounds):  # sg_bounds[-1] is lowest score
            group_size = sg_end - sg_start
            score = sorted_players[sg_start].score
            new_bits = max(1, group_size.bit_length())
            score_group_shifts[score] = score_groups_shift
            max_score_group_size = max(max_score_group_size, group_size)
            score_groups_shift += new_bits
        score_group_size_bits = max(1, max_score_group_size.bit_length())

        # ---------------------------------------------------------------
        # Preliminary MWM for odd player count: determine byeAssigneeScore
        # Matches bbpPairings' two-phase approach: lightweight MWM first
        # to find who naturally gets the bye, then narrow bye_candidates.
        # Also computes C9 state (isSingleDownfloaterTheByeAssignee +
        # unplayedGameRanks) used by `_compute_bracket_edge_weight`.
        # ---------------------------------------------------------------
        bye_assignee_score: Optional[float] = None
        is_single_downfloater_bye_assignee: bool = False
        unplayed_game_ranks: Optional[Dict[int, int]] = None
        if bye_candidates is not None and n % 2 == 1:
            top_score = sorted_players[0].score
            prelim_G = nx.Graph()
            for vi in range(n):
                prelim_G.add_node(vi)
            for vi in range(n):
                for vj in range(vi + 1, n):
                    pi, pj = sorted_players[vi], sorted_players[vj]
                    if not self._can_pair(pi, pj):
                        continue
                    # Lightweight weight: bye-eligibility + scoreGroup + top-bracket
                    pw = 0
                    pw |= (
                        1
                        + (pi.id not in bye_candidates)
                        + (pj.id not in bye_candidates)
                    )
                    pw <<= score_groups_shift
                    pw |= (
                        score_group_shifts.get(pi.score, 0)
                        + score_group_shifts.get(pj.score, 0)
                    )
                    pw <<= score_group_size_bits
                    # bbpPairings sets this bit on the LOWER-scored player
                    # of the pair (so it's only 1 when BOTH are in the top
                    # bracket). pj is the lower-scored one in our ordering.
                    pw |= int(pj.score >= top_score)
                    prelim_G.add_edge(vi, vj, weight=pw)
            prelim_m = nx.max_weight_matching(prelim_G, maxcardinality=True)
            prelim_matched = set()
            for u, v in prelim_m:
                prelim_matched.add(u)
                prelim_matched.add(v)
            # Find unmatched player (bye assignee)
            bye_assignee_score = sorted_players[0].score  # fallback
            for vi in range(n):
                if vi not in prelim_matched:
                    bye_assignee_score = sorted_players[vi].score
                    break
            # Narrow bye_candidates to those with score <= byeAssigneeScore
            bye_candidates = {
                p.id for p in sorted_players
                if p.id in bye_candidates
                and p.score <= bye_assignee_score
            }

            # --- C9: isSingleDownfloaterTheByeAssignee ---
            # True iff the bye assignee score is at the top bracket AND no
            # top-bracket player is matched to a non-top-bracket player in
            # the preliminary MWM. (Mirrors bbpPairings' computeMatching.)
            if bye_assignee_score >= top_score:
                is_single_downfloater_bye_assignee = True
                # Build prelim partner map
                prelim_partner: Dict[int, int] = {}
                for u, v in prelim_m:
                    prelim_partner[u] = v
                    prelim_partner[v] = u
                for vi in range(n):
                    if sorted_players[vi].score < top_score:
                        break
                    partner = prelim_partner.get(vi)
                    # Top-bracket player matched to a non-top-bracket player
                    # disqualifies the C9 trigger.
                    if partner is None:
                        # vi is unmatched (the bye assignee at top score)
                        continue
                    if sorted_players[partner].score < top_score:
                        is_single_downfloater_bye_assignee = False
                        break
            else:
                is_single_downfloater_bye_assignee = False

            # --- C9: unplayedGameRanks ---
            # Among players whose score == bye_assignee_score, sort by
            # playedGames DESC and assign ranks 0..k-1. With duplicates,
            # later assignment overwrites (matches bbpPairings semantics).
            played_game_counts = sorted(
                (len(p.opponents) for p in sorted_players
                 if p.score == bye_assignee_score),
                reverse=True,
            )
            unplayed_game_ranks = {}
            for rank, played_games in enumerate(played_game_counts):
                unplayed_game_ranks[played_games] = rank

        # ---------------------------------------------------------------
        # Set initial edge weights (no bracket context)
        # Only for compatible pairs — provides baseline for far-bracket
        # pairs and ensures completability.
        # ---------------------------------------------------------------
        for i in range(n):
            for j in range(i + 1, n):
                pi, pj = sorted_players[i], sorted_players[j]
                w = self._compute_bracket_edge_weight(
                    pi, pj,
                    bracket_score=min(pi.score, pj.score),
                    n=n,
                    s1_ids=set(), s2_ids=set(),
                    s1_pos={}, s2_pos={}, s_len=0,
                    in_current_bracket=False,
                    in_next_bracket=False,
                    score_group_shifts=score_group_shifts,
                    score_groups_shift=score_groups_shift,
                    score_group_size_bits=score_group_size_bits,
                    bye_candidates=bye_candidates,
                    bye_assignee_score=bye_assignee_score,
                    is_single_downfloater_bye_assignee=is_single_downfloater_bye_assignee,
                    unplayed_game_ranks=unplayed_game_ranks,
                )
                edge_weights[(i, j)] = w

        # ---------------------------------------------------------------
        # Bracket loop state
        # ---------------------------------------------------------------
        # players_by_idx: local indices into bracket, values = global indices
        players_by_idx: List[int] = []
        matched = [False] * n
        all_pairs: List[Tuple[DutchPlayer, DutchPlayer]] = []

        # Initialize with first score group
        sg_iter = 0
        first_start, first_end = sg_bounds[0]
        for k in range(first_start, first_end):
            players_by_idx.append(k)
        sg_iter = 1

        score_group_begin = 0  # Local index where current SG starts (MDPs < this)
        prev_players: List[int] = []  # For termination guard

        # ---------------------------------------------------------------
        # Main bracket loop
        # ---------------------------------------------------------------
        while len(players_by_idx) > 1 or sg_iter < len(sg_bounds):
            next_sg_begin = len(players_by_idx)  # End of current bracket

            # Load next score group into candidates
            if sg_iter < len(sg_bounds):
                ns_start, ns_end = sg_bounds[sg_iter]
                bracket_score = sorted_players[ns_start].score
                for k in range(ns_start, ns_end):
                    players_by_idx.append(k)
                sg_iter += 1
            else:
                bracket_score = sorted_players[
                    players_by_idx[-1]
                ].score

            # -------------------------------------------------------
            # Compute base edge weights for bracket + next SG
            # -------------------------------------------------------
            # base_ew[li][lj] for li > lj (li = larger local index)
            num_local = len(players_by_idx)
            base_ew: List[List[int]] = [[] for _ in range(num_local)]

            for li in range(score_group_begin, num_local):
                for lj in range(li):
                    gi = players_by_idx[li]
                    gj = players_by_idx[lj]
                    pi = sorted_players[gi]
                    pj = sorted_players[gj]

                    # In bbpPairings: larger local index = lower-ranked
                    in_current = (li < next_sg_begin)
                    in_next = (li >= next_sg_begin)

                    w = self._compute_bracket_edge_weight(
                        pj, pi, bracket_score, n,
                        s1_ids=set(), s2_ids=set(),
                        s1_pos={}, s2_pos={}, s_len=0,
                        in_current_bracket=in_current,
                        in_next_bracket=in_next,
                        score_group_shifts=score_group_shifts,
                        score_groups_shift=score_groups_shift,
                        score_group_size_bits=score_group_size_bits,
                        bye_candidates=bye_candidates,
                        bye_assignee_score=bye_assignee_score,
                        is_single_downfloater_bye_assignee=is_single_downfloater_bye_assignee,
                        unplayed_game_ranks=unplayed_game_ranks,
                    )
                    base_ew[li].append(w)

            # Set edge weights in global graph
            for li in range(score_group_begin, num_local):
                for lj in range(li):
                    gi = players_by_idx[li]
                    gj = players_by_idx[lj]
                    w = base_ew[li][lj] if lj < len(base_ew[li]) else 0
                    _set_w(gi, gj, w)

            # Compute B for reserved bits manipulation
            B = max(8, n.bit_length() + 3)

            # -------------------------------------------------------
            # edgeWeightComputer: adds exchange preference bits
            # -------------------------------------------------------
            def _exchange_weight(
                smaller_li: int, larger_li: int,
                smaller_remainder_idx: int, remainder_pairs: int,
            ) -> int:
                w = base_ew[larger_li][smaller_li] if smaller_li < len(base_ew[larger_li]) else 0
                if w:
                    addend = 0
                    # Minimize exchanges: is this player in the upper group?
                    addend |= int(smaller_remainder_idx < remainder_pairs)
                    # Minimize BSN difference
                    addend <<= (2 * B)
                    addend -= smaller_remainder_idx
                    # Reserve 1 bit for exchange selection nudge
                    addend <<= 1
                    w += addend
                return w

            # -------------------------------------------------------
            # Initial MWM for this bracket
            # -------------------------------------------------------
            stable = _run_mwm()

            # -------------------------------------------------------
            # Phase 1: MDP Selection
            # Choose which MDPs will pair in current bracket
            # -------------------------------------------------------
            moved_down_score = None
            remaining_md_players = 0
            remaining_matched_md = 0

            for li in range(score_group_begin):
                gi = players_by_idx[li]
                pi = sorted_players[gi]

                # Track MDP score groups
                if li == 0 or pi.score < moved_down_score:
                    moved_down_score = pi.score
                    remaining_md_players = 0
                    remaining_matched_md = 0
                    for mli in range(li, score_group_begin):
                        mgi = players_by_idx[mli]
                        mp = sorted_players[mgi]
                        if mp.score < moved_down_score:
                            break
                        remaining_md_players += 1
                        # Check if matched to a current-bracket resident
                        partner = stable[mgi]
                        if partner != mgi:
                            # Find partner's local index
                            partner_in_residents = False
                            for rli in range(score_group_begin, next_sg_begin):
                                if players_by_idx[rli] == partner:
                                    partner_in_residents = True
                                    break
                            if partner_in_residents:
                                remaining_matched_md += 1

                if remaining_matched_md == 0:
                    continue

                if remaining_md_players <= remaining_matched_md:
                    matched[gi] = True
                    remaining_md_players -= 1
                    continue

                remaining_md_players -= 1

                # Check if this MDP is currently matched to a resident
                partner = stable[gi]
                partner_in_residents = False
                if partner != gi:
                    for rli in range(score_group_begin, next_sg_begin):
                        if players_by_idx[rli] == partner:
                            partner_in_residents = True
                            break

                if not partner_in_residents:
                    # Boost edge weights to encourage matching
                    for rli in range(score_group_begin, next_sg_begin):
                        rgi = players_by_idx[rli]
                        bw = base_ew[rli][li] if li < len(base_ew[rli]) else 0
                        if bw:
                            bw |= 1  # Set lowest reserved bit
                            _set_w(gi, rgi, bw)
                    stable = _run_mwm()

                # Check again
                partner = stable[gi]
                partner_in_residents = False
                if partner != gi:
                    for rli in range(score_group_begin, next_sg_begin):
                        if players_by_idx[rli] == partner:
                            partner_in_residents = True
                            break

                if partner_in_residents:
                    matched[gi] = True
                    remaining_matched_md -= 1
                    # Lock with high weight
                    bracket_size = next_sg_begin - score_group_begin
                    for rli in range(score_group_begin, next_sg_begin):
                        rgi = players_by_idx[rli]
                        bw = base_ew[rli][li] if li < len(base_ew[rli]) else 0
                        if bw:
                            bw |= bracket_size
                            bw += 1
                            _set_w(gi, rgi, bw)

            # -------------------------------------------------------
            # Phase 2: MDP Opponent Selection
            # For each matched MDP, add tiebreaker weights and run MWM
            # to choose their opponent. Matches bbpPairings' sequential
            # processing: ALL MDPs' edges remain active in the graph
            # (no zeroing of other MDPs' edges). The global MWM jointly
            # optimizes across all remaining MDPs.
            # -------------------------------------------------------
            finalized_mdp_gis: set = set()
            finalized_all_gis: set = set()  # MDPs + their finalized partners

            for li in range(score_group_begin):
                gi = players_by_idx[li]
                if not matched[gi]:
                    continue
                if gi in finalized_all_gis:
                    continue

                # Add tiebreaker weights preferring higher-ranked residents
                # (matching bbpPairings' addend = playersByIndex.size())
                addend = num_local
                for rli in range(next_sg_begin - 1, score_group_begin - 1, -1):
                    rgi = players_by_idx[rli]
                    if matched[rgi]:
                        continue
                    bw = base_ew[rli][li] if li < len(base_ew[rli]) else 0
                    if bw:
                        bw += addend
                        _set_w(gi, rgi, bw)
                        addend += 1

                stable = _run_mwm()

                # Finalize the pairing (only if actually matched)
                match_gi = stable[gi]
                if match_gi != gi:
                    matched[match_gi] = True
                    _finalize_pair(gi, match_gi)
                    finalized_mdp_gis.add(gi)
                    finalized_all_gis.add(gi)
                    finalized_all_gis.add(match_gi)
                else:
                    # MDP couldn't be matched — unmark and push to next bracket
                    matched[gi] = False

            # -------------------------------------------------------
            # Phase 3: Remainder collection
            # -------------------------------------------------------
            remainder_local: List[int] = []  # Local indices
            remainder_pairs = 0

            for li in range(score_group_begin, next_sg_begin):
                gi = players_by_idx[li]
                # Skip players already finalized with MDPs
                partner = stable[gi]
                if partner != gi:
                    # Check if partner is from before score_group_begin (MDP)
                    is_mdp_partner = False
                    for mli in range(score_group_begin):
                        if players_by_idx[mli] == partner:
                            is_mdp_partner = True
                            break
                    if is_mdp_partner:
                        continue
                remainder_local.append(li)
                # Count pairs in the upper group
                if partner != gi and partner < gi:
                    remainder_pairs += 1

            if remainder_pairs > len(remainder_local):
                remainder_pairs = len(remainder_local) // 2

            # first_group_end = index into remainder_local separating
            # upper (paired) from lower (unpaired)
            first_group_end = remainder_pairs

            # -------------------------------------------------------
            # Phase 4: Exchange weight update
            # -------------------------------------------------------
            if len(remainder_local) >= 2:
                for oi, opp_li in enumerate(remainder_local):
                    opp_gi = players_by_idx[opp_li]
                    pri = 0
                    for pi_idx, player_li in enumerate(remainder_local):
                        if opp_li <= player_li:
                            break
                        player_gi = players_by_idx[player_li]
                        w = _exchange_weight(
                            player_li, opp_li, pri, remainder_pairs,
                        )
                        _set_w(player_gi, opp_gi, w)
                        pri += 1

                stable = _run_mwm()

                # Count exchanges needed
                exchange_count = 0
                for ri in range(first_group_end):
                    li = remainder_local[ri]
                    gi = players_by_idx[li]
                    partner = stable[gi]
                    # Is this upper-group player NOT paired internally?
                    paired_in_bracket = False
                    if partner != gi:
                        for rli in range(score_group_begin, next_sg_begin):
                            if players_by_idx[rli] == partner and partner > gi:
                                paired_in_bracket = True
                                break
                    if not paired_in_bracket:
                        exchange_count += 1

                # -------------------------------------------------------
                # Phase 5: Select lower players from upper group to exchange
                # -------------------------------------------------------
                exchanges_remaining = exchange_count
                pri = remainder_pairs

                for scan in range(first_group_end - 1, -1, -1):
                    if exchanges_remaining == 0:
                        break
                    pri -= 1
                    li = remainder_local[scan]
                    gi = players_by_idx[li]

                    # Is this player currently paired in bracket?
                    partner = stable[gi]
                    currently_paired = False
                    if partner != gi:
                        for rli in range(score_group_begin, next_sg_begin):
                            if players_by_idx[rli] == partner and partner > gi:
                                currently_paired = True
                                break

                    if currently_paired:
                        # Reduce weights to try to unpair
                        for oi in range(scan + 1, len(remainder_local)):
                            opp_li = remainder_local[oi]
                            opp_gi = players_by_idx[opp_li]
                            w = _exchange_weight(li, opp_li, pri, remainder_pairs)
                            if w:
                                w -= 1
                                _set_w(gi, opp_gi, w)

                        stable = _run_mwm()

                    # Check if now unpaired (exchanged)
                    partner = stable[gi]
                    is_exchange = True
                    if partner != gi:
                        for rli in range(score_group_begin, next_sg_begin):
                            if players_by_idx[rli] == partner and partner > gi:
                                is_exchange = False
                                break

                    if is_exchange:
                        exchanges_remaining -= 1

                    # Restore or finalize weights
                    for oi in range(scan + 1, len(remainder_local)):
                        opp_li = remainder_local[oi]
                        opp_gi = players_by_idx[opp_li]
                        if is_exchange:
                            # Zero base weights for this player's cross-group edges
                            if opp_li < len(base_ew) and li < len(base_ew[opp_li]):
                                base_ew[opp_li][li] = 0
                        w = _exchange_weight(li, opp_li, pri, remainder_pairs)
                        _set_w(gi, opp_gi, w)

                # -------------------------------------------------------
                # Phase 6: Select higher players from lower group to exchange
                # -------------------------------------------------------
                exchanges_remaining = exchange_count
                ri = remainder_pairs

                for scan in range(first_group_end, len(remainder_local)):
                    if exchanges_remaining <= 1:
                        break
                    li = remainder_local[scan]
                    gi = players_by_idx[li]

                    partner = stable[gi]
                    already_exchanged = False
                    if partner != gi:
                        for rli in range(score_group_begin, next_sg_begin):
                            if players_by_idx[rli] == partner and partner > gi:
                                already_exchanged = True
                                break

                    if not already_exchanged:
                        # Boost weights to try to pair internally
                        for oi in range(scan + 1, len(remainder_local)):
                            opp_li = remainder_local[oi]
                            opp_gi = players_by_idx[opp_li]
                            w = _exchange_weight(li, opp_li, ri, remainder_pairs)
                            if w:
                                w += 1
                                _set_w(gi, opp_gi, w)

                        stable = _run_mwm()

                    # Check if now paired internally (exchanged up)
                    partner = stable[gi]
                    is_exchange = False
                    if partner != gi:
                        for rli in range(score_group_begin, next_sg_begin):
                            if players_by_idx[rli] == partner and partner > gi:
                                is_exchange = True
                                break

                    if is_exchange:
                        exchanges_remaining -= 1
                        # Finalize: zero cross-group edges
                        for oi in range(len(remainder_local)):
                            opp_li = remainder_local[oi]
                            if opp_li == li:
                                continue
                            opp_gi = players_by_idx[opp_li]
                            if oi < scan:  # Before this player → same-group-only
                                if li < len(base_ew) and opp_li < len(base_ew[li]):
                                    base_ew[li][opp_li] = 0
                                elif opp_li < len(base_ew) and li < len(base_ew[opp_li]):
                                    base_ew[opp_li][li] = 0
                                _set_w(gi, opp_gi, 0)

                        # Zero next-bracket edges
                        for nli in range(next_sg_begin, num_local):
                            ngi = players_by_idx[nli]
                            if li < len(base_ew[nli]):
                                base_ew[nli][li] = 0
                            _set_w(gi, ngi, 0)

                    if not already_exchanged:
                        # Restore original weights
                        for oi in range(scan + 1, len(remainder_local)):
                            opp_li = remainder_local[oi]
                            opp_gi = players_by_idx[opp_li]
                            w = _exchange_weight(li, opp_li, ri, remainder_pairs)
                            _set_w(gi, opp_gi, w)

                    ri += 1

                # -------------------------------------------------------
                # Phase 7: Finalize exchange decisions
                # -------------------------------------------------------
                for pi_idx, player_li in enumerate(remainder_local):
                    player_gi = players_by_idx[player_li]
                    for oi in range(pi_idx + 1, len(remainder_local)):
                        opp_li = remainder_local[oi]
                        opp_gi = players_by_idx[opp_li]

                        p_partner = stable[player_gi]
                        o_partner = stable[opp_gi]

                        # Check if player is exchanged (not paired internally)
                        p_internal = False
                        if p_partner != player_gi and p_partner > player_gi:
                            for rli in range(score_group_begin, next_sg_begin):
                                if players_by_idx[rli] == p_partner:
                                    p_internal = True
                                    break

                        # Check if opponent is paired internally
                        o_internal = False
                        if o_partner != opp_gi and o_partner > opp_gi:
                            for rli in range(score_group_begin, next_sg_begin):
                                if players_by_idx[rli] == o_partner:
                                    o_internal = True
                                    break

                        if not p_internal or o_internal:
                            # One of them is exchanged → zero their mutual base weight
                            if opp_li < len(base_ew) and player_li < len(base_ew[opp_li]):
                                base_ew[opp_li][player_li] = 0

                        bw = base_ew[opp_li][player_li] if player_li < len(base_ew[opp_li]) else 0
                        _set_w(player_gi, opp_gi, bw)

            # -------------------------------------------------------
            # Phase 8: Pair upper-group players with opponents
            # -------------------------------------------------------
            for ri, player_li in enumerate(remainder_local):
                player_gi = players_by_idx[player_li]

                # Check if this player is in the upper group (paired internally)
                partner = stable[player_gi]
                in_upper = False
                if partner != player_gi and partner > player_gi:
                    for rli in range(score_group_begin, next_sg_begin):
                        if players_by_idx[rli] == partner:
                            in_upper = True
                            break

                if not in_upper:
                    continue

                # Set tiebreaker weights preferring higher-ranked opponents
                addend = 0
                for scan in reversed(range(len(remainder_local))):
                    opp_li = remainder_local[scan]
                    opp_gi = players_by_idx[opp_li]
                    if opp_li <= player_li or matched[opp_gi]:
                        continue
                    bw = base_ew[opp_li][player_li] if player_li < len(base_ew[opp_li]) else 0
                    if bw:
                        bw += addend
                        _set_w(player_gi, opp_gi, bw)
                    addend += 1

                stable = _run_mwm()

                # Finalize the pairing (only if actually matched)
                match_gi = stable[player_gi]
                if match_gi != player_gi:
                    matched[player_gi] = True
                    matched[match_gi] = True
                    _finalize_pair(player_gi, match_gi)

            # -------------------------------------------------------
            # Phase 9: Advance to next bracket
            # -------------------------------------------------------
            new_players_by_idx: List[int] = []
            new_score_group_begin = 0

            for li in range(num_local):
                gi = players_by_idx[li]
                if li < next_sg_begin and matched[gi]:
                    # Save the pair (guard against self-reference)
                    partner_gi = stable[gi]
                    if partner_gi == gi:
                        # Self-reference — not actually matched
                        new_players_by_idx.append(gi)
                        if li < next_sg_begin:
                            new_score_group_begin += 1
                        continue
                    p1 = sorted_players[gi]
                    p2 = sorted_players[partner_gi]
                    if p1.pairing_number < p2.pairing_number:
                        all_pairs.append((p1, p2))
                    else:
                        all_pairs.append((p2, p1))
                else:
                    # Add to next bracket
                    new_players_by_idx.append(gi)
                    if li < next_sg_begin:
                        new_score_group_begin += 1

            # Record floats for ALL pairs from this bracket
            if sg_iter > 0 and sg_iter <= len(sg_bounds):
                last_bracket_score = bracket_score
            else:
                last_bracket_score = bracket_score

            # Deduplicate pairs (each pair recorded once via gi < partner check)
            # Pairs were added when li < next_sg_begin and matched[gi]
            # But stable[gi] might point to a partner with gi' > gi that also
            # satisfies matched[gi']. Filter to only pairs where gi < partner_gi.
            seen = set()
            deduped: List[Tuple[DutchPlayer, DutchPlayer]] = []
            for p1, p2 in all_pairs:
                key = (min(p1.id, p2.id), max(p1.id, p2.id))
                if key not in seen:
                    seen.add(key)
                    deduped.append((p1, p2))
            all_pairs = deduped

            players_by_idx = new_players_by_idx
            score_group_begin = new_score_group_begin

            # Guard: if no score groups left and the player set is
            # identical (no pairs were made), break to prevent infinite loop
            if sg_iter >= len(sg_bounds) and new_players_by_idx == prev_players:
                break
            prev_players = list(players_by_idx)

        # ---------------------------------------------------------------
        # Record float directions for all pairs
        # ---------------------------------------------------------------
        for p1, p2 in all_pairs:
            if p1.score != p2.score:
                bracket_score = min(p1.score, p2.score)
                if p1.score > bracket_score:
                    p1.float_hist.append(FloatDir.DOWN)
                if p2.score > bracket_score:
                    p2.float_hist.append(FloatDir.DOWN)
                if p1.score < p2.score:
                    p1.float_hist.append(FloatDir.UP)
                elif p2.score < p1.score:
                    p2.float_hist.append(FloatDir.UP)

        # ---------------------------------------------------------------
        # Final remainder
        # ---------------------------------------------------------------
        paired_ids = {p.id for p1, p2 in all_pairs for p in (p1, p2)}
        final_remainder = [p for p in sorted_players if p.id not in paired_ids]

        if len(final_remainder) >= 2:
            bt_pairs = self._backtrack_match(final_remainder)
            if bt_pairs:
                all_pairs.extend(bt_pairs)
                paired_ids = {p.id for p1, p2 in all_pairs for p in (p1, p2)}
                final_remainder = [p for p in sorted_players if p.id not in paired_ids]

        all_pairs.sort(key=lambda pair: pair[0].pairing_number)
        return all_pairs, final_remainder

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
        2. Try all transpositions of S2 — score with _score_candidate (C5-C19)
        3. Try all exchanges between S1 and S2 with transpositions
        4. Pick the best candidate per B.8 (highest priority criteria first,
           then earlier in B.6 sequence for ties)
        5. Return (pairs, remainder)
        """
        if len(group) == 0:
            return [], []
        if len(group) == 1:
            return [], list(group)

        s1, s2 = self._split_scoregroup(group)

        if len(s1) == 0:
            return [], list(group)

        bracket_score = group[0].score  # homogeneous

        best_pairs = None
        best_score = None
        best_remainder: List[DutchPlayer] = []

        def _is_perfect(sc: tuple) -> bool:
            """Check if the candidate score is perfect (no violations)."""
            # sc = (c5, c6, ca1, ca2, c10, c11, c12, ..., c19)
            # Perfect = ca1..c19 all zero (C5/C6 same for same-size bracket)
            return all(v == 0 or v == 0.0 for v in sc[2:])

        # --- Step 1+2: Try all transpositions of S2,
        #     pick the candidate with best C5-C19 score. ---
        for s2_perm in self._generate_transpositions(s2, len(s1)):
            result = self._try_pair_s1_s2(s1, s2_perm)
            if result is not None:
                used = set(id(p) for p in s2_perm[:len(s1)])
                remainder = [p for p in s2 if id(p) not in used]
                score = self._score_candidate(result, remainder, bracket_score)
                if best_score is None or score < best_score:
                    best_score = score
                    best_pairs = result
                    best_remainder = remainder
                    if _is_perfect(score):
                        break  # Perfect pairing found

        if best_pairs is not None and _is_perfect(best_score):
            return best_pairs, best_remainder

        # --- Step 3: Try exchanges — compare against best from transpositions ---
        exchange_evals = 0
        for new_s1, new_s2 in self._generate_exchanges(s1, s2):
            for s2_perm in self._generate_transpositions(new_s2, len(new_s1)):
                result = self._try_pair_s1_s2(new_s1, s2_perm)
                if result is not None:
                    used = set(id(p) for p in s2_perm[:len(new_s1)])
                    remainder = [p for p in new_s2 if id(p) not in used]
                    score = self._score_candidate(
                        result, remainder, bracket_score
                    )
                    if best_score is None or score < best_score:
                        best_score = score
                        best_pairs = result
                        best_remainder = remainder
                        if _is_perfect(score):
                            break
                    exchange_evals += 1
                    if exchange_evals >= self.MAX_JOINT_EVALS:
                        break
            else:
                continue
            break  # break outer loop when inner broke
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
        Pair a heterogeneous bracket (C.04.3 §B.2-B.3, B.7, B.8).

        A heterogeneous bracket contains MDPs (moved-down players from the
        previous bracket) and resident players.

        Joint evaluation: for each MDP transposition, pair the remainder
        as a homogeneous bracket and score the combined candidate using
        ``_score_candidate`` (C5-C19).  This ensures MDP pairing choices
        consider remainder quality (floats, colour, PSD).
        """
        # Sort MDPs by pairing order (highest rank first = lowest PN)
        mdps_sorted = sorted(mdps, key=lambda p: p.pairing_number)
        residents_sorted = sorted(residents, key=lambda p: p.pairing_number)

        m1 = min(len(mdps_sorted), len(residents_sorted))
        if m1 == 0:
            # No MDPs can be paired — all go to remainder
            all_remainder = mdps_sorted + residents_sorted
            all_remainder.sort(key=lambda p: (-p.score, p.pairing_number))
            return self._pair_scoregroup(all_remainder)

        bracket_score = residents_sorted[0].score if residents_sorted else 0.0
        s1 = mdps_sorted[:m1]
        limbo = mdps_sorted[m1:]  # MDPs that can't be paired (double-float)
        s2 = residents_sorted

        best_all_pairs = None
        best_score = None
        best_final_remainder: List[DutchPlayer] = []
        joint_evals = 0

        # --- Try each MDP transposition → pair remainder → score jointly ---
        for s2_perm in self._generate_transpositions(s2, len(s1)):
            mdp_result = self._try_pair_s1_s2(s1, s2_perm)
            if mdp_result is None:
                continue

            joint_evals += 1

            paired_resident_ids = set()
            for _, r in mdp_result:
                paired_resident_ids.add(r.id)
            unpaired_residents = [
                p for p in residents_sorted if p.id not in paired_resident_ids
            ]

            if unpaired_residents:
                rem_pairs, rem_downfloaters = self._pair_scoregroup(
                    unpaired_residents
                )
            else:
                rem_pairs, rem_downfloaters = [], []

            all_pairs = mdp_result + rem_pairs
            all_downfloaters = list(limbo) + rem_downfloaters

            score = self._score_candidate(
                all_pairs, all_downfloaters, bracket_score
            )
            if best_score is None or score < best_score:
                best_score = score
                best_all_pairs = all_pairs
                best_final_remainder = all_downfloaters
                # Early exit if perfect (no C10-C19 violations)
                if all(v == 0 or v == 0.0 for v in score[2:]):
                    break

            if joint_evals >= self.MAX_JOINT_EVALS:
                break

        if best_all_pairs is not None:
            best_final_remainder.sort(
                key=lambda p: (-p.score, p.pairing_number)
            )
            return best_all_pairs, best_final_remainder

        # --- Fallback: Try reducing M1 (pair fewer MDPs) ---
        for reduce in range(1, len(s1)):
            fewer_s1 = s1[: len(s1) - reduce]
            unpaired_mdps = s1[len(s1) - reduce :]
            for s2_perm in self._generate_transpositions(s2, len(fewer_s1)):
                mdp_result = self._try_pair_s1_s2(fewer_s1, s2_perm)
                if mdp_result is not None:
                    paired_resident_ids = set()
                    for _, r in mdp_result:
                        paired_resident_ids.add(r.id)
                    unpaired_residents = [
                        p
                        for p in residents_sorted
                        if p.id not in paired_resident_ids
                    ]
                    if unpaired_residents:
                        rem_pairs, rem_downfloaters = self._pair_scoregroup(
                            unpaired_residents
                        )
                    else:
                        rem_pairs, rem_downfloaters = [], []

                    all_pairs = mdp_result + rem_pairs
                    final_remainder = (
                        list(limbo)
                        + list(unpaired_mdps)
                        + rem_downfloaters
                    )
                    final_remainder.sort(
                        key=lambda p: (-p.score, p.pairing_number)
                    )
                    return all_pairs, final_remainder

        # --- Fallback: treat everything as homogeneous ---
        all_players = mdps_sorted + residents_sorted
        all_players.sort(key=lambda p: (-p.score, p.pairing_number))
        return self._pair_scoregroup(all_players)

    # ------------------------------------------------------------------
    # C.7 — Completion-aware bracket pairing
    # ------------------------------------------------------------------

    def _can_complete(
        self,
        remainder: List[DutchPlayer],
        future_players: List[DutchPlayer],
    ) -> bool:
        """
        Check whether ``remainder`` + ``future_players`` can form a complete
        round-pairing (C.4/C.7).  Returns True if a valid matching exists
        where at most one player is left unpaired.

        Uses a fast greedy check first, then a full backtracking search
        only if the greedy check fails.
        """
        all_remaining = remainder + future_players
        n = len(all_remaining)
        if n <= 1:
            return True

        # Quick check: every player must be pairable with at least one other
        for i, p1 in enumerate(all_remaining):
            has_partner = False
            for j, p2 in enumerate(all_remaining):
                if i != j and self._can_pair(p1, p2):
                    has_partner = True
                    break
            if not has_partner and n % 2 == 0:
                # Even count — everyone must be paired; this player can't be
                return False

        # Full check via backtracking (at most ~100 players typically)
        result = self._backtrack_match(all_remaining)
        if result is None:
            return False
        # Must pair all but at most 1
        paired_count = len(result) * 2
        return paired_count >= n - 1

    def _pair_bracket_c7(
        self,
        mdps: List[DutchPlayer],
        residents: List[DutchPlayer],
        future_players: List[DutchPlayer],
        heterogeneous: bool,
    ) -> Tuple[List[Tuple[DutchPlayer, DutchPlayer]], List[DutchPlayer]]:
        """
        Pair a bracket with C.7 completion awareness.

        Checks whether the remainder from this bracket can form a valid
        matching together with all future players.  If not, retries with
        alternative candidates.
        """
        if heterogeneous:
            pairs, rem = self._pair_heterogeneous_bracket(mdps, residents)
        else:
            pairs, rem = self._pair_scoregroup(residents)

        # When rem is empty the current bracket paired everyone — accept.
        if not rem or self._can_complete(rem, future_players):
            return pairs, rem

        # --- Retry with completion-aware search ---
        logger.debug(
            "C.7: first candidate produces uncompletable remainder %s, retrying",
            [p.id for p in rem],
        )

        bracket_score = residents[0].score if residents else 0.0

        if heterogeneous:
            candidates = self._generate_hetero_candidates(mdps, residents)
        else:
            candidates = self._generate_homo_candidates(residents)

        best_pairs = None
        best_score = None
        best_rem: List[DutchPlayer] = []
        tried = 0

        for cand_pairs, cand_rem in candidates:
            tried += 1
            if tried > self.MAX_JOINT_EVALS:
                break
            if not self._can_complete(cand_rem, future_players):
                continue
            score = self._score_candidate(cand_pairs, cand_rem, bracket_score)
            if best_score is None or score < best_score:
                best_score = score
                best_pairs = cand_pairs
                best_rem = cand_rem

        if best_pairs is not None:
            best_rem.sort(key=lambda p: (-p.score, p.pairing_number))
            return best_pairs, best_rem

        # Completion not possible with structured approaches — fall back
        return pairs, rem

    def _generate_homo_candidates(
        self, group: List[DutchPlayer],
    ):
        """
        Yield (pairs, remainder) candidates for a homogeneous bracket.
        """
        if len(group) < 2:
            yield [], list(group)
            return

        s1, s2 = self._split_scoregroup(group)
        if not s1:
            yield [], list(group)
            return

        bracket_score = group[0].score

        # Transpositions
        for s2_perm in self._generate_transpositions(s2, len(s1)):
            result = self._try_pair_s1_s2(s1, s2_perm)
            if result is not None:
                used = set(id(p) for p in s2_perm[:len(s1)])
                remainder = [p for p in s2 if id(p) not in used]
                yield result, remainder

        # Exchanges
        for new_s1, new_s2 in self._generate_exchanges(s1, s2):
            for s2_perm in self._generate_transpositions(new_s2, len(new_s1)):
                result = self._try_pair_s1_s2(new_s1, s2_perm)
                if result is not None:
                    used = set(id(p) for p in s2_perm[:len(new_s1)])
                    remainder = [p for p in new_s2 if id(p) not in used]
                    yield result, remainder

    def _generate_hetero_candidates(
        self, mdps: List[DutchPlayer], residents: List[DutchPlayer],
    ):
        """
        Yield (pairs, remainder) candidates for a heterogeneous bracket.
        """
        mdps_sorted = sorted(mdps, key=lambda p: p.pairing_number)
        residents_sorted = sorted(residents, key=lambda p: p.pairing_number)

        m1 = min(len(mdps_sorted), len(residents_sorted))
        if m1 == 0:
            # Can't pair MDPs — yield homogeneous candidates for residents
            all_rem = mdps_sorted + residents_sorted
            all_rem.sort(key=lambda p: (-p.score, p.pairing_number))
            yield from self._generate_homo_candidates(all_rem)
            return

        bracket_score = residents_sorted[0].score if residents_sorted else 0.0
        s1 = mdps_sorted[:m1]
        limbo = mdps_sorted[m1:]

        for s2_perm in self._generate_transpositions(residents_sorted, len(s1)):
            mdp_result = self._try_pair_s1_s2(s1, s2_perm)
            if mdp_result is None:
                continue

            paired_resident_ids = {r.id for _, r in mdp_result}
            unpaired_residents = [
                p for p in residents_sorted if p.id not in paired_resident_ids
            ]

            if unpaired_residents:
                # Try each homogeneous candidate for the remainder
                for rem_pairs, rem_downfloaters in self._generate_homo_candidates(
                    unpaired_residents
                ):
                    all_pairs = mdp_result + rem_pairs
                    all_downfloaters = list(limbo) + rem_downfloaters
                    yield all_pairs, all_downfloaters
            else:
                yield mdp_result, list(limbo)

    def _pair_heterogeneous_with_lookahead(
        self,
        mdps: List[DutchPlayer],
        residents: List[DutchPlayer],
        next_sg: Optional[List[DutchPlayer]],
    ) -> Tuple[List[Tuple[DutchPlayer, DutchPlayer]], List[DutchPlayer]]:
        """
        Pair a heterogeneous bracket with 1-step lookahead.

        Compares two strategies:
        A) Pair max MDPs in this bracket (standard behaviour).
        B) Skip MDP pairings — pair only residents as homogeneous,
           let MDPs float to the next scoregroup.

        When a *next_sg* is available, each strategy's remainder is
        tentatively paired with the next scoregroup.  The combined quality
        across both brackets is compared using a unified metric that treats
        pairs from either bracket equally (total pairs, then colour
        violations, then PSD).
        """
        bracket_score = residents[0].score if residents else 0.0

        # --- Option A: standard heterogeneous pairing (pair max MDPs) ---
        pairs_a, rem_a = self._pair_heterogeneous_bracket(mdps, residents)

        # Without a next scoregroup, lookahead is impossible.
        if next_sg is None or len(mdps) < 1:
            return pairs_a, rem_a

        # --- Option B: skip MDP pairings, pair only residents ---
        res_pairs, res_rem = self._pair_scoregroup(list(residents))
        rem_b = sorted(
            list(mdps) + res_rem,
            key=lambda p: (-p.score, p.pairing_number),
        )

        # --- Simulate next bracket for both options ---
        # Option A next bracket
        if rem_a:
            next_pairs_a, next_rem_a = self._pair_heterogeneous_bracket(
                rem_a, list(next_sg),
            )
        else:
            next_pairs_a, next_rem_a = self._pair_scoregroup(list(next_sg))

        # Option B next bracket
        next_pairs_b, next_rem_b = self._pair_heterogeneous_bracket(
            rem_b, list(next_sg),
        )

        # --- Compare using unified quality across both brackets ---
        all_a = pairs_a + next_pairs_a
        all_b = res_pairs + next_pairs_b

        quality_a = self._combined_quality(all_a, next_rem_a)
        quality_b = self._combined_quality(all_b, next_rem_b)

        if quality_b < quality_a:
            return res_pairs, rem_b
        return pairs_a, rem_a

    @staticmethod
    def _combined_quality(
        pairs: List[Tuple["DutchPlayer", "DutchPlayer"]],
        final_remainder: List["DutchPlayer"],
    ) -> tuple:
        """
        Unified quality metric across multiple brackets.

        Returns a tuple (lower is better):
        (-total_pairs, color_violations, total_psd, remainder_count)
        """
        n_pairs = len(pairs)
        n_color = 0
        total_psd = 0.0
        for p1, p2 in pairs:
            pref1, pref2 = p1.color_preference, p2.color_preference
            if (pref1 != ColorPref.NONE and pref2 != ColorPref.NONE
                    and pref1 == pref2):
                n_color += 1
            total_psd += abs(p1.score - p2.score)
        return (-n_pairs, n_color, total_psd, len(final_remainder))

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
        bye_candidates: Optional[Set[int]] = None
        odd_count = len(all_players) % 2 == 1

        if odd_count and self.round_number == 1:
            # Round 1: deterministic bye selection (pre-select)
            bye_player = self._select_bye_player(all_players)
            if bye_player:
                all_players = [p for p in all_players if p.id != bye_player.id]
        elif odd_count and self.round_number >= 2:
            # Rounds >= 2: let MWM decide bye via completion bits.
            # Pass bye-eligible IDs; preliminary MWM inside
            # _pair_iterative_mwm will narrow them down.
            bye_eligible = [
                p for p in all_players
                if p.bye_count < self.max_byes_per_player
            ]
            if not bye_eligible:
                bye_eligible = list(all_players)
            bye_candidates = {p.id for p in bye_eligible}

        # --- Step 2: Build paired output ---
        if self.round_number == 1:
            eligible = sorted(all_players, key=lambda p: p.pairing_number)
            paired = self._generate_first_round_pairings(eligible)
            remainder: List[DutchPlayer] = []
            if len(eligible) % 2 == 1:
                remainder = [eligible[-1]]
        else:
            # Iterative bracket MWM: process brackets top-down with MWM
            paired, remainder = self._pair_iterative_mwm(
                all_players, bye_candidates=bye_candidates
            )

            # Fallback: if MWM leaves multiple unmatched, try backtracking
            if len(remainder) >= 2:
                logger.info(
                    "Round %d: iterative MWM left %d unpaired, "
                    "retrying with backtracking",
                    self.round_number, len(remainder),
                )
                bt_pairs = self._backtrack_match(all_players)
                if bt_pairs:
                    paired = bt_pairs
                    paired_ids = set()
                    for a, b in bt_pairs:
                        paired_ids.add(a.id)
                        paired_ids.add(b.id)
                    remainder = [p for p in all_players
                                 if p.id not in paired_ids]

            # Final greedy fallback
            if len(remainder) >= 2:
                logger.info(
                    "Round %d: no complete matching for %d players, "
                    "using greedy fallback",
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
                    remainder = [p for p in all_players
                                 if p.id not in paired_ids]

            # Assign bye to remaining unpaired player
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
