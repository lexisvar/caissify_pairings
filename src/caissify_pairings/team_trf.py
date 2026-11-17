"""
TRF26 for team tournaments: read, rebuild the team matches, write.

TRF26 (FIDE Handbook C.02.03 Annexure A, applied from 1 September 2025)
keeps the TRF16 player section (record 001: board-by-board games) and
adds team records. This module reads and writes:

======  =========================================================
Record  Content
======  =========================================================
013     team and its players (legacy, "to be phased out"; read only)
142     number of rounds
152     initial-colour (W/B)
162     game points for win/draw/loss... (W, D, L, A, P, X)
192     encoded type of tournament (e.g. FIDE_TEAM_TYPEA_MP_GP_BAKU)
240     full-, half- or zero-point byes of teams, per round
250     accelerated rounds (fictitious match/game points)
260     prohibited pairings
300     line-up played out of the default order (or with empty boards)
310     team: pairing number, name, nickname, strength, MP, GP, rank, players
320     pairing-allocated byes: MP, GP and the team of every round
330     forfeited matches (+-, -+, --)
352     colour sequence of the boards (implicit number of boards)
362     match points for team win/draw/loss
======  =========================================================

Records 299 (abnormal points) and 801/802 (informative) are not read;
:func:`parse_team_trf` reports them in ``warnings``. TRF16 support in
:mod:`caissify_pairings.trf` is unchanged: that parser is reused for
the header and the 001 records.

How a team match is rebuilt from the player records: a team's players
with a game in the round, in roster order (record 310), sit on boards
1, 2, ... unless a 300 record gives the line-up. The team's colour is
the colour of its board 1 (C.04.6 Article 1.6.1); when board 1 is empty
it is derived from another board through the 352 sequence. Game points
are the players' points (record 162); match points follow from comparing
game points (record 362). A team with a 330 record forfeited (or won by
forfeit); a team with no game, no bye record and no PAB was absent.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from caissify_pairings.engines.team_swiss import (
    DOUBLE_FORFEIT,
    FORFEIT_LOSS,
    FORFEIT_WIN,
    FPB,
    HPB,
    PAB,
    PLAYED,
    ZPB,
    TeamSwissSettings,
)
from caissify_pairings.trf import TRFError, TRFFormatError, TRFParser, format_player_line

TEAM_RECORDS = ("013", "310", "320", "330", "300", "240", "250", "260", "352", "362")

DEFAULT_GAME_POINTS = {"W": 1.0, "D": 0.5, "L": 0.0, "A": 0.0}
DEFAULT_MATCH_POINTS = {"TW": 2.0, "TD": 1.0, "TL": 0.0}


# ---------------------------------------------------------------------------
# Record 192: encoded type of tournament (ETT26)
# ---------------------------------------------------------------------------


def parse_type_code(code: str) -> Dict:
    """
    Settings encoded in a record-192 team code (ETT26 "Swiss for teams").

    ``FIDE_TEAM`` is ``FIDE_TEAM_TYPEA_MP_GP`` and ``FIDE_TEAM_BAKU`` is
    ``FIDE_TEAM_TYPEA_MP_GP_BAKU``. Returns {} for codes that are not a
    FIDE team Swiss (``fide`` False for CUSTOM_TEAM_SWISS*).
    """
    c = (code or "").strip().upper()
    if not c or "TEAM" not in c:
        return {}
    if c in ("FIDE_TEAM", "FIDE_TEAM_BAKU"):
        c = c.replace("FIDE_TEAM", "FIDE_TEAM_TYPEA_MP_GP")
    if c.startswith("CUSTOM_TEAM_SWISS"):
        out: Dict = {"fide": False}
        if c.endswith("_GP"):
            out["primary_score"] = "gp"
        elif c.endswith("_MP"):
            out["primary_score"] = "mp"
        return out
    if not c.startswith("FIDE_TEAM_") or "ROUNDROBIN" in c:
        return {}
    parts = c[len("FIDE_TEAM_"):].split("_")
    out = {"fide": True, "colour_preferences": "none", "accelerated": False}
    if parts and parts[0] in ("TYPEA", "TYPEB"):
        out["colour_preferences"] = parts.pop(0)[-1]
    if parts and parts[-1] == "BAKU":
        out["accelerated"] = True
        parts.pop()
    scores = [p.lower() for p in parts if p in ("MP", "GP")]
    if scores:
        out["primary_score"] = scores[0]
        out["use_secondary_for_colours"] = len(scores) > 1
    return out


def type_code(settings: Dict) -> str:
    """The record-192 code for the given team settings."""
    cp = settings.get("colour_preferences", "A") or "none"
    primary = settings.get("primary_score", "mp").upper()
    secondary = "GP" if primary == "MP" else "MP"
    parts = ["FIDE_TEAM"]
    if cp in ("A", "B"):
        parts.append("TYPE" + cp)
    parts.append(primary)
    if settings.get("use_secondary_for_colours", True):
        parts.append(secondary)
    if settings.get("accelerated"):
        parts.append("BAKU")
    return "_".join(parts)


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------


def _int(text: str) -> Optional[int]:
    t = text.strip()
    if not t:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def _float(text: str) -> Optional[float]:
    t = text.strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _fields(line: str, start: int, width: int, stride: int) -> List[str]:
    out = []
    pos = start
    while pos < len(line):
        out.append(line[pos:pos + width])
        pos += stride
    return out


def _points_table(text: str, symbols) -> Dict[str, float]:
    """Parse 'W 1.0 D 0.5 L 0.0' / 'TW 2 TD 1 TL 0' (tokens, robust to spacing)."""
    tokens = text.split()
    out: Dict[str, float] = {}
    i = 0
    while i < len(tokens) - 1:
        sym = tokens[i].upper()
        val = _float(tokens[i + 1])
        if sym in symbols and val is not None:
            out[sym] = val
            i += 2
        else:
            i += 1
    return out


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def is_team_trf(text: str) -> bool:
    """True for a TRF26 team tournament: a 310 record or a team code in record 192."""
    for line in text.replace("\r", "\n").split("\n"):
        if line.startswith("310 "):
            return True
        if line.startswith("192 ") and "TEAM" in line.upper() and "ROUNDROBIN" not in line.upper():
            return True
    return False


def parse_team_trf(text: str, baku_250_order: str = "spec") -> Dict:
    """
    Parse a TRF26 team tournament.

    ``baku_250_order``: "spec" reads record 250 by the field table
    (match points in columns 5-8, game points in 10-13); "example"
    swaps them, as the TRF26 example line "250 00.0 02.0 ..." for an
    MP-based Baku tournament does. See doc/TEAM_SWISS.md.
    """
    if baku_250_order not in ("spec", "example"):
        raise ValueError("baku_250_order must be 'spec' or 'example'")
    normalised = text.replace("\t", " ").replace("\r\n", "\n").replace("\r", "\n")
    base = TRFParser(normalised).parse()
    t = base["tournament"]
    out: Dict = {
        "tournament": t,
        "players": base["players"],
        "teams": [],
        "pab": {"mp": None, "gp": None, "rounds": {}},
        "forfeits": [],
        "out_of_order": [],
        "byes": [],
        "acceleration": [],
        "prohibited": [],
        "legacy_013": False,
        "warnings": [],
        "format_version": "TRF26",
    }
    legacy: List[Dict] = []
    skipped: Dict[str, int] = {}
    for n, line in enumerate(normalised.split("\n"), 1):
        if len(line) < 3 or line.startswith("###"):
            continue
        code = line[:3]
        body = line[4:] if len(line) > 4 else ""
        try:
            if code == "142":
                v = _int(body)
                if v:
                    t["total_rounds"] = v
            elif code == "152":
                c = body.strip().upper()[:1]
                if c in ("W", "B"):
                    t["initial_color"] = "white" if c == "W" else "black"
            elif code == "162":
                t["game_points"] = _points_table(body, {"W", "D", "L", "A", "P", "X"})
            elif code == "192":
                t["type_code"] = body.strip()
            elif code in ("202", "212"):
                t["tie_breaks_" + code] = body.strip()
            elif code == "352":
                t["board_colours"] = body.strip().upper()
            elif code == "362":
                t["match_points"] = _points_table(body, {"TW", "TD", "TL"})
            elif code == "013":
                name = line[4:36].strip()
                ids = [x for x in (_int(f) for f in _fields(line, 36, 4, 5)) if x]
                legacy.append({"name": name, "players": ids})
            elif code == "310":
                out["teams"].append(_parse_310(line))
            elif code == "320":
                out["pab"]["mp"] = _float(line[4:8])
                out["pab"]["gp"] = _float(line[9:13])
                for k, f in enumerate(_fields(line, 14, 3, 4), 1):
                    v = _int(f)
                    if v:
                        out["pab"]["rounds"][k] = v
            elif code == "330":
                kind = line[4:6]
                if kind not in ("+-", "-+", "--"):
                    raise ValueError(f"bad forfeit type {kind!r}")
                out["forfeits"].append({
                    "type": kind, "round": _int(line[7:10]), "white": _int(line[11:14]), "black": _int(line[15:18]),
                })
            elif code == "300":
                players = [(_int(f) or 0) for f in _fields(line, 16, 4, 5)]
                while players and players[-1] == 0 and len(line.rstrip()) <= 16 + 5 * (len(players) - 1):
                    players.pop()
                out["out_of_order"].append({
                    "round": _int(line[4:7]), "team": _int(line[8:11]), "opponent": _int(line[12:15]), "players": players,
                })
            elif code == "240":
                kind = line[4:5].upper()
                teams = [x for x in (_int(f) for f in _fields(line, 10, 4, 5)) if x]
                out["byes"].append({"type": kind, "round": _int(line[6:9]), "teams": teams})
            elif code == "250":
                mp, gp = _float(line[4:8]) or 0.0, _float(line[9:13]) or 0.0
                if baku_250_order == "example":
                    mp, gp = gp, mp
                out["acceleration"].append({
                    "mp": mp, "gp": gp,
                    "first_round": _int(line[14:17]), "last_round": _int(line[18:21]),
                    "first_team": _int(line[22:26]), "last_team": _int(line[27:31]),
                })
            elif code == "260":
                teams = [x for x in (_int(f) for f in _fields(line, 12, 4, 5)) if x]
                out["prohibited"].append({
                    "first_round": _int(line[4:7]), "last_round": _int(line[8:11]), "teams": teams,
                })
            elif code in ("299", "801", "802"):
                skipped[code] = skipped.get(code, 0) + 1
        except (ValueError, IndexError) as exc:
            raise TRFFormatError(f"Line {n}: bad {code} record: {exc}") from exc

    for code, count in sorted(skipped.items()):
        what = "abnormal points" if code == "299" else "informative"
        out["warnings"].append(f"{count} record(s) {code} ({what}) not read")
    if not out["teams"] and legacy:
        # Record 013 has no pairing number: the order of the records is used.
        out["legacy_013"] = True
        for i, lt in enumerate(legacy, 1):
            out["teams"].append({
                "tpn": i, "name": lt["name"], "nickname": "", "strength": None,
                "match_points": None, "game_points": None, "rank": None, "players": lt["players"],
            })
    if not out["teams"]:
        raise TRFFormatError("No team records (310 or 013) found")
    tpns = [tm["tpn"] for tm in out["teams"]]
    if len(tpns) != len(set(tpns)):
        raise TRFFormatError("Duplicate team pairing numbers in 310 records")
    _check_250(out)
    return out


def _parse_310(line: str) -> Dict:
    tpn = _int(line[4:7])
    if tpn is None:
        raise ValueError("missing team pairing number")
    strength = _float(line[47:53])
    return {
        "tpn": tpn,
        "name": line[8:40].strip(),
        "nickname": line[41:46].strip(),
        "strength": strength,
        "match_points": _float(line[54:60]),
        "game_points": _float(line[61:67]),
        "rank": _int(line[68:71]),
        "players": [x for x in (_int(f) for f in _fields(line, 73, 4, 5)) if x],
    }


def _check_250(parsed: Dict) -> None:
    settings = parse_type_code(parsed["tournament"].get("type_code", ""))
    if settings.get("primary_score", "mp") != "mp":
        return
    for rec in parsed["acceleration"]:
        if not rec["mp"] and rec["gp"]:
            parsed["warnings"].append(
                "Record 250 gives fictitious game points but no match points in an MP tournament; "
                "if it follows the TRF26 example layout, read it with baku_250_order='example'"
            )
            return


# ---------------------------------------------------------------------------
# Rebuilding team matches
# ---------------------------------------------------------------------------


def settings_from_trf(parsed: Dict) -> Dict:
    """Engine options from the tournament section (records 142-362)."""
    t = parsed["tournament"]
    s: Dict = {}
    code = parse_type_code(t.get("type_code", ""))
    for key in ("colour_preferences", "primary_score", "use_secondary_for_colours", "accelerated"):
        if key in code:
            s[key] = code[key]
    gp = dict(DEFAULT_GAME_POINTS)
    gp.update(t.get("game_points") or {})
    mp = dict(DEFAULT_MATCH_POINTS)
    mp.update(t.get("match_points") or {})
    s["game_points_win"], s["game_points_draw"], s["game_points_loss"] = gp["W"], gp["D"], gp["L"]
    s["match_points_win"], s["match_points_draw"], s["match_points_loss"] = mp["TW"], mp["TD"], mp["TL"]
    seq = t.get("board_colours")
    if seq:
        s["board_colours"] = seq
    else:
        s["boards"] = _infer_boards(parsed)
    if t.get("initial_color"):
        s["initial_color"] = t["initial_color"]
    pab = parsed["pab"]
    if pab.get("mp") is not None:
        s["bye_match_points"] = pab["mp"]
    if pab.get("gp") is not None:
        s["bye_game_points"] = pab["gp"]
    if parsed["acceleration"]:
        # TRF26: 250 records override the method named in record 192.
        s["acceleration_records"] = [dict(r) for r in parsed["acceleration"]]
        s["accelerated"] = False
    if parsed["prohibited"]:
        s["forbidden_pairs"] = [
            {"teams": list(p["teams"]), "first_round": p["first_round"] or 1, "last_round": p["last_round"] or 9999}
            for p in parsed["prohibited"]
        ]
    return s


def _infer_boards(parsed: Dict) -> int:
    best = 0
    for o in parsed["out_of_order"]:
        best = max(best, len(o["players"]))
    if best:
        return best
    team_of = _team_of_player(parsed)
    counts: Dict[Tuple[int, int], int] = {}
    for p in parsed["players"]:
        tid = team_of.get(p["starting_number"])
        for rnd, r in p["results"].items():
            if r.get("opponent") is not None or r.get("result") == "+":
                counts[(tid, rnd)] = counts.get((tid, rnd), 0) + 1
    return max(counts.values()) if counts else 4


def _team_of_player(parsed: Dict) -> Dict[int, int]:
    out = {}
    for tm in parsed["teams"]:
        for pid in tm["players"]:
            out[pid] = tm["tpn"]
    return out


def _game_value(code: str, gp: Dict[str, float]) -> float:
    c = (code or "").upper()
    if c in ("1", "+", "W"):
        return gp["W"]
    if c in ("=", "D"):
        return gp["D"]
    if c in ("0", "L"):
        return gp["L"]
    if c == "-":
        return gp.get("A", 0.0)
    return 0.0


def team_rounds(parsed: Dict, warnings: Optional[List[str]] = None) -> Dict[int, Dict[int, Dict]]:
    """
    Every team's outcome in every round: {round: {tpn: entry}} with entry
    {"opponent", "colour" (board 1: "white"/"black"/None), "type", "mp", "gp"}.
    Teams with nothing in a round are absent (type "zpb").
    """
    settings = settings_from_trf(parsed)
    gp_table = dict(DEFAULT_GAME_POINTS)
    gp_table.update(parsed["tournament"].get("game_points") or {})
    st = TeamSwissSettings(**{k: v for k, v in settings.items() if k in TeamSwissSettings.__dataclass_fields__ and k != "accelerated"})
    seq = st.board_sequence
    boards = st.boards
    team_of = _team_of_player(parsed)
    roster_index = {}
    for tm in parsed["teams"]:
        for i, pid in enumerate(tm["players"]):
            roster_index[pid] = i
    tpns = [tm["tpn"] for tm in parsed["teams"]]
    total = parsed["tournament"].get("total_rounds") or 0
    for p in parsed["players"]:
        if p["results"]:
            total = max(total, max(p["results"]))
    for rnd in parsed["pab"]["rounds"]:
        total = max(total, rnd)
    oodo = {(o["round"], o["team"]): o for o in parsed["out_of_order"]}
    forfeits = {}
    for f in parsed["forfeits"]:
        forfeits[(f["round"], f["white"])] = f
        forfeits[(f["round"], f["black"])] = f
    byes = {}
    for b in parsed["byes"]:
        for tpn in b["teams"]:
            byes[(b["round"], tpn)] = b["type"]
    out: Dict[int, Dict[int, Dict]] = {}
    for rnd in range(1, total + 1):
        games: Dict[int, List[Tuple[int, Dict]]] = {tpn: [] for tpn in tpns}
        for p in parsed["players"]:
            r = p["results"].get(rnd)
            tid = team_of.get(p["starting_number"])
            if r is None or tid is None:
                continue
            # A board won by forfeit counts (the player was present); a
            # player with no opponent and no win did not play.
            if r.get("opponent") is None and r.get("result") != "+":
                continue
            games[tid].append((p["starting_number"], r))
        entries: Dict[int, Dict] = {}
        for tpn in tpns:
            f = forfeits.get((rnd, tpn))
            if f is not None:
                white = f["white"] == tpn
                opp = f["black"] if white else f["white"]
                if f["type"] == "--":
                    status = DOUBLE_FORFEIT
                elif (f["type"] == "+-") == white:
                    status = FORFEIT_WIN
                else:
                    status = FORFEIT_LOSS
                if status == FORFEIT_WIN:
                    pts = sum(_game_value(r.get("result"), gp_table) for _, r in games[tpn])
                    gp_val = pts if games[tpn] else boards * gp_table["W"]
                    mp_val = st.match_points_win
                else:
                    gp_val, mp_val = 0.0, 0.0
                entries[tpn] = {"opponent": opp, "colour": "white" if white else "black", "type": status,
                                "mp": mp_val, "gp": gp_val}
                continue
            if parsed["pab"]["rounds"].get(rnd) == tpn:
                entries[tpn] = {"opponent": None, "colour": None, "type": PAB,
                                "mp": st.pab_match_points, "gp": st.pab_game_points}
                continue
            bye = byes.get((rnd, tpn))
            if bye in ("F", "H", "Z"):
                status = {"F": FPB, "H": HPB, "Z": ZPB}[bye]
                mp_val, gp_val = st.default_points(status)
                entries[tpn] = {"opponent": None, "colour": None, "type": status, "mp": mp_val, "gp": gp_val}
                continue
            if not games[tpn]:
                entries[tpn] = {"opponent": None, "colour": None, "type": ZPB, "mp": 0.0, "gp": 0.0}
                continue
            opponents = {team_of.get(r["opponent"]) for _, r in games[tpn] if r.get("opponent") is not None}
            opponents.discard(None)
            o = oodo.get((rnd, tpn))
            if o is not None and o.get("opponent"):
                opponents.add(o["opponent"])
            if len(opponents) != 1:
                raise TRFError(f"Round {rnd}: team {tpn} has players facing teams {sorted(opponents)}")
            opp = opponents.pop()
            entries[tpn] = {
                "opponent": opp,
                "colour": _board_one_colour(rnd, tpn, games[tpn], o, roster_index, seq),
                "type": PLAYED,
                "gp": sum(_game_value(r.get("result"), gp_table) for _, r in games[tpn]),
            }
        for tpn, e in entries.items():
            if e["type"] != PLAYED:
                continue
            other = entries.get(e["opponent"])
            if other is None or other["type"] != PLAYED or other["opponent"] != tpn:
                raise TRFError(f"Round {rnd}: teams {tpn} and {e['opponent']} disagree on their match")
            if e["colour"] is None and other["colour"] is not None:
                e["colour"] = "white" if other["colour"] == "black" else "black"
            if e["colour"] is None and warnings is not None:
                warnings.append(f"Round {rnd}: no board of match {tpn}-{e['opponent']} was played; its colour is unknown")
            if e["gp"] > other["gp"]:
                e["mp"] = st.match_points_win
            elif e["gp"] < other["gp"]:
                e["mp"] = st.match_points_loss
            else:
                e["mp"] = st.match_points_draw
        out[rnd] = entries
    return out


def _board_one_colour(rnd, tpn, games, oodo, roster_index, seq) -> Optional[str]:
    """The team's colour on board 1 (C.04.6 Article 1.6.1)."""
    if oodo is not None:
        board_of = {pid: k for k, pid in enumerate(oodo["players"]) if pid}
    else:
        ordered = sorted((pid for pid, _ in games), key=lambda pid: roster_index.get(pid, 999))
        board_of = {pid: k for k, pid in enumerate(ordered)}
    for pid, r in sorted(games, key=lambda g: board_of.get(g[0], 999)):
        c = (r.get("color") or "").lower()
        k = board_of.get(pid)
        if c not in ("w", "b") or k is None or k >= len(seq):
            continue
        same = seq[k] == seq[0]
        white_on_one = (c == "w") == same
        return "white" if white_on_one else "black"
    return None


def team_history(parsed: Dict, before_round: Optional[int] = None) -> Dict[int, List[Dict]]:
    """History entries per team (engine format) for the rounds before ``before_round``."""
    rounds = team_rounds(parsed)
    out: Dict[int, List[Dict]] = {tm["tpn"]: [] for tm in parsed["teams"]}
    for rnd in sorted(rounds):
        if before_round is not None and rnd >= before_round:
            break
        for tpn, e in rounds[rnd].items():
            out[tpn].append({
                "round": rnd, "opponent": e["opponent"], "color": e["colour"],
                "type": e["type"], "mp": e["mp"], "gp": e["gp"],
            })
    return out


def engine_input(parsed: Dict, round_number: int, paired_teams: Optional[set] = None) -> Tuple[List[Dict], Dict]:
    """
    (teams, options) to pair ``round_number`` with the team engine.

    Teams with a 240 bye record for the round are not paired. When
    ``paired_teams`` is given (the checker passes the teams that the TRF
    paired in that round), every other team is absent.
    """
    history = team_history(parsed, before_round=round_number)
    pre_assigned = {tpn for b in parsed["byes"] if b["round"] == round_number for tpn in b["teams"]}
    teams = []
    for tm in parsed["teams"]:
        tpn = tm["tpn"]
        absent = tpn in pre_assigned
        if paired_teams is not None:
            absent = tpn not in paired_teams
        teams.append({
            "id": tpn, "tpn": tpn, "name": tm["name"], "strength": tm.get("strength"),
            "players": list(tm["players"]), "history": history[tpn], "absent": absent,
        })
    return teams, settings_from_trf(parsed)


def infer_initial_colour(parsed: Dict) -> Optional[str]:
    """
    Initial-colour from round 1 when record 152 is missing: in a pair of
    teams that have yet to play, the first-team (smaller TPN when scores
    are equal) has the initial-colour if its TPN is odd (Article 4.3.1).
    """
    rounds = team_rounds(parsed)
    r1 = rounds.get(1) or {}
    for tpn in sorted(r1):
        e = r1[tpn]
        if e["type"] == PLAYED and e["colour"] and e["opponent"] and e["opponent"] > tpn:
            mine = e["colour"]
            if tpn % 2 == 1:
                return mine
            return "black" if mine == "white" else "white"
    return None


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def _fmt_points(v: float, width: int) -> str:
    return f"{v:{width}.1f}"


def write_team_trf(tournament: Dict) -> str:
    """
    Write a team tournament (the dict of
    :func:`caissify_pairings.team_rtg.simulate_team_tournament`) as TRF26.
    """
    s = tournament.get("settings", {})
    rounds_data: Dict[int, Dict] = tournament.get("rounds", {})
    num_rounds = int(tournament.get("total_rounds") or (max(rounds_data) if rounds_data else 0))
    players = sorted(tournament["players"], key=lambda p: p["starting_number"])
    teams = sorted(tournament["teams"], key=lambda t: t["tpn"])
    settings = TeamSwissSettings(**{k: v for k, v in s.items() if k in TeamSwissSettings.__dataclass_fields__ and k != "accelerated"})
    lines: List[str] = []
    lines.append(f"012 {tournament.get('name', 'Team tournament')}")
    for code, key in (("022", "location"), ("032", "federation"), ("042", "start_date"), ("052", "end_date")):
        if tournament.get(key):
            lines.append(f"{code} {tournament[key]}")
    lines.append(f"062 {len(players)}")
    lines.append(f"072 {sum(1 for p in players if p.get('rating'))}")
    lines.append(f"082 {len(teams)}")
    lines.append("092 Team Swiss")
    if tournament.get("chief_arbiter"):
        lines.append(f"102 {tournament['chief_arbiter']}")
    lines.append(f"142 {num_rounds}")
    init = tournament.get("initial_color") or s.get("initial_color", "white")
    lines.append(f"152 {'W' if init == 'white' else 'B'}")
    gpw, gpd, gpl = settings.game_points_win, settings.game_points_draw, settings.game_points_loss
    if (gpw, gpd, gpl) != (1.0, 0.5, 0.0):
        lines.append(f"162  W{gpw:4.1f}    D{gpd:4.1f}    L{gpl:4.1f}")
    lines.append(f"192 {type_code(s)}")
    lines.append(f"352 {settings.board_sequence}")
    mpw, mpd, mpl = settings.match_points_win, settings.match_points_draw, settings.match_points_loss
    if (mpw, mpd, mpl) != (2.0, 1.0, 0.0):
        lines.append(f"362 TW{mpw:4.1f}   TD{mpd:4.1f}   TL{mpl:4.1f}")

    for p in players:
        lines.append(format_player_line({
            "starting_number": p["starting_number"],
            "name": p.get("name", ""),
            "rating": p.get("rating", 0),
            "title": p.get("title", ""),
            "federation": p.get("federation", ""),
            "fide_id": p.get("fide_id"),
            "birth": p.get("birth", ""),
            "sex": p.get("sex", ""),
            "score": p.get("score", 0.0),
            "rank": p.get("rank", p["starting_number"]),
            "results": p.get("results", {}),
        }, num_rounds))

    ranked = sorted(teams, key=lambda t: (-t.get("match_points", 0), -t.get("game_points", 0), t["tpn"]))
    rank_of = {}
    for i, t in enumerate(ranked, 1):
        key = (t.get("match_points", 0), t.get("game_points", 0))
        prev = ranked[i - 2] if i > 1 else None
        if prev is not None and (prev.get("match_points", 0), prev.get("game_points", 0)) == key:
            rank_of[t["tpn"]] = rank_of[prev["tpn"]]
        else:
            rank_of[t["tpn"]] = i
    for t in teams:
        base = [" "] * 73
        base[0:3] = "310"
        base[4:7] = f"{t['tpn']:>3}"
        base[8:40] = f"{t.get('name', '')[:32]:<32}"
        base[41:46] = f"{(t.get('nickname') or '')[:5]:<5}"
        if t.get("strength") is not None:
            base[47:53] = f"{int(round(t['strength'])):>6}"
        base[54:60] = _fmt_points(float(t.get("match_points", 0.0)), 6)
        base[61:67] = _fmt_points(float(t.get("game_points", 0.0)), 6)
        base[68:71] = f"{rank_of[t['tpn']]:>3}"
        line = "".join(base) + "".join(f"{pid:>4} " for pid in t.get("players", [])).rstrip()
        lines.append(line.rstrip())

    byes_pab = {rnd: info.get("bye") for rnd, info in rounds_data.items() if info.get("bye")}
    if byes_pab:
        line = f"320 {settings.pab_match_points:4.1f} {settings.pab_game_points:4.1f}"
        for rnd in range(1, num_rounds + 1):
            line += f" {byes_pab.get(rnd, 0):>3}"
        lines.append(line)

    for rnd in sorted(rounds_data):
        for kind, w, b in rounds_data[rnd].get("forfeits", []):
            lines.append(f"330 {kind} {rnd:>3} {w:>3} {b:>3}")

    roster = {t["tpn"]: list(t.get("players", [])) for t in teams}
    for rnd in sorted(rounds_data):
        info = rounds_data[rnd]
        lineups = info.get("lineups", {})
        opp_of = {}
        for w, b in info.get("pairs", []):
            opp_of[w], opp_of[b] = b, w
        for tpn in sorted(lineups):
            lineup = lineups[tpn]
            present = [pid for pid in lineup if pid]
            default = sorted(present, key=lambda pid: roster[tpn].index(pid))
            if 0 not in lineup and present == default:
                continue
            if tpn not in opp_of:
                continue
            ids = "".join(f" {pid:>4}" for pid in lineup)
            lines.append(f"300 {rnd:>3} {tpn:>3} {opp_of[tpn]:>3}{ids}")

    for rnd in sorted(rounds_data):
        info = rounds_data[rnd]
        unpaired = sorted(set(info.get("absent", [])) | set(info.get("withdrawn", [])))
        if unpaired:
            lines.append(f"240 Z {rnd:>3}" + "".join(f" {tpn:>4}" for tpn in unpaired))

    for rec in tournament.get("acceleration_records") or []:
        lines.append(
            f"250 {rec.get('mp', 0.0):4.1f} {rec.get('gp', 0.0):4.1f} {rec['first_round']:>3} "
            f"{rec['last_round']:>3} {rec['first_team']:>4} {rec['last_team']:>4}"
        )
    for item in s.get("forbidden_pairs") or []:
        if isinstance(item, dict):
            first, last, group = item.get("first_round", 1), item.get("last_round", num_rounds), item.get("teams", [])
        else:
            first, last, group = 1, num_rounds, list(item)
        lines.append(f"260 {first:>3} {last:>3}" + "".join(f" {tpn:>4}" for tpn in group))
    return "\n".join(lines) + "\n"
