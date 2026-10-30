# Multi-Agent Emergency Response System: Design

Status: agreed design, version 2.1 (real road map, dashboard controls, 5 build phases).
Owner: Chethan Gowda GT. This document is the source of truth for implementation.

---

## 1. Purpose and scope

This project demonstrates that **agents that communicate, coordinate and negotiate** get better emergency-response outcomes than a sensible rule-based system working with the same resources. It is a demonstration of agentic coordination, not a production dispatch system. Numbers such as travel speed, treatment times and legal rules are deliberately approximate and are listed as assumptions in section 15.

The system is built with the OpenAI Agents SDK (Python) on top of a deterministic, seeded simulator, and visualized with Streamlit and Plotly.

### The claim we want to be able to make

> With the same seeds, incidents, walk-ins and resources, the multi-agent system got X% of critical victims to care on time versus Y% for the greedy baseline, winning on N of 20 seeds.

---

## 2. Design principles

1. **The simulator owns the truth.** All state lives in the simulator. Agents never change it directly; they propose actions, and the simulator validates and applies them.
2. **Rules do the math, the LLM makes judgments.** Travel times, capacities, deadlines and scores are computed in code (tools). The LLM is used only where there is a real trade-off.
3. **Each agent sees only what it needs.** Limited views are what force agents to communicate. Only the Arbiter sees everything.
4. **Negotiation needs two parties who each control something and want different things.** Interactions that don't meet this test are requests or decisions, not negotiations.
5. **Every negotiation has a defined end.** Round limits, timeouts and the Arbiter guarantee that no request is left hanging.
6. **Fair comparison.** The baseline and the agents run in the same world with the same rules; only the decision-making differs. Negotiation costs simulated time.
7. **Reproducible.** The world is seeded; LLM calls are recorded and can be replayed exactly.

---

## 3. World model (simulator)

### 3.1 Space and time

| Item | Value |
|---|---|
| Map | A real city area of roughly 5 to 10 km across, chosen by the user at the start of Phase 1. Its drivable road network is downloaded once from OpenStreetMap with OSMnx and saved in the repo, so runs are reproducible and work offline |
| Locations | Nodes of the road network (latitude, longitude), restricted to its largest strongly connected component so every point can reach every other |
| Travel time | Fastest route on the road network, using edge travel times from speed limits, multiplied by an emergency driving factor (default 0.85), rounded up to whole minutes, minimum 1 minute. Traffic is ignored |
| Movement | Units follow the route's street geometry; their position at any moment is interpolated along the route, so the map can show smooth movement |
| Tick | 1 simulated minute |
| Horizon | 360 minutes (incidents arrive in the first 120) |
| Randomness | One `random.Random(seed)`; never use any other source of randomness |

### 3.2 Victims and deadlines

Every victim has a severity, a care need and a deadline measured from the incident's report time.

| Severity | Meaning | Deadline to reach care | ER treatment time |
|---|---|---|---|
| critical | Life-threatening | 60 min | 90 min |
| serious | Needs care soon | 120 min | 60 min |
| minor | Can wait | 240 min | 30 min |

Care needs are `trauma`, `burns` or `general`. Minor victims always need `general`. Others need the incident type's specialty (accident and collapse: trauma; fire: burns; medical emergency: general).

**"On time"** means the victim arrives at a hospital that can treat them (for criticals, any hospital that can at least stabilize them) before the deadline.

### 3.3 Incidents

| Field | Notes |
|---|---|
| type | traffic_accident, building_fire, structural_collapse, medical_emergency, bus_fire (surge) |
| location, reported_at | A seeded random road-network node; arrival times are fixed by the seed |
| victims | Hidden ground truth; revealed when the incident is reported |
| requires | `fire` (fires), `rescue` (collapses) or none; victims cannot be transported until a matching team has cleared the scene (10 min after the team arrives) |
| description | Short text the Incident Assessment LLM can read for context (e.g. "school bus") |

One incident per scenario is a large surge event (multi-vehicle pileup or bus fire, 6 to 10 victims).

### 3.4 Dispatch-controlled units (public fleet)

| Unit | Crew | Can carry |
|---|---|---|
| ALS ambulance | Paramedics | Any victim, including critical; capacity 1 critical, or 2 non-critical |
| BLS ambulance | EMTs | Serious and minor only; capacity 2 |
| Fire team | Firefighters | Nobody; clears `fire` scenes |
| Rescue team | Rescue crew | Nobody; clears `rescue` scenes |

All paramedics belong to Dispatch. Default fleet: 3 ALS, 3 BLS, 2 fire teams, 1 rescue team.

Unit bases: ambulances at hospitals and at real ambulance or fire stations in the area (taken from OpenStreetMap when available, otherwise seeded random nodes); fire and rescue teams at fire stations.

Unit timings: 5 minutes on scene before transport, 5 minutes handover at the hospital, then the unit returns to base (and can be reassigned while returning).

### 3.5 Hospitals

| Resource | Notes |
|---|---|
| ER slots | Patients occupy a slot from arrival until released |
| Ward beds | Serious and critical patients move here after ER treatment; if full, they "board" in the ER and keep the slot |
| Doctors | Have skills (`general` plus one specialty); max 2 patients each |
| Nurses | Max 3 patients each |
| Transport vehicles | Owned by the hospital, basic crew, **no paramedics**; can carry only non-critical or stabilized patients; default 1 per hospital |
| Capabilities | Which care needs the hospital can treat |

Hospitals sit at real hospital locations in the chosen area (3 chosen from OpenStreetMap so they are well spread out), but use fictional names and simulated capacities, so nobody mistakes the simulation for real data.

Default hospitals:

| ID | Name | Capabilities | ER slots | Ward beds | Doctors | Nurses |
|---|---|---|---|---|---|---|
| H1 | Central Trauma Center | trauma, burns, general | 6 | 10 | 3 | 3 |
| H2 | Northside General | trauma, general | 4 | 6 | 2 | 2 |
| H3 | Eastside Community | burns, general | 4 | 6 | 2 | 2 |

Each ER patient needs one doctor with the matching skill and one nurse. Patients wait in the ER if staff are unavailable.

### 3.6 Patient lifecycle at a hospital

```
arrives -> in_er (waiting for staff) -> in_treatment
        -> [after 20 min, critical only] transferable (severity becomes serious,
           new deadline = now + 120 to reach the receiving hospital)
        -> treatment done -> ready_for_release
        -> released: minor = discharged; others = moved to ward bed (or boarding)
```

- **Release delay:** a `ready_for_release` patient is released automatically after 30 minutes (paperwork). A hospital may release them earlier with `release_patient`. The greedy baseline never releases early.
- **Stabilization:** only the simulator decides when a patient is transferable. Agents only decide whether to transfer.

### 3.7 Walk-in patients

Patients arrive at hospitals by private car, at a seeded random rate (default: on average 1 per hospital per 40 minutes; severity mix 20% critical, 40% serious, 40% minor). Under US law (EMTALA) a hospital must screen and stabilize anyone who arrives, so **a walk-in always gets a slot**. If no slot is free, the walk-in takes a firm reservation's slot, and the simulator emits `reservation_broken` for the affected ambulance.

### 3.8 Reservations

| Type | Meaning | Rules |
|---|---|---|
| Firm | A specific patient is assigned and a unit is on the way | Honored, except when a walk-in takes it |
| Hold | A slot kept free for an expected critical | Must reference a reason (a pre-alert or known incoming critical), plus at most 1 general hold per hospital; expires after 15 minutes if no patient is assigned; released immediately if the expected critical goes elsewhere; the Arbiter may override it |

Expired holds that were never used are counted as wasted capacity.

### 3.9 Simulator actions

All actions are validated. A rejected action returns a reason and is logged.

| Action | Who may request it | Key validation |
|---|---|---|
| `send_to_scene(unit, incident)` | Dispatch | Unit idle or returning |
| `set_destination(unit, hospital, victims)` | Dispatch (after admission is agreed) | Firm reservation exists; unit type can carry these victims |
| `assign_team(team, incident)` | Dispatch | Capability matches `requires`; incident not already covered |
| `redirect(unit, new_incident)` | Dispatch | ALS only; only for a critical; max once per trip |
| `reroute(unit, new_hospital)` | Dispatch | Before arrival; max 1 per trip (more only via Arbiter); new firm reservation exists; patient still meets the deadline |
| `reserve(hospital, victims, kind)` | Hospital | Firm or hold; hold rules above |
| `release_hold(hospital, hold)` | Hospital or Arbiter | Hold exists |
| `release_patient(victim)` | Hospital | Patient is `ready_for_release` |
| `transfer_patient(victim, to_hospital, vehicle)` | Sending hospital (routine) or Arbiter (urgent) | Patient transferable; receiver has a firm reservation; vehicle free; 5 min preparation before departure |
| `lend_vehicle(vehicle, until)` | Hospital (mutual aid) | Vehicle free; becomes usable by Dispatch until the end time |
| `lend_staff(staff, to_hospital, until)` | Lending hospital (or Arbiter, urgent) | Staff member has no current patients; arrives after travel time; returns after the end time once current patients are done |

### 3.10 Events

The simulator emits events into a JSONL log, including: `incident_reported`, `scene_cleared`, `unit_on_scene`, `transport_started`, `patient_delivered`, `walk_in`, `reservation_broken`, `stabilized`, `ready_for_release`, `patient_released`, `hold_expired`, `deadline_missed`, `action_applied`, `action_rejected`. Agent messages are logged in the same stream.

### 3.11 Scenario defaults and clarifications (agreed at the start of Phase 1)

| Topic | Rule |
|---|---|
| Area | Central Boston, MA (Back Bay, South End, Fenway, Longwood, Downtown, North End, Charlestown), about 5 x 6 km |
| Incidents per scenario | 12: 11 normal incidents with 1 to 4 victims each, plus 1 surge event (6 to 10 victims) |
| Incident types (normal) | traffic_accident 35%, medical_emergency 35%, building_fire 20%, structural_collapse 10% |
| Surge event | bus_fire or multi-vehicle pileup (a traffic_accident), 50/50, reported between minute 30 and 90 |
| Victim severity mix | 25% critical, 35% serious, 40% minor |
| "On time" for criticals | The hospital must have the victim's care need as a capability (a critical burns victim counts only at H1 or H3) |
| Doctor skills | Every doctor has `general` plus one specialty. H1: trauma, trauma, burns. H2: trauma, trauma. H3: burns, burns |
| Walk-in care need | Minor walk-ins need `general`; others are drawn from the receiving hospital's own capabilities |
| Walk-in, ER full of patients | If every slot is occupied by patients (none merely reserved), the walk-in is placed over capacity and the minute counts as overload |
| Broken reservation choice | A walk-in breaks the firm reservation whose unit is expected latest |
| Reservations | One firm reservation per victim (one ER slot each) |
| Scene timing | Ambulance on-scene work starts when the unit is there and the scene is cleared; transport starts 5 minutes later |
| Idle ambulance on scene | An ambulance on scene with no victim assigned and nobody left waiting that it can carry returns to base |
| Report-to-care time | Hospital arrival (delivery) minus report time |
| "Victim waiting anywhere" (idle minutes) | Any incident victim reported but not yet delivered to a hospital |
| Utilization | `1 - real free capacity / ER slots`, with real free capacity = min(free ER slots, free doctor slots, free nurse slots) |
| Unit bases (current data) | The OpenStreetMap station query failed (server overloaded), so `places.json` has no stations: ALS units are based at H1 to H3, and BLS, fire and rescue units at seeded random road nodes (the 3.4 fallback). Re-running `scripts/fetch_roads.py` later adds real stations and changes every scenario's bases |
| Transferred patient | Treatment continues at the receiver for the remaining minutes once staff are free there |
| Walk-in, no free slot | Takes a hold first (latest created), then a firm reservation (expected latest) |
| Lent hospital vehicle | Carries like a BLS: up to 2 non-critical or stabilized patients |
| Redirect | ALS only, before loading (driving to a scene, or on scene with no victims assigned), once per trip |
| Holds | A targeted hold names the expected critical victim; a general hold names none (max 1 per hospital). Wasted hold minutes = minutes held by holds that expired unused |
| Reroute deadline | Rejected if any victim whose deadline has not yet passed would arrive after it at the new hospital |
| Escalation timing | An escalation takes 1 + 2 = 3 minutes from sending until the Arbiter's decision takes effect |
| Admission unit of negotiation | One Contract Net thread per unit load (the victims one ambulance will carry), so every award names a single hospital for that unit |
| Load expected ready time | Unit ETA to the scene + 5 minutes, + 10 if the scene still needs clearance |
| Swap | An ALS heading to a job with no critical is redirected to the critical; a hospital vehicle is lent to take over the abandoned job |
| Holds in negotiation | When a critical has no unit yet, Dispatch pre-alerts the nearest capable hospital, which may place a targeted hold |
| Unit request pushback | Not used: Dispatch already plans with the extra units Incident Assessment asks for |
| Units for non-criticals | After every incident has the right unit type, a free ALS may take a remaining non-critical need, but only if no critical anywhere still lacks an ALS |
| Mutual aid trigger | Dispatch asks hospitals for a vehicle only for non-criticals within 60 minutes of their deadline (moving minors in early fills ER slots criticals need) |
| Escalation triggers in Contract Net | Escalate at once when every offer for a victim arrives too late (trigger 1); when hospitals simply decline, negotiate up to 3 rounds and escalate only if a deadline is at risk (under 15 minutes of slack, trigger 2); a victim no capable hospital could reach in time is given the earliest offer (already the least-bad option) |
| Arbiter least-bad with no slot yet | Binding: the unit goes to the first capable hospital that has room, closest first, and the Arbiter may release a ready-for-release patient to make room; no renegotiation |
| Unit-request threads | One thread per incident holds its unit request, Incident Assessment's updates and Dispatch's responses |
| Same-minute capacity | A hospital declines an award ("capacity taken since my offer") if reservations already placed this minute leave too few slots |
| Evaluation repeats | The baseline and rules-only agents are deterministic, so they run once per seed; the 3 repeats apply to live LLM runs |
| Evaluation statistics | Repeats are averaged per seed first; spread is the standard deviation across seeds (and, live, the average across a seed's repeats); improvement uses the means; a win means strictly higher criticals on time, ties are reported apart; a seed with no criticals is left out of that metric |
| Metrics population | Primary and secondary metrics count incident victims only; walk-ins add load |

---

## 4. Agents

| Agent | Count | Owns | Sees |
|---|---|---|---|
| Incident Assessment | 1 | Nothing | Incidents (reported facts, description) |
| Dispatch | 1 | All ALS/BLS ambulances, paramedics, fire and rescue teams | All its units, incident reports, a summary of hospitals (capabilities only) |
| Hospital | 1 per hospital | Its ER slots, ward beds, staff, transport vehicles | Only its own hospital, plus messages addressed to it |
| Arbiter | 1 | Authority to make binding decisions | Everything |

The Arbiter appears as "Resolver" on the presentation slides; it is the same agent.

### 4.1 Incident Assessment

**Job:** turn a new or changed incident into a report and a unit request for Dispatch.

**Rules (code):**

1. Response type:
   - any critical victim: `critical_care`
   - fire or collapse: `rescue_first` (combined with the others)
   - otherwise: `transport`
2. Units needed: 1 ALS per critical victim; 1 BLS per 2 serious or minor victims; 1 fire team for fires; 1 rescue team for collapses.
3. Urgency: minutes left until the earliest deadline among waiting victims, and the number of victims at risk of missing it.
4. Care needed: counts by care need and severity, so hospitals can be pre-alerted.

**LLM judgment:** reads the description and adds what rules miss (e.g. "school bus: likely children, flag pediatric care"; "heavy smoke: more burns likely"; "rush-hour highway: expect slower travel"). It may adjust the unit request, with a one-line reason.

**Sends:** `unit_request` to Dispatch, and `incident_update` when something changes (victims picked up, deadline under 15 minutes, scene report differs).

**Negotiation:** weak. It owns nothing; it can push back on a partial response ("children involved, I need 2 ALS"), which Dispatch may accept or decline.

Incidents are ranked by **minutes left** (fewest first), then by victims at risk. There are no P1/P2/P3 labels.

### 4.2 Dispatch

**Job:** get the right unit to the right incident at the right time, and find each victim a hospital.

**Rules (code):** list free units and units that will free up soon (with remaining busy time), compute ETAs, match units to requests (critical victims need ALS).

**LLM judgment:**

- Several incidents competing for the same units.
- Timing: don't rush the nearest unit to a scene it will only wait at (e.g. fire clearance not done).
- Shortfalls: send what is available, queue, ask hospitals for vehicles, or redirect.

**Process for each request:**

1. Send units to the scene immediately (`send_to_scene`), while negotiating the destination in parallel.
2. Send a pre-alert and an admission call for proposals to hospitals (section 5.2).
3. When admission is agreed, `set_destination`.
4. Reply to Incident Assessment with what was sent and the ETAs (full or partial).

**When the fleet is exhausted:**

| New request | Dispatch does |
|---|---|
| Critical | Redirect an ALS unit from a lower-urgency job (max once per trip; the abandoned job goes to the front of the queue), or use the ALS unit that frees up soonest |
| Serious or minor | Queue by urgency, and send a mutual aid request for hospital vehicles |
| Critical, no ALS available soon | Ask a hospital to take over one of Dispatch's non-critical jobs with its vehicle (the swap), freeing an ALS unit |

It informs Incident Assessment of the expected delay.

### 4.3 Hospital (one per hospital)

**Job:** act in its own hospital's interest: treat patients it can treat well, use its resources efficiently, and protect capacity for criticals.

**Real capacity:** the smaller of free ER slots and staff able to treat the needed care (`free doctor slots with the skill`, `free nurse slots`).

**Mood by utilization of real capacity:**

| Utilization | Mood | Behavior |
|---|---|---|
| Under 60% | Eager | Idle resources are waste; accepts readily and may offer to take more |
| 60 to 85% | Normal | Accepts patients it treats well |
| Over 85% | Protective | Keeps its last slots for criticals; accepts others only with conditions, or declines with a reason |

**When a request arrives and capacity is tight, it tries, in order:**

1. Use free real capacity.
2. Release patients who are `ready_for_release`.
3. Stabilize and transfer a transferable patient (routine transfer, section 5.5), using its own vehicle.
4. Make a conditional offer (fewer patients, a later time, or "after my transfer completes").
5. Decline, with a reason.

**Beds free but no staff:** it weighs the delay against the patient's deadline ("bed now, treatment starts in 25 minutes" is fine for a minor, not a critical), or asks other hospitals for staff (section 5.6).

**Legal constraints (EMTALA, simplified):**

- Walk-ins are always accepted.
- Patient selection never depends on payment; "efficiency" is about resources, not money.
- A receiving hospital may refuse a lateral transfer (the sender can treat the patient equally well), but must accept a transfer that needs a specialty the sender lacks if it has capacity.

**Sends:** admission offers, mutual aid replies, transfer and staff requests and replies, `capacity_alert` when a walk-in breaks a reservation.

### 4.4 Arbiter

**Job:** resolve cases the other agents cannot resolve between themselves. It is not a central planner.

**Triggers (the only times it runs):**

1. No admission offer meets a patient's deadline.
2. A negotiation reaches 3 rounds without agreement while a deadline is at risk.
3. A reroute is needed but no hospital accepts in time, or a second reroute is needed for the same trip.
4. An urgent staff loan or transfer is needed to meet a deadline.
5. Time left is shorter than a negotiation would take (skip straight to the Arbiter).

**Options, tried in order (it stops at the first that meets the deadline):**

1. Another hospital that can be reached in time and accepts.
2. Release `ready_for_release` patients at the target hospital.
3. Stabilize-and-transfer swap: a transferable patient leaves the target hospital, the critical takes the slot.
4. Override a hold.
5. Reroute a different en-route non-critical patient to free a firm reservation.
6. Urgent staff loan.
7. Least-bad option: the closest capable hospital even if the deadline will be missed, logged as a loss.

**Authority:** it sends its plan to the parties to confirm. If a party refuses, the decision is binding, because the alternative is a missed critical deadline. Its decisions are final; nobody renegotiates them.

---

## 5. Negotiation protocols

All negotiations use the message schema in section 6, cost simulated time (section 7) and obey the limits in section 8.

### 5.1 Unit request (Incident Assessment and Dispatch)

```
IA -> Dispatch: unit_request (needs, urgency, care needed, note)
Dispatch -> IA: unit_response (sent units + ETAs, shortfall, plan for shortfall)
IA -> Dispatch: (optional) pushback with a reason
Dispatch -> IA: accept or decline the pushback
```

### 5.2 Admission (Dispatch and all hospitals): Contract Net

1. Dispatch sends a `pre_alert` and one `admission_cfp` (call for proposals) to every capable hospital: victims (severity, care need), deadlines, ETA from the unit to each hospital, and whether any other hospital is reachable in time.
2. Each hospital replies with an `admission_offer`: accept N, conditional (count, time, "after transfer"), or decline with a reason.
3. Dispatch awards (`admission_award`), possibly splitting victims across hospitals, or counters (`admission_counter`). The awarded hospital places a firm reservation.
4. Max 3 rounds. If no offer meets a deadline: escalate to the Arbiter.

### 5.3 Mutual aid vehicles (Dispatch and hospitals)

Dispatch sends `mutual_aid_request` ("need 1 transport vehicle at I07 for 2 serious victims"). Each hospital replies: send now, send later ("after minute 70"), or decline with a reason. Hospital vehicles carry only non-critical patients.

### 5.4 The swap (Dispatch and a hospital)

To free an ALS unit for a critical, Dispatch asks a hospital to take over one of its non-critical jobs with the hospital's vehicle. The hospital may accept, counter on timing, or decline.

### 5.5 Stabilize and transfer (between hospitals)

- **Routine** (no deadline pressure): the sending hospital sends `transfer_request` to other hospitals; receivers reply according to their mood and the EMTALA rule; the sender picks one and executes `transfer_patient` with its own vehicle.
- **Urgent** (a deadline cannot otherwise be met): the Arbiter arranges it as a swap (section 4.4, option 3).

The swap at the hospital door: the same ambulance can deliver the critical and pick up the transferring patient. Pick-up happens before drop-off, so the slot count never exceeds capacity.

### 5.6 Staff lending (between hospitals)

The borrowing hospital sends `staff_request` ("1 trauma doctor for about 90 minutes") to all hospitals. Each replies: send now, send later, or decline (e.g. "I have a pre-alert"). The borrower picks the best offer. Urgent cases go through the Arbiter. In the US this is realistic mainly within a health system or under disaster privileges; state it as an assumption.

### 5.7 Reroute after a broken reservation

```
Simulator: walk-in takes H1's slot -> reservation_broken
H1 -> Dispatch: capacity_alert ("can't take A4's patient")
Dispatch: checks the patient's severity and time left
  serious/minor with slack -> admission_cfp to other hospitals -> reroute
  critical, another hospital reachable in time -> reroute
  critical, none reachable in time -> escalation to the Arbiter
```

New ETAs are computed from the unit's current position.

---

## 6. Message schema

Every message is a Pydantic model with these fields:

| Field | Meaning |
|---|---|
| `id` | Unique, deterministic (counter) |
| `thread_id` | Groups the rounds of one negotiation |
| `round` | 1, 2, 3 |
| `sent_at`, `deliver_at` | Simulated minutes |
| `sender`, `recipient` | Agent IDs (`ia`, `dispatch`, `H1`, `H2`, `H3`, `arbiter`) or `all_hospitals` |
| `type` | See below |
| `incident_id` | When relevant |
| `victim_ids` | When relevant |
| `proposed_action` | Structured payload (units, counts, times, conditions) |
| `reason` | One short sentence; required for declines and counters |
| `decided_by` | `rule` or `llm` |

Message types: `unit_request`, `unit_response`, `incident_update`, `pre_alert`, `admission_cfp`, `admission_offer`, `admission_award`, `admission_counter`, `decline`, `mutual_aid_request`, `mutual_aid_offer`, `swap_request`, `swap_response`, `transfer_request`, `transfer_response`, `staff_request`, `staff_offer`, `capacity_alert`, `escalation`, `arbiter_decision`, `ack`.

---

## 7. Time cost of negotiation

| Action | Simulated cost |
|---|---|
| One message round (request to reply) | 1 minute |
| Escalation to the Arbiter | +2 minutes |
| Transfer preparation at the sending hospital | 5 minutes |
| Greedy baseline decision | 0 minutes (like a computer-aided dispatch system) |

The world does not pause during negotiation: deadlines keep running and units keep moving. Because units are sent to the scene while the destination is negotiated, most negotiation time overlaps with driving.

---

## 8. Limits

| Situation | Limit | Then |
|---|---|---|
| Rounds in one negotiation | 3 | Take the best offer, or escalate if a deadline is at risk |
| No reply | 1 round | Counts as a decline |
| Reroutes per unit trip | 1 | Further changes only through the Arbiter |
| Redirects of an ALS unit | Once per trip | The abandoned job goes to the front of the queue |
| Arbiter decisions | Final | No renegotiation |
| Time left shorter than a negotiation | - | Go straight to the Arbiter |

---

## 9. Orchestration (one simulated minute)

1. `world.step()` advances time and emits events.
2. The orchestrator turns events and due messages into agent triggers (event-driven: an agent runs only when something happens to it).
3. Triggered agents run. Each agent first tries its **fast path** (code decides when only one option fits); otherwise it calls the LLM. Requests arriving at the same agent in the same minute are batched into one call.
4. Outgoing messages are scheduled with their delivery time (section 7).
5. Agreed actions are submitted to the simulator; rejections become events for the next minute.
6. Everything is logged.

---

## 10. Greedy baseline

A sensible rule-based system with no negotiation and no looking ahead.

**It does:**

1. Handle incidents most severe first, then oldest first.
2. Send the free unit with the shortest road travel time, of the right type (ALS for criticals, BLS for others; if no ALS is free, any free unit that is allowed to carry the victim, otherwise wait).
3. Choose the hospital with the shortest road travel time that has a free slot and the right capability at that moment, and reserve it.
4. Assign the capable team with the shortest road travel time to fires and collapses.
5. If a walk-in breaks its reservation, the unit finds out on arrival and drives to the next hospital (by road travel time) with space.

Clarifications: "free" units are idle or returning to base. The hospital is chosen once the ambulance is on scene and the scene is cleared. If no capable hospital has a free slot, the unit waits and the choice is retried every minute.

**It never:** holds slots, makes conditional offers or counters, requests mutual aid, swaps, transfers, releases early, lends staff, reroutes before arrival or uses the Arbiter.

Everything else (seeds, incidents, walk-ins, fleet, hospitals, simulator rules) is identical.

---

## 11. Metrics and evaluation

### 11.1 Metrics

| Group | Metric | Better |
|---|---|---|
| **Primary** | Critical victims on time (%) | Higher |
| Secondary | Unserved critical victims (count) | Lower |
| Secondary | Average report-to-care time (min) | Lower |
| Secondary | ER load balance: busiest peak minus quietest peak | Smaller |
| Efficiency (per hospital) | Idle minutes: utilization under 60% while any victim is waiting anywhere | Lower |
| Efficiency (per hospital) | Overload minutes: patients waiting in ER for staff, or ER at 100% | Lower |
| Efficiency | Wasted hold minutes | Lower |
| Diagnostic | Response time, unit utilization, reroutes, escalations, rejected actions | Explains results |
| Negotiation | Threads, rounds, counters, deals closed without the Arbiter, Arbiter decisions, share of decisions made by rule vs LLM | Shows that negotiation happened |

### 11.2 Method

- Seeds 1 to 20. The baseline runs once per seed (deterministic). The agents run 3 times per seed live; report mean and spread.
- For each metric report: mean baseline vs mean agents, improvement `(agents - baseline) / baseline`, and **win rate** (seeds where agents beat baseline on the primary metric).

### 11.3 Ablations

| Variant | What changes | Shows |
|---|---|---|
| No negotiation | Hospitals answer only accept or decline; no counters, holds, transfers or mutual aid | Value of negotiation itself |
| No Arbiter | Escalations fall back to the least-bad option | Value of the Arbiter |
| Rule hospitals | Hospital agents replaced by "accept if real capacity is free" | Whether hospital agents earn their place |

---

## 12. Reproducibility and LLM use

1. Pin the model version (from configuration) and use temperature 0.
2. **Record and replay:** every LLM call (agent, input, output) is written to a run log. Replay mode reads answers from the log instead of calling the API, so any run can be reproduced exactly, including for the dashboard demo.
3. Every agent output must validate against a Pydantic schema. If it does not, a fallback rule decides, and the fallback is logged.
4. Rules compute all numbers; the LLM only makes judgments.
5. Agents are event-driven, use a fast path when only one option fits, and batch simultaneous requests. Expected scale: roughly 60 to 150 LLM calls per run.

---

## 13. Dashboard (Streamlit, pydeck, Plotly)

The dashboard is the only way the user runs anything: no command line needed. A small launcher script (double-click) starts it.

### 13.1 Controls (sidebar)

| Control | Phase | What it does |
|---|---|---|
| Seed | 1 | Picks the scenario |
| Policy | 1 (greedy), 2+ (agents) | Which decision-maker to run |
| Run simulation | 1 | Runs with a progress bar, saves the run, loads it onto the map |
| New scenario | 1 | Random seed, then runs it |
| Past runs | 1 | Reopens any saved run instantly |
| Play / pause, speed (1x, 5x, 20x), time slider | 1 | Playback |
| Live or replay | 3 | Agents: call the LLM (recorded) or replay a recorded run |
| Compare with baseline | 4 | Baseline and agents side by side on the same seed |
| Run evaluation | 5 | Seeds, repeats and ablations; shows the results table and charts |

### 13.2 Panels

| Panel | Phase | Contents |
|---|---|---|
| Map | 1 | Real street basemap (free, no API key); incidents appear as circles (red with criticals, amber without) sized by victims; units move smoothly along streets (ALS, BLS, teams, hospital vehicles in distinct styles) with their remaining route dashed; hospitals as markers showing ER use |
| Resources | 1 | Units free and busy by type; per hospital: ER, ward, doctors, nurses (and holds and mood from Phase 2) |
| Live events | 1 | Scrolling feed of events at the current minute |
| KPIs | 1 | Metrics from section 11, updating with playback |
| Agent activity | 4 | Each agent's status, recent messages, pending requests, last decision (rule or LLM) |
| Negotiation history | 4 | Threads with rounds, offers, counters, awards, declines with reasons, Arbiter decisions |

All panels read the same recorded run and stay in sync with the playback minute. Replay never needs an API key.

## 14. Worked examples

### 14.1 Bus fire admission (Contract Net)

Bus fire: 2 critical (burns), 4 serious (burns), 2 minor.

```
Dispatch -> all: admission_cfp
H1 (80%, protective): offer: 2 criticals; no seriouses (keeping last slot)
H2 (30%, eager):      offer: can't treat burns; can take the 2 minors
H3 (50%, 1 doctor):   offer: 2 seriouses now, 2 more after a transfer completes
Dispatch -> H1: award 2 criticals
Dispatch -> H3: award 4 seriouses (2 now, 2 after transfer)
Dispatch -> H2: award 2 minors
```

### 14.2 Walk-in breaks a reservation

```
Walk-in critical arrives at H1 -> reservation_broken (A4, 1 serious)
H1 -> Dispatch: capacity_alert
Dispatch: A4's patient is serious, 70 min left; H2 is 18 min from A4
Dispatch -> H2: admission_cfp; H2: accept
Dispatch: reroute(A4, H2)
```

### 14.3 Arbiter swap

```
Critical needs care within 20 min. H1 is 15 min away but full; H2 accepts but is 40 min away.
Dispatch -> Arbiter: escalation (no offer meets the deadline)
Arbiter: H1 has transferable patient X; H2 has space; X's new deadline allows 40 min
Arbiter -> H1: transfer X to H2 in your vehicle; accept the critical
Arbiter -> H2: accept X
H1, H2: ack
Arbiter -> Dispatch: destination H1
```

---

## 15. Assumptions and simplifications

- Road speeds come from OpenStreetMap speed limits (with defaults where missing) times an emergency factor; traffic is ignored. Deadlines, treatment and stabilization times are approximate.
- Hospitals start empty; walk-ins add background load.
- Staff work the whole run (no shifts or breaks).
- Victims do not deteriorate while waiting.
- Severity and victim counts are known when an incident is reported (no estimates).
- No treatment on scene; every victim goes to a hospital.
- Idle units return to base; there is no repositioning for coverage.
- Legal rules (EMTALA) are simplified to the three rules in section 4.3.
- Staff lending between separate hospitals is assumed possible (health system or disaster privileges).

## 16. Postponed ideas

Patient deterioration; treat on scene; staff shifts; estimated vs actual victim counts and information sources (vehicle registry, operator data, 911 callers); a coverage agent for repositioning idle ambulances.

## 17. Build phases

| Phase | Builds | Done when |
|---|---|---|
| **1. World, baseline and live map** | Road network and routing; the world (incidents, hospitals, fleet, patient flow, walk-ins, firm reservations, event log with routes); the greedy baseline; metrics; dashboard with controls, map, resources, events and KPIs | The user clicks Run simulation for seed 42 and watches ambulances drive real streets to incidents and hospitals, with all panels in sync; the same seed always replays identically |
| **2. Agents** | The four agents (rules, fast path, fallback, LLM judgment); LLM wrapper with record and replay; read-only tools; agent-only world actions (holds, early release, transfers, staff and vehicle lending, redirect, reroute) | Each agent returns valid output on hand-built cases with no API key |
| **3. Negotiation** | Messages, bus, time costs, limits, the orchestrator, all protocols in section 5, Arbiter escalation, ablation switches | A live agent run completes from the dashboard and its replay matches exactly |
| **4. Dashboard v2** | Agent activity, negotiation history, baseline vs agents side by side | A recorded agent run plays back with all panels, next to the baseline |
| **5. Evaluation** | Seeds, repeats, ablations, results table and charts, all from a dashboard button | One click produces the final comparison |

Phase 1 deliberately leaves out everything only agents use, so the baseline world is complete and fair before agents arrive.
