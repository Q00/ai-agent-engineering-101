from __future__ import annotations
import argparse


import csv
import json
import os
import re

from datetime import datetime, timezone
from pathlib import Path

from chat import Chat, Meter
#from tools_shared import Chat, Meter
from contractor import create_team
from manager import run_round

ROOT = Path(__file__).resolve().parent
CONDITIONS = ("baseline", "homogeneous", "overconfident")
CSV_FIELDS = [
    "run", "condition", "tasks", "correct", "messages", "unassigned", "misawards",
    "execution_success", "execution_fail", "execution_messages",
    "gold_ok_exec_ok", "gold_ok_exec_fail", "misaward_exec_ok", "misaward_exec_fail", "note",
]



PROVIDER = "openai"
MODEL = os.environ.get("AGENT_MODEL", "muse-spark-1.3")
DEFAULT_TEMPERATURE = float(os.environ.get("CNP_TEMP", "0"))
DEFAULT_MAX_TOKENS = int(os.environ.get("CNP_MAX_TOKENS", "2048"))


def make_logger(logs_dir: Path, condition: str, run_id: int, provider: str, model: str, temperature) -> tuple:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = logs_dir / f"{condition}_run{run_id}_{stamp}.txt"
    lines = [f"provider={provider} model = {model} temperature = {temperature}"]

    def log(msg: str) -> None:
        lines.append(msg)
        print(msg)
    
    def flush(note: str = "") -> Path:
        if note:
            lines.append(f"note={note}")
        path.write_text("\n".join(lines) + "\n")

        return path
    return log, flush


def load_tasks(path: Path) -> list[dict]:
    return json.loads(path.read_text())

def append_result(csv_path:Path, row:dict) -> None:
    new = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in CSV_FIELDS})

def run_one(condition:str, run_id: int, tasks: list[dict], logs_dir: Path, results_path: Path, execute_awards: bool) -> None:
    chat = Chat(meter = Meter(), model = MODEL, provider = PROVIDER, temperature=DEFAULT_TEMPERATURE, max_tokens=DEFAULT_MAX_TOKENS, mock=False)
    team = create_team(condition)
    print(f"chat.header_line(): {chat.header_line()}")
    log, flush = make_logger(Path("logs"), condition, run_id, PROVIDER, MODEL, 0)

    note =""

    try:
        result = run_round(tasks, team, chat, log=log, execute_awards=execute_awards)
        note = (
            f"parse_fails={result.parse_fails}; tokens={chat.meter.tokens}; "
            f"calls={chat.meter.calls}; errors={chat.meter.errors}; "
            f"exec_ok={result.execution_success}/{result.execution_attempted}; "
            f"gold_ok_exec_ok={result.gold_ok_exec_ok}; "
            f"misaward_exec_ok={result.misaward_exec_ok}"
        )
        log(note)
        row = {
            "run": run_id,
            "condition": condition,
            "tasks": result.tasks,
            "correct": result.correct,
            "messages": result.messages,
            "unassigned": result.unassigned,
            "misawards": result.misawards,
            "execution_success": result.execution_success,
            "execution_fail": result.execution_fail,
            "execution_messages": result.execution_messages,
            "gold_ok_exec_ok": result.gold_ok_exec_ok,
            "gold_ok_exec_fail": result.gold_ok_exec_fail,
            "misaward_exec_ok": result.misaward_exec_ok,
            "misaward_exec_fail": result.misaward_exec_fail,
            "note": note,
        }
        flush(f"parse_fails={result.parse_fails}")
    
    except Exception as e:
        note = f"CRASH: {e}"
        log(note)
        row = {
            "run": run_id,
            "condition": condition,
            "tasks": len(tasks),
            "correct": 0,
            "messages": 0,
            "unassigned": 0,
            "misawards": 0,
            "execution_success": 0,
            "execution_fail": 0,
            "execution_messages": 0,
            "gold_ok_exec_ok": 0,
            "gold_ok_exec_fail": 0,
            "misaward_exec_ok": 0,
            "misaward_exec_fail": 0,
            "note": note,
        }
        flush(str(e))
    
    append_result(results_path, row)
    print(f"wrote {logs_dir.name} | {row}")

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tasks", default=str(ROOT / "tasks.json"))
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--condition", choices=CONDITIONS, default=None)
    p.add_argument("--no-execute", action="store_true")

    args = p.parse_args()

    tasks = load_tasks(Path(args.tasks))
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(exist_ok = True)
    results_path = ROOT / "results.csv"
    if args.condition is None and results_path.exists():
        results_path.unlink()
    
    coniditions = (args.condition,) if args.condition else CONDITIONS
    for cond in coniditions:
        for i in range(1, args.repeats + 1):
            run_one(cond, i, tasks, logs_dir, results_path, execute_awards=not args.no_execute)

if __name__ == "__main__":
    main()