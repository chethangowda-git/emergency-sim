"""The "Agents" strip on the Simulation page (DESIGN 13.2): one line per agent at the playback minute.

Status, mood (hospitals), pending requests and the agent's last message in plain words, from the
recorded messages (dashboard/agent_views.py for status and pending, dashboard/conversation.py for wording).
"""
from __future__ import annotations

from dashboard import agent_views
from dashboard import conversation as C
from dashboard import wording as W

ORDER = ("ia", "dispatch", "H1", "H2", "H3", "arbiter")
MOOD_WORDS = {"eager": "room to spare", "normal": "moderately busy", "protective": "nearly full"}


def status_text(waiting: int, acted: bool) -> str:
    """What the agent itself is doing: waiting for N replies, deciding (it acted this minute) or idle."""
    if waiting:
        return f"waiting for {waiting} repl{'ies' if waiting > 1 else 'y'}"
    return "deciding" if acted else "idle"


def to_answer(n: int) -> str:
    return f"{n} request{'s' * (n > 1)} to answer" if n else ""


def last_message(ctx: C.Context, agent: str) -> dict | None:
    """The agent's latest message as one conversation line ('→ H1, H2: Can you take …?'); one line even
    when it went to several hospitals."""
    mine = [m for m in agent_views.messages(ctx.events) if m["sender"] == agent]
    if not mine:
        return None
    last = mine[-1]
    same = [m for m in mine if m["t"] == last["t"] and m["thread_id"] == last["thread_id"]
            and m["msg_type"] == last["msg_type"]]
    to = list(dict.fromkeys(m["recipient"] for m in same))
    return {"t": last["t"], "to": to, "text": C.says(ctx, {**last, "_to": to})}


def rows(ctx: C.Context) -> list[dict]:
    minute = ctx.run["timeline"][ctx.minute]
    cards = {c["id"]: c for c in agent_views.agent_cards(ctx.events, minute, ctx.minute)}
    msgs = agent_views.messages(ctx.events)
    by_thread = agent_views.threads_of(msgs)
    out = []
    for agent in (a for a in ORDER if a in cards):
        card = cards[agent]
        msg = last_message(ctx, agent)
        waiting = len(agent_views.pending(msgs, by_thread, agent, ctx.minute, incoming=False))
        incoming = len(agent_views.pending(msgs, by_thread, agent, ctx.minute, incoming=True))
        last = card["decision"]
        acted = any(m["sender"] == agent and m["t"] == ctx.minute for m in msgs) or \
            (last is not None and last["t"] == ctx.minute)
        out.append({"id": agent, "name": C.name(agent), "status": status_text(waiting, acted),
                    "mood": MOOD_WORDS.get(card.get("mood")), "pending": incoming,
                    "last": msg, "last_text": "" if msg is None else
                    f"{W.minute_tag(msg['t'])} → {', '.join(C.name(r) for r in msg['to'])}: {msg['text']}"})
    return out


def label(row: dict) -> str:
    """The one-line button text: name, status, mood, pending, last message."""
    parts = [f"**{row['name']}**", row["status"]]
    if row["mood"]:
        parts.append(row["mood"])
    if row["pending"]:
        parts.append(to_answer(row["pending"]))
    head = " · ".join(parts)
    return f"{head} — {row['last_text']}" if row["last_text"] else f"{head} — no message yet"


def involves(line: dict, agent: str) -> bool:
    return line["kind"] == "msg" and (line["sender"] == agent or agent in line["to"])


def only(lines: list[dict], agent: str | None) -> list[dict]:
    """A conversation filtered to one agent's messages (sent or received); narrator lines drop out."""
    return lines if agent is None else [l for l in lines if involves(l, agent)]
