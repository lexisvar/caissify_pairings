# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Breaking changes may occur between `0.x` releases; the API will be frozen
at `1.0.0`.

## [Unreleased]

## [0.4.3] — 2026-04-21

> **Release note.** ``v0.4.2`` was tagged on GitHub but never reached
> PyPI — the release build failed because an earlier edit to
> ``pyproject.toml`` put ``[project.optional-dependencies]`` inside the
> ``[project]`` table, which caused hatchling to reinterpret
> ``authors`` as an extras group and abort the sdist build. ``v0.4.3``
> ships everything that was intended for ``v0.4.2`` plus the
> ``dutch_pairings`` wrapper fix below and the corrected
> ``pyproject.toml`` layout. There is no ``0.4.2`` on PyPI.

### Fixed
- **``dutch_pairings()`` convenience wrapper silently dropped most
  engine kwargs.** The wrapper in
  ``caissify_pairings.engines.dutch`` named only ``bye_value`` and
  ``max_byes_per_player``, so callers who used the documented
  ``from caissify_pairings.engines.dutch import dutch_pairings``
  entry point (as shown in the README) and passed ``accelerated=True``
  — or ``initial_color="black"``, or any future engine option — got a
  ``TypeError`` instead of the expected Baku / initial-colour
  behaviour. The wrapper now forwards ``**kwargs`` verbatim to
  :class:`DutchEngine`, so any option the engine accepts is accepted
  by the wrapper too, and future options don't require touching this
  file. Applying ``**kwargs`` here mirrors the fix shipped for the
  JSON-over-stdin CLI in ``v0.4.1``.
- **``pyproject.toml`` — ``[project.optional-dependencies]`` now lives
  outside the ``[project]`` table.** The earlier placement (inside
  ``[project]``, just after ``dependencies``) silently reassigned
  ``authors``/``keywords``/``classifiers`` into the extras table as
  far as TOML parsing was concerned, which caused hatchling to reject
  the ``v0.4.2`` sdist build with ``TypeError: Dependency #1 of option
  'authors' … must be a string``. Caught by ``scripts/release.sh``
  before anything was uploaded.
- **TRF parser silently dropped pending-round pairings.** A round block
  of the form `"NNNN c  "` (opponent + colour + blank result — FIDE
  TRF16's encoding for a pairing that has been generated but not yet
  played) was being lost during parse because
  ``TRFParser._normalise`` applied a global ``rstrip`` to every line,
  eating the trailing spaces that encode the empty result column, and
  ``_parse_round_results`` then rejected the 2-token block. Reported by
  downstream tournament-manager integrators — their arbiter UI could
  generate round *N*, save the TRF, reload it, and see round *N*
  silently disappear. The parser now preserves trailing spaces and
  admits the 2-token case as ``{"opponent": …, "color": …,
  "result": ""}``.
- **TRF writer now emits exactly 10 characters per round block.**
  ``TRFWriter._format_rounds`` previously produced a 9-char block
  when a round had no result yet, which would corrupt positional
  alignment of any subsequent round. The writer now always pads to 10,
  so pending rounds round-trip losslessly through write → parse.

### Added
- **Public JSON Schema for engine output.** The shape returned by
  `generate_pairings()` and the `caissify-pairings` CLI is now formally
  described by a JSON Schema (draft 2020-12) shipped inside the wheel at
  `caissify_pairings/schemas/engine_output.schema.json`. Load it via
  `caissify_pairings.schemas.engine_output_schema()` or read the file
  directly with `importlib.resources`. Downstream consumers
  (Rust/TypeScript/Swift) are encouraged to code-generate types from
  this schema rather than re-deriving the shape by hand — this is what
  would have caught the recent downstream bug where `black_id` was
  modelled as a non-null `i64`.
- `tests/test_output_schema.py` — regression guard that validates the
  output of every engine (Dutch, accelerated Dutch, round-robin,
  casual) plus explicit PAB/pre-bye cases against the schema, and also
  asserts the schema itself rejects malformed rows (missing `black_id`,
  `bye=true` with non-null `black_id`, null `black_id` without `bye`,
  unknown `bye_type`, `table=0`).
- `tests/test_trf_pending_rounds.py` — covers the pending-round fix:
  last-round pending, mid-line pending (parser stays positionally
  aligned), write/parse round-trip of a tournament that contains a
  pending round, and ``_normalise`` does not eat trailing spaces.
- `tests/test_dutch_pairings_wrapper.py` — locks in the
  ``dutch_pairings`` wrapper contract: ``accelerated`` and
  ``initial_color`` are forwarded, legacy ``bye_value`` /
  ``max_byes_per_player`` still work, arbitrary future kwargs pass
  through to the engine, and round-1 acceleration actually produces
  Baku-shaped pairings when requested via the wrapper.

### Changed
- README "Output JSON schema" section expanded into a full field table
  documenting nullability, bye rows, `bye_type` codes, and `float_type`.
- `pyproject.toml`: `jsonschema>=4.0` added under an optional
  `[project.optional-dependencies] test` group — not a runtime dep.

## [0.4.1] — 2026-04-21

### Fixed
- JSON-over-stdin CLI (`caissify-pairings`) now forwards **every**
  non-reserved top-level key to the selected engine as a keyword
  argument. Previously only `bye_value` and `max_byes_per_player`
  were explicitly forwarded, so `accelerated` (Baku), `cycles`
  (double round-robin), `initial_color`, `bye_type`, and any other
  engine kwarg passed via JSON were silently dropped. Reported by
  downstream tournament-manager integrators.
- README JSON schema and `__main__.py` docstring now document the
  generic pass-through so the contract is explicit.

### Added
- `tests/test_cli.py` — regression tests for the CLI kwarg
  pass-through covering `accelerated`, `cycles`, `initial_color`,
  `bye_type`, `bye_value`, and `max_byes_per_player`, plus a small
  end-to-end check that the CLI produces a full round of pairings
  for Dutch, accelerated Dutch, and double round-robin.

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

[Unreleased]: https://github.com/lexisvar/caissify_pairings/compare/v0.4.3...HEAD
[0.4.3]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.4.3
[0.4.1]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.4.1
[0.4.0]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.4.0
[0.3.0]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.3.0
[0.2.0]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.2.0
[0.1.0]: https://github.com/lexisvar/caissify_pairings/releases/tag/v0.1.0
