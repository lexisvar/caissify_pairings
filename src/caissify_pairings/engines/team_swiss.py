"""
FIDE Swiss Team Pairing System (C.04.6, in force from 1 February 2026).

The pairing of one round of a team Swiss, following the text of
C.04.6 (2026) and the parts of C.04.1, C.04.2 and C.04.7 it imports.
``doc/TEAM_SWISS.md`` maps every article to this code and lists every
interpretation choice; the comments below cite the articles.

Outline (C.04.6 Article 3.3.2):

1. If the number of teams to pair is odd, the pairing-allocated bye
   goes to the team chosen by Article 3.4.
2. The top scoregroup plus a set of upfloaters (Article 3.5) forms a
   bracket, paired by Article 3.6. Repeat until every team is paired.
3. Colours by Article 4 ("first-team" rules 4.3.1-4.3.9), then the
   board colours of each match.

Quality criteria are minimised in priority order (C4 > C5 > C6 > C7 when
choosing upfloaters, C8 > C9 > C10 when pairing a bracket); among the
choices that reach every minimum, the one first in the regulation's
lexicographic order wins. The search is exact; see
:mod:`caissify_pairings.engines._team_matching` for how it stays fast.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import combinations, product
from typing import Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

import networkx as nx

from caissify_pairings.base import BasePairingEngine
from caissify_pairings.engines import _team_matching as tm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# History vocabulary
# ---------------------------------------------------------------------------

#: A match that was actually played (at least one player of each team present).
PLAYED = "played"
#: Adjourned match: a draw for pairing purposes (C.04.2 Article 3.1).
ADJOURNED = "adjourned"
#: Paired, the opponent did not show up.
FORFEIT_WIN = "forfeit_win"
#: Paired, this team did not show up.
FORFEIT_LOSS = "forfeit_loss"
#: Paired, neither team showed up.
DOUBLE_FORFEIT = "double_forfeit"
#: Pairing-allocated bye (C.04.6 Article 1.4).
PAB = "pab"
#: Full-point bye given by the arbiter (FIDE-deprecated, still blocks the PAB: C2).
FPB = "fpb"
#: Half-point bye.
HPB = "hpb"
#: Zero-point bye: absent, not paired (C.04.2 Article 3.3).
ZPB = "zpb"

_STATUS_ALIASES = {
    "played": PLAYED, "p": PLAYED, "game": PLAYED, "match": PLAYED,
    "adjourned": ADJOURNED, "x": ADJOURNED,
    "forfeit_win": FORFEIT_WIN, "+": FORFEIT_WIN, "+-": FORFEIT_WIN, "fw": FORFEIT_WIN,
    "forfeit_loss": FORFEIT_LOSS, "-": FORFEIT_LOSS, "-+": FORFEIT_LOSS, "fl": FORFEIT_LOSS,
    "double_forfeit": DOUBLE_FORFEIT, "--": DOUBLE_FORFEIT,
    "pab": PAB, "u": PAB, "bye": PAB, "pairing_allocated_bye": PAB,
    "fpb": FPB, "f": FPB, "full_point_bye": FPB,
    "hpb": HPB, "h": HPB, "half_point_bye": HPB,
    "zpb": ZPB, "z": ZPB, "absent": ZPB, "zero_point_bye": ZPB, "unpaired": ZPB, "not_paired": ZPB,
}

PLAYED_STATUSES = frozenset({PLAYED, ADJOURNED})
FORFEIT_STATUSES = frozenset({FORFEIT_WIN, FORFEIT_LOSS, DOUBLE_FORFEIT})

COLOUR_PREFERENCE_TYPES = ("A", "B", "none")


def normalise_status(value: Optional[str]) -> str:
    """Map a history status (or a TRF-like code) to the engine vocabulary."""
    if value is None:
        return PLAYED
    key = str(value).strip().lower()
    if key not in _STATUS_ALIASES:
        raise ValueError(f"Unknown team history status {value!r}")
    return _STATUS_ALIASES[key]


def _colour_letter(value) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in ("w", "white"):
        return "W"
    if v in ("b", "black"):
        return "B"
    if v in ("", "-", "none"):
        return None
    raise ValueError(f"Unknown colour {value!r}")


# ---------------------------------------------------------------------------
# Baku Acceleration (C.04.7 Article 1) for team competitions
# ---------------------------------------------------------------------------


def baku_virtual_points(round_number: int, total_rounds: int, win_points: float = 2.0) -> float:
    """
    Virtual points of group GA in ``round_number`` (C.04.7 Article 1.4).

    The accelerated rounds are the first half (rounded up) of the
    tournament; GA gets the points of a win (``win_points``, 2 MP by
    default) before the first half (rounded up) of them and half that
    before the rest. C.04.7 Example 2: 11 rounds, MP primary -> 2 in
    rounds 1-3, 1 in rounds 4-6.
    """
    if total_rounds < 1 or round_number < 1:
        return 0.0
    accelerated = -(-total_rounds // 2)
    full = -(-accelerated // 2)
    if round_number <= full:
        return float(win_points)
    if round_number <= accelerated:
        return float(win_points) / 2
    return 0.0


def baku_group_size(teams: int) -> int:
    """GA: the first half of the teams rounded up to an even number, 2 x ceil(N/4) (C.04.7 Article 1.2)."""
    if teams < 1:
        return 0
    return 2 * (-(-teams // 4))


def baku_records(total_rounds: int, teams: int, win_points: float = 2.0, group_size: Optional[int] = None) -> List[dict]:
    """
    The Baku schedule as TRF26 record-250 style dicts (virtual MP for
    team competitions), one per block of rounds with the same points.
    """
    ga = group_size if group_size is not None else baku_group_size(teams)
    out: List[dict] = []
    current = None
    for r in range(1, total_rounds + 1):
        pts = baku_virtual_points(r, total_rounds, win_points)
        if not pts:
            continue
        if current and current["mp"] == pts and current["last_round"] == r - 1:
            current["last_round"] = r
        else:
            current = {"mp": pts, "gp": 0.0, "first_round": r, "last_round": r, "first_team": 1, "last_team": ga}
            out.append(current)
    return out


# ---------------------------------------------------------------------------
# Colour preferences (C.04.6 Article 1.7)
# ---------------------------------------------------------------------------

NO_PREFERENCE: Tuple[Optional[str], int] = (None, 0)
MILD = 1
STRONG = 2


def colour_difference(colours: Sequence[str]) -> int:
    """CD (Article 1.6.2): Whites minus Blacks over the matches actually played."""
    return sum(1 for c in colours if c == "W") - sum(1 for c in colours if c == "B")


def type_a_preference(colours: Sequence[str]) -> Optional[str]:
    """Simple (Type A) colour preference, Article 1.7.1: 'W', 'B' or None."""
    if not colours:
        return None
    cd = colour_difference(colours)
    last_two = list(colours[-2:])
    if cd < -1 or (cd in (0, -1) and last_two == ["B", "B"]):
        return "W"
    if cd > 1 or (cd in (0, 1) and last_two == ["W", "W"]):
        return "B"
    return None


def type_b_preference(
    colours: Sequence[str],
    last_round: bool,
    keep_strong_in_last_round: bool = True,
) -> Tuple[Optional[str], int]:
    """
    Type B colour preference, Article 1.7.2: (colour, strength) with
    strength 2 = strong, 1 = mild, or (None, 0).

    The text says a team has no preference "when its CD is zero when
    pairing for the last round", while the strong rule also covers CD 0
    after two equal colours. ``keep_strong_in_last_round`` (default
    True) keeps that strong preference; False applies the "no
    preference" sentence literally. See doc/TEAM_SWISS.md.
    """
    if not colours:
        return NO_PREFERENCE
    cd = colour_difference(colours)
    strong = type_a_preference(colours)
    if strong is not None and not (last_round and cd == 0 and not keep_strong_in_last_round):
        return strong, STRONG
    if cd == -1:
        return "W", MILD
    if cd == 1:
        return "B", MILD
    if cd == 0 and not last_round:
        return ("W", MILD) if colours[-1] == "B" else ("B", MILD)
    return NO_PREFERENCE


# ---------------------------------------------------------------------------
# The regulation's orders (Articles 3.5.3-3.5.4 and 3.6.2-3.6.3)
# ---------------------------------------------------------------------------


def upfloater_sets(levels: Sequence[Sequence[int]], counts: Sequence[int]) -> Iterator[List[int]]:
    """
    Upfloater sets with ``counts[h]`` teams from ``levels[h]``, in the
    order of Articles 3.5.3-3.5.4: each set lists its teams by descending
    score, then ascending TPN (``levels`` are given highest score first,
    each in ascending TPN order), and the sets follow the lexicographic
    order of those lists.

    3.5.4 example: ``levels=[[2, 6, 8], [1, 3, 5]]``, ``counts=[2, 1]``
    gives {2,6,1} < {2,6,3} < {2,6,5} < {2,8,1} < ... < {6,8,5}.
    """
    factors = [combinations(levels[h], counts[h]) for h in range(len(levels)) if counts[h]]
    for combo in product(*factors):
        yield [i for part in combo for i in part]


def pairing_identifier(pairs: Iterable[Tuple[int, int]]) -> List[int]:
    """
    Article 3.6.2: the TPNs of the top members (the smaller TPN of each
    pair) in ascending order, followed by the TPNs of their partners.

    Example: 11-24 16-6 10-9 8-4 -> [4, 6, 9, 11, 8, 16, 10, 24].
    """
    ordered = sorted((min(a, b), max(a, b)) for a, b in pairs)
    return [a for a, _ in ordered] + [b for _, b in ordered]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass
class TeamSwissSettings:
    """
    Competition rules and interpretation switches for the team engine.

    Every field has the C.04.6 default. The ``*_mode`` / ``*_is_*``
    switches select between readings of an ambiguous article; the
    defaults and the alternatives are documented in doc/TEAM_SWISS.md.
    """

    #: Article 1.2: "mp" (default) or "gp".
    primary_score: str = "mp"
    #: Article 1.2.1 / 4.2.2: use the secondary score to choose the first-team.
    use_secondary_for_colours: bool = True
    #: Article 1.7: "A" (default), "B" or "none".
    colour_preferences: str = "A"
    #: Article 4.1: colour drawn by lot before round 1.
    initial_color: str = "white"
    #: Match points for a won / drawn / lost match (TRF26 record 362).
    match_points_win: float = 2.0
    match_points_draw: float = 1.0
    match_points_loss: float = 0.0
    #: Game points for a won / drawn / lost game (TRF26 record 162).
    game_points_win: float = 1.0
    game_points_draw: float = 0.5
    game_points_loss: float = 0.0
    #: Number of boards per match.
    boards: int = 4
    #: Board colours of the team with White on board 1 (TRF26 record 352).
    #: Default: alternate, odd boards White (Olympiad pattern).
    board_colours: Optional[str] = None
    #: Article 1.4: points of the pairing-allocated bye (default: a draw).
    bye_match_points: Optional[float] = None
    bye_game_points: Optional[float] = None
    #: C.04.7 Article 1: Baku Acceleration (MP primary only).
    accelerated: bool = False
    acceleration_group_size: Optional[int] = None
    #: TRF26 record 250 blocks: overrides ``accelerated`` when given.
    acceleration_records: Optional[List[dict]] = None
    #: Pairs or groups that must not meet (TRF26 record 260; absolute, like C1).
    forbidden_pairs: Optional[List] = None
    #: C6: "minimise" (default) or "parity". See doc/TEAM_SWISS.md.
    c6_mode: str = "minimise"
    #: C6: which scoregroup is "the following scoregroup". "score" (default):
    #: the scoregroup with the next lower score in the round (Article 1.3.1);
    #: when all its teams became or are upfloaters it is empty and C6 does not
    #: apply. "unpaired": the highest scoregroup among the teams still to be
    #: paired (the reading before 0.8.0). See doc/TEAM_SWISS.md, A4.
    c6_following: str = "score"
    #: Article 1.5 for C7/C10: is the PAB team a floater in the next round?
    bye_is_floater: bool = False
    #: Article 1.5 for C7/C10: does a paired-but-unplayed match make floaters?
    unplayed_is_floater: bool = False
    #: Article 1.7.2: keep a strong Type B preference with CD 0 in the last round.
    type_b_keep_strong_in_last_round: bool = True
    #: Article 3.4.3: count forfeited matches as "played".
    forfeits_count_as_played: bool = False
    #: C.04.7 Article 1.5: first-team (4.2.1) uses the pairing score.
    baku_score_for_colours: bool = True

    def __post_init__(self):
        self.primary_score = str(self.primary_score).lower()
        if self.primary_score not in ("mp", "gp"):
            raise ValueError("primary_score must be 'mp' or 'gp'")
        cp = self.colour_preferences
        cp = "none" if cp in (None, False, "", "none", "None", "NONE") else str(cp).upper()
        if cp not in ("A", "B", "none"):
            raise ValueError("colour_preferences must be 'A', 'B' or 'none'")
        self.colour_preferences = cp
        ic = str(self.initial_color).lower()
        if ic in ("w", "white"):
            self.initial_color = "white"
        elif ic in ("b", "black"):
            self.initial_color = "black"
        else:
            raise ValueError("initial_color must be 'white' or 'black'")
        if self.c6_mode not in ("minimise", "parity"):
            raise ValueError("c6_mode must be 'minimise' or 'parity'")
        if self.c6_following not in ("score", "unpaired"):
            raise ValueError("c6_following must be 'score' or 'unpaired'")
        if self.boards < 1:
            raise ValueError("boards must be at least 1")
        if self.board_colours:
            seq = str(self.board_colours).strip().upper()
            if not seq or any(c not in "WB" for c in seq):
                raise ValueError("board_colours must be a string of W and B")
            self.board_colours = seq
            self.boards = len(seq)
        if self.accelerated and self.primary_score != "mp" and not self.acceleration_records:
            # C.04.7 Article 1.4.4 note: with game points as the primary
            # score the Baku Acceleration cannot be used.
            raise ValueError("Baku Acceleration needs match points as the primary score (C.04.7 1.4.4)")

    @property
    def secondary_score(self) -> str:
        return "gp" if self.primary_score == "mp" else "mp"

    @property
    def board_sequence(self) -> str:
        """Colours board by board of the team with White on board 1."""
        if self.board_colours:
            seq = self.board_colours
            # A sequence written for the team with Black on board 1 is
            # read from that team's side (doc/TEAM_SWISS.md, record 352).
            if seq[0] == "B":
                seq = _invert(seq)
            return seq
        return "".join("W" if i % 2 == 0 else "B" for i in range(self.boards))

    @property
    def pab_match_points(self) -> float:
        return self.match_points_draw if self.bye_match_points is None else float(self.bye_match_points)

    @property
    def pab_game_points(self) -> float:
        if self.bye_game_points is not None:
            return float(self.bye_game_points)
        return self.boards * self.game_points_draw

    def default_points(self, status: str) -> Tuple[float, float]:
        """(MP, GP) awarded for an unplayed outcome when the history omits them."""
        n = self.boards
        if status == ADJOURNED:
            return self.match_points_draw, n * self.game_points_draw
        if status == FORFEIT_WIN or status == FPB:
            return self.match_points_win, n * self.game_points_win
        if status == HPB:
            return self.match_points_draw, n * self.game_points_draw
        if status == PAB:
            return self.pab_match_points, self.pab_game_points
        return 0.0, 0.0


def _invert(seq: str) -> str:
    return "".join("B" if c == "W" else "W" for c in seq)


_SETTING_FIELDS = set(TeamSwissSettings.__dataclass_fields__)


def settings_from_kwargs(kwargs: dict) -> TeamSwissSettings:
    """Build settings from engine keyword arguments (unknown keys are ignored)."""
    aliases = {
        "colour_preference": "colour_preferences",
        "color_preferences": "colour_preferences",
        "color_preference": "colour_preferences",
        "initial_colour": "initial_color",
        "board_colors": "board_colours",
        "bye_mp": "bye_match_points",
        "bye_gp": "bye_game_points",
        "use_secondary_for_colors": "use_secondary_for_colours",
    }
    clean = {}
    for k, v in kwargs.items():
        k = aliases.get(k, k)
        if k in _SETTING_FIELDS:
            clean[k] = v
    if "match_points" in kwargs and kwargs["match_points"] is not None:
        mp = kwargs["match_points"]
        clean.setdefault("match_points_win", mp.get("win", 2.0) if isinstance(mp, dict) else mp[0])
        clean.setdefault("match_points_draw", mp.get("draw", 1.0) if isinstance(mp, dict) else mp[1])
        clean.setdefault("match_points_loss", mp.get("loss", 0.0) if isinstance(mp, dict) else mp[2])
    return TeamSwissSettings(**clean)


# ---------------------------------------------------------------------------
# Team state
# ---------------------------------------------------------------------------


@dataclass
class TeamState:
    """A team as the pairing rules see it before the round."""

    id: int
    tpn: int
    name: str
    mp: float
    gp: float
    #: Pairing score: primary score plus virtual points (C.04.7 Article 1.5).
    score: float
    #: Secondary score (no virtual points).
    secondary: float
    #: First-board colours of the matches actually played, oldest first (1.6.1).
    colours: List[str] = field(default_factory=list)
    matches_played: int = 0
    bye_eligible: bool = True
    prev_floater: bool = False
    opponents: Set[int] = field(default_factory=set)
    pref: Optional[str] = None
    pref_strength: int = 0
    #: Score used to choose the first-team (4.2.1).
    colour_score: float = 0.0
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def cd(self) -> int:
        return colour_difference(self.colours)


def _num(value, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _team_tpn(t: dict) -> int:
    for key in ("tpn", "starting_number", "pairing_number"):
        if t.get(key):
            return int(t[key])
    return int(t["id"])


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class TeamSwissEngine(BasePairingEngine):
    """
    FIDE Swiss Team Pairing System, C.04.6 (2026).

    ``players`` is the list of **teams** (the base-class name is kept so
    the engine plugs into :func:`caissify_pairings.generate_pairings`).
    Each team dict::

        {
          "id": 7,                      # used in the output
          "tpn": 7,                     # Article 1.1 (default: starting_number, then id)
          "name": "Team",
          "strength": 2450,             # informative
          "players": [701, 702, ...],   # optional board order
          "history": [                  # one entry per earlier round
            {"round": 1, "opponent": 3, "color": "white",
             "type": "played", "mp": 2, "gp": 3.0},
            {"round": 2, "opponent": None, "type": "pab", "mp": 1, "gp": 2.0},
          ],
          "absent": False,              # not paired this round (C.04.2 3.3)
          "withdrawn": False,           # not paired any more (C.04.2 3.2)
        }

    ``color`` is the colour the team was scheduled to have on board 1.
    ``type`` is one of played, adjourned, forfeit_win, forfeit_loss,
    double_forfeit, pab, fpb, hpb, zpb (TRF letters U/F/H/Z/+/- work).
    Withdrawn and absent teams should still be passed (with the flag) so
    their opponents' float history can be worked out.

    ``previous_pairings`` adds (id, id) pairs of matches actually played
    to those in the histories (a paired but unplayed match does not stop
    a rematch: C.04.2 Article 3.5).

    Options: see :class:`TeamSwissSettings` (plus ``absent_teams`` and
    ``withdrawn_teams``, lists of ids).

    Output rows follow the common contract (``white_id`` is the team
    with White on board 1) and add ``white_tpn``/``black_tpn`` and
    ``boards`` (per-board colours). A bye row has ``bye_type`` "U" and
    the bye's ``match_points``/``game_points``. ``violations`` flags
    rows that break an absolute criterion because no legal pairing
    exists (Article 3.3.3: the Chief Arbiter decides).
    """

    name = "team_swiss"

    #: Upfloater sets examined by direct enumeration before switching
    #: to the exact weighted-matching search (same result, polynomial).
    UPFLOATER_ENUMERATION_BUDGET = 600

    def __init__(
        self,
        players: List[dict],
        previous_pairings: Optional[Set[Tuple[int, int]]] = None,
        round_number: int = 1,
        total_rounds: int = 9,
        **kwargs,
    ):
        super().__init__(
            players=players,
            previous_pairings=set(previous_pairings or ()),
            round_number=round_number,
            total_rounds=total_rounds,
            **kwargs,
        )
        if round_number < 1:
            raise ValueError("round_number must be >= 1")
        if total_rounds < round_number:
            raise ValueError("total_rounds must be >= round_number")
        self.settings = kwargs.pop("settings", None) or settings_from_kwargs(kwargs)
        self.absent_ids = set(kwargs.get("absent_teams") or ())
        self.withdrawn_ids = set(kwargs.get("withdrawn_teams") or ())
        self.trace: List[dict] = []
        self._raw = list(players)
        ids = [t["id"] for t in self._raw]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate team ids")
        tpns = [_team_tpn(t) for t in self._raw]
        if len(tpns) != len(set(tpns)):
            raise ValueError("Duplicate team pairing numbers (C.04.6 Article 1.1.1)")
        self._num_teams = max([len(self._raw)] + tpns) if self._raw else 0
        self.teams: Dict[int, TeamState] = {}
        self._build_states()

    # ------------------------------------------------------------------
    # Rounds and virtual points
    # ------------------------------------------------------------------

    @property
    def is_last_round(self) -> bool:
        return self.round_number == self.total_rounds

    @property
    def in_last_two_rounds(self) -> bool:
        """C7 and C10 do not apply in the last two rounds."""
        return self.round_number >= self.total_rounds - 1

    def virtual_points(self, tpn: int, round_number: int) -> float:
        """Virtual points added to the pairing score of team ``tpn`` in ``round_number``."""
        s = self.settings
        if s.acceleration_records:
            key = s.primary_score
            total = 0.0
            for rec in s.acceleration_records:
                if int(rec.get("first_round", 0)) <= round_number <= int(rec.get("last_round", 0)) and \
                        int(rec.get("first_team", 0)) <= tpn <= int(rec.get("last_team", 0)):
                    total += _num(rec.get(key))
            return total
        if not s.accelerated:
            return 0.0
        ga = s.acceleration_group_size or baku_group_size(self._num_teams)
        if tpn > ga:
            return 0.0
        return baku_virtual_points(round_number, self.total_rounds, s.match_points_win)

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _history(self, t: dict) -> Dict[int, dict]:
        out: Dict[int, dict] = {}
        s = self.settings
        for i, e in enumerate(t.get("history") or []):
            rnd = int(e.get("round") or (i + 1))
            if rnd >= self.round_number:
                continue
            status = normalise_status(e.get("type", e.get("status")))
            d_mp, d_gp = s.default_points(status)
            mp = _num(e.get("mp", e.get("match_points")), d_mp)
            gp = _num(e.get("gp", e.get("game_points")), d_gp)
            if status == ADJOURNED:
                # C.04.2 Article 3.1: a draw for pairing purposes only.
                mp, gp = d_mp, d_gp
            out[rnd] = {
                "round": rnd,
                "status": status,
                "opponent": e.get("opponent"),
                "colour": _colour_letter(e.get("color", e.get("colour"))),
                "mp": mp,
                "gp": gp,
            }
        return out

    def _build_states(self) -> None:
        s = self.settings
        histories = {t["id"]: self._history(t) for t in self._raw}
        tpn_of = {t["id"]: _team_tpn(t) for t in self._raw}
        primary_key = s.primary_score

        def primary_before(tid: int, rnd: int) -> float:
            return sum(h[primary_key] for r, h in histories[tid].items() if r < rnd)

        def pairing_score_at(tid: int, rnd: int) -> float:
            return primary_before(tid, rnd) + self.virtual_points(tpn_of[tid], rnd)

        prev = self.round_number - 1
        for t in self._raw:
            tid = t["id"]
            hist = histories[tid]
            mp = sum(h["mp"] for h in hist.values())
            gp = sum(h["gp"] for h in hist.values())
            if t.get("match_points") is not None:
                mp = float(t["match_points"])
            if t.get("game_points") is not None:
                gp = float(t["game_points"])
            primary = mp if primary_key == "mp" else gp
            secondary = gp if primary_key == "mp" else mp
            virtual = self.virtual_points(tpn_of[tid], self.round_number)
            colours: List[str] = []
            opponents: Set[int] = set()
            played = 0
            bye_eligible = True
            for rnd in sorted(hist):
                h = hist[rnd]
                st = h["status"]
                if st in PLAYED_STATUSES:
                    played += 1
                    if h["opponent"] is not None:
                        opponents.add(h["opponent"])
                    if h["colour"]:
                        colours.append(h["colour"])
                elif st in FORFEIT_STATUSES and s.forfeits_count_as_played:
                    played += 1
                # C2: a PAB, a forfeit win or a full-point bye rules out the PAB.
                if st in (PAB, FORFEIT_WIN, FPB):
                    bye_eligible = False
            if t.get("bye_eligible") is False:
                bye_eligible = False
            for a, b in self.previous_pairings:
                if a == tid:
                    opponents.add(b)
                elif b == tid:
                    opponents.add(a)

            # Article 1.5 / C7 / C10: did the team float in the previous round?
            if "floater_last_round" in t:
                prev_floater = bool(t["floater_last_round"])
            else:
                prev_floater = False
                h = hist.get(prev)
                if h is not None:
                    st = h["status"]
                    opp = h["opponent"]
                    counts = st in PLAYED_STATUSES or (st in FORFEIT_STATUSES and s.unplayed_is_floater)
                    if st == PAB and s.bye_is_floater:
                        prev_floater = True
                    elif counts and opp is not None and opp in histories:
                        prev_floater = pairing_score_at(tid, prev) != pairing_score_at(opp, prev)

            last_round = self.is_last_round
            if s.colour_preferences == "A":
                pref, strength = type_a_preference(colours), 0
                strength = STRONG if pref else 0
            elif s.colour_preferences == "B":
                pref, strength = type_b_preference(colours, last_round, s.type_b_keep_strong_in_last_round)
            else:
                pref, strength = None, 0

            score = primary + virtual
            self.teams[tid] = TeamState(
                id=tid,
                tpn=tpn_of[tid],
                name=t.get("name", ""),
                mp=mp,
                gp=gp,
                score=score,
                secondary=secondary,
                colours=colours,
                matches_played=played,
                bye_eligible=bye_eligible,
                prev_floater=prev_floater,
                opponents=opponents,
                pref=pref,
                pref_strength=strength,
                colour_score=score if s.baku_score_for_colours else primary,
                raw=t,
            )

    def _is_active(self, t: dict) -> bool:
        tid = t["id"]
        if t.get("absent") or t.get("withdrawn"):
            return False
        return tid not in self.absent_ids and tid not in self.withdrawn_ids

    def _forbidden(self) -> Set[FrozenSet[int]]:
        """Pairs that may not meet this round (TRF26 record 260 semantics)."""
        out: Set[FrozenSet[int]] = set()
        for item in self.settings.forbidden_pairs or []:
            if isinstance(item, dict):
                first = int(item.get("first_round", 1))
                last = int(item.get("last_round", self.total_rounds))
                if not first <= self.round_number <= last:
                    continue
                group = list(item.get("teams") or [])
            else:
                group = list(item)
            for a, b in combinations(group, 2):
                if a != b:
                    out.add(frozenset((a, b)))
        return out

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def generate_pairings(self) -> List[dict]:
        self.trace = []
        active = sorted(
            (self.teams[t["id"]] for t in self._raw if self._is_active(t)),
            key=lambda x: x.tpn,
        )
        ids = [t.id for t in active]
        forbidden = self._forbidden()
        adj: Dict[int, Set[int]] = {i: set() for i in ids}
        for a, b in combinations(ids, 2):
            ta = self.teams[a]
            if b in ta.opponents or a in self.teams[b].opponents:
                continue  # C1
            if frozenset((a, b)) in forbidden:
                continue
            adj[a].add(b)
            adj[b].add(a)
        self._adj = adj
        self._pm_cache: Dict[FrozenSet[int], bool] = {}
        self._relaxed: Set[FrozenSet[int]] = set()

        rows: List[dict] = []
        unpaired = set(ids)
        # Article 1.3.1: the scoregroups of the round (the PAB team included).
        self._round_scores = sorted({self.teams[i].score for i in ids}, reverse=True)
        bye_team: Optional[int] = None
        bye_violation = False
        if len(unpaired) % 2:
            bye_team, bye_violation = self._choose_pab(unpaired)
            unpaired.discard(bye_team)
        if not self._pm(unpaired):
            self._relax_c1(unpaired)

        pairs: List[Tuple[int, int]] = []
        while unpaired:
            top_score = max(self.teams[i].score for i in unpaired)
            residents = {i for i in unpaired if self.teams[i].score == top_score}
            upfloaters = self._select_upfloaters(unpaired, residents)
            bracket = residents | set(upfloaters)
            bracket_pairs = self._pair_bracket(bracket, set(upfloaters))
            pairs.extend(bracket_pairs)
            unpaired -= bracket

        for a, b in pairs:
            white, black = self.allocate_colours(self.teams[a], self.teams[b])
            row = {
                "white_id": white.id,
                "black_id": black.id,
                "table": 0,
                "white_tpn": white.tpn,
                "black_tpn": black.tpn,
                "boards": self.board_colours(white.id, black.id),
            }
            if frozenset((a, b)) in self._relaxed:
                row["violations"] = ["C1"]
            rows.append(row)
        rows = self._order_tables(rows)
        if bye_team is not None:
            bye_row = {
                "white_id": bye_team,
                "black_id": None,
                "table": len(rows) + 1,
                "bye": True,
                "bye_type": "U",
                "white_tpn": self.teams[bye_team].tpn,
                "match_points": self.settings.pab_match_points,
                "game_points": self.settings.pab_game_points,
            }
            if bye_violation:
                bye_row["violations"] = ["C2"]
            rows.append(bye_row)
        return rows

    # ------------------------------------------------------------------
    # Completion checks
    # ------------------------------------------------------------------

    def _pm(self, nodes: Iterable[int]) -> bool:
        return tm.has_perfect_matching(nodes, self._adj, self._pm_cache)

    def _relax_c1(self, unpaired: Set[int]) -> None:
        """
        Article 3.3.3: no legal pairing exists, the Chief Arbiter decides.
        The engine still returns a full round with the fewest rematches /
        forbidden pairs, and flags them (``violations``: C1).
        """
        nodes = sorted(unpaired, key=lambda i: self.teams[i].tpn)
        full = {i: set(nodes) - {i} for i in nodes}
        res = tm.min_cost_perfect_matching(
            nodes, full, lambda a, b: 0 if b in self._adj[a] else 1
        )
        assert res is not None
        for a, b in res[1].items():
            if b not in self._adj[a]:
                self._adj[a].add(b)
                self._adj[b].add(a)
                self._relaxed.add(frozenset((a, b)))
        self._pm_cache.clear()
        logger.warning("Round %d: no legal pairing exists (C1); %d pair(s) break it", self.round_number, len(self._relaxed))
        self.trace.append({"step": "relax_c1", "pairs": sorted(sorted(self.teams[x].tpn for x in p) for p in self._relaxed)})

    # ------------------------------------------------------------------
    # Article 3.4: the pairing-allocated bye
    # ------------------------------------------------------------------

    def bye_order(self, candidates: Iterable[int]) -> List[int]:
        """3.4.2 lowest score, 3.4.3 most matches played, 3.4.4 largest TPN."""
        return sorted(
            candidates,
            key=lambda i: (self.teams[i].score, -self.teams[i].matches_played, -self.teams[i].tpn),
        )

    def _choose_pab(self, unpaired: Set[int]) -> Tuple[int, bool]:
        order = self.bye_order(unpaired)
        for c in order:
            if self.teams[c].bye_eligible and self._pm(unpaired - {c}):
                self.trace.append({"step": "pab", "team": self.teams[c].tpn})
                return c, False
        # Article 3.3.3: no C2-compliant choice leaves a legal pairing.
        for c in order:
            if self._pm(unpaired - {c}):
                bad = not self.teams[c].bye_eligible
                if bad:
                    logger.warning("Round %d: no legal bye; team %s gets a second one (C2)", self.round_number, c)
                self.trace.append({"step": "pab", "team": self.teams[c].tpn, "violates_c2": bad})
                return c, bad
        eligible = [c for c in order if self.teams[c].bye_eligible]
        c = eligible[0] if eligible else order[0]
        self.trace.append({"step": "pab", "team": self.teams[c].tpn, "no_legal_pairing": True})
        return c, not self.teams[c].bye_eligible

    # ------------------------------------------------------------------
    # Article 3.5: upfloaters for the top scoregroup
    # ------------------------------------------------------------------

    def _c7_applies(self) -> bool:
        return not self.in_last_two_rounds

    def _select_upfloaters(self, unpaired: Set[int], residents: Set[int]) -> List[int]:
        lower = sorted(unpaired - residents, key=lambda i: (-self.teams[i].score, self.teams[i].tpn))
        if not lower:
            self.trace.append({"step": "bracket", "residents": self._tpns(residents), "upfloaters": []})
            return []
        self._last_selection = {}
        result = self._upfloaters_by_enumeration(unpaired, residents, lower)
        method = "enumeration"
        if result is None:
            result = self._upfloaters_by_matching(unpaired, residents, lower)
            method = "matching"
            next_group = self._following_group(residents, self._levels(lower))
            self._last_selection = {
                "c6": self._c6_value(unpaired, residents, result, next_group),
                "c7": self._c7_value(result),
            }
        step = {
            "step": "bracket",
            "residents": self._tpns(residents),
            "upfloaters": [self.teams[i].tpn for i in result],
            "method": method,
        }
        step.update(self._last_selection)
        self.trace.append(step)
        return result

    def _tpns(self, ids: Iterable[int]) -> List[int]:
        return sorted(self.teams[i].tpn for i in ids)

    def _levels(self, lower: List[int]) -> List[List[int]]:
        """Teams below the top scoregroup, by score (highest first), each level by TPN."""
        levels: List[List[int]] = []
        last = None
        for i in lower:
            sc = self.teams[i].score
            if sc != last:
                levels.append([])
                last = sc
            levels[-1].append(i)
        return levels

    def _legal_set(self, unpaired: Set[int], residents: Set[int], ups: Sequence[int]) -> bool:
        bracket = residents | set(ups)
        return self._pm(bracket) and self._pm(unpaired - bracket)

    def _following_group(self, residents: Set[int], levels: List[List[int]]) -> Set[int]:
        """
        C6: the unpaired teams of "the following scoregroup". With
        ``c6_following="score"`` that is the scoregroup with the next lower
        score of the round (Article 1.3.1); if all its teams became or are
        upfloaters of a bracket already paired, it is empty ("thus this
        scoregroup is now empty") and C6 does not apply. With
        ``"unpaired"``, the highest scoregroup among the unpaired teams.
        """
        if not levels:
            return set()
        if self.settings.c6_following == "unpaired":
            return set(levels[0])
        top = self.teams[next(iter(residents))].score
        below = [s for s in getattr(self, "_round_scores", []) if s < top]
        if below and self.teams[levels[0][0]].score != below[0]:
            return set()
        return set(levels[0])

    def _c6_value(self, unpaired: Set[int], residents: Set[int], ups: Sequence[int], next_group: Set[int]) -> int:
        """
        C6: upfloaters the bracket of the following scoregroup will need
        (with C1 and C3), given this set. 0 when that scoregroup is now
        empty. In "parity" mode, 0 when it needs no more than its parity
        requires, else 1.
        """
        rest = unpaired - residents - set(ups)
        group = next_group - set(ups)
        if not group:
            return 0
        need = self._next_bracket_upfloaters(rest, group)
        if self.settings.c6_mode == "parity":
            return 0 if need == len(group) % 2 else 1
        return need

    def _next_bracket_upfloaters(self, rest: Set[int], group: Set[int]) -> int:
        parity = len(group) % 2
        outside = rest - group
        if parity == 0:
            if self._pm(group) and self._pm(outside):
                return 0
        else:
            tried = 0
            reachable = set().union(*(self._adj[g] for g in group))
            for y in sorted(outside, key=lambda i: (-self.teams[i].score, self.teams[i].tpn)):
                if y not in reachable:
                    continue
                if self._pm(group | {y}) and self._pm(outside - {y}):
                    return 1
                tried += 1
                if tried >= 8:
                    break
        # Exact lower bound: the teams a maximum matching inside the group
        # leaves unpaired. If those teams (no two of which may meet) can
        # be completed with the teams outside, the bound is reached.
        inner = tm.maximum_matching(group, self._adj)
        bound = len(group) - len(inner)
        left = group - set(inner)
        if self._pm(outside | left):
            return bound
        nodes = sorted(rest, key=lambda i: self.teams[i].tpn)
        res = tm.min_cost_perfect_matching(
            nodes, self._adj, lambda a, b: 1 if (a in group) != (b in group) else 0
        )
        if res is None:  # pragma: no cover - C3 guarantees a completion
            return len(group)
        # Each cross edge has exactly one end in the group.
        return res[0]

    def _c7_value(self, ups: Sequence[int]) -> int:
        if not self._c7_applies():
            return 0
        return sum(1 for i in ups if self.teams[i].prev_floater)

    @staticmethod
    def _compositions(sizes: List[int], k: int) -> Iterator[Tuple[int, ...]]:
        """
        Upfloater counts per level with total k, in C5 order: fewest from
        the lowest level first, then the next lowest, and so on (this
        maximises the upfloaters' scores taken in ascending order).
        """
        h_count = len(sizes)

        def rec(h: int, remaining: int) -> Iterator[Dict[int, int]]:
            if h == 0:
                if remaining <= sizes[0]:
                    yield {0: remaining}
                return
            for c in range(0, min(sizes[h], remaining) + 1):
                for rest in rec(h - 1, remaining - c):
                    out = dict(rest)
                    out[h] = c
                    yield out

        for comp in rec(h_count - 1, k):
            yield tuple(comp[h] for h in range(h_count))

    def _upfloaters_by_enumeration(self, unpaired: Set[int], residents: Set[int], lower: List[int]) -> Optional[List[int]]:
        """
        Article 3.5 literally: sets in C4/C5 order, then lexicographic
        (3.5.3/3.5.4); the first legal one fixes C4/C5; within those,
        the minimum C6 then C7, first in order. Returns None when the
        budget runs out (the matching search takes over).
        """
        levels = self._levels(lower)
        sizes = [len(lv) for lv in levels]
        next_group = self._following_group(residents, levels)
        budget = self.UPFLOATER_ENUMERATION_BUDGET
        evals = 0
        c7_on = self._c7_applies()
        for k in range(len(residents) % 2, min(len(residents), len(lower)) + 1, 2):
            for comp in self._compositions(sizes, k):
                evals += 1
                if evals > budget:
                    return None
                best_key = None
                best_set: Optional[List[int]] = None
                legal_seen = 0
                # Lower bounds: the group parity for C6, forced floaters for C7.
                remaining_next = len(next_group) - comp[0] if next_group else 0
                if self.settings.c6_mode == "parity" or remaining_next == 0:
                    c6_lb = 0
                else:
                    c6_lb = remaining_next % 2
                c7_lb = 0
                if c7_on:
                    for h in range(len(levels)):
                        if comp[h]:
                            non_floaters = sum(1 for i in levels[h] if not self.teams[i].prev_floater)
                            c7_lb += max(0, comp[h] - non_floaters)
                for ups in upfloater_sets(levels, comp):
                    evals += 1
                    if evals > budget:
                        return None
                    if not self._legal_set(unpaired, residents, ups):
                        continue
                    legal_seen += 1
                    key = (self._c6_value(unpaired, residents, ups, next_group), self._c7_value(ups))
                    if best_key is None or key < best_key:
                        best_key, best_set = key, ups
                        best_rank = legal_seen
                        if key == (c6_lb, c7_lb):
                            break
                if best_set is not None:
                    self._last_selection = {"c6": best_key[0], "c7": best_key[1], "legal_set_rank": best_rank}
                    return best_set
        return None

    def _upfloaters_by_matching(self, unpaired: Set[int], residents: Set[int], lower: List[int]) -> List[int]:
        """
        The same choice as :meth:`_upfloaters_by_enumeration`, by
        weighted matching: one maximum-weight perfect matching of all the
        unpaired teams whose weights encode C4 > C5 > C6 > C7 exactly,
        then the lexicographically first set, member by member.
        """
        levels = self._levels(lower)
        level_of = {i: h for h, lv in enumerate(levels) for i in lv}
        next_group = self._following_group(residents, levels)
        n = len(unpaired)
        base = n + 1
        h_count = len(levels)
        w1 = base ** (h_count + 3)
        c7_on = self._c7_applies()

        def weight(a: int, b: int, use_c6: bool) -> int:
            ra, rb = a in residents, b in residents
            if ra and rb:
                return w1
            w = 0
            if ra or rb:
                x = b if ra else a
                w -= base ** (level_of[x] + 2)
                if c7_on and self.teams[x].prev_floater:
                    w -= 1
                return w
            if use_c6:
                la, lb = level_of[a], level_of[b]
                if (la == 0) != (lb == 0):
                    w -= base
            return w

        nodes = sorted(unpaired, key=lambda i: self.teams[i].tpn)

        def solve(forced_in: Set[int], forced_out: Set[int], use_c6: bool) -> Optional[Tuple[int, Dict[int, int]]]:
            g = nx.Graph()
            g.add_nodes_from(nodes)
            offset = w1 * 2
            for idx, a in enumerate(nodes):
                for b in self._adj[a]:
                    if b not in unpaired or self.teams[b].tpn <= self.teams[a].tpn:
                        continue
                    # forced_in: x must meet a resident; forced_out: x must not.
                    bad = False
                    for x, y in ((a, b), (b, a)):
                        if x in forced_in and y not in residents:
                            bad = True
                        if x in forced_out and y in residents:
                            bad = True
                    if bad:
                        continue
                    g.add_edge(a, b, weight=offset + weight(a, b, use_c6))
            mate = nx.max_weight_matching(g, maxcardinality=True)
            if 2 * len(mate) != len(nodes):
                return None
            total = 0
            out: Dict[int, int] = {}
            for a, b in mate:
                out[a], out[b] = b, a
                total += weight(a, b, use_c6)
            return total, out

        use_c6 = bool(next_group)
        best = solve(set(), set(), use_c6)
        assert best is not None  # C3 holds for the unpaired teams
        if self.settings.c6_mode == "parity":
            ups0 = [x for x in best[1] if x not in residents and best[1][x] in residents]
            group = next_group - set(ups0)
            cross = sum(1 for a in group if best[1][a] not in group and best[1][a] not in residents)
            if group and cross != len(group) % 2:
                use_c6 = False
                best = solve(set(), set(), use_c6)
                assert best is not None
        target, mate = best
        counts = [0] * h_count
        for x in lower:
            if mate[x] in residents:
                counts[level_of[x]] += 1
        chosen: List[int] = []
        forced_in: Set[int] = set()
        forced_out: Set[int] = set()
        for h in range(h_count):
            need = counts[h]
            members = levels[h]
            if not need:
                forced_out.update(members)
                continue
            picked = 0
            for idx, x in enumerate(members):
                left = len(members) - idx
                if picked == need:
                    forced_out.add(x)
                    continue
                if left == need - picked:
                    forced_in.add(x)
                    chosen.append(x)
                    picked += 1
                    continue
                trial = solve(forced_in | {x}, forced_out, use_c6)
                if trial is not None and trial[0] == target:
                    forced_in.add(x)
                    chosen.append(x)
                    picked += 1
                else:
                    forced_out.add(x)
        return chosen

    # ------------------------------------------------------------------
    # Article 3.6: pairing a bracket
    # ------------------------------------------------------------------

    def pair_cost_vector(self, a: TeamState, b: TeamState, upfloaters: Set[int]) -> Tuple[int, int, int]:
        """(C8, C9, C10) contribution of the pair a-b."""
        c8 = 1 if (a.pref is not None and a.pref == b.pref) else 0
        c9 = 0
        if self.settings.colour_preferences == "B":
            c9 = 1 if (a.pref is not None and a.pref == b.pref and a.pref_strength == STRONG and b.pref_strength == STRONG) else 0
        c10 = 0
        if not self.in_last_two_rounds:
            if a.id in upfloaters and b.prev_floater:
                c10 += 1
            if b.id in upfloaters and a.prev_floater:
                c10 += 1
        return c8, c9, c10

    def _pair_bracket(self, bracket: Set[int], upfloaters: Set[int]) -> List[Tuple[int, int]]:
        order = sorted(bracket, key=lambda i: self.teams[i].tpn)
        base = len(order) + 1

        def cost(a: int, b: int) -> int:
            c8, c9, c10 = self.pair_cost_vector(self.teams[a], self.teams[b], upfloaters)
            return (c8 * base + c9) * base + c10

        matcher = tm.LexFirstMatcher(order, self._adj, cost)
        res = matcher.solve()
        if res is None:  # pragma: no cover - the bracket was chosen legal
            raise RuntimeError("bracket has no legal pairing")
        opt, pairs = res
        c10 = opt % base
        c9 = (opt // base) % base
        c8 = opt // (base * base)
        if self.trace and self.trace[-1].get("step") == "bracket":
            self.trace[-1].update({
                "pairs": [[self.teams[a].tpn, self.teams[b].tpn] for a, b in pairs],
                "c8": c8, "c9": c9, "c10": c10,
            })
        return pairs

    # ------------------------------------------------------------------
    # Article 4: colours
    # ------------------------------------------------------------------

    def first_team(self, a: TeamState, b: TeamState) -> Tuple[TeamState, TeamState]:
        """Article 4.2: higher primary score, higher secondary score, smaller TPN."""
        if a.colour_score != b.colour_score:
            return (a, b) if a.colour_score > b.colour_score else (b, a)
        if self.settings.use_secondary_for_colours and a.secondary != b.secondary:
            return (a, b) if a.secondary > b.secondary else (b, a)
        return (a, b) if a.tpn < b.tpn else (b, a)

    def allocate_colours(self, a: TeamState, b: TeamState) -> Tuple[TeamState, TeamState]:
        """Return (team with White on board 1, team with Black on board 1)."""
        first, other = self.first_team(a, b)
        first_white = self._colour_rule(first, other)[1]
        return (first, other) if first_white else (other, first)

    def colour_rule(self, a: TeamState, b: TeamState) -> str:
        """The Article 4.3 rule that decides the colours of a-b (for explanations and tests)."""
        first, other = self.first_team(a, b)
        return self._colour_rule(first, other)[0]

    def _colour_rule(self, first: TeamState, other: TeamState) -> Tuple[str, bool]:
        s = self.settings
        # 4.3.1 both teams have yet to play a match.
        if not first.colours and not other.colours:
            initial_white = s.initial_color == "white"
            return "4.3.1", initial_white if first.tpn % 2 == 1 else not initial_white
        use_prefs = s.colour_preferences in ("A", "B")
        pf = first.pref if use_prefs else None
        po = other.pref if use_prefs else None
        # 4.3.2 only one team has a preference.
        if pf and not po:
            return "4.3.2", pf == "W"
        if po and not pf:
            return "4.3.2", po == "B"
        # 4.3.3 opposite preferences.
        if pf and po and pf != po:
            return "4.3.3", pf == "W"
        # 4.3.4 (Type B) only one strong preference.
        if s.colour_preferences == "B" and pf and po:
            sf, so = first.pref_strength == STRONG, other.pref_strength == STRONG
            if sf and not so:
                return "4.3.4", pf == "W"
            if so and not sf:
                return "4.3.4", po == "B"
        # 4.3.5 White to the lower colour difference.
        if first.cd != other.cd:
            return "4.3.5", first.cd < other.cd
        # 4.3.6 alternate from the most recent time they differed. Only
        # played matches count, unplayed rounds moved to the start
        # (C.04.2 Article 3.4), so the sequences are aligned at the end.
        for k in range(1, min(len(first.colours), len(other.colours)) + 1):
            cf, co = first.colours[-k], other.colours[-k]
            if cf != co:
                return "4.3.6", cf == "B"
        # 4.3.7 the first-team's preference.
        if pf:
            return "4.3.7", pf == "W"
        # 4.3.8 alternate the first-team's colour from its last played round.
        if first.colours:
            return "4.3.8", first.colours[-1] == "B"
        # 4.3.9 alternate the other team's colour.
        if other.colours:
            return "4.3.9", other.colours[-1] == "W"
        return "4.3.1", s.initial_color == "white"  # pragma: no cover

    def board_colours(self, white_id: int, black_id: int) -> List[dict]:
        """Per-board colours of a match, given the team with White on board 1."""
        out = []
        for i, c in enumerate(self.settings.board_sequence, 1):
            if c == "W":
                out.append({"board": i, "white_id": white_id, "black_id": black_id})
            else:
                out.append({"board": i, "white_id": black_id, "black_id": white_id})
        return out

    # ------------------------------------------------------------------
    # C.04.2 Article 3.6: order of the matches
    # ------------------------------------------------------------------

    def _order_tables(self, rows: List[dict]) -> List[dict]:
        def key(row: dict):
            a, b = self.teams[row["white_id"]], self.teams[row["black_id"]]
            hi = min(a, b, key=lambda t: (-t.score, t.tpn))
            return (-hi.score, -(a.score + b.score), hi.tpn)

        rows.sort(key=key)
        for i, row in enumerate(rows, 1):
            row["table"] = i
        return rows


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def team_swiss_pairings(
    teams: List[dict],
    round_number: int,
    total_rounds: int,
    previous_pairings: Optional[Set[Tuple[int, int]]] = None,
    **kwargs,
) -> List[dict]:
    """Pair one round of a team Swiss (C.04.6). See :class:`TeamSwissEngine`."""
    return TeamSwissEngine(
        players=teams,
        previous_pairings=previous_pairings or set(),
        round_number=round_number,
        total_rounds=total_rounds,
        **kwargs,
    ).generate_pairings()


# Engine registry alias - engines/__init__.py looks for `Engine`
Engine = TeamSwissEngine
