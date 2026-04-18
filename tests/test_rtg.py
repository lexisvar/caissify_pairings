"""
Tests for the RTG (Random Tournament Generator) — Phase 2.6

Validates that:
- RTG produces valid TRF output
- Generated tournaments follow pairing rules (no repeat opponents per round)
- Colour balance constraints hold
- TRF round-trips through parser
- generate_tournaments() batch mode works
"""

from __future__ import annotations

import pytest

from caissify_pairings.rtg import (
    generate_tournament,
    generate_tournaments,
    expected_score,
    simulate_result,
)
from caissify_pairings.trf import parse_trf


# ---------------------------------------------------------------------------
# FIDE probability formula tests
# ---------------------------------------------------------------------------

class TestExpectedScore:
    """Test the FIDE expected-score formula."""

    def test_equal_ratings(self):
        assert expected_score(2000, 2000) == pytest.approx(0.5, abs=0.001)

    def test_higher_rated_advantage(self):
        assert expected_score(2400, 2000) > 0.5
        assert expected_score(2400, 2000) < 1.0

    def test_lower_rated_disadvantage(self):
        assert expected_score(2000, 2400) < 0.5
        assert expected_score(2000, 2400) > 0.0

    def test_symmetric(self):
        p = expected_score(2200, 1800)
        assert p + expected_score(1800, 2200) == pytest.approx(1.0, abs=0.001)

    def test_large_difference(self):
        # 800 point diff → ~99% for stronger player
        assert expected_score(2800, 2000) > 0.95


class TestSimulateResult:
    """Test result simulation."""

    def test_returns_valid_result(self):
        for _ in range(100):
            r = simulate_result(2000, 1900)
            assert r in ("1", "0", "=")


# ---------------------------------------------------------------------------
# Tournament generation tests
# ---------------------------------------------------------------------------

class TestGenerateTournament:
    """Test single tournament generation."""

    def test_basic_generation(self):
        trf = generate_tournament(num_players=10, num_rounds=5, seed=42)
        assert isinstance(trf, str)
        assert "012 " in trf
        assert "001" in trf
        assert "XXR 5" in trf

    def test_parseable(self):
        """Generated TRF must be parseable by our parser."""
        trf = generate_tournament(num_players=10, num_rounds=5, seed=42)
        result = parse_trf(trf)
        assert len(result["players"]) == 10
        assert result["tournament"]["total_rounds"] == 5

    def test_correct_player_count(self):
        for n in [6, 10, 20, 50]:
            trf = generate_tournament(num_players=n, num_rounds=5, seed=1)
            result = parse_trf(trf)
            assert len(result["players"]) == n

    def test_scores_add_up(self):
        """Total score across all players must equal total decisive points."""
        trf = generate_tournament(num_players=10, num_rounds=5, seed=42)
        result = parse_trf(trf)
        total = sum(p["score"] for p in result["players"])
        # Each game produces exactly 1 point total (split 1-0, 0.5-0.5, or 0-1)
        # Byes add 1 point. With 10 players × 5 rounds: 5 games + 0 byes per round = 25 total points
        # This should be 25.0 for even players
        assert total == pytest.approx(25.0, abs=0.01)

    def test_odd_player_scores(self):
        """Odd players: total score = (n-1)/2 games × rounds + byes."""
        trf = generate_tournament(num_players=9, num_rounds=5, seed=42)
        result = parse_trf(trf)
        total = sum(p["score"] for p in result["players"])
        # 9 players → 4 games + 1 bye per round → 4+1 = 5 points per round → 25 total
        assert total == pytest.approx(25.0, abs=0.01)

    def test_no_repeat_opponents_in_round(self):
        """Each player appears at most once per round."""
        trf = generate_tournament(num_players=10, num_rounds=5, seed=42)
        result = parse_trf(trf)
        for rnd in range(1, 6):
            seen = set()
            for p in result["players"]:
                r = p["results"].get(rnd)
                if r and r["opponent"]:
                    pair = tuple(sorted([p["starting_number"], r["opponent"]]))
                    seen.add(pair)
            # Each pair should be unique (no player paired multiple times)
            players_in_round = set()
            for a, b in seen:
                assert a not in players_in_round, f"Player {a} paired twice in round {rnd}"
                assert b not in players_in_round, f"Player {b} paired twice in round {rnd}"
                players_in_round.add(a)
                players_in_round.add(b)

    def test_no_three_same_colors_in_row(self):
        """No player gets the same colour 3 times in a row."""
        trf = generate_tournament(num_players=10, num_rounds=7, seed=42)
        result = parse_trf(trf)
        for p in result["players"]:
            colors = []
            for rnd in range(1, 8):
                r = p["results"].get(rnd)
                if r and r.get("color"):
                    colors.append(r["color"])
            # Check no 3 in a row
            for i in range(len(colors) - 2):
                if colors[i] == colors[i+1] == colors[i+2]:
                    pytest.fail(
                        f"Player {p['starting_number']} has 3 {colors[i]}s "
                        f"in a row: rounds {i+1}-{i+3}"
                    )

    def test_seed_reproducibility(self):
        """Same seed produces identical output."""
        trf1 = generate_tournament(num_players=10, num_rounds=5, seed=999)
        trf2 = generate_tournament(num_players=10, num_rounds=5, seed=999)
        assert trf1 == trf2

    def test_different_seeds_differ(self):
        """Different seeds produce different output."""
        trf1 = generate_tournament(num_players=10, num_rounds=5, seed=1)
        trf2 = generate_tournament(num_players=10, num_rounds=5, seed=2)
        assert trf1 != trf2


# ---------------------------------------------------------------------------
# Batch generation tests
# ---------------------------------------------------------------------------

class TestGenerateTournaments:
    """Test batch generation."""

    def test_batch_generation(self):
        trfs = generate_tournaments(count=3, num_players=8, num_rounds=5)
        assert len(trfs) == 3
        for trf in trfs:
            result = parse_trf(trf)
            assert len(result["players"]) == 8


# ---------------------------------------------------------------------------
# Roundtrip tests (RTG → TRF → Parse → FPC)
# ---------------------------------------------------------------------------

class TestRTGRoundtrip:
    """End-to-end: generate → write TRF → parse → verify."""

    def test_roundtrip_even(self):
        trf = generate_tournament(num_players=10, num_rounds=5, seed=42)
        parsed = parse_trf(trf)
        assert len(parsed["players"]) == 10
        for p in parsed["players"]:
            assert len(p["results"]) == 5

    def test_roundtrip_odd(self):
        trf = generate_tournament(num_players=9, num_rounds=5, seed=42)
        parsed = parse_trf(trf)
        assert len(parsed["players"]) == 9
        for p in parsed["players"]:
            assert len(p["results"]) == 5

    @pytest.mark.parametrize("n_players,n_rounds",
                             [(6, 5), (12, 7), (20, 9), (30, 9)])
    def test_various_sizes(self, n_players, n_rounds):
        trf = generate_tournament(num_players=n_players, num_rounds=n_rounds, seed=42)
        parsed = parse_trf(trf)
        assert len(parsed["players"]) == n_players
