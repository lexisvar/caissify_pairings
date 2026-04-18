# caissify-pairings

Pluggable chess tournament pairing engines for Swiss-system and other tournament formats.

## Supported Engines

| Engine | FIDE Ref | Status |
|--------|----------|--------|
| **FIDE Dutch** | C.04.3 (Feb 2026) | ✅ Complete — 131 tests (121 passing, 10 skipped/JavaFo) |
| Swiss (casual) | — | 🔜 Planned |
| Burstein | C.04.4 | 🔜 Planned |

## Installation

```bash
# From source (development)
pip install -e /path/to/caissify_pairings

# Or as a dependency
pip install caissify-pairings
```

## Quick Start

```python
from caissify_pairings import generate_pairings

players = [
    {"id": 1, "name": "Carlsen", "score": 2.0, "rating": 2830, "starting_number": 1,
     "color_hist": ["white", "black"], "float_history": [], "bye_count": 0},
    {"id": 2, "name": "Firouzja", "score": 2.0, "rating": 2785, "starting_number": 2,
     "color_hist": ["black", "white"], "float_history": [], "bye_count": 0},
    {"id": 3, "name": "Ding", "score": 1.5, "rating": 2780, "starting_number": 3,
     "color_hist": ["white", "black"], "float_history": [], "bye_count": 0},
    {"id": 4, "name": "Nepo", "score": 1.5, "rating": 2775, "starting_number": 4,
     "color_hist": ["black", "white"], "float_history": [], "bye_count": 0},
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

## Using a Specific Engine Directly

```python
from caissify_pairings.engines.dutch import dutch_pairings

pairings = dutch_pairings(
    players=players,
    previous_pairings=set(),
    round_number=1,
    total_rounds=9,
)
```

## CLI Usage

```bash
# Pipe JSON input via stdin
echo '{"system": "dutch", "players": [...], "previous_pairings": [], "round_number": 1, "total_rounds": 9}' \
  | caissify-pairings

# Or from a file
caissify-pairings < tournament_state.json

# Output is JSON to stdout
```

### FIDE Endorsement Tools

```bash
# Free Pairings Checker (FPC) — validate a TRF file against the Dutch engine
caissify-pairings --check tournament.trf

# Random Tournament Generator (RTG) — generate simulated tournaments
caissify-pairings-rtg --players 20 --rounds 9 -n 100 -o ./output/
```

### Input JSON Schema

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

### Output JSON Schema

```json
[
  {"white_id": 1, "black_id": 4, "table": 1},
  {"white_id": 3, "black_id": 2, "table": 2}
]
```

## Architecture

```
caissify_pairings/
├── __init__.py          # Public API: generate_pairings()
├── __main__.py          # CLI entry point (JSON stdin → stdout)
├── base.py              # Abstract base class for all engines
├── fpc.py               # Free Pairings Checker (FIDE C.04.A)
├── rtg.py               # Random Tournament Generator (FIDE C.04.A)
├── trf.py               # TRF16 parser & builder
└── engines/
    ├── __init__.py      # Engine registry
    └── dutch.py         # FIDE Dutch System (C.04.3)
```

### Adding a New Engine

1. Create `engines/my_system.py`
2. Subclass `BasePairingEngine`
3. Register in `engines/__init__.py`

```python
from caissify_pairings.base import BasePairingEngine

class MyEngine(BasePairingEngine):
    name = "my_system"

    def generate_pairings(self) -> list[dict]:
        # Your algorithm here
        ...
```

## Zero Dependencies

This package has **no external dependencies** — pure Python stdlib only.
This makes it easy to bundle as a standalone binary (PyInstaller/Nuitka)
for desktop apps or use as a Tauri sidecar.

## License

MIT
