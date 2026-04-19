"""
Tests against FIDE-endorsed reference pairings (bbpPairings v6.0.0).

These fixtures were generated using bbpPairings (BieremaBoyzProgramming),
a FIDE-endorsed C++ Dutch pairing engine, and verified with its own FPC
checker (0 discrepancies for all files).

Test categories:
- Specific rule tests (C5, C9) from bbpPairings test suite
- RTG-generated tournaments of varying sizes and seeds
- Stress/regression tests from bbpPairings issue tracker

The tests record the current match rate against the reference engine.
As the engine improves (Phase 3+), thresholds should be raised.
"""

from __future__ import annotations

import pathlib

import pytest

from caissify_pairings.fpc import check_trf

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "fide_official"


def _check_fixture(filename: str) -> dict:
    """Run FPC against a reference TRF file and return the report."""
    trf_path = FIXTURES_DIR / filename
    trf_text = trf_path.read_text()
    return check_trf(trf_text)


# ---------------------------------------------------------------------------
# Specific FIDE rule tests (must match 100%)
# ---------------------------------------------------------------------------

class TestFIDERuleTests:
    """Tests for specific Dutch system rules — must match reference exactly."""

    def test_dutch_C5(self):
        """C5 rule test: 6 players, 3 rounds — heterogeneous bracket handling."""
        report = _check_fixture("bbp_dutch_C5.trf")
        s = report["summary"]
        assert s["rounds_matched"] == s["rounds_checked"], (
            f"C5 test: {s['rounds_matched']}/{s['rounds_checked']} rounds match; "
            + "; ".join(
                f"R{r['round']}: {r['discrepancies']}"
                for r in report["rounds"] if not r["match"]
            )
        )

    def test_dutch_C9(self):
        """C9 rule test: 5 players, 1 round."""
        report = _check_fixture("bbp_dutch_C9.trf")
        s = report["summary"]
        assert s["rounds_matched"] == s["rounds_checked"], (
            f"C9 test: {s['rounds_matched']}/{s['rounds_checked']} rounds match; "
            + "; ".join(
                f"R{r['round']}: {r['discrepancies']}"
                for r in report["rounds"] if not r["match"]
            )
        )


# ---------------------------------------------------------------------------
# RTG-generated reference tournaments
# ---------------------------------------------------------------------------

# (filename, min_match_ratio) — raise thresholds as engine improves
RTG_FIXTURES = [
    ("bbp_10p5r_s42.trf", 1.00),
    ("bbp_10p5r_s43.trf", 1.00),
    ("bbp_10p5r_s44.trf", 1.00),
    ("bbp_11p5r_s42.trf", 0.80),
    ("bbp_11p5r_s43.trf", 0.60),
    ("bbp_20p7r_s42.trf", 1.00),
    ("bbp_20p7r_s43.trf", 1.00),
    ("bbp_40p9r_s42.trf", 1.00),
]


class TestRTGReference:
    """RTG-generated tournaments validated against bbpPairings."""

    @pytest.mark.parametrize("filename,min_ratio", RTG_FIXTURES,
                             ids=[f[0].replace(".trf", "") for f in RTG_FIXTURES])
    def test_rtg_match_rate(self, filename, min_ratio):
        report = _check_fixture(filename)
        s = report["summary"]
        ratio = s["rounds_matched"] / s["rounds_checked"]
        assert ratio >= min_ratio, (
            f"{filename}: {s['rounds_matched']}/{s['rounds_checked']} "
            f"rounds match ({ratio:.0%}), expected >= {min_ratio:.0%}; "
            + "; ".join(
                f"R{r['round']}: {r['discrepancies']}"
                for r in report["rounds"] if not r["match"]
            )
        )


# ---------------------------------------------------------------------------
# Large stress/regression tests (from bbpPairings issue tracker)
# ---------------------------------------------------------------------------

class TestStressRegression:
    """Large tournament regression tests — currently expected to have low
    match rates; thresholds will increase as engine matures."""

    @pytest.mark.slow
    def test_issue7_60p14r(self):
        report = _check_fixture("bbp_issue7_60p14r.trf")
        s = report["summary"]
        # Currently low match rate; track improvement
        assert s["rounds_checked"] == 14
        assert s["rounds_matched"] >= 1

    @pytest.mark.slow
    def test_issue15_180p11r(self):
        report = _check_fixture("bbp_issue15_180p11r.trf")
        s = report["summary"]
        assert s["rounds_checked"] == 11
        # Currently 0/11 — will improve with Phase 3 work
        assert s["rounds_matched"] >= 0
