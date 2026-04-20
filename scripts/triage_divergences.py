#!/usr/bin/env python3
"""
Three-way triage of pairing divergences per FIDE C.04.A §A.7.

For each bbpPairings-generated tournament, classify every round into:

    MATCH          — all three engines agree
    OUR_BUG        — bbp and JaVaFo agree, we differ  (-> fix dutch.py)
    BBP_QUIRK      — we and JaVaFo agree, bbp differs (-> A.7 bucket 1:
                     RTG error / not our bug)
    JAVAFO_QUIRK   — we and bbp agree, JaVaFo differs (-> rare)
    THREE_WAY      — all three disagree                 (-> A.7 bucket 3:
                     rule-interpretation divergence)

The two FIDE-endorsed oracles are:
  * bbpPairings (vendor/bbpPairings/bbpPairings.exe) — v6.0.0, 2025 rules
  * JaVaFo      (vendor/javafo/javafo.jar)           — v2.2, 2016-rules era

See doc/DIVERGENCE_TESTING.md for the methodology.

Usage:
    python scripts/triage_divergences.py [NUM_PLAYERS] [NUM_ROUNDS] [COUNT] [SEED_OFFSET]

Defaults: 9 players, 5 rounds, 100 tournaments, seed offset 0.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _javafo as jvf  # noqa: E402
from caissify_pairings.fpc import check_trf as our_check_trf  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BBP_BINARY = PROJECT_ROOT / "vendor" / "bbpPairings" / "bbpPairings.exe"


# ---------------------------------------------------------------------------
# bbpPairings RTG
# ---------------------------------------------------------------------------

def bbp_generate(num_players: int, num_rounds: int, seed: int, out_path: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as cfg:
        cfg.write(f"PlayersNumber={num_players}\nRoundsNumber={num_rounds}\n")
        cfg_path = cfg.name
    try:
        subprocess.run(
            [str(BBP_BINARY), "--dutch", "-g", cfg_path, "-o", out_path, "-s", str(seed)],
            check=True, capture_output=True, timeout=30,
        )
    finally:
        os.unlink(cfg_path)


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

def _pairset_from_our_round(rd: dict) -> tuple[frozenset, int | None]:
    """Extract {frozenset of (a,b) pairs, bye} from our FPC round report."""
    pairs = set()
    bye = None
    for p in rd.get("engine_pairings", []):
        w = p.get("white")
        b = p.get("black")
        if p.get("bye") or b is None:
            bye = w
        else:
            pairs.add((min(w, b), max(w, b)))
    return frozenset(pairs), bye


def _pairset_from_trf_round(rd: dict) -> tuple[frozenset, int | None]:
    pairs = set()
    bye = None
    for p in rd.get("trf_pairings", []):
        w = p.get("white")
        b = p.get("black")
        if p.get("bye") or b is None:
            bye = w
        else:
            pairs.add((min(w, b), max(w, b)))
    return frozenset(pairs), bye


def classify(
    ours: tuple[frozenset, int | None],
    bbp: tuple[frozenset, int | None],
    jvfo: tuple[frozenset, int | None],
) -> str:
    o_eq_b = ours == bbp
    o_eq_j = ours == jvfo
    b_eq_j = bbp == jvfo
    if o_eq_b and o_eq_j:
        return "MATCH"
    if b_eq_j and not o_eq_b:
        return "OUR_BUG"
    if o_eq_j and not o_eq_b:
        return "BBP_QUIRK"
    if o_eq_b and not o_eq_j:
        return "JAVAFO_QUIRK"
    return "THREE_WAY"


def triage_tournament(num_players: int, num_rounds: int, seed: int, tmpdir: str) -> list[dict]:
    trf_path = os.path.join(tmpdir, f"t_{seed}.trf")
    bbp_generate(num_players, num_rounds, seed, trf_path)

    with open(trf_path) as f:
        trf_content = f.read()

    our_report = our_check_trf(trf_content)
    jvf_report = jvf.check_trf(trf_path)

    results: list[dict] = []
    for rd in our_report["rounds"]:
        rnd = rd["round"]
        ours = _pairset_from_our_round(rd)
        bbp_view = _pairset_from_trf_round(rd)
        jvf_rd = jvf_report["rounds"].get(rnd)
        if jvf_rd is None:
            continue
        if not jvf_rd.get("has_body"):
            # JaVaFo agrees with the TRF (i.e. with bbp).
            jvfo_view = bbp_view
        else:
            # JaVaFo only prints *differing* pairs. Reconstruct the full
            # JaVaFo pair-set from the TRF plus the adds/removes.
            trf_pairs, trf_bye = bbp_view
            javafo_only = frozenset(jvf_rd["javafo_only_pairs"])
            trf_only = frozenset(jvf_rd["trf_only_pairs"])
            jvfo_full = (trf_pairs - trf_only) | javafo_only
            jvfo_bye = jvf_rd["javafo_bye"] if jvf_rd["javafo_bye"] is not None else trf_bye
            jvfo_view = (frozenset(jvfo_full), jvfo_bye)
        verdict = classify(ours, bbp_view, jvfo_view)
        results.append({
            "seed": seed, "round": rnd, "verdict": verdict,
            "ours": ours, "bbp": bbp_view, "javafo": jvfo_view,
        })
    return results


def _fmt(p: tuple[frozenset, int | None]) -> str:
    pairs = ", ".join(f"{a}-{b}" for a, b in sorted(p[0]))
    bye = p[1]
    return f"[{pairs}]{' bye='+str(bye) if bye is not None else ''}"


def main() -> int:
    num_players = int(sys.argv[1]) if len(sys.argv) > 1 else 9
    num_rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    seed_offset = int(sys.argv[4]) if len(sys.argv) > 4 else 0

    if not jvf.is_available():
        print("JaVaFo not available (jar missing or no JVM). See scripts/_javafo.py.", file=sys.stderr)
        return 2
    if not BBP_BINARY.exists():
        print(f"bbpPairings not found at {BBP_BINARY}", file=sys.stderr)
        return 2

    print(f"# Triage — {num_players}p/{num_rounds}r × {count} tournaments (seeds {seed_offset}..{seed_offset+count-1})")
    print(f"# bbpPairings: {BBP_BINARY.name}")
    print(f"# JaVaFo:      {jvf.version()}")
    print()

    counts: Counter = Counter()
    interesting: list[dict] = []
    total_rounds = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(count):
            seed = seed_offset + i
            try:
                rows = triage_tournament(num_players, num_rounds, seed, tmpdir)
            except Exception as exc:
                print(f"seed {seed}: ERROR {exc}", file=sys.stderr)
                continue
            total_rounds += len(rows)
            for row in rows:
                counts[row["verdict"]] += 1
                if row["verdict"] != "MATCH":
                    interesting.append(row)

    print(f"## Rounds analyzed: {total_rounds}")
    for k in ("MATCH", "OUR_BUG", "BBP_QUIRK", "JAVAFO_QUIRK", "THREE_WAY", "PARSE_MISMATCH"):
        if k in counts:
            print(f"  {k:<14} {counts[k]:>5}")
    print()

    # Group non-match rows by verdict for detail
    by_verdict: dict[str, list[dict]] = {}
    for row in interesting:
        by_verdict.setdefault(row["verdict"], []).append(row)

    for verdict in ("OUR_BUG", "BBP_QUIRK", "THREE_WAY", "JAVAFO_QUIRK", "PARSE_MISMATCH"):
        rows = by_verdict.get(verdict, [])
        if not rows:
            continue
        print(f"## {verdict}  ({len(rows)})")
        for row in rows[:50]:
            print(f"  seed {row['seed']:>4} round {row['round']}:")
            print(f"    ours   = {_fmt(row['ours'])}")
            print(f"    bbp    = {_fmt(row['bbp'])}")
            print(f"    javafo = {_fmt(row['javafo'])}")
        if len(rows) > 50:
            print(f"  ... {len(rows)-50} more")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
