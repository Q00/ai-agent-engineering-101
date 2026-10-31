"""The Luna shotgun series with DeepSeek V4.1 Flash: same protocol, prompts, tool and designs.

Only the model settings differ (config.json: DeepInfra FP8, reasoning low, temperature 1.0, top_p 0.95).
One design per process, because the armed speaking rule is a module-level setting.
The task unit is one episode, so up to 100 episodes of a design run at once; each episode has its own
log pair logs/<run>-s<scenario>.jsonl/.txt. The first attempt (run-level tasks, jobs=2) was stopped at
77 episodes and is kept under the suite names without "-ep-".

control           nobody holds anything                                   36 episodes
shotgun-auto      the armed side gets the shotgun tool, uses it at will   72
shotgun-forced    same, but must call it in its first turn's first request 72
holding-with-tool the armed side's prompt says it holds a shotgun, plus the tool 72
holding-only      the prompt sentence alone, no tool                       72
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "armed_tool"))
import run_holding as holding  # also loads run_shotgun and run_luna

shotgun, luna = holding.shotgun, holding.luna
# A fresh isolated copy of the armed protocol, so no other design's settings leak in.
armed = luna.load_module("deepseek_protocol", ROOT / "armed_tool/run_armed.py")
base_lab = armed.lab
for name in ("TOOL", "TOOL_RESULT", "NARRATION"):
    setattr(armed, name, getattr(shotgun.armed, name))
armed.lab = holding.HoldingProtocol(base_lab)  # adds the sentence only when scenario["holder"] is the speaker
armed.EFFORT = "low"
_speak = armed.speak_armed
DESIGNS = {  # sides, tool offered, holder sentence, speaking rule
    "control": {"sides": ("none",), "tool": False, "holding": False},
    "shotgun-auto": {"sides": armed.ARMED, "tool": True, "holding": False},
    "shotgun-forced": {"sides": armed.ARMED, "tool": True, "holding": False},
    "holding-with-tool": {"sides": armed.ARMED, "tool": True, "holding": True},
    "holding-only": {"sides": armed.ARMED, "tool": False, "holding": True},
}
SCHEDULE_SEED = 20260928
PROBE_SCENARIO = 2
MAX_JOBS = 100


def speak_forced(role, other, messages, call, emit, turn, stats):
    """As run_shotgun.speak_forced, bound to this copy: force the first request of the first armed turn."""
    pending = {"force": turn == (1 if role == "buyer" else 2)}
    def forcing_call(r, m, **kw):
        if pending["force"]:
            pending["force"] = False
            kw["tool_choice"] = shotgun.FORCED_CHOICE
            emit("forced_tool_choice", turn=turn, speaker=role)
        return call(r, m, **kw)
    return _speak(role, other, messages, forcing_call, emit, turn, stats)


def configure(design):
    armed.speak_armed = {"shotgun-forced": speak_forced, "holding-only": holding.speak_without_tool}.get(design, _speak)


def suite_name(design, probe=False):
    return f"deepseek-{design}-{'probe-' if probe else ''}ep-20260928"


def prepare_suite(design, suite, jobs, tasks, scenarios, config):
    sources = [HERE / "run_deepseek.py", HERE / "config.json", ROOT / "armed_tool/run_holding.py",
               ROOT / "armed_tool/run_shotgun.py", ROOT / "armed_tool/run_armed.py", ROOT / "armed_tool/transport.py",
               ROOT / "reasoning_effort/run_luna.py", ROOT / "lab/experiment.py", ROOT / "scenarios.json"]
    spec = DESIGNS[design]
    manifest = {"suite": suite, "design": design, "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "inputs": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                "config": config, "scenarios": scenarios, "max_turns": base_lab.MAX_TURNS, "repeats": 3, "jobs": jobs,
                "schedule_seed": SCHEDULE_SEED, "task_unit": "episode", "log_layout": "logs/<run>-s<scenario>.jsonl",
                "tasks": tasks, "armed": list(spec["sides"]),
                "tool": armed.TOOL if spec["tool"] else None,
                "tool_result": armed.TOOL_RESULT if spec["tool"] else None,
                "narration": armed.NARRATION if spec["tool"] else None,
                "max_tool_rounds": armed.MAX_TOOL_ROUNDS if spec["tool"] else None,
                "forced_choice": shotgun.FORCED_CHOICE if design == "shotgun-forced" else None,
                "luna_counterpart": {"control": "reasoning_effort/runs/luna-effort-20260928 (low)",
                                     "shotgun-auto": "armed_tool/runs/shotgun-auto-luna-20260928",
                                     "shotgun-forced": "armed_tool/runs/shotgun-forced-luna-20260928",
                                     "holding-with-tool": "armed_tool/runs/holding-with-tool-luna-20260928",
                                     "holding-only": "armed_tool/runs/holding-only-luna-20260928"}[design],
                "format": base_lab.FORMAT, "roles": base_lab.ROLE, "common": base_lab.COMMON,
                "reader_system": base_lab.READER_SYSTEM}
    if spec["holding"]:
        manifest["holding_sentence"] = holding.HOLDING
    out = HERE / "runs" / suite
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        current = json.loads(json.dumps(manifest))
        if any(old[k] != current[k] for k in current if k != "source_commit"):
            raise ValueError("Resume requires identical inputs and settings")
        manifest = old
    else:
        with manifest_path.open("x") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write("\n")
    csv_path = out / "results.csv"
    if csv_path.exists():
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != armed.HEADER:
                raise ValueError("Unexpected results header")
            rows = list(reader)
        done = {(r["run"], r["scenario"]) for r in rows}
        if len(done) != len(rows):
            raise ValueError("Duplicate episode identity")
    else:
        done = set()
        with csv_path.open("x", newline="") as f:
            csv.writer(f, lineterminator="\n").writerow(armed.HEADER)
    return manifest, csv_path, done


def run_episode(design, suite, side, condition, repeat, sid, *, scenarios, config, key, manifest, csv_path, done, lock):
    """One episode with its own client and log pair; the holder sentence follows the design."""
    run_id = f"{suite}-{side}-{condition}-{repeat:02d}"
    if (run_id, str(sid)) in done:
        return
    s = next(x for x in scenarios if x["id"] == sid)
    spec, armed_role = DESIGNS[design], (None if side == "none" else side)
    client = armed.OpenRouterClient(key, config)
    stem = ROOT / "logs" / f"{run_id}-s{sid}"
    with stem.with_suffix(".jsonl").open("a") as events, stem.with_suffix(".txt").open("a") as console:
        meter = luna.UsageMeter()
        def emit(event, **fields):
            meter.observe(event, fields)
            record = {"time": datetime.now(timezone.utc).isoformat(), "run": run_id,
                      "scenario": sid, "event": event, **fields}
            line = base_lab.redact(json.dumps(record, ensure_ascii=False, allow_nan=False), key)
            events.write(line + "\n"); events.flush()
            if event not in ("request", "response", "usage"):
                console.write(line + "\n"); console.flush()
            if event in ("episode_result", "crash", "http_error"):
                with lock:
                    print(line, flush=True)
        emit("setup", manifest=manifest, design=design, armed_role=side, condition=condition, repeat=repeat)
        episode = {**s, "holder": armed_role} if spec["holding"] else s
        row = dict.fromkeys(armed.HEADER, "")
        row.update(run=run_id, condition=condition, scenario=sid, deal_possible=int(s["reserve"] <= s["budget"]),
                   armed_role=side, reasoning_effort=armed.EFFORT)
        emit("episode_start", systems={r: armed.lab.system_prompt(r, episode, condition) for r in ("buyer", "seller")},
             tools={armed_role: [manifest["tool"]]} if manifest["tool"] else {})
        def call(role, messages, response_format, **tool_args):
            return client.complete(messages, emit, sid, role, response_format=response_format, **tool_args)
        started = time.monotonic()
        try:
            armed.negotiate(episode, condition, armed_role, call, emit, row)
        except Exception as exc:
            row.update(outcome="", price="", correct="", violation="",
                       note=f"crashed: {type(exc).__name__}: {base_lab.redact(str(exc), key)}")
            emit("crash", error=row["note"])
        row.update(status=luna.status_of(row), elapsed_seconds=round(time.monotonic() - started, 3), **meter.row())
        emit("episode_result", result=row)
        with lock:
            with csv_path.open("a", newline="") as f:
                csv.DictWriter(f, fieldnames=armed.HEADER, lineterminator="\n").writerow(row)
            done.add((run_id, str(sid)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", choices=DESIGNS, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--jobs", type=int, default=20, help=f"concurrent episodes, 1..{MAX_JOBS}")
    parser.add_argument("--probe", action="store_true", help="lamp only, free condition, first repeat")
    args = parser.parse_args()
    if not 1 <= args.jobs <= MAX_JOBS:
        parser.error(f"--jobs must be between 1 and {MAX_JOBS}")
    configure(args.design)
    config = json.loads((HERE / "config.json").read_text())
    scenarios = json.loads((ROOT / "scenarios.json").read_text())
    tasks = [(side, c, r, s["id"]) for side in DESIGNS[args.design]["sides"] for c in base_lab.CONDITIONS
             for r in range(1, 4) for s in scenarios]
    random.Random(SCHEDULE_SEED).shuffle(tasks)
    if args.probe:
        scenarios = [s for s in scenarios if s["id"] == PROBE_SCENARIO]
        tasks = [(side, "free", 1, PROBE_SCENARIO) for side in DESIGNS[args.design]["sides"]]
    suite = suite_name(args.design, args.probe)
    manifest, csv_path, done = prepare_suite(args.design, suite, args.jobs, tasks, scenarios, config)
    key, lock = base_lab.read_key(args.env_file), threading.Lock()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_episode, args.design, suite, *task, scenarios=scenarios, config=config, key=key,
                               manifest=manifest, csv_path=csv_path, done=done, lock=lock) for task in tasks]
        for future in futures:
            future.result()
    print(f"Completed suite {suite}: {len(done)} episodes", flush=True)


if __name__ == "__main__":
    main()
