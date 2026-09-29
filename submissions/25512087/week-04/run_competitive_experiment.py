"""Run the two-buyer, one-seller Week 4 negotiation experiment."""

import argparse
import csv
import json
from pathlib import Path

from competitive_negotiation import run_competitive_episode
from run_experiment import CONDITIONS, JsonlLog, OpenAIModel, setting


HEADER = [
    "run",
    "condition",
    "scenario",
    "deal_possible",
    "outcome",
    "winner",
    "price",
    "correct",
    "violation",
    "rounds",
    "agent_calls",
    "format_errors",
    "reader_calls",
    "total_model_calls",
    "affinity_buyer_1",
    "affinity_buyer_2",
    "penalty_buyer_1",
    "penalty_buyer_2",
    "seller_reserve_penalty",
    "seller_opportunity_penalty",
    "highest_offers",
    "note",
]


def completed_keys(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            (row["condition"], row["scenario"], row["run"])
            for row in csv.DictReader(handle)
        }


def result_row(repeat: int, condition: str, scenario: dict, result: dict) -> list:
    return [
        repeat,
        condition,
        scenario["id"],
        int(any(budget >= scenario["reserve"] for budget in scenario["budgets"].values())),
        result["outcome"],
        result["winner"] or "",
        result["price"] if result["price"] is not None else "",
        result["correct"],
        result["violation"],
        result["rounds"],
        result["agent_calls"],
        result["format_errors"],
        result["reader_calls"],
        result["total_model_calls"],
        result["affinity_buyer_1"],
        result["affinity_buyer_2"],
        result["penalty_buyer_1"],
        result["penalty_buyer_2"],
        result["seller_reserve_penalty"],
        result["seller_opportunity_penalty"],
        json.dumps(result["highest_offers"], separators=(",", ":")),
        "",
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--results", default="competitive_results.csv")
    parser.add_argument("--log-dir", default="competitive_logs")
    args = parser.parse_args()
    if args.runs < 1 or args.rounds < 1:
        parser.error("--runs and --rounds must be positive")
    if not setting("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required; no key is stored in this submission")

    root = Path(__file__).resolve().parent
    scenarios = json.loads(
        (root / "competitive_scenarios.json").read_text(encoding="utf-8")
    )
    result_path = Path(args.results)
    if not result_path.is_absolute():
        result_path = root / result_path
    log_root = Path(args.log_dir)
    if not log_root.is_absolute():
        log_root = root / log_root
    result_path.parent.mkdir(parents=True, exist_ok=True)
    existing = completed_keys(result_path)
    if not result_path.exists():
        result_path.write_text(",".join(HEADER) + "\n", encoding="utf-8")

    model = OpenAIModel()
    with result_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for repeat in range(1, args.runs + 1):
            for condition in CONDITIONS:
                log_path = log_root / f"{condition}-{repeat:02d}.jsonl"
                with JsonlLog(log_path) as log:
                    log({
                        "event": "meta",
                        "condition": condition,
                        "repeat": repeat,
                        "provider": model.base_url,
                        "model": model.model,
                        "temperature": 0,
                        "round_limit": args.rounds,
                        "protocol": "two_buyers_one_seller_feedback_affinity_penalty",
                    })
                    for scenario in scenarios:
                        key = (condition, scenario["id"], str(repeat))
                        if key in existing:
                            continue
                        try:
                            result = run_competitive_episode(
                                scenario,
                                condition,
                                model,
                                log,
                                max_rounds=args.rounds,
                            )
                            row = result_row(repeat, condition, scenario, result)
                        except Exception as error:
                            log({
                                "event": "crash",
                                "scenario": scenario["id"],
                                "error": f"{type(error).__name__}: {error}",
                            })
                            row = [
                                repeat,
                                condition,
                                scenario["id"],
                                int(any(
                                    budget >= scenario["reserve"]
                                    for budget in scenario["budgets"].values()
                                )),
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                f"crash: {type(error).__name__}: {error}",
                            ]
                        writer.writerow(row)
                        handle.flush()


if __name__ == "__main__":
    main()
