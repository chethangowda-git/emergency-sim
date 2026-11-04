# CLAUDE.md

Instructions for Claude Code in this repository. Read this file fully at the start of every session.

## What this project is

A multi-agent emergency response system: a **deterministic, seeded simulator on a real city road network**, a **greedy baseline**, and later **LLM agents (OpenAI Agents SDK)** that negotiate over ambulances, hospital capacity and staff. Everything is run and watched from a **Streamlit dashboard** with buttons; the user never needs the command line.

**`docs/DESIGN.md` is the source of truth.** Read the sections a task references before coding. If code and DESIGN.md disagree, DESIGN.md wins. If DESIGN.md is ambiguous or seems wrong, stop and ask; don't invent rules.

The repo starts empty except for this file and `docs/DESIGN.md`. We build in 5 phases (DESIGN section 17). **Current phase: 5.** Area: central Boston (agreed; see DESIGN 3.11). Don't build anything from later phases.

## Golden rules

1. **Only the simulator changes state.** Policies (and later agents) return actions; `World` validates and applies them.
2. **One source of randomness:** the seeded `random.Random(seed)`. Never use module-level `random`, `time.time()`, set iteration order, or anything else non-deterministic in simulation code. Sort before iterating when order matters.
3. **The road network is a committed file.** Simulation code never downloads anything; only the one-time fetch script does.
4. **Rules compute numbers.** Travel times, capacities, deadlines and metrics are plain, tested Python functions.
5. **The baseline and future agents share the same world.** Never add a world rule that only one of them experiences.
6. **The dashboard only reads runs**: saved run folders and the minutes a live run publishes (DESIGN 13.3). It starts and steers runs (run, pause, resume, step, speed, place an incident) through `sim/live.py` and `run_store`, but never contains simulation logic.
7. **Don't name any local package `agents`** (it clashes with the OpenAI Agents SDK import). Future agents live in `coordination/`.

## Setup (for the user, done once)

The user double-clicks `launch.command` (macOS), `launch.bat` (Windows) or runs `./launch.sh` (Linux). The launcher creates `.venv` if missing, installs `requirements.txt`, and starts `streamlit run dashboard/app.py`. Create all three launchers in Phase 1 and keep them working.

`requirements.txt` (pin versions once it works): `osmnx`, `networkx`, `shapely`, `streamlit`, `pydeck`, `plotly`, `pandas`, `pytest`. Later phases add `openai-agents` and `pydantic`.

## Commands (for you, Claude Code)

```bash
pytest -q                                    # all tests, no network
python scripts/fetch_roads.py                # one-time road download (needs network)
python -m cli run --policy greedy --seed 42  # headless run, used by tests and debugging
python -m cli eval --seeds 1-20              # headless evaluation (DESIGN 11.2) under runs/evals/
streamlit run dashboard/app.py               # the dashboard
```

## Repository layout (Phase 1)

```
CLAUDE.md
docs/DESIGN.md
docs/FINDINGS.md     # negotiation diagnosis, fixes, before and after numbers
launch.command  launch.bat  launch.sh
requirements.txt  README.md  .gitignore
config/
  area.json            # chosen city area, bounding box or place name, emergency speed factor
scripts/
  fetch_roads.py       # one-time: download roads + hospital and station locations
data/roads/            # committed outputs of fetch_roads.py
  network.graphml
  places.json          # hospitals (3 chosen), public ambulance stations (none) and fire stations
sim/
  config.py            # SimConfig dataclass, all tunables from DESIGN section 3
  roads.py             # load network, fastest route, route geometry, position along route
  models.py            # Incident, Victim, Hospital, Staff, Unit, Reservation, ...
  rules.py             # small pure rules: care needs, loading, slots, real capacity
  actions.py           # SendToScene, SetDestination, AssignTeam
  generator.py         # seeded scenario: incidents, victims, walk-in schedule, fleet
  world.py             # World: step(), actions, event log
  hospital_flow.py     # patient lifecycle inside a hospital, walk-ins
  agent_actions.py     # agent-only actions (holds, transfers, loans, redirect, reroute)
  views.py             # World.snapshot() data and road-time helpers for policies
  metrics.py           # DESIGN section 11 (Phase 1 subset)
  runner.py            # run(seed, policy, progress_cb) -> run folder; prepare / Stepper / save shared with live runs
  live.py              # live runs (DESIGN 13.3): background stepping, pause, step, speed, placed incidents
policies/
  base.py              # Policy interface: decide(view, events) -> list[Action]
  greedy.py            # DESIGN section 10
coordination/          # Phase 2 agents (never name a package `agents`)
  schemas.py           # Pydantic decisions and proposed actions
  llm.py               # LLM wrapper: off, fake, replay, live; record every call
  tools.py             # read-only scoped views per agent, SDK function tools
  base.py  prompts.py  # rule options + LLM pick + fallback; instructions
  incident_assessment.py  dispatch.py  hospital.py  arbiter.py
  messages.py          # section 6 messages and the bus with section 7 time costs
  orchestrator.py      # AgentPolicy: the minute loop, admission Contract Net, Arbiter escalation
  protocols.py         # holds, transfers, staff lending, mutual aid, swap
  choices.py           # what the rule would have answered to a recorded LLM call (dashboard and diagnostics)
policies/registry.py   # policy names (greedy, agents and ablations) and LLM client setup
config/llm.json        # LLM mode and pinned model (unset until the first live run)
config/evaluations.json  # evaluation names, the headline set, contribution steps and timeline text
evaluation/
  runner.py            # seeds x policies x repeats as light runs, results.jsonl, summary.json, rebuild for playback
  summary.py           # DESIGN 11.2: mean and spread, improvement over the baseline, win rate, the claim
cli.py
dashboard/
  app.py               # one-screen layout: top control bar, map, right panel (DESIGN 13.1)
  style.py             # page CSS for the one-screen layout
  run_store.py         # list, load and save runs
  map_view.py          # pydeck layers
  panels.py            # resources, events, KPIs, baseline vs agents comparison
  agent_views.py       # agent activity and negotiation history, formatted from recorded messages
  evaluation_page.py   # the Evaluation page: final-results overview, picker, results, Run a new evaluation
  eval_catalog.py      # readable evaluation names and the overview, from config/evaluations.json
  feed.py              # the right panel's tabs: Conversations, Agents, Resources, Log
  names.py             # hospital display names (display only; run files keep the internal names)
  agents_tab.py        # the Agents tab: one card per agent (plain status, mood, last message), click to filter
  versions.py          # the Version choice (the four evaluated systems) and the recordings a replay can use
  wording.py           # every event, message, reason and decision code in plain English (tests/test_wording.py)
  conversation.py      # each incident's agent conversation, one line per message (the Incidents tab)
  explain.py           # the "what's happening now" sentence and the tooltip glossary
  explain_agents.py    # hospital status per thread, recorded LLM calls per decision, the rule's alternative
  eval_views.py        # evaluation tables and Plotly charts
tools/backfill_metrics.py  # adds new metrics to saved runs and evaluations by replay, existing numbers unchanged
tools/highlights.py    # ranks negotiations in recorded live runs, what-if with the rule, config/highlights.json
tools/diagnostics/
  negotiation_gap.py   # traces criticals lost to negotiation, on development seeds only (docs/FINDINGS.md)
  llm_choices.py       # a recorded live run: where the LLM chose differently from the rule, and the effect
  live_vs_rules.py     # greedy vs rules-only agents vs live LLM agents, from two saved evaluations
  llm_losses.py        # which LLM choices cost criticals (counterfactual replays), stuck-load counters, guard estimates
  final_table.py       # the final README table: greedy, rules-only, LLM guarded and unguarded
tests/
runs/                  # gitignored; one folder per run; evaluations in runs/evals/<eval-id>/
                       # (runs/dev-evals/: development-seed evaluations; only their summaries are committed)
```

## Coding conventions

- Python 3.11+, type hints, `from __future__ import annotations`.
- `sim/` uses dataclasses and the standard library plus `networkx`/`shapely` for roads.
- Deterministic string IDs (`H1`, `ALS2`, `I07`, `I07-V3`, `W12`), never UUIDs or timestamps. Run IDs: `seed42-greedy-001`.
- Times are integer simulated minutes; positions are (lat, lon).
- Every rejected action carries a short machine-readable reason (`unit_busy`, `er_full`, `capability_mismatch`, `not_cleared`, ...).
- Small, pure functions for rules. No global mutable state. Functions under ~50 lines.
- Docstrings only where the *why* isn't obvious; no comments restating code.

## Testing rules

- No network in tests. Tests use a **tiny hand-made road graph** fixture (a few nodes and edges) plus the committed real network for one smoke test.
- Determinism: the same seed produces byte-identical `events.jsonl`, `routes.json`, `timeline.jsonl` and `metrics.json`.
- Every world action: success path, each rejection reason, effects on reservations and timing.
- Baseline: follows DESIGN section 10 exactly (shortest road time choices; never holds, transfers, lends, reroutes early or releases early).
- Metrics: small scenarios with hand-computed answers.
- Run `pytest -q` after every change; don't move on with failing tests.

## How to work

1. Before each task, read the referenced DESIGN.md sections and outline the plan (files, functions, tests). Show the plan before large changes.
2. Build in small steps, one component with its tests at a time.
3. Tick the checkbox when a task is done.
4. To change a design decision, ask first, then update DESIGN.md in the same change.
5. Keep README.md (what it is, how to launch, assumptions) in sync.

---

## Phase 1: world, baseline and live map

Read DESIGN sections 3, 10, 11, 13, 15 and 17.

### 1.1 Area and road data

- [x] **First, ask the user which city and area to use** (about 5 to 10 km across, with at least 3 hospitals). Save it in `config/area.json`.
- [x] `scripts/fetch_roads.py`: download the drivable network with OSMnx; keep the largest strongly connected component; add edge speeds and travel times (default speeds where limits are missing); save `network.graphml`.
- [x] Also fetch hospital, ambulance-station and fire-station locations; snap each to its nearest network node; choose 3 well-spread hospitals; save `places.json`. Hospitals get fictional names (DESIGN 3.5). (Stations added later with `--stations-only`: 16 public fire stations (Marine Unit and Headquarters left out); the only ambulance station is private and excluded, so BLS units are based at fire stations.)
- [x] Commit both files. Simulation code only reads them.

### 1.2 Roads module

- [x] Load the graph once; fastest route by travel time (cached per origin and destination); travel minutes = route time x emergency factor, rounded up, minimum 1.
- [x] Route geometry as a list of (lat, lon) points following the street shapes, with cumulative times, so `position_at(route, depart_t, t)` interpolates smoothly (including fractional minutes for the dashboard).
- [x] Tests on the tiny fixture graph.

### 1.3 World (Phase 1 subset of DESIGN section 3)

- [x] Models: incidents (including one surge event), victims, hospitals (ER slots, ward beds, doctors with skills, nurses, transport vehicles), units (ALS, BLS, fire team, rescue team), firm reservations.
- [x] Generator: seeded incidents at random network nodes, victims (severity, care need, deadline), descriptions, walk-in schedule, fleet and bases (DESIGN 3.4).
- [x] Patient flow: arrival, staff assignment, treatment, `transferable` state for criticals after 20 minutes (state only; transfers come in Phase 2), `ready_for_release`, automatic release after 30 minutes, ward or boarding.
- [x] Scene rules: fire and collapse need clearance by the right team (10 minutes after arrival); 5 minutes on scene; 5 minutes handover; return to base along the road.
- [x] Unit loading rules: ALS carries 1 critical or 2 non-critical; BLS 2 non-critical; criticals only in ALS.
- [x] Walk-ins (DESIGN 3.7): always get a slot; may break a firm reservation (`reservation_broken`); the unit learns this on arrival (`arrived_no_slot`) and may be given a new destination.
- [x] Actions: `send_to_scene`, `set_destination` (also allowed after `arrived_no_slot`), `assign_team`. Validate and log every action.
- [x] Event log (`events.jsonl`) with every event in DESIGN 3.10 that exists in Phase 1, and `routes.json` with every trip's geometry and timing.
- [x] Views: `World.snapshot()` for the policy and the dashboard.

### 1.4 Baseline and metrics

- [x] `policies/base.py`: `Policy.decide(view, events) -> list[Action]`, called once per minute before `step()`.
- [x] `policies/greedy.py`: exactly DESIGN section 10, using road travel times. Zero decision time.
- [x] `sim/metrics.py`: critical on time %, unserved criticals, average report-to-care time, ER load balance, per-hospital idle and overload minutes, response time, unit utilization, rejected actions.
- [x] `sim/runner.py`: `run(seed, policy, progress_cb)` writes `runs/<run-id>/` with `config.json`, `scenario.json`, `events.jsonl`, `routes.json`, `metrics.json`, plus `timeline.jsonl` (state per minute, for playback) and `victims.json` (final victim records).
- [x] `cli.py run` (headless, for tests and debugging).

### 1.5 Dashboard

- [x] Sidebar: seed, policy (greedy only for now), **Run simulation** (progress bar), **New scenario**, **Past runs**, play/pause, speed (1x, 5x, 20x), time slider.
- [x] Map (pydeck): free basemap that needs no API key; road-following unit movement with smooth interpolation; incidents appearing at their report time (red with criticals, amber without, sized by victims); remaining route dashed; hospitals with ER use. Legend.
- [x] Panels synced to the playback minute: units free and busy by type; per-hospital ER, ward, doctors, nurses; live events feed; KPIs.
- [x] Smooth playback without full-page flicker (for example a Streamlit fragment that reruns on a timer, updating only the map and panels).
- [x] Launchers for macOS, Windows and Linux.

### Phase 1 is done when

- [x] All tests pass with no network.
- [x] From the launcher: Run simulation for seed 42 → ambulances drive real streets to incidents and hospitals; all panels update in sync; Past runs reopens it. (Map markers reworked after user feedback.)
- [x] Running seed 42 twice gives identical files.
- [x] README explains launching, the chosen area and the assumptions.

---

## Phase 2: agents

Read DESIGN sections 2, 3.8, 3.9, 3.11, 4, 12 and 17. Agreed scope: agents return **typed decision objects** (Pydantic) with proposed world actions; the section 6 message schema, the bus, time costs and the orchestrator are Phase 3. Nothing in Phase 2 calls the OpenAI API: tests use a fake LLM, and the model in `config/llm.json` stays unset until the user picks one.

### 2.1 Agent-only world actions (same world for everyone; the baseline never uses them)

- [x] `reserve` (firm for given victims, or hold: targeted at an expected critical, or at most 1 general hold per hospital; holds expire after 15 minutes, are released when their critical goes elsewhere, and convert when it is reserved here). `set_destination` reuses an existing firm reservation, otherwise creates one as in Phase 1.
- [x] `release_hold`, early `release_patient`.
- [x] Hospital transport vehicles as units (`H1-TV1`): `transfer_patient` (5 minutes preparation, remaining treatment continues at the receiver), `lend_vehicle` (carries like BLS while lent, returns after the end time once free).
- [x] `lend_staff` (travels by road, returns after the end time once its patients are done).
- [x] `redirect` (ALS, before loading, once per trip, only to an incident with a waiting critical) and `reroute` (while transporting, once per trip unless by the Arbiter, firm reservation at the new hospital, deadlines still met).
- [x] Walk-ins take a hold before breaking a firm reservation. Permission checks (`by`) and a rejection reason for every rule. Events for all of it.
- [x] Metrics: wasted hold minutes, holds, transfers, reroutes, redirects, early releases, loans.
- [x] Greedy runs unchanged in behaviour; all Phase 1 tests still pass.

### 2.2 LLM wrapper, tools and agents (`coordination/`)

- [x] `coordination/llm.py`: modes `off`, `fake` (tests), `replay` and `live`/`record` (OpenAI Agents SDK, pinned model, temperature 0). Every call recorded (`llm_calls.jsonl`); outputs validated with Pydantic; invalid or missing output falls back to a rule, logged.
- [x] `coordination/tools.py`: read-only scoped views (incident facts; Dispatch: its units and hospital capabilities only; Hospital: only its own hospital; Arbiter: everything), also offered to the LLM as function tools in live mode.
- [x] Agents: rules (code) build the numbers and the candidate options; a fast path decides when only one option fits; otherwise the LLM picks among the candidates with a one-line reason; a fallback rule covers anything invalid.
  - [x] Incident Assessment: report (response type, units needed, urgency, care counts) plus LLM context from the description.
  - [x] Dispatch: unit plan for competing requests (shortfall plans: queue, wait for a unit, redirect, mutual aid, swap) and admission awards from hospital offers (escalate when no offer meets a deadline).
  - [x] Hospital: mood, admission offer by the section 4.3 ladder, replies to transfer, staff and vehicle requests (EMTALA rules).
  - [x] Arbiter: options 1 to 7 in order, stopping at the first that meets the deadline.
- [x] Tests: every agent returns valid output on hand-built cases with no API key (fast path, LLM choice, invalid LLM output → fallback, replay).

### Phase 2 is done when

- [x] All tests pass with no network and no API key.
- [x] Each agent returns valid output on hand-built cases (rules, fake LLM, replay).
- [x] The greedy baseline on seed 42 gives the same events and metrics as before (apart from new metric keys).

---

## Phase 3: negotiation

Read DESIGN sections 5, 6, 7, 8, 9, 11, 12 and 3.11. The live LLM run comes last and needs the user's model ID and API key; everything else is built and tested with LLM mode `off` and the fake LLM.

- [x] `coordination/messages.py`: the section 6 message model (deterministic ids, threads, rounds) and a bus that delivers after the section 7 costs (1 minute per round; escalation 1 + 2 = 3 minutes).
- [x] `coordination/orchestrator.py`: `AgentPolicy` (a `Policy`): each minute turns world events and due messages into agent triggers, runs the agents, schedules replies, returns agreed actions (including delayed `not_before` actions). Agent messages and agent decisions are logged in the event stream.
- [x] Protocols (section 5): unit request and updates; admission Contract Net per unit load (pre-alert, call for proposals, offers, award, firm reservation, destination; up to 3 rounds); mutual aid vehicles; swap; routine stabilize-and-transfer; staff lending; reroute after a broken reservation (capacity alert).
- [x] Limits (section 8): 3 rounds; no reply in a round counts as a decline; reroute and redirect limits; escalate to the Arbiter on the section 4.4 triggers; Arbiter decisions are final.
- [x] Ablations (11.3): `agents-nonego`, `agents-noarb`, `agents-rulehosp`.
- [x] Runner and CLI: `--policy agents...`, LLM mode (`off`, `live`, `replay --from <run>`); writes `messages.jsonl` and `llm_calls.jsonl`; negotiation metrics (threads, rounds, counters, deals without the Arbiter, Arbiter decisions, rule vs LLM share).
- [x] Dashboard: policy choice includes the agents and ablations; Live or replay control.
- [x] Tests: each protocol on hand-built cases; limits; determinism (same seed, same files); replay of a recorded (fake-LLM) run matches exactly.

### Phase 3 is done when

- [x] All tests pass with no network and no API key.
- [x] Agents runs (rules only) complete for seeds 1 to 20 with no crashes.
- [x] A live agent run completes from the dashboard and its replay matches exactly (`seed42-agents-012`, replayed as `seed42-agents-013`).

---

## Phase 4: dashboard v2

Read DESIGN sections 6, 11.1 and 13. Everything reads recorded runs; no simulation logic in the dashboard.

- [x] View switch under the map for agent runs: resources and events, agent activity, negotiation history.
- [x] Agent activity (`dashboard/agent_views.py`): per agent, status at the playback minute, pending requests (in their round and unanswered), recent messages, last decision (rule, LLM or fallback); hospitals also show mood, ER use and holds.
- [x] Negotiation history: section 11.1 negotiation totals; thread table (protocol, incident, victims, rounds, counters, declines, outcome as of the minute) with protocol and incident filters; thread detail with messages and linked decisions in order, following the latest thread by default.
- [x] One unit-request thread per incident (request, updates and responses), so threads read as one negotiation each.
- [x] Hospital offer reasons say what the offer does (for example "protective: decline (no free capacity now)").
- [x] Compare with baseline: the newest greedy run of the same seed with a byte-identical scenario (or a button that runs it); two maps on one slider; KPI comparison at the minute (baseline, agents, change, better or worse); both runs' resources and events.
- [x] Tests: activity, pending and status timing, thread summaries and outcomes, filters, baseline lookup, KPI comparison.

### Phase 4 is done when

- [x] All tests pass with no network.
- [x] A recorded agent run plays back with all panels, next to the baseline (rules-only run; checked in a headless browser).

## Phase 5: evaluation

Read DESIGN sections 1, 11 and 12. The evaluation seeds are 1 to 20; don't tune agents on them.

- [x] Light runs (`run(..., light=True)`): config, scenario, metrics and LLM calls only; `evaluation.runner.rebuild_run` re-creates the full folder exactly (re-simulated, or replayed for LLM runs) for playback.
- [x] `evaluation/runner.py`: seeds x policies (the baseline always, plus the chosen agent variants) x repeats; each seed's policies must run byte-identical scenarios; `results.jsonl` (one row per run) and `summary.json` (written last) under `runs/evals/<eval-id>/`. LLM modes: rules only (each seed once, deterministic), live (repeats, default 3), replay of a recorded evaluation.
- [x] `evaluation/summary.py` (DESIGN 11.2): repeats averaged per seed; mean and standard deviation across seeds, plus spread across repeats; improvement (agents - baseline) / baseline on the means; wins, ties and losses on the primary metric; the section 1 claim sentence.
- [x] `cli.py eval --seeds 1-20 --policies ... --llm off|live|replay --repeats N --from <eval-id>`.
- [x] Dashboard Evaluation page: settings, run count and LLM call estimate, **Run evaluation** with progress, past evaluations; the claim, a KPI row, criticals on time by policy (bars with ±1 sd), wins table, any variant against the baseline (every metric, per-seed dumbbell chart), all-policy table, per-seed table, open any evaluated run in playback.
- [x] Tests: summary maths, settings, evaluation files and determinism, recorded evaluation replays exactly, rebuilt runs match their evaluated metrics, tables and charts.

### Phase 5 is done when

- [x] All tests pass with no network.
- [x] One click produces the final comparison (rules only; checked in a headless browser: 100 runs in about 30 seconds).
- [x] A live evaluation (agents 3 times per seed, DESIGN 11.2) and its replay: `eval-008` (gpt-4o-mini, 60 runs, 0 failed), replayed exactly as `eval-009`. Live LLM agents 68.6% vs rules-only 85.0% (docs/FINDINGS.md).

## Live runs (DESIGN 3.3, 12, 13.3)

Agreed with the user after Phase 5: runs happen in real time instead of being computed first and played back.

- [x] `sim/live.py`: a background worker steps the world minute by minute at the chosen speed (1x = one simulated minute per second; 0.5x, 2x, 4x, 10x; skip quiet time); pause, resume, step one minute; a finished live run saves byte-identical files to a headless run.
- [x] Placed incidents: pause, click a street intersection, choose type and victims (or let the seed decide); reported the next minute; saved with the run and replayable; the baseline runs against the same incidents.
- [x] Dashboard: Run simulation starts a live run; Restart with new seed waits paused at minute 0; Past runs replay from minute 0 with the same clock; no time slider; Policy menu shows the baseline and agents (ablations stay on the Evaluation page); 4 incidents by default; "Walk-in patients (N)" on each hospital.
- [x] Tests: live run equals headless run (with and without placed incidents), spawn rejections, pause, step and resume in the background, abandoning a run.
- [ ] Decide later: whether the baseline runs alongside a live agents run on the same clock (for now Compare works once the run is saved).

## Presentation UI (DESIGN 13)

- [x] One screen at 1440 x 900: top control bar (seed, policy, LLM with replay picker, Run, Pause/Resume, Step, speed, skip quiet time, Spawn, ⚙ popover), map left, KPI cards, units, hospitals and a tabbed feed right; compare mode with two maps and a KPI table.
- [x] Playback: 1x = one simulated minute per second (0.5x to 10x), skip quiet time (default on), 8 frames per second with fading trails; outcomes unchanged (tests).
- [x] Evaluation page opens on the final results (headline numbers, chart, contributions, timeline), readable names, Show all evaluations, Run a new evaluation at the bottom; named evaluations' summaries committed.
- [x] Explained without help: "what's happening now" sentence, feed tabs Incidents / Negotiations / Decisions / Hospitals / Log from templates (no LLM calls; recorded reasons verbatim), guard steps logged as `guard` events (no decision or metric changes), map clicks open cards and highlight, friendly names and tooltips, intro with a final-eval replay as default, "How to read this".
- [x] Readable for a non-technical viewer: one wording module (coverage test), Negotiations as one timeline per incident, highlights with "Jump to this moment", the Demo preset (seed 376 + 2 placed incidents, recorded live, runs/demo/).
- [x] Simpler feed: only Incidents (one story per incident, numbered plain sentences, one "(AI)" tag with the AI's words on hover) and Log.
- [x] Conversation feed: each incident's agent conversation in time order (sender → recipients, reason in the sentence, "·" narrator lines for world events, guard blocks and folded repeats), Between hospitals entry, replacing the stories.
- [x] Dashboard fixes and required metrics: one Version dropdown (Replay/Live, recording per seed) with the status line always matching it; labelled buttons; six KPI cards; unit free now and busy so far; hospital staff free and use so far; unit dots named on hover; Evaluation page resource use and conflicts table, plain ablation sentences, one decimal; allocation conflicts and hospital utilization metrics back-filled by replay (DESIGN 11.1, 11.2).
- [x] Assignment check: Agents strip (one line per agent, click filters the conversations), KPI cards in one row, "Unserved critical victims" everywhere, the mapping to the assignment's agents and why scenarios come from a seeded generator (README, DESIGN 4).
- [x] Right panel: KPI cards on top, then four tabs (Conversations, Agents as a 2-column card grid with plain statuses, Resources, Log), each opening with a grey line saying what it shows.
- [x] Polish: neutral hospital display names, no overlapping map labels (walk-ins and parked vehicles on hover), feed fills the window, Negotiations as list + detail with merged bubbles, hospital chips and plain wording.

## Things not to do

- Don't tune agent rules on the evaluation seeds 1 to 20; use other seeds for debugging.
- Don't download map or road data at simulation time or in tests.
- Don't use straight-line distance anywhere a travel time is needed; always use the road network.
- Don't put simulation logic in the dashboard.
- Don't use real hospital names or imply real capacity data.
- Don't change thresholds, timings or metric definitions without asking and updating DESIGN.md.
