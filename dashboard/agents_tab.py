"""The Agents tab on the Simulation page (DESIGN 13.2): one card per agent at the playback minute.

A plain status with an icon, a hospital's mood, and the agent's last message, from the recorded
messages (dashboard/agent_views.py for pending requests, dashboard/conversation.py for wording).
"""
from __future__ import annotations

from html import escape

from dashboard import agent_views
from dashboard import conversation as C
from dashboard import wording as W
from dashboard.names import hospital_name

ORDER = ("ia", "dispatch", "H1", "H2", "H3", "arbiter")
MOOD_WORDS = {"eager": "room to spare", "normal": "moderately busy", "protective": "nearly full"}
ICONS = {"asleep": "💤", "waiting": "⏳", "to_answer": "📥", "deciding": "⚙️", "ai": "🧠", "done": "✓"}


def waiting_text(recipients: list[str]) -> str:
    """'Waiting for H1's answer', 'Waiting for answers from H1 and H3', 'Waiting for 3 hospitals' answers'."""
    names = [C.name(r) for r in recipients]
    if len(names) == 1:
        return f"Waiting for {names[0]}'s answer"
    if len(names) >= 3 and all(r.startswith("H") for r in recipients):
        return f"Waiting for {len(names)} hospitals' answers"
    return f"Waiting for answers from {W.join_words(names)}"


def status(*, talked: bool, waiting_on: list[str], to_answer: int, acted: bool, by_ai: bool) -> str:
    """One plain phrase with an icon. 'Nothing to do' only when nothing is pending."""
    if not talked:
        return f"{ICONS['asleep']} Not needed yet"
    parts = []
    if waiting_on:
        parts.append(f"{ICONS['waiting']} {waiting_text(waiting_on)}")
    if to_answer:
        parts.append(f"{ICONS['to_answer']} {to_answer} request{'s' * (to_answer > 1)} to answer")
    if parts:
        return " · ".join(parts)
    if acted:
        return f"{ICONS['ai']} Deciding (AI)" if by_ai else f"{ICONS['deciding']} Deciding"
    return f"{ICONS['done']} Nothing to do"


def last_message(ctx: C.Context, agent: str) -> dict | None:
    """The agent's latest message as one conversation line; one line even when it went to several hospitals."""
    mine = [m for m in agent_views.messages(ctx.events) if m["sender"] == agent]
    if not mine:
        return None
    last = mine[-1]
    same = [m for m in mine if m["t"] == last["t"] and m["thread_id"] == last["thread_id"]
            and m["msg_type"] == last["msg_type"]]
    to = list(dict.fromkeys(m["recipient"] for m in same))
    return {"t": last["t"], "to": to, "text": C.says(ctx, {**last, "_to": to})}


def acted_now(ctx: C.Context, agent: str, msgs: list[dict]) -> tuple[bool, bool]:
    """(sent a message or decided this minute, and whether the AI made that call)."""
    now = [m for m in msgs if m["sender"] == agent and m["t"] == ctx.minute]
    decisions = [e for e in ctx.events if e["type"] == "agent_decision" and e["agent"] == agent and e["t"] == ctx.minute]
    by_ai = any(m.get("decided_by") == "llm" for m in now) or any(e["decided_by"] == "llm" for e in decisions)
    return bool(now or decisions), by_ai


def cards(ctx: C.Context) -> list[dict]:
    minute = ctx.run["timeline"][ctx.minute]
    moods = {c["id"]: c.get("mood") for c in agent_views.agent_cards(ctx.events, minute, ctx.minute)}
    msgs = agent_views.messages(ctx.events)
    by_thread = agent_views.threads_of(msgs)
    out = []
    for agent in (a for a in ORDER if a in moods):
        waiting = agent_views.pending(msgs, by_thread, agent, ctx.minute, incoming=False)
        to_answer = agent_views.pending(msgs, by_thread, agent, ctx.minute, incoming=True)
        acted, by_ai = acted_now(ctx, agent, msgs)
        msg = last_message(ctx, agent)
        text = status(talked=any(agent_views.involves(m, agent) for m in msgs),
                      waiting_on=list(dict.fromkeys(m["recipient"] for m in waiting)), to_answer=len(to_answer),
                      acted=acted, by_ai=by_ai)
        mood = MOOD_WORDS.get(moods[agent])
        out.append({"id": agent, "name": C.name(agent),
                    "full": hospital_name(agent) if agent.startswith("H") else C.name(agent),
                    "status": text + (f" · {mood}" if mood else ""),
                    "last_text": "" if msg is None else
                    f"{W.minute_tag(msg['t'])} → {', '.join(C.name(r) for r in msg['to'])}: {msg['text']}"})
    return out


def card_label(card: dict) -> str:
    """The clickable card's text (a Streamlit button, so markdown): name, status, last message in grey."""
    name = f"**{card['name']}**" + (f" · {md(card['full'])}" if card["full"] != card["name"] else "")
    return f"{name}  \n{md(card['status'])}  \n:gray[{md(card['last_text'] or 'No message yet')}]"


PAGE = 10   # lines shown when an agent opens; "Show older" adds this many more


def involves(line: dict, agent: str) -> bool:
    return line["kind"] == "msg" and (line["sender"] == agent or agent in line["to"])


def agent_lines(ctx: C.Context, agent: str) -> list[dict]:
    """Every message the agent sent or received up to the minute, in time order, each tagged with the incident
    it is about (or "Between hospitals"), worded as in Conversations."""
    minute = ctx.run["timeline"][ctx.minute]
    names = {i["id"]: W.incident_name(i) for i in minute["incidents"]}
    ids = list(names)
    out, seen = [], set()
    for iid in [*ids, C.BETWEEN]:
        for line in C.conversation(ctx, iid, ids):
            key = (line["t"], line.get("sender"), tuple(line.get("to") or ()), line["text"])
            if involves(line, agent) and key not in seen:
                seen.add(key)
                out.append({**line, "incident": iid, "incident_name": names.get(iid, "Between hospitals"),
                            "out": line["sender"] == agent})
    return sorted(out, key=lambda l: l["t"])


def newest_first(lines: list[dict], shown: int) -> list[dict]:
    """The latest `shown` lines, newest first (lines come in time order; same-minute lines keep their order
    reversed too, so the very last message is on top)."""
    return lines[::-1][:shown]


def lines_html(lines: list[dict]) -> str:
    """'min 136 · I05 → H1, H2: …' for what the agent sent, 'min 137 · I05 ← from H2: …' (indented, tinted)
    for what it received; the minutes stay in one column."""
    if not lines:
        return "<div class='cv-n'>· No messages yet.</div>"
    rows = []
    for l in lines:
        tag = "Between" if l["incident"] == C.BETWEEN else l["incident"]
        where = f"<span class='ag-i' title='{escape(l['incident_name'], quote=True)}'>{escape(tag)}</span>"
        who = ("→ " + ", ".join(C.who_html(r) for r in l["to"])) if l["out"] else f"← from {C.who_html(l['sender'])}"
        ai = f" <span class='ai-tag' title='{escape(l['ai'], quote=True)}'>(AI)</span>" if l.get("ai") else ""
        rows.append(f"<div class='ag-l {'out' if l['out'] else 'in'}'><span class='cv-t'>{W.minute_tag(l['t'])}</span>"
                    f"<span class='ag-b'>{where} <b>{who}</b>: {escape(l['text'])}{ai}</span></div>")
    return "<div class='ag-lines'>" + "".join(rows) + "</div>"


def md(text: str) -> str:
    """Button labels are markdown: keep '*', '_', '[' and the like literal."""
    return "".join("\\" + ch if ch in "\\`*_{}[]<>#+!|~:$" else ch for ch in text)
