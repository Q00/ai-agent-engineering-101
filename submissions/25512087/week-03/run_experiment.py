"""Run all Week 03 Contract Net conditions and preserve every attempt."""
import argparse
import csv
import json
import os
from pathlib import Path

from contract_net import build_team, run_round


CONDITIONS = ("baseline", "homogeneous", "overconfident")
HEADER = [
    "run", "condition", "tasks", "correct", "messages", "unassigned",
    "misawards", "note",
]
TEMPERATURE = 0


def read_setting(name: str, default: str = "") -> str:
    """Read a text setting without accidental shell whitespace or newlines."""
    return os.environ.get(name, default).strip()


class JsonlLogger:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("w", encoding="utf-8")
        return self

    def __call__(self, event: dict):
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        print(line)
        self.handle.write(line + "\n")
        self.handle.flush()

    def __exit__(self, exc_type, exc_value, traceback):
        self.handle.close()


class OpenRouterModel:
    def __init__(self):
        from openai import OpenAI

        self.model = read_setting(
            "AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free"
        )
        self.client = OpenAI(
            api_key=read_setting("OPENAI_API_KEY"),
            base_url=read_setting("OPENAI_BASE_URL") or None,
        )

    def __call__(self, system_prompt: str, announcement: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=TEMPERATURE,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": announcement},
            ],
        )
        return response.choices[0].message.content or ""


def load_tasks(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def completed_runs(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8", newline="") as handle:
        return max(0, sum(1 for row in csv.reader(handle) if row) - 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if not read_setting("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required; no key is stored by this program")

    root = Path(__file__).resolve().parent
    tasks = load_tasks(root / "tasks.json")
    results_path = root / "results.csv"
    new_file = not results_path.exists()
    run_number = completed_runs(results_path)
    model = OpenRouterModel()
    provider = read_setting("OPENAI_BASE_URL", "OpenAI-compatible endpoint")

    with results_path.open("a", encoding="utf-8", newline="") as results:
        writer = csv.writer(results)
        if new_file:
            writer.writerow(HEADER)

        for condition in CONDITIONS:
            for _ in range(args.runs):
                run_number += 1
                log_path = root / "logs" / f"{condition}-{run_number:02d}.jsonl"
                with JsonlLogger(log_path) as log:
                    log({
                        "event": "meta",
                        "provider": provider,
                        "model": model.model,
                        "temperature": TEMPERATURE,
                        "condition": condition,
                    })
                    try:
                        outcome = run_round(tasks, build_team(condition), model, log)
                        note = f"parse_fails={outcome.parse_fails}"
                        row = [
                            run_number, condition, outcome.tasks, outcome.correct,
                            outcome.messages, outcome.unassigned, outcome.misawards,
                            note,
                        ]
                        log({"event": "summary", **outcome.__dict__})
                    except Exception as error:
                        note = f"crash: {type(error).__name__}: {error}"
                        row = [run_number, condition, "", "", "", "", "", note]
                        log({"event": "crash", "error": note})
                    writer.writerow(row)
                    results.flush()


if __name__ == "__main__":
    main()
