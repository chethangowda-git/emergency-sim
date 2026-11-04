"""Each incident's conversation between the agents, in time order (DESIGN 13.2), from the logged run.

One line per message: sender → recipient(s), what was said, and the reason in the same sentence. A message
sent to several hospitals is one line. World events, guard steps and repeated rounds are short grey
narrator lines starting with "·". "(AI)" after a line means the sender's own AI decided it; its tooltip
has the AI's exact words. Transfers and staff loans go under the incident of the patient they help;
the rest go to "Between hospitals". All wording comes from dashboard/wording.py; nothing calls an LLM.
"""
from __future__ import annotations

from collections import defaultdict
from html import escape

from dashboard import wording as W
from dashboard.explain_agents import hospital_chips, rule_choice
from dashboard.names import hospital_name

BETWEEN = "between"
OPEN = ("waiting", "assigned", "in_transit", "at_door")
REFEREE = {1: "{h} takes {who}", 2: "{h} sends home a patient who is ready, and takes {who}",
           3: "{h} moves a stable patient to another hospital, and takes {who}", 4: "{h} gives {who} a slot it was holding",
           5: "another patient goes elsewhere, so {h} can take {who}", 6: "{h} borrows staff to take {who}",
           7: "{who} goes to {h}, the least-bad choice"}
AGENTS = {"ia": "Incident Assessment", "dispatch": "Dispatch", "arbiter": "Referee"}
NARRATED = ("unit_on_scene", "scene_cleared", "transport_started", "arrived_no_slot", "reservation_broken",
            "patient_delivered", "deadline_missed", "hold_placed", "unit_redirected", "transfer_started",
            "transfer_completed")


class Context:
    def __init__(self, run: dict, minute: int, index: dict, calls: dict[int, dict], roads=None) -> None:
        self.run, self.minute, self.index, self.calls, self.roads = run, minute, index, calls, roads
        self.events = [e for e in run["events"] if e["t"] <= minute]
        self.severity = {v["id"]: v["original_severity"] for v in run["victims"]}
        self.deadline = {v["id"]: v["deadline"] for v in run["victims"]}
        self.hospital_nodes = {h["id"]: h.get("node") for h in run["timeline"][0].get("hospitals", [])}
        self.decisions = defaultdict(list)          # (agent, minute) -> decisions, to find the AI's own words
        for e in self.events:
            if e["type"] == "agent_decision":
                self.decisions[(e["agent"], e["t"])].append(e)

    def patients(self, vids) -> str:
        """'the critical patient', 'the second critical patient', '2 minor patients', 'a serious and a minor patient'."""
        vids = sorted(v for v in vids if v)
        if not vids:
            return "the patients"
        if len(vids) == 1:
            if "-V" not in vids[0]:
                return "a walk-in patient"
            sev = self.severity.get(vids[0], "injured")
            return f"the {self.which(vids[0], sev)}{sev} patient"
        kinds = defaultdict(list)
        for v in vids:
            kinds[self.severity.get(v, "injured")].append(v)
        parts = [(f"{len(vs)} {sev}", "patients") if len(vs) > 1 else (f"a {sev}", "patient")
                 for sev in ("critical", "serious", "minor", "injured") if (vs := kinds.get(sev))]
        if len(parts) == 1:
            return " ".join(parts[0])
        if all(n == "patient" for _, n in parts):
            return " and ".join(a for a, _ in parts) + " patient"
        return " and ".join(" ".join(x) for x in parts)

    def which(self, vid: str, sev: str) -> str:
        iid = vid.split("-V")[0]
        same = sorted(v for v, s in self.severity.items() if v.startswith(iid + "-V") and s == sev)
        order = ("first", "second", "third", "fourth", "fifth")
        i = same.index(vid) if vid in same else 0
        return f"{order[i]} " if len(same) > 1 and i < len(order) else ""

    def ai_words(self, m: dict) -> str | None:
        """The tooltip of an (AI) tag: the AI's exact words, and what the rule would have chosen."""
        p = m.get("proposed_action") or {}
        if m["msg_type"] in ("unit_request", "incident_update") and p.get("decided_by") == "llm":
            read = "; ".join([*p.get("flags", []), *([p["note"]] if p.get("note") else [])])
            return f"Incident Assessment's AI read the description: “{read}”" if read else \
                "Incident Assessment's AI read the description (no written note)."
        if m.get("decided_by") != "llm":
            return None
        tip = f"{name(m['sender'])}'s AI: “{m['reason']}”" if m.get("reason") and m["reason"] != "rules" \
            else f"{name(m['sender'])}'s AI decided this (no written reason)."
        decision = next((e for e in self.decisions[(m["sender"], m["t"])] if e["decided_by"] == "llm"), None)
        other, differs = rule_choice(decision, self.calls.get(decision["seq"])) if decision else (None, False)
        if other and differs:
            tip += " Rule would have chosen: " + other.replace("rule: ", "").replace("rules alone: ", "") \
                .replace("rule order: ", "ranking ")
        return tip

    def arrives_in(self, m: dict) -> str:
        """'Arrives in about 10 min' for a request: ready to leave, plus the fastest drive to the asked hospitals."""
        p = m.get("proposed_action") or {}
        if self.roads is None or not p.get("origin_node") or p.get("aboard"):
            return ""
        drives = [self.roads.minutes(p["origin_node"], self.hospital_nodes[h]) for h in m["_to"] if h in self.hospital_nodes]
        if not drives:
            return ""
        return f" Arrives in about {max(0, p.get('ready_at', m['t']) - m['t']) + min(drives)} min."


def name(agent: str) -> str:
    return AGENTS.get(agent, agent)


def who_html(agent: str) -> str:
    full = hospital_name(agent) if agent.startswith("H") else AGENTS.get(agent, agent)
    return f"<span title='{escape(full, quote=True)}'>{escape(name(agent))}</span>"


def line(t: int, sender: str, to: list[str], text: str, ai: str | None = None, tip: str = "", type: str = "") -> dict:
    return {"t": t, "kind": "msg", "sender": sender, "to": to, "text": text, "ai": ai, "tip": tip, "type": type}


def narrator(t: int, text: str, tip: str = "", order: int = 1) -> dict:
    return {"t": t, "kind": "narr", "text": text, "tip": tip, "order": order}


# ----- what each message says -----

def says(ctx: Context, m: dict) -> str:
    p, k = m.get("proposed_action") or {}, m["msg_type"]
    who = ctx.patients(m.get("victim_ids") or [])
    if k in ("unit_request", "incident_update"):
        return (("Update: " if k == "incident_update" else "") + f"{m['incident_id']} needs {W.units_text(p['units_needed'])}; "
                f"{W.plural(p['at_risk'], 'patient')} at risk, {p['minutes_left']} min left.")
    if k == "unit_response":
        sent = f"Sending {W.units_join(p['sent'])}." if p.get("sent") else "No unit is free right now."
        short = p.get("shortfall") if p.get("shortfall") and any(p["shortfall"].values()) else None
        return sent + (f" Still short of {W.units_text(short)}; I'll {W.plan_text(p.get('plan'))}." if short else "")
    if k in ("admission_cfp", "admission_counter"):
        return ("Asking again: c" if k == "admission_counter" else "C") + f"an you take {who}?" + ctx.arrives_in(m)
    if k == "admission_offer":
        return offer_text(ctx, m, p)
    if k == "admission_award":
        when = award_arrival(ctx, m)
        return f"Please take {who}" + (f"; they arrive at {W.minute_tag(when)}" if when else "") + \
            (", once your transfer is done." if p.get("conditional") else ".")
    if k == "ack":
        return f"Done, I've reserved a slot for {who}." if m["recipient"] == "dispatch" else "Thanks, agreed."
    if k == "decline":
        why = W.my_reason(m.get("reason"))
        return f"Sorry, I can't keep the slot for {who}" + (f": {why}." if why else ".")
    if k == "escalation":
        victim = (m.get("victim_ids") or [None])[0]
        left = ctx.deadline.get(victim)
        left = f" ({left - m['t']} min left)" if left is not None else ""
        return f"Please decide for {who}: {W.TRIGGERS.get(m.get('reason'), 'we could not agree')}{left}."
    if k == "arbiter_decision":
        h = p.get("hospital")
        decided = REFEREE.get(p.get("option"), "{who} goes to {h}").format(h=h or "no hospital", who=ctx.patients([p.get("victim")]))
        late = "" if p.get("meets_deadline") else ", even though it will be late"
        return f"My ruling: {decided}, arriving {W.minute_tag(p.get('arrival'))}{late}."
    if k == "transfer_request":
        return f"Can you take one of my patients, stable now ({p.get('care_need', 'general')} care), so I can make room?"
    if k == "transfer_response":
        return "Yes, I'll take them." if p.get("accept") else "No" + (f": {W.my_reason(m.get('reason'))}." if W.my_reason(m.get("reason")) else ".")
    if k == "capacity_alert":
        return f"A walk-in took the slot I had kept for {who}."
    if k == "pre_alert":
        return f"Heads-up: {who} is waiting for an ambulance; can you hold a slot?"
    if k in ("staff_offer", "mutual_aid_offer", "swap_response"):
        why = W.my_reason(m.get("reason"))
        return {"send_now": "Yes, sending now.", "send_later": f"Yes, later, at {W.minute_tag(p.get('at_minute'))}."
                }.get(p.get("answer"), "No" + (f": {why}." if why else ", I can't."))
    return W.message_sentence(m, ctx.severity)


def offer_text(ctx: Context, m: dict, p: dict) -> str:
    cond = [v for c in p.get("conditional", []) for v in c["victims"]]
    releases = any(a.get("action") == "release_patient" for a in p.get("proposed_actions", []))
    if p.get("accept"):
        text = f"Yes, I can take {ctx.patients(p['accept'])} now" + (", after sending home a patient who is ready." if releases else ".")
        return text + (f" {ctx.patients(cond).capitalize()} only once I move a stable patient out." if cond else "")
    if cond:
        return f"I can take {ctx.patients(cond)} once I move a stable patient out."
    reasons = [d["reason"] for d in p.get("decline", [])]
    why = W.my_reason(reasons[-1]) if reasons else W.my_reason(m.get("reason"))
    return "No" + (f", {why}." if why else ", I can't take anyone right now.")


def award_arrival(ctx: Context, m: dict) -> int | None:
    d = next((e for e in ctx.decisions[("dispatch", m["t"])] if e["kind"] == "award"
              and any(a["hospital"] == m["recipient"] for a in e.get("awards", []))), None)
    return next((a["arrival"] for a in d["awards"] if a["hospital"] == m["recipient"]), None) if d else None


# ----- one thread as lines -----

def thread_lines(ctx: Context, th: dict) -> list[dict]:
    """The thread's messages as lines, merging one message to several recipients and folding repeated rounds."""
    ms = [m for m in th["messages"] if m["t"] <= ctx.minute]
    merged = []
    for m in ms:
        kind = "admission_cfp" if m["msg_type"] == "pre_alert" and th["protocol"] == "Admission" else m["msg_type"]
        key = (m["t"], m["sender"], m["round"], kind, tuple(m.get("victim_ids") or []))
        same = next((x for x in merged[-8:] if x["key"] == key), None)
        if same is None:
            same = {"key": key, "m": m, "to": [], "round": m["round"]}
            merged.append(same)
        if m["msg_type"] != "pre_alert" or th["protocol"] != "Admission":
            same["m"] = m
        if m["recipient"] not in same["to"]:
            same["to"].append(m["recipient"])
    return fold_rounds(ctx, th, merged)


def round_signature(group: list[dict]) -> tuple:
    return tuple(sorted((x["m"]["sender"], x["m"]["msg_type"], says_key(x["m"])) for x in group))


def says_key(m: dict) -> str:
    p = m.get("proposed_action") or {}
    accept = p.get("accept", [])
    return f"{sorted(accept) if isinstance(accept, list) else accept}{bool(p.get('conditional'))}{m.get('reason')}"


def fold_rounds(ctx: Context, th: dict, merged: list[dict]) -> list[dict]:
    """Rounds that repeat the round before (same request, same replies) become one narrator line."""
    by_round = defaultdict(list)
    for x in merged:
        by_round[x["round"]].append(x)
    out, last, repeats = [], None, []
    unit = next((x["m"]["proposed_action"]["unit"] for x in merged if (x["m"].get("proposed_action") or {}).get("unit")), None)
    for r in sorted(by_round):
        group = by_round[r]
        sig = round_signature(group)
        if sig == last:
            repeats.append(group[0]["m"]["t"])
            continue
        if repeats:
            out.append(repeat_line(repeats))
            repeats = []
        last = sig
        for x in group:
            m = {**x["m"], "_to": x["to"]}
            out.append(line(m["t"], m["sender"], x["to"], says(ctx, m), ctx.ai_words(m),
                            tip=", ".join(m.get("victim_ids") or []) + f" · {th['id']}", type=m["msg_type"]))
            out[-1]["unit"] = unit
    if repeats:
        out.append(repeat_line(repeats))
    return fold_waits(out)


def fold_waits(lines: list[dict]) -> list[dict]:
    """Dispatch's 'no unit is free' replies after the first, while still short of the same units, become one line."""
    out, waits, last = [], [], None
    for l in lines:
        idle = l.get("type") == "unit_response" and l["text"].startswith("No unit is free")
        short = l["text"].split("Still short of ")[-1].split(";")[0] if idle else None
        if idle and short == last and not l.get("ai"):
            waits.append(l["t"])
            continue
        if waits:
            out.append(wait_line(waits))
            waits = []
        last = short if idle else (None if l.get("type") == "unit_response" else last)
        out.append(l)
    if waits:
        out.append(wait_line(waits))
    return out
    return out


def wait_line(times: list[int]) -> dict:
    at = ", ".join(str(t) for t in times[:-1]) + (" and " if len(times) > 1 else "") + str(times[-1])
    return narrator(times[-1], f"Dispatch checked again at min {at}: still no free unit.")


def repeat_line(times: list[int]) -> dict:
    n = {1: "once more", 2: "twice more"}.get(len(times), f"{len(times)} more times")
    return narrator(times[-1], f"Dispatch asked {n}; still no hospital agreed.")


# ----- which threads and events belong to an incident -----

def threads_of(ctx: Context, iid: str) -> list[dict]:
    out = []
    for th in ctx.index.values():
        ms = th["messages"]
        mine = any(m.get("incident_id") == iid or any(v.startswith(iid + "-") for v in m.get("victim_ids") or [])
                   for m in ms)
        if mine and th["protocol"] not in ("Transfer", "Staff loan"):
            out.append(th)
    return out + linked_transfers(ctx, out)


def linked_transfers(ctx: Context, threads: list[dict]) -> list[dict]:
    """Transfers a hospital started to make room right after a conditional award in these threads."""
    out = []
    for th in threads:
        for award in (m for m in th["messages"] if m["msg_type"] == "admission_award"
                      and m["proposed_action"].get("conditional")):
            for tr in ctx.index.values():
                first = tr["messages"][0]
                if tr["protocol"] == "Transfer" and first["sender"] == award["recipient"] and \
                        award["t"] <= first["t"] <= award["t"] + 2 and tr not in out:
                    out.append(tr)
    return out


def between_threads(ctx: Context, incidents: list[str]) -> list[dict]:
    """Staff loans and transfers that help no incident's patient."""
    linked = {tr["id"] for iid in incidents for tr in threads_of(ctx, iid) if tr["protocol"] == "Transfer"}
    return [th for th in ctx.index.values() if th["protocol"] in ("Transfer", "Staff loan") and th["id"] not in linked]


def narrated(ctx: Context, iid: str) -> list[dict]:
    out = []
    for e in ctx.events:
        if e["type"] not in NARRATED:
            continue
        victims = e.get("victims") if isinstance(e.get("victims"), list) else []
        vids = [v for v in victims if isinstance(v, str)] + ([e["victim"]] if e.get("victim") else [])
        if e.get("incident") == iid or any(v.startswith(iid + "-") for v in vids):
            out.append(narrator(e["t"], world_line(ctx, e, vids), ", ".join(vids), order=2))
    return out


def world_line(ctx: Context, e: dict, vids: list[str]) -> str:
    k, who = e["type"], ctx.patients(vids)
    unit = W.cap(W.unit_name(e["unit"])) if e.get("unit") else ""
    if k == "patient_delivered":
        v = vids[0]
        on_time = ctx.deadline.get(v) is None or e["t"] <= ctx.deadline[v]
        return f"{W.cap(who)} reached {e['hospital']}{'' if ctx.deadline.get(v) is None else ', on time' if on_time else ', late'}."
    return {"unit_on_scene": f"{unit} reached {e.get('incident')}.",
            "scene_cleared": f"{e.get('incident')} was made safe.",
            "transport_started": f"{unit} left for {e.get('hospital')} with {who}.",
            "arrived_no_slot": f"{unit} reached {e.get('hospital')}, but there was no slot.",
            "reservation_broken": f"A walk-in took the slot {e.get('hospital')} had kept for {who}.",
            "deadline_missed": f"{W.cap(who)} missed the deadline.",
            "hold_placed": f"{e.get('hospital')} is holding a slot for {who}.",
            "unit_redirected": f"{unit} was taken from {e.get('from_incident')} to {e.get('incident')}.",
            "transfer_started": f"{W.cap(who)} is moving from {e.get('from_hospital')} to {e.get('to_hospital')}.",
            "transfer_completed": f"{W.cap(who)} arrived at {e.get('hospital')}.",
            }[k]


def guard_lines(ctx: Context, threads: list[dict], iid: str | None) -> list[dict]:
    """Guard steps (DESIGN 12.8) as narrator lines naming the agent; same-minute blocks of the same kind merged."""
    ids = {th["id"] for th in threads}
    groups: dict[tuple, list[dict]] = {}
    for e in ctx.events:
        if e["type"] == "guard" and (e.get("thread") in ids or (iid and e.get("incident") == iid and "suggested" in e)):
            groups.setdefault((e["t"], "suggested" in e, "withheld" in e, e["why"]), []).append(e)
    out = []
    for (t, suggested, withheld, why), es in groups.items():
        e = es[0]
        if suggested:
            text = (f"The guard cut Incident Assessment's AI request from {e['suggested'].split()[0]} extra paramedic "
                    f"ambulances to {e['allowed'].split()[0]}.")
        elif withheld:
            agents = W.join_words(sorted({x["agent"] for x in es}))
            text = f"The guard kept the option “after a transfer” from the AI of {agents}: {why}."
        else:
            text = (f"The guard stopped Dispatch's AI counting {e['hospital']}'s offer to take "
                    f"{ctx.patients([e['victim']])} after a transfer: {why}.")
        out.append(narrator(t, text, e.get("ai_reason") or "", order=-1))
    return out


# ----- the conversation of one incident (or the between-hospitals entry) -----

def conversation(ctx: Context, iid: str, incidents: list[str] | None = None) -> list[dict]:
    if iid == BETWEEN:
        return fold_repeated_threads([thread_lines(ctx, th) for th in between_threads(ctx, incidents or [])])
    threads = threads_of(ctx, iid)
    lines = [l for th in threads for l in thread_lines(ctx, th)] + guard_lines(ctx, threads, iid) + narrated(ctx, iid)
    tell_apart(lines)
    alerts = {l["t"] for l in lines if l.get("type") == "capacity_alert"}
    lines = [l for l in lines if not (l["kind"] == "narr" and l["t"] in alerts and l["text"].startswith("A walk-in took"))]
    return sorted(lines, key=lambda l: (l["t"], {"msg": 0}.get(l["kind"], l.get("order", 1))))


def tell_apart(lines: list[dict]) -> None:
    """Two loads of the same incident can read the same; then each line says which ambulance's patients it is about."""
    seen = defaultdict(list)
    for l in lines:
        if l["kind"] == "msg":
            seen[(l["t"], l["sender"], tuple(l["to"]), l["text"])].append(l)
    for same in seen.values():
        if len(same) > 1:
            for l in same:
                if l.get("unit"):
                    l["text"] = l["text"].rstrip(".?") + f" ({l['unit']}'s patients)" + l["text"][-1]


def fold_repeated_threads(threads: list[list[dict]]) -> list[dict]:
    """A request that gets the same answers as the same hospital's last one becomes a narrator line."""
    last: dict[str, tuple] = {}
    again: dict[str, list[int]] = defaultdict(list)
    out = []
    for lines in sorted((ls for ls in threads if ls), key=lambda ls: (ls[0]["t"], ls[0]["sender"])):
        sender = lines[0]["sender"]
        sig = tuple((l["sender"], tuple(l["to"]), l["text"]) for l in lines)
        if last.get(sender) == sig:
            again[sender].append(lines[0]["t"])
            continue
        if again[sender]:
            out.append(asked_again(sender, again.pop(sender)))
        last[sender] = sig
        out.extend(lines)
    out += [asked_again(s, ts) for s, ts in again.items() if ts]
    return sorted(out, key=lambda l: (l["t"], 0 if l["kind"] == "msg" else 1))


def asked_again(sender: str, times: list[int]) -> dict:
    at = ", ".join(str(t) for t in times[:-1]) + (" and " if len(times) > 1 else "") + str(times[-1])
    return narrator(times[-1], f"{name(sender)} asked the same again at min {at}; the answers were the same.")


def entries(run: dict, minute: int, has_between: bool) -> list[dict]:
    """One line per incident, active first (least time left first), finished collapsed; then 'Between hospitals'."""
    out = []
    for inc in run["timeline"][minute]["incidents"]:
        open_v = [v for v in inc["victims"] if v["status"] in OPEN]
        left = min((v["deadline"] - minute for v in open_v if v["deadline"] is not None), default=None)
        crit = sum(1 for v in open_v if v["original_severity"] == "critical")
        if open_v:
            label = W.incident_name(inc) + (f" · {crit} critical" if crit else "") + \
                ("" if left is None else f" · {left} min left" if left >= 0 else f" · {-left} min past the deadline")
        else:
            label = f"✓ {W.incident_name(inc)} · everyone at a hospital"
        out.append({"id": inc["id"], "label": label, "active": bool(open_v), "left": left if left is not None else 10**6})
    out.sort(key=lambda e: (not e["active"], e["left"], e["id"]))
    if has_between:
        out.append({"id": BETWEEN, "label": "Between hospitals · staff loans and transfers", "active": False,
                    "left": 10**6})
    return out


def conversation_html(lines: list[dict]) -> str:
    if not lines:
        return "<div class='cv-n'>· Nothing has been said here yet.</div>"
    rows = []
    for l in lines:
        tip = f" title='{escape(l['tip'], quote=True)}'" if l.get("tip") else ""
        if l["kind"] == "narr":
            rows.append(f"<div class='cv-n'{tip}>· {escape(l['text'])}</div>")
            continue
        to = ", ".join(who_html(r) for r in l["to"])
        ai = f" <span class='ai-tag' title='{escape(l['ai'], quote=True)}'>(AI)</span>" if l.get("ai") else ""
        rows.append(f"<div class='cv'{tip}><span class='cv-t'>{W.minute_tag(l['t'])}</span> "
                    f"<b>{who_html(l['sender'])} → {to}</b>: {escape(l['text'])}{ai}</div>")
    return "<div class='convo'>" + "".join(rows) + "</div>"
