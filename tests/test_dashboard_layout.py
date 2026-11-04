from __future__ import annotations

from dashboard import agent_views, eval_catalog, eval_views, map_view, panels
from sim.runner import run
from tests.test_generator_runner import PLACES, SMALL, roads


def minute_of(tmp_path, t=6):
    out = run(5, "agents", runs_dir=tmp_path, cfg=SMALL, roads=roads(), places=PLACES, llm_mode="off")
    from dashboard import run_store
    data = run_store.load_run(out.name, tmp_path)
    return data, data["timeline"][t]


# ----- right panel -----

def test_kpi_cards_are_the_four_headline_numbers():
    m = {"critical_on_time_pct": 75.0, "criticals_on_time": 3, "criticals_total": 4, "unserved_criticals": 1,
         "avg_report_to_care_min": 41.5, "victims_delivered": 9}
    cards = panels.kpi_cards(m)
    assert [c[0] for c in cards] == ["Criticals on time", "Unserved criticals", "Avg to care", "Delivered"]
    assert cards[0][1:] == ("75.0%", "3 of 4") and cards[2][1] == "41.5 min"
    assert panels.kpi_cards({**m, "critical_on_time_pct": None, "avg_report_to_care_min": None})[0][1] == "–"


def test_units_and_hospitals_fit_one_line_each(tmp_path):
    _, minute = minute_of(tmp_path)
    line = panels.units_free_line(minute)
    assert line.startswith("<div class='units-line'>Free now:") and "<b>Paramedic (ALS)</b>" in line and "ⓘ" in line
    h = dict(minute["hospitals"][0], er_used=4, er_slots=4, doctors=[{"patients": ["a"], "max": 1},
                                                                    {"patients": [], "max": 2}],
             patients=[{"walk_in": True}, {"walk_in": False}])
    row = panels.hospital_line(h)
    assert (row["level"], row["load_pct"], row["free_doctors"], row["walk_ins"]) == ("full", 100, 1, 1)
    assert panels.hospitals_html(minute).count("class='hosp'") == len(minute["hospitals"])
    both = panels.compare_resources_html(minute, minute)
    assert "<b>Baseline</b> free:" in both and "<b>Agents</b> free:" in both and both.count("ER baseline") == 3


def test_event_and_agent_feeds_escape_text(tmp_path):
    html = panels.events_html([(5, "a <b>x</b>"), (4, "older")], 5)
    assert "&lt;b&gt;" in html and "class='ev now'" in html and panels.events_html([], 0).count("No events") == 1
    data, _ = minute_of(tmp_path)
    cards = agent_views.agent_cards(data["events"], data["timeline"][10], 10)
    html = agent_views.agent_card_html({**cards[0], "status": "idle <now>"})
    assert cards[0]["name"] in html and "idle &lt;now&gt;" in html and "Last decision" in html


def test_a_moving_unit_leaves_a_trail_that_ends_where_it_is(tmp_path):
    data, _ = minute_of(tmp_path)
    trip = next(t for trips in data["trips_by_unit"].values() for t in trips if t["arrive_t"] - t["depart_t"] >= 3)
    t = trip["depart_t"] + 2.5
    unit = next(u for u in map_view.units_at(data, t) if u["id"] == trip["unit"])
    tr = unit["trail"]
    assert tr["times"][-1] == t and tr["path"][-1] == [unit["lon"], unit["lat"]]
    assert len(tr["path"]) == len(tr["times"]) >= 2 and tr["times"] == sorted(tr["times"])
    assert tr["times"][1] >= t - map_view.TRAIL_MINUTES - 1e-9      # one point before the window, then inside it
    layers = {layer.id for layer in map_view.build_deck(data, t, {"latitude": 0, "longitude": 0, "zoom": 12}).layers}
    assert "trails" in layers


# ----- the Evaluation page overview -----

def summary(**means):
    return {"summary": {"metrics": {p: {"critical_on_time_pct": {"mean": m, "sd": 1.0, "n": 20, "repeat_sd": None}}
                                    for p, m in means.items()}},
            "settings": {"seeds": list(range(1, 21)), "llm_mode": "off", "policies": ["greedy", *means]}}


EVALS = {"e-rules": summary(greedy=40.0, agents=85.0), "e-abl": summary(greedy=40.0, **{"agents-nonego": 70.0}),
         "e-llm": summary(greedy=40.0, agents=86.0)}
CATALOG = {"evaluations": {"e-rules": "Final rules", "e-llm": "Final LLM"},
           "headline": [{"label": "Greedy", "eval": "e-rules", "policy": "greedy"},
                        {"label": "LLM", "eval": "e-llm", "policy": "agents", "focus": True},
                        {"label": "Missing", "eval": "e-gone", "policy": "agents"}],
           "contributions": [{"label": "Greedy", "eval": "e-rules", "policy": "greedy"},
                             {"label": "+ coordination", "eval": "e-abl", "policy": "agents-nonego", "vs": "Greedy"},
                             {"label": "+ negotiation", "eval": "e-rules", "policy": "agents", "vs": "+ coordination"},
                             {"label": "LLM", "eval": "e-llm", "policy": "agents", "vs": "+ negotiation", "side": True}],
           "timeline": [{"title": "v1", "eval": "e-gone", "policy": "agents", "text": "t"},
                        {"title": "now", "eval": "e-llm", "policy": "agents", "text": "t"}]}


def loader(eval_id):
    if eval_id not in EVALS:
        raise FileNotFoundError(eval_id)
    return EVALS[eval_id]


def test_evaluation_names_and_which_are_shown():
    settings = {"seeds": [101, 120], "llm_mode": "live", "llm_guard": False, "policies": ["greedy", "agents"]}
    assert eval_catalog.label("e-rules", settings, CATALOG) == "Final rules"
    assert eval_catalog.label("eval-099", settings, CATALOG) == \
        "eval-099 · live LLM, guard off · 1 agent variant (seeds 101-120)"
    replay = {**settings, "llm_mode": "replay", "replay_from": "eval-008", "policies": ["greedy", "a", "b"]}
    assert "replay of eval-008 · 2 agent variants" in eval_catalog.auto_label("eval-009", replay)
    found = ["eval-099", "e-llm", "e-rules"]
    assert eval_catalog.visible(found, CATALOG, show_all=False) == ["e-rules", "e-llm"]
    assert eval_catalog.visible(found, CATALOG, show_all=True) == ["e-rules", "e-llm", "eval-099"]


def test_overview_numbers_come_from_the_summaries():
    head = eval_catalog.headline(CATALOG, loader)
    assert [(h["label"], h["mean"]) for h in head] == [("Greedy", 40.0), ("LLM", 86.0)]   # missing ones skipped
    rows = eval_catalog.contributions(CATALOG, loader)
    assert [(r["label"], r["mean"], r["change"]) for r in rows] == [
        ("Greedy", 40.0, None), ("+ coordination", 70.0, 30.0), ("+ negotiation", 85.0, 15.0), ("LLM", 86.0, 1.0)]
    assert rows[-1]["side"] and not rows[0]["side"]
    assert [s["mean"] for s in eval_catalog.timeline(CATALOG, loader)] == [None, 86.0]
    table = eval_views.contributions_table(rows)
    assert list(table["Change"]) == ["", "+30.0 pts", "+15.0 pts", "+1.0 pts"]
    fig = eval_views.headline_bars(head, "light")
    assert list(fig.data[0].x) == [40.0, 86.0] and fig.data[0].marker.color[1] == eval_views.ACCENT["light"]
