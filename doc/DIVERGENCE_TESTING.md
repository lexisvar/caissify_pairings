# Divergence Testing Methodology

> How we measure pairing-engine divergences against the FIDE-endorsed
> reference, and what the relevant FIDE rules say.

---

## 1. What we're testing

Our `caissify_pairings.engines.dutch.DutchEngine` is a Python implementation
of the **FIDE (Dutch) Swiss System**. To get FIDE software endorsement
(C.04.A Appendix), the engine must produce pairings that match an
already-endorsed reference engine to within a strict tolerance.

A **divergence** (or *discrepancy*) is a single pair in a single round where
our engine produces a different pairing than the reference engine for the
same input state.

The goals are:

- **Round-level**: count how many rounds contain at least one different pair.
- **Pair-level**: count how many individual pairs differ across all rounds.
- **Tournament-level**: aggregate across many random tournaments.

---

## 2. The FIDE references we use

### 2.1 The rule book (the *spec*)

The pairing rules themselves are defined in the **FIDE Handbook**:

| Section | What it covers |
|---------|----------------|
| **C.04.3** | *FIDE (Dutch) System* — the actual pairing algorithm: brackets, MWM criteria C.1–C.19, color allocation, floats, byes. |
| **C.04.A Appendix** | *Endorsement of a software program* — how a candidate engine becomes FIDE-endorsed (defines the A.6 / A.7 procedure). |
| **C.04.A §A.4** | Defines the **Free Pairings Checker (FPC)** that every endorsed engine must publish. |
| **C.04.A §A.5** | Defines the **Random Tournament Generator (RTG)**. |
| **C.04.A §A.7** | The cross-validation procedure used when other engines are already endorsed: **5000 random tournaments**, **at most 10 discrepancies allowed**. |

The 2025 Dutch rules are the ones currently in force (effective date
delayed to 2026). All matching is done against this version.

### 2.2 Where exactly FIDE defines "discrepancies"

FIDE talks about divergences in two distinct places, and they mean
different things. This matters because it tells us *what* we are
supposed to compare against.

**A.4 — FPC (consistency of a single TRF with its engine)**

> "*The checker must be able to read `FIDE_Report_File.fid` when coded in
> TRF16 […]. Then, for each round, the checker must rebuild the
> tournament, pair the round using the embedded pairing engine, and
> output a report describing which pairings are or are not consistent
> with those produced by the pairing engine.*"
> — C.04.A §A.4

A.4 is purely intrinsic: it asks whether the pairings recorded inside a
TRF match what *the program's own* pairing engine would have produced
for the same state. It does **not** set a numeric limit, and it does
**not** involve any other program. This is what our
`caissify_pairings.fpc.check_trf` implements, and what `bbpPairings.exe
-c` implements on the C++ side.

**A.7 — Cross-validation against another endorsed program (the ≤ 10 bar)**

> "*As, by definition, an external RTG is available, it will be used to
> generate 5000 random tournaments. Such tournaments will be given in
> input to the candidate FPC and each discrepancy, as long as there are
> at most 10 of them, will be collected.*"
> — C.04.A §A.7

> "*If the candidate has its own RTG, the latter is used to generate
> 5000 random tournaments, which will be then given in input to one (or
> more) of the available FPC(s). The analysis of the discrepancies is
> conducted in the same way as above.*"
> — C.04.A §A.7

This is the rule that sets the **"5000 tournaments / ≤ 10
discrepancies"** bar. Crucially, it is explicitly defined *against
another, already-endorsed program*:

- "*an external RTG is available*" — i.e. an RTG belonging to a program
  on the FIDE endorsed list (Annex-3 / FEP19);
- "*one (or more) of the available FPC(s)*" — i.e. the FPC of an
  endorsed program in the reverse direction.

FIDE even classifies any discrepancy found by this procedure into one of
three buckets:

> "*Such discrepancies may depend on either:
> - an error in the input file (i.e. they are the responsibility of the
>   endorsed program which provided the RTG), [or]
> - an error coming from the candidate, [or]
> - an interpretation divergence caused by unclear rules.*"
> — C.04.A §A.7

So to be unambiguous about what this repository measures:

| What | Against what | Where FIDE requires it | Numeric bound |
|------|-------------|------------------------|---------------|
| `caissify_pairings.fpc` vs. its own engine output (self-consistency) | **The engine itself** (intrinsic) | A.4 (FPC must exist and be consistent) | None — just "consistent" |
| Our RTG feeding our FPC (`tests/test_rtg_fpc_validation.py`) | **The engine itself** | Internal sanity check, **not** mandated by A.7 | We choose 0 |
| Path A — bbpPairings RTG → our FPC (`tests/test_cross_validation.py`) | **bbpPairings** (endorsed) | A.7 — "external RTG → candidate FPC" | **≤ 10 per 5000** |
| Path B — our RTG → bbpPairings FPC (`tests/test_cross_validation.py`) | **bbpPairings** (endorsed) | A.7 — "candidate RTG → available FPC" | **≤ 10 per 5000** |

The numbers we care about for endorsement come from the last two
rows (A.7 paths), **not** from self-consistency. A.7 is by
construction a comparison against another endorsed program; it cannot
be satisfied by an engine comparing itself to itself. See
[`FIDE_CONFORMANCE.md`](FIDE_CONFORMANCE.md) for the current A.7
numbers.

### 2.2 The reference engines (the *oracles*)

FIDE does not specify *which* endorsed program to use; A.7 just says
"an external RTG" and "one (or more) of the available FPC(s)". We use
**two** endorsed programs, because having two oracles lets us classify
each divergence into one of the three buckets FIDE itself defines in
§A.7 (see section 7 below).

#### bbpPairings (primary oracle)

- **Source:** <https://github.com/BieremaBoyzProgramming/bbpPairings>
- **Vendored at:** `vendor/bbpPairings/`
- **Binary used:** `vendor/bbpPairings/bbpPairings.exe` (built from source via `make`)
- **Version:** 6.0.0 — implements the **2025 Dutch rules**
- **License:** Apache-2.0

```bash
# RTG
bbpPairings.exe --dutch -g config.txt -o out.trf -s <seed>
# FPC
bbpPairings.exe --dutch tournament.trf -c
```

#### JaVaFo (secondary oracle)

- **Source:** <https://www.rrweb.org/javafo/>
- **Vendored at:** `vendor/javafo/javafo.jar`
- **Version:** 2.2 (Build 3223) — implements the **2016/2018-era Dutch rules**
- **License:** Free for use (including commercial), cite [rrweb.org/javafo](https://www.rrweb.org/javafo/).
- **Author:** Roberto Ricca, Secretary of the FIDE SPPC (the commission
  that *writes* C.04.3). Historically, bbpPairings was developed
  specifically to match JaVaFo's output.

```bash
# RTG
java -ea -jar vendor/javafo/javafo.jar -g <seed> -o out.trf
# FPC
java -ea -jar vendor/javafo/javafo.jar tournament.trf -c
# Pair the next round
java -ea -jar vendor/javafo/javafo.jar tournament.trf -p pairs.txt
```

A Python wrapper is provided in `scripts/_javafo.py` so dev tools can
call JaVaFo without bothering with the `java` command directly.

#### Why we need both

bbpPairings targets the 2025 rules; JaVaFo targets an older 2016-era
amendment. On any given tournament, the two oracles may themselves
disagree — that is exactly the *"interpretation divergence caused by
unclear rules"* situation that FIDE calls out in §A.7 bullet 3. If
our engine agrees with *either* oracle on a given round, that round is
compliant with at least one endorsed program's reading of C.04.3 — the
minimum bar for A.7.

The two-oracle triage is what turned the investigation around: several
of the "our bugs" we were chasing turned out to be cases where we
already agreed with JaVaFo and it was bbp that was out on a limb (see
section 8 below).

---

## 3. The data format: TRF16

Tournaments are exchanged as **TRF16** (Tournament Report Format 2016)
files — fixed-column ASCII text defined by the FIDE Technical Commission.
Both bbpPairings and our engine read and write this format.

A TRF file contains:

- Tournament metadata (`012`, `022`, `032`, …, `XXR` total-rounds tag).
- One `001` line per player with: starting number, name, rating, score,
  and a 10-character cell per played round encoding `<opponent> <color> <result>`.

Our parser/writer lives in `src/caissify_pairings/trf.py`; the FPC
front-end is `src/caissify_pairings/fpc.py`.

---

## 4. The two cross-validation paths (FIDE A.7)

The endorsement appendix prescribes two symmetric paths. Both must
produce **≤ 10 discrepancies across 5000 tournaments** for the
candidate engine to qualify.

```
                ┌────────────────────────────┐
   Path A:      │ bbpPairings RTG → our FPC  │
                └────────────────────────────┘

                ┌────────────────────────────┐
   Path B:      │   our RTG → bbpPairings FPC │
                └────────────────────────────┘
```

### 4.1 Path A — bbp generates, we check

1. `bbpPairings.exe --dutch -g cfg -o out.trf -s <seed>` produces a full
   TRF for an N-player M-round tournament (results are random; pairings
   are bbp's).
2. We run our FPC (`caissify_pairings.fpc.check_trf`) on `out.trf`.
3. For each round in the file, our FPC:
   - rebuilds the engine input from rounds `1..r-1`,
   - calls `DutchEngine.generate_pairings()`,
   - compares the result to the round-`r` pairings recorded in the TRF.
4. Each pair that differs counts as **one discrepancy**.

This catches cases where **our engine** disagrees with bbp on what the
correct pairing is.

### 4.2 Path B — we generate, bbp checks

1. `caissify_pairings.rtg.generate_tournament(...)` produces a TRF using
   our engine for pairings and a random model for results.
2. We invoke `bbpPairings.exe --dutch out.trf -c`.
3. bbp prints one `Round #N` header per round and one `<a> - <b>` line
   per pair it would have made differently.
4. Our test harness parses that output to count rounds-mismatched and
   total discrepancies.

This catches cases where **bbp** disagrees with our engine, including
divergences that Path A might mask (e.g. our engine could be
self-consistent but still wrong on certain bracket shapes).

---

## 5. Test harness layout

| File | Purpose |
|------|---------|
| `tests/test_cross_validation.py` | The **A.7 harness**. Implements Path A (`TestPathA_BbpRTG_OurFPC`) and Path B (`TestPathB_OurRTG_BbpFPC`). Smoke tests run 10 tournaments; the `@pytest.mark.slow` tests run the full 5000. |
| `tests/test_dutch_fide_official.py` | Curated, hand-checked TRFs from the bbpPairings test suite (e.g. `bbp_dutch_C5.trf`, `bbp_dutch_C9.trf`) plus several RTG-generated reference tournaments. Must match 100 % on rule-specific fixtures. |
| `tests/test_rtg_fpc_validation.py` | **Self-consistency**: our RTG → our FPC. Confirms the engine is internally deterministic (10 000 tournaments, 70 000 rounds, 0 discrepancies). |
| `tests/test_dutch_trf_fixtures.py` | Replays JaVaFo and bbpPairings golden fixtures round-by-round. |
| `tests/fixtures/fide_official/` | Frozen bbpPairings-generated TRFs used by the two test files above. |

The shared helpers `_bbp_generate(...)` and `_bbp_check(...)` in
`test_cross_validation.py` do the actual `subprocess` calls into
`bbpPairings.exe` and parse its output.

### Smoke vs full A.7 run

```bash
# Fast: 10 tournaments per shape, no slow marker
pytest tests/test_cross_validation.py -v

# Full A.7 validation: 5000 tournaments per shape
pytest tests/test_cross_validation.py -v -m slow
```

The slow tests are the ones that actually enforce the
"≤ 10 discrepancies" budget:

```python
@pytest.mark.slow
def test_5000_tournaments_10p5r(self):
    result = self._run_batch(5000, num_players=10, num_rounds=5)
    assert result["total_discrepancies"] <= 10, detail
```

Two tournament shapes are exercised:

- **10 players × 5 rounds** (small, includes odd-player/bye edge cases when seeds vary).
- **20 players × 9 rounds** (closer to a real Swiss event).

Each shape is run on both Path A and Path B.

---

## 6. Profiling and triaging divergences

When the harness reports `N discrepancies > 0`, the next step is to
find out *which* tournament, *which* round, *which* bracket, and *why*.
That's what the `scripts/` directory is for:

| Script | Role |
|--------|------|
| `scripts/profile_divergences.py` | Runs the FPC on a batch of bbp-generated tournaments and, for each mismatched round, dumps the score group, colour preferences, float history, and ratings of the divergent players. Used to categorise patterns (e.g. "all divergences are in odd-sized score groups with downfloats"). |
| `scripts/triage_divergences.py` | Three-way triage (us / `bbpPairings` / JaVaFo). Labels every non-matching round as `OUR_BUG`, `BBP_QUIRK`, `JAVAFO_QUIRK`, or `THREE_WAY` per the §A.7 bullets, so only genuine candidate errors are chased. See §8 below. |
| `scripts/compare_edge_weights.py` | Compares the integer edge weights produced by our `_compute_bracket_edge_weight` with the C++ `computeEdgeWeight` for the same player pair; used to localise bit-layout disagreements. |
| `scripts/_javafo.py` | Thin Python wrapper around the JaVaFo jar, so the triage tools can shell out without knowing the exact `java -ea -jar …` invocation. |

A typical investigation loop:

1. Run `profile_divergences.py 9 5 100 0` — profile 100 tournaments of
   9 players × 5 rounds starting at seed 0.
2. Run `triage_divergences.py 9 5 200 0` on the same range to classify
   each mismatch into the §A.7 buckets.
3. For each `OUR_BUG` row, set `CAISSIFY_TRACE_BRACKET=1` and re-run
   `caissify-pairings-check` on the single tournament to dump the
   engine's bracket / MWM decisions.
4. Diff against the endorsed oracle's output — the disagreement usually
   maps onto one specific FIDE criterion (C.6, C.9, C.12, C.18 …).
5. Fix the encoding/logic in `dutch.py`, re-run the smoke harness, then
   the full 5000-tournament A.7 harness.

---

## 7. How a discrepancy is *counted*

To avoid ambiguity, both directions use the same definition:

- A round is **mismatched** iff the set of unordered pairs
  `{(min(a,b), max(a,b))}` produced by our engine differs from the set
  produced by bbp on the same round.
- Each pair that appears in one set but not the other contributes
  **one discrepancy** to the total.
- Color-only disagreements on otherwise-identical pairs are tracked
  separately (`num_color_only_diffs` in the profiler) but are *not*
  counted towards the A.7 budget by the harness — A.7 measures pair
  identity, not color allocation. Color correctness is enforced by the
  FPC fixtures and by the FIDE official rule tests instead.

This matches how `bbpPairings.exe -c` reports differences: it prints one
`<a> - <b>` line per missing/extra pair per round.

---

## 8. Three-way triage (bbpPairings vs JaVaFo vs ours)

Once both oracles are available, every failing round can be labelled
with exactly one of five verdicts. This matches FIDE's own §A.7
classification (bullets 1, 2 and 3).

| Verdict | Meaning | FIDE §A.7 bucket | Action |
|---------|---------|------------------|--------|
| `MATCH` | all three agree | n/a | — |
| `OUR_BUG` | bbp *and* JaVaFo agree, we differ | *"an error coming from the candidate"* | fix `dutch.py` |
| `BBP_QUIRK` | we and JaVaFo agree, bbp differs | *"an error in the input file… responsibility of the endorsed program which provided the RTG"* | not our bug — we are rule-correct |
| `JAVAFO_QUIRK` | we and bbp agree, JaVaFo differs | *"interpretation divergence"* (usually 2016- vs 2025-rules drift) | not our bug |
| `THREE_WAY` | all three disagree | *"interpretation divergence caused by unclear rules"* | referred to SPPC |

The triage tool is `scripts/triage_divergences.py`. Example invocation:

```bash
python scripts/triage_divergences.py 9 5 200 0   # 9p × 5r × 200 tournaments
```

### 8.1 Current snapshot

Most recent triage runs against `bbpPairings` 6.0.0 + JaVaFo 2.2:

| Shape | Sample | `OUR_BUG` | `JAVAFO_QUIRK` |
|---|---:|---:|---:|
| 9p × 5r  | 1000 seeds | 0 | — |
| 11p × 5r | 500 seeds  | 0 | — |
| 13p × 5r | 500 seeds  | 0 | — |
| 20p × 9r | 500 seeds  | 0 | 346 |

Headlines:

- `OUR_BUG` is at zero across every sampled shape — there are no rounds
  where `caissify-pairings` disagrees with *both* endorsed oracles.
- On 20p/9r, JaVaFo 2.2 (2016 rules) disagrees with both us and
  `bbpPairings` 6.0.0 (2025 rules) on 346 rounds. Under the §A.7
  three-bucket classification these count as "interpretation divergence
  caused by unclear rules", which is not a candidate error.

### 8.2 Interpreting the numbers vs the A.7 bar

FIDE A.7 measures discrepancies against **one** endorsed oracle. So for
submission we pick bbp as the primary oracle (it tracks the current
2025 rules) and our effective divergence count per round is
`OUR_BUG + BBP_QUIRK + THREE_WAY` (everything where we differ from
bbp). `JAVAFO_QUIRK` does not count against the A.7 budget when bbp is
the oracle; it would only count if JaVaFo were the oracle.

Equivalently, if we picked JaVaFo as the A.7 oracle, our count would
be `OUR_BUG + JAVAFO_QUIRK + THREE_WAY`.

Practically, the triage tool is used to:
1. **Prioritize fixes** — only `OUR_BUG` rows get `dutch.py` changes.
2. **Justify remaining diffs at submission time** — `BBP_QUIRK` and
   `THREE_WAY` rows are documented as §A.7 bucket-1/bucket-3
   discrepancies in the FE-1 paperwork rather than treated as bugs.

---

## 9. Current status (snapshot)

> For the authoritative per-engine status table see
> [`FIDE_CONFORMANCE.md`](FIDE_CONFORMANCE.md); the endorsement-process
> side is in [`ENDORSEMENT.md`](ENDORSEMENT.md). The headline numbers
> below are the ones that gate the Dutch engine's A.7 conformance.

- **Self-consistency (our RTG → our FPC):** 10 000 tournaments,
  70 000 rounds, **0 discrepancies**.
- **Cross-validation Path A (bbp RTG → our FPC), 20p/9r:** 5000
  tournaments, 45 000 rounds, **0 discrepancies**. FIDE target ≤ 10.
- **Cross-validation Path A (bbp RTG → our FPC), 10p/5r:** 5000
  tournaments, 25 000 rounds, **0 discrepancies**. FIDE target ≤ 10.
- **Cross-validation Path B (our RTG → bbp FPC), same shapes:**
  likewise **0 discrepancies** on both.
- **FIDE official rule fixtures (C.5 / C.9):** passing.

The bar set by FIDE A.7 — **≤ 10 discrepancies per 5000 tournaments per
path** — is the success criterion these tests gate the engine on.
