"""Run free, tagged, and structured buyer/seller negotiations."""
import argparse
import csv
import json
import os
import time
from pathlib import Path

from negotiation import run_episode


CONDITIONS = ("free", "tagged", "structured")
HEADER = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "turns", "format_errors", "reader_calls", "note",
]


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class OpenAIModel:
    def __init__(self):
        from openai import OpenAI
        self.model = setting("AGENT_MODEL", "gpt-5.6-luna")
        self.base_url = setting("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.client = OpenAI(api_key=setting("OPENAI_API_KEY"), base_url=self.base_url)

    def __call__(self, system: str, messages: list[dict[str, str]]) -> str:
        kwargs = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": 0,
            "max_completion_tokens": 180,
        }
        if self.model.startswith("gpt-5.6-"):
            kwargs["reasoning_effort"] = "none"
        if "openrouter.ai" in self.base_url:
            kwargs["extra_body"] = {"reasoning": {"enabled": False}}
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except Exception as error:
                if getattr(error, "status_code", None) != 429 or attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")


class JsonlLog:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a", encoding="utf-8")
        return self

    def __call__(self, event):
        self.handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        self.handle.flush()
        print(json.dumps(event, ensure_ascii=False, sort_keys=True))

    def __exit__(self, *_):
        self.handle.close()


def load_results(path: Path, scenario_count: int, requested_runs: int):
    if not path.exists():
        return set(), 0
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    run_numbers = [int(row["run"]) for row in rows if row.get("run", "").isdigit()]
    # Earlier local runs used a global row counter in the `run` column. Keep
    # those results, but map their deterministic loop order back to repeat
    # numbers so a resumed run does not duplicate them.
    legacy_global_runs = bool(run_numbers) and max(run_numbers) > requested_runs
    if legacy_global_runs:
        width = len(CONDITIONS) * scenario_count
        pairs = set()
        for row in rows:
            run_number = int(row["run"])
            repeat = (run_number - 1) // width + 1
            pairs.add((row["condition"], row["scenario"], str(repeat)))
    else:
        pairs = {(row["condition"], row["scenario"], row["run"]) for row in rows}
    return pairs, len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--turn-limit", type=int, default=8)
    parser.add_argument(
        "--results",
        default="results.csv",
        help="CSV output path, relative to this directory by default",
    )
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="JSONL log directory, relative to this directory by default",
    )
    args = parser.parse_args()
    if args.runs < 1 or args.turn_limit < 1:
        parser.error("--runs and --turn-limit must be positive")
    if not setting("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required; no key is stored in this submission")

    root = Path(__file__).resolve().parent
    scenarios = json.loads((root / "scenarios.json").read_text(encoding="utf-8"))
    result_path = Path(args.results)
    if not result_path.is_absolute():
        result_path = root / result_path
    log_root = Path(args.log_dir)
    if not log_root.is_absolute():
        log_root = root / log_root
    result_path.parent.mkdir(parents=True, exist_ok=True)
    existing, _ = load_results(result_path, len(scenarios), args.runs)
    if not result_path.exists():
        result_path.write_text(",".join(HEADER) + "\n", encoding="utf-8")
    model = OpenAIModel()

    with result_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for repeat in range(1, args.runs + 1):
            for condition in CONDITIONS:
                log_path = log_root / f"{condition}-{repeat:02d}.jsonl"
                with JsonlLog(log_path) as log:
                    log({"event": "meta", "condition": condition, "repeat": repeat,
                         "provider": model.base_url, "model": model.model,
                         "temperature": 0, "turn_limit": args.turn_limit})
                    for scenario in scenarios:
                        key = (condition, scenario["id"], str(repeat))
                        if key in existing:
                            continue
                        deal_possible = int(scenario["reserve"] <= scenario["budget"])
                        try:
                            result = run_episode(scenario, condition, model, log, args.turn_limit)
                            row = [repeat, condition, scenario["id"], deal_possible,
                                   result["outcome"], result["price"] if result["price"] is not None else "",
                                   result["correct"], result["violation"], result["turns"],
                                   result["format_errors"], result["reader_calls"], ""]
                        except Exception as error:
                            log({"event": "crash", "scenario": scenario["id"],
                                 "error": f"{type(error).__name__}: {error}"})
                            row = [repeat, condition, scenario["id"], deal_possible,
                                   "", "", "", "", "", "", "", f"crash: {type(error).__name__}: {error}"]
                        writer.writerow(row)
                        handle.flush()


if __name__ == "__main__":
    main()
