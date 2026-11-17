"""
``caissify-pairings --team``: pair one round of a team Swiss (C.04.6).

Two inputs, same JSON output as the other engines (one row per match,
``white_id`` = team with White on board 1, plus ``boards`` and the bye)::

    caissify-pairings --team tournament.trf [--round N]
    caissify-pairings --team < state.json

With a TRF26 file the competition rules come from the file (records
142-362) and the round defaults to the first one nobody has been paired
in yet. Teams with a 240 bye record for that round are not paired.

The JSON state is the engine input::

    {"teams": [{"id": 1, "tpn": 1, "name": "...", "history": [...]}, ...],
     "round_number": 3, "total_rounds": 9,
     "colour_preferences": "A", "primary_score": "mp", ...}
"""

from __future__ import annotations

import json
import logging
import sys
from typing import List, Optional

from caissify_pairings.engines.team_swiss import TeamSwissEngine


def _next_round(parsed) -> int:
    from caissify_pairings.team_trf import team_rounds

    rounds = team_rounds(parsed)
    nxt = 1
    for rnd in sorted(rounds):
        if any(e["type"] != "zpb" for e in rounds[rnd].values()):
            nxt = rnd + 1
    return nxt


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="caissify-pairings --team",
                                     description="Pair a round of a team Swiss tournament (FIDE C.04.6)")
    parser.add_argument("trf_file", nargs="?", help="TRF26 team tournament (default: JSON on stdin)")
    parser.add_argument("--round", type=int, default=None, help="round to pair (TRF input; default: the next one)")
    parser.add_argument("--total-rounds", type=int, default=None, help="total rounds (if the TRF has no record 142)")
    parser.add_argument("--explain", action="store_true", help="add the engine's decisions to the output")
    parser.add_argument("--baku-250-order", choices=["spec", "example"], default="spec")
    args = parser.parse_args(argv)
    logging.getLogger("caissify_pairings").setLevel(logging.ERROR)

    try:
        if args.trf_file:
            from caissify_pairings.team_trf import engine_input, parse_team_trf

            with open(args.trf_file, "r", encoding="utf-8") as f:
                parsed = parse_team_trf(f.read(), baku_250_order=args.baku_250_order)
            rnd = args.round or _next_round(parsed)
            total = args.total_rounds or parsed["tournament"].get("total_rounds") or rnd
            teams, options = engine_input(parsed, rnd)
            engine = TeamSwissEngine(players=teams, round_number=rnd, total_rounds=total, **options)
        else:
            raw = sys.stdin.read()
            if not raw.strip():
                print(json.dumps({"error": "Empty input. Provide JSON on stdin or a TRF26 file."}), file=sys.stderr)
                return 1
            data = json.loads(raw)
            teams = data.pop("teams", None) or data.pop("players", [])
            data.pop("system", None)
            rnd = data.pop("round_number", 1)
            total = data.pop("total_rounds", 9)
            prev = {tuple(p) for p in data.pop("previous_pairings", [])}
            engine = TeamSwissEngine(players=teams, previous_pairings=prev, round_number=rnd,
                                     total_rounds=total, **data)
        rows = engine.generate_pairings()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    if args.explain:
        json.dump({"round": engine.round_number, "pairings": rows, "trace": engine.trace}, sys.stdout, indent=2)
    else:
        json.dump(rows, sys.stdout, indent=2)
    print()
    return 0
