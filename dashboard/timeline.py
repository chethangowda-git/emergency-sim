"""One timeline per incident for the Negotiations tab (DESIGN 13.2), from the logged run.

Steps, in time order: the report, Incident Assessment's call and units needed, Dispatch's units and any
shortage, each admission request with every hospital's reply grouped under it (repeated tries for the
same victims numbered Attempt 1, 2, 3), guard steps, escalations with the referee's ruling (which of its
7 options, how much time was left), related transfers, warnings, and each victim's outcome. Every step
has a coloured dot (red refused or short, purple referee, green settled), a badge (Rule, AI, Guard
blocked, Fallback), its full messages for expanding, and for AI steps what the rule would have chosen.
"""
from __future__ import annotations

from collections import defaultdict
from html import escape

from coordination.choices import rule_ranking_of
from dashboard import wording as W
from dashboard.explain_agents import bubbles_of, hospital_chips, rule_choice

TRANSFER_REPLY = {"accepted": ("green", "said yes"), "declined": ("red", "said no"), "waiting": ("grey", "no answer yet"),
                  "offered": ("green", "said yes"), "later": ("amber", "said later")}
DOTS = {"red": "🔴", "purple": "🟣", "green": "🟢", "grey": "⚪", "blue": "🔵", "amber": "🟠"}
REPLY = {"offered": ("green", "can take them now"), "offered later": ("amber", "can take them after moving a patient out"),
         "cant": ("red", "can't treat this injury"), "declined": ("red", "no"), "waiting": ("grey", "no answer yet"),
         "awarded": ("green", "was chosen"), "accepted": ("green", "confirmed, slot reserved"),
         "referee": ("purple", "chosen by the referee"), "later": ("amber", "later"), "": ("grey", "")}


def step(t, dot, title, badge="rule", lines=(), messages=(), rule=None, tip="") -> dict:
    return {"t": t, "dot": dot, "title": title, "badge": badge, "lines": list(lines), "messages": list(messages),
            "rule": rule, "tip": tip}


class Context:
    """What every builder needs: the run so far, names, decisions by thread and the matched LLM calls."""

    def __init__(self, run: dict, minute: int, index: dict, calls: dict[int, dict]) -> None:
        self.run, self.minute, self.index, self.calls = run, minute, index, calls
        self.events = [e for e in run["events"] if e["t"] <= minute]
        self.severity = {v["id"]: v["original_severity"] for v in run["victims"]}
        self.deadline = {v["id"]: v["deadline"] for v in run["victims"]}

    def which(self, vids: list[str]) -> str:
        """' (2 of 2)' when the incident has several victims this phrase would describe the same way."""
        if len(vids) != 1 or "-V" not in vids[0]:
            return ""
        iid, sev = vids[0].split("-V")[0], self.severity.get(vids[0])
        same = sorted(v for v, s in self.severity.items() if v.startswith(iid + "-V") and s == sev)
        return f" ({same.index(vids[0]) + 1} of {len(same)})" if len(same) > 1 else ""

    def why(self, e: dict) -> str:
        if e.get("kind") == "assess" and e.get("decided_by") == "llm" and e.get("reason") == "rules":
            return "The AI gave no written reason."
        return W.because(e.get("reason"), e.get("decided_by"))

    def rule_of(self, e: dict) -> str | None:
        other, differs = rule_choice(e, self.calls.get(e["seq"]))
        return other.replace("rule order: ", "ranking ").replace("rule: ", "").replace("rules alone: ", "") \
            if other and differs else None


# ----- the incident list -----

def incident_list(run: dict, minute: int, steps_of) -> list[dict]:
    """One entry per reported incident, active first, then a 'Hospital requests' entry if there are any."""
    state = run["timeline"][minute]
    out = []
    for inc in state["incidents"]:
        active = any(v["status"] in ("waiting", "assigned", "in_transit", "at_door") for v in inc["victims"])
        out.append({"id": inc["id"], "name": W.incident_name(inc), "active": active, "steps": len(steps_of(inc["id"]))})
    out.sort(key=lambda e: (not e["active"], e["id"]))
    n = len(steps_of(None))
    return out + ([{"id": None, "name": "Hospital requests", "active": False, "steps": n}] if n else [])


# ----- steps of one incident -----

def incident_steps(ctx: Context, iid: str | None) -> list[dict]:
    if iid is None:
        return hospital_request_steps(ctx)
    steps = report_steps(ctx, iid) + assessment_steps(ctx, iid) + unit_steps(ctx, iid) + admission_steps(ctx, iid)
    steps += guard_steps(ctx, iid) + warning_steps(ctx, iid) + outcome_steps(ctx, iid)
    return sorted(steps, key=lambda s: (s["t"], s.get("order", 1)))


def report_steps(ctx: Context, iid: str) -> list[dict]:
    e = next((e for e in ctx.events if e["type"] == "incident_reported" and e["incident"] == iid), None)
    if e is None:
        return []
    crit = sum(1 for v in e["victims"] if v["severity"] == "critical")
    s = step(e["t"], "grey", f"{iid} {W.INCIDENT_TYPES.get(e['incident_type'], '')} reported: “{e['description']}”",
             lines=[f"{W.plural(len(e['victims']), 'victim')}" + (f", {crit} critical" if crit else "")])
    return [{**s, "order": 0}]


def assessment_steps(ctx: Context, iid: str) -> list[dict]:
    out = []
    for e in ctx.events:
        if e["type"] == "agent_decision" and e["kind"] == "assess" and e.get("incident") == iid:
            flags = f" (it flagged: {', '.join(e['flags'])})" if e.get("flags") else ""
            rule = ctx.rule_of(e)
            out.append(step(e["t"], "blue", f"Incident Assessment: needs {W.units_text(e['units_needed'])}{flags}",
                            e["decided_by"], [ctx.why(e)] if e["decided_by"] != "rule" else [], rule=rule))
    return out


def rank_note(ctx: Context, plan: dict, iid: str) -> str | None:
    """Where the rule would have put this incident, when the AI ranked it differently."""
    call = ctx.calls.get(plan.get("seq"))
    if plan.get("decided_by") != "llm" or call is None or iid not in plan.get("ranking", []):
        return None
    rule = rule_ranking_of(call["input"])
    if iid not in rule or rule.index(iid) == plan["ranking"].index(iid):
        return None
    nth = lambda i: f"{i + 1}{'st' if i == 0 else 'nd' if i == 1 else 'rd' if i == 2 else 'th'}"
    return f"{iid} {nth(rule.index(iid))} of {len(rule)} by time left (the AI put it {nth(plan['ranking'].index(iid))})"


def unit_steps(ctx: Context, iid: str) -> list[dict]:
    """Dispatch's answers to the unit request: what it sent, and each new shortage (repeats folded in)."""
    plans = {e["t"]: e for e in ctx.events if e["type"] == "agent_decision" and e["kind"] == "plan_units"}
    out, current = [], None
    for m in ctx.events:
        if m["type"] != "message" or m["msg_type"] != "unit_response" or m.get("incident_id") != iid:
            continue
        p = m["proposed_action"]
        short = p.get("shortfall") if p.get("shortfall") and any(p["shortfall"].values()) else None
        plan = plans.get(m["t"], {})
        if current and not p.get("sent") and short == current["short"]:
            current["repeats"] += 1
            current["plans"].add(W.plan_text(p.get("plan")))
            current["step"]["lines"][0] = (f"Still short {W.plural(current['repeats'], 'more time')}, last at "
                                           f"{W.minute_tag(m['t'])}; plans tried: {', '.join(sorted(current['plans']))}")
            continue
        sent = ", ".join(W.unit_name(u) for u in p.get("sent", []))
        title = (f"Dispatch sent {sent}" if sent else "Dispatch has no unit free") + \
                (f"; short of {W.units_text(short)}" if short else "")
        lines = [f"Plan for the shortage: {W.plan_text(p.get('plan'))}"] if short else [""]
        if plan.get("decided_by") == "llm":
            lines.append(W.because(plan.get("reason"), "llm"))
        s = step(m["t"], "red" if short else "green", title, plan.get("decided_by", "rule"), lines,
                 rule=rank_note(ctx, plan, iid) if plan else None)
        out.append(s)
        current = {"short": short, "repeats": 0, "plans": {W.plan_text(p.get("plan"))}, "step": s} if short else None
    for e in ctx.events:
        if e["type"] == "unit_redirected" and iid in (e["incident"], e["from_incident"]):
            to_me = e["incident"] == iid
            out.append(step(e["t"], "amber", f"{W.cap(W.unit_name(e['unit']))} was redirected "
                            + (f"here from {e['from_incident']}" if to_me else f"to {e['incident']}, a more urgent job")))
    return out


def admission_steps(ctx: Context, iid: str) -> list[dict]:
    threads = [th for th in ctx.index.values() if th["protocol"] in ("Admission", "Reroute")
               and any(m.get("incident_id") == iid or any(v.startswith(iid + "-") for v in m.get("victim_ids") or [])
                       for m in th["messages"])]
    tries: dict[tuple, int] = defaultdict(int)
    out = []
    for th in sorted(threads, key=lambda th: (th["messages"][0]["t"], th["id"])):
        ms = [m for m in th["messages"] if m["t"] <= ctx.minute]
        if not ms:
            continue
        victims = tuple(sorted({v for m in ms for v in m.get("victim_ids") or []}))
        tries[victims] += 1
        out += thread_steps(ctx, th, ms, tries[victims]) + related_transfers(ctx, th, ms)
    return out


def thread_steps(ctx: Context, th: dict, ms: list[dict], attempt: int) -> list[dict]:
    """One admission request with every hospital's reply grouped under it, then the referee if called."""
    vids = sorted({v for m in ms for v in m.get("victim_ids") or []})
    who = W.victims_phrase(vids, ctx.severity) + ctx.which(vids)
    decisions = [e for e in th["decisions"] if e["t"] <= ctx.minute]
    lines = reply_lines(ctx, ms, decisions)
    award = next((e for e in reversed(decisions) if e["kind"] == "award"), None)
    chips = hospital_chips(ms)
    settled = any(c["status"] == "accepted" for c in chips)
    escalated = any(m["msg_type"] == "escalation" for m in ms)
    open_ = any(m["deliver_at"] >= ctx.minute for m in ms) and not settled
    dot = "green" if settled else "purple" if escalated else "blue" if open_ else "red"
    badge = award["decided_by"] if award else ("llm" if any(e["decided_by"] == "llm" for e in decisions) else "rule")
    rule = next((f"{W.place(e['agent'])}: {r}" if e["kind"] != "award" else r for e in decisions
                 if e["decided_by"] == "llm" and (r := ctx.rule_of(e))), None)
    title = (f"Where should {who} go?" if th["protocol"] == "Admission"
             else f"A walk-in took the slot for {who}: where now?") + (f" · Attempt {attempt}" if attempt > 1 else "")
    out = [step(ms[0]["t"], dot, title, badge, lines, bubbles_of(ms, ctx.minute, ctx.severity), rule,
                tip=f"{th['id']} · {', '.join(sorted({v for m in ms for v in m.get('victim_ids') or []}))}")]
    return out + referee_steps(ctx, th, ms)


def reply_lines(ctx: Context, ms: list[dict], decisions: list[dict]) -> list[str]:
    rounds = max(m["round"] for m in ms)
    asked = sorted({m["recipient"] for m in ms if m["msg_type"] in ("admission_cfp", "admission_counter")})
    lines = [f"Dispatch asked {', '.join(W.place(h) for h in asked)}" + (f" ({W.plural(rounds, 'round')})" if rounds > 1 else "")
             if asked else "Dispatch looks for another hospital"]
    for c in hospital_chips(ms):
        colour, text = REPLY.get(c["status"], ("grey", c["text"]))
        offer = next((m for m in reversed(ms) if m["sender"] == c["hospital"] and m["msg_type"] in
                      ("admission_offer", "decline")), None)
        reason = (W.because(offer.get("reason"), offer.get("decided_by"))
                  if offer and c["status"] not in ("accepted", "cant", "referee", "awarded") else "")
        lines.append(f"{DOTS[colour]} {W.place(c['hospital'])}: {text}" + (f" — {reason}" if reason else ""))
    award = next((e for e in reversed(decisions) if e["kind"] == "award"), None)
    if award and award["awards"]:
        chosen = "; ".join(f"{W.place(a['hospital'])} (arriving {W.minute_tag(a['arrival'])})" for a in award["awards"])
        lines.append(f"Dispatch chose {chosen} — {ctx.why(award) or 'because it fits best'}")
    elif award:
        lines.append("No hospital could take them in time" + (", so Dispatch asked the referee" if award.get("escalate")
                                                              else ""))
    return lines


def referee_steps(ctx: Context, th: dict, ms: list[dict]) -> list[dict]:
    out = []
    for esc in (m for m in ms if m["msg_type"] == "escalation"):
        victim = (esc.get("victim_ids") or [None])[0]
        left = ctx.deadline.get(victim)
        left = f"{left - esc['t']} min before the deadline" if left is not None else "no deadline"
        ruling = next((m for m in ms if m["msg_type"] == "arbiter_decision" and m["seq"] > esc["seq"]), None)
        lines = [f"Called in because {W.TRIGGERS.get(esc.get('reason'), 'the agents could not agree')}, {left}."]
        badge = "rule"
        if ruling:
            p = ruling["proposed_action"]
            verdict = "on time" if p.get("meets_deadline") else "too late, the least-bad choice"
            lines.append(f"Ruling: option {p.get('option')} of 7, {W.ARBITER_OPTIONS.get(p.get('option'), '')}, at "
                         f"{W.place(p['hospital']) if p.get('hospital') else 'no hospital'}, arriving "
                         f"{W.minute_tag(p.get('arrival'))} ({verdict})")
            badge = ruling.get("decided_by", "rule")
            if badge == "llm":
                lines.append(W.because(ruling.get("reason"), "llm"))
        out.append(step(esc["t"], "purple", f"Referee called in for {W.victims_phrase([victim], ctx.severity)}", badge,
                        lines, bubbles_of([m for m in ms if m["msg_type"] in ("escalation", "arbiter_decision")],
                                          ctx.minute, ctx.severity)))
    return out


def transfers_for(ctx: Context, ms: list[dict]) -> list[dict]:
    """Transfers a hospital started to make room right after a conditional award in these messages."""
    out = []
    for award in (m for m in ms if m["msg_type"] == "admission_award" and m["proposed_action"].get("conditional")):
        for tr in ctx.index.values():
            first = tr["messages"][0]
            if tr["protocol"] == "Transfer" and first["sender"] == award["recipient"] and award["t"] <= first["t"] <= award["t"] + 2:
                out.append(tr)
    return out


def related_transfers(ctx: Context, th: dict, ms: list[dict]) -> list[dict]:
    return [transfer_step(ctx, tr, f"{W.place(tr['messages'][0]['sender'])} tried to move a patient out to make room")
            for tr in transfers_for(ctx, ms)]


def transfer_step(ctx: Context, tr: dict, title: str) -> dict:
    ms = [m for m in tr["messages"] if m["t"] <= ctx.minute]
    chips = hospital_chips(ms)
    moved = next((c["hospital"] for c in chips if c["status"] == "accepted"), None)
    lines = [f"{DOTS[TRANSFER_REPLY.get(c['status'], ('grey', ''))[0]]} {W.place(c['hospital'])}: "
             f"{TRANSFER_REPLY.get(c['status'], ('', c['text']))[1]}" for c in chips]
    return step(ms[0]["t"], "green" if moved else "red", title + (f": {W.place(moved)} took the patient" if moved else ""),
                "rule", lines, bubbles_of(ms, ctx.minute, ctx.severity), tip=tr["id"])


def guard_steps(ctx: Context, iid: str) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for e in ctx.events:
        if e["type"] == "guard" and e.get("incident") == iid:
            groups[(e["rule"], e["agent"], e.get("victim"), "suggested" in e)].append(e)
    out = []
    for es in groups.values():
        times = f" ({len(es)} times, last {W.minute_tag(es[-1]['t'])})" if len(es) > 1 else ""
        out.append(step(es[0]["t"], "amber", W.guard_sentence(es[0], ctx.severity) + times, "guard"))
    return out


def warning_steps(ctx: Context, iid: str) -> list[dict]:
    kinds = ("arrived_no_slot", "reservation_broken", "deadline_missed")
    out = []
    for e in ctx.events:
        victims = e.get("victims") or ([e["victim"]] if e.get("victim") else [])
        if e["type"] in kinds and any(v.startswith(iid + "-") for v in victims):
            out.append(step(e["t"], "red", W.event_sentence(e, ctx.severity)))
    return out


def outcome_steps(ctx: Context, iid: str) -> list[dict]:
    inc = next((i for i in ctx.run["timeline"][ctx.minute]["incidents"] if i["id"] == iid), None)
    if inc is None:
        return []
    arrived = {e["victim"]: e for e in ctx.events if e["type"] == "patient_delivered"}
    out = []
    for v in inc["victims"]:
        who = W.cap(W.victims_phrase([v["id"]], ctx.severity))
        e = arrived.get(v["id"])
        if e:
            on_time = v["deadline"] is None or e["t"] <= v["deadline"]
            out.append({**step(e["t"], "green" if on_time else "red",
                               f"{who} reached {W.place(e['hospital'])} at {W.minute_tag(e['t'])}: "
                               f"{'on time' if on_time else 'late'}" + (f" (deadline {W.minute_tag(v['deadline'])})"
                                                                         if v["deadline"] else ""), tip=v["id"]), "order": 2})
    return out


def hospital_request_steps(ctx: Context) -> list[dict]:
    """Staff loans and transfers that belong to no single incident."""
    linked = {tr["id"] for th in ctx.index.values() if th["protocol"] == "Admission"
              for tr in transfers_for(ctx, th["messages"])}
    out = []
    for th in sorted(ctx.index.values(), key=lambda th: th["messages"][0]["t"]):
        first = th["messages"][0]
        if first["t"] > ctx.minute or th["id"] in linked:
            continue
        if th["protocol"] == "Transfer":
            out.append(transfer_step(ctx, th, f"{W.place(first['sender'])} asked others to take a stable patient"))
        elif th["protocol"] == "Staff loan":
            ms = [m for m in th["messages"] if m["t"] <= ctx.minute]
            chips = hospital_chips(ms)
            ok = next((c["hospital"] for c in chips if c["status"] in ("offered", "later")), None)
            p = first.get("proposed_action") or {}
            out.append(step(first["t"], "green" if ok else "red",
                            f"{W.place(first['sender'])} asked to borrow a {p.get('kind', 'staff member')}"
                            + (f": {W.place(ok)} said yes" if ok else ": nobody could"), "rule",
                            [f"{DOTS[TRANSFER_REPLY.get(c['status'], ('grey', ''))[0]]} {W.place(c['hospital'])}: "
                             f"{TRANSFER_REPLY.get(c['status'], ('', c['text']))[1]}" for c in chips],
                            bubbles_of(ms, ctx.minute, ctx.severity), tip=th["id"]))
    return out


# ----- html -----

BADGE = {"rule": ("Rule", "b-rule"), "llm": ("AI", "b-ai"), "guard": ("Guard blocked", "b-guard"),
         "fallback": ("Fallback", "b-fb")}


def step_html(s: dict) -> str:
    text, cls = BADGE.get(s["badge"], BADGE["rule"])
    lines = "".join(f"<div class='tl-line'>{escape(line)}</div>" for line in s["lines"] if line)
    rule = f"<div class='tl-rule'>Rule would have chosen: {escape(s['rule'])}</div>" if s.get("rule") else ""
    return (f"<div class='tl-step' title='{escape(s.get('tip', ''), quote=True)}'><span class='tl-dot'>{DOTS[s['dot']]}</span>"
            f"<span class='ev-t'>{W.minute_tag(s['t'])}</span> <b>{escape(s['title'])}</b> "
            f"<span class='badge {cls}'>{text}</span>{lines}{rule}</div>")
