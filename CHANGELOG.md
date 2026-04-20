# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Breaking changes may occur between `0.x` releases; the API will be frozen
at `1.0.0`.

## [Unreleased]

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

[Unreleased]: https://github.com/lexisvar/caissify_pairings/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.1.0
