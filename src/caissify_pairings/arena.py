"""
Arena ("Rush") pairing and scoring.

A Rush event is not played in rounds. Players join, get paired as soon as
they are free, play, and are paired again; whoever has the most points at
the end wins. This module holds the two pure pieces of that format; the
caller (a server) owns everything live: who is connected, the clock of the
event, creating games and storing results.

Pairing — :func:`pair_waiting`
    Called every couple of seconds with the players who are waiting right
    now. Returns the best set of pairs:

    1. **As many games as possible.** Every waiting player is paired when
       the hard rules below allow it; an odd one out waits for the next
       call.
    2. **Close in the standings, then close in rating.** The cost of a pair
       is the gap in rank, weighted more heavily near the top (leaders
       should meet each other), plus the gap in rating.
    3. **Hard rules.** No immediate rematch of a player's last opponent,
       and nobody gets the same colour three games in a row.
    4. **Colours.** The player who has had White more often (or White last)
       gets Black.

    Large pools are split into rank-ordered chunks so the matching stays
    fast.

Scoring — :func:`score_game`
    Win 2, draw 1, loss 0. Two wins in a row put a player *on fire*: until
    their next non-win, wins are worth 4 and draws 2. A *berserk* player
    (half the clock, no increment) gets +1 for a win if they made at least
    :data:`BERSERK_MIN_MOVES` moves. A draw shorter than
    :data:`SHORT_DRAW_MOVES` moves scores nothing.

Standings — :func:`standings`
    Points, then performance rating, then fewer games played.

All functions are deterministic and never mutate their inputs.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

import networkx as nx

__all__ = [
    "BERSERK_MIN_MOVES",
    "SHORT_DRAW_MOVES",
    "FIRE_AFTER_WINS",
    "pair_waiting",
    "score_game",
    "performance_rating",
    "standings",
]

#: A berserk win earns its bonus only if the berserk player made at least this many moves.
BERSERK_MIN_MOVES = 7
#: A draw in fewer than this many moves (full moves, both sides) scores 0 for both players.
SHORT_DRAW_MOVES = 10
#: Consecutive wins needed to be "on fire" (double points).
FIRE_AFTER_WINS = 2

# One step in the standings costs this much rating difference at the bottom
# of the table, and up to (1 + _TOP_WEIGHT) times as much at the top.
_RANK_UNIT = 100
_TOP_WEIGHT = 3
# Pools larger than this are matched in rank-ordered chunks.
_MAX_POOL = 80


# ── Pairing ──────────────────────────────────────────────────────────────────

def pair_waiting(
    waiting: Sequence[dict],
    *,
    field_size: Optional[int] = None,
    max_pool: int = _MAX_POOL,
) -> List[dict]:
    """
    Pair the players who are waiting now.

    Each waiting player is a dict with:

    ``id``
        Any hashable, sortable id.
    ``rank``
        Current place in the standings (1 = leader).
    ``rating``
        Rating used for pairing (int).
    ``last_opponent``
        Id of the previous opponent in this event, or ``None``.
    ``colors``
        Colours of this player's games in the event so far, oldest first,
        as ``"w"``/``"b"`` (only the recent ones matter).

    ``field_size`` is the number of players in the standings (for weighting
    rank gaps); defaults to the largest rank among the waiting players.

    Returns ``[{"white_id": ..., "black_id": ...}, ...]``, best pairs first
    (the pair closest to the top of the standings first).
    """
    players = sorted(waiting, key=lambda p: (p["rank"], p["id"]))
    if len(players) < 2:
        return []
    size = max(field_size or 0, max(p["rank"] for p in players), 2)

    pairs: List[dict] = []
    if len(players) <= max_pool:
        pairs = _match(players, size)
    else:
        # Rank-ordered chunks; an unpaired player carries into the next chunk.
        carry: List[dict] = []
        for start in range(0, len(players), max_pool):
            chunk = carry + players[start:start + max_pool]
            got = _match(chunk, size)
            pairs.extend(got)
            used = {p["white_id"] for p in got} | {p["black_id"] for p in got}
            carry = [p for p in chunk if p["id"] not in used]

    by_id = {p["id"]: p for p in players}
    pairs.sort(key=lambda pr: (min(by_id[pr["white_id"]]["rank"], by_id[pr["black_id"]]["rank"]), pr["white_id"]))
    return pairs


def _match(players: List[dict], size: int) -> List[dict]:
    graph = nx.Graph()
    graph.add_nodes_from(p["id"] for p in players)
    costs = {}
    for i, a in enumerate(players):
        for b in players[i + 1:]:
            if not _allowed(a, b):
                continue
            costs[(a["id"], b["id"])] = _cost(a, b, size)
    if not costs:
        return []
    ceiling = max(costs.values()) + 1
    for (a, b), cost in costs.items():
        graph.add_edge(a, b, weight=ceiling - cost)
    matching = nx.max_weight_matching(graph, maxcardinality=True)
    by_id = {p["id"]: p for p in players}
    out = []
    for a, b in matching:
        white, black = _colours(by_id[a], by_id[b])
        out.append({"white_id": white["id"], "black_id": black["id"]})
    return out


def _cost(a: dict, b: dict, size: int) -> float:
    top = min(a["rank"], b["rank"])
    weight = 1 + _TOP_WEIGHT * (1 - (top - 1) / max(size - 1, 1))
    return abs(a["rank"] - b["rank"]) * _RANK_UNIT * weight + abs(int(a.get("rating") or 0) - int(b.get("rating") or 0))


def _forced(p: dict) -> Optional[str]:
    """The colour this player must get (after two games in a row with the other one)."""
    recent = list(p.get("colors") or [])[-2:]
    if recent == ["w", "w"]:
        return "b"
    if recent == ["b", "b"]:
        return "w"
    return None


def _allowed(a: dict, b: dict) -> bool:
    if a.get("last_opponent") == b["id"] or b.get("last_opponent") == a["id"]:
        return False
    fa, fb = _forced(a), _forced(b)
    return not (fa is not None and fa == fb)


def _colours(a: dict, b: dict):
    """(white, black) for a pair that passed :func:`_allowed`."""
    fa, fb = _forced(a), _forced(b)
    if fa == "w" or fb == "b":
        return a, b
    if fb == "w" or fa == "b":
        return b, a

    def balance(p):  # whites minus blacks
        cs = p.get("colors") or []
        return cs.count("w") - cs.count("b")

    ba, bb = balance(a), balance(b)
    if ba != bb:
        return (a, b) if ba < bb else (b, a)
    la = (a.get("colors") or [None])[-1]
    lb = (b.get("colors") or [None])[-1]
    if la != lb:
        if la == "w" or lb == "b":
            return b, a
        if lb == "w" or la == "b":
            return a, b
    # Nothing to choose: the higher-ranked player gets White (deterministic).
    return (a, b) if (a["rank"], str(a["id"])) <= (b["rank"], str(b["id"])) else (b, a)


# ── Scoring ──────────────────────────────────────────────────────────────────

def score_game(
    outcome: str,
    *,
    streak: int = 0,
    berserk: bool = False,
    own_moves: int = 0,
    game_moves: int = 0,
) -> Dict[str, object]:
    """
    Points for one finished game, from one player's side.

    ``outcome``
        ``"win"``, ``"draw"`` or ``"loss"``.
    ``streak``
        Consecutive wins in this event *before* this game.
    ``berserk``
        The player berserked this game.
    ``own_moves``
        Moves the player made.
    ``game_moves``
        Length of the game in full moves (used for the short-draw rule).

    Returns ``{"points", "streak", "on_fire", "berserk_bonus", "short_draw"}``
    where ``streak`` is the player's win streak *after* this game and
    ``on_fire`` says whether this game was played on fire (double points).
    """
    if outcome not in ("win", "draw", "loss"):
        raise ValueError(f"outcome must be win, draw or loss, not {outcome!r}")
    on_fire = streak >= FIRE_AFTER_WINS
    factor = 2 if on_fire else 1
    bonus = False
    short_draw = False
    if outcome == "win":
        bonus = bool(berserk and own_moves >= BERSERK_MIN_MOVES)
        points = 2 * factor + (1 if bonus else 0)
        new_streak = streak + 1
    elif outcome == "draw":
        short_draw = game_moves < SHORT_DRAW_MOVES
        points = 0 if short_draw else 1 * factor
        new_streak = 0
    else:
        points = 0
        new_streak = 0
    return {"points": points, "streak": new_streak, "on_fire": on_fire, "berserk_bonus": bonus, "short_draw": short_draw}


# ── Standings ────────────────────────────────────────────────────────────────

def performance_rating(opponent_ratings: Iterable[int], score: float) -> Optional[int]:
    """
    A simple performance rating: average opponent rating plus 400 × (wins −
    losses) / games, written with the game score (win 1, draw ½).

    Returns ``None`` before the first game.
    """
    ratings = [int(r) for r in opponent_ratings]
    if not ratings:
        return None
    n = len(ratings)
    return round(sum(ratings) / n + 400 * (2 * score - n) / n)


def standings(players: Iterable[dict]) -> List[dict]:
    """
    Order players for the table: points, then performance, then fewer games,
    then id. Each player dict needs ``id``, ``points`` and ``games``;
    ``performance`` may be ``None``. Returns new dicts with ``rank`` added
    (tied players share a rank only if every key is equal).
    """
    rows = [dict(p) for p in players]
    rows.sort(key=lambda p: (-p["points"], -(p.get("performance") if p.get("performance") is not None else -10_000), p["games"], str(p["id"])))
    rank = 0
    prev = None
    for i, p in enumerate(rows, start=1):
        key = (p["points"], p.get("performance"), p["games"])
        if key != prev:
            rank = i
            prev = key
        p["rank"] = rank
    return rows
