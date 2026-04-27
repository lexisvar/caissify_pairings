# Algorithm Description — `caissify-pairings` Dutch System Engine

> **Purpose.** This document maps the `caissify-pairings` Dutch System
> implementation to every clause of **FIDE Handbook C.04.3** (effective
> 1 February 2026). It is intended for a FIDE Technical and Education
> Commission (TEC) reviewer evaluating an FE-1 endorsement application.
>
> All source references point to
> `src/caissify_pairings/engines/dutch.py` unless stated otherwise.

---

## Overview

The engine is implemented as a pure Python class `DutchEngine` that
accepts a single self-contained round snapshot (players, previous
pairings, round number) and returns a list of pairings. It has no
persistent state between rounds; the caller is responsible for
accumulating and passing per-player history on each call.

Runtime stack: **Python 3.10+** with
[**networkx**](https://networkx.org/) for maximum-weight matching
(Edmonds' blossom algorithm). No other dependencies.

Entry points (all delegate to `DutchEngine`):

| Entry point | Use |
|---|---|
| `generate_pairings(system="dutch", ...)` | Library API |
| `caissify-pairings` | JSON-over-stdin CLI |
| `caissify-pairings-check FILE.trf` | Free Pairings Checker (FPC) |
| `dutch_pairings(players, ...)` | Convenience wrapper |

---

## A — General concepts and definitions

### A.1 — Pairing number assignment

> *Players are ranked according to their score, then their rating, then
> their title, then their FIDE ID.*

**Implementation.** `DutchEngine._build_players()` sorts players by
descending rating, descending title priority, ascending starting
number, and alphabetical name. Pairing numbers are assigned
sequentially from this sorted order and remain fixed for the
tournament (caller passes `pairing_number` from round 2 onward).

### A.2 — Score groups

> *Players are divided into score groups. Within each group players are
> ordered by pairing number.*

**Implementation.** `DutchEngine._build_scoregroups()` groups
`DutchPlayer` objects by `score` (descending) and sorts within each
group by `pairing_number` (ascending).

### A.3 — S1/S2 split

> *Each score group is divided into S1 (upper half) and S2 (lower half).
> If the group has an odd number of players, S2 gets the extra player.*

**Implementation.** `DutchEngine._split_scoregroup()` slices the sorted
group at `n // 2`. S1 = `group[:half]`, S2 = `group[half:]`.

### A.4 — Homogeneous and heterogeneous brackets

> *A bracket is homogeneous when all players come from the same score
> group; heterogeneous when it contains moved-down players (MDPs) from
> a higher bracket.*

**Implementation.** The iterative bracket loop in
`_pair_iterative_mwm()` tracks a carried-over remainder from each
bracket. Remainder players from bracket *k* join bracket *k+1* as
MDPs, forming a heterogeneous bracket. The MWM edge-weight encoding
uses two separate tier groups (TIER 1/2 for same-score pairs,
TIER 3/4 for cross-bracket pairs) to prioritize within-group pairings
over cross-bracket pairings.

### A.5–A.7 — Floats

> *A player who moves from one bracket to another is said to float.
> Moving down is a downfloat; moving up is an upfloat.*

**Implementation.** `DutchEngine._record_floats()` appends `FloatDir.DOWN`
or `FloatDir.UP` to each player's `float_hist` after pairing. For
pairings produced by `_pair_iterative_mwm()`, float directions are
inferred from the relative scores of the two paired players:
if `score(p1) > score(p2)`, then p1 floated down and p2 floated up.
The `DutchPlayer.last_float` and `DutchPlayer.float_hist` properties
expose this history to the criteria evaluators.

---

## B — Absolute criteria

### B.1 — No repeated opponents

> *Two players must not be paired together more than once in the same
> tournament.*

**Implementation.** `DutchEngine._can_pair()` checks `p2.id in p1.opponents`.
The `opponents` set is built in `_build_players()` from the
`previous_pairings` argument passed by the caller.

### B.2 — No pairing within a round

Enforced by MWM graph construction (no self-edges, and each player
appears only once in the output).

### B.3–B.4 — Availability and withdrawal

Players absent for a round (withdrawn, absent) are excluded by the
caller before calling the engine. The engine treats its input list as
the complete set of active, pairable players.

### B.5–B.6 — Absolute colour constraints

> *A player must not receive the same colour three times in a row. A
> player's colour difference must not exceed ±2.*

**Implementation.** `DutchPlayer.would_violate_absolute_color()` checks
both constraints for a candidate colour assignment. `_can_pair()` calls
`_has_legal_color_assignment()` which tests both possible assignments
(p1 white / p2 black and vice versa) and returns `False` only if
neither is legal.

**Last-round relaxation.** Per C.04.1 §6-7, absolute colour constraints
are relaxed in the final round for top scorers. `_can_pair()` applies
this when `_is_last_round` is `True` and at least one player's score
exceeds `(rounds_played / 2.0)`.

---

## C — Pairing criteria (C.1–C.19)

The engine uses a **bracket-by-bracket maximum-weight matching** (MWM)
via networkx's `max_weight_matching`. All criteria C.1–C.19 are encoded
as bit-packed integer edge weights computed by
`_compute_bracket_edge_weight()` so that the MWM maximises a single
objective that is lexicographically equivalent to the full criterion
hierarchy.

### C.1 — Maximize the number of players paired

**Implementation.** Completion bits (2-bit field, most significant) in
the edge weight. Value 3 = both players available for a non-bye game,
2 = one is a bye candidate, 1 = both are bye candidates. MWM maximizes
this first, ensuring the maximum number of players are paired.

### C.2 — Top score group first

**Implementation.** The bracket loop in `_pair_iterative_mwm()` processes
score groups from highest to lowest. Higher brackets are finalized
before lower brackets are considered.

### C.3 — Minimize score differences (PSD)

**Implementation.** TIER 1/2 and TIER 3/4 in the edge weight encode the
score of the higher-ranked player in the pair. Pairs within the current
bracket (same score) are weighted higher than cross-bracket pairs; among
cross-bracket pairs, those involving higher-scoring players are
preferred. This is equivalent to minimizing the sum of score differences
(PSD).

### C.4 — S1 vs S2 pairing

> *Each player from S1 is paired with a player from S2.*

**Implementation.** `_split_scoregroup()` partitions the bracket.
The iterative MWM respects this split via the TIER 1/2 same-bracket
weight: a pair where both players are in the current bracket (same score
group, hence S1 meets S2) scores higher than any cross-bracket pair.

### C.5–C.8 — Transpositions

> *If the S1-vs-S2 pairing cannot be completed, transpositions of S2
> are tried in lexicographic order.*

**Implementation.** `_generate_transpositions()` generates permutations
of S2 players and sorts them lexicographically by pairing number. The
MWM approach subsumes the explicit transposition search: by encoding all
criteria in edge weights and running MWM, the optimal S2 ordering is
found without enumerating all permutations. For the legacy greedy path
(used as a final fallback only), `_pair_scoregroup()` explicitly tries
transpositions up to `MAX_TRANSPOSITIONS`.

### C.9–C.12 — Exchanges

> *If no transposition completes the bracket, a player from S1 is
> exchanged with a player from S2 from a lower bracket, in order of
> minimizing the pairing-number difference.*

**Implementation.** `_generate_exchanges()` generates all single-player
swaps between S1 and S2, sorted by ascending pairing-number difference
(closest swap first). In the MWM path, exchanges are handled naturally
by the bracket's heterogeneous pairing phase: MDPs from a lower bracket
that are matched to an upper-bracket player constitute the exchange.
The TIER 3/4 weight encodes the cross-bracket pairing score to prefer
exchanges with the smallest score gap.

### C.10 — Minimize colour preference violations

> *Minimize the number of players who do not receive their colour
> preference.*

**Implementation.** The `c_compat` bit in the colour section of the
edge weight. Set to 1 when the two players' colour preferences are
compatible (different or one has no preference), i.e. both can receive
what they want. MWM maximizes this, minimizing violations.

### C.11 — Minimize strong preference violations

> *Among colour violations, minimize those involving strong preferences.*

**Implementation.** The `c_strong` bit. Set to 1 when at least one
player has a strong (strength ≥ 2) or absolute (strength 3) preference
that conflicts with the other player's preference. This bit is placed
above `c_compat` in the weight, so MWM resolves strong conflicts before
mild ones.

### C.12–C.15 — Repeat float minimization

> *C.12 and C.14: minimize the number of players receiving the same
> float direction as in the previous round (C.12) and two rounds ago
> (C.14). C.13 and C.15: same for upfloats.*

**Implementation.** Bits C12–C15 in the edge weight. For a potential
downfloater (i.e. a player that would be carried to a lower bracket),
the bit is set to 0 if the player also downfloated in the relevant
previous round, and 1 otherwise. Only **remainder** players (those who
will actually float) are tested — paired MDPs are not counted.

### C.16–C.19 — Score-based float tie-breaking

> *When C.12/C.14 or C.13/C.15 are still tied, minimize the score sum
> of the repeated floaters.*

**Implementation.** Bits C16–C19 in the edge weight, using a quantized
score-group encoding (score group shift per `scoreGroupShifts` map).
This encodes the score of the floating player as a multi-bit field; MWM
prefers pairings where the repeated floater has a lower score, directly
implementing the tie-breaking criteria.

---

## D — Moved-down players (MDPs)

> *Players not paired in their own bracket are moved down to the next
> bracket as MDPs. MDPs must be paired with players from the bracket
> they move into, before the bracket's own players are paired among
> themselves.*

**Implementation.** In `_pair_iterative_mwm()`:

1. **Preliminary MWM** (odd counts only): a lightweight MWM run with
   completion-only weights determines which player is the bye candidate
   (`byeAssigneeScore`), narrowing the `bye_candidates` set.
2. **Phase 1 — MDP selection**: The MWM graph includes all active
   players in the current and next bracket. MDPs are identified as
   players from the current bracket whose pairing partner is in the
   next bracket. Their edges are boosted to lock their matches.
3. **Phase 2 — MDP opponent selection**: All MDPs are active in the
   graph simultaneously (no isolation). MWM selects the best
   opponent for each MDP jointly.
4. **Phase 3 — Remainder collection**: Unpaired players from the
   current bracket become the next bracket's MDPs.
5. **Phase 4–7 — Exchange mechanism**: Minimize exchanges between S1
   and S2 remainder groups, then minimize BSN (bracket scoring number)
   differences among valid exchange sets.
6. **Phase 8 — Finalize pairs**: Apply `_assign_colors()` to each
   matched pair.
7. **Phase 9 — Advance bracket**: Termination guard for no-progress
   situations; unpaired players cascade to the next bracket.

---

## E — Colour allocation (§E.1–E.6)

All colour allocation is implemented in `DutchEngine._assign_colors()`.

### E.1 — Grant both preferences (compatible preferences)

If the two players have different non-`NONE` colour preferences, each
gets what they want. Implemented as the first branch in `_assign_colors()`.

### E.2 — Grant the stronger preference

If preferences are incompatible, the player with the higher
`preference_strength` (3 = absolute, 2 = strong, 1 = mild) receives
their preference. When both strengths are equal and both want the same
colour, the player with the wider colour imbalance (more negative diff
for white, more positive for black) is granted their preference.

### E.3 — Alternation from history

When E.1 and E.2 do not resolve the assignment, the colour histories
are scanned backwards for the most recent round where the two players
had different colours. The assignment is the reverse of that round
(alternation principle).

### E.4 — Higher-ranked player's preference

If history scanning yields no differentiating round, the higher-ranked
player (lower pairing number) receives their colour preference.

### E.5 — Initial-colour rule

If no preference exists (both players are in their first round or have
no preference), the higher-ranked player receives the `initial_color`
(default `"white"`, configurable) when their pairing number is odd, and
the opposite when it is even.

### E.6 — Forfeits and byes

Unplayed games (byes, forfeits) do **not** affect `color_hist`.
`DutchPlayer.color_hist` contains only colours from actually-played
games. The FPC (`caissify_pairings.fpc.check_trf`) reconstructs colour
history from TRF data using the same rule.

---

## Bye assignment (C.04.1 §3–4)

`DutchEngine._select_bye_player()` selects the bye recipient:

1. Candidates are players where `is_bye_eligible` is `True` (no prior
   PAB or forfeit-win, matching bbpPairings `eligibleForBye`).
2. Among candidates, the player with the lowest score and highest
   pairing number (weakest player) is preferred.
3. In rounds ≥ 2 with ≤ 30 players, a completability check is run:
   the candidate is only selected if removing them leaves the remaining
   players fully pairable (verified by `_backtrack_match()`).
4. If no eligible candidate allows full pairing, the search expands to
   all players (allowing repeat byes if necessary), sorted by
   fewest prior unplayed games, then lowest score.

For round 1 the bye is pre-selected before the MWM run. For rounds ≥ 2
with an odd player count, bye assignment is integrated into the MWM via
completion bits, with preliminary MWM narrowing the candidate set.

---

## Baku Acceleration (C.04.5.1)

Opt-in via `DutchEngine(accelerated=True)`.

`_apply_baku_virtual_scores()` adds `+1.0` to the pairing-time score
of the top half (ceiling division) of players, by starting number, for
rounds 1 and 2. From round 3 onwards no virtual point is applied.
Real `score` values are never mutated; the virtual point is applied to
a private copy of the player dicts before `_build_players()`. The rest
of the engine operates identically on the modified scores.

**A.7 status:** Baku Acceleration is implemented and unit-tested
(14 tests in `tests/test_baku_acceleration.py`). Full A.7
cross-validation against `bbpPairings` under acceleration is not yet
complete — it requires `XXA` TRF tag support, which is planned but not
yet implemented. The base (non-accelerated) Dutch submission is not
affected.

---

## Fallback chain

When the primary iterative MWM cannot produce a complete matching, the
engine falls back in order:

1. **Backtracking** (`_backtrack_match()`) — exhaustive FIDE-compliant
   search with progressive constraint relaxation, mirroring the FIDE
   §C.4 procedure.
2. **Greedy** (`_greedy_match()`) — O(n²) last-resort matcher; invoked
   only when backtracking also fails (should not occur in practice for
   valid inputs).

---

## Source file map

| C.04.3 clause | Primary source location |
|---|---|
| A.1–A.3 (ordering, scoregroups, S1/S2) | `_build_players()`, `_build_scoregroups()`, `_split_scoregroup()` |
| B.1–B.6 (absolute criteria) | `_can_pair()`, `_has_legal_color_assignment()`, `DutchPlayer.would_violate_absolute_color()` |
| C.1–C.4 (maximize pairs, PSD, S1/S2) | `_pair_iterative_mwm()`, `_compute_bracket_edge_weight()` TIER 0–4 |
| C.5–C.8 (transpositions) | `_generate_transpositions()`, `_pair_scoregroup()` (fallback) |
| C.9–C.12 (exchanges) | `_generate_exchanges()`, MWM TIER 3/4 |
| C.10–C.11 (colour violations) | `_compute_bracket_edge_weight()` `c_compat`, `c_strong` bits |
| C.12–C.15 (repeat float bits) | `_compute_bracket_edge_weight()` C12–C15 bits |
| C.16–C.19 (float score tie-break) | `_compute_bracket_edge_weight()` C16–C19 bits |
| D (MDPs, bracket loop) | `_pair_iterative_mwm()` phases 1–9 |
| E.1–E.6 (colour allocation) | `_assign_colors()`, `DutchPlayer.color_preference`, `DutchPlayer.preference_strength` |
| C.04.1 §3–4 (bye) | `_select_bye_player()`, completion bits in `_compute_bracket_edge_weight()` |
| C.04.1 §6–7 (last-round relaxation) | `_is_last_round`, `_can_pair()` last-round branch |
| C.04.5.1 (Baku Acceleration) | `_apply_baku_virtual_scores()`, `DutchEngine.__init__` |

---

## A.7 conformance summary

| Benchmark | Direction | Tournaments | Discrepancies | Result |
|---|---|---|---|---|
| 20 players × 9 rounds | bbpPairings RTG → our FPC | 5,000 | **0** | ✅ PASS |
| 20 players × 9 rounds | our RTG → bbpPairings FPC | 5,000 | **0** | ✅ PASS |
| 10 players × 5 rounds | bbpPairings RTG → our FPC | 5,000 | **0** | ✅ PASS |
| 10 players × 5 rounds | our RTG → bbpPairings FPC | 5,000 | **0** | ✅ PASS |

Test harness: `tests/test_cross_validation.py` and
`tests/test_rtg_fpc_validation.py`. Reference oracle:
`bbpPairings` v6.0.0 (FIDE-endorsed, Apache-2.0,
[github.com/BieremaBoyzProgramming/bbpPairings](https://github.com/BieremaBoyzProgramming/bbpPairings)).
