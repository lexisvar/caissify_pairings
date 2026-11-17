# FIDE Endorsement — C.02.03 §7 and C.04.A Checklist

> A concise status of `caissify-pairings` against the FIDE rules for
> pairing software. This is a checklist, not a marketing document.
> Where something is not yet done, it says so.
>
> **`caissify-pairings` is not FIDE-accepted or FIDE-endorsed.** No
> application has been made.

Since **1 March 2026** the former C.04.A (*Endorsement of a software
program*) is part of **C.02.03 §7, "Tournament Handler Programs"**
(Chess Equipment with Electronic Components, approved by the Council
on 11/12/2025), with the common process in C.02.01 (General Rules) and
the public register in C.02.04. Section 1 below maps §7 for both FIDE
systems the library implements. The C.04.A tables after it are kept as
the historical record of the work done under the old text.

---

## 1. C.02.03 §7 — Tournament Handler Programs (from 1 March 2026)

### 1.1 What §7 asks for, and the process around it

* A **THP** is "a program running on a commercially available operating
  system and capable to manage Swiss tournaments by handling player
  data, pairings, and results". `caissify-pairings` is a **pairing
  engine library** with a checker and a generator, not a THP: like the
  TEC's own reference engine (Gacrux, "an engine, not a Tournament
  Handler Program"), it can only reach acceptance as the engine of a
  THP that embeds it (C.02.01 §7.5.1 makes the same point for
  administration systems).
* Acceptance is the **Technical Acceptance of Product Compliance
  (TAPC)**: vendor registration → Acceptance Cycle → self-assessment
  against the **Verification Check List (VCL)** → **SDPC** (self
  declaration) → TAPC (TEC testing; Council approval). **Endorsement**
  is a separate, later commercial agreement with FIDE (C.02.01
  §7.7–7.8). The TEC 2026 Congress report says the VCLs are not final
  and new TAPC assessments start under the incoming Commission.
* §7.3: rules change on a New Rules Date at least four years apart; a
  TAPC is valid for the Acceptance Cycle.

### 1.2 Requirement map — Dutch (C.04.3) and Team (C.04.6)

| §7 | Requirement | Dutch system | Team system |
|---|---|---|---|
| 7.1.1 a | Import FIDE rating lists | ❌ Not in the library (a THP's job) | ❌ Same |
| 7.1.1 b | Pair with at least one FIDE Swiss system and **all** FIDE acceleration methods | ✅ C.04.3 (`system="dutch"`); ✅ Baku, the only C.04.7 method (`accelerated=True`) | ✅ C.04.6 (`system="team_swiss"`, [`TEAM_SWISS.md`](TEAM_SWISS.md)); ✅ Baku with MP primary (C.04.7 forbids it with GP primary) |
| 7.1.1 c | Final standings with **every MTB26 tie-break** | ❌ **No tie-breaks are computed** | ❌ **No tie-breaks are computed** |
| 7.1.2 | Compliance declared per pairing system | Engine, checker and generator are separate per system | Same |
| 7.1.3 a | English interface; full user manual or online help | ✅ English CLIs and docs; ⚠️ README + docs, no user manual for a THP | Same |
| 7.1.3 b | Import/export the **latest** FIDE Data Exchange Format (TRF26) and older ones | ⚠️ TRF16 read/write (plus the `XXR`/`XXA`/`XXC`/`XXS` extensions); ❌ **no TRF26 export** for individual events (records 142, 152, 162, 192, 202/212…); ❌ no TRF06 | ✅ TRF26 read/write of the team records (013 read, 142, 152, 162, 192, 240, 250, 260, 300, 310, 320, 330, 352, 362); ⚠️ records 299 (abnormal points) and 801/802 (informative) are not read, 202/212 not written |
| 7.1.3 c | Free **Pairings and Tie-break Checker** (PTC) and RTG | ⚠️ Pairing half: `caissify-pairings-check`; ❌ tie-break half missing | ⚠️ Pairing half: `caissify-pairings-check --team`; ❌ tie-break half missing |
| 7.1.3 d | Controlled test environment | ✅ `pip install` in any virtualenv; pure Python | Same |
| 7.1.4 | Extra (non-FIDE) features must not compromise FIDE pairings | ✅ `casual`/`round_robin`/`arena` are separate engines | ✅ Interpretation switches are off-default and documented |
| 7.2.1–7.2.2 | TRF as the exchange format (TRF26, TRF16, TRF06) | See 7.1.3 b | See 7.1.3 b |
| 7.2.3 | PTC: CLI, free, uses the built-in pairing **and tie-break** engine | ⚠️ Pairings only | ⚠️ Pairings only |
| 7.2.4 | RTG: free CLI, customisable parameters | ✅ `caissify-pairings-rtg` (players, rounds, ratings, draw rate, Baku) | ✅ `caissify-pairings-rtg --team` (teams, rounds, boards, reserves, forfeits, absences, withdrawals, colour type, MP/GP, secondary score, Baku) |
| 7.3 | Acceptance Cycle, TAPC validity, transition | Not applied for | Not applied for |
| MTB26 | Every tie-break in the Mandatory Tie-Breaks list (record 202/212 codes) | ❌ None of DE, BPG, BWG, REP, STD, SB, ARO, TPR, PTP, APRO, APPO, RTNG, WIN, WON, PS, TPN, BH, AOB, FB, KS (with C1/C2/M1/M2/P/F/R/Lx) | ❌ None of WIN, WON, PS, TPN, BH, AOB, FB, KS (`:MP`/`:GP`) nor the team-only BC, TBR, BBE, MPvGP, EMMSB, EMGSB, EGMSB, EGGSB, EDE(+BT/BB/T/B), SSSC |

### 1.3 Evidence of pairing conformance

| System | Evidence | Outside reference |
|---|---|---|
| Dutch | A.7-style cross-validation: 0 discrepancies on 5000 × 20p/9r and 5000 × 10p/5r, both directions, against bbpPairings ([`FIDE_CONFORMANCE.md`](FIDE_CONFORMANCE.md)) | bbpPairings (endorsed), JaVaFo |
| Team | Agreement with an independent brute-force implementation of the C.04.6 text on every comparable round of thousands of random tournaments (3–16 teams); the engine's own RTG tournaments always pass its checker ([`TEAM_SWISS.md`](TEAM_SWISS.md) §4) | **None yet.** Cross-validation against an outside reference (for example the TEC's Gacrux) is a separate step that needs the owner's approval |

### 1.4 TODO before any SDPC

1. **Tie-break engine for every MTB26 code**, individual and team
   (`:MP`/`:GP`, cut/median, `/P`, `/F`, `/R`, `Lx`, `Kx`), with records
   202/212 read and written. Today the library computes **no**
   tie-breaks.
2. **Tie-break checker**: extend both checkers into a PTC that also
   recomputes the standings of a TRF26 file and reports differences.
3. **TRF26 for individual tournaments**: write records 142, 152, 162,
   192, 202/212, 222, 240 (and read them); keep TRF16; add TRF06 import.
4. **Team TRF26 gaps**: record 299 (abnormal points) and 801/802.
5. **Team cross-validation** against an outside reference (Gacrux or an
   endorsed THP), once the owner approves.
6. **Dutch + Baku** A.7-scale cross-check (see §A.7 below).
7. A THP to embed the engine (rating-list import, standings, user
   manual), then the VCL self-assessment when the TEC publishes it.

---

# Historical: C.04.A checklist (in force until 28 February 2026)

---

## A.2 — Program requirements

| Clause | Requirement | Status |
|---|---|---|
| A.2.1 | Implement the FIDE (Dutch) System (C.04.3) | ✅ — `src/caissify_pairings/engines/dutch.py`; A.7 conformance 0 discrepancies on both 5000-tournament benchmarks |
| A.2.2 | FIDE mode offering all required pairing functionality | ✅ — `generate_pairings(system="dutch", …)` is the FIDE path; casual/round-robin are orthogonal engines |
| A.2.3 | English-language interface | ✅ — all CLIs, docs, and error messages are English |
| A.2.4 | Read TRF16 input | ✅ — `src/caissify_pairings/trf.py` (parser) + `src/caissify_pairings/fpc.py` |
| A.2.5 | Write TRF16 output | ✅ — `src/caissify_pairings/trf.py` (writer); used by the RTG |
| A.2.6 | Publish a Free Pairings Checker (FPC) | ✅ — `caissify-pairings-check <file.trf>` CLI, Apache-2.0-licensed, pip-installable |
| A.2.7 | FIDE mode must not cause pairing mishaps | ✅ — full suite passes; self-consistency: 0 discrepancies on 70 000 rounds. Since 0.7.1 a round with no legal pairing keeps at most one bye and flags every rule it breaks (`violations`: C1/C2/C3) instead of handing out extra byes (found against JaVaFo's "pairing impossible") |
| A.2.8 | Additional (non-FIDE) services permitted | ✅ — the `casual` engine is opt-in, clearly marked non-FIDE |

**Error-correction policy (A.2 cont.):** major errors must be fixed
within two weeks, minor errors within two months. The project is on
semantic versioning with a public CHANGELOG; the test suite and A.7
benchmarks run on every release.

---

## A.3 — Data exchange formats

| Clause | Requirement | Status |
|---|---|---|
| A.3.1 | TRF16 read/write | ✅ — round-trip tested by the FPC and RTG |
| A.3.1b | TRF06 (legacy 2006) read | ❌ — not implemented; lower priority |
| A.3.2 | Generate a full TRF | ✅ — `caissify-pairings-rtg` writes a full TRF16 per tournament |

TRF tags `XXC` (colour allocation) and `XXS` (special rules) are
written conditionally, matching how `bbpPairings` reference TRFs use
them. The `XXA` tag (per-round acceleration codes used for Baku) is
not yet parsed or emitted — see the Baku row in §A.7 below.

---

## A.4 — Free Pairings Checker (FPC)

| Requirement | Status |
|---|---|
| Command-line tool | ✅ — `caissify-pairings-check FILE.trf` |
| Reads TRF16 | ✅ |
| Rebuilds tournament round-by-round | ✅ |
| Pairs each round with the embedded engine | ✅ — uses `DutchEngine` directly |
| Outputs a consistency report | ✅ — per-round match/mismatch lines on stdout |
| Freely available, no licence required | ✅ — Apache-2.0, on PyPI |

---

## A.5 — Random Tournament Generator (RTG)

| Requirement | Status |
|---|---|
| Command-line tool | ✅ — `caissify-pairings-rtg --players N --rounds R -n 5000 -o out/` |
| Produces a full TRF16 per tournament | ✅ |
| Strictly follows the pairing rules | ✅ — uses the same `DutchEngine` as production |
| Game results follow the FIDE rating probability table | ✅ — expected-score formula in `rtg.py` |
| Freely available | ✅ — Apache-2.0, on PyPI |

---

## A.6 / A.7 — Endorsement procedure

### A.6 — First endorsement (no endorsed oracle available)

| Step | Status |
|---|---|
| Submit FE-1 application to the Pairing Programs Commission | Not yet submitted |
| Commission names a 4-member subcommittee at the next Congress | n/a until submitted |
| Subcommittee reports at the following Congress | n/a until submitted |

### A.7 — Cross-validation (endorsed oracle available, preferred path for Dutch)

| Step | Status |
|---|---|
| External endorsed RTG → candidate FPC, 5000 tournaments, ≤ 10 discrepancies | ✅ — `bbpPairings` RTG → our FPC: **0 / 5000** on 20p/9r and 10p/5r |
| Candidate RTG → external endorsed FPC, 5000 tournaments, ≤ 10 discrepancies | ✅ — our RTG → `bbpPairings` FPC: **0 / 5000** on 20p/9r and 10p/5r |
| Discrepancies classified per A.7 bullets 1–3 | ✅ — 0 to classify; a three-way triage harness (us / bbp / JaVaFo) is available for future diffs |
| Application submitted ≥ 4 months before Congress | Not yet submitted |

### A.7 — Baku Acceleration (C.04.5.1) path

| Step | Status |
|---|---|
| Implement Baku virtual-points + Dutch integration | ✅ — `DutchEngine(accelerated=True)`; 14 unit tests |
| Parse / emit the TRF `XXA` per-round acceleration tag | ✅ — since 0.5.0 (`trf.py`) |
| Run 5000-tournament A.7 both directions under acceleration | ❌ — not run yet |

---

## A.9 / Annex-3 — Endorsed programs list (reference only)

Reference: [FEP19 — list of endorsed programs](https://spp.fide.com/wp-content/uploads/C04Annex3_FEP19-1-1.pdf).

`caissify-pairings` is **not** on the Annex-3 endorsed-programs list.
A.7 conformance is a technical prerequisite only; endorsement itself
is granted by the FIDE Technical and Education Commission (TEC, which
absorbed the former Pairing Programs Commission).

---

## What is still missing for an FE-1 submission

1. **The FE-1 form itself** (Annex-1): program name, author, version,
   pairing system, contact details.
2. ~~An algorithm-description annex~~ ✅ — [`doc/ALGORITHM_DESCRIPTION.md`](ALGORITHM_DESCRIPTION.md)
   maps every C.04.3 clause (A1–E6) to the implementation.
3. **`XXA` TRF support** if the submission is to cover Baku
   Acceleration (C.04.5.1). Optional — the initial submission can
   target the un-accelerated Dutch path only.

See [`FIDE_CONFORMANCE.md`](FIDE_CONFORMANCE.md) for the detailed
cross-validation results, and [`DIVERGENCE_TESTING.md`](DIVERGENCE_TESTING.md)
for the methodology used to measure them.
