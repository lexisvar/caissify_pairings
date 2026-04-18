"""
CLI entry point for caissify-pairings.

Modes:
    caissify-pairings                  — read JSON from stdin, output pairings
    caissify-pairings --check FILE.trf — FPC: check a TRF file against engine
    caissify-pairings-check FILE.trf   — FPC shortcut
    caissify-pairings-rtg [options]    — RTG: generate random tournaments
"""

from __future__ import annotations

import json
import sys

from caissify_pairings import generate_pairings


def main() -> None:
    # If --check flag is present, delegate to FPC
    if len(sys.argv) >= 2 and sys.argv[1] == "--check":
        from caissify_pairings.fpc import main as fpc_main
        # Shift argv so fpc_main sees the file as argv[1]
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

    # Convert list-of-lists to set-of-tuples for previous_pairings
    raw_pairings = data.get("previous_pairings", [])
    previous_pairings = {(a, b) for a, b in raw_pairings}

    # Forward engine-specific options
    kwargs = {}
    if "bye_value" in data:
        kwargs["bye_value"] = data["bye_value"]
    if "max_byes_per_player" in data:
        kwargs["max_byes_per_player"] = data["max_byes_per_player"]

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
    print()  # trailing newline


if __name__ == "__main__":
    main()
