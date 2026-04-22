"""
Regression tests for pending-round (paired-but-not-yet-played) handling
in the TRF16 parser/writer.

Before this test existed, :class:`caissify_pairings.trf.TRFParser` would
silently discard any round block that contained opponent+colour but no
result character (FIDE TRF16's "NNNN c  " encoding). That came from two
interacting behaviours — a global ``rstrip`` in ``_normalise`` chewing
off the trailing spaces that encode the empty result column, and
``_parse_round_results`` only accepting blocks that tokenise to three
or more parts. The combination dropped the most important row in the
TRF for a tournament manager: the round the arbiter just paired.

The behaviour we now guarantee:

1. A pending round block is parsed into ``{"opponent": …, "color": …,
   "result": ""}`` rather than discarded.
2. A pending round block mid-line (pending R2 followed by a complete
   R3) does not throw off positional alignment of later rounds.
3. Writing a pending round back out produces exactly 10 characters per
   block, so the parser can re-read it losslessly.
"""

from __future__ import annotations

from typing import Dict, List

import pytest

from caissify_pairings.trf import TRFParser, parse_trf, write_trf


# ------------------------------------------------------------------ helpers


def _mk_player_line(sn: int, name: str, rounds_blob: str) -> str:
    """Build a fixed-width TRF16 001 line with arbitrary round blocks."""
    s = list(" " * 91)
    s[0:3] = list("001")
    s[4:8] = list(f"{sn:>4}")
    s[14:14 + min(len(name), 33)] = list(name[:33])
    s[80:84] = list(f"{0.0:4.1f}")
    s[85:89] = list(f"{sn:>4}")
    return "".join(s) + rounds_blob


def _wrap(num_rounds: int, player_lines: List[str]) -> str:
    header = (
        "012 Test\n"
        f"062 {len(player_lines)}\n"
        f"072 {len(player_lines)}\n"
        "082 0\n"
        "092 Dutch\n"
        f"XXR {num_rounds}\n"
    )
    return header + "\n".join(player_lines) + "\n"


# ------------------------------------------------------------------ tests


class TestPendingLastRound:
    """R1+R2 played, R3 paired-but-not-played — the reported bug."""

    @pytest.fixture(scope="class")
    def parsed(self) -> Dict:
        trf = _wrap(
            3,
            [
                _mk_player_line(1, "Alice", "  0003 w 1  0004 b 1  0002 w  "),
                _mk_player_line(2, "Bob",   "  0004 w 1  0003 b 1  0001 b  "),
                _mk_player_line(3, "Carol", "  0001 b 0  0002 w 0  0004 w  "),
                _mk_player_line(4, "Dan",   "  0002 b 0  0001 w 0  0003 b  "),
            ],
        )
        return parse_trf(trf)

    def test_round_three_present_for_every_player(self, parsed):
        for p in parsed["players"]:
            assert set(p["results"].keys()) == {1, 2, 3}, (
                f"player {p['starting_number']} missing rounds"
            )

    def test_round_three_has_empty_result(self, parsed):
        for p in parsed["players"]:
            assert p["results"][3]["result"] == ""

    def test_round_three_opponents_and_colours_preserved(self, parsed):
        by_sn = {p["starting_number"]: p for p in parsed["players"]}
        assert by_sn[1]["results"][3] == {"opponent": 2, "color": "w", "result": ""}
        assert by_sn[2]["results"][3] == {"opponent": 1, "color": "b", "result": ""}
        assert by_sn[3]["results"][3] == {"opponent": 4, "color": "w", "result": ""}
        assert by_sn[4]["results"][3] == {"opponent": 3, "color": "b", "result": ""}

    def test_played_rounds_still_parse_unchanged(self, parsed):
        """Regression guard: the fix must not disturb existing parsing."""
        by_sn = {p["starting_number"]: p for p in parsed["players"]}
        assert by_sn[1]["results"][1] == {"opponent": 3, "color": "w", "result": "1"}
        assert by_sn[3]["results"][2] == {"opponent": 2, "color": "w", "result": "0"}


class TestPendingMidLine:
    """
    A mid-line pending round (R2 pending, R3 played) must not push R3 out
    of its 10-char slot. This is the scenario that would silently corrupt
    downstream data if the parser relied on splitting rather than fixed
    positions.
    """

    def test_pending_middle_round_does_not_corrupt_later_rounds(self):
        # R1 played, R2 pending, R3 played. Each block is exactly 10 chars.
        rounds = (
            "  0002 w 1"   # R1: played vs 2, white, win
            "  0003 b  "   # R2: pending vs 3, black (no result yet)
            "  0004 w 1"   # R3: played vs 4, white, win
        )
        trf = _wrap(3, [_mk_player_line(1, "Alice", rounds)])
        parsed = parse_trf(trf)
        results = parsed["players"][0]["results"]

        assert set(results.keys()) == {1, 2, 3}
        assert results[1] == {"opponent": 2, "color": "w", "result": "1"}
        assert results[2] == {"opponent": 3, "color": "b", "result": ""}
        assert results[3] == {"opponent": 4, "color": "w", "result": "1"}


class TestWriterPendingRoundTrip:
    """
    Writing a tournament that contains a pending round back out to TRF
    must produce something the parser round-trips losslessly. This is the
    end-to-end contract a tournament manager relies on when it saves
    state between "round paired" and "results entered".
    """

    def _build_tournament(self) -> Dict:
        return {
            "tournament": {
                "name": "Round-trip Test",
                "total_rounds": 3,
            },
            "players": [
                {
                    "starting_number": 1, "name": "Alice",
                    "sex": "", "title": "", "rating": 2400,
                    "federation": "ESP", "fide_id": None, "birth": "",
                    "score": 2.0,
                    "results": {
                        1: {"opponent": 3, "color": "w", "result": "1"},
                        2: {"opponent": 4, "color": "b", "result": "1"},
                        3: {"opponent": 2, "color": "w", "result": ""},
                    },
                },
                {
                    "starting_number": 2, "name": "Bob",
                    "sex": "", "title": "", "rating": 2300,
                    "federation": "ESP", "fide_id": None, "birth": "",
                    "score": 2.0,
                    "results": {
                        1: {"opponent": 4, "color": "w", "result": "1"},
                        2: {"opponent": 3, "color": "b", "result": "1"},
                        3: {"opponent": 1, "color": "b", "result": ""},
                    },
                },
                {
                    "starting_number": 3, "name": "Carol",
                    "sex": "", "title": "", "rating": 2200,
                    "federation": "ESP", "fide_id": None, "birth": "",
                    "score": 0.0,
                    "results": {
                        1: {"opponent": 1, "color": "b", "result": "0"},
                        2: {"opponent": 2, "color": "w", "result": "0"},
                        3: {"opponent": 4, "color": "w", "result": ""},
                    },
                },
                {
                    "starting_number": 4, "name": "Dan",
                    "sex": "", "title": "", "rating": 2100,
                    "federation": "ESP", "fide_id": None, "birth": "",
                    "score": 0.0,
                    "results": {
                        1: {"opponent": 2, "color": "b", "result": "0"},
                        2: {"opponent": 1, "color": "w", "result": "0"},
                        3: {"opponent": 3, "color": "b", "result": ""},
                    },
                },
            ],
        }

    def test_write_produces_exact_10_char_blocks(self):
        data = self._build_tournament()
        text = write_trf(data["tournament"], data["players"], 3)
        for raw in text.splitlines():
            if not raw.startswith("001"):
                continue
            # content starts at col 4, round blocks at col 91
            rounds_blob = raw[91:]
            assert len(rounds_blob) == 30, (
                f"expected 30 chars of round data (3 rounds × 10), "
                f"got {len(rounds_blob)} for {raw!r}"
            )

    def test_round_trip_is_lossless(self):
        data = self._build_tournament()
        text = write_trf(data["tournament"], data["players"], 3)
        reparsed = parse_trf(text)

        # Every player's results dict must match bit-for-bit.
        orig_by_sn = {p["starting_number"]: p["results"] for p in data["players"]}
        new_by_sn = {p["starting_number"]: p["results"] for p in reparsed["players"]}
        assert orig_by_sn == new_by_sn


class TestNormaliseDoesNotLoseData:
    """
    The internal ``_normalise`` must preserve trailing spaces — they are
    load-bearing for the TRF16 encoding of pending results.
    """

    def test_trailing_spaces_are_preserved(self):
        trf = "012 X\n062 1\n072 1\n082 0\n092 Dutch\nXXR 1\n"
        line = _mk_player_line(1, "Alice", "  0000 - U  ")
        out = TRFParser._normalise(trf + line + "\n")
        # The 001 line must still contain the full blank-trailing block.
        line_out = next(l for l in out.split("\n") if l.startswith("001"))
        assert line_out.endswith("0000 - U  "), repr(line_out)
