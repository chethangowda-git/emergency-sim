# Emergency response simulator

A multi-agent emergency response demo. A **deterministic, seeded simulator** runs on a **real city road network** (central Boston). A **greedy rule-based baseline** dispatches ambulances, fire and rescue teams, and hospital admissions. LLM agents negotiate over the same resources, and the dashboard plays both side by side. Everything is run and watched from a **Streamlit dashboard**.

The design is in [`docs/DESIGN.md`](docs/DESIGN.md) (the source of truth). All five build phases are done; the live LLM runs are still to come (see [Results](#results)).

## Results

Seeds 1 to 20, the same incidents, walk-ins and resources for every policy, **rules only (no LLM)**:

> The multi-agent system got **70.8%** of critical victims to care on time versus **38.3%** for the greedy baseline, winning on **18 of 20** seeds (1 tie, 1 loss).

| Policy | Criticals on time | Seeds won (W/T/L) | Report → care | Response time | Deadlines missed | Victims delivered |
|---|---|---|---|---|---|---|
| Greedy baseline | 38.3% ± 18.4 | – | 87.4 min | 46.5 min | 18.7 | 26.3 |
| **Agents** | **70.8% ± 23.2** | **18 / 1 / 1** | **70.3 min** | 29.6 min | **11.8** | **29.4** |
| Agents, no negotiation | 69.1% ± 22.8 | 18 / 1 / 1 | 81.4 min | 33.8 min | 16.0 | 26.9 |
| Agents, no Arbiter | 70.0% ± 22.7 | 18 / 1 / 1 | 70.1 min | 29.4 min | 12.0 | 29.4 |
| Agents, rule hospitals | 67.3% ± 20.5 | 17 / 2 / 1 | 83.0 min | 33.9 min | 14.8 | 27.7 |

Values are means over the 20 seeds; ± is the standard deviation across seeds. A seed counts as won when the policy gets strictly more criticals to care on time than the baseline.

**Against the baseline**, the full agent system improves criticals on time by 85%, cuts average report-to-care time by 20% (17 minutes), response time by 36% and missed deadlines by 37%, and delivers 12% more victims. Hospital overload minutes drop by 12%. Two things get worse: ER load balance (busiest minus quietest hospital peak, 60 vs 56 points) and rejected actions (about 50 per run, against none for the baseline; see below).

**What the ablations show:**

- **Most of the gain on criticals comes from coordination itself, not from negotiation.** With negotiation switched off, the agents still get 69.1% of criticals on time. Incident Assessment sizing the response, Dispatch planning units by urgency, and admissions by earliest capable arrival already double the baseline.
- **Negotiation pays off in speed and throughput.** Compared with no negotiation, the full system reaches care 11 minutes sooner, misses 4 fewer deadlines per run and delivers 2.5 more victims. That variant switches off counters, holds, transfers and mutual aid together (DESIGN 11.3), so this measures them as a group.
- **The Arbiter adds little in rules-only mode.** Without it, escalations fall back to the least-bad option, which is usually what the Arbiter would rule anyway (70.0% vs 70.8%).
- **Hospital agents earn their place.** Replacing them with "accept if real capacity is free" costs 3.5 points on criticals, 13 minutes to care and 3 more missed deadlines per run.

**Where the agents don't win:** seed 2 is a tie (18.8% each), and the agents lose seed 9 (36.4% vs 54.5%). Results vary a lot between scenarios (standard deviation about 20 points), so the per-seed chart on the Evaluation page is worth a look alongside the means.

**Caveats:**

- **No LLM was involved.** Every agent decision in these numbers was made by the rules (fast path and defaults). DESIGN 11.2 asks for the agents to run 3 times per seed with a live, recorded LLM. That evaluation still needs a pinned OpenAI model and an API key, and its results may differ.
- Seeds 1 to 20 were used to find bugs while the agents were built (Phase 3). Tuning stopped there so the evaluation stays fair, but the seeds were not completely unseen.
- About 50 agent actions per run are rejected by the simulator, mostly when two agents claim the same hospital slot in the same minute. The world rejects them safely and the agents renegotiate, but the cost shows up as extra negotiation time.
- The world is a simplified one (section [Assumptions](#assumptions-and-simplifications)): no traffic, no deterioration, approximate times and fictional hospital capacities.

To reproduce: open the **Evaluation** page and press **Run evaluation** (about 30 seconds), or run `python -m cli eval --seeds 1-20`. Rules-only runs are deterministic, so you get exactly these numbers.

## Launch

You only need Python 3.11 or newer installed. The first launch creates a virtual environment and installs packages, which takes a few minutes.

| System | How |
|---|---|
| macOS | Double-click `launch.command` (the first time, right-click → Open if macOS warns about it) |
| Windows | Double-click `launch.bat` |
| Linux | Run `./launch.sh` |

The dashboard opens at <http://localhost:8501>. Then:

1. Pick a **seed** (42 is the reference scenario) and press **Run simulation**. A progress bar shows the 360 simulated minutes, then playback starts.
2. **New scenario** runs a random seed.
3. **Past runs** reopens any saved run instantly.
4. **Play / Pause** and **Speed** (1x, 5x, 20x simulated minutes per second) are in the sidebar. The **Minute** slider above the map jumps to any minute.

The map shows ALS (purple) and BLS (blue) ambulances, fire teams (grey), rescue teams (brown) and lent hospital vehicles (green) driving along real streets, with their remaining route dashed. Incidents appear when reported: red if they have critical victims, amber if not, grey once everyone is at a hospital. Hospitals show their ER use. The panels below (units, hospitals, live events, KPIs) follow the playback minute.

## The area

Central Boston, MA: Back Bay, South End, Fenway, Longwood, Downtown, North End and Charlestown. That's a box of about 5 × 6 km (`config/area.json`). The drivable road network, real hospital sites, and ambulance and fire stations were downloaded once from OpenStreetMap by `scripts/fetch_roads.py` and committed under `data/roads/`. Simulations never download anything.

Ambulance and fire station locations could not be downloaded (the OpenStreetMap server was overloaded). For now, ALS ambulances are based at the hospitals, and BLS ambulances, fire teams and rescue teams at seeded random street corners. Running `python scripts/fetch_roads.py` again when OpenStreetMap responds adds real stations, which changes every scenario's unit bases.

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

For an agent run, the bar under the map switches between three views, all synced with the playback minute:

- **Resources & events:** units, hospitals and the live events feed.
- **Agent activity:** a card per agent (Incident Assessment, Dispatch, each hospital, the Arbiter). Each card shows the agent's status, its pending requests, its recent messages and its last decision, marked as made by a rule, the LLM or the fallback rule. Hospital cards also show mood, ER use and holds.
- **Negotiation history:** totals for threads, rounds, counters, deals without the Arbiter, escalations and Arbiter decisions. Below them is a table of every negotiation thread so far, with protocol, incident, victims, rounds, counters, declines and where it stands. You can filter by protocol and incident. Pick a thread, or let the view follow the latest one, to read its messages and the decisions behind them in order.

**Compare with baseline** (sidebar, for agent runs) puts the greedy baseline's run of the same seed and scenario next to the agent run. You get two maps on one time slider, a baseline vs agents table of the KPIs at that minute, and both runs' resources and events. If no baseline run exists yet, a button runs it (it takes about a second).

## Evaluation (Phase 5)

Open the **Evaluation** page (top of the sidebar) and press **Run evaluation**. It runs seeds 1 to 20 with the greedy baseline, the agents and the three ablations (DESIGN 11.2 and 11.3). With rules only, that's 100 runs in about half a minute. The page shows:

- the headline result as one sentence, and the main numbers against the baseline;
- criticals on time for each policy (mean over seeds, with the spread across seeds);
- wins, ties and losses per seed against the baseline;
- any variant against the baseline on every metric, with a per-seed chart;
- **Open in playback** for any seed and policy of the evaluation.

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
