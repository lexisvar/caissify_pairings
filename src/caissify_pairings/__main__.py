"""
CLI entry point for caissify-pairings.

Reads a JSON object from stdin, runs the specified pairing engine,
and writes the resulting pairings as JSON to stdout.

Usage:
    echo '{"system":"dutch","players":[...],...}' | caissify-pairings
    caissify-pairings < tournament_state.json
"""

from __future__ import annotations

import json
import sys

from caissify_pairings import generate_pairings


def main() -> None:
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
