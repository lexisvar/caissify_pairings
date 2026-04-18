"""
Cross-validation: Caissify engine vs bbpPairings (FIDE-endorsed reference).

FIDE C.04.A §A.7 requires two cross-validation paths to produce ≤10
discrepancies each across 5000 tournaments:

  Path A — bbpPairings RTG → our FPC
  Path B — our RTG → bbpPairings FPC

This module automates both paths using the compiled bbpPairings binary
in vendor/bbpPairings/.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from caissify_pairings.fpc import check_trf
from caissify_pairings.rtg import generate_tournament


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BBP_BINARY = PROJECT_ROOT / "vendor" / "bbpPairings" / "bbpPairings.exe"

# Skip all tests if bbpPairings binary is not available
pytestmark = pytest.mark.skipif(
    not BBP_BINARY.exists(),
    reason=f"bbpPairings binary not found at {BBP_BINARY}",
)


def _bbp_generate(num_players: int, num_rounds: int, seed: int, output_path: str):
    """Generate a tournament using bbpPairings RTG."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as cfg:
        cfg.write(f"PlayersNumber={num_players}\nRoundsNumber={num_rounds}\n")
        cfg_path = cfg.name
    try:
        subprocess.run(
            [
                str(BBP_BINARY), "--dutch",
                "-g", cfg_path,
                "-o", output_path,
                "-s", str(seed),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    finally:
        os.unlink(cfg_path)


def _bbp_check(trf_path: str) -> dict:
    """
    Run bbpPairings FPC on a TRF file, parse its output.

    Returns::

        {
            "rounds_checked": int,
            "rounds_mismatched": int,
            "total_discrepancies": int,   # sum of pairing diffs across all rounds
        }
    """
    result = subprocess.run(
        [str(BBP_BINARY), "--dutch", trf_path, "-c"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    rounds_checked = 0
    rounds_mismatched = 0
    total_discrepancies = 0

    current_round_diffs = 0
    for line in output.splitlines():
        # "filename: Round #N" marks the start of a round check
        if re.search(r"Round\s+#\d+", line):
            # Commit previous round
            if current_round_diffs > 0:
                rounds_mismatched += 1
                total_discrepancies += current_round_diffs
            rounds_checked += 1
            current_round_diffs = 0
        # Indented lines with " N - N " patterns are pairing diffs
        elif re.match(r"\s+\d+\s+-\s+\d+", line):
            current_round_diffs += 1

    # Commit final round
    if current_round_diffs > 0:
        rounds_mismatched += 1
        total_discrepancies += current_round_diffs

    return {
        "rounds_checked": rounds_checked,
        "rounds_mismatched": rounds_mismatched,
        "total_discrepancies": total_discrepancies,
    }


# ---------------------------------------------------------------------------
# Path A: bbpPairings RTG → our FPC
# ---------------------------------------------------------------------------

class TestPathA_BbpRTG_OurFPC:
    """bbpPairings generates tournaments, our FPC checks them."""

    @staticmethod
    def _run_batch(count: int, num_players: int, num_rounds: int, seed_offset: int = 0):
        total_rounds_checked = 0
        total_rounds_mismatched = 0
        total_discrepancies = 0
        failed = []

        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(count):
                seed = seed_offset + i
                trf_path = os.path.join(tmpdir, f"bbp_{seed}.trf")
                _bbp_generate(num_players, num_rounds, seed, trf_path)

                with open(trf_path) as f:
                    trf_content = f.read()

                report = check_trf(trf_content)
                s = report["summary"]
                total_rounds_checked += s["rounds_checked"]
                total_rounds_mismatched += s["rounds_mismatched"]
                total_discrepancies += s["total_discrepancies"]

                if s["rounds_mismatched"] > 0:
                    failed.append({
                        "seed": seed,
                        "mismatched": s["rounds_mismatched"],
                        "discrepancies": s["total_discrepancies"],
                    })

        return {
            "count": count,
            "total_rounds_checked": total_rounds_checked,
            "total_rounds_mismatched": total_rounds_mismatched,
            "total_discrepancies": total_discrepancies,
            "failed": failed,
        }

    def test_smoke_10_tournaments_10p5r(self):
        """Quick: 10 × 10p/5r — bbpRTG → our FPC."""
        result = self._run_batch(10, num_players=10, num_rounds=5)
        # Informational — report stats but don't fail (yet)
        print(
            f"\nPath A 10×10p5r: {result['total_rounds_checked']} rounds, "
            f"{result['total_rounds_mismatched']} mismatched, "
            f"{result['total_discrepancies']} discrepancies, "
            f"{len(result['failed'])} failed tournaments"
        )

    def test_smoke_10_tournaments_20p9r(self):
        """Quick: 10 × 20p/9r — bbpRTG → our FPC."""
        result = self._run_batch(10, num_players=20, num_rounds=9)
        print(
            f"\nPath A 10×20p9r: {result['total_rounds_checked']} rounds, "
            f"{result['total_rounds_mismatched']} mismatched, "
            f"{result['total_discrepancies']} discrepancies, "
            f"{len(result['failed'])} failed tournaments"
        )

    @pytest.mark.slow
    def test_5000_tournaments_10p5r(self):
        """FIDE A.7: 5000 × 10p/5r — bbpRTG → our FPC. Target ≤10."""
        result = self._run_batch(5000, num_players=10, num_rounds=5)
        detail = (
            f"Path A: {result['total_rounds_checked']} rounds checked, "
            f"{result['total_rounds_mismatched']} mismatched, "
            f"{result['total_discrepancies']} discrepancies across "
            f"{result['count']} tournaments."
        )
        assert result["total_discrepancies"] <= 10, detail

    @pytest.mark.slow
    def test_5000_tournaments_20p9r(self):
        """FIDE A.7: 5000 × 20p/9r — bbpRTG → our FPC. Target ≤10."""
        result = self._run_batch(5000, num_players=20, num_rounds=9)
        detail = (
            f"Path A: {result['total_rounds_checked']} rounds checked, "
            f"{result['total_rounds_mismatched']} mismatched, "
            f"{result['total_discrepancies']} discrepancies across "
            f"{result['count']} tournaments."
        )
        assert result["total_discrepancies"] <= 10, detail


# ---------------------------------------------------------------------------
# Path B: our RTG → bbpPairings FPC
# ---------------------------------------------------------------------------

class TestPathB_OurRTG_BbpFPC:
    """Our RTG generates tournaments, bbpPairings FPC checks them."""

    @staticmethod
    def _run_batch(count: int, num_players: int, num_rounds: int, seed_offset: int = 0):
        total_rounds_checked = 0
        total_rounds_mismatched = 0
        total_discrepancies = 0
        failed = []

        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(count):
                seed = seed_offset + i
                trf_content = generate_tournament(
                    num_players=num_players,
                    num_rounds=num_rounds,
                    seed=seed,
                )
                trf_path = os.path.join(tmpdir, f"our_{seed}.trf")
                with open(trf_path, "w") as f:
                    f.write(trf_content)

                report = _bbp_check(trf_path)
                total_rounds_checked += report["rounds_checked"]
                total_rounds_mismatched += report["rounds_mismatched"]
                total_discrepancies += report["total_discrepancies"]

                if report["rounds_mismatched"] > 0:
                    failed.append({
                        "seed": seed,
                        "mismatched": report["rounds_mismatched"],
                        "discrepancies": report["total_discrepancies"],
                    })

        return {
            "count": count,
            "total_rounds_checked": total_rounds_checked,
            "total_rounds_mismatched": total_rounds_mismatched,
            "total_discrepancies": total_discrepancies,
            "failed": failed,
        }

    def test_smoke_10_tournaments_10p5r(self):
        """Quick: 10 × 10p/5r — our RTG → bbpFPC."""
        result = self._run_batch(10, num_players=10, num_rounds=5)
        print(
            f"\nPath B 10×10p5r: {result['total_rounds_checked']} rounds, "
            f"{result['total_rounds_mismatched']} mismatched, "
            f"{result['total_discrepancies']} discrepancies, "
            f"{len(result['failed'])} failed tournaments"
        )

    def test_smoke_10_tournaments_20p9r(self):
        """Quick: 10 × 20p/9r — our RTG → bbpFPC."""
        result = self._run_batch(10, num_players=20, num_rounds=9)
        print(
            f"\nPath B 10×20p9r: {result['total_rounds_checked']} rounds, "
            f"{result['total_rounds_mismatched']} mismatched, "
            f"{result['total_discrepancies']} discrepancies, "
            f"{len(result['failed'])} failed tournaments"
        )

    @pytest.mark.slow
    def test_5000_tournaments_10p5r(self):
        """FIDE A.7: 5000 × 10p/5r — our RTG → bbpFPC. Target ≤10."""
        result = self._run_batch(5000, num_players=10, num_rounds=5)
        detail = (
            f"Path B: {result['total_rounds_checked']} rounds checked, "
            f"{result['total_rounds_mismatched']} mismatched, "
            f"{result['total_discrepancies']} discrepancies across "
            f"{result['count']} tournaments."
        )
        assert result["total_discrepancies"] <= 10, detail

    @pytest.mark.slow
    def test_5000_tournaments_20p9r(self):
        """FIDE A.7: 5000 × 20p/9r — our RTG → bbpFPC. Target ≤10."""
        result = self._run_batch(5000, num_players=20, num_rounds=9)
        detail = (
            f"Path B: {result['total_rounds_checked']} rounds checked, "
            f"{result['total_rounds_mismatched']} mismatched, "
            f"{result['total_discrepancies']} discrepancies across "
            f"{result['count']} tournaments."
        )
        assert result["total_discrepancies"] <= 10, detail
