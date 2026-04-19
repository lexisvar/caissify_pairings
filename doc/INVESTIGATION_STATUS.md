# Investigation Status — Odd-Player Divergences

> **Date:** April 19, 2026 (Phase 3.5)  
> **Engine:** `src/caissify_pairings/engines/dutch.py` (~3,200 lines)  
> **Reference:** `vendor/bbpPairings/` (bbpPairings v6.0.0, FIDE-endorsed C++)  
> **Goal:** Achieve ≤10 discrepancies across 5000 random tournaments (FIDE A.7)

---

## 1. Current State — Summary

### What works perfectly
- **All even-player tournaments: 0 divergences** (10p, 12p, 14p, 20p — tested 50 tournaments each)
- **All 137 non-slow tests pass** (~88s)
- **12/12 FIDE official fixture tests pass**
- Self-consistency: 0 discrepancies across 10,000+ tournaments

### What still diverges
- **Odd-player tournaments** have 20–24 pair diffs per 50 tournaments:

| Players | Diffs/50t | Divergent rounds | Pattern |
|---------|-----------|------------------|---------|
| 9p/5r   | 22        | R3–R5            | Score groups size 1–5 |
| 11p/5r  | 24        | R2–R5            | Score groups size 1–6 |
| 13p/5r  | 20        | R2–R5            | Score groups size 1–5 |
| 15p/5r  | 11        | R2–R5            | Fewer diffs at larger sizes |
| 21p/5r  | 13        | R3–R5            | Fewer diffs at larger sizes |

### FIDE fixture match rates

| Fixture | Rate | Notes |
|---------|------|-------|
| bbp_10p5r_s42/s43/s44 | 100% each | Perfect |
| bbp_11p5r_s42 | 80% (4/5) | R5 diverges — bye diff |
| bbp_11p5r_s43 | 60% (3/5) | R3+R5 diverge |
| bbp_20p7r_s42/s43 | 100% each | Perfect |
| bbp_40p9r_s42 | 100% (9/9) | Perfect |

---

## 2. Fixes Applied This Session (Phase 3.4)

### Fix 1: Self-pair bug (Phase 2 + Phase 9)
- **Cause:** Phase 2 MDP finalization called `_finalize_pair(gi, gi)` when MWM couldn't match an MDP
- **Fix:** Added `if match_gi != gi:` guard in Phase 2 (~line 1941) and Phase 9 (~line 2244)

### Fix 2: Edge weight rewrite (`_compute_bracket_edge_weight`)
- **Removed** C6_PSD, C10, C11 extra tiers that don't exist in bbpPairings
- **Fixed** `c_strong`: changed from penalizing when ONE player has str≥2 to requiring BOTH str≥2
- **Fixed** `c_absP`: added proper colorImbalance/repeatedColor sub-conditions matching C++ `insertColorBits` bit 2
- **Added** `_repeated_color()` helper
- **Gated** C14/C15 by `rounds_played >= 2`, C16/C17 by `rounds_played >= 1`, C18/C19 by `rounds_played >= 2`

### Fix 3: MDP isolation removal (BIGGEST IMPACT)
- **Cause:** Phase 2 zeroed other MDPs' edges during opponent selection (save/restore pattern)
- **bbpPairings approach:** ALL MDPs' edges stay active — global MWM jointly optimizes
- **Impact:** 10p divergences: 20 → **0** pair diffs

### Fix 4: Bye-in-MWM for odd tournaments
- **Added** `bye_candidates` parameter to `_compute_bracket_edge_weight` and `_pair_iterative_mwm`
- **Added** completion bits: `3` (both non-bye), `2` (one non-bye), `1` (both bye candidates)
- **Added** preliminary MWM (lightweight weights) to determine `byeAssigneeScore`
- **Modified** `generate_pairings()`: R1 keeps pre-selection, R2+ uses bye-in-MWM
- **Impact:** 11p divergences: 79 → **24** pair diffs

### Fix 5 (Phase 3.5): Preliminary MWM `top_score` bit
- **Cause:** `top_bracket_bit` in preliminary weight was using `pi.score >= top_score`
  (higher-ranked player), but bbpPairings sets it from `pj.score >= top_score`
  (lower-ranked player). See `_pair_iterative_mwm()` ~line 1700.
- **Fix:** Changed to `pj.score >= top_score` to match
  bbpPairings' preliminary weight logic.
- **Impact:** Algorithmically correct; **no measurable change** in divergence
  counts — the bit is a low-priority tiebreaker that rarely flips an MWM result.

### Fix 6 (Phase 3.5): C9 `unplayedGameRanks` actually populated
- **Cause:** C9 reserved `2*B` bits but never set them — bit space wasted.
- **Fix:**
  1. Extended `_compute_bracket_edge_weight()` signature with three new
     params: `bye_assignee_score`, `is_single_downfloater_bye_assignee`,
     `unplayed_game_ranks`.
  2. C9 block (line ~1530) now mirrors bbpPairings' logic: among players
     at `byeAssigneeScore`, fewer played games → higher rank → harder to
     leave unmatched.
  3. After preliminary MWM, `_pair_iterative_mwm()` (~line 1750) computes
     `bye_assignee_score`, evaluates `is_single_downfloater_bye_assignee`
     (single MDP whose score equals byeAssigneeScore), and builds dense
     `unplayed_game_ranks: Dict[played_games, rank]`.
  4. All `_compute_bracket_edge_weight` call sites updated to pass these.
- **Tests:** All 137 non-slow tests still pass.
- **Impact:** Algorithmically correct; **no measurable change** in
  divergence counts. The activation predicate
  (`isSingleDownfloaterTheByeAssignee`) is rarely true on the
  divergent fixtures, and when true, higher-priority tiers usually
  dominate the MWM choice.

### Phase 3.5 measured divergence counts (post-fix)

| Players/Rounds | Mismatched rounds | Pair diffs / 50 tournaments |
|----------------|-------------------|------------------------------|
| 9p / 5r        | 12                | 22                           |
| 10p / 5r       | 0                 | 0                            |
| 11p / 5r       | 11                | 24                           |
| 12p / 7r       | 0                 | 0                            |

Even-player tournaments remain perfect; odd-player counts are unchanged
from the pre-Phase-3.5 baseline (so Fixes 5 and 6 are correctness
improvements with no observable behavioral effect on the measured
divergent cases).

### Verified non-issue: "Quick Win Candidate 1" (conditional bit allocation)

The doc previously listed *"always allocate C12-C19 bit space"* as a
likely structural mismatch. Re-reading
`vendor/bbpPairings/src/swisssystems/dutch.cpp`
lines 338, 361, 385, 423 confirms that **bbpPairings ALSO conditionally
allocates** with the same predicates (`tournament.playedRounds` for
C12/C13/C16/C17 and `tournament.playedRounds > 1u` for C14/C15/C18/C19).
Python and C++ already agree here. This is **not** a bug.

---

## 3. Root Cause Analysis — Remaining Odd-Player Divergences

### 3.1 Traced example: 9p/5r seed 47, Round 5

**Our pairing:** P6-P1, P9-P7, P2-P4, P8-P5, BYE=P3  
**bbpPairings:** P4-P1, BYE=P2, P3-P6, P8-P5, P9-P7

Key observations:
- **Bye differs:** We give bye to P3, bbp gives bye to P2 (both score=2.0, both bye-eligible)
- **Different bye ⇒ different pairings** — cascading effect
- Preliminary MWM's lightweight weights selected P3 as the unmatched player, but bbpPairings selected P2

### 3.2 Hypothesis: Preliminary MWM weight differences

bbpPairings' preliminary matching uses (MSB→LSB):
```
completion(2b) | scoreGroupShifts(sum of both players) | top_bracket_bit(1b)
```

Our implementation matches this structure. BUT the `_can_pair()` check might differ in edge cases, which would produce different MWM inputs and different bye selection.

### 3.3 Hypothesis: C9 unplayedGameRanks not implemented

bbpPairings has a C9 criterion (TIER 5) that uses `isSingleDownfloaterTheByeAssignee` and `unplayedGameRanks` to prefer leaving unpaired the player with fewest unplayed games. Our implementation allocates the bit space (`s += 2 * B`) but never sets bits — the comment says:
```python
# === Bye criterion (2*B bits) — simplified: all players equally ===
s += 2 * B
```

This criterion only fires when the bye assignee is from the top score group and is the sole downfloater. It's RARE but could explain some divergences.

### 3.4 Hypothesis: Exchange mechanism differences

The exchange weight mechanism (`_exchange_weight` at line 1818) uses the reserved bits (3*B + 1) to encode:
- Homogeneous/heterogeneous bracket ordering
- Exchange priority within the bracket

This has NOT been compared line-by-line against bbpPairings' `edgeWeightComputer` class. It could contain subtle ordering differences.

### 3.5 Divergence pattern: most occur in later rounds (R4-R5)

This suggests the differences compound — early rounds pair correctly, but accumulated differences in bye selection, float history, or color history cause later rounds to diverge. A single wrong bye in R3 cascades through R4 and R5.

---

## 4. Key Code Locations

### Python engine (`src/caissify_pairings/engines/dutch.py`)

| Function | Line | Purpose |
|----------|------|---------|
| `_can_pair()` | 287 | Absolute criteria (B1 repeat, B5/B6 color) |
| `_select_bye_player()` | 328 | Pre-selection bye (R1 only now) |
| `_assign_colors()` | 375 | Color allocation (E1-E6) |
| `_compute_bracket_edge_weight()` | 1319 | **CRITICAL** — edge weight encoding |
| `_repeated_color()` | 1378 | Helper for c_absP criterion |
| `_pair_iterative_mwm()` | 1565 | 9-phase bracket MWM algorithm |
| `_finalize_pair()` | 1627 | Lock pairing by zeroing alternatives |
| Preliminary MWM | 1672 | Lightweight bye assignee determination |
| `_exchange_weight()` | 1818 | Reserved bits for exchange ordering |
| Phase 1: MDP Selection | 1841 | Boost MDP weights |
| Phase 2: MDP Opponent | 1927 | Joint MDP optimization |
| Phase 3: Remainder | 1972 | Collect upper/lower groups |
| Phase 4-7: Exchange | 2003 | Exchange mechanism |
| Phase 8: Upper-group | 2204 | Final pair assignments |
| Phase 9: Advance | 2244 | Move to next bracket |
| `generate_pairings()` | 2970 | Entry point — bye handling, MWM call |

### bbpPairings C++ reference (`vendor/bbpPairings/src/swisssystems/dutch.cpp`)

| Function | Line | Purpose |
|----------|------|---------|
| `compatible()` | 34 | Compatibility check (B1 + B5/B6 + topscorer relaxation) |
| `isByeCandidate()` | 213 | Bye eligibility + score threshold |
| `computeEdgeWeight()` | 232 | **CRITICAL** — template<bool max> edge weight |
| `computeMatching()` | 700 | Main entry: preliminary MWM → bracket loop |
| Preliminary MWM (odd) | 766 | Lightweight weights for bye determination |
| byeAssigneeScore extraction | 824 | Find unmatched player's score |
| isSingleDownfloater | 849 | C9 activation flag |
| unplayedGameRanks | 878 | C9 rank computation |
| Full weight recompute | 893 | Re-set all edges with bye info |
| Bracket loop | 930 | Main bracket-by-bracket processing |
| `edgeWeightComputer` | ~1000+ | Exchange weight adjustments |

### Edge weight bit layout comparison

**bbpPairings (MSB→LSB):**
```
completion(2b) | TIER1_pairs(B) | TIER2_scores(SB) | TIER3_pairs(B) | TIER4_scores(SB)
| C9_bye(2×B) | insertColorBits(4×B) | C12(B) | C13(B) | C14(B) | C15(B)
| C16(SB) | C17(SB) | C18(SB) | C19(SB) | reserved(3×B+1)
```

**Our Python (MSB→LSB):**
```
completion(2b) | TIER1_pairs(B) | TIER2_scores(SB) | TIER3_pairs(B) | TIER4_scores(SB)
| bye(2×B) [ZEROED] | color(4×B) | C12(B) | C13(B) | [C14(B) | C15(B) if R≥2]
| [C16(SB) | C17(SB) if R≥1] | [C18(SB) | C19(SB) if R≥2] | reserved(3×B+1)
```

**Differences to investigate:**
1. C9 bye criterion always zeroed in Python (bit space allocated but unused)
2. C12-C19 conditional allocation by rounds_played — does bbpPairings do the same?
3. Color bit encoding: verify `insertColorBits` sub-conditions match exactly

---

## 5. Profiling & Debugging Tools

### Profile divergences
```bash
# Quick: 10 tournaments
python scripts/profile_divergences.py 11 5 10 42

# Standard: 50 tournaments
python scripts/profile_divergences.py 11 5 50 42

# For even players (should be 0)
python scripts/profile_divergences.py 10 5 50 42

# Summary only
python scripts/profile_divergences.py 11 5 50 42 2>&1 | grep -E "^SUMMARY|pair diffs|Color-only"
```

### Debug specific divergence
```bash
# Trace edge weights for a specific case
python scripts/debug_divergence.py 9 5 47 5
# Args: num_players num_rounds seed target_round
```

### Run tests
```bash
# All non-slow tests
python -m pytest tests/ -x -q --tb=short -m "not slow"

# FIDE official fixtures only
python -m pytest tests/test_dutch_fide_official.py -v

# Core engine + integration
python -m pytest tests/test_dutch_engine.py tests/test_dutch_integration.py -x -q
```

### bbpPairings commands
```bash
# Generate RTG tournament
cd vendor/bbpPairings
echo "PlayersNumber=11\nRoundsNumber=5" > /tmp/cfg.txt
./bbpPairings.exe --dutch -g /tmp/cfg.txt -o /tmp/out.trf -s 42

# Check pairings
./bbpPairings.exe --dutch /tmp/out.trf -c

# Pair (outputs to stdout)
./bbpPairings.exe --dutch /tmp/out.trf -p
```

---

## 6. Investigation Priorities

### Priority 1: C9 unplayedGameRanks criterion
- **What:** Implement the `isSingleDownfloaterTheByeAssignee` flag and `unplayedGameRanks` encoding in the bye criterion (2×B bits)
- **Where:** `_compute_bracket_edge_weight()` line ~1527, and compute the flag in `_pair_iterative_mwm()` after preliminary MWM
- **Why:** This directly affects which player gets the bye in the final MWM

### Priority 2: Preliminary MWM fidelity
- **What:** Verify our lightweight preliminary MWM produces the same bye assignee as bbpPairings for all divergent cases
- **How:** Add logging to `_pair_iterative_mwm()` preliminary MWM section, compare with bbpPairings output
- **Tool:** `scripts/debug_divergence.py` already partially does this

### Priority 3: C12-C19 conditional allocation
- **What:** bbpPairings always allocates C12-C19 bit space (even if rounds_played < threshold). Our code conditionally allocates with `if rounds_played >= N`. This means the bit POSITIONS shift depending on round — which may cause misalignment
- **Where:** `_compute_bracket_edge_weight()` lines 1391-1470
- **Fix:** Always allocate the bit space, only conditionally SET the bits

### Priority 4: Exchange weight mechanism
- **What:** Compare `_exchange_weight()` (line 1818) with bbpPairings' `edgeWeightComputer` class
- **Where:** Phase 4-7 in `_pair_iterative_mwm()` (lines 2003-2200)
- **Why:** The reserved bits (3×B+1) encode exchange ordering that affects pair selection within brackets

### Priority 5: Bye cascade effect
- **What:** Once the bye player differs, all subsequent pairings in that round differ, AND future rounds diverge due to different game history
- **How:** Compare round-by-round: if R3 bye differs, check if R4/R5 would match given corrected R3 history

---

## 7. bbpPairings Architecture Reference

### Key insight from the Rust study document
The session summary at `/Users/ares/Development/rust/caissify_pairings/doc/SESSION_SUMMARY.md` identified these as the **top divergence sources** from the Python experience:

1. **Weight calculation differences** → Must port `computeEdgeWeight()` exactly
2. **Float tracking errors** → Use same Float enum structure  
3. **Color assignment order** → Match exact sequence
4. **Bracket boundary cases** → Test topscorer rules explicitly
5. **PSD calculation** → Embedded in weight, not separate

### bbpPairings' computeEdgeWeight() criteria order (from C++)
```
Tier 0: completion + bye eligibility (2 bits)
Tier 1: current bracket pair count (B bits)
Tier 2: current bracket score sum (SB bits)
Tier 3: next bracket pair count (B bits)
Tier 4: next bracket score sum (SB bits)
Tier 5: C9 bye unplayed games (2×B bits) — isSingleDownfloater only
Tier 6: insertColorBits ×4 (4×B bits)
Tier 7: C12 downfloat R-1 count (B bits)
Tier 8: C13 upfloat R-1 (B bits)
Tier 9: C14 downfloat R-2 count (B bits)
Tier 10: C15 upfloat R-2 (B bits)
Tier 11: C16 downfloat scores R-1 (SB bits)
Tier 12: C17 upfloat scores R-1 (SB bits)
Tier 13: C18 downfloat scores R-2 (SB bits)
Tier 14: C19 upfloat scores R-2 (SB bits)
Tier 15: reserved for exchange (2×B + B + 1 bits)
```

All tiers are ALWAYS allocated (bit space reserved). Bits are conditionally SET based on player state. Our code conditionally ALLOCATES some tiers — this is a structural mismatch.

---

## 8. Quick Win Candidates — Status

1. ~~**Always allocate C12-C19 bit space**~~ — **NOT A BUG.**
   bbpPairings also conditionally allocates with the same predicates.
   See "Verified non-issue" note in §2.

2. ~~**Implement C9 unplayedGameRanks**~~ — **DONE in Phase 3.5 (Fix 6),**
   but yielded no measurable improvement on the divergent fixtures.

3. ~~**Verify preliminary MWM bye matches bbpPairings**~~ — partially done.
   The `top_bracket_bit` bug was found and fixed (Phase 3.5, Fix 5)
   but did not move divergence counts.

---

## 9. Open Investigation Threads (Phase 3.6+)

The two highest-leverage hypotheses remaining, given that even-player
tournaments are perfect and odd-player counts are stuck at 22-24/50:

### 9.1 Bracket-loop weight composition vs. recompute

bbpPairings computes `baseEdgeWeights` **once per bracket** and then in
the bracket loop only **adds bits** to that base
(`addEdgeWeight`/`edgeWeightComputer` at
`vendor/bbpPairings/src/swisssystems/dutch.cpp` ~line 1055). Our Python
implementation **recomputes** weights inside the bracket loop by calling
`_compute_bracket_edge_weight` again with `in_current_bracket=True`,
overwriting earlier values via `_set_w(gi, gj, w)` (~line 1810).

These should be equivalent in principle, but any subtle difference in
which bits are "added" vs "set fresh" could be hiding behind it. Worth
auditing this specifically.

### 9.2 MDP/opponent finalization in odd brackets

bbpPairings' MDP-resident loop (lines 1107-1255) finalizes one MDP at a
time, then re-runs the matching with `nextScoreGroupBegin - scoreGroupBegin`
boost on the chosen opponent (line 1196). Our Phase 2 (MDP opponent
selection at line ~1927) does this differently. With odd-player counts
this is exactly where divergences would surface.

### 9.3 Confirmed scope

Both threads above only matter for the bracket loop in the **presence of
MDPs** (odd brackets). Since even-player tournaments are 0-divergent, the
issue is definitively confined to MDP / bye-assignee handling within the
bracket loop, not in the global edge-weight encoding.
