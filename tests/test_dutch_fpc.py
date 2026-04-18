"""
Tests for the FPC (Free Pairings Checker) — Phase 2.5

Validates that:
- TRF parser handles well-formed and edge-case TRF files
- TRF writer produces valid TRF that round-trips through the parser
- FPC correctly compares engine output to TRF pairings
- FPC reports discrepancies accurately
"""

from __future__ import annotations

import pytest

from caissify_pairings.trf import TRFParser, TRFWriter, parse_trf, write_trf, TRFParseError, TRFFormatError
from caissify_pairings.fpc import check_trf, _build_engine_players, _build_previous_pairings
from caissify_pairings.rtg import generate_tournament


# ---------------------------------------------------------------------------
# TRF Parser tests
# ---------------------------------------------------------------------------

class TestTRFParser:
    """Test standalone TRF parser."""

    MINIMAL_TRF = (
        "012 Test Tournament\n"
        "062 4\n"
        "072 4\n"
        "XXR 3\n"
        "001    1   GM Player One                        2500 ESP                         2.0    1     2 b 1     3 w 1     4 b 0  \n"
        "001    2      Player Two                        2400 ESP                         1.0    2     1 w 0     4 b 1     3 w 0  \n"
        "001    3      Player Three                      2300 ESP                         1.0    3     4 w 0     1 b 0     2 b 1  \n"
        "001    4      Player Four                       2200 ESP                         2.0    4     3 b 1     2 w 0     1 w 1  \n"
    )

    def test_parse_basic(self):
        result = parse_trf(self.MINIMAL_TRF)
        assert result["tournament"]["name"] == "Test Tournament"
        assert len(result["players"]) == 4
        assert result["tournament"]["total_rounds"] == 3

    def test_parse_player_fields(self):
        result = parse_trf(self.MINIMAL_TRF)
        p1 = result["players"][0]
        assert p1["starting_number"] == 1
        assert p1["rating"] == 2500
        assert p1["title"] == "GM"
        assert "Player One" in p1["name"]

    def test_parse_round_results(self):
        result = parse_trf(self.MINIMAL_TRF)
        p1 = result["players"][0]
        r1 = p1["results"][1]
        assert r1["opponent"] == 2
        assert r1["color"] == "b"
        assert r1["result"] == "1"

    def test_parse_validates_name(self):
        bad_trf = "062 1\n001    1       Player                            2000 ESP                          0.0    1\n"
        with pytest.raises(TRFFormatError, match="name"):
            parse_trf(bad_trf)

    def test_parse_validates_players(self):
        bad_trf = "012 Empty Tournament\n"
        with pytest.raises(TRFFormatError, match="players"):
            parse_trf(bad_trf)

    def test_parse_rejects_duplicate_starting_numbers(self):
        dup_trf = (
            "012 Dup Test\n"
            "001    1       Player A                          2000 ESP                          0.0    1\n"
            "001    1       Player B                          1900 ESP                          0.0    2\n"
        )
        with pytest.raises(TRFFormatError, match="Duplicate"):
            parse_trf(dup_trf)

    def test_parse_xxr_line(self):
        result = parse_trf(self.MINIMAL_TRF)
        assert result["tournament"]["total_rounds"] == 3

    def test_parse_xxc_line(self):
        trf = self.MINIMAL_TRF + "XXC colour allocation\n"
        result = parse_trf(trf)
        assert result["tournament"]["color_method"] == "colour allocation"

    def test_parse_bye_result(self):
        trf = (
            "012 Bye Test\n"
            "XXR 1\n"
            "001    1      Player A                          2000 ESP                         1.0    1  0000 - U  \n"
            "001    2      Player B                          1900 ESP                         0.0    2     1 w 0  \n"
        )
        # This player line is malformed for a 2-player bye scenario but tests parser
        result = parse_trf(trf)
        p1 = result["players"][0]
        assert p1["results"][1]["result"] == "U"
        assert p1["results"][1]["opponent"] is None


# ---------------------------------------------------------------------------
# TRF Writer tests
# ---------------------------------------------------------------------------

class TestTRFWriter:
    """Test TRF writer produces valid output."""

    def test_write_basic(self):
        tournament = {"name": "Writer Test", "total_rounds": 2}
        players = [
            {"starting_number": 1, "name": "Alice", "rating": 2000,
             "score": 1.5, "results": {
                 1: {"opponent": 2, "color": "w", "result": "1"},
                 2: {"opponent": None, "color": None, "result": "U"},
             }},
            {"starting_number": 2, "name": "Bob", "rating": 1900,
             "score": 0.0, "results": {
                 1: {"opponent": 1, "color": "b", "result": "0"},
             }},
        ]
        trf = write_trf(tournament, players, 2)
        assert "012 Writer Test" in trf
        assert "XXR 2" in trf
        assert "001" in trf

    def test_roundtrip(self):
        """Write → parse → compare."""
        tournament = {"name": "Roundtrip Test"}
        players = [
            {"starting_number": 1, "name": "Charlie", "rating": 2100,
             "score": 1.0, "results": {
                 1: {"opponent": 2, "color": "w", "result": "1"},
             }},
            {"starting_number": 2, "name": "Diana", "rating": 2000,
             "score": 0.0, "results": {
                 1: {"opponent": 1, "color": "b", "result": "0"},
             }},
        ]
        trf_text = write_trf(tournament, players, 1)
        parsed = parse_trf(trf_text)
        assert len(parsed["players"]) == 2
        assert parsed["players"][0]["results"][1]["opponent"] == 2
        assert parsed["players"][1]["results"][1]["opponent"] == 1


# ---------------------------------------------------------------------------
# FPC checker tests
# ---------------------------------------------------------------------------

class TestFPCChecker:
    """Test FPC round-by-round checking."""

    def test_check_round1_matches(self):
        """Round 1 should always match (deterministic Dutch initial pairing)."""
        trf = generate_tournament(num_players=10, num_rounds=1, seed=42)
        report = check_trf(trf)
        assert report["summary"]["rounds_checked"] >= 1
        # Round 1 is fully deterministic so it must match
        r1 = report["rounds"][0]
        assert r1["round"] == 1
        assert r1["match"] is True, f"Round 1 mismatch: {r1['discrepancies']}"

    def test_check_reports_structure(self):
        trf = generate_tournament(num_players=6, num_rounds=3, seed=99)
        report = check_trf(trf)
        assert "tournament_name" in report
        assert "summary" in report
        assert report["summary"]["rounds_checked"] == 3
        assert report["num_players"] == 6

    def test_check_all_rounds_self_consistency(self):
        """When the RTG generates with our engine, all rounds should match."""
        trf = generate_tournament(num_players=10, num_rounds=5, seed=123)
        report = check_trf(trf)
        s = report["summary"]
        assert s["rounds_checked"] == 5
        assert s["rounds_matched"] == 5, (
            f"Expected all 5 rounds to match but got {s['rounds_matched']}: "
            + "; ".join(
                f"R{r['round']}: {r['discrepancies']}"
                for r in report["rounds"] if not r["match"]
            )
        )

    def test_check_odd_players(self):
        """Odd player count — bye handling must be consistent."""
        trf = generate_tournament(num_players=9, num_rounds=5, seed=77)
        report = check_trf(trf)
        assert report["summary"]["rounds_checked"] == 5
        assert report["summary"]["rounds_matched"] == 5, (
            f"Mismatch in odd-player tournament: "
            + "; ".join(
                f"R{r['round']}: {r['discrepancies']}"
                for r in report["rounds"] if not r["match"]
            )
        )

    def test_check_large_tournament(self):
        """Larger tournament — 20 players, 7 rounds."""
        trf = generate_tournament(num_players=20, num_rounds=7, seed=456)
        report = check_trf(trf)
        assert report["summary"]["rounds_checked"] == 7
        assert report["summary"]["rounds_matched"] == 7, (
            f"Large tournament mismatch: "
            + "; ".join(
                f"R{r['round']}: {r['discrepancies']}"
                for r in report["rounds"] if not r["match"]
            )
        )


# ---------------------------------------------------------------------------
# FPC helper tests
# ---------------------------------------------------------------------------

class TestFPCHelpers:
    """Test FPC internal helper functions."""

    def test_build_engine_players(self):
        """Players active in round 2 (have round 2 result) should be included."""
        player_map = {
            1: {"name": "A", "rating": 2000, "title": "",
                "results": {
                    1: {"opponent": 2, "color": "w", "result": "1"},
                    2: {"opponent": 3, "color": "b", "result": "0"},
                }},
            2: {"name": "B", "rating": 1900, "title": "",
                "results": {
                    1: {"opponent": 1, "color": "b", "result": "0"},
                    2: {"opponent": 4, "color": "w", "result": "1"},
                }},
            3: {"name": "C", "rating": 1800, "title": "",
                "results": {
                    1: {"opponent": 4, "color": "w", "result": "="},
                    2: {"opponent": 1, "color": "w", "result": "1"},
                }},
            4: {"name": "D", "rating": 1700, "title": "",
                "results": {
                    1: {"opponent": 3, "color": "b", "result": "="},
                    2: {"opponent": 2, "color": "b", "result": "0"},
                }},
        }
        ep = _build_engine_players(player_map, 2)
        assert len(ep) == 4
        p1 = next(p for p in ep if p["id"] == 1)
        assert p1["score"] == 1.0
        assert p1["color_hist"] == ["white"]

    def test_build_previous_pairings(self):
        player_map = {
            1: {"results": {1: {"opponent": 2, "color": "w", "result": "1"}}},
            2: {"results": {1: {"opponent": 1, "color": "b", "result": "0"}}},
        }
        pp = _build_previous_pairings(player_map, 2)
        assert (1, 2) in pp


# ---------------------------------------------------------------------------
# Multiple-seed FPC stress tests
# ---------------------------------------------------------------------------

class TestFPCSelfConsistency:
    """
    Self-consistency: generate with our engine, check with our engine.
    Every round MUST match since both sides use the same engine.
    """

    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
    def test_10p_5r(self, seed):
        trf = generate_tournament(num_players=10, num_rounds=5, seed=seed)
        report = check_trf(trf)
        assert report["summary"]["rounds_matched"] == report["summary"]["rounds_checked"], (
            f"Seed {seed}: "
            + "; ".join(
                f"R{r['round']}: {r['discrepancies']}"
                for r in report["rounds"] if not r["match"]
            )
        )

    @pytest.mark.parametrize("seed", [10, 20, 30])
    def test_20p_9r(self, seed):
        trf = generate_tournament(num_players=20, num_rounds=9, seed=seed)
        report = check_trf(trf)
        assert report["summary"]["rounds_matched"] == report["summary"]["rounds_checked"], (
            f"Seed {seed}: "
            + "; ".join(
                f"R{r['round']}: {r['discrepancies']}"
                for r in report["rounds"] if not r["match"]
            )
        )
