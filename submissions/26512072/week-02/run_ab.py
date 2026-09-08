"""Run supplied harnesses, append real measurements, preserve raw logs.

python run_ab.py --check       readiness check without making an API call
python run_ab.py --runs 3      three runs per harness, failures included

The plan prompt and the plan parser are experiment conditions, recorded in
every log and in the note column. Runs from different conditions live in the
same results.csv but are never averaged together; summarize_results.py
refuses to pool them unless asked for a per-condition breakdown.

python run_ab.py --runs 3 --only plan_exec --plan-parser tolerant
python run_ab.py --runs 3 --only plan_exec --plan-prompt v2
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
import platform
import re
import subprocess
import time
from datetime import datetime, timezone
from functools import partial
from importlib.metadata import version
from pathlib import Path

from harness_plan_execute import PLAN_PROMPTS, SYSTEM_EXEC, run_plan_execute
from harness_react import SYSTEM, run_react
from tools_shared import BASE_URL, MAX_TOKENS, MODEL, TEMPERATURE, TIMEOUT, TOOL_SPECS, Meter

HEADER = ["run", "harness", "success", "tokens", "iters", "interventions", "note"]
ROOT = Path(__file__).resolve().parent


def read_task(path="TASK.md"):
    text = Path(path).read_text(encoding="utf-8")
    task = re.search(r"^task:\s*(.+)$", text, flags=re.M)
    expected = re.search(r"^expected:\s*(.+)$", text, flags=re.M)
    if not task or not expected:
        raise ValueError("TASK.md needs a 'task:' line and an 'expected:' line")
    return task.group(1).strip(), expected.group(1).strip()


def judge(answer: str, expected: str) -> bool:
    """The starter's precommitted criterion: expected occurs in final answer."""
    return expected.lower() in (answer or "").lower()


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def preflight():
    problems = []
    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        problems.append("OPENROUTER_API_KEY is not set; live runs skipped")
    if importlib.util.find_spec("openai") is None:
        problems.append("Install requirements.txt with this Python interpreter")
    if not MODEL.endswith(":free"):
        problems.append("AGENT_MODEL must end in :free; automatic model routing is not allowed")
    try:
        git("ls-files", "--error-unmatch", "TASK.md")
        git("diff", "--exit-code", "HEAD", "--", "TASK.md")
        git("show", "HEAD:submissions/26512072/week-02/TASK.md")
    except (OSError, subprocess.CalledProcessError):
        problems.append("Commit TASK.md with the success criterion before running")
    if not Path("app.log").is_file():
        problems.append("The unchanged starter app.log is required")
    return problems


def conditions(task, expected, max_steps, plan_prompt="v1", plan_parser="strict",
               run_order="alternating react, plan_exec"):
    sources = ["TASK.md", "app.log", "tools_shared.py", "harness_react.py",
               "harness_plan_execute.py", "run_ab.py", "requirements.txt"]
    return {
        "provider": "OpenRouter", "base_url": BASE_URL, "model": MODEL,
        "task": task, "expected": expected, "tools": TOOL_SPECS,
        "prompts": {"react": SYSTEM, "plan": PLAN_PROMPTS[plan_prompt],
                    "execute": SYSTEM_EXEC},
        "plan_prompt": plan_prompt, "plan_parser": plan_parser,
        "max_steps": max_steps, "max_replan": 1, "max_tool_rounds": 3,
        "max_tokens": MAX_TOKENS, "temperature": TEMPERATURE,
        "timeout_seconds": TIMEOUT, "sdk_retries": 0,
        "iters_definition": "attempted model calls, including plan and final answer",
        "tokens_definition": "sum of input and output usage reported by the API",
        "interventions_definition": "human approvals plus human denials; replans excluded",
        "run_order": run_order, "seed": None,
        "python": platform.python_version(), "openai": version("openai"),
        "git_commit": git("rev-parse", "HEAD"),
        "git_status": git("status", "--short", "--", "."),
        "sha256": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in sources},
    }


def next_run_number():
    seen = [0]
    if Path("results.csv").exists() and Path("results.csv").stat().st_size:
        with open("results.csv", newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != HEADER:
                raise ValueError("Existing results.csv has a different header; leave it intact")
            seen.extend(int(row["run"]) for row in reader)
    for path in Path("logs").glob("*.txt"):
        match = re.fullmatch(r"(?:react|plan_exec)-(\d+)", path.stem)
        if match:
            seen.append(int(match[1]))
    return max(seen) + 1


def run_one(run_no, name, fn, task, expected, config):
    path = Path("logs", f"{name}-{run_no:02d}.txt")
    # Exclusive creation protects previous attempts. Flush every event so an
    # interrupted process still leaves its transcript.
    with path.open("x", encoding="utf-8") as stream:
        def log(message):
            print(message, flush=True)
            stream.write(str(message) + "\n")
            stream.flush()

        meter = Meter(max_steps=config["max_steps"], log=log)
        log("[conditions] " + json.dumps(config, ensure_ascii=False))
        log(f"[run] {run_no} {name} {datetime.now(timezone.utc).isoformat()}")
        started = time.monotonic()
        note = ""
        crashed = False
        try:
            out = fn(task, max_steps=config["max_steps"], log=log, meter=meter)
            answer = out[0]
            if name == "plan_exec":
                # The condition travels with the row so no reader has to open
                # the log to know which plan variant produced it.
                note = (f"plan_prompt={config.get('plan_prompt', 'v1')} "
                        f"plan_parser={config.get('plan_parser', 'strict')} "
                        f"replans={out[2]}")
        except (Exception, KeyboardInterrupt) as error:
            crashed = True
            answer = ""
            note = f"crash: {type(error).__name__}: {error}; tokens=reported_usage_only"
            log(note)
        success = not crashed and judge(answer, expected)
        log("[final]\n" + answer)
        log(f"[judge] expected={expected!r} -> {'O' if success else 'X'}")
        log(f"[metrics] tokens={meter.tokens} iters={meter.iters} "
            f"interventions={meter.interventions} seconds={time.monotonic() - started:.3f}")
        return [run_no, name, "O" if success else "X", meter.tokens,
                meter.iters, meter.interventions, note]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, help="runs per harness")
    parser.add_argument("--max-steps", type=int, default=16, help="model calls per run, both harnesses")
    parser.add_argument("--check", action="store_true", help="readiness only; no API call or result rows")
    parser.add_argument("--plan-prompt", choices=sorted(PLAN_PROMPTS), default="v1",
                        help="planner system prompt variant; v1 is the condition of runs 1-6")
    parser.add_argument("--plan-parser", choices=("strict", "tolerant"), default="strict",
                        help="strict needs the whole reply to be the JSON plan; tolerant accepts one embedded array")
    parser.add_argument("--only", choices=("both", "react", "plan_exec"), default="both",
                        help="run one harness only; react is unaffected by the plan conditions")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.max_steps < 1:
        parser.error("--runs and --max-steps must be positive")
    os.chdir(ROOT)
    task, expected = read_task()
    problems = preflight()
    print(f"provider=OpenRouter model={MODEL} max_steps={args.max_steps} "
          f"plan_prompt={args.plan_prompt} plan_parser={args.plan_parser} only={args.only}")
    if problems:
        for problem in problems:
            print("NOT READY: " + problem)
        return 1
    if args.check:
        print("READY: no API call made")
        return 0
    harnesses = [("react", run_react),
                 ("plan_exec", partial(run_plan_execute,
                                       system_plan=PLAN_PROMPTS[args.plan_prompt],
                                       plan_parser=args.plan_parser))]
    if args.only != "both":
        harnesses = [pair for pair in harnesses if pair[0] == args.only]
    order = ("alternating react, plan_exec" if args.only == "both"
             else f"{args.only} only")
    config = conditions(task, expected, args.max_steps, args.plan_prompt,
                        args.plan_parser, order)
    run_no = next_run_number()
    Path("logs").mkdir(exist_ok=True)
    new_file = not Path("results.csv").exists() or not Path("results.csv").stat().st_size
    with open("results.csv", "a", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        if new_file:
            writer.writerow(HEADER)
            stream.flush()
        for _ in range(args.runs):
            for name, fn in harnesses:
                row = run_one(run_no, name, fn, task, expected, config)
                writer.writerow(row)
                stream.flush()
                run_no += 1
                if "KeyboardInterrupt" in row[-1]:
                    return 130
    print("results.csv updated:", ROOT / "results.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
