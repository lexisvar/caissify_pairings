"""
The team engine against the brute-force reference pairer
(:mod:`tests.team_reference`) on random tournaments.

Every round of every tournament is paired by both; the tournament then
continues with the reference's pairing (results simulated board by board
by the RTG), so a difference cannot hide later ones. Rounds that have no
legal pairing at all (C.04.6 Article 3.3.3: the Chief Arbiter decides)
are skipped by the reference and counted separately.

The fast suite runs a few hundred tournaments; ``-m slow`` runs
thousands, including fields of up to 16 teams.
"""

from __future__ import annotations

import logging
import random

import pytest

from caissify_pairings.engines.team_swiss import TeamSwissEngine
from caissify_pairings.team_rtg import random_settings, simulate_team_tournament

from tests.team_reference import reference_pairing


def run_comparison(seeds, min_teams, max_teams, budget=None):
    stats = {"tournaments": 0, "rounds": 0, "compared": 0, "matched": 0, "no_legal_pairing": 0}
    diffs = []
    previous_budget = TeamSwissEngine.UPFLOATER_ENUMERATION_BUDGET
    if budget is not None:
        TeamSwissEngine.UPFLOATER_ENUMERATION_BUDGET = budget
    logging.disable(logging.WARNING)
    try:
        for seed in seeds:
            rng = random.Random(seed)
            n = rng.randint(min_teams, max_teams)
            rounds = rng.randint(2, min(9, max(2, n)))
            opts = random_settings(rng)
            opts.update(
                forfeit_rate=rng.choice([0, 0.05, 0.15]),
                absence_rate=rng.choice([0, 0.05, 0.1]),
                withdrawal_rate=rng.choice([0, 0.03]),
                initial_color=rng.choice(["white", "black"]),
            )
            if rng.random() < 0.15:
                opts["c6_mode"] = "parity"
            if rng.random() < 0.15:
                opts["c6_following"] = "unpaired"

            def pairer(teams, prev, rnd, total, **kw):
                stats["rounds"] += 1
                rows = TeamSwissEngine(players=teams, previous_pairings=prev, round_number=rnd,
                                       total_rounds=total, **kw).generate_pairings()
                ref = reference_pairing(teams, rnd, total, **kw)
                if ref is None:
                    stats["no_legal_pairing"] += 1
                    return rows
                stats["compared"] += 1
                ref_pairs, ref_bye = ref
                eng_pairs = sorted((r["white_id"], r["black_id"]) for r in rows if not r.get("bye"))
                eng_bye = next((r["white_id"] for r in rows if r.get("bye")), None)
                if sorted(ref_pairs) == eng_pairs and ref_bye == eng_bye:
                    stats["matched"] += 1
                else:
                    diffs.append({"seed": seed, "round": rnd, "options": opts,
                                  "reference": (sorted(ref_pairs), ref_bye), "engine": (eng_pairs, eng_bye)})
                out = [{"white_id": w, "black_id": b} for w, b in ref_pairs]
                if ref_bye is not None:
                    out.append({"white_id": ref_bye, "black_id": None, "bye": True})
                return out

            simulate_team_tournament(num_teams=n, num_rounds=rounds, seed=seed, pairer=pairer, **opts)
            stats["tournaments"] += 1
    finally:
        logging.disable(logging.NOTSET)
        TeamSwissEngine.UPFLOATER_ENUMERATION_BUDGET = previous_budget
    return stats, diffs


def test_engine_matches_reference_small_fields():
    stats, diffs = run_comparison(range(300), 3, 10)
    assert stats["compared"] > 1000
    assert diffs == []


def test_exact_matching_path_matches_reference():
    # Force the weighted-matching upfloater search (normally used only
    # when the literal enumeration would be too long).
    stats, diffs = run_comparison(range(300, 400), 3, 10, budget=0)
    assert stats["compared"] > 300
    assert diffs == []


@pytest.mark.slow
def test_engine_matches_reference_thousands():
    stats, diffs = run_comparison(range(1000, 4000), 3, 12)
    print(stats)
    assert diffs == []


@pytest.mark.slow
def test_engine_matches_reference_up_to_sixteen_teams():
    stats, diffs = run_comparison(range(5000, 5300), 13, 16)
    print(stats)
    assert diffs == []
