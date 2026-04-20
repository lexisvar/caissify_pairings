# caissify-pairings

Chess tournament pairing engines for Python. Currently implements the
**FIDE Dutch System (C.04.3)**, cross-validated against the FIDE-endorsed
reference `bbpPairings` to full **A.7 conformance**.

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

Measured against [`bbpPairings`](https://github.com/BieremaBoyzvoortMediaFoundation/bbpPairings)
(FIDE-endorsed, C++) using our Random Tournament Generator + Free Pairings
Checker pipeline:

| Benchmark | Rounds checked | Discrepancies | FIDE target | Result |
|-----------|----------------|---------------|-------------|--------|
| 5000 × 20-player / 9-round | 45,000 | **0** | ≤ 10 | ✅ PASS |
| 5000 × 10-player / 5-round | 25,000 | **0** | ≤ 10 | ✅ PASS |

See [`doc/ENGINE_STATUS.md`](doc/ENGINE_STATUS.md) for the full story
and [`doc/DIVERGENCE_TESTING.md`](doc/DIVERGENCE_TESTING.md) for the
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
- Full TRF16 round-trip parsing/writing.
- Free Pairings Checker (FPC) for validating existing TRF files.
- Random Tournament Generator (RTG) for test corpora.
- Pure-Python, single runtime dependency (`networkx`).

## What does not work yet

Being honest up front — these are known limitations you will hit if your
use-case is beyond them:

- **Only the Dutch system is implemented.** No Accelerated Dutch, Burstein,
  Monrad, or round-robin generators yet.
- **Large-tournament fixture match rates are lower** against pre-recorded
  `bbpPairings` outputs for 40+ players (e.g. 40p/9r reports ~11% exact
  pair agreement on a handful of fixtures). The 5000-tournament A.7
  conformance benchmark tops out at 20p/9r, where the engine is at 0
  discrepancies; at larger scales tie-breaking divergences are expected.
- **No tournament-director UI.** This is an engine / library, not a
  standalone product.
- **No FIDE endorsement.** A.7 conformance is met technically; endorsement
  is a separate process that has not been pursued.
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

## Acknowledgements

Cross-validated against the FIDE-endorsed reference implementations:

- **[bbpPairings](https://github.com/BieremaBoyzvoortMediaFoundation/bbpPairings)**
  by Bierema Boyzvoort Media Foundation — Apache-2.0.
- **[JaVaFo](https://javafo.sourceforge.net/)** by Roberto Ricca.

Thanks to their authors for making these tools freely available.

## License

MIT — see [`LICENSE`](LICENSE).
