"""
CLI entry point for caissify-pairings.

Modes:
    caissify-pairings                  — read JSON from stdin, output pairings
    caissify-pairings --check FILE.trf — FPC: check a TRF file against engine
    caissify-pairings-check FILE.trf   — FPC shortcut
    caissify-pairings-rtg [options]    — RTG: generate random tournaments

Input JSON shape (stdin mode)::

    {
        "system":            "dutch",       # or "casual", "round_robin"
        "players":           [...],
        "previous_pairings": [[1, 3], ...],
        "round_number":      1,
        "total_rounds":      9,
        ...engine-specific kwargs...
    }

Any JSON key that is **not** one of the core contract fields above
(``system``, ``players``, ``previous_pairings``, ``round_number``,
``total_rounds``) is forwarded verbatim to the selected engine as a
keyword argument. This is how ``accelerated`` (Dutch / Baku),
``cycles`` (round-robin), ``initial_color`` (Dutch), ``bye_value``,
``bye_type``, ``max_byes_per_player``, etc. reach the engine without
the CLI having to know about them individually.
"""

from __future__ import annotations

import json
import sys

from caissify_pairings import generate_pairings

# Keys that the CLI consumes directly as positional arguments to
# ``generate_pairings``. Everything else is forwarded as a kwarg.
_RESERVED_KEYS = frozenset(
    {
        "system",
        "players",
        "previous_pairings",
        "round_number",
        "total_rounds",
    }
)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--check":
        from caissify_pairings.fpc import main as fpc_main
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        fpc_main()
        return

    raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"error": "Empty input. Provide JSON on stdin."}), file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON: {e}"}), file=sys.stderr)
        sys.exit(1)

    system = data.get("system", "dutch")
    players = data.get("players", [])
    round_number = data.get("round_number", 1)
    total_rounds = data.get("total_rounds", 9)

    raw_pairings = data.get("previous_pairings", [])
    previous_pairings = {(a, b) for a, b in raw_pairings}

    # Forward every non-reserved top-level key to the engine as a kwarg.
    # This lets callers supply accelerated=true, cycles=2, initial_color,
    # bye_value, bye_type, max_byes_per_player, ... without the CLI
    # needing to know about them individually.
    kwargs = {k: v for k, v in data.items() if k not in _RESERVED_KEYS}

    try:
        pairings = generate_pairings(
            system=system,
            players=players,
            previous_pairings=previous_pairings,
            round_number=round_number,
            total_rounds=total_rounds,
            **kwargs,
        )
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    json.dump(pairings, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
