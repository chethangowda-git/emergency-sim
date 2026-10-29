# Emergency response simulator

A multi-agent emergency response demo. A **deterministic, seeded simulator** runs on a **real city road network** (central Boston). A **greedy rule-based baseline** dispatches ambulances, fire and rescue teams, and hospital admissions. LLM agents that negotiate over the same resources come in later phases. Everything is run and watched from a **Streamlit dashboard**.

The design is in [`docs/DESIGN.md`](docs/DESIGN.md) (the source of truth). The current phase is **1: world, baseline and live map**.

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

The map shows ambulances (ALS red, BLS blue), fire teams (orange) and rescue teams (brown) driving along real streets, with their remaining route dashed. Incidents appear when reported: red if they have critical victims, amber if not, grey once everyone is at a hospital. Hospitals show their ER use. The panels below (units, hospitals, live events, KPIs) follow the playback minute.

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

Nothing calls the OpenAI API yet. `config/llm.json` has `mode: off` and no model. Before the first live run (Phase 3), set `model` to a pinned OpenAI model ID and provide `OPENAI_API_KEY`. Every LLM call is then recorded, so a run can be replayed exactly without a key.

## Negotiation (Phase 3)

Choose **agents** in the Policy menu to run the multi-agent system. Agents talk through messages, and each round costs a simulated minute; an escalation to the Arbiter costs 3. The world keeps moving while they negotiate.

- **Admission:** for each ambulance load, Dispatch runs a Contract Net with the hospitals (call for proposals, offers, award). The awarded hospital places a firm reservation, then Dispatch sets the destination.
- **Other protocols:** mutual-aid vehicles, the swap, routine transfers between hospitals, staff loans, and rerouting an ambulance after a walk-in takes its reserved slot.
- **The Arbiter** steps in when no offer is in time, or when 3 rounds fail while a deadline is at risk. Its rulings are final.

Three comparison variants switch parts of the system off: **no negotiation**, **no Arbiter** and **rule hospitals**.

For agents, the sidebar also has an **LLM** choice: rules only (no key needed), live (needs the model and key described above; every call is recorded), or replay of a recorded run (exact, no key). Run folders for agents also contain `messages.jsonl` and `llm_calls.jsonl`. In the Live events feed, the **Agent messages** toggle shows the messages and each agent's decisions.

## For developers

```bash
pytest -q                                    # all tests, no network
python -m cli run --policy greedy --seed 42  # headless run (also: --policy agents --llm off)
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
