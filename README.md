# Emergency response simulator

A multi-agent emergency response demo. A **deterministic, seeded simulator** runs on a **real city road network** (central Boston). A **greedy rule-based baseline** dispatches ambulances, fire and rescue teams, and hospital admissions. LLM agents negotiate over the same resources, and the dashboard plays both side by side. Everything is run and watched from a **Streamlit dashboard**.

The design is in [`docs/DESIGN.md`](docs/DESIGN.md) (the source of truth). All five build phases are done; the live LLM runs are still to come (see [Results](#results)).

## Results

**Rules only (no LLM) · real fire-station bases · 12 incidents per scenario · seeds 1 to 20** (`runs/evals/eval-007`). Every policy gets the same incidents, walk-ins and resources. This is the evaluation after the negotiation fixes in [docs/FINDINGS.md](docs/FINDINGS.md), run once with no tuning afterwards:

> The multi-agent system got **85.0%** of critical victims to care on time versus **39.6%** for the greedy baseline, winning on **19 of 20** seeds (1 tie, no losses).

| Policy | Criticals on time | Unserved criticals | Seeds won (W/T/L) | Report → care | Response time | Deadlines missed | Victims delivered |
|---|---|---|---|---|---|---|---|
| Greedy baseline | 39.6% ± 17.1 | 0.40 | – | 88.3 min | 48.3 min | 18.7 | 26.4 |
| **Agents** | **85.0% ± 21.4** | 0.20 | **19 / 1 / 0** | 82.8 min | 33.2 min | 13.8 | **28.0** |
| Agents, no negotiation | 70.8% ± 20.2 | 0 | 19 / 0 / 1 | 82.9 min | 36.8 min | 15.8 | 27.7 |
| Agents, no Arbiter | 88.4% ± 19.7 | 0.15 | 19 / 1 / 0 | **80.1 min** | **29.6 min** | **13.3** | 27.6 |
| Agents, rule hospitals | 69.4% ± 21.1 | 0 | 19 / 0 / 1 | 80.5 min | 33.6 min | 15.2 | 27.6 |

Values are means over the 20 seeds; ± is the standard deviation across seeds. A seed counts as won when the policy gets strictly more criticals to care on time than the baseline.

**Against the baseline**, the full agent system more than doubles criticals on time (+45.4 points), cuts response time by 31% (15 minutes), report-to-care time by 6% and missed deadlines by 26%, and delivers 6% more victims. Hospital overload minutes drop by 9%. ER load balance is slightly worse (55 vs 53 points), and about 36 agent actions per run are rejected, against none for the baseline (see below).

**Gain breakdown on criticals on time:** greedy 39.6% → no negotiation 70.8% (+31.2) → rule hospitals 69.4% (−1.4) → no Arbiter 88.4% (+19.0) → full agents 85.0% (−3.5). The ablations are not strictly nested, so each step is the difference between two variants:

- **Coordination itself** (Incident Assessment sizing the response, Dispatch planning units by urgency, admissions by earliest capable arrival) gives the first +31 points.
- **Hospital agents with negotiation** give the next +19: holds, conditional offers, early release of ready patients for criticals, transfers, staff loans and mutual aid together. Without them (no negotiation, or rule hospitals) the agents stay near 70%.
- **The Arbiter costs 3.5 points** in rules-only mode (85.0% with it, 88.4% without). This was not investigated, to avoid tuning on the evaluation seeds.

**Where the agents don't win:** the full system ties seed 2 (18.8% each), the hardest scenario. Results vary a lot between scenarios (standard deviation about 20 points), so the per-seed chart on the Evaluation page is worth a look alongside the means.

**The fixes, and what they cost** ([docs/FINDINGS.md](docs/FINDINGS.md)): before them, the full system got only 61.1% (it was the weakest agent variant). Diagnosed on development seeds 101 to 120, the cause was hospitals promising the same ready-for-release patient in several offers (and mostly to non-critical loads), so awards were declined and criticals lost their early reservation. Each ready patient is now promised once, early release is kept for loads with a critical, and a declined award goes straight to the next offer. Keeping early releases for criticals makes non-criticals wait longer: report-to-care time is 13 minutes longer than before the fixes, but still better than the baseline.

**Seed 42** (the reference scenario, not an evaluation seed), rules only, 12 incidents: agents get 11 of 15 criticals on time (73.3%) against 8 of 15 (53.3%) for greedy, miss 13 deadlines instead of 17 and deliver 37 victims instead of 32. Their average report-to-care time is longer (92.8 vs 82.0 min) because they also deliver the victims greedy never reaches.

### Live LLM evaluation

**Full agents with `gpt-4o-mini-2024-07-18` · seeds 1 to 20 × 3 repeats · 12 incidents · real fire-station bases** (`runs/evals/eval-008`, recorded; its replay `eval-009` reproduces `results.jsonl` byte for byte). No agent logic was changed before or after the run.

> **The LLM made the agents worse.** Live LLM agents got **68.6%** of criticals to care on time, against **85.0%** for the same agents with rules only and **39.6%** for greedy. They still beat greedy on 16 of 20 seeds, but lost to rules-only agents on 16 of 20.

| | Greedy | Agents, rules only | Agents, live LLM |
|---|---|---|---|
| Criticals on time (mean over seeds) | 39.6% ± 17.1 | **85.0%** ± 21.4 | 68.6% ± 19.3 |
| Spread across the 3 repeats (sd, per seed) | – | – | 3.8 points |
| Unserved criticals | 0.40 | **0.20** | 1.45 |
| W / T / L vs greedy | – | 19 / 1 / 0 | 16 / 2 / 2 |
| W / T / L vs rules-only agents | – | – | 3 / 1 / 16 |
| Report → care | 88.3 min | 82.8 min | **78.3 min** |
| Response time | 48.3 min | 33.2 min | **25.9 min** |
| Deadlines missed | 18.7 | **13.8** | 16.8 |
| Victims delivered | 26.4 | **28.0** | 23.5 |
| Decisions by rule / LLM / fallback | – | 100 / 0 / 0% | 74.2 / 25.1 / 0.7% |

60 live runs, 0 failed, 0 API errors; 8,763 LLM calls, 6.83 M input and 0.43 M output tokens, about **$1.28** at $0.15 / $0.60 per million tokens; 56 minutes with 4 runs in parallel. The 0.7% fallbacks are model answers that did not fit the options (for example a Dispatch ranking that left out an incident); none came from API errors (DESIGN 12.7).

**What the model did differently** (all 60 runs): in 87% of hospital admission offers (3,733 of 4,288) it chose the conditional "after transfer" offer, which the rule takes only when nothing else fits; it ranked incidents differently from the rule in 61% of Dispatch plans; it asked for extra units in 61% of incident assessments; and it chose "spare the specialist hospital" in 28% of awards. The live agents escalated to the Arbiter less than half as often (6.1 vs 13.6 escalations per run), delivered 4.5 fewer victims and left more criticals unserved. They respond faster, because extra units are sent. Why the conditional offers cost criticals has not been diagnosed yet; that work belongs on development seeds, not on these. See [docs/FINDINGS.md](docs/FINDINGS.md).

**Since then** (development seeds 101 to 120 only; [docs/FINDINGS.md](docs/FINDINGS.md)): the loss came mostly from two coordination bugs that only the LLM's choices reached (conditional awards overwritten at a hospital and left unanswered, and split loads aboard one unit), made worse by extra ALS requests on a 3-ALS fleet. Both are fixed, and a rule guard (`llm_guard` in `config/llm.json`, on by default) now checks "after transfer" offers and extra units. On the development seeds, live LLM agents went from 63.8% to 80.7% of criticals on time, against 81.8% for rules only. The seed 1 to 20 results above predate these changes and are reproducible at tag `v1-live-eval`; they have not been rerun.

**Caveats:**

- The tables above the live section were made by the rules only (fast path and defaults); the live section shows the same agents with the LLM making the judgment calls. The headline numbers for this project are the rules-only ones; the LLM, as currently prompted, lowers them.
- Seeds 1 to 20 were used to find bugs while the agents were built (Phase 3). The negotiation fixes were diagnosed and checked only on seeds 101 to 120, then evaluated once on seeds 1 to 20.
- About 36 agent actions per run are rejected by the simulator, mostly when two agents claim the same hospital slot in the same minute. The world rejects them safely and the agents renegotiate.
- The world is a simplified one (section [Assumptions](#assumptions-and-simplifications)): no traffic, no deterioration, approximate times and fictional hospital capacities.

To reproduce: open the **Evaluation** page and press **Run evaluation** (about 30 seconds), or run `python -m cli eval --seeds 1-20`. Rules-only runs are deterministic, so you get exactly these numbers. The live evaluation replays exactly without a key: `python -m cli eval --seeds 1-20 --policies agents --llm replay --from eval-008 --repeats 3`.

## Launch

You only need Python 3.11 or newer installed. The first launch creates a virtual environment and installs packages, which takes a few minutes.

| System | How |
|---|---|
| macOS | Double-click `launch.command` (the first time, right-click → Open if macOS warns about it) |
| Windows | Double-click `launch.bat` |
| Linux | Run `./launch.sh` |

The dashboard opens at <http://localhost:8501>. Then:

1. Pick a **seed** (42 is the reference scenario), the number of **incidents** (4 by default; one is always the surge) and the **policy** (greedy baseline or agents), then press **Run simulation**. The run happens live: the simulation advances while you watch, and it is saved when it reaches minute 359.
2. **⏸ Pause / ▶ Resume** stops and restarts the simulation itself, so you can read the resources, events and agent messages at that minute. **⏭ Step 1 min** advances one minute and stays paused.
3. **Speed:** 1x plays the whole 360-minute run in about 50 seconds; 0.5x, 2x and 4x are slower or faster. Speed and pauses never change the outcome.
4. **🚨 Spawn incident** pauses the run and shows the street intersections as small red dots. Click one, choose what happened and how many victims (or let the seed decide), and press **Place incident**. It is reported at the next minute, the run carries on, and it is saved with the run, so replays include it.
5. **🎲 Restart with new seed** picks a random seed and waits at minute 0; press Resume to start.
6. **Past runs** reopens a saved run and replays it from minute 0 with the same clock controls.

The map shows ALS (purple) and BLS (blue) ambulances, fire teams (grey), rescue teams (brown) and lent hospital vehicles (green) driving along real streets, with their remaining route dashed. Incidents appear when reported: red if they have critical victims, amber if not, grey once everyone is at a hospital. Hospitals show their ER use and **Walk-in patients (N)**, the people in the ER who came in on their own. The panels below (units, hospitals, live events, KPIs) follow the clock.

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

For agents, the sidebar also has an **LLM** choice: rules only (no key needed), live (needs the model and key described above; every call is recorded), or replay of a recorded run (exact, no key). Run folders for agents also contain `messages.jsonl` and `llm_calls.jsonl`. In the Live events feed, the **Agent messages** toggle shows the messages and each agent's decisions.

## Dashboard v2 (Phase 4)

For an agent run, the bar under the map switches between three views, all synced with the clock:

- **Resources & events:** units, hospitals and the live events feed.
- **Agent activity:** a card per agent (Incident Assessment, Dispatch, each hospital, the Arbiter). Each card shows the agent's status, its pending requests, its recent messages and its last decision, marked as made by a rule, the LLM or the fallback rule. Hospital cards also show mood, ER use and holds.
- **Negotiation history:** totals for threads, rounds, counters, deals without the Arbiter, escalations and Arbiter decisions. Below them is a table of every negotiation thread so far, with protocol, incident, victims, rounds, counters, declines and where it stands. You can filter by protocol and incident. Pick a thread, or let the view follow the latest one, to read its messages and the decisions behind them in order.

**Compare with baseline** (sidebar, for agent runs) puts the greedy baseline's run of the same seed and scenario next to the agent run once it is saved. You get two maps on one clock, a baseline vs agents table of the KPIs at that minute, and both runs' resources and events. If no baseline run exists yet, a button runs it (it takes about a second), with the same placed incidents.

## Evaluation (Phase 5)

Open the **Evaluation** page (top of the sidebar) and press **Run evaluation**. It runs seeds 1 to 20 with the greedy baseline, the agents and the three ablations (DESIGN 11.2 and 11.3). With rules only, that's 100 runs in about half a minute. The page shows:

- the headline result as one sentence, and the main numbers against the baseline;
- criticals on time for each policy (mean over seeds, with the spread across seeds);
- wins, ties and losses per seed against the baseline;
- any variant against the baseline on every metric, with a per-seed chart;
- **Open in playback** for any seed and policy of the evaluation (the ablations are only reachable from here).

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
