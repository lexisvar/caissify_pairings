# caissify-pairings

Chess tournament pairing engines for Python. Ships three engines:

- **`dutch`** — the **FIDE Dutch System (C.04.3)**, cross-validated
  against the FIDE-endorsed reference `bbpPairings` to full **A.7
  conformance**. Use this for rated Swiss tournaments. Optional
  **Baku Acceleration** (FIDE C.04.5.1) via `accelerated=True` for
  large opens.
- **`round_robin`** — **FIDE Berger Tables** (FIDE Handbook §C.05).
  Every player meets every other player; supports single and double
  round-robin. Verified to match the published FIDE tables exactly.
- **`casual`** — a small, deterministic Swiss engine for club nights
  and non-rated events. Simpler, more readable, no FIDE guarantees.

| Engine        | Use case                                    | FIDE compliance | Complexity |
|---------------|---------------------------------------------|-----------------|------------|
| `dutch`       | Rated Swiss / official tournaments          | A.7 — 0 discrepancies on 70k rounds; Baku C.04.5.1 acceleration available | High (full C.04.3) |
| `round_robin` | Round-robin / Scheveningen / club leagues   | Berger Tables verified vs FIDE Handbook §C.05 | Low |
| `casual`      | Club nights, ladders, non-rated Swiss       | (not the goal) | Low |

## What you get

- A programmable pairing engine — use it as a library, call it from a web
  service, or wrap it in a tournament-director UI.
- TRF16 parser and writer.
- A Free Pairings Checker (FPC) — validate a TRF file against the engine.
- A Random Tournament Generator (RTG) — generate simulated tournaments for
  testing.
- A simple JSON-over-stdin CLI.

## FIDE A.7 conformance

The FIDE *Swiss Pairings Programs Commission* evaluates endorsed pairing
software using the **A.7 test**: a program is expected to produce pairings
that match an already-endorsed program on at least **4990 of 5000** random
tournaments (≤ 10 discrepancies).

Measured against [`bbpPairings`](https://github.com/BieremaBoyzProgramming/bbpPairings)
(FIDE-endorsed, C++) using our Random Tournament Generator + Free Pairings
Checker pipeline:

| Benchmark | Rounds checked | Discrepancies | FIDE target | Result |
|-----------|----------------|---------------|-------------|--------|
| 5000 × 20-player / 9-round | 45,000 | **0** | ≤ 10 | ✅ PASS |
| 5000 × 10-player / 5-round | 25,000 | **0** | ≤ 10 | ✅ PASS |

See [`doc/FIDE_CONFORMANCE.md`](doc/FIDE_CONFORMANCE.md) for
per-engine status against the FIDE Handbook, and
[`doc/DIVERGENCE_TESTING.md`](doc/DIVERGENCE_TESTING.md) for the
methodology.

> **Note.** A.7 conformance is a technical criterion. `caissify-pairings`
> is **not yet FIDE-endorsed** — endorsement is a separate administrative
> process with the FIDE SPP Commission.

## Installation

```bash
pip install caissify-pairings
```

Requires Python 3.10+. The only runtime dependency is [`networkx`](https://networkx.org/)
(for maximum-weight matching).

## Quick start — library

```python
from caissify_pairings import generate_pairings

players = [
    {"id": 1, "name": "Carlsen",  "score": 2.0, "rating": 2830,
     "starting_number": 1, "color_hist": ["white", "black"],
     "float_history": [], "bye_count": 0},
    {"id": 2, "name": "Firouzja", "score": 2.0, "rating": 2785,
     "starting_number": 2, "color_hist": ["black", "white"],
     "float_history": [], "bye_count": 0},
    {"id": 3, "name": "Ding",     "score": 1.5, "rating": 2780,
     "starting_number": 3, "color_hist": ["white", "black"],
     "float_history": [], "bye_count": 0},
    {"id": 4, "name": "Nepo",     "score": 1.5, "rating": 2775,
     "starting_number": 4, "color_hist": ["black", "white"],
     "float_history": [], "bye_count": 0},
]

pairings = generate_pairings(
    system="dutch",
    players=players,
    previous_pairings={(1, 3), (2, 4)},
    round_number=3,
    total_rounds=9,
)

for p in pairings:
    print(f"Table {p['table']}: {p['white_id']} vs {p['black_id']}")
```

## Command-line usage

```bash
# Pair a round from a JSON state on stdin, write pairings to stdout
caissify-pairings < tournament_state.json

# Validate a FIDE TRF file against the Dutch engine
caissify-pairings-check tournament.trf

# Generate random tournaments for testing
caissify-pairings-rtg --players 20 --rounds 9 -n 100 -o ./output/
```

### Input JSON schema

```json
{
  "system": "dutch",
  "players": [
    {
      "id": 1,
      "name": "Player Name",
      "score": 0.0,
      "rating": 2400,
      "starting_number": 1,
      "color_hist": [],
      "float_history": [],
      "bye_count": 0
    }
  ],
  "previous_pairings": [[1, 3], [2, 4]],
  "round_number": 1,
  "total_rounds": 9,
  "bye_value": 1.0,
  "max_byes_per_player": 1
}
```

### Output JSON schema

```json
[
  {"white_id": 1, "black_id": 4, "table": 1},
  {"white_id": 3, "black_id": 2, "table": 2}
]
```

## What works today

- **FIDE Dutch System (C.04.3, Feb 2026 spec)** — full A.7 conformance
  against `bbpPairings` on 20p/9r and 10p/5r benchmarks (see above).
- **FIDE Berger round-robin** — opt-in via `system="round_robin"`,
  matches the published FIDE Berger tables exactly.
- **Casual Swiss engine** — opt-in via `system="casual"` for club-level
  events where FIDE conformance is not required.
- Full TRF16 round-trip parsing/writing.
- Free Pairings Checker (FPC) for validating existing TRF files.
- Random Tournament Generator (RTG) for test corpora.
- Pure-Python, single runtime dependency (`networkx`).

## Baku Acceleration — quick start

Baku Acceleration (FIDE Handbook §C.04.5.1) is an opt-in modifier on
the Dutch engine. For rounds 1 and 2 the top half of the field (by
initial pairing number / rating) gets a **+1 virtual point** added to
its score for pairing purposes only. The result: top-half plays
top-half and bottom-half plays bottom-half early, spreading the
field — exactly what large opens need.

```python
from caissify_pairings import generate_pairings

pairings = generate_pairings(
    system="dutch",
    players=players,            # any list of player dicts
    previous_pairings=set(),
    round_number=1,
    total_rounds=9,
    accelerated=True,           # ← opt in to Baku
)
```

What you can rely on:

- The virtual point is applied only for rounds 1 and 2; round 3
  onwards is a normal Dutch pairing on real scores.
- Real `score`, `color_hist`, etc. are never modified — only the
  internal pairing-time score is inflated, on a private copy.
- For odd player counts the extra slot goes to the **top** half
  (FIDE convention — ceiling division).
- Output shape is unchanged (`white_id`, `black_id`, `table`, …).

To generate accelerated TRF fixtures end-to-end the RTG exposes the
same flag. Cross-validation against `bbpPairings` itself is done via
the TRF `XXA` tag — bbp has no `--accelerated` command-line flag;
acceleration is configured per-round in the TRF file — so this is
currently an internal-consistency tool, not yet a full A.7 run for
the Baku configuration.

```bash
caissify-pairings-rtg --players 100 --rounds 9 -n 50 --accelerated -o ./baku_fixtures/
```

## Round-robin — quick start

```python
from caissify_pairings import generate_pairings

# Single round-robin: pair round 1 of an 8-player event.
pairings = generate_pairings(
    system="round_robin",
    players=players,            # any 8 players (list of dicts)
    previous_pairings=set(),    # ignored — RR is deterministic
    round_number=1,
    total_rounds=7,             # n - 1 for n=8
)

# Double round-robin: 14 rounds, each pair meets twice with reversed colours.
pairings = generate_pairings(
    system="round_robin",
    players=players,
    previous_pairings=set(),
    round_number=8,             # first round of cycle 2
    total_rounds=14,
    cycles=2,
)
```

Need the full schedule up front (e.g. to print all rounds at once)?

```python
from caissify_pairings.engines.round_robin import berger_schedule

# Returns 7 rounds × (n/2) pairs as (white_pairing_no, black_pairing_no).
all_rounds = berger_schedule(8)
```

Pairing numbers are taken from each player's `starting_number`
(ascending). Odd player counts get one bye per round (each player byes
exactly once over a single cycle).

## Casual engine — quick start

```python
from caissify_pairings import generate_pairings

pairings = generate_pairings(
    system="casual",
    players=players,
    previous_pairings=set(),
    round_number=1,
    total_rounds=5,
    bye_type="F",            # "F" full-point, "H" half-point, "U" PAB, …
    max_byes_per_player=1,   # each player gets at most one bye
)
```

The casual engine follows a very small rulebook:

1. Players sorted by `(-score, -rating, id)`.
2. Round 1 uses the Dutch half-split (top half vs bottom half).
3. Later rounds pair greedily within score groups; unmatched players
   float down one bracket.
4. Odd fields award one bye to the lowest-scored eligible player.
5. Colours: perfect alternation > avoid a 3-in-a-row streak > minimise
   colour imbalance.

It never mutates your input dicts. Use `dutch` if you need FIDE A.7
behaviour — the two engines share the exact same input/output contract,
so switching is a one-line change.

## What does not work yet

Being honest up front — these are known limitations you will hit if your
use-case is beyond them:

- **Swiss systems other than Dutch are not implemented yet.** No Dubov,
  Burstein, or Monrad generators — those are on the roadmap.
- **Large-tournament fixture match rates are lower** against pre-recorded
  `bbpPairings` outputs for 40+ players (e.g. 40p/9r reports ~11% exact
  pair agreement on a handful of fixtures). The 5000-tournament A.7
  conformance benchmark tops out at 20p/9r, where the engine is at 0
  discrepancies; at larger scales tie-breaking divergences are expected.
- **Baku Acceleration is not yet A.7 cross-validated.** The Dutch engine
  passes A.7 on unaccelerated tournaments; the accelerated path is
  unit-tested and self-consistent but has not been run through a full
  5000-tournament `XXA`-enabled cross-check against `bbpPairings`.
- **No tournament-director UI.** This is an engine / library, not a
  standalone product.
- **Not FIDE-endorsed.** A.7 conformance is met technically; endorsement
  is a separate administrative process with the FIDE TEC (formerly SPP)
  Commission.
- **API is not stabilised.** Version `0.x` may introduce breaking changes.
  API will be frozen at `1.0.0`.

If any of these block you, please open an issue — priorities are driven by
real user needs.

## Using an engine directly

```python
from caissify_pairings.engines.dutch import dutch_pairings

pairings = dutch_pairings(
    players=players,
    previous_pairings=set(),
    round_number=1,
    total_rounds=9,
)
```

## Extending

New pairing systems plug in via a small registry.

```python
from caissify_pairings.base import BasePairingEngine

class MyEngine(BasePairingEngine):
    name = "my_system"

    def generate_pairings(self) -> list[dict]:
        ...
```

Register it in `caissify_pairings/engines/__init__.py` to expose it through
the top-level `generate_pairings(system="my_system", ...)` call.

## Testing

```bash
# Fast suite (skip @slow 5000-tournament benchmarks)
pytest -m "not slow"

# Full suite including FIDE A.7 benchmarks (takes ~25 min)
pytest
```

## Release process (maintainers)

Secrets live in a git-ignored `.env` at the repo root.

```bash
cp .env.example .env
# edit .env and set PYPI_API_TOKEN=pypi-...
```

Then, after bumping the version in `pyproject.toml`, updating
`CHANGELOG.md`, committing, and tagging `vX.Y.Z`:

```bash
scripts/release.sh --dry-run   # build + twine check only
scripts/release.sh --test      # upload to TestPyPI
scripts/release.sh             # upload to PyPI
```

The script builds an sdist + wheel in an isolated venv, runs
`twine check`, uploads to the chosen index, and then verifies by
installing the published version in a throwaway venv.

**Never commit `.env`.** Rotate your PyPI token if it has ever been
shared or exposed.

## Acknowledgements

Cross-validated against the FIDE-endorsed reference implementations:

- **[bbpPairings](https://github.com/BieremaBoyzProgramming/bbpPairings)**
  by Bierema Boyz Programming — Apache-2.0.
- **[JaVaFo](https://www.rrweb.org/javafo/)** by Roberto Ricca.

Thanks to their authors for making these tools freely available.

## License

MIT — see [`LICENSE`](LICENSE).
