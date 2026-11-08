"""Guard the six-hour code overseer using native Claude /usage, not token estimates.

usage_guard.py --check
usage_guard.py --report /path/to/report.md -- claude -p ...

Checks before launch and every minute while running. At the session/week limit,
or if usage cannot be read, pause the overseer (exit 4). Regular market agents and
ADMIL are not stopped. A later scheduled run resumes only after a successful check.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
LINE = re.compile(r"Current (session|week \(all models\)):\s*(\d+(?:\.\d+)?)% used(?:\s*·\s*resets ([^\r\n]+))?")
NAMES = {"session": "session", "week (all models)": "week"}


def parse_usage(text):
    snapshot = {NAMES[m.group(1)]: {"pct": float(m.group(2)), "resets": m.group(3)} for m in LINE.finditer(text)}
    if set(snapshot) != {"session", "week"} or any(not 0 <= x["pct"] <= 100 for x in snapshot.values()):
        raise ValueError("native /usage did not provide valid session and week percentages")
    return snapshot


def probe_usage(timeout=20):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with tempfile.TemporaryDirectory(dir=HERE, prefix=".usage-probe-") as empty:
        response = subprocess.run(["claude", "-p", "/usage"], cwd=empty, env=env,
                                  capture_output=True, text=True, timeout=timeout)
    if response.returncode:
        raise RuntimeError("native /usage command failed")
    return parse_usage(response.stdout)


def reached(snapshot, limits):
    return [f"{key} {snapshot[key]['pct']:g}% >= {limits[key]}% (resets {snapshot[key].get('resets')})"
            for key in ("session", "week") if snapshot[key]["pct"] >= limits[key]]


def audit(path, event, **fields):
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"ts": time.time(), "event": event, **fields}, ensure_ascii=False) + "\n")


def terminate(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def pause(reason, snapshot, log, phase, report=None, interrupted=False):
    payload = {"is_error": False, "paused": True, "subtype": "quota_paused", "quota": snapshot,
               "total_cost_usd": None if interrupted else 0, "num_turns": None if interrupted else 0,
               "result": f"자동 보완 대기: {reason}. 다음 예약 실행에서 사용량을 다시 확인합니다."
                         + (" 진행 중 작업을 중단했으므로 다음 실행에서 미커밋 변경을 먼저 확인해야 합니다." if interrupted else "")}
    audit(log, "paused", phase=phase, reason=reason, usage=snapshot, interrupted=interrupted)
    if report is not None and not report.exists():
        report.parent.mkdir(exist_ok=True)
        report.write_text(payload["result"] + "\n\n" + json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 4, json.dumps(payload, ensure_ascii=False), ""


def run_guard(command, limits, log, report=None, probe=None):
    probe = probe or (lambda: probe_usage(limits["probe_timeout_seconds"]))
    try:
        snapshot = probe()
    except Exception as exc:
        return pause(f"사용량 확인 실패 ({type(exc).__name__})", {}, log, "before", report)
    audit(log, "checked", phase="before", usage=snapshot, limits={k: limits[k] for k in ("session", "week")})
    over = reached(snapshot, limits)
    if over:
        return pause("; ".join(over), snapshot, log, "before", report)
    if not command:
        return 0, json.dumps({"allowed": True, "usage": snapshot}, ensure_ascii=False), ""

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True, env=env)
    audit(log, "started", pid=process.pid)
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=limits["check_every_seconds"])
                audit(log, "finished", returncode=process.returncode)
                return process.returncode, stdout, stderr
            except subprocess.TimeoutExpired:
                try:
                    snapshot = probe()
                    audit(log, "checked", phase="during", usage=snapshot)
                    over = reached(snapshot, limits)
                    reason = "; ".join(over)
                except Exception as exc:
                    reason = f"사용량 확인 실패 ({type(exc).__name__})"
                if reason and process.poll() is None:
                    terminate(process)
                    stdout, stderr = process.communicate()
                    # Preserve partial output; never claim an interrupted call had zero cost.
                    stopped = log.parent / f"overseer-interrupted-{time.time_ns()}.json"
                    stopped.write_text(json.dumps({"stdout": stdout, "stderr": stderr}, ensure_ascii=False), encoding="utf-8")
                    return pause(reason, snapshot, log, "during", report, interrupted=True)
    finally:
        terminate(process)


def load_limits(path):
    limits = json.loads(path.read_text(encoding="utf-8"))
    for key in ("session", "week"):
        if type(limits.get(key)) not in (int, float) or not 0 < limits[key] <= 100:
            raise ValueError(f"invalid {key} limit")
    for key in ("check_every_seconds", "probe_timeout_seconds"):
        if type(limits.get(key)) not in (int, float) or not 0 < limits[key] <= 120:
            raise ValueError(f"invalid {key}")
    return limits


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--config", type=Path, default=HERE / "usage_limits.json")
    parser.add_argument("--log", type=Path, default=HERE / "logs/overseer-quota.jsonl")
    parser.add_argument("--report", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.check:
        command = []
    elif not command:
        parser.error("provide --check or a command after --")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    code, stdout, stderr = run_guard(command, load_limits(args.config), args.log, args.report)
    sys.stdout.write(stdout + ("\n" if stdout and not stdout.endswith("\n") else ""))
    sys.stderr.write(stderr)
    sys.exit(code)
