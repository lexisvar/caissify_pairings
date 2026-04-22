"""
FIDE TRF16 / TRF06 Parser and Writer (standalone, no Django dependency).

Parses TRF files into plain dicts and writes tournament state back to TRF.
Used by the FPC (Free Pairings Checker) and RTG (Random Tournament Generator).

Reference:
- C.04.A Annex-2 — TRF16 specification
- C.04.A Annex-2 — TRF06 specification (legacy)
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TRFError(Exception):
    """Base exception for TRF processing."""


class TRFParseError(TRFError):
    """Raised when TRF file parsing fails."""

    def __init__(self, message: str, line_number: int | None = None, line_content: str | None = None):
        self.line_number = line_number
        self.line_content = line_content
        if line_number:
            message = f"Line {line_number}: {message}"
        if line_content:
            message = f"{message} (content: {line_content!r})"
        super().__init__(message)


class TRFFormatError(TRFError):
    """Raised when TRF format is invalid or unsupported."""


# ---------------------------------------------------------------------------
# TRF Parser
# ---------------------------------------------------------------------------

class TRFParser:
    """
    Standalone TRF16/TRF06 parser.

    Usage::

        parsed = TRFParser(trf_text).parse()
        # parsed = {
        #   "tournament": { "name": ..., "rounds": ..., ... },
        #   "players":    [ { "starting_number": 1, "name": ..., "rating": ...,
        #                     "results": { 1: {...}, 2: {...} } }, ... ],
        #   "format_version": "TRF16",
        # }
    """

    def __init__(self, trf_content: str):
        normalised = self._normalise(trf_content)
        self.lines = [l for l in normalised.split("\n") if l.strip()]
        self.tournament: Dict = {}
        self.players: List[Dict] = []
        self._line_parsers = {
            "001": self._parse_player_line,
            "012": self._parse_name,
            "022": self._parse_location,
            "032": self._parse_federation,
            "042": self._parse_start_date,
            "052": self._parse_end_date,
            "062": self._parse_player_count,
            "072": self._parse_rated_count,
            "082": self._parse_type,
            "092": self._parse_system,
            "102": self._parse_chief_arbiter,
            "112": self._parse_deputy_arbiter,
            "122": self._parse_round_dates,
        }

    # -- public API ---------------------------------------------------------

    def parse(self) -> Dict:
        for line_num, line in enumerate(self.lines, 1):
            self._dispatch(line, line_num)
        self._validate()
        return {
            "tournament": self.tournament,
            "players": self.players,
            "format_version": "TRF16",
        }

    # -- normalisation ------------------------------------------------------

    @staticmethod
    def _normalise(content: str) -> str:
        # NB: we deliberately do NOT rstrip each line. In FIDE TRF16 the
        # round-result block "NNNN c  " (opponent + colour + BLANK result)
        # encodes a pairing that has been generated but not yet played;
        # the trailing spaces are the data. After CR/LF normalisation the
        # tabs-to-spaces conversion is the only transform we apply per line.
        content = content.replace("\t", " ")
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        return content

    # -- dispatcher ---------------------------------------------------------

    def _dispatch(self, line: str, line_num: int):
        if len(line) < 3:
            return
        code = line[:3]
        content = line[4:] if len(line) > 4 else ""

        if code == "XXR":
            try:
                self.tournament["total_rounds"] = int(content.strip())
            except ValueError:
                pass
            return

        if code == "XXC":
            self.tournament["color_method"] = content.strip()
            return

        if code == "XXS":
            self.tournament["special_rules"] = content.strip()
            return

        parser = self._line_parsers.get(code)
        if parser:
            parser(content, line_num)

    # -- header parsers -----------------------------------------------------

    def _parse_name(self, c: str, _n: int):
        self.tournament["name"] = c.strip()

    def _parse_location(self, c: str, _n: int):
        self.tournament["location"] = c.strip()

    def _parse_federation(self, c: str, _n: int):
        self.tournament["federation"] = c.strip()

    def _parse_start_date(self, c: str, _n: int):
        self.tournament["start_date"] = c.strip()

    def _parse_end_date(self, c: str, _n: int):
        self.tournament["end_date"] = c.strip()

    def _parse_player_count(self, c: str, _n: int):
        try:
            self.tournament["player_count"] = int(c.strip())
        except ValueError:
            pass

    def _parse_rated_count(self, c: str, _n: int):
        try:
            self.tournament["rated_count"] = int(c.strip())
        except ValueError:
            pass

    def _parse_type(self, c: str, _n: int):
        self.tournament["type"] = c.strip()

    def _parse_system(self, c: str, _n: int):
        self.tournament["system"] = c.strip()

    def _parse_chief_arbiter(self, c: str, _n: int):
        self.tournament["chief_arbiter"] = c.strip()

    def _parse_deputy_arbiter(self, c: str, _n: int):
        self.tournament["deputy_arbiter"] = c.strip()

    def _parse_round_dates(self, c: str, _n: int):
        self.tournament["round_dates"] = c.strip()

    # -- player line (001) --------------------------------------------------

    def _parse_player_line(self, content: str, line_num: int):
        """Parse fixed-width 001 line (TRF16 layout).

        Content positions are offset by 4 from full-line positions
        because the dispatcher strips the "001 " prefix.

        Full-line → content mapping:
          [4:8]   → [0:4]   starting number
          [9]     → [5]     sex
          [11:14] → [7:10]  title
          [14:47] → [10:43] name
          [48:52] → [44:48] rating
          [53:56] → [49:52] federation
          [57:68] → [53:64] fide_id
          [69:79] → [65:75] birth
          [80:84] → [76:80] score
          [85:89] → [81:85] rank
          [91:]   → [87:]   round results
        """
        if len(content) < 50:
            raise TRFParseError("Player line too short", line_num, content)

        sn_str = content[0:5].strip()
        if not sn_str.isdigit():
            raise TRFParseError(f"Bad starting number: {content[0:5]!r}", line_num, content)
        starting_number = int(sn_str)

        sex = content[5:6].strip() if len(content) > 5 else ""
        title = content[7:10].strip() if len(content) > 9 else ""
        name = content[10:43].strip() if len(content) > 42 else ""

        rating_str = content[44:48].strip() if len(content) > 47 else ""
        rating = int(rating_str) if rating_str.isdigit() and rating_str != "0" else 0

        federation = content[49:52].strip() if len(content) > 51 else ""

        fide_id_str = content[53:64].strip() if len(content) > 63 else ""
        fide_id = int(fide_id_str) if fide_id_str.isdigit() and fide_id_str != "0" else None

        birth = content[65:75].strip() if len(content) > 74 else ""

        score_str = content[76:80].strip() if len(content) > 79 else ""
        try:
            score = float(score_str) if score_str else 0.0
        except ValueError:
            score = 0.0

        # Round results start at content position 87 (full-line 91)
        results: Dict[int, Dict] = {}
        if len(content) > 87:
            self._parse_round_results(content[87:], results)

        self.players.append({
            "starting_number": starting_number,
            "name": name,
            "sex": sex,
            "title": title,
            "rating": rating,
            "federation": federation,
            "fide_id": fide_id,
            "birth": birth,
            "score": score,
            "results": results,
        })

    # -- round-result blocks inside 001 line --------------------------------

    @staticmethod
    def _parse_round_results(text: str, results: Dict[int, Dict]):
        """Parse the 10-char-per-round blocks appended to a 001 line."""
        rnd = 1
        pos = 0
        while pos < len(text):
            block = text[pos:pos + 10]
            pos += 10
            stripped = block.strip()
            if not stripped:
                rnd += 1
                continue

            parts = stripped.split()

            # Bye patterns: "0000 - H", "0000 - U", single-char "H"/"F"/"U"/"Z"
            is_bye = False
            bye_type = ""
            if len(parts) >= 3 and parts[0] in ("0", "00", "0000") and parts[1] == "-":
                is_bye = True
                bye_type = parts[2]
            elif len(parts) == 1 and parts[0] in ("H", "F", "U", "Z", "+", "=", "-"):
                is_bye = True
                bye_type = parts[0]

            if is_bye:
                results[rnd] = {
                    "opponent": None,
                    "color": None,
                    "result": bye_type,
                }
            elif len(parts) >= 3:
                opp_str, color, res = parts[0], parts[1], parts[2]
                opp = int(opp_str) if opp_str.isdigit() and opp_str != "0" else None
                if opp is not None:
                    results[rnd] = {
                        "opponent": opp,
                        "color": color.lower(),
                        "result": res,
                    }
            elif len(parts) == 2:
                # Opponent + colour, no result character: a pairing that
                # has been generated but not yet played. FIDE TRF16
                # encodes this as "NNNN c  " (blank result column). We
                # preserve it with result="" so the arbiter's own UI can
                # show pending pairings and, crucially, round-trip them.
                opp_str, color = parts[0], parts[1]
                opp = (
                    int(opp_str)
                    if opp_str.isdigit() and opp_str != "0"
                    else None
                )
                if opp is not None and color.lower() in ("w", "b"):
                    results[rnd] = {
                        "opponent": opp,
                        "color": color.lower(),
                        "result": "",
                    }
            rnd += 1

    # -- validation ---------------------------------------------------------

    def _validate(self):
        if not self.tournament.get("name"):
            raise TRFFormatError("Tournament name (012) is required")
        if not self.players:
            raise TRFFormatError("No players (001 lines) found")
        sns = [p["starting_number"] for p in self.players]
        if len(sns) != len(set(sns)):
            raise TRFFormatError("Duplicate starting numbers in 001 lines")


# ---------------------------------------------------------------------------
# TRF Writer
# ---------------------------------------------------------------------------

class TRFWriter:
    """
    Writes a tournament state dict to a TRF16 string.

    The input ``tournament`` dict mirrors the output of :class:`TRFParser`.

    Usage::

        trf_text = TRFWriter(tournament_dict, players_list, num_rounds).write()
    """

    def __init__(
        self,
        tournament: Dict,
        players: List[Dict],
        num_rounds: int,
    ):
        self.tournament = tournament
        self.players = sorted(players, key=lambda p: p["starting_number"])
        self.num_rounds = num_rounds

    def write(self) -> str:
        lines: List[str] = []
        lines.extend(self._header_lines())
        lines.extend(self._player_lines())
        return "\n".join(lines)

    # -- header -------------------------------------------------------------

    def _header_lines(self) -> List[str]:
        t = self.tournament
        lines = []
        lines.append(f"012 {t.get('name', 'Tournament')}")
        if t.get("location"):
            lines.append(f"022 {t['location']}")
        if t.get("federation"):
            lines.append(f"032 {t['federation']}")
        if t.get("start_date"):
            lines.append(f"042 {t['start_date']}")
        if t.get("end_date"):
            lines.append(f"052 {t['end_date']}")
        lines.append(f"062 {len(self.players)}")
        lines.append(f"072 {len(self.players)}")
        if t.get("type"):
            lines.append(f"082 {t['type']}")
        if t.get("system"):
            lines.append(f"092 {t['system']}")
        if t.get("chief_arbiter"):
            lines.append(f"102 {t['chief_arbiter']}")
        if t.get("deputy_arbiter"):
            lines.append(f"112 {t['deputy_arbiter']}")
        if t.get("round_dates"):
            lines.append(f"122 {t['round_dates']}")
        lines.append(f"XXR {self.num_rounds}")
        if t.get("color_method"):
            lines.append(f"XXC {t['color_method']}")
        if t.get("special_rules"):
            lines.append(f"XXS {t['special_rules']}")
        return lines

    # -- 001 player lines ---------------------------------------------------

    def _player_lines(self) -> List[str]:
        lines: List[str] = []
        for p in self.players:
            line = self._format_player(p)
            lines.append(line)
        return lines

    def _format_player(self, p: Dict) -> str:
        """Build a single 001 line in TRF16 fixed-width format."""
        # Base: 91 chars of space (positions 0–90), round blocks appended
        base = [" "] * 91
        base[0:3] = "001"

        # Starting number — right-aligned in cols 4–8
        sn = f"{p['starting_number']:>4}"
        base[4:8] = sn

        # Sex — col 9
        sex = p.get("sex", "")
        if sex:
            base[9] = sex[0]

        # Title — cols 11-13 (3 chars, left-aligned) — ends before name at col 14
        title = p.get("title", "")
        if title:
            t = f"{title:<3}"[:3]
            base[11:14] = t

        # Name — cols 14–46 (33 chars, left-aligned)
        name = f"{p.get('name', ''):<33}"[:33]
        base[14:47] = name

        # Rating — cols 48–51 (right-aligned 4 digits)
        rating = p.get("rating", 0)
        if rating:
            base[48:52] = f"{rating:>4}"

        # Federation — cols 53–55
        fed = p.get("federation", "")
        if fed:
            base[53:56] = f"{fed:<3}"[:3]

        # FIDE ID — cols 57–67 (right-aligned 11 chars)
        fide_id = p.get("fide_id")
        if fide_id:
            base[57:68] = f"{fide_id:>11}"[:11]

        # Birth — cols 69–78
        birth = p.get("birth", "")
        if birth:
            base[69:69 + min(len(birth), 10)] = birth[:10]

        # Score — cols 80–83 (4 chars)
        score = p.get("score", 0.0)
        base[80:84] = f"{score:4.1f}"

        # Rank — cols 85–88
        rank = p.get("rank", p["starting_number"])
        base[85:89] = f"{rank:>4}"

        # Round results — each block 10 chars, starting at col 91
        rounds_str = self._format_rounds(p)
        return "".join(base) + rounds_str

    def _format_rounds(self, p: Dict) -> str:
        # Every round block MUST be exactly 10 characters wide — the parser
        # reads positionally with text[pos:pos+10]. An empty result
        # ("pending") is encoded as a blank result column, not omitted, so
        # subsequent rounds stay aligned.
        results: Dict[int, Dict] = p.get("results", {})
        blocks: List[str] = []
        for rnd in range(1, self.num_rounds + 1):
            r = results.get(rnd)
            if r is None:
                block = " " * 10
            else:
                opp = r.get("opponent")
                if opp is None:
                    bye_type = (r.get("result") or "U")[:1]
                    block = f"0000 - {bye_type}  "
                else:
                    color = (r.get("color") or "w")[:1]
                    raw_res = r.get("result", "-")
                    res = "" if raw_res is None else str(raw_res)
                    res_col = (res[:1] if res else " ")
                    block = f"{opp:>4d} {color} {res_col}  "
            blocks.append(block.ljust(10)[:10])
        return "".join(blocks)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def parse_trf(trf_content: str) -> Dict:
    """Parse a TRF16/06 string and return structured data."""
    return TRFParser(trf_content).parse()


def write_trf(tournament: Dict, players: List[Dict], num_rounds: int) -> str:
    """Write tournament state to a TRF16 string."""
    return TRFWriter(tournament, players, num_rounds).write()
