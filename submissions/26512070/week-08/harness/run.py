"""The runner of the new layers (design 20-5 step 3): it replaces outer.py.

    py -m harness.run            # runs until stopped (ops/harness.ps1 starts it, like ops/outer.ps1 did)
    py -m harness.run --once     # one pass of everything that is due

Kept from outer.py: collection (outer/collect.py), the forum turn (forum.py, its own process), the benchmark.
New: market rules watch, health and emergency (harness.ops), the class view, participant knowledge, role reports,
the capability list, and the meta-harness cycle at each even hour in its own process (it calls models for minutes).
Nothing here trades: the kit and its hooks keep running on the last good strategy when this loop is down. The
status file stays bus/status/outer.json so the kit's checks and the status page read it as before.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, bus, paths  # noqa: E402
from core.events import EventWriter  # noqa: E402

LOOP_SECONDS = 30
EVERY = {"collect": 180, "forum": 300, "health": 300, "benchmark": 1500, "rules": 1800, "score_class": 600,
         "participant": 1800, "reports": 3600, "capability": 3600}
FORUM_TIMEOUT = 300
META_LOCK_S = 3 * 3600          # a cycle older than this is taken as dead


def run_forum():
    from harness import ops
    if ops.stopped("forum_stop"):
        return "stopped (emergency)"
    r = subprocess.run([sys.executable, str(HERE / "forum.py")], capture_output=True, text=True, encoding="utf-8",
                       timeout=FORUM_TIMEOUT, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(f"forum.py exited {r.returncode}: {(r.stderr or '')[-300:]}")
    return "ok"


def collect_round():
    from outer import collect
    c = collect.Collector()
    try:
        return c.round()
    finally:
        c.close()


def meta_due(now, last):
    tm = time.localtime(now)
    sid = time.strftime("%Y-%m-%dT%H", tm)
    return sid if tm.tm_hour % 2 == 0 and sid != last else None


def start_meta():
    lock = paths.memory() / "harness" / "meta.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < META_LOCK_S:
        return "skipped: the previous cycle is still running"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(time.time()), encoding="utf-8")
    code = "from harness import meta; import pathlib, sys\ntry:\n    meta.cycle()\nfinally:\n" \
           f"    pathlib.Path({str(lock)!r}).unlink(missing_ok=True)"
    p = subprocess.Popen([sys.executable, "-c", code], cwd=HERE, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                            else {"start_new_session": True}))
    return {"started": p.pid}


SHADOW_SKIP = ("collect", "forum", "benchmark", "health")   # what touches the market or the live outer's files


def once(state, now, shadow=False):
    """One pass. shadow: nothing that touches the market or the live outer's files (design 20-5: a trial run beside
    the live harness, writing only under HARNESS_OUT)."""
    from harness import capability, metrics, ops, participant, rules_watch, score_class
    done = {}

    def job(name, fn):
        state[name] = now                       # also on failure: not asked again at once
        try:
            done[name] = fn()
        except Exception as e:                  # one failure is an alert, not the end of the loop
            done[name] = f"failed: {type(e).__name__}: {e}"[:300]
            bus.alert("outer", "warn", f"{name}_failed", done[name])

    def due(name):
        if shadow and name in SHADOW_SKIP:
            return False
        return now - state.get(name, 0) >= EVERY[name]

    changed = False
    if due("rules"):
        job("rules", lambda: rules_watch.check(now))
        changed = any(v.get("changed") for v in (done.get("rules") or {}).values()) \
            if isinstance(done.get("rules"), dict) else False
    if due("collect"):
        job("collect", collect_round)
    if due("forum"):
        job("forum", run_forum)
    if shadow and now - state.get("usage", 0) >= EVERY["health"]:
        state["usage"] = now
        done["usage"] = ops.model_usage(now)
    if due("health") or (changed and not shadow):
        job("health", lambda: len(ops.health(now, rules_changed=changed)["findings"]))
    if due("benchmark"):
        from outer import benchmark
        job("benchmark", lambda: benchmark.build(now)["ts"])
    if due("score_class"):
        job("score_class", lambda: str(score_class.write(score_class.latest(now))))
    if due("participant"):
        job("participant", lambda: len(participant.to_knowledge(*participant.build(now), now=now)))
    if due("reports"):
        job("reports", lambda: [metrics.report(r, now)["role"] for r in ("buyer", "shop")])
    if due("capability"):
        job("capability", lambda: str(capability.write()))
    sid = meta_due(now, state.get("meta"))
    if sid:
        state["meta"] = sid
        try:
            done["meta"] = start_meta()
        except Exception as e:
            done["meta"] = f"failed: {type(e).__name__}: {e}"[:300]
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--shadow", action="store_true", help="no market, no live outer files; HARNESS_OUT must be set")
    ap.add_argument("--minutes", type=float, default=0, help="stop after this long (0: run until stopped)")
    a = ap.parse_args()
    if a.shadow and not os.environ.get("HARNESS_OUT"):
        sys.exit("--shadow needs HARNESS_OUT (a folder outside the live memory)")
    from harness import out_dir
    st_path = (out_dir() if a.shadow else paths.memory() / "harness") / "run_state.json"
    t_end = time.time() + a.minutes * 60 if a.minutes else None
    state = atomic.read_json(st_path, default=None) or {}
    w = EventWriter("harness-shadow" if a.shadow else "harness-run", source="outer")
    try:
        while True:
            now = time.time()
            active = atomic.read_json(paths.memory() / "strategy" / "active.json", default={}) or {}
            if not a.shadow:
                bus.write_status("outer", strategy_version=active.get("version"),
                                 note=f"harness.run; last meta {state.get('meta')}, last collect {state.get('collect', 0):.0f}")
            done = once(state, now, shadow=a.shadow)
            if done and not a.shadow:                   # a trial run leaves no events in the live record
                w.emit("outer.pass", {k: (v if isinstance(v, (int, float, str)) else json.dumps(v, default=str)[:200])
                                      for k, v in done.items()})
            atomic.write_json(st_path, state)
            if done and a.shadow:
                print(time.strftime("%H:%M:%S"), json.dumps(done, default=str, ensure_ascii=False)[:600], flush=True)
            if a.once or (t_end and time.time() >= t_end):
                print(json.dumps(done, default=str, ensure_ascii=False)[:3000])
                return
            time.sleep(LOOP_SECONDS)
    finally:
        w.close()


if __name__ == "__main__":
    main()
