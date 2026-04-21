# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Breaking changes may occur between `0.x` releases; the API will be frozen
at `1.0.0`.

## [Unreleased]

## [0.4.0] — 2026-04-21

### Added
- **Baku Acceleration** (FIDE Handbook §C.04.5.1) on the Dutch engine,
  opt-in via `accelerated=True`:
  - For rounds 1 and 2, the top half of the field (by initial pairing
    number / rating) receives a +1 *virtual point* added to its score
    for pairing purposes only. From round 3 onwards no virtual point
    is added.
  - For odd player counts, the extra slot goes to the top half
    (FIDE convention — ceiling division).
  - Real player scores, color histories, and all other state are
    untouched; only the pairing-time score is inflated, on a private
    copy of the player dicts.
  - Round 1 with acceleration falls through to the standard MWM
    bracket pairing path so that the artificial scoregroups created
    by the virtual point are respected (instead of the canonical
    Dutch top-vs-bottom split).
- `caissify_pairings.rtg.generate_tournament(..., accelerated=True)`
  and a matching `--accelerated` flag on the
  `caissify-pairings-rtg` CLI, for generating accelerated TRF
  fixtures end-to-end.
- 14 new unit tests in `tests/test_baku_acceleration.py` covering
  the helper, R1/R2 separation, R3+ no-op, defaults, input
  immutability, and a multi-round smoke test.

### Fixed
- `tests/test_dutch_C5` — the Free Pairings Checker now passes through
  arbiter pre-assigned byes (`Z`, `H`, `F`) when comparing engine
  output to a TRF round, so engines are no longer penalised for
  correctly excluding those players from active pairing.

## [0.3.0] — 2026-04-19

### Added
- **Round-robin engine** (`system="round_robin"`) implementing the
  **FIDE Berger Tables** (FIDE Handbook §C.05) for pairing every player
  against every other player exactly once.
  - Algorithm verified to match the published FIDE Berger tables
    exactly for `n = 4, 6, 8` (and tested for invariants up to
    `n = 20`).
  - Odd player counts handled via a phantom player → one bye per round,
    each player byeing exactly once.
  - **Double round-robin** support via `cycles=2`: cycle 2 plays the
    same schedule with every pair's colours reversed.
  - Configurable bye type (`bye_type="U"` by default — Pairing-Allocated
    Bye).
- Public helpers `berger_round(n, round_number)` and
  `berger_schedule(n)` exposed from `caissify_pairings.engines.round_robin`
  for callers that want the full schedule up front.

## [0.2.0] — 2026-04-19

### Added
- **Casual Swiss engine** (`system="casual"`): a small, deterministic
  Swiss-pairing algorithm intended for club nights, online ladders, and
  tournaments that are not FIDE-rated. Prioritises simplicity and
  readability over FIDE C.04 conformance.
  - Round 1: Dutch half-split (seed *i* plays seed *i + n/2*).
  - Rounds 2+: greedy pairing within score groups with downward floats.
  - Configurable bye type and per-player bye cap.
  - Never mutates the caller's player dicts.
- `CasualSwissEngine` registered in `caissify_pairings.engines`, usable
  via `generate_pairings(system="casual", …)`.
- README section comparing the `dutch` and `casual` engines and
  documenting casual-engine options.

## [0.1.0] — 2026-04-20

First public release.

### Added
- **FIDE Dutch System (C.04.3, Feb 2026)** pairing engine with full
  A.7 conformance against `bbpPairings`:
  - 5000 × 20p/9r benchmark: **0 discrepancies / 45,000 rounds**.
  - 5000 × 10p/5r benchmark: **0 discrepancies / 25,000 rounds**.
- TRF16 parser and writer (`caissify_pairings.trf`).
- Free Pairings Checker (`caissify_pairings.fpc`) — validate a TRF file
  against the engine.
- Random Tournament Generator (`caissify_pairings.rtg`).
- Top-level `generate_pairings(system=..., ...)` API and
  `caissify_pairings.engines` registry.
- CLI entry points: `caissify-pairings` (JSON stdin/stdout),
  `caissify-pairings-check`, `caissify-pairings-rtg`.

### Known limitations
- Only the Dutch system is implemented (no Accelerated Dutch, Burstein,
  Monrad, or round-robin yet).
- Exact-pair match rate against pre-recorded `bbpPairings` fixtures drops
  at 40+ players; the A.7 conformance benchmark tops out at 20p/9r where
  the engine is at 0 discrepancies.
- Not FIDE-endorsed (endorsement is a separate administrative process).

[Unreleased]: https://github.com/lexisvar/caissify_pairings/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.4.0
[0.3.0]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.3.0
[0.2.0]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.2.0
[0.1.0]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.1.0
