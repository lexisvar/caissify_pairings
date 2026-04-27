# `caissify-pairings`: `generate_pairings` and `fpc.check_trf` disagree on Dutch R3

> **Status.** **Fixed in v0.4.4.** Root cause: the caller was omitting
> `float_history` from round 2 onward, causing the two pairing surfaces to
> diverge silently. The fix adds a `MissingFloatHistoryWarning` and the
> regression is covered by `tests/test_engine_surface_parity.py`.
> Reproducer: `doc/issue_0_4_3/repro_engine_divergence.py` in this repository.
> See the v0.4.4 entry in `CHANGELOG.md` for the full explanation.
>
> **Audience.** `caissify-pairings` maintainers. The desktop side
> (`caissify_tm`) and the Caissify HTTP API are *not* implicated by this
> report — both were audited end-to-end and ruled out (see
> [§4](#4-what-was-ruled-out-on-the-desktop-side) below).

## 1. Summary

`caissify_pairings` exposes two pairing surfaces that should agree but
don't:

1. **`generate_pairings(...)`** (and its CLI entry point
   `caissify-pairings`) — used to *produce* a round.
2. **`fpc.check_trf(trf_text)`** — used to *verify* a round (re-pairs
   each round from the TRF and compares to what the TRF records).

For the same logical state (10 players, 2 rounds played, no
constraints), the two paths return different R3 pairings:

| Engine path | R3 pairings produced |
| --- | --- |
| `caissify-pairings` CLI (`generate_pairings`) | bd1 `3 vs 2`, bd2 `1 vs 8`, bd3 `5 vs 6`, bd4 `9 vs 10`, bd5 `7 vs 4` |
| `caissify_pairings.fpc.check_trf` | bd1 `1 vs 10`, bd2 `3 vs 2`, bd3 `7 vs 4`, bd4 `5 vs 8`, bd5 `9 vs 6` |

Both paths claim to implement FIDE Dutch (C.04.A §A). Each
individually is internally consistent — neither violates colour, score,
or already-played constraints. They are simply different valid (or
"valid-looking") pairings of the same standings.

`check_trf`'s job is to be the oracle for `generate_pairings`. If they
disagree, by definition one of them is wrong, and any TRF produced by
the other will fail `fpc` validation forever.

## 2. Concrete impact

This surfaces in the Caissify desktop tournament manager
(`caissify_tm`) as follows:

1. The arbiter generates R3 via the desktop, which calls
   `caissify-pairings` (PATH A above). The pairings are stored and
   played.
2. The arbiter clicks **Validate tournament** in the desktop (which
   pipes the generated TRF to `fpc.check_trf`, PATH B above).
3. The Validate dialog reports R3 as "6 discrepancies" against an
   "engine-only" pairing that *the same package* would never have
   produced via the path that actually paired the round.

End-user effect: the arbiter cannot tell whether their tournament is
valid, because the package contradicts itself. R1, R2, and R4 in the
real tournament all match — only R3 diverges, which is exactly the
shape you'd expect from a tie-breaking or float-history disagreement
that happens to bite at one specific bracket configuration but not
others.

A screenshot of the in-app Validate dialog showing the divergence is
attached as `doc/assets/validate-r3-divergence.png` (kept here in the
desktop repo; happy to ship it as part of an upstream issue too).

## 3. Reproducer

`doc/issue_0_4_3/repro_engine_divergence.py` in this repository.

```bash
pip install 'caissify-pairings>=0.4.2,<0.4.4'
python doc/issue_0_4_3/repro_engine_divergence.py
```

The script:

1. Hard-codes the 10-player roster and the R1+R2 results that the
   desktop's local SQLite DB contains.
2. Builds the **exact JSON shape** that
   `caissify_tm/src-tauri/src/commands/pairing.rs` writes to
   `caissify-pairings`'s stdin, and reads back the R3 pairings the
   CLI produces (PATH A).
3. Generates a TRF the way `caissify_tm/src/lib/trf.ts` does (R1+R2
   played, R3 pairings filled in from PATH A's output, no float
   markers anywhere because the TRF generator does not emit them),
   pipes it through `caissify_pairings.fpc.check_trf` (PATH B), and
   prints the report.

PATH A and PATH B print different R3 pairings on `0.4.2`. PATH A's
output is byte-identical to what's in the desktop DB; PATH B's output
is byte-identical to the "engine only" list shown to the arbiter.

The script is dependency-free apart from `caissify-pairings` itself.
Recommended next step on the package side: copy it into
`caissify_pairings/tests/`, turn the divergence into an `assert`, and
let CI go red.

### 3.1 Observed output (abridged, on 0.4.2)

```
========================================================================
PATH A — caissify-pairings CLI  (generate_pairings)
========================================================================
Engine R3 pairings (normalised):
  bd 1: 3 vs 2
  bd 2: 1 vs 8
  bd 3: 5 vs 6
  bd 4: 9 vs 10
  bd 5: 7 vs 4

========================================================================
PATH B — caissify_pairings.fpc.check_trf
========================================================================
check_trf report (round 3 only):
  trf_pairings    : 1v8, 3v2, 7v4, 5v6, 9v10
  engine_pairings : 1v10, 3v2, 7v4, 5v8, 9v6
  match           : False
  discrepancies   :
    TRF only    : 1 vs 8, 5 vs 6, 9 vs 10
    Engine only : 1 vs 10, 5 vs 8, 6 vs 9
```

## 4. What was ruled out on the desktop side

Before opening this report, the desktop side was audited against the
exact tournament state in the user's local DB
(`tournament_id = 25`):

- ❌ **Manual pairing override.** No edits via `save_pairings` after
  the round was generated; user confirms they only entered results.
- ❌ **Forbidden pairs.** `forbidden_pairs` table is empty for this
  tournament.
- ❌ **Pre-assigned byes.** `pre_assigned_byes` is empty.
- ❌ **Withdrawals.** No row in `players` has `withdrawn_after_round`
  set.
- ❌ **Late-edited results.** `result_history` contains only entries
  for R3 itself (entered after R3 was generated) — no edits to R1 or
  R2 results that would have changed the standings R3 was paired
  against.
- ❌ **Stale React Query cache, stale TRF, etc.** The Validate flow
  reads straight from SQLite via `validate.rs`, regenerates the TRF
  per request, and pipes it to `check_trf` over stdin. No HTTP, no
  caches, no Caissify backend involved.

That leaves PATH A vs PATH B as the only two moving parts that could
disagree, and the reproducer confirms they do.

## 5. Suspected causes (ranked)

These are hypotheses, in order of how strongly the symptom shape
points at them. The fix that finally lands may be one of these or
something adjacent — listed mostly so you can decide where to start
diffing.

### 5.1 `float_history` — most likely

`generate_pairings` accepts `float_history` as an explicit per-player
input. The desktop sends `[]` for every player every time (the desktop
does not currently track Dutch float history; the engine has always
been the source of truth on it).

`check_trf`, by contrast, has to *infer* float history from the TRF
round blocks (and any `f`/`F` markers, none of which the desktop
emits).

If the inference disagrees with "no floats so far" for R1/R2 — even by
e.g. classifying a downfloater in R2 as having a float that
`generate_pairings` never saw — R3's bracket-pairing step will pick
different opponents inside the 1.0-point group, which is exactly the
group that diverges in this reproducer.

Worth diffing:

- `caissify_pairings/engines/dutch/dutch_pairings.py` — what does it
  default `float_history` to when missing or empty? Does it ever
  *re-derive* it from `previous_pairings`?
- `caissify_pairings/fpc.py` (or wherever `check_trf` lives) — what
  does it pass into the engine for round R after parsing R-1's TRF
  block? Is it inferring floats from "player went up/down a bracket"
  even when the TRF carries no float markers?

### 5.2 Colour-history reconstruction

`check_trf` reads `w`/`b` codes from each TRF round block;
`generate_pairings` takes a `color_hist: ["white", "black", ...]` list.
If `check_trf` builds a slightly different list — for example, by
counting an unplayed-bye round as a colour, or by indexing colour
preference differently when a player skipped a round — colour-balance
inside the 1.0-point bracket will pick different opponents.

### 5.3 Tie-breaking inside a score bracket

FIDE Dutch (C.04.A §A.7) has many tie-break sub-rules — transposition
order, exchange, `S1`/`S2` sort, etc. If `check_trf` and
`generate_pairings` end up calling different `dutch_pairings(...)`
overloads (e.g. one through the public
`caissify_pairings.generate_pairings(system="dutch", ...)` wrapper and
the other directly into `engines.dutch.dutch_pairings(...)`), and the
two wrappers default kwargs differently, the tie-break order can flip.

### 5.4 Player-id vs starting-number indexing

`generate_pairings` is given opaque ids (the desktop sends the SQLite
primary key); `check_trf` only has TRF starting numbers. If anywhere
inside the algorithm the engine tie-breaks on the *input id* rather
than on the canonical `(score, rating, starting_number)` tuple, the
two paths will deterministically disagree on opponents inside any
bracket that needs a tie-break — which, again, is exactly the shape of
this reproducer.

The reproducer happens to use `id == starting_number` to sidestep this
class of bug for the desktop call. The package itself should
nevertheless be id-agnostic everywhere it pairs.

## 6. Recommended fix shape

Whichever of 5.1–5.4 turns out to be the cause, the structural fix is
the same:

1. **Make `fpc.check_trf` literally call `generate_pairings(...)`**
   per round — the same public function with the same shape of input
   — instead of going through a privately-imported
   `dutch_pairings(...)` (or equivalent). Single source of truth, no
   drift possible by construction.
2. **Stop inferring `float_history` from a TRF that carries no float
   markers.** If the TRF has no `f`/`F` codes for a round, treat that
   as "no floats", not as "float history we'll re-derive from
   bracket movement".
3. **Add a regression test** that runs both surfaces against the
   tournament shape in `repro_engine_divergence.py` and asserts the
   two outputs agree as multisets of `{frozenset({white, black})}`.
   The fact that 0.4.x ships two pairing surfaces and no test that
   they agree is the actual root cause of "any future Dutch tweak can
   silently re-introduce this".

## 7. What the desktop side will / won't change

- **Won't change** (intentionally): the desktop will keep pinning
  `caissify-pairings>=0.4.3,<0.5.0` and will pick up the fix
  automatically when a 0.4.x patch ships. No desktop release is
  required to consume the fix.
- **Will likely change anyway** (not blocking on this issue): the
  desktop's Validate dialog currently says *"Manual overrides will
  show up here as a transparency signal — they are not errors."* That
  copy is misleading when the two engine paths disagree on a round
  the user never edited. It will be softened to acknowledge that the
  underlying package can disagree with itself, and to surface a 3-way
  diff (TRF actual / `generate_pairings` / `check_trf`) so the
  arbiter can see at a glance whether Validate is reporting a real
  problem or this internal divergence.

## 8. Contact / artifacts to attach to an upstream issue

- This document: `doc/CAISSIFY_PAIRINGS_DIVERGENCE.md`
- The reproducer: `doc/issue_0_4_3/repro_engine_divergence.py`
- The screenshot of the Validate dialog showing the in-app symptom:
  `doc/assets/validate-r3-divergence.png` (optional, but useful for
  framing the user-visible impact).
- Affected `caissify-pairings` versions: `0.4.2` confirmed; `0.4.3`
  expected (no Dutch-algorithm change in 0.4.3 changelog).
- Affected pairing system: `dutch` (FIDE C.04.A §A). Casual Swiss and
  round-robin are not exercised by `check_trf` in the same way and
  are not part of this report.
