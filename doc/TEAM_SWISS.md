# Team Swiss — FIDE C.04.6 (2026) design note

> How `caissify-pairings` implements the **FIDE Swiss Team Pairing
> System** (Handbook C.04.6, approved by the Council on 28/10/2025, in
> force from 1 February 2026), article by article, and every reading we
> had to choose where the text leaves room. The regulation text is the
> authority; this note only says what the code does with it.

Code:

| Module | Role |
|---|---|
| [`engines/team_swiss.py`](../src/caissify_pairings/engines/team_swiss.py) | The engine (`TeamSwissEngine`, `system="team_swiss"`) |
| [`engines/_team_matching.py`](../src/caissify_pairings/engines/_team_matching.py) | Perfect-matching checks and the lexicographic-first optimal pairing |
| [`team_trf.py`](../src/caissify_pairings/team_trf.py) | TRF26 team records: read, rebuild matches, write |
| [`team_fpc.py`](../src/caissify_pairings/team_fpc.py) | Free pairings checker for team files |
| [`team_rtg.py`](../src/caissify_pairings/team_rtg.py) | Random team tournament generator |
| [`team_cli.py`](../src/caissify_pairings/team_cli.py) | `caissify-pairings --team` |
| [`tests/team_reference.py`](../tests/team_reference.py) | Brute-force reference pairer (tests only) |

Sources read: C.04.6 2026 (and the 2024 text, to see what changed),
C.04.1 and C.04.2 (2026), C.04.7 (2026), TRF26 (C.02.03 Annexure A),
ETT26 and MTB26 (Annexures B and C), C.02.03 §7, the TEC 2026 Congress
report.

---

## 1. Article map

### C.04.6 — Swiss Team Pairing System

| Article | Rule | Code |
|---|---|---|
| 0 | C.04.1 and C.04.2 Arts. 1, 2.4, 2.5, 3, 4 apply mutatis mutandis; Basic Rules 6 and 7 never apply | See the C.04.1/C.04.2 tables below. There is no absolute colour rule anywhere in the engine. |
| 1.1.1–1.1.3 | Distinct TPNs, set by the competition rules / Chief Arbiter, stable | Input field `tpn` (else `starting_number`, else `id`). Duplicates raise `ValueError`. The engine never renumbers. |
| 1.2.1–1.2.2 | Primary score MP or GP; secondary used for colours or not; default MP primary, GP for colours | `primary_score="mp"` (default) / `"gp"`; `use_secondary_for_colours=True` (default). `TeamState.score` / `.secondary`. |
| 1.3.1 | Scoregroup = same score | `generate_pairings`: the top scoregroup is every unpaired team with the highest pairing score. |
| 1.3.2 | Bracket = residents + upfloaters, even | `_select_upfloaters` + `_pair_bracket`. |
| 1.4 | PAB: no opponent, no colour, a draw's MP and GP unless the rules say otherwise; same for all PABs | `bye_match_points` / `bye_game_points` (defaults: draw MP; boards × draw GP). The bye row carries `match_points`/`game_points`; the engine itself awards nothing. |
| 1.5 | Floater = plays an opponent with a different score | `TeamState.prev_floater`, worked out from the histories (both teams of a cross-score match are floaters). See ambiguities A2, A3. |
| 1.6.1–1.6.2 | Colour = first-board colour in matches actually played; CD = Whites − Blacks | `TeamState.colours` (played and adjourned matches only), `colour_difference`. |
| 1.7 | Type A by default; Type B or none if the rules say so | `colour_preferences="A"` / `"B"` / `"none"`. |
| 1.7.1 | Type A simple preference | `type_a_preference`. |
| 1.7.2 | Type B strong / mild preference | `type_b_preference` (ambiguity A6). |
| 2.1.1 | C1: no rematch | Adjacency built in `generate_pairings`: played matches (history + `previous_pairings`) and forbidden pairs are never edges. |
| 2.1.2 | C2: no PAB after a PAB, a forfeit win or a full-point bye | `TeamState.bye_eligible`. |
| 2.2.1 | C3: a legal pairing must exist for the teams not yet paired | Every upfloater set is checked with `_legal_set`: the bracket **and** the rest must each have a perfect matching. The PAB choice checks the rest too. |
| 2.3.1 | C4: fewest upfloaters | Size loop in `_upfloaters_by_enumeration` (smallest legal size first); weight `w1` in `_upfloaters_by_matching`. |
| 2.3.2 | C5: maximise the upfloaters' scores, taken in ascending order | `_compositions` yields per-level counts in C5 order (fewest from the lowest level, then the next…). |
| 2.3.3 | C6: the following scoregroup's bracket must comply with C1, C3, C4 | `_c6_value` / `_next_bracket_upfloaters` (ambiguity A4). |
| 2.3.4 | C7: fewest upfloaters that floated in the previous round (not in the last two rounds) | `_c7_value`; `in_last_two_rounds`. |
| 2.3.5 | C8: fewest teams whose preference is not met | `pair_cost_vector` (a pair costs 1 when both prefer the same colour). |
| 2.3.6 | C9 (Type B): fewest unmet strong preferences | `pair_cost_vector` (both strong for the same colour). |
| 2.3.7 | C10: fewest upfloaters' opponents that floated in the previous round (not in the last two rounds) | `pair_cost_vector`. |
| 3.1 | Legal = C1 + C2 (+ C3 during the pairing) | As above. |
| 3.2 | Top scoregroup | `generate_pairings`. |
| 3.3.1–3.3.2 | Steps: PAB, then top scoregroup + upfloaters → bracket, repeat, then colours | `generate_pairings`, in that order. |
| 3.3.3 | No legal round-pairing: the Chief Arbiter decides | The engine still returns a full round with the fewest broken rules and flags the rows (`violations`: `C1`, `C2`), like the Dutch engine since 0.7.1. `_relax_c1`, `_choose_pab`. |
| 3.4.1–3.4.4 | PAB: leaves a legal pairing; lowest score; most matches played; largest TPN | `bye_order`, `_choose_pab` (ambiguity A7). |
| 3.5.1–3.5.5 | Upfloater sets: C4/C5, sorted by score desc then TPN asc, lexicographic; first legal with C6, C7 | `_upfloaters_by_enumeration` (literal) with `_upfloaters_by_matching` as the exact polynomial fallback. `upfloater_sets` shows the order. |
| 3.6.1–3.6.4 | Pairings identified by sorted top members then their partners; first in lexicographic order with C1, C8–C10 | `_pair_bracket` → `LexFirstMatcher`; `pairing_identifier` shows the identifier. |
| 4.1 | Initial-colour by lot | `initial_color` (default `"white"`; TRF record 152). |
| 4.2.1–4.2.3 | First-team: higher primary score, higher secondary (unless not used), smaller TPN | `first_team`. |
| 4.3.1–4.3.9 | Colour rules in order | `_colour_rule` (returns the rule that decided; `colour_rule` exposes it). |

### C.04.1 — Basic rules (applied mutatis mutandis)

| Article | Team reading | Code |
|---|---|---|
| 1 | Number of rounds declared beforehand | `total_rounds` (required). |
| 2 | Two teams meet once | C1. |
| 3 | PAB; same points for every PAB | Overridden by C.04.6 1.4 (a draw by default). |
| 4 | PAB not after a PAB or a win's points without playing | C.04.6 C2 (PAB, forfeit win, full-point bye). |
| 5 | Pair equal scores | Scoregroups. |
| 6, 7 | Colour difference / three in a row | **Never apply** (C.04.6 §0). |
| 8 | General colour principle | Article 4 rules. |
| 9 | Pairings must be explainable | `engine.trace` lists every decision; `caissify-pairings-check --team --explain`. |

### C.04.2 — General handling rules (Arts. 1, 2.4, 2.5, 3, 4)

| Article | Team reading | Code |
|---|---|---|
| 1.1–1.3 | Published system; acceleration announced; reported | TRF record 192 (`FIDE_TEAM_…`), record 250. |
| 1.4 | Identical pairings across programs; free checker | `caissify-pairings-check --team`. |
| 2.4, 2.5 | Late entries: no points for unplayed rounds, paired on arrival, provisional TPNs | Caller sets `tpn` and the history (zero-point rounds). Baku keeps GA fixed with `acceleration_group_size` (C.04.7 1.3). |
| 3.1 | Adjourned match = draw for pairing | History type `adjourned`: draw MP and GP, colours count. |
| 3.2 | Withdrawn teams not paired | `withdrawn: True` or `withdrawn_teams=[…]`. |
| 3.3 | Teams known to be absent not paired, score zero | `absent: True` or `absent_teams=[…]`; history type `zpb`. |
| 3.4 | Only played matches count for colour sequences; unplayed rounds move to the start | `TeamState.colours` holds played matches only; rule 4.3.6 aligns the two sequences at their ends. |
| 3.5 | Paired but unplayed matches may be repeated | Forfeits are not C1 edges. |
| 3.6 | Order of the boards: score of the higher-ranked team, sum of scores, its TPN | `_order_tables` (pairing scores). |
| 4 | Competition rules (corrections, published pairings) | Arbiter's process: re-run the engine on corrected data. |

### C.04.7 — Baku Acceleration (team use)

| Article | Rule | Code |
|---|---|---|
| 1.1 | Win = two draws, loss = 0 | Holds for MP 2/1/0. |
| 1.2 | GA = first half rounded up to even = 2 × ceil(N/4) | `baku_group_size`; GA by TPN. |
| 1.3 | Late entries: same last GA team | `acceleration_group_size`. |
| 1.4 | Accelerated rounds = first half (rounded up); a win's points before the first half of them, half after; Example 2: 11 rounds MP → 2 in rounds 1–3, 1 in rounds 4–6; GP primary cannot use Baku | `baku_virtual_points(win_points=match_points_win)`; `accelerated=True` with `primary_score="gp"` raises `ValueError`. |
| 1.5 | Pairing score = standings points + virtual points (scoregroups, order, board sorting) | `TeamState.score`. See ambiguity A8 for where else it is used. |

---

## 2. Algorithm

1. **State.** For every team: MP, GP, pairing score (primary + virtual
   points), the first-board colours of played matches, played opponents
   (C1), PAB eligibility (C2), whether it floated in the previous round,
   its colour preference.
2. **PAB (3.4).** Candidates in 3.4 order; the first that is C2-eligible
   and leaves a perfect matching of the others gets the bye.
3. **Upfloaters (3.5).** Sets are visited literally: size (C4), then
   per-level counts in C5 order, then the TPN order of 3.5.3/3.5.4. The
   first legal set (bracket and rest both pairable: C1, C3) fixes C4 and
   C5; the sets with that composition are then ranked by (C6, C7) and the
   first one with the minimum wins; the scan stops as soon as a set
   reaches the lower bounds of C6 and C7. If more than 600 sets would be
   visited, an exact maximum-weight matching (weights encode C4 > C5 > C6
   > C7) finds the optimum and the first set is built member by member.
4. **Bracket (3.6).** Every pair costs (C8, C9, C10). The minimum total
   comes from Edmonds' blossom algorithm (networkx); then the first
   pairing in identifier order with that total is built: top members
   first (smallest TPN that can still be a top member), then each top
   member's smallest possible partner. Each step is checked against an
   optimal "incumbent" pairing, repaired by a two-pair swap when
   possible; only when that fails does a new blossom run decide.
5. **Colours (4).** Rules 4.3.1–4.3.9 in order, then board colours from
   the colour sequence.

The search is exact: on small fields it agrees with
[`tests/team_reference.py`](../tests/team_reference.py), which enumerates
every upfloater subset and every pairing (section 4).

---

## 3. Ambiguities and the readings chosen

Each reading is explicit; where a reasonable alternative exists it is an
option.

**A1. "Choose the first … that complies with C6 and C7" and "the first
pairing that also complies with C8, C9 and C10" (3.5.5, 3.6.4).**
Read as *minimisation*: among the candidates, keep those with the
minimum C6, then of those the minimum C7 (and C8, C9, C10 for brackets),
then take the first in the regulation's order. Reason: Article 2.3 says
"comply **as much as possible** with the following criteria, given in
descending priority", and C4–C10 are all worded "minimise". A literal
pass/fail reading would leave the pairing undefined whenever no
candidate reaches zero. Not configurable (the alternative is not a
complete rule). Whenever some candidate reaches the ideal value, both
readings give the same result.

**A2. Is the PAB team a floater in the next round (C7, C10)?**
No by default: Article 1.5 defines a floater as a team that *plays
against an opponent* with a different score, and the PAB has no
opponent. (The Dutch system counts the bye as a downfloat, but the team
system has no downfloats and no such rule.) Option:
`bye_is_floater=True`.

**A3. Does a paired but unplayed match (forfeit) make floaters?**
No by default: Article 1.5 says "plays against", and 1.6.1 shows that
the regulation means *actually played*. Option: `unplayed_is_floater=True`.
"The previous round" is round *r − 1* itself: a team absent then is not a
floater.

**A4. C6 and C3 when the following scoregroup is odd.**
C6 is measured as the number of upfloaters the next bracket needs
(C4) for the scoregroup that remains after this choice, while the rest
of the field stays pairable (C1, C3): that is, the minimum, over all
perfect matchings of the remaining teams, of the scoregroup's teams
paired outside it. An odd scoregroup needs at least one upfloater; that
is not a C6 failure, because every candidate set with the same C4/C5
composition leaves the same parity. Default `c6_mode="minimise"`:
minimise that number. Option `c6_mode="parity"`: a set complies when the
number equals the parity (0 or 1) and all non-complying sets are equal.
The two differ only when no set reaches the parity. If every team of the
following scoregroup became an upfloater, C6 does not apply ("this
scoregroup is now empty"); only that scoregroup is looked at, as the
note says.

**A5. TRF26 record 250 field order.** The field table puts fictitious
**match** points in columns 5–8 and **game** points in 10–13, but the
TRF26 example for an MP-based Baku team event is `250 00.0 02.0 001 003
0001 0090`, i.e. 2 points in the game-points column. We follow the field
table (`baku_250_order="spec"`, default; real TRF26 team files we have
seen write `250  2 …`, MP first). `baku_250_order="example"` swaps the two
fields. The reader warns when an MP tournament has a 250 record with game
points but no match points. Virtual points of the primary kind are added
to the pairing score; the other kind is kept for round-trips only.

**A6. Type B, last round, CD 0 after two equal colours.** 1.7.2 gives a
*strong* preference to a CD-0 team that had the same colour twice, but
also says a team has no Type B preference "when its CD is zero when
pairing for the last round". We keep the strong preference (default
`type_b_keep_strong_in_last_round=True`): the "no preference" sentence
mirrors the "not the last round" exception of the mild rule, and the
strong rule is word for word the Type A rule, which has no last-round
exception. `False` applies the sentence literally.

**A7. "Has played the highest number of matches" (3.4.3).** Counts
matches actually played (1.6.1's notion); forfeits do not count.
Option `forfeits_count_as_played=True`.

**A8. Where the Baku pairing score is used.** C.04.7 1.5 names
scoregroups, their internal order and the board order. We also use it
for every other "score" of the pairing rules: PAB 3.4.2, C5, the floater
definition (pairing scores of round *r − 1*) and the first-team (4.2.1;
option `baku_score_for_colours=False` uses the real primary score
there). The secondary score never gets virtual points. Virtual points
are a win's match points (2 by default), as in C.04.7 Example 2.

**A9. Rule 4.3.6 with unplayed rounds.** "Alternate the colours to the
most recent time in which one team had White and the other Black", with
C.04.2 3.4 (unplayed rounds moved to the start): the two played-colour
sequences are aligned at their **ends**, not by round number.
`BWBuW` vs `WBWBW` compares as `uBWBW` vs `WBWBW`.

**A10. Upfloaters' opponents (C10).** The opponents of the members of
the chosen upfloater set. In a C4-minimal set two upfloaters never meet
each other (removing such a pair would leave fewer upfloaters), so every
upfloater meets a resident.

**A11. Board colours (record 352).** The sequence is read as the board
colours of the team with White on board 1; one starting with `B` is read
from the other team's side (inverted). Default without a sequence:
alternate, odd boards White (the Olympiad rule: "the teams ranked first
in the pairing list shall have the white pieces on the odd-numbered
boards").

**A12. Initial-colour without record 152.** TRF26 says the default is
"the colour of the highest ranked participant who was paired in the
first round". The checker infers it from round 1 with Article 4.3.1
instead (an even-TPN first-team has the opposite colour); the two agree
whenever TPN 1 played round 1, and the checker reports that it inferred
it.

**A13. The PAB's game points.** "As many … game points as are rewarded
for a draw": a drawn match, i.e. boards × the draw game points (2 for
four boards). Record 320 overrides.

**A14. Forfeit wins in TRF26.** "Teams getting forfeit wins … receive TW
MP and P×W GP" (record 299 notes): the reader sums the present players'
`+` results; when no player is credited it uses boards × W.

**A15. Who sits on which board (TRF26).** Present players in roster
order (record 310), unless a 300 record gives the line-up. A team's
colour is its board-1 colour; when board 1 is empty it is derived from
another board through the 352 sequence. When no board of a played match
was played (every game forfeited), the file cannot tell the colour: the
reader warns and the match does not count for colours.

**A16. Forbidden pairings (record 260).** Treated as absolute, like C1,
in the rounds given.

**A17. No legal pairing (3.3.3).** The Chief Arbiter decides. The
engine returns a complete round anyway, with the fewest rematches /
forbidden pairs (`violations: ["C1"]`) or a second bye (`["C2"]`), and
logs a warning, so the arbiter sees exactly which rule had to give.

---

## 4. Validation

* **Unit tests** (`tests/test_team_swiss_engine.py`): every criterion
  C1–C10, the bye rules, each colour rule 4.3.1–4.3.9, Types A / B /
  none, MP and GP primary, Baku, forfeits, withdrawals and absences,
  C.04.2 3.4, the regulation's examples (3.5.4, 3.6.2) and a 6-team,
  3-round example checked by hand.
* **Brute-force reference** (`tests/team_reference.py`,
  `tests/test_team_reference.py`): an independent pairer that re-derives
  every team's state and enumerates every upfloater subset and every
  pairing in identifier order. Compared with the engine on random
  tournaments (3–16 teams, forfeits, absences, withdrawals, all colour
  types, MP/GP, Baku, both C6 modes). Result at the time of writing,
  **0 differences**: 42,600 of 42,600 compared rounds over 10,000
  tournaments of 3–12 teams, 2,717 of 2,717 over 500 tournaments of
  13–16 teams, and 8,563 of 8,563 with the exact fallback search for
  upfloaters forced on. Rounds with no legal pairing at all (3.3.3, the
  arbiter decides) are not compared.
* **RTG → TRF26 → checker** (`tests/test_team_trf_fpc.py`): the engine's
  own tournaments always pass its checker; TRF26 round-trips are exact.
* **Performance** (Python 3.13, one core): 200 teams over 11 rounds,
  Types A and B, with and without Baku: about 0.05 s per round on
  average, at most 0.12–0.25 s depending on the run; 400 teams: at most
  0.4 s per round. The exact fallback for upfloaters, when the literal
  enumeration exceeds 600 sets (not seen in these runs), costs up to a
  few seconds at 200 teams. `tests/test_team_trf_fpc.py::test_performance_200_teams`
  (slow) measures it.

No third-party pairing program was run. Cross-validation against an
outside reference (for example Gacrux) is a separate step.
