# FIDE Software Endorsement — Requirements & Status

> **Reference:** [C.04.A Appendix: Endorsement of a software program](https://spp.fide.com/c-04-a-appendix-endorsement-of-a-software-program/)  
> **Program:** Caissify (API + Desktop)  
> **Last Updated:** April 18, 2026 (Phase 3 restructured — analyzed C.04.A requirements vs roadmap)  

---

## Endorsement Cycle

| Item | Detail |
|------|--------|
| Current cycle | 2025–2028 (YearX=2024) |
| Applications accepted | Years 1–3 of cycle (2025–2027) |
| No new endorsements in | Year 4 (2028), unless SPPC decides otherwise |
| Transition period | Jan 1 2025 → Congress 2025 |
| Rule amendments deadline | Congress of YearX+3 = Congress 2027 |
| Amended rules effective | July 1 2029 |

**Target:** Submit FE-1 application during 2026 or 2027.

---

## A.2 — Program Requirements

> _"The program must be able to manage Swiss tournaments using the FIDE (Dutch) System (see C.04.3) or any other pairing systems approved by FIDE."_

| # | Requirement | Status | Notes |
|---|-------------|--------|-------|
| A.2.1 | Implement FIDE (Dutch) System (C.04.3) | **In Progress** | `src/caissify_pairings/engines/dutch.py` — Phases 1 & 2 complete. Phase 2.5.1: full C5–C19 multi-criteria scoring, joint MDP+remainder evaluation, FPC float history tracking. 10p fixtures: 80–100%, 11p: 60–80%, 20p: 43–57% match rates vs bbpPairings. See `doc/ROADMAP.md` |
| A.2.2 | **FIDE mode** that offers all required functionalities | **In Progress** | Branched on `tournament.is_fide_rated`; casual mode preserved |
| A.2.3 | English language interface | **Done** | API is English; desktop app has English UI |
| A.2.4 | Import files in FIDE Data Exchange Format (TRF16) | **Done** | `tournament/services/trf_parser.py` + `trf_importer.py` |
| A.2.5 | Export files in FIDE Data Exchange Format (TRF16) | **Done** | `src/caissify_pairings/trf.py` — TRF16 writer (used by RTG). XXC/XXS written conditionally. API exporter in `caissify_api`. bbpPairings reference TRFs omit XXC/XXS — they are optional metadata. |
| A.2.6 | Public availability of a **(free) Pairings Checker** (FPC) | **Done** | `src/caissify_pairings/fpc.py` — CLI: `caissify-pairings --check FILE.trf`. MIT-licensed, pip-installable. Also planned for **Caissify Desktop** (Tauri binary). |
| A.2.7 | FIDE mode must not cause pairing mishaps | **In Progress** | 149+ tests (30 unit + 22 integration + 10 JavaFo + 21 TRF-fixture + 22 RTG + 26 FPC + 12 FIDE official + 4 RTG→FPC validation + 2 slow 5000-tournament). Self-consistency: 10,000 tournaments, 70,000 rounds, 0 discrepancies. Needs bbpPairings cross-validation improvement. |
| A.2.8 | Additional services allowed if not prohibited by FIDE | **OK** | Casual mode, analytics, etc. are non-conflicting |

### Error correction policy (A.2 cont.)
> _"Major errors must be fixed within two weeks. Minor errors within two months. Failure → endorsement suspended/revoked."_

- Need: CI pipeline that can run FPC test suite on every release.

---

## A.3 — Data Exchange Formats

| # | Requirement | Status | Notes |
|---|-------------|--------|-------|
| A.3.1 | **TRF16** (Tournament Report Format 2016) support | **Partial** | Export exists. Import exists. Need field-level audit against Annex-2 spec. |
| A.3.1b | **TRF06** (legacy 2006) support | **Not Started** | "Should be able to read" — lower priority |
| A.3.2 | Generate Tournament Report File (TRF) | **Partial** | `trf_exporter.py` — needs XXC, XXS lines, exact field positioning |

### TRF16 Gap Checklist

| TRF16 Line | Description | Status |
|-------------|-------------|--------|
| `012` | Tournament name | Done |
| `022` | City | Done |
| `032` | Federation | Done |
| `042` | Start date | Done |
| `052` | End date | Done |
| `062` | Number of players | Done |
| `072` | Number of rated players | Done |
| `082` | Number of teams | N/A |
| `092` | Type of tournament | Done |
| `102` | Chief arbiter | Done |
| `112` | Deputy arbiter | Done |
| `122` | Allotted times | Done |
| `132` | Dates of rounds | Needs review |
| `001` | Player data lines | Done |
| `013` | Team data | N/A |
| `XXR` | Number of rounds | Done |
| XXC | Color allocation method | **Optional** — `trf.py` writes conditionally; bbpPairings reference TRFs omit this |
| XXS | Special rules | **Optional** — `trf.py` writes conditionally; bbpPairings reference TRFs omit this |
| `XXP` | Points for bye/forfeit | Needs review |

---

## A.4 — Free Pairings Checker (FPC)

> _"An External Pairings Checker is a tool, embedded in the main program and containing the pairing engine, that can be freely used by anyone (without the user interface)."_

| Requirement | Status | Notes |
|-------------|--------|-------|
| Command-line tool | **Done** | `caissify-pairings --check FILE.trf` — reads TRF16, replays each round, compares pairings |
| Reads TRF16 files | **Done** | `src/caissify_pairings/trf.py` — full TRF16 parser |
| Reads TRF06 files (should) | Not Started | Lower priority |
| Rebuilds tournament round by round | **Done** | FPC replays from round 1, accumulating state |
| Pairs each round using embedded pairing engine | **Done** | Uses DutchEngine directly |
| Outputs consistency report | **Done** | Per-round match/mismatch report to stdout |
| Command format: `caissify -check FILE.fid` | **Done** | `caissify-pairings --check FILE.trf` |
| Freely available (no license required) | **Planned** | MIT-licensed; also planned for Desktop app (Tauri binary) |

### Architecture Decision

The FPC is implemented **in this package** (`caissify-pairings`) as a CLI tool:
```
caissify-pairings --check FILE.trf
```

It will also be bundled into the **Caissify Desktop** project (`caissify_tm`) as a native binary via Tauri/PyInstaller. Both satisfy the FIDE requirement for a freely available, downloadable pairings checker.

**Files:** `src/caissify_pairings/fpc.py`, `src/caissify_pairings/trf.py`

---

## A.5 — Random Tournament Generator (RTG)

> _"The RTG is a freely available tool that, preferably run from a command prompt, can easily generate many simulated tournaments producing a full TRF16 file for each of them."_

| Requirement | Status | Notes |
|-------------|--------|-------|
| Command-line tool (preferably) | **Done** | `caissify-pairings-rtg --players 20 --rounds 9 -n 5000 -o output_dir/` |
| Generates simulated tournaments | **Done** | `src/caissify_pairings/rtg.py` — uses DutchEngine + FIDE rating probability model |
| Produces full TRF16 output | **Done** | Via `src/caissify_pairings/trf.py` |
| Pairing rules strictly followed | **Done** | Uses DutchEngine — same engine as production |
| Game results respect FIDE rating probability table | **Done** | Expected score formula implemented |
| Freely available | **Done** | MIT-licensed, pip-installable |

### RTG Notes

- For endorsement when other programs are already endorsed (A.7): **5000 random tournaments** are generated and fed through the candidate FPC. At most **10 discrepancies** are allowed.
- CLI: `caissify-pairings-rtg -n 5000 -p 20 -r 9 -o ./rtg_output/`
- 22 RTG tests passing (`tests/test_rtg.py`)
- **FIDE A.7 validation complete:**
  - 5000 × 20p/9r → **0 discrepancies** (45,000 rounds checked)
  - 5000 × 10p/5r → **0 discrepancies** (25,000 rounds checked)
  - Tests: `tests/test_rtg_fpc_validation.py` (4 tests: 2 smoke + 2 full 5000-tournament)
- Fixed RTG float history tracking: `_update_player()` now computes float direction from pre-round scores
- **Files:** `src/caissify_pairings/rtg.py`, `src/caissify_pairings/trf.py`, `tests/test_rtg_fpc_validation.py`

---

## A.6 / A.7 — Endorsement Procedure

### First endorsement (A.6)
1. Submit **FE-1 form** (Annex-1) to SPPC
2. SPPC names a **subcommittee of 4** at the next Congress
3. Subcommittee reports to the following Congress whether the program is suitable

### When other programs already endorsed for the same system (A.7)
1. Application must reach SPP secretariat **≥ 4 months before Congress**
2. An external RTG generates **5000 random tournaments**
3. Tournaments fed through candidate FPC
4. Each discrepancy collected (max 10 allowed)
5. Discrepancies classified as: RTG error / candidate error / rule interpretation divergence
6. Candidate errors must be corrected before Congress

### If candidate has its own RTG
- Candidate RTG generates 5000 tournaments
- Fed through one or more existing FPCs
- Same discrepancy analysis applies

---

## A.9 / Annex-3 — Currently Endorsed Programs

Reference: [Endorsed Programs List (FEP19)](http://spp.fide.com/wp-content/uploads/C04Annex3_FEP19-1-1.pdf)

Known endorsed programs (for context):
- Swiss-Manager
- Vega
- JavaPairing (JavaFo)
- WinTD
- Swiss-System (Sevilla)
- Chess-Results
- Others (~10 total worldwide)

---

## A.10 — Section Annexes

| Annex | Description | Relevance |
|-------|-------------|-----------|
| Annex-1 | FE-1 Application Form | Need to fill and submit |
| Annex-2 | TRF06 + TRF16 format specs | Reference for import/export |
| Annex-3 | List of FIDE Endorsed Programs (FEP19) | Competitive reference |
| Annex-4 | Verification Check-List (VCL19) | **Critical** — the checklist SPPC uses to evaluate |

---

## Overall Endorsement Readiness

| Component | Weight | Status | Progress |
|-----------|--------|--------|----------|
| Dutch System engine (C.04.3) | Critical | In Progress | ~88% *(C5–C19 criteria implemented; small fixtures 80–100%; medium/large need global matching)* |
| FIDE mode (`is_fide_rated`) | Critical | Done | 100% |
| English interface | Required | Done | 100% |
| TRF16 import/parse | Required | Done | 100% |
| TRF16 export/build | Required | Done | 100% *(XXC/XXS optional; bbpPairings omits them)* |
| FPC (command-line checker) | Required | Done | 100% *(float history tracking fixed; 10,000 tournaments self-consistent)* |
| RTG (tournament generator) | Required | Done | 100% *(5000-tournament validation: 0 discrepancies)* |
| TRF fixture validation | Recommended | Done | 100% *(15 fixtures, 21 tests)* |
| Self-consistency (our RTG → our FPC) | Required | **Done** | **100%** *(10,000 tournaments, 70,000 rounds, 0 discrepancies)* |
| **A.7 cross-validation (bbpRTG → our FPC)** | **Critical** | **Not Started** | **0%** *(estimated many discrepancies — match rate 43–57% at 20p)* |
| **A.7 cross-validation (our RTG → bbpFPC)** | **Critical** | **Not Started** | **0%** *(must pass ≤10 discrepancies threshold)* |
| FE-1 application submission | Required | Not Started | 0% |

### Critical Gap: A.7 Cross-Validation

The endorsement procedure (A.7) requires **both directions** of cross-validation:
1. **External RTG → our FPC:** An endorsed program's RTG generates 5000 tournaments; our FPC checks them.
2. **Our RTG → external FPC:** Our RTG generates 5000 tournaments; an endorsed FPC checks them.

Both must produce **≤10 discrepancies** across all tournaments. Our self-consistency is perfect (0 discrepancies), but current match rates vs bbpPairings on medium/large tournaments (43–57% at 20p, 11% at 40p) indicate many discrepancies would be found. The root cause is our bracket-by-bracket greedy approach vs bbpPairings' global maximum-weight matching.

**This is the primary remaining blocker for endorsement.**

### Items NOT Required for Endorsement (moved to deferred)

After careful analysis of C.04.A, the following were removed from the endorsement roadmap:
- **Arbiter override API** — C.04.A does not require manual pairing adjustments for engine endorsement
- **XXC/XXS TRF lines** — Optional metadata; bbpPairings reference TRFs omit them
- **TRF field positioning audit** — Already validated by RTG→FPC self-consistency; only matters for interop

**Estimated overall endorsement readiness: ~70%** *(down from 75% — cross-validation gap now properly accounted for)*

---

## Action Items (Priority Order)

1. **Improve Dutch engine match rate on medium/large fixtures** — remaining divergence from bbpPairings stems from greedy bracket-by-bracket approach vs bbpPairings' global maximum weight matching. Options: implement Blossom V / Hungarian algorithm, or refine heuristic with look-ahead
2. **Run 5000-tournament RTG→FPC validation** — `caissify-pairings-rtg -n 5000` then `caissify-pairings --check` each output (this repo)
3. **Audit TRF16 export in API** — add XXC/XXS lines, field positioning (`caissify_api` repo)
4. **Download & run FIDE official FPC test suites** — validate against endorsed programs' output
5. **Build FPC into Caissify Desktop** — native binary for distribution (separate project)
6. **Prepare FE-1 application** — fill form, compile documentation
7. **Submit to SPPC** — ≥4 months before target Congress
