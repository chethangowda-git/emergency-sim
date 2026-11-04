"""Display names (DESIGN 3.5, 13.2): neutral hospital names that fit where each site is on the map.

Display only: the simulator, run files and IDs keep their recorded names, so every run, old or new,
shows the same names and still replays byte for byte.
"""
from __future__ import annotations

HOSPITAL_NAMES = {"H1": "South End General", "H2": "Riverside Medical", "H3": "Muddy River Community"}


def hospital_name(hospital_id: str, recorded: str | None = None) -> str:
    return HOSPITAL_NAMES.get(hospital_id, recorded or hospital_id)


def hospital_label(h: dict) -> str:
    """'H1 South End General' from a recorded hospital dict."""
    return f"{h['id']} {hospital_name(h['id'], h.get('name'))}"
