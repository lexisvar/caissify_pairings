"""
Free pairings checker for team Swiss tournaments (C.04.6, TRF26).

Reads a TRF26 team tournament, rebuilds it round by round, re-pairs every
round with the embedded team engine and reports where the file and the
engine differ: pairs, the pairing-allocated bye, and the team with White
on board 1.

Teams the file did not pair in a round (absent, withdrawn, or with a
full-, half- or zero-point bye) are left out of that round, as the
arbiter did. The competition rules come from records 142-362 (type code,
initial-colour, scoring, board colours, PAB points, acceleration,
prohibited pairings); options passed to :func:`check_team_trf` override
them.

CLI::

    caissify-pairings-check --team tournament.trf
    caissify-pairings-check tournament.trf          # TRF26 team files are detected
    caissify-pairings-check --team --json tournament.trf
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from caissify_pairings.engines.team_swiss import (
    DOUBLE_FORFEIT,
    FORFEIT_LOSS,
    FORFEIT_WIN,
    PAB,
    PLAYED,
    TeamSwissEngine,
)
from caissify_pairings.team_trf import (
    engine_input,
    infer_initial_colour,
    parse_team_trf,
    settings_from_trf,
    team_rounds,
)

_PAIRED = (PLAYED, FORFEIT_WIN, FORFEIT_LOSS, DOUBLE_FORFEIT, PAB)


def _trf_round(entries: Dict[int, Dict]) -> Tuple[List[Tuple[int, int]], Optional[int], set]:
    pairs = set()
    bye = None
    paired = set()
    for tpn, e in entries.items():
        if e["type"] not in _PAIRED:
            continue
        paired.add(tpn)
        if e["type"] == PAB:
            bye = tpn
            continue
        opp = e["opponent"]
        if e["colour"] == "white":
            pairs.add((tpn, opp))
        elif e["colour"] == "black":
            pairs.add((opp, tpn))
        else:
            pairs.add((min(tpn, opp), max(tpn, opp)) + ("?",))
    return sorted(pairs), bye, paired


def check_team_trf(trf_content: str, baku_250_order: str = "spec", **overrides) -> Dict:
    """
    Check a TRF26 team tournament against the team engine.

    Returns::

        {
            "tournament_name": str,
            "total_rounds": int,
            "num_teams": int,
            "settings": {...},             # the competition rules used
            "warnings": [...],
            "rounds": [
                {"round": 1, "match": True, "trf_pairings": [...],
                 "engine_pairings": [...], "discrepancies": [...],
                 "trace": [...]},          # the engine's decisions
            ],
            "summary": {"rounds_checked", "rounds_matched",
                        "rounds_mismatched", "total_discrepancies"},
        }
    """
    parsed = parse_team_trf(trf_content, baku_250_order=baku_250_order)
    warnings = list(parsed["warnings"])
    rounds = team_rounds(parsed, warnings=warnings)
    settings = settings_from_trf(parsed)
    if "initial_color" not in settings:
        inferred = infer_initial_colour(parsed)
        if inferred:
            settings["initial_color"] = inferred
            warnings.append(f"No record 152: initial-colour {inferred} inferred from round 1")
    settings.update(overrides)
    total = parsed["tournament"].get("total_rounds") or (max(rounds) if rounds else 0)

    report_rounds: List[Dict] = []
    matched = mismatched = discrepancies_total = 0
    for rnd in sorted(rounds):
        trf_pairs, trf_bye, paired = _trf_round(rounds[rnd])
        if not paired:
            continue
        teams, _ = engine_input(parsed, rnd, paired_teams=paired)
        engine = TeamSwissEngine(
            players=teams, round_number=rnd, total_rounds=max(total, rnd), **settings
        )
        try:
            rows = engine.generate_pairings()
        except Exception as exc:  # pragma: no cover - reported, not raised
            report_rounds.append({
                "round": rnd, "match": False, "trf_pairings": _show(trf_pairs, trf_bye),
                "engine_pairings": [], "discrepancies": [f"Engine error: {exc}"],
            })
            mismatched += 1
            discrepancies_total += 1
            continue
        eng_pairs = sorted((r["white_id"], r["black_id"]) for r in rows if not r.get("bye"))
        eng_bye = next((r["white_id"] for r in rows if r.get("bye")), None)
        diffs = _compare(trf_pairs, trf_bye, eng_pairs, eng_bye)
        entry = {
            "round": rnd,
            "match": not diffs,
            "trf_pairings": _show(trf_pairs, trf_bye),
            "engine_pairings": _show(eng_pairs, eng_bye),
            "discrepancies": diffs,
            "trace": engine.trace,
        }
        if any(r.get("violations") for r in rows):
            entry["engine_violations"] = [
                {"white": r["white_id"], "black": r.get("black_id"), "violations": r["violations"]}
                for r in rows if r.get("violations")
            ]
        report_rounds.append(entry)
        if diffs:
            mismatched += 1
            discrepancies_total += len(diffs)
        else:
            matched += 1

    return {
        "tournament_name": parsed["tournament"].get("name", "(unknown)"),
        "total_rounds": total,
        "num_teams": len(parsed["teams"]),
        "settings": settings,
        "warnings": warnings,
        "rounds": report_rounds,
        "summary": {
            "rounds_checked": len(report_rounds),
            "rounds_matched": matched,
            "rounds_mismatched": mismatched,
            "total_discrepancies": discrepancies_total,
        },
    }


def _show(pairs, bye) -> List[Dict]:
    out = []
    for p in pairs:
        if len(p) == 3:
            out.append({"white": None, "black": None, "teams": [p[0], p[1]]})
        else:
            out.append({"white": p[0], "black": p[1]})
    if bye is not None:
        out.append({"white": bye, "black": None, "bye": True})
    return out


def _compare(trf_pairs, trf_bye, eng_pairs, eng_bye) -> List[str]:
    diffs: List[str] = []
    trf_set = {frozenset(p[:2]): p for p in trf_pairs}
    eng_set = {frozenset(p): p for p in eng_pairs}
    for key in sorted(set(trf_set) - set(eng_set), key=sorted):
        a, b = sorted(key)
        diffs.append(f"TRF only: {a} vs {b}")
    for key in sorted(set(eng_set) - set(trf_set), key=sorted):
        a, b = sorted(key)
        diffs.append(f"Engine only: {a} vs {b}")
    for key in sorted(set(trf_set) & set(eng_set), key=sorted):
        t, e = trf_set[key], eng_set[key]
        if len(t) == 3:
            continue  # colour unknown in the file
        if t[0] != e[0]:
            diffs.append(f"Colours {min(key)}-{max(key)}: TRF gives White on board 1 to {t[0]}, engine to {e[0]}")
    if trf_bye != eng_bye:
        diffs.append(f"Bye: TRF {trf_bye}, engine {eng_bye}")
    return diffs


def print_team_report(report: Dict, explain: bool = False) -> None:
    """Human-readable report on stdout."""
    print(f"Tournament: {report['tournament_name']}")
    print(f"Teams: {report['num_teams']}, Rounds: {report['total_rounds']}")
    for w in report["warnings"]:
        print(f"Warning: {w}")
    print()
    for r in report["rounds"]:
        if r["match"]:
            print(f"  Round {r['round']}: OK")
        else:
            print(f"  Round {r['round']}: MISMATCH")
            for d in r["discrepancies"]:
                print(f"    - {d}")
        if explain:
            for step in r.get("trace", []):
                print(f"      {step}")
    s = report["summary"]
    print()
    print(
        f"Summary: {s['rounds_matched']}/{s['rounds_checked']} rounds match, "
        f"{s['total_discrepancies']} discrepancies"
    )


def main(argv: Optional[List[str]] = None) -> int:
    """``caissify-pairings-check --team [--json] [--explain] FILE.trf``; returns the exit code."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog="caissify-pairings-check --team",
                                     description="Check a TRF26 team tournament against the C.04.6 engine")
    parser.add_argument("trf_file")
    parser.add_argument("--team", action="store_true", help="(implied) team mode")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--explain", action="store_true", help="show the engine's decisions per round")
    parser.add_argument("--baku-250-order", choices=["spec", "example"], default="spec",
                        help="field order of TRF26 record 250 (see doc/TEAM_SWISS.md)")
    args = parser.parse_args(argv)
    try:
        with open(args.trf_file, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"File not found: {args.trf_file}", file=sys.stderr)
        return 1
    logging.getLogger("caissify_pairings").setLevel(logging.ERROR)
    report = check_team_trf(text, baku_250_order=args.baku_250_order)
    if args.json:
        json.dump(report, sys.stdout, indent=2, default=str)
        print()
    else:
        print_team_report(report, explain=args.explain)
    return 1 if report["summary"]["rounds_mismatched"] else 0
