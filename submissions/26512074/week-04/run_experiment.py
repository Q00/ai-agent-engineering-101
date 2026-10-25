import argparse
import csv
import json
import traceback
from datetime import datetime
from pathlib import Path

from negotiate import (
    BASE_URL,
    MAX_TURNS,
    MODEL,
    TEMPERATURE,
    append_result,
    ensure_results,
    existing_pairs,
    negotiate_episode,
)

SUBMIT_DIR = Path(__file__).resolve().parent
SCENARIOS_PATH = SUBMIT_DIR / "scenarios.json"
RESULTS_PATH = SUBMIT_DIR / "results.csv"
LOG_DIR = SUBMIT_DIR / "logs"

CONDITIONS = ("free", "tagged", "structured")
REPEATS = 3


class RunLogger:
    def __init__(self, path: Path):
        self.path = path
        self.fp = path.open("a", encoding="utf-8")

    def log(self, message: str) -> None:
        print(message)
        self.fp.write(message + "\n")
        self.fp.flush()

    def close(self) -> None:
        self.fp.close()


def load_scenarios() -> list[dict]:
    with SCENARIOS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def write_run_log_header(logger: RunLogger, condition: str, run: int) -> None:
    logger.log(
        f"provider=openrouter base_url={BASE_URL} model={MODEL} "
        f"temperature={TEMPERATURE} max_turns={MAX_TURNS}"
    )
    logger.log(f"condition={condition} repeat={run}")
    logger.log("")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--condition",
        choices=CONDITIONS,
        help="run only one condition; default runs all conditions",
    )
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), help="run one repeat")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ensure_results(RESULTS_PATH)
    pairs = existing_pairs(RESULTS_PATH)
    scenarios = load_scenarios()

    conditions = (args.condition,) if args.condition else CONDITIONS
    repeats = (args.repeat,) if args.repeat else range(1, REPEATS + 1)

    for condition in conditions:
        for run in repeats:
            log_path = LOG_DIR / f"{condition}-run{run}-final.txt"
            logger = RunLogger(log_path)
            write_run_log_header(logger, condition, run)
            logger.log(f"scenarios={len(scenarios)}")

            for scenario in scenarios:
                key = (str(run), condition, str(scenario["id"]))
                if key in pairs:
                    logger.log(
                        f"[skip] run={run} condition={condition} "
                        f"scenario={scenario['id']} already in results.csv"
                    )
                    continue

                try:
                    episode = negotiate_episode(
                        scenario, condition, run, logger.log
                    )
                    append_result(RESULTS_PATH, episode)
                    pairs.add(key)
                    logger.log(
                        f"[saved] scenario={scenario['id']} outcome={episode.outcome} "
                        f"price={episode.price} correct={episode.correct} "
                        f"violation={episode.violation} turns={episode.turns} "
                        f"format_errors={episode.format_errors} "
                        f"reader_calls={episode.meter.reader_calls}"
                    )
                except Exception as exc:
                    note = f"{type(exc).__name__}: {exc}"
                    logger.log(f"[crash] scenario={scenario['id']} {note}")
                    logger.log(traceback.format_exc())

                    row = {
                        "run": str(run),
                        "condition": condition,
                        "scenario": str(scenario["id"]),
                        "deal_possible": str(
                            int(scenario["reserve"] <= scenario["budget"])
                        ),
                        "outcome": "",
                        "price": "",
                        "correct": "",
                        "violation": "",
                        "turns": "",
                        "format_errors": "",
                        "reader_calls": "",
                        "note": note,
                    }
                    with RESULTS_PATH.open("a", encoding="utf-8", newline="") as f:
                        writer = csv.DictWriter(
                            f,
                            fieldnames=[
                                "run", "condition", "scenario", "deal_possible",
                                "outcome", "price", "correct", "violation",
                                "turns", "format_errors", "reader_calls", "note",
                            ],
                        )
                        writer.writerow(row)
                    pairs.add(key)

            logger.log("")
            logger.log(f"[done] {datetime.now().isoformat(timespec='seconds')}")
            logger.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
