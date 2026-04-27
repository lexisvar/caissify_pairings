"""
Reproduce a `caissify-pairings` internal divergence between two of its
own pairing surfaces:

  PATH A — `caissify_pairings.generate_pairings(...)` (used via the CLI
           `caissify-pairings` from the desktop's `pairing.rs`).
  PATH B — `caissify_pairings.fpc.check_trf(trf_text)` (used by the
           desktop's `validate.rs` to verify a TRF round-by-round).

For the same logical state — 10 players, 2 rounds played, no
constraints (no forbidden pairs, no pre-assigned byes, no withdrawals,
no float markers) — the two paths return different R3 pairings on
`caissify-pairings == 0.4.2` (and almost certainly 0.4.3, since the
0.4.3 release notes do not mention any Dutch algorithm change).

Usage:

    pip install 'caissify-pairings>=0.4.2,<0.5.0'
    python scripts/repro_engine_divergence.py

Expected: PATH A and PATH B print different R3 pairings.

See `doc/issue_0_4_3/CAISSIFY_PAIRINGS_DIVERGENCE.md` for the full write-up.
This bug was fixed in v0.4.4; this script reproduces the pre-fix behaviour
when run against caissify-pairings>=0.4.2,<0.4.4.
"""
import json
import subprocess
import sys
from pprint import pprint

PLAYERS = [
    # (sn, name, rating, fide_id)
    (1, "Carlsen, Magnus",        2840, 1503014),
    (2, "Kasparov, Garry",        2812, 4100018),
    (3, "Nakamura, Hikaru",       2810, 2016192),
    (4, "Kramnik, Vladimir",      2753, 4101588),
    (5, "Gukesh D",               2732, 46616543),
    (6, "Nepomniachtchi, Ian",    2729, 4168119),
    (7, "Topalov, Veselin",       2717, 2900084),
    (8, "Svidler, Peter",         2682, 4102142),
    (9, "Karpov, Anatoly",        2617, 4100026),
    (10, "Shirov, Alexei",        2604, 2209390),
]

# Pairings as they're stored in the desktop DB for tournament 25.
# Format: (white_sn, black_sn, result_str)
R1 = [
    (1, 6,  "1-0"),
    (7, 2,  "0-1"),
    (3, 8,  "0.5-0.5"),
    (9, 4,  "1-0"),
    (5, 10, "0.5-0.5"),
]
R2 = [
    (2, 1,  "1-0"),
    (10, 3, "0-1"),
    (4, 5,  "0.5-0.5"),
    (6, 7,  "1-0"),
    (8, 9,  "1-0"),
]
# What the desktop produced for R3 via PATH A (also what the TRF stores):
R3_TRF = [
    (3, 2,  "1-0"),
    (1, 8,  "0-1"),
    (5, 6,  "1-0"),
    (9, 10, "0.5-0.5"),
    (7, 4,  "1-0"),
]

# --- 1) score / colour history after R1+R2 ---------------------------------

def score(result, side):
    """side: 'w' or 'b'"""
    if result == "1-0":     return 1.0 if side == "w" else 0.0
    if result == "0-1":     return 0.0 if side == "w" else 1.0
    if result == "0.5-0.5": return 0.5
    return 0.0

scores     = {sn: 0.0 for sn, *_ in PLAYERS}
color_hist = {sn: []  for sn, *_ in PLAYERS}
prev_pairs = set()

for played in (R1, R2):
    for w, b, r in played:
        scores[w] += score(r, "w")
        scores[b] += score(r, "b")
        color_hist[w].append("white")
        color_hist[b].append("black")
        prev_pairs.add((min(w, b), max(w, b)))

def make_engine_input(round_number=3):
    """The exact JSON shape `pairing.rs` emits to the CLI on stdin."""
    return {
        "system": "dutch",
        "players": [
            {
                "id":              sn,
                "name":            name,
                "score":           scores[sn],
                "rating":          rating,
                "starting_number": sn,
                "color_hist":      color_hist[sn],
                "float_history":   [],
                "bye_count":       0,
            }
            for sn, name, rating, _fide in PLAYERS
        ],
        "previous_pairings":   [list(p) for p in sorted(prev_pairs)],
        "round_number":        round_number,
        "total_rounds":        9,
        "bye_value":           0.5,
        "max_byes_per_player": 1,
    }

# --- 2) build a TRF the way the desktop's trf.ts emits (best-effort) ------

def fmt_round_block(sn, round_pairings):
    """Return the 10-char ' NNNN c r ' block for player `sn` in this round."""
    for w, b, r in round_pairings:
        if w == sn or b == sn:
            opp   = b if w == sn else w
            color = "w" if w == sn else "b"
            if   r == "1-0":     code = "1" if color == "w" else "0"
            elif r == "0-1":     code = "0" if color == "w" else "1"
            elif r == "0.5-0.5": code = "="
            else:                code = " "
            return f" {opp:>4} {color} {code}"
    return f" {0:>4} - {' '}"  # didn't play this round

def build_trf(include_r3=True):
    rounds_played = [R1, R2] + ([R3_TRF] if include_r3 else [])
    points_after = {sn: 0.0 for sn, *_ in PLAYERS}
    for played in rounds_played:
        for w, b, r in played:
            points_after[w] += score(r, "w")
            points_after[b] += score(r, "b")
    lines = [
        "012 Knox Roub Robing",
        "022 Sydney",
        "032 AUS",
        "042 2026/04/24",
        "052 2026/04/29",
        "062 10",
        "072 10",
        "082 0",
        "092 Individual: Swiss-System (FIDE Dutch)",
        "122 90+30",
        "XXR 9",
    ]
    ranked  = sorted(PLAYERS, key=lambda p: (-points_after[p[0]], -p[2], p[1]))
    rank_of = {p[0]: i + 1 for i, p in enumerate(ranked)}
    for sn, name, rating, fide in PLAYERS:
        line = (
            f"001 {sn:>4}      "
            f"{name:<33}"
            f"{rating:>4} "
            f"AUS "
            f"{fide:>11} "
            f"            "
            f"{points_after[sn]:>4.1f} "
            f"{rank_of[sn]:>4}"
        )
        for round_idx in range(1, 4):
            played = (
                rounds_played[round_idx - 1]
                if round_idx - 1 < len(rounds_played)
                else []
            )
            line += " " + fmt_round_block(sn, played)
        lines.append(line)
    return "\r\n".join(lines) + "\r\n"

# --- 3) drive both engine paths -------------------------------------------

def run_generate(round_number=3):
    inp = make_engine_input(round_number)
    p = subprocess.run(
        ["caissify-pairings"],
        input=json.dumps(inp),
        capture_output=True, text=True, timeout=10,
    )
    if p.returncode != 0:
        print("generate_pairings stderr:\n", p.stderr)
        sys.exit(1)
    return json.loads(p.stdout)

def run_check_trf(trf_text):
    from caissify_pairings.fpc import check_trf
    return check_trf(trf_text)

# --- 4) run + report -------------------------------------------------------

print("=" * 72)
print("PATH A — caissify-pairings CLI  (generate_pairings)")
print("=" * 72)
gen = run_generate(round_number=3)
print("Engine returned for R3 (raw):")
pprint(gen)
pairs_list = gen if isinstance(gen, list) else gen.get("pairings", [])
print()
print("Engine R3 pairings (normalised):")
for pair in pairs_list:
    if isinstance(pair, dict):
        w  = pair.get("white_id") or pair.get("white") or pair.get("w")
        b  = pair.get("black_id") or pair.get("black") or pair.get("b")
        bd = pair.get("table")    or pair.get("board") or "?"
        print(f"  bd {bd}: {w} vs {b}")
    else:
        print(f"  {pair}")
print()

print("=" * 72)
print("PATH B — caissify_pairings.fpc.check_trf")
print("=" * 72)
trf = build_trf(include_r3=True)
print("TRF being fed to check_trf:")
print("-" * 72)
print(trf, end="")
print("-" * 72)
report = run_check_trf(trf)
print("check_trf report:")
pprint(report)
