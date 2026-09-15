"""Run the optional ontology and language-repair Contract Net extension."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from extended_contract_net import run_extended_contract_net
from ontology import OntologyState
from run import OpenAICompatibleChat, REPOSITORY_ROOT, load_env, new_run_id


ROOT = Path(__file__).resolve().parent
HEADER = [
    "run", "tasks", "correct", "messages", "unassigned", "misawards",
    "clarifications", "semantic_warnings", "note",
]


def append_result(row: dict) -> None:
    path = ROOT / "extended_results.csv"
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADER, lineterminator="\n")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def main() -> int:
    load_env(REPOSITORY_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--continue-state", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--limit", type=int, help="limit tasks in smoke mode")
    parser.add_argument(
        "--model",
        default=os.environ.get("AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free"),
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args()
    if args.limit is not None and (not args.smoke or args.limit < 1):
        parser.error("--limit must be positive and used with --smoke")

    state_path = ROOT / "state" / "latest.json"
    source = state_path if args.continue_state and state_path.exists() else ROOT / "ontology_seed.json"
    ontology = OntologyState.from_file(source)
    client = OpenAICompatibleChat(args.model, args.temperature)
    run_id = new_run_id("extended")
    log_dir = ROOT / ("smoke" if args.smoke else "extended_logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    with (log_dir / f"{run_id}.jsonl").open("x", encoding="utf-8") as log:
        def emit(event: str, **data) -> None:
            line = json.dumps({"event": event, **data}, ensure_ascii=False)
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()

        emit("setup", run=run_id, mode="extended", model=args.model,
             temperature=args.temperature, continued=args.continue_state)
        tasks = json.loads((ROOT / "tasks.json").read_text(encoding="utf-8"))
        if args.limit is not None:
            tasks = tasks[:args.limit]
        try:
            metrics = run_extended_contract_net(tasks, client, emit, ontology)
            row = {"run": run_id, **metrics, "note": ""}
            emit("summary", **row, llm_calls=client.calls)
            failed = 0
        except Exception as exc:
            row = dict.fromkeys(HEADER, "")
            row.update(run=run_id, note=type(exc).__name__)
            emit("crash", **row, llm_calls=client.calls)
            failed = 1

    ontology.save(state_path)
    if not args.smoke:
        append_result(row)
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
