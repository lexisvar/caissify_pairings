"""
RTG → FPC Validation Pipeline — FIDE C.04.A §A.7

Generates random tournaments using the RTG, then validates each through the
FPC (Free Pairings Checker). The FIDE requirement: at most 10 discrepancies
across 5000 tournaments.

This is the core endorsement validation: proving the pairing engine is
self-consistent when tournaments are generated and re-checked.
"""

from __future__ import annotations

import pytest

from caissify_pairings.rtg import generate_tournament
from caissify_pairings.fpc import check_trf


# ---------------------------------------------------------------------------
# Self-consistency validation (RTG → FPC)
# ---------------------------------------------------------------------------

class TestRTGFPCValidation:
    """
    FIDE A.7 validation: generate tournaments via RTG, check via FPC.

    The engine must produce ≤10 discrepancies across 5000 tournaments.
    """

    @staticmethod
    def _run_batch(count: int, num_players: int, num_rounds: int):
        """Run *count* tournaments and return aggregate stats."""
        total_rounds_checked = 0
        total_rounds_mismatched = 0
        total_discrepancies = 0
        failed_tournaments = []

        for seed in range(count):
            trf = generate_tournament(
                num_players=num_players,
                num_rounds=num_rounds,
                seed=seed,
            )
            report = check_trf(trf)
            s = report["summary"]
            total_rounds_checked += s["rounds_checked"]
            total_rounds_mismatched += s["rounds_mismatched"]
            total_discrepancies += s["total_discrepancies"]

            if s["rounds_mismatched"] > 0:
                failed_tournaments.append({
                    "seed": seed,
                    "mismatched": s["rounds_mismatched"],
                    "discrepancies": s["total_discrepancies"],
                    "details": [
                        f"R{r['round']}: {r['discrepancies']}"
                        for r in report["rounds"]
                        if not r["match"]
                    ],
                })

        return {
            "count": count,
            "num_players": num_players,
            "num_rounds": num_rounds,
            "total_rounds_checked": total_rounds_checked,
            "total_rounds_mismatched": total_rounds_mismatched,
            "total_discrepancies": total_discrepancies,
            "failed_tournaments": failed_tournaments,
        }

    # -- Quick smoke tests (always run) -------------------------------------

    def test_100_tournaments_10p5r(self):
        """100 × 10p/5r — quick smoke test."""
        result = self._run_batch(100, num_players=10, num_rounds=5)
        assert result["total_discrepancies"] == 0, (
            f"{result['total_discrepancies']} discrepancies in "
            f"{len(result['failed_tournaments'])} tournaments: "
            + "; ".join(
                f"seed={t['seed']}: {t['details']}"
                for t in result["failed_tournaments"][:5]
            )
        )

    def test_100_tournaments_20p9r(self):
        """100 × 20p/9r — medium smoke test."""
        result = self._run_batch(100, num_players=20, num_rounds=9)
        assert result["total_discrepancies"] == 0, (
            f"{result['total_discrepancies']} discrepancies in "
            f"{len(result['failed_tournaments'])} tournaments: "
            + "; ".join(
                f"seed={t['seed']}: {t['details']}"
                for t in result["failed_tournaments"][:5]
            )
        )

    # -- Full FIDE A.7 validation (5000 tournaments) -----------------------

    @pytest.mark.slow
    def test_5000_tournaments_20p9r(self):
        """
        FIDE A.7: 5000 × 20p/9r — full endorsement validation.

        Must produce ≤10 total discrepancies.
        """
        result = self._run_batch(5000, num_players=20, num_rounds=9)
        detail = (
            f"Checked {result['total_rounds_checked']} rounds across "
            f"{result['count']} tournaments. "
            f"Mismatched rounds: {result['total_rounds_mismatched']}. "
            f"Total discrepancies: {result['total_discrepancies']}."
        )
        if result["failed_tournaments"]:
            detail += " Failed seeds: " + ", ".join(
                str(t["seed"]) for t in result["failed_tournaments"][:20]
            )
        assert result["total_discrepancies"] <= 10, detail

    @pytest.mark.slow
    def test_5000_tournaments_10p5r(self):
        """
        FIDE A.7 (small): 5000 × 10p/5r — small tournament validation.

        Must produce ≤10 total discrepancies.
        """
        result = self._run_batch(5000, num_players=10, num_rounds=5)
        detail = (
            f"Checked {result['total_rounds_checked']} rounds across "
            f"{result['count']} tournaments. "
            f"Mismatched rounds: {result['total_rounds_mismatched']}. "
            f"Total discrepancies: {result['total_discrepancies']}."
        )
        if result["failed_tournaments"]:
            detail += " Failed seeds: " + ", ".join(
                str(t["seed"]) for t in result["failed_tournaments"][:20]
            )
        assert result["total_discrepancies"] <= 10, detail
