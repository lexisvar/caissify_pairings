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

        # C18: minimize score-sum of repeat downfloaters (2 ago)
        penalty = 0
        if _fb(p1, 2) == FloatDir.DOWN and p1.score > bracket_score:
            penalty += int(p1.score * 2)
        if _fb(p2, 2) == FloatDir.DOWN and p2.score > bracket_score:
            penalty += int(p2.score * 2)
        w |= max(0, max_score_int * 2 - penalty) << s
        s += SB

        # C17: minimize opponent-score of repeat upfloaters (prev)
        penalty = 0
        if _fb(p1, 1) == FloatDir.UP and p1.score < p2.score:
            penalty += int(p2.score * 2)
        if _fb(p2, 1) == FloatDir.UP and p2.score < p1.score:
            penalty += int(p1.score * 2)
        w |= max(0, max_score_int - penalty) << s
        s += SB

        # C16: minimize score-sum of repeat downfloaters (prev)
        penalty = 0
        if _fb(p1, 1) == FloatDir.DOWN and p1.score > bracket_score:
            penalty += int(p1.score * 2)
        if _fb(p2, 1) == FloatDir.DOWN and p2.score > bracket_score:
            penalty += int(p2.score * 2)
        w |= max(0, max_score_int * 2 - penalty) << s
        s += SB

        # C15: repeat upfloaters (2 ago)
        c15 = 1
        for p, opp in [(p1, p2), (p2, p1)]:
            if _fb(p, 2) == FloatDir.UP and p.score < opp.score:
                c15 = 0
        w |= c15 << s
        s += B

        # C14: repeat downfloaters (2 ago)
        c14 = 2
        for p in (p1, p2):
            if _fb(p, 2) == FloatDir.DOWN and p.score > bracket_score:
                c14 -= 1
        w |= max(0, c14) << s
        s += B

        # C13: repeat upfloaters (prev)
        c13 = 1
        for p, opp in [(p1, p2), (p2, p1)]:
            if _fb(p, 1) == FloatDir.UP and p.score < opp.score:
                c13 = 0
        w |= c13 << s
        s += B

        # C12: repeat downfloaters (prev)
        c12 = 2
        for p in (p1, p2):
            if _fb(p, 1) == FloatDir.DOWN and p.score > bracket_score:
                c12 -= 1
        w |= max(0, c12) << s
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

            for idx, sg in enumerate(scoregroups):
                sg_score = sg[0].score if sg else 0.0

                # Collect future players (all remaining scoregroups)
                future_players: List[DutchPlayer] = []
                for future_sg in scoregroups[idx + 1:]:
                    future_players.extend(future_sg)

                if remainder:
                    # Heterogeneous bracket: MDPs + residents
                    sg_pairs, remainder = self._pair_bracket_c7(
                        remainder, list(sg), future_players,
                        heterogeneous=True,
                    )
                else:
                    sg_pairs, remainder = self._pair_bracket_c7(
                        [], list(sg), future_players,
                        heterogeneous=False,
                    )

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
