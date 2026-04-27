# FIDE Endorsement — C.04.A Checklist

> A concise status of `caissify-pairings` against each clause of
> [FIDE Handbook C.04.A](https://handbook.fide.com/chapter/C04A)
> (*Endorsement of a software program*). This is a checklist, not a
> marketing document. Where something is not yet done, it says so.

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
| A.2.7 | FIDE mode must not cause pairing mishaps | ✅ — full fast suite passes (250 passed, 10 skipped); self-consistency: 0 discrepancies on 70 000 rounds |
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
| Parse / emit the TRF `XXA` per-round acceleration tag | ❌ — not yet; required for the bbp cross-check |
| Run 5000-tournament A.7 both directions under acceleration | ❌ — blocked on `XXA` support |

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
