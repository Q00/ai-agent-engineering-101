"""The outer runtime's loop (HARNESS.md 2.2, 2.3): collection, forum, and the 2-hour slices.

    py outer.py                 # runs until stopped (ops/outer.ps1 starts it from Task Scheduler)
    py outer.py --once          # one pass of everything that is due, then exit

Every LOOP_SECONDS it writes bus/status/outer.json and runs what is due: collection every COLLECT_EVERY, a
forum turn every FORUM_EVERY (forum.py, its own process), a slice at each even hour. Nothing here trades:
the inner runtime (the kit and its hooks) keeps running on the last good strategy and facts even when this
loop is down. A failure is an alert and the loop goes on.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import atomic, bus, paths  # noqa: E402
from core.events import EventWriter  # noqa: E402

LOOP_SECONDS = 30
COLLECT_EVERY = 180
FORUM_EVERY = 300          # user 11/3 23:1x: the kit allows one post per 5 min per side; buyer posts are 0.1 of buyer ALL (agent.md)
FORUM_TIMEOUT = 300
HEALTH_EVERY = 300         # health metrics (outer/health.py)
BENCH_EVERY = 1500         # benchmark (outer/benchmark.py) shortly before each half-hour slice


def due_slice(now, last_slice):
    """The slice id to run now (each even hour, once; every 30 min in the night mode's hours), or None."""
    from outer import slice as outer_slice
    tm = time.localtime(now)
    sid = outer_slice.slice_id(now)
    due = tm.tm_hour in outer_slice.AUTO_HOURS or tm.tm_hour % 2 == 0
    return sid if due and sid != last_slice else None


def run_forum():
    r = subprocess.run([sys.executable, str(HERE / "forum.py")], capture_output=True, text=True, encoding="utf-8",
                       timeout=FORUM_TIMEOUT, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-300:])
    return (r.stdout or "").strip().splitlines()[-1:] or [""]


def once(state, w, now=None):
    """Run what is due. Each job fails on its own: an alert, and the other jobs still run."""
    from outer import collect, watch
    now = now or time.time()
    done = {}

    def job(name, fn):
        try:
            done[name] = fn()
        except Exception as e:
            done[name] = f"failed: {type(e).__name__}: {e}"[:300]
            bus.alert("outer", "warn", f"{name}_failed", done[name])
            print(f"[outer] {name}: {done[name]}", flush=True)

    job("watch", lambda: watch.check(state, now))

    def collect_round():
        c = collect.Collector()
        try:
            return c.round()
        finally:
            c.close()

    if now - state.get("collect", 0) >= COLLECT_EVERY:
        state["collect"] = now                   # also on failure: the market is not asked again at once
        job("collect", collect_round)
    if now - state.get("forum", 0) >= FORUM_EVERY:
        state["forum"] = now                     # also on failure: a broken forum turn waits for the next one
        job("forum", run_forum)
    if now - state.get("health", 0) >= HEALTH_EVERY:     # HARNESS 14.4: measured, not found by hand
        state["health"] = now
        from outer import health
        job("health", lambda: len(health.check(now)["findings"]))
    if now - state.get("benchmark", 0) >= BENCH_EVERY:   # user 11/4 03:3x: keep benchmarking the top players
        state["benchmark"] = now
        from outer import benchmark
        job("benchmark", lambda: benchmark.build(now)["ts"])
    sid = due_slice(now, state.get("slice"))
    if sid:
        state["slice"] = sid
        job("slice", start_slice)
    return done


def start_slice():
    """Start `py -m outer.slice` in its own process unless one is still running (it holds memory/facts/slice.lock)."""
    from outer import slice as outer_slice
    if outer_slice.running():
        return "skipped: the previous slice is still running"
    import os
    p = subprocess.Popen([sys.executable, "-m", "outer.slice"], cwd=HERE, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                            else {"start_new_session": True}))
    return {"started": p.pid}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    st_path = paths.memory() / "facts" / "outer_state.json"
    state = atomic.read_json(st_path, default=None) or {}
    w = EventWriter("outer-loop", source="outer")
    try:
        while True:
            now = time.time()
            active = atomic.read_json(paths.memory() / "strategy" / "active.json", default={}) or {}
            bus.write_status("outer", strategy_version=active.get("version"),
                             note=f"last slice {state.get('slice')}, last collect {state.get('collect', 0):.0f}")
            try:
                done = once(state, w, now)
                if done:
                    w.emit("outer.pass", {k: (v.get("steps") if k == "slice" and isinstance(v, dict) else v)
                                          for k, v in done.items()})
            except Exception as e:                   # one failure is an alert, not the end of the loop
                bus.alert("outer", "warn", "outer_failed", f"{type(e).__name__}: {e}"[:300])
                print(f"[outer] {type(e).__name__}: {e}", flush=True)
            atomic.write_json(st_path, state)
            if a.once:
                print(json.dumps(state))
                return
            time.sleep(LOOP_SECONDS)
    finally:
        w.close()


if __name__ == "__main__":
    main()
