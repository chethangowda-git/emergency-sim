# Emergency response simulator

A multi-agent emergency response demo. A **deterministic, seeded simulator** runs on a **real city road network** (central Boston). A **greedy rule-based baseline** dispatches ambulances, fire and rescue teams, and hospital admissions. LLM agents negotiate over the same resources, and the dashboard plays both side by side. Everything is run and watched from a **Streamlit dashboard**.

The design is in [`docs/DESIGN.md`](docs/DESIGN.md) (the source of truth). All five build phases are done; the live LLM runs are still to come (see [Results](#results)).

## Results

**Final evaluation · seeds 1 to 20 · 12 incidents per scenario · real fire-station bases** (tag `final-eval`; `runs/evals/eval-010` to `eval-013`, whose summaries are committed). Every policy gets the same incidents, walk-ins and resources. Run once on the current code, with no tuning afterwards. The LLM is `gpt-4o-mini-2024-07-18` at temperature 0.

> With the LLM and the rule guard, the multi-agent system got **85.7%** of critical victims to care on time versus **39.6%** for the greedy baseline, winning on **all 20 seeds**. The same agents with rules only got 84.6%, and with the LLM but no guard 79.2%.

| Seeds 1 to 20 | Greedy | Agents, rules only | **Agents, LLM + guard** | Agents, LLM, no guard |
|---|---|---|---|---|
| Criticals on time | 39.6% ± 17.1 | 84.6% ± 21.3 | **85.7%** ± 19.0 | 79.2% ± 20.2 |
| Spread across repeats (sd per seed) | – | – (deterministic) | 1.6 points (3 repeats) | – (1 repeat) |
| Unserved criticals per run | 0.40 | 0.20 | 0.08 | 0.00 |
| W / T / L vs greedy | – | 19 / 1 / 0 | **20 / 0 / 0** | 20 / 0 / 0 |
| W / T / L vs rules only | – | – | 8 / 8 / 4 | 5 / 6 / 9 |
| Report → care | 88.3 min | 83.1 min | **81.3 min** | 84.2 min |
| Victims delivered | 26.4 | 27.5 | **27.8** | 27.4 |
| Decisions by rule / LLM / fallback | – | 100 / 0 / 0% | 88.3 / 11.1 / 0.5% | 72.0 / 27.7 / 0.4% |
| Live runs (failed) | – | – | 60 (0) | 20 (0) |
| LLM calls, cost | – | – | 8,155, $1.17 | 6,264, $0.73 |

Values are means over the 20 seeds (repeats averaged per seed first); ± is the standard deviation across seeds. A seed counts as won when the policy gets strictly more criticals to care on time. Fallbacks are model answers that did not fit the options; none came from API errors (DESIGN 12.7). Three randomly chosen guarded runs were rebuilt from their recorded calls: metrics, scenario and every LLM call matched, and two rebuilds were byte-identical on all files.

**What each part contributes** (rules only unless marked, final code; the ablations are `eval-013`):

| Step | Criticals on time | Change |
|---|---|---|
| Greedy baseline | 39.6% | |
| + coordination (incident assessment, dispatch by urgency, admissions; no negotiation) | 70.8% | +31.2 pts |
| + hospital negotiation (full agents, rules only) | 84.6% | +13.8 pts |
| + LLM judgment with the guard | **85.7%** | +1.0 pts |
| *Alternatives, against full rules-only agents:* | | |
| Without the Arbiter | 88.8% | +4.1 pts |
| Rule hospitals instead of hospital agents | 70.8% | −13.8 pts |
| LLM judgment without the guard | 79.2% | −5.4 pts |

Coordination itself gives the largest gain, and hospital negotiation the next. The Arbiter still costs about 4 points in rules-only mode (88.8% without it); this was not investigated, to avoid tuning on the evaluation seeds. Rule hospitals and no negotiation now give the same result (both 70.8%).

**What it means.** Coordination by agents more than doubles criticals on time over the greedy baseline, with or without the LLM. With the guard, the LLM is now slightly ahead of rules only (+1.0 point; 8 wins, 8 ties, 4 losses), which is within the variation between seeds; it is no longer a cost. Without the guard it is 5.4 points behind rules only. The guard (`llm_guard` in `config/llm.json`, on by default) is the "LLM proposes, rules guard" step: the LLM still makes its choices, but rules check its conditional "after transfer" offers and its extra-unit requests (DESIGN 12.8).

### How we got here

1. **v1, rules only** (`eval-007`, tag `v1-live-eval`): agents 85.0% vs greedy 39.6%, after fixing how hospitals promise ready-for-release patients ([docs/FINDINGS.md](docs/FINDINGS.md)).
2. **v1, live LLM** (`eval-008`, 3 repeats, replayed exactly as `eval-009`): **68.6%**, 16.4 points below rules only and losing to it on 16 of 20 seeds. The model chose the conditional "after transfer" offer in 87% of hospital admissions and asked for extra units in 61% of incident assessments.
3. **Diagnosis on development seeds 101 to 120 only** (`runs/dev-evals/`, `tools/diagnostics/llm_losses.py`): the loss came mostly from coordination bugs that only the LLM's choices reached, because rules never make conditional offers:
   - **B1:** a hospital kept one pending conditional award, so a second one overwrote the first, which never got an answer; ambulances (and on a 3-ALS fleet, sometimes all of them) waited on scene for the rest of the run.
   - **B2:** a load already aboard one unit could be split across hospitals, leaving a victim at a door with no reservation.
   - **B3:** a hospital vehicle whose transfer patient arrived to no slot (a walk-in took it) never renegotiated.
   - Extra ALS requests made B1 worse, and conditional offers that counted as "in time" delayed escalation to the Arbiter.
4. **Fixes and guard:** B1 to B3 fixed (DESIGN 5.2, 5.5, 5.7, 8); guard G1 ("after transfer" only when a transfer could start, and 15 minutes of slack for a conditional offer) and G2 (at most 1 extra ALS, filled last, never a shortfall). Two further guards (re-ranking only within a deadline band, and limits on award plans) were estimated by replaying recorded calls and did not help, so they were not added. On development seeds the live LLM went from 63.8% to 80.7%, against 81.8% for rules only, before anything was run on seeds 1 to 20.
5. **Final evaluation:** the table above. The fixes also changed the rules-only agents slightly (85.0% → 84.6%).

**Caveats:**

- Seeds 1 to 20 were used to find bugs while the agents were built (Phase 3). Every diagnosis and fix since then was made on seeds 101 to 120, then evaluated once on seeds 1 to 20.
- Results vary a lot between scenarios (standard deviation about 20 points); seed 2 is the hardest (greedy and rules-only agents 18.8%, LLM agents 25.0% guarded and 31.3% unguarded; rules only ties greedy there). The per-seed chart on the Evaluation page is worth a look alongside the means.
- The unguarded evaluation ran 1 repeat to keep the cost down, so its per-seed numbers include model variation (the guarded spread across repeats is 1.6 points).
- The world is a simplified one (section [Assumptions](#assumptions-and-simplifications)): no traffic, no deterioration, approximate times and fictional hospital capacities.

To reproduce: `python -m cli eval --seeds 1-20 --policies greedy agents` and `--policies agents-nonego agents-noarb agents-rulehosp` (rules only, deterministic, about 30 seconds each; or **Run a new evaluation** on the Evaluation page), and replay the live evaluations without a key: `python -m cli eval --seeds 1-20 --policies greedy agents --llm replay --from eval-011 --repeats 3` (and `--from eval-012 --repeats 1`). `python -m tools.diagnostics.final_table --rules eval-010 --guarded eval-011 --unguarded eval-012` prints the table. The v1 results are reproducible at tag `v1-live-eval`.

## Launch

You only need Python 3.11 or newer installed. The first launch creates a virtual environment and installs packages, which takes a few minutes.

| System | How |
|---|---|
| macOS | Double-click `launch.command` (the first time, right-click → Open if macOS warns about it) |
| Windows | Double-click `launch.bat` |
| Linux | Run `./launch.sh` |

The dashboard opens at <http://localhost:8501>, with **Simulation** and **Evaluation** at the top. Each page fits one screen (1440 × 900): the controls are in one bar along the top, the map is on the left, and the numbers are on the right.

1. Pick a **seed** (42 is the reference scenario) and the **policy** (greedy baseline or agents; for agents, the **LLM**: rules only, live, or replay of a recorded run), then press **▶ Run**. The run happens live and is saved when it reaches minute 359. The line under the bar shows **Min X / 359** and a progress bar.
2. **⏸ Pause / ▶ Resume** stops and restarts the simulation itself, so you can read the panels at that minute. **⏭ 1 min** advances one minute and stays paused.
3. **Speed:** 1x is one simulated minute per second; 0.5x, 2x, 4x and 10x are slower or faster. **Skip quiet time** (on by default) fast-forwards while no unit is moving and no incident is open, and drops back to your speed the moment an incident is reported or a unit sets off. Speed, pauses and skipping never change the outcome.
4. **🚨 Spawn** pauses the run and shows the street intersections as small red dots. Click one, choose what happened and how many victims (or let the seed decide), and press **Place incident**. It is reported at the next minute, the run carries on, and it is saved with the run, so replays include it.
5. **⚙** holds the rarely used settings: incidents per scenario (4 by default; one is always the surge), **🎲 Restart with new seed** (waits at minute 0), **Past runs** (replayed from minute 0 with the same clock) and **Compare with baseline** (two maps side by side on one clock, with a KPI comparison table).

The map shows ALS (purple) and BLS (blue) ambulances, fire teams (grey), rescue teams (brown) and lent hospital vehicles (green) gliding along real streets, with a short fading trail behind them and their remaining route dashed. Incidents appear when reported: red if they have critical victims, amber if not, grey once everyone is at a hospital. Hospitals show their ER use and **Walk-in patients (N)**. On the right: four KPI cards (criticals on time, unserved criticals, average time to care, victims delivered), units free by type, one row per hospital with an ER bar, and a feed with **Events**, **Agents** and **Negotiations** tabs, all following the clock.

With live LLM calls, a minute lasts as long as the agents take to answer: the clock shows "agents deciding" meanwhile, and a whole run takes about 4 to 5 minutes whatever the speed.

## The area

Central Boston, MA: Back Bay, South End, Fenway, Longwood, Downtown, North End and Charlestown. That's a box of about 5 × 6 km (`config/area.json`). The drivable road network, real hospital sites and real fire stations were downloaded once from OpenStreetMap by `scripts/fetch_roads.py` and committed under `data/roads/`. Simulations never download anything.

Unit bases are real places. ALS ambulances are based at the three hospitals. BLS ambulances, fire teams and rescue teams are based at 16 real Boston, Cambridge and Somerville fire stations (Boston EMS shares stations with the fire department; the Marine Unit and Fire Department Headquarters are left out). The seed decides which station each unit uses, and no two units of the same type share one. The only ambulance station OpenStreetMap lists in the area belongs to a private company, so it isn't used: Dispatch controls the public fleet only. `python scripts/fetch_roads.py --stations-only` refreshes the stations without touching the roads or hospitals.

Three well-spread real hospital sites host the simulated hospitals. They have **fictional names and capacities** (Central Trauma Center, Northside General, Eastside Community) and say nothing about the real hospitals at those locations.

## Assumptions and simplifications

- Travel time is the fastest route by OpenStreetMap speed limits (defaults where missing), times an emergency factor of 0.85, rounded up to whole minutes. Traffic is ignored.
- 360-minute horizon. 12 incidents arrive in the first 120 minutes, including one surge event (bus fire or pileup, 6 to 10 victims). Victims are 25% critical, 35% serious, 40% minor.
- Deadlines to reach a capable hospital: critical 60, serious 120, minor 240 minutes. ER treatment takes 90, 60 or 30 minutes. Criticals become transferable after 20 minutes of treatment.
- Fires and collapses must be cleared by a fire or rescue team (10 minutes after it arrives) before victims can be moved. Ambulances spend 5 minutes on scene and 5 minutes on handover.
- Walk-ins arrive by private car (about 1 per hospital per 40 minutes) and always get an ER slot (EMTALA). They may take a slot reserved for an incoming ambulance, which finds out on arrival.
- Hospitals start empty. Staff work the whole run. Victims don't deteriorate. Every victim goes to a hospital.
- The full list, with the exact rules, is in DESIGN sections 3, 3.11 and 15.

## Agents (Phase 2)

Four agents live in `coordination/`: Incident Assessment, Dispatch, one Hospital agent per hospital, and the Arbiter. Rules compute every number and list the possible options. When only one option fits, the rule decides. Otherwise an LLM picks among the options and gives a one-line reason, and a rule takes over if the LLM's answer is missing or invalid. Each decision records whether a rule, the LLM or the fallback made it.

The agents can propose actions the baseline never uses: holds, early release, transfers between hospitals, lending a vehicle or a staff member, and redirecting or rerouting an ambulance. The simulator still checks every action against the same rules.

No live LLM run has been made yet. `config/llm.json` has `mode: off` and no model. Before the first live run, set `model` to a pinned OpenAI model ID and provide `OPENAI_API_KEY`. Every LLM call is then recorded, so a run can be replayed exactly without a key.

## Negotiation (Phase 3)

Choose **agents** in the Policy menu to run the multi-agent system. Agents talk through messages, and each round costs a simulated minute; an escalation to the Arbiter costs 3. The world keeps moving while they negotiate.

- **Admission:** for each ambulance load, Dispatch runs a Contract Net with the hospitals (call for proposals, offers, award). The awarded hospital places a firm reservation, then Dispatch sets the destination.
- **Other protocols:** mutual-aid vehicles, the swap, routine transfers between hospitals, staff loans, and rerouting an ambulance after a walk-in takes its reserved slot.
- **The Arbiter** steps in when no offer is in time, or when 3 rounds fail while a deadline is at risk. Its rulings are final.

Three comparison variants switch parts of the system off: **no negotiation**, **no Arbiter** and **rule hospitals**.

For agents, the control bar also has an **LLM** choice: rules only (no key needed), live (needs the model and key described above; every call is recorded), or replay of a recorded run (exact, no key). Run folders for agents also contain `messages.jsonl` and `llm_calls.jsonl`.

## Dashboard v2 (Phase 4)

For an agent run, the feed on the right has three tabs, all synced with the clock:

- **Events:** the world's events, newest first.
- **Agents:** a short block per agent (Incident Assessment, Dispatch, each hospital, the Arbiter): its status, pending requests, recent messages and last decision, marked as made by a rule, the LLM or the fallback rule. Hospitals also show mood, ER use and holds.
- **Negotiations:** totals for threads, rounds, counters, deals without the Arbiter and escalations, the table of negotiation threads so far (protocol, incident, victims, rounds, counters, declines and where it stands), and the most recently active thread's messages in order.

**Compare with baseline** (⚙, for saved agent runs) puts the greedy baseline's run of the same seed and scenario next to the agent run: two maps on one clock, and on the right a baseline vs agents table of the KPIs at that minute with units free and ER use for both. If no baseline run exists yet, a button runs it (it takes about a second), with the same placed incidents.

## Evaluation (Phase 5)

The **Evaluation** page opens on the final results: four headline numbers (greedy, rules-only agents, LLM + guard, LLM without the guard), the criticals-on-time chart next to a "What each part contributes" table, and a short "How we got here" timeline. Evaluations have readable names (`config/evaluations.json`); the picker opens any of them in full: the claim sentence, criticals on time per policy, wins, ties and losses, any variant against the baseline on every metric with a per-seed chart, and **Open in playback** for any seed and policy (the ablations are only reachable from here). Development, replayed and outdated evaluations appear with **Show all evaluations**.

To run a new one, open **Run a new evaluation** at the bottom: seeds 1 to 20 with the greedy baseline, the agents and the three ablations (DESIGN 11.2 and 11.3) take about half a minute with rules only.

Rules-only runs are deterministic, so each seed runs once. With a live LLM, the agents run 3 times per seed. Every call is recorded, and **Replay a recorded evaluation** reproduces it exactly without a key. Evaluations are saved in `runs/evals/<eval-id>/`. Each one holds `results.jsonl` (one row per run), `summary.json`, and a small folder per run (config, scenario, metrics, LLM calls) that is enough to rebuild the full run.

The current numbers and what they show are in [Results](#results).

## For developers

```bash
pytest -q                                    # all tests, no network
python -m cli run --policy greedy --seed 42  # headless run (also: --policy agents --llm off)
python -m cli eval --seeds 1-20              # headless evaluation under runs/evals/
python scripts/fetch_roads.py                # one-time road download (needs network)
streamlit run dashboard/app.py               # the dashboard
```

Each run is saved in `runs/<run-id>/` (for example `seed42-greedy-001`):

| File | Contents |
|---|---|
| `config.json` | Seed, policy, area and every simulator setting |
| `scenario.json` | The generated incidents, victims, walk-in schedule, hospitals and fleet |
| `events.jsonl` | Every simulator event and every applied or rejected action |
| `routes.json` | Every trip's road geometry and timing |
| `timeline.jsonl` | The world state at the end of each minute (what the dashboard plays back) |
| `victims.json` | Final record of every incident victim |
| `metrics.json` | The DESIGN section 11 metrics |

The same seed always produces byte-identical `events.jsonl`, `routes.json`, `timeline.jsonl` and `metrics.json`.
