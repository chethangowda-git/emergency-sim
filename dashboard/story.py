"""Each incident's story, as numbered plain sentences in time order (DESIGN 13.2), from the logged run.

A story says what happened at the incident: the report and the units needed, which units came and when,
each request to the hospitals with every hospital's reply and reason under it (repeated tries folded into
one line), the referee's decision, any guard step as a plain sentence, warnings, and how each patient
ended. AI decisions carry one small "(AI)" tag; its tooltip has the AI's own words and what the rule would
have chosen. All wording comes from dashboard/wording.py; nothing here calls an LLM.
"""
from __future__ import annotations

from collections import defaultdict
from html import escape

from coordination.choices import rule_ranking_of
from dashboard import wording as W
from dashboard.explain_agents import hospital_chips, rule_choice

OPEN = ("waiting", "assigned", "in_transit", "at_door")
REFEREE = {1: "{h} takes {who}", 2: "{h} sends home a patient who is ready, and takes {who}",
           3: "{h} moves a stable patient to another hospital, and takes {who}", 4: "{h} gives {who} a slot it was holding",
           5: "another patient goes elsewhere, so {h} can take {who}", 6: "{h} borrows staff to take {who}",
           7: "{who} goes to {h}, the least-bad choice"}


class Context:
    """The run up to the minute, with names, decisions by thread and the matched LLM calls."""

    def __init__(self, run: dict, minute: int, index: dict, calls: dict[int, dict]) -> None:
        self.run, self.minute, self.index, self.calls = run, minute, index, calls
        self.events = [e for e in run["events"] if e["t"] <= minute]
        self.severity = {v["id"]: v["original_severity"] for v in run["victims"]}
        self.deadline = {v["id"]: v["deadline"] for v in run["victims"]}

    def patients(self, vids) -> str:
        """'the critical patient', 'the 2 serious patients', 'the first critical patient'."""
        vids = sorted(vids)
        if not vids:
            return "the patients"
        if len(vids) == 1 and "-V" not in vids[0]:
            return "a walk-in patient"
        kinds = defaultdict(list)
        for v in vids:
            kinds[self.severity.get(v, "injured")].append(v)
        parts = []
        for sev in ("critical", "serious", "minor", "injured"):
            vs = kinds.get(sev)
            if vs:
                parts.append(f"{len(vs)} {sev}" if len(vs) > 1 else self.which(vs[0], sev) + sev)
        noun = "patient" if len(vids) == 1 else "patients"
        return f"the {' and '.join(parts)} {noun}"

    def which(self, vid: str, sev: str) -> str:
        iid = vid.split("-V")[0]
        same = sorted(v for v, s in self.severity.items() if v.startswith(iid + "-V") and s == sev)
        order = ("first", "second", "third", "fourth", "fifth")
        i = same.index(vid) if vid in same else 0
        return f"{order[i]} " if len(same) > 1 and i < len(order) else ""

    def ai_tip(self, e: dict | None) -> str | None:
        """The (AI) tag's tooltip: the AI's own words and what the rule would have chosen."""
        if not e or e.get("decided_by") != "llm":
            return None
        parts = [f"The AI's words: “{e['reason']}”" if e.get("reason") and e["reason"] != "rules"
                 else "The AI gave no written reason."]
        call = self.calls.get(e.get("seq"))
        other, differs = rule_choice(e, call)
        if other and differs:
            if e["kind"] == "plan_units":
                other = "ranking " + ", ".join(rule_ranking_of(call["input"]))
            parts.append("Rule would have chosen: " + other.replace("rule: ", "").replace("rules alone: ", "")
                         .replace("rule order: ", "ranking "))
        return " ".join(parts)


def item(t: int, text: str, sub=(), ai: str | None = None, tip: str = "") -> dict:
    return {"t": t, "text": text, "sub": [s for s in sub if s], "ai": ai, "tip": tip}


def sub_line(text: str, ai: str | None = None, tip: str = "") -> dict:
    return {"text": text, "ai": ai, "tip": tip}


# ----- the story of one incident -----

def story(ctx: Context, iid: str) -> list[dict]:
    items = opening(ctx, iid) + units(ctx, iid) + requests(ctx, iid) + warnings(ctx, iid) + outcomes(ctx, iid)
    return sorted(items, key=lambda i: (i["t"], i.get("order", 1)))


def opening(ctx: Context, iid: str) -> list[dict]:
    e = next((e for e in ctx.events if e["type"] == "incident_reported" and e["incident"] == iid), None)
    if e is None:
        return []
    crit = sum(1 for v in e["victims"] if v["severity"] == "critical")
    text = f"Reported at {W.minute_tag(e['t'])}: “{e['description']}”, {W.plural(len(e['victims']), 'patient')}" + \
           (f", {crit} critical" if crit else "") + "."
    assess = next((d for d in ctx.events if d["type"] == "agent_decision" and d["kind"] == "assess"
                   and d.get("incident") == iid), None)
    subs = []
    if assess:
        subs.append(sub_line(f"Needs {W.units_text(assess['units_needed'])}.", ctx.ai_tip(assess)))
    for g in ctx.events:
        if g["type"] == "guard" and g.get("incident") == iid and "suggested" in g:
            subs.append(sub_line(f"The AI asked for {g['suggested'].split()[0]} more paramedic ambulances than the "
                                 f"rules; at most 1 extra is allowed, so 1 was added.", None, g.get("ai_reason") or ""))
    return [{**item(e["t"], text, subs), "order": 0}]


def units(ctx: Context, iid: str) -> list[dict]:
    """The first units sent and any shortage, then the units that came later, in one line each."""
    responses = [m for m in ctx.events if m["type"] == "message" and m["msg_type"] == "unit_response"
                 and m.get("incident_id") == iid]
    if not responses:
        return []
    plans = {e["t"]: e for e in ctx.events if e["type"] == "agent_decision" and e["kind"] == "plan_units"}
    first = responses[0]
    p = first["proposed_action"]
    sent = [W.unit_name(u) for u in p.get("sent", [])]
    short = p.get("shortfall") if p.get("shortfall") and any(p["shortfall"].values()) else None
    if sent and short:
        text = f"Only {join(sent)} {'was' if len(sent) == 1 else 'were'} free; still needed {W.units_text(short)}."
    elif sent:
        text = f"Dispatch sent {join(sent)}."
    else:
        text = f"No unit was free; still needed {W.units_text(short or {})}."
    out = [item(first["t"], text, ai=ctx.ai_tip(plans.get(first["t"])))]
    later = [e for e in ctx.events if e["type"] in ("unit_dispatched", "unit_redirected") and e.get("incident") == iid
             and e["t"] > first["t"]]
    seen = set(p.get("sent", []))
    later = [e for e in later if e["unit"] not in seen and not seen.add(e["unit"])]
    if later:
        arrivals = [f"{W.unit_name(e['unit'])} at {W.minute_tag(e['t'])}"
                    + (f" (taken from {e['from_incident']})" if e["type"] == "unit_redirected" else "") for e in later]
        out.append(item(later[0]["t"], f"More units came later: {join(arrivals)}."))
    return out


def requests(ctx: Context, iid: str) -> list[dict]:
    """Each set of patients: the first request with every hospital's reply, then the tries after it."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for th in sorted(ctx.index.values(), key=lambda th: (th["messages"][0]["t"], th["id"])):
        ms = [m for m in th["messages"] if m["t"] <= ctx.minute]
        if not ms or th["protocol"] not in ("Admission", "Reroute"):
            continue
        vids = tuple(sorted({v for m in ms for v in m.get("victim_ids") or [] if v.startswith(iid + "-")}))
        if vids:
            groups[vids].append({"th": th, "ms": ms})
    return [request_item(ctx, vids, tries) for vids, tries in groups.items()] + \
        [r for vids, tries in groups.items() for r in referee_items(ctx, vids, tries)]


def request_item(ctx: Context, vids: tuple, tries: list[dict]) -> dict:
    who = ctx.patients(vids)
    first = tries[0]["ms"]
    asked = sorted({m["recipient"] for m in first if m["msg_type"] in ("admission_cfp", "admission_counter")})
    text = (f"Dispatch asked {join([W.place(h) for h in asked])} to take {who}." if asked
            else f"Dispatch looked for another hospital for {who}.")
    chips = [c for c in hospital_chips([m for m in first if m["round"] == 1])
             if c["status"] not in ("awarded", "accepted", "referee")]
    subs = [sub_line("No answers yet.")] if chips and all(c["status"] == "waiting" for c in chips) \
        else [reply(ctx, first, c) for c in chips]
    rounds = sum(max(m["round"] for m in t["ms"]) for t in tries) - 1
    agreed = next(((t, h) for t in tries if (h := agreed_hospital(t["ms"]))), None)
    if rounds and not agreed:
        subs.append(sub_line(f"Dispatch asked again {times(rounds)}; still no hospital agreed."))
    elif rounds and agreed and (agreed[0] is not tries[0] or max(m["round"] for m in first) > 1):
        subs.append(sub_line(f"Dispatch asked again {times(rounds)}; then {W.place(agreed[1])} agreed."))
    award = next((e for t in tries for e in t["th"]["decisions"] if e["kind"] == "award" and e["awards"]), None)
    if agreed:
        subs.append(sub_line(f"{W.place(agreed[1])} reserved a slot.", ctx.ai_tip(award)))
    subs += guard_lines(ctx, tries) + transfer_lines(ctx, tries)
    return item(first[0]["t"], text, subs, tip=f"{', '.join(vids)} · {', '.join(t['th']['id'] for t in tries)}")


def reply(ctx: Context, ms: list[dict], chip: dict) -> dict:
    h = W.place(chip["hospital"])
    offer = next((m for m in ms if m["sender"] == chip["hospital"] and m["msg_type"] == "admission_offer"), None)
    decision = next((e for e in ctx.events if e["type"] == "agent_decision" and e["kind"] == "admission_offer"
                     and offer and e.get("thread") == offer["thread_id"] and e["agent"] == chip["hospital"]), None)
    ai = ctx.ai_tip(decision)
    if chip["status"] == "offered":
        return sub_line(f"{h} said yes.", ai)
    if chip["status"] == "offered later":
        return sub_line(f"{h} offered to take them after moving a patient out.", ai)
    if chip["status"] == "cant":
        return sub_line(f"{h} said no: it can't treat this injury.", ai)
    if chip["status"] == "waiting":
        return sub_line(f"{h} has not answered yet.")
    by_ai = decision is not None and decision.get("decided_by") == "llm" and chip["reason"] == (offer or {}).get("reason")
    why = "" if by_ai or not chip["reason"] else short_reason(W.reason_phrase(chip["reason"]))
    return sub_line(f"{h} said no" + (f": {why}." if why else "."), ai if by_ai else None)


def short_reason(text: str) -> str:
    """'there is no free room right now; it is nearly full' -> 'its ER is full'."""
    if text.startswith("there is no free room"):
        return "its ER is full"
    if text.startswith("it keeps its last slots"):
        return "it keeps its last beds for critical patients"
    return text.split(";")[0]


def agreed_hospital(ms: list[dict]) -> str | None:
    return next((c["hospital"] for c in hospital_chips(ms) if c["status"] == "accepted"), None)


def guard_lines(ctx: Context, tries: list[dict]) -> list[dict]:
    threads = {t["th"]["id"] for t in tries}
    guards = [e for e in ctx.events if e["type"] == "guard" and e.get("thread") in threads]
    out = []
    if any("withheld" in e for e in guards):
        out.append(sub_line("The AI suggested waiting for a transfer, but none was possible, so the usual rule was used."))
    if any("victim" in e and "hospital" in e for e in guards):
        out.append(sub_line("A hospital offered to take them after a transfer, but that would have been too late, "
                            "so it did not count."))
    return out


def transfer_lines(ctx: Context, tries: list[dict]) -> list[dict]:
    out = []
    for t in tries:
        for award in (m for m in t["ms"] if m["msg_type"] == "admission_award" and m["proposed_action"].get("conditional")):
            for tr in ctx.index.values():
                first = tr["messages"][0]
                if tr["protocol"] == "Transfer" and first["sender"] == award["recipient"] and award["t"] <= first["t"] <= award["t"] + 2:
                    took = agreed_hospital([m for m in tr["messages"] if m["t"] <= ctx.minute]) or \
                        next((c["hospital"] for c in hospital_chips(tr["messages"]) if c["status"] == "accepted"), None)
                    out.append(sub_line(f"{W.place(first['sender'])} tried to move a stable patient out to make room"
                                        + (f"; {W.place(took)} took them." if took else "; no hospital could take them.")))
    return out[:2]


def referee_items(ctx: Context, vids: tuple, tries: list[dict]) -> list[dict]:
    out = []
    for t in tries:
        for esc in (m for m in t["ms"] if m["msg_type"] == "escalation"):
            ruling = next((m for m in t["ms"] if m["msg_type"] == "arbiter_decision" and m["seq"] > esc["seq"]), None)
            victim = (esc.get("victim_ids") or [None])[0]
            who = ctx.patients([victim])
            deadline = ctx.deadline.get(victim)
            left = f"With {deadline - esc['t']} min left before the deadline, " if deadline is not None else ""
            if ruling is None:
                out.append(item(esc["t"], f"No hospital agreed, so Dispatch asked the referee to decide for {who}."))
                continue
            p = ruling["proposed_action"]
            h = W.place(p["hospital"]) if p.get("hospital") else "no hospital"
            decided = REFEREE.get(p.get("option"), "{who} goes to {h}").format(h=h, who=who)
            late = "" if p.get("meets_deadline") else ", even though it will be late"
            why = W.TRIGGERS.get(esc.get("reason"), "the agents could not agree")
            out.append(item(esc["t"], f"No hospital agreed in time, so the referee decided: {decided}{late}.",
                            [sub_line(W.cap(f"{left}the referee was called because {why}."))],
                            tip=f"option {p.get('option')} of 7"))
    return out


def warnings(ctx: Context, iid: str) -> list[dict]:
    out = []
    for e in ctx.events:
        if e["type"] not in ("reservation_broken", "arrived_no_slot"):
            continue
        victims = e.get("victims") or ([e["victim"]] if e.get("victim") else [])
        mine = [v for v in victims if v.startswith(iid + "-")]
        if not mine:
            continue
        if e["type"] == "reservation_broken":
            out.append(item(e["t"], f"A walk-in patient took the slot {W.place(e['hospital'])} had kept for "
                                    f"{ctx.patients(mine)}, so Dispatch looked again."))
        elif e["type"] == "arrived_no_slot":
            out.append(item(e["t"], f"{W.cap(W.unit_name(e['unit']))} reached {W.place(e['hospital'])}, but there was no slot."))
    return out


def outcomes(ctx: Context, iid: str) -> list[dict]:
    """How each patient ended: patients reaching the same hospital in the same minute share one sentence."""
    inc = next((i for i in ctx.run["timeline"][ctx.minute]["incidents"] if i["id"] == iid), None)
    if inc is None:
        return []
    arrived = {e["victim"]: e for e in ctx.events if e["type"] == "patient_delivered"}
    groups: dict[tuple, list[str]] = defaultdict(list)
    out = []
    for v in inc["victims"]:
        e = arrived.get(v["id"])
        if e:
            on_time = v["deadline"] is None or e["t"] <= v["deadline"]
            groups[(e["t"], e["hospital"], on_time)].append(v["id"])
        elif v["deadline"] is not None and ctx.minute > v["deadline"]:
            out.append({**item(v["deadline"], f"{W.cap(ctx.patients([v['id']]))} missed the deadline at "
                                              f"{W.minute_tag(v['deadline'])}.", tip=v["id"]), "order": 2})
    for (t, h, on_time), vids in sorted(groups.items()):
        out.append({**item(t, f"{W.cap(ctx.patients(vids))} reached {W.place(h)} at {W.minute_tag(t)}. "
                              f"{'On time.' if on_time else 'Late.'}", tip=", ".join(vids)), "order": 2})
    return out


def now_line(ctx: Context, iid: str) -> str:
    """Where the incident's patients are now, while it is still open."""
    inc = next((i for i in ctx.run["timeline"][ctx.minute]["incidents"] if i["id"] == iid), None)
    if inc is None:
        return ""
    units = {u["id"]: u for u in ctx.run["timeline"][ctx.minute]["units"]}
    where = defaultdict(int)
    for v in inc["victims"]:
        if v["status"] in ("waiting", "assigned"):
            where["still at the scene"] += 1
        elif v["status"] == "in_transit":
            u = units.get(v.get("unit") or "")
            where[f"on the way to {W.place(u['hospital'])}" if u and u.get("hospital") else "on the way to hospital"] += 1
        elif v["status"] == "at_door":
            where["waiting at a hospital door"] += 1
    return ("Now: " + ", ".join(f"{W.plural(n, 'patient')} {place}" for place, n in where.items()) + ".") if where else ""


# ----- the incident list -----

def entries(run: dict, minute: int) -> list[dict]:
    """One line per reported incident: active first (least time left first), finished ones after."""
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
    return sorted(out, key=lambda e: (not e["active"], e["left"], e["id"]))


# ----- html -----

def ai_tag(tip: str | None) -> str:
    return f" <span class='ai-tag' title='{escape(tip, quote=True)}'>(AI)</span>" if tip else ""


def story_html(items: list[dict], now: str = "") -> str:
    rows = []
    for n, it in enumerate(items, 1):
        subs = "".join(f"<div class='st-sub' title='{escape(s['tip'], quote=True)}'>{escape(s['text'])}{ai_tag(s['ai'])}</div>"
                       for s in it["sub"])
        rows.append(f"<li title='{escape(it['tip'], quote=True)}'>{escape(it['text'])}{ai_tag(it['ai'])}{subs}</li>")
    tail = f"<div class='st-now'>{escape(now)}</div>" if now else ""
    return f"<ol class='story'>{''.join(rows)}</ol>{tail}" if rows else "<i>Nothing has happened here yet.</i>"


def join(parts: list[str]) -> str:
    parts = list(parts)
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1] if parts else ""


def times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")
