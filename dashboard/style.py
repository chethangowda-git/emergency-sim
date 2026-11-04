"""Page CSS for a one-screen layout at 1440x900 (DESIGN 13.1): tight padding, compact panels."""
from __future__ import annotations

import streamlit as st

CSS = """
<style>
.block-container {padding: 3.4rem 1.1rem 0.3rem 1.1rem; max-width: 100%;}
div[data-testid="stVerticalBlock"] {gap: 0.45rem;}
div[data-testid="stWidgetLabel"] p {font-size: 0.72rem; margin-bottom: -0.2rem; opacity: 0.8;}
div[data-testid="stElementContainer"]:has(> div > div.stMarkdown style) {display: none;}
.clock {display: flex; gap: 0.8rem; align-items: baseline; font-size: 0.9rem; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis;}
.clock .min {font-weight: 700; font-variant-numeric: tabular-nums; font-size: 1.05rem;}
.clock .tag {opacity: 0.75;}
.prog {height: 4px; background: rgba(128,128,128,.25); border-radius: 2px; margin: 3px 0 8px 0;}
.prog > div {height: 100%; background: #2a78d6; border-radius: 2px;}
.kpis {display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px;}
.kpi {border: 1px solid rgba(128,128,128,.3); border-radius: 8px; padding: 5px 8px;}
.kpi-t {font-size: 0.7rem; opacity: 0.75;}
.kpi-v {font-size: 1.3rem; font-weight: 650; line-height: 1.25; font-variant-numeric: tabular-nums;}
.kpi-d {font-size: 0.66rem; opacity: 0.6;}
.units-line {font-size: 0.82rem;}
.hosp {margin: 0 0 5px 0;}
.hosp-n {font-size: 0.8rem; font-weight: 600;}
.hosp-s {font-size: 0.72rem; opacity: 0.8;}
.bar {height: 6px; background: rgba(128,128,128,.25); border-radius: 3px; overflow: hidden; margin: 2px 0;}
.fill {height: 100%;}
.fill.ok {background: #2e7d32;} .fill.busy {background: #e67800;} .fill.full {background: #c62828;}
.feed .ev, .agent .ev {font-size: 0.78rem; line-height: 1.35; padding: 1px 0;}
.ev-t {display: inline-block; min-width: 2.3em; opacity: 0.55; font-variant-numeric: tabular-nums;}
.ev.now {font-weight: 600;}
.agent {border-bottom: 1px solid rgba(128,128,128,.2); padding: 4px 0; font-size: 0.8rem;}
.agent-d {font-size: 0.72rem; opacity: 0.75;}
.legend {font-size: 0.72rem; line-height: 1.7;}
.legend span {transform: scale(0.9);}
.headline {display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 6px;}
.headline .kpi-v {font-size: 1.9rem;}
.headline .kpi.focus {border-color: #2a78d6; border-width: 2px;}
.steps {display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 14px;}
.step {border-left: 3px solid #2a78d6; padding: 2px 10px; font-size: 0.8rem;}
.step b {font-size: 0.9rem;}
.step .num {font-size: 1.2rem; font-weight: 650;}
</style>
"""


def apply() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
