# FIDE Conformance — `caissify-pairings`

> Where each engine sits against the FIDE Handbook, and how that was
> measured. This document is written for an external reader (for
> example a FIDE software reviewer) — it assumes only the Handbook
> text, not internal development history.

- **Package:** [`caissify-pairings`](https://pypi.org/project/caissify-pairings/)
- **License:** MIT
- **Runtime:** pure Python 3.10+ with `networkx` only
- **Engines shipped:** Dutch (C.04.3), Round-Robin (C.05 Berger Tables),
  Casual Swiss (non-FIDE), Baku Acceleration modifier (C.04.5.1) on the
  Dutch engine

---

## 1. Engine-by-engine summary

| Engine | Handbook section | Status against FIDE text | Oracle(s) used |
|---|---|---|---|
| **Dutch** | C.04.3 (2025 rules) | **A.7 conformant — 0 discrepancies / 5000 tournaments on both canonical benchmarks.** | `bbpPairings` 6.0.0 (primary), JaVaFo 2.2 (secondary triage) |
| **Dutch + Baku Acceleration** | C.04.5.1 | Implemented and self-consistent (RTG → FPC). **Not yet cross-validated at A.7 scale** against `bbpPairings` via `XXA` TRF tags. | — |
| **Round-Robin (Berger Tables)** | C.05 | Reproduces the published FIDE Berger tables exactly for `n = 4, 6, 8`; structural invariants tested up to `n = 20`. Supports single- and double-cycle schedules with FIDE colour-reversal rule. | FIDE Handbook §C.05 tables |
| **Casual Swiss** | *(not a FIDE system)* | Explicitly out of scope for FIDE compliance — intended for club nights and online ladders. | — |

---

## 2. Dutch engine — A.7 cross-validation results

FIDE C.04.A §A.7 defines the cross-validation acceptance criterion:
an already-endorsed program's RTG generates 5000 random tournaments
and the candidate's FPC checks them; **at most 10 discrepancies** are
allowed across the 5000 tournaments (and vice versa with the
candidate's RTG feeding an endorsed FPC).

Using `bbpPairings` 6.0.0 (FIDE-endorsed, C++, Apache-2.0) as the oracle:

| Benchmark | Tournaments | Rounds checked | Discrepancies | FIDE bar | Result |
|---|---:|---:|---:|---|---|
| 20 players × 9 rounds (primary A.7) | 5,000 | 45,000 | **0** | ≤ 10 | ✅ PASS |
| 10 players × 5 rounds (small A.7) | 5,000 | 25,000 | **0** | ≤ 10 | ✅ PASS |

Both benchmarks run both A.7 directions (our RTG → bbp FPC, and bbp
RTG → our FPC). Test harness: `tests/test_rtg_fpc_validation.py` and
`tests/test_cross_validation.py`. Methodology and classification
rules are documented in [`DIVERGENCE_TESTING.md`](DIVERGENCE_TESTING.md).

### Three-way triage (Dutch vs `bbpPairings` vs JaVaFo)

On random samples a second oracle is used to classify any
disagreement per FIDE §A.7 bullets 1–3 (input-file error / candidate
error / interpretation divergence). `JAVAFO_QUIRK` means the candidate
matches `bbpPairings` but JaVaFo differs — under A.7 with `bbpPairings`
as the oracle this does not count against the candidate.

| Shape | Sample | `OUR_BUG` (we differ from both oracles) | `JAVAFO_QUIRK` (JaVaFo differs) |
|---|---:|---:|---:|
| 9 players × 5 rounds | 1000 seeds | 0 | — |
| 11 players × 5 rounds | 500 seeds | 0 | — |
| 13 players × 5 rounds | 500 seeds | 0 | — |
| 20 players × 9 rounds | 500 seeds | 0 | 346 |

### Pre-recorded `bbpPairings` fixture replays

Independent from the A.7 benchmark, a set of pre-recorded `bbpPairings`
TRF outputs is replayed round-by-round for regression protection. Fixture
match rates are expected to be lower than A.7 — A.7 measures "our pairing
is valid under C.04.3"; fixture replay additionally measures "our
tie-breaking choice happens to match a pre-recorded bbp tie-breaking
choice", which C.04.3 does not mandate.

| Fixture | Match rate | Notes |
|---|---|---|
| `bbp_dutch_C5.trf` | 100 % (2/2 rounds) | FIDE C.5 rule test |
| `bbp_dutch_C9.trf` | 100 % (2/2 rounds) | FIDE C.9 rule test |
| `bbp_10p5r_s{42,43,44}.trf` | 60–80 % | Valid tie-breaking divergences |
| `bbp_11p5r_s{42,43}.trf` | 60 % | Valid tie-breaking divergences |
| `bbp_20p7r_s{42,43}.trf` | 43–57 % | Valid tie-breaking divergences |
| `bbp_40p9r_s42.trf` | 11 % | Large-field divergence (see §4) |

---

## 3. Round-Robin engine — Berger-table verification

C.05 prescribes the Berger pairing tables for round-robin events.
The engine (`caissify_pairings.engines.round_robin`) implements these
tables directly:

- `berger_round(n, round_number)` returns the pairing for a single
  round; `berger_schedule(n)` returns the full schedule.
- Odd player counts are handled via a phantom player, producing one
  bye per round so that each real player byes exactly once per cycle.
- Double round-robin (`cycles=2`) replays the schedule with every
  pair's colours reversed, as required by C.05.

Tests (`tests/test_round_robin_engine.py`): 35 cases covering direct
table comparison for `n = 4, 6, 8`, colour-alternation invariants,
odd-player bye rotation, and double-cycle colour reversal.

---

## 4. Baku Acceleration modifier (Dutch, C.04.5.1)

Opt-in via `accelerated=True`. For rounds 1 and 2 the top half of the
field (by initial pairing number) receives a **+1 virtual point** on
the pairing-time score only; from round 3 onwards no virtual point is
added.

Implementation notes:

- Real `score`, `color_hist`, etc. are never mutated — the virtual
  point is applied to a private copy of the player dicts.
- Odd player counts allocate the extra slot to the top half (ceiling
  division), per FIDE convention.
- Round 1 under acceleration falls through to the standard MWM bracket
  pairing path so that the artificial scoregroups created by the
  virtual point are respected, instead of the canonical Dutch
  top-vs-bottom split.

Tests (`tests/test_baku_acceleration.py`): 14 unit tests covering the
helper, round 1 / round 2 separation, round 3+ no-op, defaults, input
immutability, and a multi-round smoke run.

**Not yet A.7 cross-validated.** `bbpPairings` applies Baku via the
TRF `XXA` per-round acceleration codes, not via a CLI flag. A full
5000-tournament A.7 run for the accelerated configuration therefore
requires emitting `XXA` codes in our RTG output and parsing them back
in our FPC; both are on the roadmap but neither is done yet.

---

## 5. Casual engine — explicit non-goal

`caissify_pairings.engines.casual` is a small, deterministic Swiss
engine for club nights, ladders, and non-rated events. It is
intentionally *not* C.04.3-compliant and makes no claim of FIDE
conformance. It shares the input/output contract of the Dutch engine,
so applications can switch between the two with a single parameter.

---

## 6. Known tie-breaking divergence (does not affect A.7)

The `bbpPairings` C++ source (`dutch.cpp` line 706) assigns
`scoreGroupShift = 0` to the highest score group and increments
downward. The Python implementation uses `reversed(sg_bounds)`, which
assigns `shift = 0` to the lowest score group. Empirically, switching
to the "C++ direction" produced worse fixture match rates, suggesting
the two implementations are not strictly analogous in how
`scoreGroupShifts` feeds the edge-weight formula.

Kept `reversed(sg_bounds)`. The resulting divergences are valid FIDE
pairings (different tie-breaking, not a rule violation) and do **not**
count against the A.7 budget: both 5000-tournament benchmarks pass
with 0 discrepancies.

---

## 7. Test-suite snapshot

| Suite | Tests | Purpose |
|---|---:|---|
| `test_dutch_engine.py` | 30 | Dutch engine components (scoregroups, S1/S2, floats, colours) |
| `test_dutch_integration.py` | 22 | Full-tournament simulations, odd counts, withdrawals |
| `test_dutch_fpc.py` | 20 | Free Pairings Checker, TRF replay |
| `test_dutch_fide_official.py` | 5 | FIDE reference fixtures (C.5 / C.9 rule tests) |
| `test_dutch_trf_fixtures.py` | 21 | Replay of pre-recorded `bbpPairings` / JaVaFo TRFs |
| `test_dutch_javafo.py` | 10 | JaVaFo cross-validation (skipped when JaVaFo jar absent) |
| `test_rtg.py` | 19 | Random Tournament Generator |
| `test_rtg_fpc_validation.py` | 4 | A.7 self-consistency (2 smoke + 2 @slow 5000-tournament) |
| `test_cross_validation.py` | 8 | A.7 bbpPairings cross-validation (4 smoke + 4 @slow) |
| `test_round_robin_engine.py` | 35 | Berger-table verification, double-cycle, odd counts |
| `test_casual_engine.py` | 18 | Casual Swiss engine |
| `test_baku_acceleration.py` | 14 | Baku Acceleration on Dutch |

Fast suite (`pytest -m "not slow"`) — current status: **204 passed,
10 skipped, 8 `@slow` deselected**, ~96 s. The 8 `@slow` tests are the
full 5000-tournament A.7 benchmarks; the 10 skipped tests are the
JaVaFo ones that require the JaVaFo jar to be present at
`vendor/javafo/javafo.jar`.

---

## 8. Versioning

This document tracks the engine shipped on PyPI. Breaking changes may
occur between `0.x` releases; the API will be frozen at `1.0.0`. See
[`CHANGELOG.md`](../CHANGELOG.md) for the per-version release notes.
