#!/usr/bin/env python3
"""v121b labpc runner (성수님 결정 (CoS 전달 18:06): option (a) = a new runner process on labpc).

Runs, every RUNNER_PERIOD_S seconds, three stages in order, each isolated (one failing stage never stops the others):
  memory   memory.run.run_once(market dir)   (its POST_INGEST includes the v121 LLM memory-op judge)
  harness  llm.harness2_propose watch_cycle + run_cycle, from a factory  RUNNER_H2_FACTORY="module:function"
  rlpo     rlpo.llm_live.step, from a factory                            RUNNER_RLPO_FACTORY="module:function"
A stage without a factory is logged "not_wired" and skipped (fail-closed: no default wiring is invented here).
v121c (CR v121b R2, CoS 결정 18:48): the memory stage is OFF unless RUNNER_MEMORY=1 (code default OFF; the go script
sets RUNNER_MEMORY=1 + R3_MEMORY_LLM=1 = the decided deploy config) and runs under the memory/run.lock flock
(memory.run.run_once_locked). R3_STATE comes from the seller env (go script) so memory reads/writes the seller state dir.

Separate process: started with setsid + nohup by the go script; it does not touch the kit, tmux, the buyer, the
seller or the autostart task. Single instance: flock on <dir>/runner.lock. Stop: touch <dir>/STOP or SIGTERM
(the stage in progress finishes, then exit 0). No call-count limit: caps_v4 auto-OFF (per role; quota / auth global)
is the only LLM stop. The log holds statuses / numbers only (never prompt or reply text).
Dir: RUNNER_DIR (default <work>/.r3_runner_v121).  --once runs one tick (tests / smoke).
"""
import argparse
import fcntl
import importlib
import json
import os
import signal
import sys
import time
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
VERSION = "v121c"
_STOP = {"sig": False}


def rdir():
    return Path(os.environ.get("RUNNER_DIR") or WORK / ".r3_runner_v121")


def _short(x, n=160):
    return str(x).replace("\n", " ")[:n]


def log(d, row):
    row = dict(row, v=VERSION, ts=round(time.time(), 3))
    line = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
    with open(d / "runner.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def factory(env_name):
    spec = os.environ.get(env_name, "").strip()
    if not spec:
        return None
    mod, _, fn = spec.partition(":")
    return getattr(importlib.import_module(mod), fn or "make")


def stage_memory(now):
    if os.environ.get("RUNNER_MEMORY", "0") != "1":
        return {"status": "skipped", "why": "RUNNER_MEMORY not 1 (default OFF)"}
    from memory import run as MR
    return {"status": "ok", "out": _short(MR.run_once_locked(MR.market_dir(None), now))}


def stage_harness(now):
    f = factory("RUNNER_H2_FACTORY")
    if f is None:
        return {"status": "not_wired"}
    from llm import harness2_propose as H2
    w = f()        # {"live", "runs", "target", "replay", "observe", optional "metrics", "enabled"}
    out = {"status": "ok"}
    out["watch"] = _short(H2.watch_cycle(w["runs"], w["target"], w["observe"], now))
    out["cycle"] = _short(H2.run_cycle(w["live"], w["runs"], w["target"], w["replay"], now,
                                       metrics=w.get("metrics"), enabled=w.get("enabled")))
    return out


def stage_rlpo(now):
    f = factory("RUNNER_RLPO_FACTORY")
    if f is None:
        return {"status": "not_wired"}
    w = f()        # {"loop", "llms", "source", "state"}  (state: callable -> s)
    from rlpo import llm_live
    r = llm_live.step(w["loop"], w["llms"], w["source"], w["state"](), now)
    return {"status": "ok", "llm": r.get("llm"),
            "proposal": (r.get("proposal") or {}).get("a_status"), "approver": (r.get("approver") or {}).get("a_status"),
            "watch": (r.get("watch") or {}).get("a_status") if isinstance(r.get("watch"), dict) else None}


STAGES = (("memory", stage_memory), ("harness", stage_harness), ("rlpo", stage_rlpo))


def tick(d, now=None):
    out = {}
    for name, fn in STAGES:
        if stop_requested(d):
            out[name] = "stopped"
            break
        t0 = time.monotonic()
        try:
            r = fn(time.time() if now is None else now)
        except Exception as e:                       # noqa: BLE001 - one stage never stops the runner
            r = {"status": "error", "error": type(e).__name__, "msg": _short(e, 120)}
        r["ms"] = int((time.monotonic() - t0) * 1000)
        log(d, dict(r, stage=name))
        out[name] = r["status"]
    return out


def stop_requested(d):
    return _STOP["sig"] or (d / "STOP").exists()


def _on_term(signum, frame):                          # noqa: ARG001
    _STOP["sig"] = True


def main(argv=None):
    ap = argparse.ArgumentParser(description="v121b labpc runner")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    d = rdir()
    d.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(WORK)] + [p for p in os.environ.get("RUNNER_PYTHONPATH", "").split(os.pathsep) if p]
    lk = open(d / "runner.lock", "a+")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("RUNNER refused: another runner holds " + str(d / "runner.lock"), flush=True)
        return 3
    if (d / "STOP").exists() and not a.once:
        print("RUNNER refused: STOP file present (remove it to start)", flush=True)
        return 4
    signal.signal(signal.SIGTERM, _on_term)
    (d / "runner.pid").write_text(str(os.getpid()) + "\n")
    period = float(os.environ.get("RUNNER_PERIOD_S", "600"))
    log(d, {"stage": "start", "pid": os.getpid(), "period_s": period, "once": a.once,
            "h2": bool(os.environ.get("RUNNER_H2_FACTORY")), "rlpo": bool(os.environ.get("RUNNER_RLPO_FACTORY")),
            "memory": os.environ.get("RUNNER_MEMORY", "0") == "1", "memory_llm": os.environ.get("R3_MEMORY_LLM") == "1",
            "r3_state": os.environ.get("R3_STATE", ".r3")})
    try:
        while True:
            tick(d)
            if a.once:
                break
            t_end = time.monotonic() + period
            while time.monotonic() < t_end and not stop_requested(d):
                time.sleep(min(2.0, max(0.0, t_end - time.monotonic())))
            if stop_requested(d):
                break
    finally:
        log(d, {"stage": "exit", "pid": os.getpid(), "why": "sigterm" if _STOP["sig"] else "stop_file_or_once"})
        try:
            (d / "runner.pid").unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
