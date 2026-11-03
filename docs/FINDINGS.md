# Findings: why full negotiation got fewer criticals to care on time

## The problem

With real fire-station bases, the rules-only evaluation (seeds 1 to 20, 12 incidents, `eval-004`) showed the full agent system as the **weakest** agent variant on the primary metric:

| Policy | Criticals on time |
|---|---|
| Greedy baseline | 39.6% |
| Agents (full) | 61.1% |
| Agents, no negotiation | 71.8% |

All diagnosis below used **development seeds 101 to 120 only** (`tools/diagnostics/negotiation_gap.py`), never the evaluation seeds. The gap was there too, but smaller: full agents 68.5% vs no negotiation 71.5% (`eval-005`). Of 204 criticals, 29 got to care on time without negotiation but not with it, and 22 the other way round.

## The evidence

For each of the 29, the tool compares the two runs stage by stage (report to dispatch, drive to the scene, waiting on scene, drive to hospital) and takes the stage that lost the most time as the primary cause:

| Primary cause | Victims | Average time lost |
|---|---|---|
| A. Award declined after an accept offer ("capacity taken since my offer"), then a 5-minute retry with rounds reset; the early firm reservation was lost | 15 | 26 min waiting on scene |
| D. Longer drive to hospital (arrived to no slot, or a farther least-bad hospital) | 6 | 28 min |
| C. ALS reached the critical later (tied up longer on earlier jobs) | 5 | 20 min |
| E. Waited on scene with no accept offer | 2 | 36 min |
| B. Arbiter had no ready patient to release, fell to least-bad | 1 | 10 min |

Contributing factors (a victim can have several): award declined 19; firm reservation came later 17; Arbiter ruled least-bad where no negotiation got "release ready patients" 15; different hospital 15; ALS later 7; a hold for another victim 3; arrived to no slot 3. Conditional offers, swaps, transfers and reroutes were not involved. Holds (10 in 20 runs) and transfers (9) were too rare to matter.

Across all 20 development runs, the root cause was the hospitals' second ladder rung, "release ready-for-release patients, then accept":

| Seeds 101 to 120 | Full agents | No negotiation |
|---|---|---|
| Awards declined "capacity taken since my offer" | 657 | 229 |
| … where the award relied on a promised early release | 602 | 0 |
| … where that ready patient had already been released for another award | 211 | 0 |
| Early releases promised to non-critical loads | 966 | 0 |
| Early releases promised to loads with a critical | 167 | 0 |
| Arbiter rulings "release ready patients" | 42 | 172 |
| Escalations | 199 | 313 |

1. A hospital could promise the **same** ready patient in several open offers. Only the first award could use it; the others were declined.
2. Each decline cost a 5-minute retry and a restart from round 1, so the critical lost its early firm reservation and the ALS stood on scene.
3. About 85% of early releases went to non-critical loads. That spent the headroom the Arbiter's option 2 (release a ready patient) needs for criticals, so escalated criticals fell to least-bad.
4. Accept offers that later failed also delayed escalation, burning deadline slack.

Negotiation never delayed ALS dispatch directly (Dispatch plans units separately from admissions); cause C is a knock-on. Overall, full agents spent *less* ALS time waiting on scene (7,793 vs 9,913 ALS-minutes): the loss was concentrated in these criticals.

## The fixes (DESIGN updated in the same change)

| Fix | Rule | DESIGN |
|---|---|---|
| **F1** One promise per ready patient | A ready-for-release patient backs at most one open offer, until its award could arrive (3 minutes) or its round ends | 4.3 ladder rung 2; 3.11 "Same-minute capacity" |
| **F2** Early release only for criticals | Rung 2 applies only to a request that includes a critical; non-criticals use free capacity or wait | 4.3 ladder rung 2 |
| **F3** A declined award goes to the next offer | Award the next valid offer of the same round at once; if none, a critical with under 15 minutes of slack escalates (trigger `award_declined`); otherwise the decline counts as a round. No separate 5-minute retry | 5.2 step 4; 8 "Declined award"; 3.11 escalation triggers |

F3 is a protocol rule, so it applies to every agent variant, including the ablations. Two further proposals were not implemented: a fast path that skips negotiation for a critical when a hospital can accept now (F4), and holds yielding to a ready critical (F5, only 3 cases).

## Before and after

**Development seeds 101 to 120** (rules only, 12 incidents; `eval-005` before, `eval-006` after):

| | Before | After |
|---|---|---|
| Greedy baseline | 54.7% | 54.7% |
| Agents (full) | 68.5% | **82.3%** |
| Agents, no negotiation | 71.5% | 74.7% |
| Criticals on time only without negotiation | 29 | **7** |
| Criticals on time only with full agents | 22 | 22 |
| Awards declined "capacity taken" (full agents) | 657 | **235** |
| … relying on a promised release | 602 | 27 |
| Arbiter rulings "release ready patients" (full agents) | 42 | 148 |

**Evaluation seeds 1 to 20**, run once after the fixes, with no tuning afterwards (`eval-004` before, `eval-007` after):

| Policy | Criticals on time, before → after | Unserved criticals | W/T/L vs greedy (after) | Report → care, before → after | Deadlines missed | Victims delivered |
|---|---|---|---|---|---|---|
| Greedy baseline | 39.6% → 39.6% | 0.40 → 0.40 | – | 88.3 → 88.3 min | 18.7 → 18.7 | 26.4 → 26.4 |
| Agents (full) | 61.1% → **85.0%** | 0.05 → 0.20 | 19 / 1 / 0 | 69.9 → 82.8 min | 12.0 → 13.8 | 29.6 → 28.0 |
| No negotiation | 71.8% → 70.8% | 0 → 0 | 19 / 0 / 1 | 80.8 → 82.9 min | 15.5 → 15.8 | 27.1 → 27.7 |
| No Arbiter | 64.6% → **88.4%** | 0.15 → 0.15 | 19 / 1 / 0 | 69.0 → 80.1 min | 11.7 → 13.3 | 29.4 → 27.6 |
| Rule hospitals | 69.5% → 69.4% | 0 → 0 | 19 / 0 / 1 | 81.1 → 80.5 min | 14.9 → 15.2 | 27.8 → 27.6 |

What the fixes cost: with early releases kept for criticals, non-critical victims wait longer. For full agents, average report-to-care time rose by 13 minutes, missed deadlines by 1.8 per run, and victims delivered fell by 1.6 per run. They are still better than the baseline on all three.

What is left: the Arbiter now costs 3.5 points on criticals in rules-only mode (85.0% with it, 88.4% without). This was not investigated, to avoid tuning on the evaluation seeds; a next diagnosis would again use development seeds.

## Bug: hospital offers reached the LLM without their details

**Found** in the first live LLM run through the dashboard (seed 42, `runs/seed42-agents-010`) with `tools/diagnostics/llm_choices.py`. The hospital agent put each admission option's details (whom it accepts, conditions, declines, releases) under the payload key `options`; the shared `pick()` then replaced that key with the list of option ids. So for hospital admission offers the model saw only names such as `accept_now` and `release_then_accept`. In 106 of 171 admission calls it chose differently from the rule, almost always "accept now", with reasons like "accepting now allows immediate treatment" for an option that, with no free capacity, accepts nobody. Dispatch awards and Arbiter rulings were not affected (their details travel as `plans`, `offers` and `candidates`).

**Fix:** the details travel as `option_details`, the hospital prompt says what they contain, and `pick()` refuses a payload that uses the reserved `options` key. Tests check that the admission input carries every option's details. Rules-only results are unchanged (rules never read the payload); live runs recorded before the fix no longer replay against the new code.

**Seed 42, 12 incidents, `gpt-4o-mini-2024-07-18`, both runs through the live dashboard path, both replayed byte-identically:**

| Run | Criticals on time | Unserved | Deadlines missed | Delivered | LLM calls | Admission calls differing from the rule | Decided by rule / LLM / fallback |
|---|---|---|---|---|---|---|---|
| Greedy | 8/15 | 0 | 17 | 32 | – | – | – |
| Agents, rules only | 11/15 | 0 | 13 | 37 | – | – | 100 / 0 / 0% |
| Live LLM, before the fix (`seed42-agents-010`) | 11/15 | 0 | 13 | 31 | 243 | 106 of 171 | 72.1 / 27.8 / 0.1% |
| Live LLM, after the fix (`seed42-agents-012`) | 9/15 | 2 | 17 | 30 | 155 | 55 of 74 | 76.5 / 22.5 / 1.1% |

With the details visible, the model agrees with the rule on every "release then accept" (17 of 17). All 55 remaining differences are one pattern: the rule's default "accept now" accepts nobody (no free capacity), and the model offers "after transfer" instead, a conditional acceptance that the DESIGN 4.3 ladder places before a decline. In the counterfactual replays some of those choices cost a critical on this seed, and some Dispatch choices (a "spare specialist" award, extra units from Incident Assessment) did too. One live run on one seed is too noisy to judge the LLM: the live evaluation (3 repeats per seed, DESIGN 11.2) is the real test.

## Reproduce

```bash
python -m tools.diagnostics.negotiation_gap --seeds 101-120 --out /tmp/gap   # the tables above (current code)
python -m cli eval --seeds 101-120 --policies agents agents-nonego             # development check
python -m cli eval --seeds 1-20                                                # the final evaluation
python -m tools.diagnostics.llm_choices runs/seed42-agents-012                  # LLM vs rule, with counterfactuals
```
