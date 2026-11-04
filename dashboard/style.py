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
.kpis {display: grid; grid-template-columns: repeat(6, 1fr); gap: 4px; margin-bottom: 12px;}
.kpis .kpi {padding: 2px 6px;}
.kpis .kpi-t {font-size: 0.62rem; line-height: 1.15; min-height: 1.45rem;}
.kpis .kpi-v {font-size: 1.0rem; line-height: 1.2;}
.kpis .kpi-d {font-size: 0.6rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
.ag-lines {font-size: 0.78rem; line-height: 1.4; margin: 0 0 6px 0;}
.ag-l {display: flex; gap: 0.4rem; padding: 1px 4px; border-radius: 4px;}
.ag-l .cv-t {flex: 0 0 3.2rem; opacity: 0.6; font-variant-numeric: tabular-nums;}
.ag-l .ag-b {flex: 1 1 auto; min-width: 0;}
.ag-l.in {background: rgba(42,120,214,.10);}
.ag-l.in .ag-b {padding-left: 1.1rem;}
.ag-i {font-size: 0.68rem; opacity: 0.7; border: 1px solid rgba(128,128,128,.4); border-radius: 4px; padding: 0 3px;
    cursor: help;}
.tab-intro {font-size: 0.74rem; opacity: 0.6; margin: -4px 0 4px 0;}
.st-key-agentlist {gap: 0.35rem !important;}
.st-key-agentlist [class*="st-key-agent_open_"] button {border-color: #2a78d6; background: rgba(42,120,214,.14);}
.st-key-agentlist [class*="st-key-earlier_"] button p {font-size: 0.74rem; font-weight: 400; opacity: 0.8;}
.st-key-agentlist [class*="st-key-agent_"] button {justify-content: flex-start; text-align: left; padding: 4px 9px;
    min-height: 4.7rem; height: 4.7rem; overflow: hidden; align-items: flex-start;}
.st-key-agentlist [class*="st-key-agent_"] button div, .st-key-agentlist [class*="st-key-agent_"] button p {
    text-align: left; justify-content: flex-start; max-width: 100%; margin: 0;}
.st-key-agentlist [class*="st-key-agent_"] button p {font-size: 0.76rem; line-height: 1.3; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;}
.st-key-controlbar [data-testid='stHorizontalBlock'] {gap: 0.4rem;}
.st-key-controlbar button {padding-left: 0.45rem; padding-right: 0.45rem;}
.st-key-controlbar button p, .st-key-controlbar label p {font-size: 0.84rem; white-space: nowrap;}
.resources {display: grid; grid-template-columns: repeat(5, 1fr); gap: 5px; margin: 4px 0 6px 0;}
.res {border: 1px solid rgba(128,128,128,.25); border-radius: 6px; padding: 2px 6px;}
.res-n {font-size: 0.66rem; opacity: 0.75; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
.res-v {font-size: 0.85rem; font-weight: 600; font-variant-numeric: tabular-nums;}
.res-d {font-size: 0.64rem; opacity: 0.6;}
.kpi {border: 1px solid rgba(128,128,128,.3); border-radius: 8px; padding: 4px 7px; min-width: 0;}
.kpi-t {font-size: 0.7rem; opacity: 0.75;}
.kpi-v {font-size: 1.15rem; font-weight: 650; line-height: 1.25; font-variant-numeric: tabular-nums;}
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
.headline {display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px;}
.removals {font-size: 0.86rem; margin: 0 0 6px 0; padding-left: 1.2rem;}
.removals li, .removals li p {margin-bottom: 3px; font-size: 0.86rem !important;}
.headline .kpi-v {font-size: 1.9rem;}
.headline .kpi.focus {border-color: #2a78d6; border-width: 2px;}
.steps {display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 14px;}
.step {border-left: 3px solid #2a78d6; padding: 2px 10px; font-size: 0.8rem;}
.step b {font-size: 0.9rem;}
.step .num {font-size: 1.2rem; font-weight: 650;}
.now-line {font-size: 0.85rem; margin: -2px 0 2px 0; opacity: 0.92; white-space: nowrap; overflow: hidden;
           text-overflow: ellipsis;}
.tip {cursor: help; opacity: 0.55; font-size: 0.8em;}
.icard {border: 1px solid rgba(128,128,128,.3); border-radius: 8px; padding: 5px 8px; margin: -6px 0 6px 0;
        font-size: 0.78rem;}
.icard.done {margin-top: 0; opacity: 0.7;}
.icard.changed {border-left: 3px solid #2a78d6;}
.icard.focus {border-color: #ffc400; box-shadow: 0 0 0 1px #ffc400;}
.chip {display: inline-block; font-size: 0.72rem; padding: 0 6px; border-radius: 9px; background: rgba(211,47,47,.15);}
.desc {font-size: 0.74rem; opacity: 0.75; margin: 1px 0;}
.steps-bar {display: flex; gap: 3px; align-items: center; margin: 3px 0;}
.pstep {display: inline-block; width: 34px; height: 5px; border-radius: 3px; background: rgba(128,128,128,.3);}
.pstep.on {background: #2e7d32;}
.step-now {font-size: 0.68rem; opacity: 0.7; margin-left: 4px;}
.vrow {font-size: 0.76rem; line-height: 1.35;}
.sev {font-size: 0.66rem; padding: 0 4px; border-radius: 4px; background: rgba(128,128,128,.25);}
.sev.critical {background: rgba(211,47,47,.35);}
.st {font-size: 0.68rem; padding: 0 4px; border-radius: 4px; margin-left: 3px;}
.st.on-track, .st.on-time {background: rgba(46,125,50,.3);} .st.tight {background: rgba(230,120,0,.35);}
.st.late {background: rgba(198,40,40,.45);}
.neg-link {font-size: 0.72rem; opacity: 0.8;}
.thread {border-bottom: 1px solid rgba(128,128,128,.25); padding: 5px 0; font-size: 0.78rem;}
.verdict {font-size: 0.68rem; padding: 0 6px; border-radius: 9px; margin-right: 4px; background: rgba(128,128,128,.3);}
.v-agreed {background: rgba(46,125,50,.4);} .v-escalated {background: rgba(123,31,162,.45);}
.v-no {background: rgba(198,40,40,.4);} .v-in {background: rgba(42,120,214,.35);} .v-units {background: rgba(46,125,50,.4);} .v-short {background: rgba(230,120,0,.4);}
.round {font-size: 0.66rem; opacity: 0.6; margin: 3px 0 0 0; text-transform: uppercase; letter-spacing: 0.04em;}
.bubble {margin: 2px 0 2px 8px; padding: 2px 8px; border-radius: 8px; background: rgba(128,128,128,.12);}
.bubble.new {background: rgba(42,120,214,.18);}
.why {font-size: 0.72rem; opacity: 0.6;}
.dcard {border-bottom: 1px solid rgba(128,128,128,.25); padding: 5px 0; font-size: 0.78rem;}
.badge {font-size: 0.66rem; padding: 0 6px; border-radius: 9px; cursor: help;}
.b-rule {background: rgba(128,128,128,.35);} .b-ai {background: rgba(42,120,214,.45);}
.b-guard {background: rgba(255,196,0,.45);} .b-fb {background: rgba(198,40,40,.4);}
.hcard {border-bottom: 1px solid rgba(128,128,128,.25); padding: 5px 0; font-size: 0.8rem;}
.intro {border: 1px solid rgba(42,120,214,.5); border-radius: 10px; padding: 10px 16px; margin: 8px 0;}
.intro h3 {margin: 0 0 4px 0;}
div[data-testid="stButton"] button[kind="tertiary"] {padding: 0; min-height: 1.4rem; font-weight: 600;}
.st-key-feedbox, .st-key-neglist, .st-key-negdetail,
div:has(> .st-key-feedbox), div:has(> .st-key-neglist), div:has(> .st-key-negdetail) {
    height: calc(100vh - 308px) !important; max-height: calc(100vh - 308px) !important; min-height: 180px;
    flex: 0 0 calc(100vh - 308px) !important;}
.st-key-neglist div[data-testid="stButton"] button[kind="tertiary"] {text-align: left; font-size: 0.78rem;
    line-height: 1.25; justify-content: flex-start;}
.st-key-neglist div[data-testid="stVerticalBlock"] {gap: 0.1rem;}
.tentry {font-size: 0.72rem; margin: -2px 0 6px 0; padding-bottom: 4px; border-bottom: 1px solid rgba(128,128,128,.2);}
.tentry.new .verdict {box-shadow: 0 0 0 1px #2a78d6;}
.tdetail {font-size: 0.78rem;}
.chips {margin: 4px 0;}
.hchip {display: inline-block; font-size: 0.68rem; padding: 0 6px; border-radius: 9px; margin: 1px 2px 1px 0;
        background: rgba(128,128,128,.3); cursor: help;}
.c-waiting {background: rgba(42,120,214,.3);} .c-offered {background: rgba(46,125,50,.35);}
.c-awarded, .c-accepted {background: rgba(46,125,50,.6);} .c-declined, .c-cant {background: rgba(198,40,40,.4);}
.c-referee {background: rgba(123,31,162,.45);} .c-later {background: rgba(230,120,0,.35);}
.hl {font-size: 0.8rem; margin-top: 4px;}
.convo {font-size: 0.8rem; line-height: 1.4; margin: 0 0 8px 0; padding-left: 0.4rem;}
.convo .cv {margin-bottom: 3px; padding-left: 3.4rem; text-indent: -3.4rem;}
.convo .cv-t {display: inline-block; width: 3.2rem; text-indent: 0; opacity: 0.6; font-variant-numeric: tabular-nums;}
.convo .cv b span[title] {cursor: help;}
.cv-n {margin: 0 0 3px 3.4rem; font-size: 0.76rem; opacity: 0.62;}
.ai-tag {font-size: 0.68rem; opacity: 0.7; cursor: help; border-bottom: 1px dotted currentColor;}
.st-key-feedbox div[data-testid="stButton"] button[kind="tertiary"] {justify-content: flex-start; text-align: left;}
.tl-step {border-left: 2px solid rgba(128,128,128,.35); margin: 0 0 0 7px; padding: 3px 0 5px 12px; font-size: 0.8rem;
          position: relative;}
.tl-dot {position: absolute; left: -9px; top: 3px; font-size: 0.72rem;}
.tl-line {font-size: 0.76rem; opacity: 0.9; line-height: 1.35;}
.tl-rule {font-size: 0.72rem; opacity: 0.75; font-style: italic;}
.st-key-negdetail div[data-testid="stExpander"] {margin: -4px 0 4px 19px;}
.st-key-negdetail div[data-testid="stExpander"] summary {padding: 0.1rem 0.5rem; font-size: 0.72rem; min-height: 0;}
</style>
"""


def apply() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
