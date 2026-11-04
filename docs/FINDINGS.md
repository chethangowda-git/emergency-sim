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

## Live LLM evaluation: the LLM lowers the results

**Setup.** Full agents with `gpt-4o-mini-2024-07-18` (temperature 0, hospital offers with their details), seeds 1 to 20 × 3 repeats, 12 incidents, real fire-station bases, 4 runs in parallel (`eval-008`). No agent logic was changed before or after. The safety rules of DESIGN 12.7 held: 60 runs, 0 API errors, 0 failed runs, so no rule fallback caused by an API error is in these numbers. 8,763 calls, 6.83 M input and 0.43 M output tokens, about $1.28, 56 minutes. The whole evaluation replays exactly (`eval-009`, identical `results.jsonl` and summary), and 3 randomly chosen runs rebuilt twice gave identical files.

| | Greedy | Agents, rules only (`eval-007`) | Agents, live LLM (`eval-008`) |
|---|---|---|---|
| Criticals on time | 39.6% | **85.0%** | 68.6% (repeat spread 3.8 points) |
| Unserved criticals | 0.40 | **0.20** | 1.45 |
| W / T / L vs greedy | – | 19 / 1 / 0 | 16 / 2 / 2 |
| W / T / L vs rules-only | – | – | 3 / 1 / 16 |
| Report → care | 88.3 min | 82.8 min | **78.3 min** |
| Response time | 48.3 min | 33.2 min | **25.9 min** |
| Deadlines missed | 18.7 | **13.8** | 16.8 |
| Victims delivered | 26.4 | **28.0** | 23.5 |
| Escalations / Arbiter decisions per run | – | 13.6 / 39.3 | 6.1 / 17.2 |
| Decided by rule / LLM / fallback | – | 100 / 0 / 0% | 74.2 / 25.1 / 0.7% |

The live agents still beat greedy clearly, and they reach care and respond faster. But they get 16.4 points fewer criticals to care on time than the same agents with rules only, and lose to them on 16 of 20 seeds. The spread across repeats is small (3.8 points), so this is not noise from a few unlucky runs.

**What the model chose** (all 60 runs):

| Decision | Model's choice |
|---|---|
| Hospital admission offer (4,288) | "after transfer" 87%, "release then accept" 12%, "accept now" 1% |
| Dispatch unit plan with a ranking (2,597) | a different ranking from the rule 61% |
| Incident assessment (720) | extra units 61% |
| Dispatch award (988) | earliest 71%, spare the specialist 28%, one hospital 1% |

The dominant pattern is the hospitals' conditional "after my transfer" offer, which the DESIGN 4.3 ladder ranks above a decline but which the rule picks only when nothing else fits. With it, a load waits for a transfer that may not happen in time, and offers that are not declines also delay escalation (escalations fell from 13.6 to 6.1 per run). That is a hypothesis consistent with the numbers, not a diagnosis: the counterfactual tool (`tools/diagnostics/llm_choices.py`) on development-seed live runs would test it, at about $0.03 per run.

**Status:** the project's headline results are the rules-only ones. The LLM, as currently prompted and with these options, makes the agents worse on the primary metric. Nothing was tuned on seeds 1 to 20 after seeing this.

## Why the LLM lowers the results, and the guard (development seeds 101 to 120)

The live evaluation above is kept as recorded: tag `v1-live-eval` reproduces `eval-008` exactly. Everything in this section ran on the development seeds only (`runs/dev-evals/`, one live repeat per seed); seeds 1 to 20 were not touched.

**The same gap on development seeds.** Rules-only agents got 159 of 204 criticals to care on time (82.3%), live LLM agents 124 (63.8%): 44 lost against rules only, 9 gained (`eval-001`, `eval-002`; 20 live runs, 0 failed, $0.41).

**Which LLM choices cost criticals.** `tools/diagnostics/llm_losses.py` replays each live run with one kind of LLM choice turned back to the rule, and also adds only that kind to rules only. A diverged replay takes the model's answer to the most similar recorded question, so these are estimates; with nothing reverted the replay reproduces the live run, and with everything reverted the rules-only run (checked on all 20 seeds).

| LLM choice | Lost criticals recovered by removing it alone (of 44) | Criticals on time with it removed (live: 124) | Rules only plus only this choice (rules: 159) |
|---|---|---|---|
| Extra units (Incident Assessment) | 28 | 147 | 154 |
| "After transfer" offers (hospitals) | 26 | 146 | 156 |
| Award plan, e.g. "spare the specialist" (Dispatch) | 17 | 134 | 162 |
| Re-ranking incidents (Dispatch) | 0 | 119 | 156 |
| Shortfall plans, other admission, transfer and staff answers | 0 | 124 | 159 |

Each choice alone costs only 3 to 5 criticals; together they cost 35. 28 of the 44 lost criticals were never carried by any ALS.

**The mechanism: two coordination bugs that only the LLM's choices reached** (rules never make an "after transfer" offer):

- **B1, conditional awards overwritten.** A hospital kept one pending conditional award; a second award to the same hospital overwrote the first, which then never got an answer, with no timeout. 958 conditional awards, only 26 transfers started, 92 awards overwritten (counted with a one-off hook), 84 loads still "awarded" at the end of their runs (18,689 load-minutes stuck "awarded" for 15+ minutes; rules only: 0). An ALS in a stuck load stays on scene, and other ALS units wait there too because a critical they could carry is still waiting. With 3 ALS in the fleet and the model asking for 100 extra ALS over 20 runs, one stuck scene could hold the whole ALS fleet (seed 119: all 3 at the school bus fire from minute 120 to the end, 6 criticals never picked up).
- **B2, split loads aboard one unit.** Splitting a load whose victims were already aboard left the other victim at a hospital door with no reservation, while the load negotiated for a victim already delivered (1,751 unit-minutes with the LLM, 459 with rules only).
- **Delayed escalation** follows from B1: a conditional offer counts as in time, so a critical relying on it does not escalate (111 escalations with the LLM, 293 with rules only).

**Fixes** (DESIGN 5.2 and 8): each conditional award is tracked on its own negotiation and confirmed when its transfer starts, or declined if the transfer cannot start or has not started one round after the condition's time. The condition's time is now when a transfer can actually start, 11 minutes after the offer (6 minutes of messages plus the 5-minute preparation; it was 5). Victims aboard one unit are never split, and a renegotiation covers exactly the victims aboard. B2 also changes rules-only runs: 158 instead of 159 criticals on time on the development seeds (81.8%, `eval-003`).

**Guard** (`llm_guard`, on by default; DESIGN 4.1 to 4.4 and 12.8). G1: a hospital offers "after transfer" only if a transfer could start now, and not again in a negotiation where one did not happen; Dispatch counts a conditional offer as in time only with 15 minutes of slack, otherwise the patient escalates. G2: at most 1 extra ALS per incident; extra units are filled last, from free units, and never cause a shortfall plan.

**Free estimates** (recorded `eval-002` calls replayed under the new code; no API calls):

| Development seeds 101 to 120 | Criticals on time | Mean % |
|---|---|---|
| Rules only (new code) | 158 of 204 | 81.8 |
| Live as recorded (old code) | 124 | 63.8 |
| B1 + B2 | 140 | 72.9 |
| B1 + B2 + G1 + G2 | 160 | 82.4 |
| ... + G3 (re-rank only within a 15-minute band) | 159 | 82.1 |
| ... + G4 (non-earliest award only if criticals keep their earliest arrival) | 160 | 82.4 |

G3 and G4 do not help, so they are not implemented.

**Live check, guard on** (`eval-004`; 20 live runs, 0 failed, 2,705 calls, $0.39):

| Development seeds 101 to 120 | Greedy | Rules only (new code) | Live LLM, before (old code) | Live LLM, guard on |
|---|---|---|---|---|
| Criticals on time | 54.7% | **81.8%** | 63.8% | **80.7%** |
| Unserved criticals per run | | 0.10 | 2.05 | 0.00 |
| Wins / ties / losses vs greedy | | 14 / 5 / 1 | 12 / 5 / 3 | 13 / 6 / 1 |
| Wins / ties / losses vs rules only | | | | 2 / 13 / 5 |
| Victims delivered | | 29.1 | 24.9 | 28.9 |
| Escalations per run | | 14.8 | 5.5 | 15.6 |
| Decided by rule / LLM / fallback | | 100 / 0 / 0% | | 88.2 / 11.2 / 0.6% |

With the fixes and the guard, live LLM agents are about level with rules only (1.1 points lower, 13 of 20 seeds tied) instead of 18 points lower. The LLM no longer hurts, but it does not yet help either. Replays of guarded live runs reproduce their metrics and calls exactly.

**B3, fixed afterwards.** A hospital vehicle doing an inter-hospital transfer whose reservation was broken by a walk-in waited at the receiving hospital's door with no renegotiation (transfers had no load); seen in 3 development seeds, for 200 to 293 minutes each. Now the vehicle renegotiates from the door like an ambulance, excluding the hospital it waits at, and the onward trip is still a transfer (DESIGN 5.5, 5.7). The agents' view now includes walk-in patients aboard a vehicle, so the patient can be offered. Free checks on the development seeds: rules only byte-identical to before (`eval-005` = `eval-003`); the guarded estimate unchanged (160 of 204); the estimate replay of the guarded live run gives its recorded 155 of 204.

## Final evaluation (seeds 1 to 20, tag `final-eval`)

Run once on the code with B1 to B3 and the guard, no tuning afterwards: `eval-010` (greedy and rules-only agents), `eval-011` (live LLM, guard on, 3 repeats), `eval-012` (live LLM, guard off, 1 repeat).

| Seeds 1 to 20 | Greedy | Agents, rules only | Agents, LLM + guard | Agents, LLM, no guard |
|---|---|---|---|---|
| Criticals on time | 39.6% ± 17.1 | 84.6% ± 21.3 | **85.7%** ± 19.0 | 79.2% ± 20.2 |
| Spread across repeats | – | – | 1.6 points | – |
| Unserved criticals per run | 0.40 | 0.20 | 0.08 | 0.00 |
| W / T / L vs greedy | – | 19 / 1 / 0 | 20 / 0 / 0 | 20 / 0 / 0 |
| W / T / L vs rules only | – | – | 8 / 8 / 4 | 5 / 6 / 9 |
| Report → care | 88.3 min | 83.1 min | 81.3 min | 84.2 min |
| Victims delivered | 26.4 | 27.5 | 27.8 | 27.4 |
| Decisions by rule / LLM / fallback | – | 100 / 0 / 0% | 88.3 / 11.1 / 0.5% | 72.0 / 27.7 / 0.4% |
| Live runs (failed) | – | – | 60 (0) | 20 (0) |
| LLM calls, tokens, cost | – | – | 8,155; 6.24 M in, 0.39 M out; $1.17 | 6,264; 3.92 M in, 0.23 M out; $0.73 |

Against v1 (`eval-008`, 68.6%), the fixes alone (no guard) bring the live LLM to 79.2%, and the guard to 85.7%, slightly ahead of rules only (+1.0 point, 8 / 8 / 4). The development-seed result predicted roughly this (80.7% guarded vs 81.8% rules only there); on these seeds the guarded LLM came out a little ahead rather than a little behind, a difference within the variation between seeds. The fixes moved rules only from 85.0% to 84.6%.

Three randomly chosen guarded runs (`seed18-agents-r2`, `seed9-agents-r2`, `seed15-agents-r3`) were rebuilt from their recorded calls: metrics and scenario match the evaluated run, every LLM call matches the recording, and two rebuilds are byte-identical on all 8 files. During the unguarded evaluation, four API calls hung for about 30 minutes (the client's timeout and retries); they then completed, and no run had an API error.

## Reproduce

```bash
python -m tools.diagnostics.negotiation_gap --seeds 101-120 --out /tmp/gap   # the tables above (current code)
python -m cli eval --seeds 101-120 --policies agents agents-nonego             # development check
python -m cli eval --seeds 1-20                                                # the final evaluation
python -m tools.diagnostics.llm_choices runs/seed42-agents-012                  # LLM vs rule, with counterfactuals
python -m cli eval --seeds 1-20 --policies agents --llm replay --from eval-008 --repeats 3   # the live evaluation, replayed
python -m tools.diagnostics.live_vs_rules --live eval-008 --rules eval-007        # the v1 live vs rules-only table (tag v1-live-eval)
python -m tools.diagnostics.final_table --rules eval-010 --guarded eval-011 --unguarded eval-012   # the final table
python -m tools.diagnostics.llm_losses --live eval-002 --estimate --evals-dir runs/dev-evals         # fixes and guards
# the loss attribution and stuck counts above, on the code eval-002 was run with:
git worktree add /tmp/v1 v1-live-eval && cp tools/diagnostics/llm_losses.py /tmp/v1/tools/diagnostics/
mkdir -p /tmp/v1/runs && ln -s "$PWD/runs/dev-evals" /tmp/v1/runs/dev-evals
(cd /tmp/v1 && python -m tools.diagnostics.llm_losses --live eval-002 --rules eval-001)
```

Only the summaries of `runs/dev-evals/` are committed (settings, results, summary); the run folders with the recorded LLM calls stay local, so the two `llm_losses` commands need them.
