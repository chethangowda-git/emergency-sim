# CLAUDE.md

Instructions for Claude Code in this repository. Read this file fully at the start of every session.

## What this project is

A multi-agent emergency response system: a **deterministic, seeded simulator on a real city road network**, a **greedy baseline**, and later **LLM agents (OpenAI Agents SDK)** that negotiate over ambulances, hospital capacity and staff. Everything is run and watched from a **Streamlit dashboard** with buttons; the user never needs the command line.

**`docs/DESIGN.md` is the source of truth.** Read the sections a task references before coding. If code and DESIGN.md disagree, DESIGN.md wins. If DESIGN.md is ambiguous or seems wrong, stop and ask; don't invent rules.

The repo starts empty except for this file and `docs/DESIGN.md`. We build in 5 phases (DESIGN section 17). **Current phase: 1.** Area: central Boston (agreed; see DESIGN 3.11). Don't build anything from later phases.

## Golden rules

1. **Only the simulator changes state.** Policies (and later agents) return actions; `World` validates and applies them.
2. **One source of randomness:** the seeded `random.Random(seed)`. Never use module-level `random`, `time.time()`, set iteration order, or anything else non-deterministic in simulation code. Sort before iterating when order matters.
3. **The road network is a committed file.** Simulation code never downloads anything; only the one-time fetch script does.
4. **Rules compute numbers.** Travel times, capacities, deadlines and metrics are plain, tested Python functions.
5. **The baseline and future agents share the same world.** Never add a world rule that only one of them experiences.
6. **The dashboard only reads saved runs** (plus calling the run function behind the Run button). It never contains simulation logic.
7. **Don't name any local package `agents`** (it clashes with the OpenAI Agents SDK import). Future agents live in `coordination/`.

## Setup (for the user, done once)

The user double-clicks `launch.command` (macOS), `launch.bat` (Windows) or runs `./launch.sh` (Linux). The launcher creates `.venv` if missing, installs `requirements.txt`, and starts `streamlit run dashboard/app.py`. Create all three launchers in Phase 1 and keep them working.

`requirements.txt` (pin versions once it works): `osmnx`, `networkx`, `shapely`, `streamlit`, `pydeck`, `plotly`, `pandas`, `pytest`. Later phases add `openai-agents` and `pydantic`.

## Commands (for you, Claude Code)

```bash
pytest -q                                    # all tests, no network
python scripts/fetch_roads.py                # one-time road download (needs network)
python -m cli run --policy greedy --seed 42  # headless run, used by tests and debugging
streamlit run dashboard/app.py               # the dashboard
```

## Repository layout (Phase 1)

```
CLAUDE.md
docs/DESIGN.md
launch.command  launch.bat  launch.sh
requirements.txt  README.md  .gitignore
config/
  area.json            # chosen city area, bounding box or place name, emergency speed factor
scripts/
  fetch_roads.py       # one-time: download roads + hospital and station locations
data/roads/            # committed outputs of fetch_roads.py
  network.graphml
  places.json          # hospitals (3 chosen), ambulance and fire stations
sim/
  config.py            # SimConfig dataclass, all tunables from DESIGN section 3
  roads.py             # load network, fastest route, route geometry, position along route
  models.py            # Incident, Victim, Hospital, Staff, Unit, Reservation, ...
  rules.py             # small pure rules: care needs, loading, slots, real capacity
  actions.py           # SendToScene, SetDestination, AssignTeam
  generator.py         # seeded scenario: incidents, victims, walk-in schedule, fleet
  world.py             # World: step(), actions, event log
  hospital_flow.py     # patient lifecycle inside a hospital, walk-ins
  views.py             # World.snapshot() data and road-time helpers for policies
  metrics.py           # DESIGN section 11 (Phase 1 subset)
  runner.py            # run(seed, policy, progress_cb) -> run folder
policies/
  base.py              # Policy interface: decide(view, events) -> list[Action]
  greedy.py            # DESIGN section 10
cli.py
dashboard/
  app.py               # layout, sidebar controls
  run_store.py         # list, load and save runs
  map_view.py          # pydeck layers
  panels.py            # resources, events, KPIs
tests/
runs/                  # gitignored; one folder per run
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
- [x] Also fetch hospital, ambulance-station and fire-station locations; snap each to its nearest network node; choose 3 well-spread hospitals; save `places.json`. Hospitals get fictional names (DESIGN 3.5). (Stations still missing: Overpass was overloaded, so `--skip-stations` was used; re-run the script to add them.)
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
- [ ] From the launcher: Run simulation for seed 42 → ambulances drive real streets to incidents and hospitals; all panels update in sync; Past runs reopens it.
- [x] Running seed 42 twice gives identical files.
- [x] README explains launching, the chosen area and the assumptions.

---

## Later phases (outline only; detailed instructions will be added when each phase starts)

2. **Agents:** the four agents, LLM wrapper with record and replay, read-only tools, agent-only actions.
3. **Negotiation:** messages, bus, time costs, limits, orchestrator, Arbiter escalation, ablations.
4. **Dashboard v2:** agent activity, negotiation history, baseline vs agents side by side.
5. **Evaluation:** seeds, repeats, ablations, results from one button.

## Things not to do

- Don't build Phase 2+ features (holds, transfers, lending, reroutes, early release, agents, LLM calls) in Phase 1.
- Don't download map or road data at simulation time or in tests.
- Don't use straight-line distance anywhere a travel time is needed; always use the road network.
- Don't put simulation logic in the dashboard.
- Don't use real hospital names or imply real capacity data.
- Don't change thresholds, timings or metric definitions without asking and updating DESIGN.md.
