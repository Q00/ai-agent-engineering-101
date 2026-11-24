from __future__ import annotations
import argparse
import csv

import json
from pathlib import Path

from negotiate import MAX_TURNS, run_episode
from llm import LLM
from parse import ProtocolLayer
from typeset import CSV_FIELDS, Condition, EpisodeResult, Scenario

ROOT = Path(__file__).resolve().parent

CONDITIONS: tuple[Condition, ...] = ("free", "tagged", "structured")


def load_scenarios(path: Path) -> list[Scenario]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Scenario(**row) for row in data]


def load_done(results_path: Path) -> set[tuple[str, int]]:
    done: set[tuple[str, int]] = set()
    if not results_path.exists():
        return done
    with results_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                if str(row.get("note", "")).startswith("dead:"):
                    continue
                done.add((row["run"], int(row["scenario"])))
            except (KeyError, ValueError):
                continue
    return done


def write_log_header(path: Path, llm: LLM) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = "unset" if llm.temperature is None else llm.temperature
    path.write_text(
        f"provider={llm.provider} model={llm.model} temperature={temp} turn_limit={MAX_TURNS}\n\n",
        encoding="utf-8",
    )


def _format_log(result: EpisodeResult) -> str:
    lines = [
        f"=== scenario {result.scenario} ({result.item}) ===",
        *result.transcript,
        (
            f"[result] outcome={result.outcome} price={result.price} "
            f"correct={result.correct} violation={result.violation} turns={result.turns} "
            f"format_errors={result.format_errors} reader_calls={result.reader_calls}"
        ),
    ]
    if result.note:
        lines.append(f"[note] {result.note}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _atomic_csv(results_path: Path, rows: list[dict]) -> None:
    tmp = results_path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})
    tmp.replace(results_path)


def upsert_result(results_path: Path, result: EpisodeResult) -> None:
    rows: list[dict] = []
    if results_path.exists():
        with results_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if (row.get("run"), str(row.get("scenario"))) != (result.run, str(result.scenario)):
                    rows.append(row)
    rows.append(result.csv_row())
    _atomic_csv(results_path, rows)


def flush_log(path: Path, llm: LLM, result: EpisodeResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write_log_header(path, llm)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_format_log(result))
        fh.flush()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FIPA-ACL week-04 buyer/seller lab")
    p.add_argument("--provider", default="openai", choices=["anthropic", "openai", "claude_cli", "mock"])
    p.add_argument("--model", default="gpt-6-luna")
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument("--replicates", type=int, default=3)
    p.add_argument("--conditions", nargs="+", default=list(CONDITIONS))
    p.add_argument("--scenarios", type=Path, default=ROOT / "scenarios.json")
    p.add_argument("--results", type=Path, default=ROOT / "results.csv")
    p.add_argument("--logs", type=Path, default=ROOT / "logs")
    p.add_argument("--start-rep", type=int, default=1)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    scenarios = load_scenarios(args.scenarios)
    llm = LLM(args.provider, args.model, args.temperature)
    protocol = ProtocolLayer(llm.reader)
    done = load_done(args.results)
    args.logs.mkdir(parents=True, exist_ok=True)
    pending: list[EpisodeResult] = []
    log_chunks: dict[Path, list[str]] = {}

    for condition in args.conditions:
        if condition not in CONDITIONS:
            raise SystemExit(f"bad condition: {condition}")
        for rep in range(args.start_rep, args.start_rep + args.replicates):
            run_id = f"{condition}-{rep:02d}"
            log_path = args.logs / f"{run_id}.txt"
            for scenario in scenarios:
                key = (run_id, scenario.id)
                if key in done:
                    continue
                result = run_episode(llm, protocol, scenario, condition, run_id)  # type: ignore[arg-type]
                flush_log(log_path, llm, result)
                upsert_result(args.results, result)
                done.add(key)
                print(
                    f"{run_id} s{scenario.id} {result.outcome} "
                    f"correct={result.correct} viol={result.violation} "
                    f"turns={result.turns} ferr={result.format_errors} "
                    f"rcall={result.reader_calls} {result.note}",
                    flush=True,
                )


if __name__ == "__main__":
    main()



