"""
Casual Swiss pairing engine.

A small, predictable Swiss-pairing algorithm intended for club nights,
online ladders, and tournaments that are not FIDE-rated. Prioritises
simplicity, determinism, and readability over FIDE C.04 conformance.

If you need FIDE A.7 conformance (e.g. to generate pairings for a rated
tournament), use ``system="dutch"`` instead — that engine is
cross-validated against ``bbpPairings`` to zero discrepancies on the A.7
benchmark.

Rules enforced by this engine:

1. Sort players by ``(-score, -rating, id)``.
2. **Round 1** uses the Dutch split: top half vs bottom half, paired in
   order (1 vs n/2+1, 2 vs n/2+2, …). This prevents the top two seeds
   from meeting in round 1.
3. **Rounds 2+** pair within each score group greedily; players who
   cannot be paired inside their group float down to the next lower
   score group.
4. **Byes.** If the player count is odd, the lowest-scored player who
   has not yet reached ``max_byes_per_player`` receives the bye. The
   ``bye_type`` (FIDE code) is configurable (default ``"F"`` —
   full-point bye).
5. **Colours.** Priority chain: perfect alternation > avoid 3-in-a-row
   streak > minimise colour imbalance. Ties broken by rating then id.
6. **Never mutates caller's player dicts.** All working state is kept
   local to the engine instance.

Output format matches :class:`caissify_pairings.base.BasePairingEngine`:
a list of ``{white_id, black_id, table, [bye], [bye_type], [float_type]}``
dicts.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from caissify_pairings.base import BasePairingEngine


class CasualSwissEngine(BasePairingEngine):
    """Simple, deterministic Swiss engine for casual tournaments."""

    name = "casual"

    def __init__(
        self,
        players: List[dict],
        previous_pairings: Set[Tuple[int, int]],
        round_number: int,
        total_rounds: int,
        *,
        max_byes_per_player: int = 1,
        bye_type: str = "F",
        **kwargs,
    ):
        super().__init__(
            players=players,
            previous_pairings=previous_pairings,
            round_number=round_number,
            total_rounds=total_rounds,
            **kwargs,
        )
        self.max_byes_per_player = max_byes_per_player
        self.bye_type = bye_type

    # ------------------------------------------------------------------ public

    def generate_pairings(self) -> List[dict]:
        """Return the pairings for ``self.round_number``."""
        # Work exclusively with shallow copies so we never touch the caller's
        # data. Any derived lists (color_hist etc.) we may mutate locally are
        # also copied below.
        players = [self._snapshot(p) for p in self.players]
        players.sort(key=lambda p: (-p["score"], -p.get("rating", 0), p["id"]))

        if self.round_number == 1:
            regular, byes = self._round_one(players)
        else:
            regular, byes = self._subsequent_rounds(players)

        return self._assign_tables(regular, byes, players)

    # ------------------------------------------------------------------ round 1

    def _round_one(self, players: List[dict]) -> Tuple[List[dict], List[dict]]:
        """FIDE Dutch half-split for the opening round."""
        remaining = list(players)
        byes: List[dict] = []

        if len(remaining) % 2 == 1:
            bye_player = remaining[-1]  # lowest-rated gets the round-1 bye
            byes.append(self._bye_pairing(bye_player))
            remaining = remaining[:-1]

        n = len(remaining)
        half = n // 2
        top = remaining[:half]
        bottom = remaining[half:]

        pairings: List[dict] = []
        for top_p, bot_p in zip(top, bottom):
            white, black = self._assign_colors(top_p, bot_p)
            pairings.append({
                "white_id": white["id"],
                "black_id": black["id"],
                "table": 0,
            })
        return pairings, byes

    # ----------------------------------------------------------- rounds 2..N

    def _subsequent_rounds(
        self, players: List[dict]
    ) -> Tuple[List[dict], List[dict]]:
        """Greedy pairing within score groups with downward floats."""
        regular: List[dict] = []
        byes: List[dict] = []

        # One pre-assigned bye if odd count.
        if len(players) % 2 == 1:
            bye_player = self._select_bye_player(players)
            if bye_player is not None:
                byes.append(self._bye_pairing(bye_player))
                players = [p for p in players if p["id"] != bye_player["id"]]

        # Bucket remaining players by score, descending.
        score_groups: Dict[float, List[dict]] = defaultdict(list)
        for p in players:
            score_groups[p["score"]].append(p)
        scores_desc = sorted(score_groups.keys(), reverse=True)

        carry: List[dict] = []  # downfloaters from higher brackets
        for score in scores_desc:
            group = carry + score_groups[score]
            carry = []
            paired, unpaired = self._pair_group(group)
            regular.extend(paired)
            carry.extend(unpaired)

        # Anything still unpaired after the last bracket: pair them with each
        # other if possible, otherwise award byes (respecting the per-player
        # cap where we can).
        leftover_pairs, leftover_unpaired = self._pair_group(carry)
        regular.extend(leftover_pairs)
        for p in leftover_unpaired:
            byes.append(self._bye_pairing(p))

        return regular, byes

    # ---------------------------------------------------------- group pairing

    def _pair_group(
        self, group: List[dict]
    ) -> Tuple[List[dict], List[dict]]:
        """
        Greedy pairing inside a single bracket.

        For each unmatched player (highest-standing first), pick the
        best-scoring valid partner from the rest of the group. "Valid"
        means no previous-round rematch. Partner preference score
        discourages — but does not forbid — repeating a float direction,
        which is the bug we're fixing vs the original simple_swiss.
        """
        pairings: List[dict] = []
        unpaired: List[dict] = []
        used: set[int] = set()

        for i, p1 in enumerate(group):
            if p1["id"] in used:
                continue

            best_j: Optional[int] = None
            best_score: Optional[Tuple[int, int, int]] = None

            for j in range(i + 1, len(group)):
                p2 = group[j]
                if p2["id"] in used:
                    continue
                if self._previously_paired(p1, p2):
                    continue

                # Lower tuple = preferred partner.
                #   0: float-direction penalty (repeat float = +1)
                #   1: colour-conflict penalty (both need the same colour = +1)
                #   2: standings distance (closer standings preferred)
                penalty = (
                    self._float_penalty(p1, p2),
                    self._colour_conflict_penalty(p1, p2),
                    j - i - 1,
                )
                if best_score is None or penalty < best_score:
                    best_score = penalty
                    best_j = j

            if best_j is None:
                unpaired.append(p1)
                continue

            p2 = group[best_j]
            white, black = self._assign_colors(p1, p2)
            pairing: dict[str, Any] = {
                "white_id": white["id"],
                "black_id": black["id"],
                "table": 0,
            }
            # Cross-score-group pair -> mark float direction.
            if p1["score"] != p2["score"]:
                higher, lower = (p1, p2) if p1["score"] > p2["score"] else (p2, p1)
                pairing["float_type"] = "down"
                higher["_float"] = "down"
                lower["_float"] = "up"
            pairings.append(pairing)
            used.add(p1["id"])
            used.add(p2["id"])

        return pairings, unpaired

    # -------------------------------------------------------------- byes

    def _select_bye_player(self, players: List[dict]) -> Optional[dict]:
        """Lowest-scored bye-eligible player (respecting max_byes_per_player)."""
        eligible = [
            p for p in players
            if p.get("bye_count", 0) < self.max_byes_per_player
        ]
        if not eligible:
            # Nobody eligible — return None; caller will leave one player
            # unpaired rather than forcibly overriding the cap.
            return None
        eligible.sort(key=lambda p: (p["score"], -p.get("rating", 0), p["id"]))
        return eligible[0]

    def _bye_pairing(self, player: dict) -> dict:
        return {
            "white_id": player["id"],
            "black_id": None,
            "table": 0,
            "bye": True,
            "bye_type": self.bye_type,
        }

    # ------------------------------------------------------------- colour

    @staticmethod
    def _would_streak(player: dict, colour: str) -> bool:
        hist = player.get("color_hist", [])
        return len(hist) >= 2 and hist[-1] == colour and hist[-2] == colour

    def _assign_colors(
        self, p1: dict, p2: dict
    ) -> Tuple[dict, dict]:
        """
        Choose who plays white and who plays black.

        Priority:
            1. Perfect alternation (one had white last, the other had black).
            2. Avoid creating a 3-in-a-row colour streak.
            3. Minimise each player's cumulative colour imbalance.
            4. Higher rating gets the colour they need more; final tiebreak
               by starting-number/id.
        """
        c1 = p1.get("color_hist", [])
        c2 = p2.get("color_hist", [])
        last1 = c1[-1] if c1 else None
        last2 = c2[-1] if c2 else None

        # 1. Perfect alternation.
        if last1 == "black" and last2 == "white":
            return p1, p2
        if last1 == "white" and last2 == "black":
            return p2, p1

        # 2. Avoid streaks when the alternation signal is weak.
        p1_w_streak = self._would_streak(p1, "white")
        p2_w_streak = self._would_streak(p2, "white")
        p1_b_streak = self._would_streak(p1, "black")
        p2_b_streak = self._would_streak(p2, "black")

        candidates: List[Tuple[Tuple[int, int, int, int], dict, dict]] = []
        for white, black in ((p1, p2), (p2, p1)):
            streak_cost = (
                self._would_streak(white, "white")
                + self._would_streak(black, "black")
            )

            # Cumulative imbalance after this assignment.
            w_hist = white.get("color_hist", [])
            b_hist = black.get("color_hist", [])
            w_imb = abs((w_hist.count("white") + 1) - w_hist.count("black"))
            b_imb = abs(b_hist.count("white") - (b_hist.count("black") + 1))

            # Tiebreak: higher rating first, lower id first.
            rating_pair = -(white.get("rating", 0) + black.get("rating", 0))
            id_pair = white["id"] + black["id"]

            candidates.append(
                ((streak_cost, w_imb + b_imb, rating_pair, id_pair), white, black)
            )

        candidates.sort(key=lambda c: c[0])
        _, white, black = candidates[0]
        return white, black

    # ------------------------------------------------------------- helpers

    def _previously_paired(self, a: dict, b: dict) -> bool:
        pair = tuple(sorted((a["id"], b["id"])))
        return pair in self.previous_pairings

    @staticmethod
    def _float_penalty(p1: dict, p2: dict) -> int:
        """Prefer not to repeat a float direction vs the last round."""
        penalty = 0
        hist1 = p1.get("float_history", [])
        hist2 = p2.get("float_history", [])
        if hist1 and hist1[-1] == "down":
            penalty += 1
        if hist2 and hist2[-1] == "up":
            penalty += 1
        return penalty

    @staticmethod
    def _colour_conflict_penalty(p1: dict, p2: dict) -> int:
        """If both players had the same colour last round, +1."""
        c1 = p1.get("color_hist", [])
        c2 = p2.get("color_hist", [])
        if c1 and c2 and c1[-1] == c2[-1]:
            return 1
        return 0

    @staticmethod
    def _snapshot(player: dict) -> dict:
        """
        Shallow copy + defensive copy of mutable list fields so the engine
        never leaks changes back into the caller's player dicts.
        """
        snap = dict(player)
        for key in ("color_hist", "float_history"):
            if key in snap and isinstance(snap[key], list):
                snap[key] = list(snap[key])
        return snap

    # ----------------------------------------------------- table assignment

    @staticmethod
    def _assign_tables(
        regular: List[dict], byes: List[dict], players: List[dict]
    ) -> List[dict]:
        """
        Regular games first (tables 1..k), then byes.
        Within each category, pairings led by the highest-standing player
        get lower table numbers.
        """
        lookup = {p["id"]: p for p in players}

        def standing(pid: int) -> Tuple[float, float, int, int]:
            p = lookup.get(pid, {})
            return (
                -p.get("score", 0),
                -p.get("rating", 0),
                p.get("starting_number", 1_000_000),
                pid,
            )

        def priority(pairing: dict) -> Tuple[float, float, int, int]:
            wid = pairing["white_id"]
            bid = pairing.get("black_id")
            if bid is None:
                return standing(wid)
            return min(standing(wid), standing(bid))

        regular_sorted = sorted(regular, key=priority)
        byes_sorted = sorted(byes, key=priority)

        table = 1
        for pairing in regular_sorted:
            pairing["table"] = table
            table += 1
        for pairing in byes_sorted:
            pairing["table"] = table
            table += 1

        return regular_sorted + byes_sorted


# Convention expected by the engine registry.
Engine = CasualSwissEngine
