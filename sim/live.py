"""Live runs (DESIGN 13.3): the simulation advances while the dashboard watches it.

A background thread steps the world one minute at a time, paced to the chosen speed. Pause, resume,
one-minute steps and incidents placed on the map are its only inputs. Pacing only adds waiting between
minutes, so a finished live run saves exactly the files `runner.run` saves for the same seed, policy
and placed incidents.
"""
from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path

from sim.generator import SPAWN_MAX_VICTIMS, SPAWN_TYPES, materialize, spawn_id, spawn_spec
from sim.runner import RUNS_DIR, Prepared, Stepper, incident_victims, prepare, save

MINUTES_PER_SECOND_1X = 1.0   # 1x: one simulated minute per real second (DESIGN 13.3)
QUIET_RATE = 30.0             # minutes per second while skipping quiet time
DECIDING_AFTER = 0.3          # seconds a minute may take before the clock says the agents are deciding
OPEN_VICTIM = ("waiting", "assigned", "in_transit", "at_door")


def is_quiet(minute: dict) -> bool:
    """Nothing to watch: no unit is driving and no incident has victims still to reach a hospital."""
    return (not any(u["trip"] for u in minute["units"])
            and not any(v["status"] in OPEN_VICTIM for inc in minute["incidents"] for v in inc["victims"]))

_ids = itertools.count(1)


class LiveRun:
    def __init__(self, seed: int, policy: str = "greedy", *, rate: float = MINUTES_PER_SECOND_1X,
                 paused: bool = False, skip_quiet: bool = False, runs_dir: Path = RUNS_DIR, **run_kwargs) -> None:
        self.prep: Prepared = prepare(seed, policy, **run_kwargs)
        self.key = f"live-{next(_ids)}"
        self.runs_dir = runs_dir
        self.run_id: str | None = None
        self.error: str | None = None
        self._stepper = Stepper(self.prep.world, self.prep.policy)
        self._cond = threading.Condition()
        self._world_lock = threading.Lock()
        self._rate, self._paused, self._steps, self._stopped = rate, paused, 0, False
        self._skip_quiet, self._quiet = skip_quiet, False
        self._due = 0.0
        self._busy_since: float | None = None
        self._published_at = 0.0
        self._trips: dict[str, dict] = {}
        self._view: dict | None = None
        self._thread = threading.Thread(target=self._loop, name=self.key, daemon=True)

    # ----- the clock -----

    def start(self) -> LiveRun:
        self._thread.start()
        return self

    def _loop(self) -> None:
        try:
            self.advance()                    # minute 0 always shows, even when starting paused
            while not self._stepper.done:
                if not self._wait_turn():
                    return
                self.advance()
            self.finish()
        except Exception as exc:              # shown on the dashboard; the run is not saved
            self.error = f"{type(exc).__name__}: {exc}"

    def _wait_turn(self) -> bool:
        with self._cond:
            while not self._stopped:
                if self._steps:
                    self._steps -= 1
                    return True
                if self._paused:
                    self._cond.wait()
                    continue
                wait = self._due - time.monotonic()
                if wait <= 0:
                    return True
                self._cond.wait(wait)
            return False

    def advance(self) -> None:
        """Simulate and publish one minute."""
        with self._world_lock:
            self._busy_since = time.monotonic()
            self._stepper.advance()
            self._publish()
            self._busy_since = None
        now = time.monotonic()
        with self._cond:
            self._published_at = now
            self._due = max(self._due + 1 / self.rate, now)

    def finish(self) -> str:
        with self._world_lock:
            self.run_id = save(self.prep, self.runs_dir).name
        return self.run_id

    # ----- controls -----

    def pause(self) -> None:
        with self._cond:
            self._paused = True
            self._cond.notify_all()

    def resume(self) -> None:
        with self._cond:
            self._paused, self._due = False, time.monotonic()
            self._cond.notify_all()

    def step(self) -> None:
        """One more minute, then stay paused."""
        with self._cond:
            if self._paused and not self.done:
                self._steps += 1
                self._cond.notify_all()

    def set_rate(self, rate: float) -> None:
        with self._cond:
            self._rate = rate
            self._due = min(self._due, time.monotonic() + 1 / self.rate)
            self._cond.notify_all()

    def set_skip_quiet(self, on: bool) -> None:
        """Fast-forward while nothing happens (DESIGN 13.3); like speed, it never changes the outcome."""
        with self._cond:
            self._skip_quiet = on
            self._due = min(self._due, time.monotonic() + 1 / self.rate)
            self._cond.notify_all()

    def stop(self) -> None:
        """Abandon the run; nothing is saved."""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    def spawn(self, node: str, kind: str | None = None, n_victims: int | None = None) -> dict:
        """Place an incident at a road node; it is reported at the next minute (DESIGN 3.3)."""
        with self._world_lock:
            w = self.prep.world
            t = self._stepper.minutes
            if self.done or not t:
                raise ValueError("run_not_running")
            if t >= w.cfg.horizon:
                raise ValueError("too_late")
            if node not in w.roads.graph:
                raise ValueError("unknown_node")
            if kind is not None and kind not in SPAWN_TYPES:
                raise ValueError("unknown_incident_type")
            if n_victims is not None and not 1 <= n_victims <= SPAWN_MAX_VICTIMS:
                raise ValueError("bad_victim_count")
            k = len(self.prep.spawns) + 1
            raw = spawn_spec(self.prep.seed, w.cfg, k, t, node, kind, n_victims)
            w.add_incident(*materialize(w.cfg, w.roads, spawn_id(k), raw))
            self.prep.spawns.append(raw)
            return raw

    # ----- what the dashboard reads -----

    @property
    def done(self) -> bool:
        return self.run_id is not None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def skipping(self) -> bool:
        return self._skip_quiet and self._quiet

    @property
    def rate(self) -> float:
        """Minutes per second right now: the chosen speed, or the quiet-time rate while skipping."""
        return max(self._rate, QUIET_RATE) if self.skipping else self._rate

    @property
    def deciding(self) -> bool:
        busy = self._busy_since
        return busy is not None and time.monotonic() - busy > DECIDING_AFTER

    def ticking(self) -> bool:
        """True while the picture can still change without a click."""
        if self.error or self._stopped or self.done:
            return False
        return (self._view is None or not self._paused or self._steps > 0 or self._busy_since is not None
                or self._stepper.done)

    def view(self) -> dict | None:
        """The run so far, shaped like a saved run (`run_store.load_run`); None before minute 0."""
        return self._view

    def clock(self) -> float:
        """The minute to draw: the newest one, plus how far into the next minute the clock has run."""
        view = self._view
        if view is None:
            return 0.0
        minute = len(view["timeline"]) - 1
        if self._paused or self._stepper.done:
            return float(minute)
        return minute + min(0.99, (time.monotonic() - self._published_at) * self.rate)

    def _publish(self) -> None:
        w = self.prep.world
        by_unit: dict[str, list[dict]] = {}
        for trip in w.trips:
            d = self._trips.get(trip.id)
            if d is None or d["ended_at"] != trip.ended_at:
                d = {**trip.to_dict(), "route": trip.route}
                d["end_t"] = d["ended_at"] if d["ended_at"] is not None else d["arrive_t"]
                self._trips[trip.id] = d
            by_unit.setdefault(trip.unit, []).append(d)
        config = {"run_id": None, "seed": self.prep.seed, "policy": self.prep.policy.name,
                  "sim": w.cfg.to_dict(), "spawns": list(self.prep.spawns)}
        self._view = {"run_id": None, "config": config, "events": list(w.events), "trips_by_unit": by_unit,
                      "timeline": list(w.timeline), "victims": incident_victims(w)}
        self._quiet = is_quiet(w.timeline[-1])
