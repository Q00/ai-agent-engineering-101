import csv
import json
from openai import RateLimitError
from pathlib import Path

from negotiation import run_episode
from model import MODEL


RESULTS = Path("results.csv")
LOG_DIR = Path("logs")

CONDITIONS = ["free", "tagged", "structured"]

HEADER = [
    "run",
    "condition",
    "scenario",
    "deal_possible",
    "outcome",
    "price",
    "correct",
    "violation",
    "turns",
    "format_errors",
    "reader_calls",
    "note",
]


def load_scenarios():
    with open("scenarios.json", encoding="utf-8") as f:
        return json.load(f)


def ensure_results_file():
    if not RESULTS.exists():
        with RESULTS.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(HEADER)


def completed_keys():
    if not RESULTS.exists():
        return set()

    done = set()

    with RESULTS.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add(
                (
                    int(row["run"]),
                    row["condition"],
                    row["scenario"],
                )
            )

    return done


def append_row(row):
    with RESULTS.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def main():
    scenarios = load_scenarios()
    ensure_results_file()
    done = completed_keys()

    run_number = 1

    for condition in CONDITIONS:
        for repeat in range(1, 4):
            log_path = LOG_DIR / f"{condition}-{repeat:02d}.txt"
            LOG_DIR.mkdir(exist_ok=True)

            existing_text = ""
            if log_path.exists():
                existing_text = log_path.read_text(encoding="utf-8")

            lines = []

            if not existing_text:
                lines.append(
                    f"provider=OpenRouter model={MODEL} "
                    f"temperature=0 condition={condition} repeat={repeat}"
                )

            def log(message):
                print(message)
                lines.append(str(message))

            print(
                f"\n=== run {run_number}: "
                f"{condition} repeat {repeat} ==="
            )

            for scenario in scenarios:
                key = (
                    run_number,
                    condition,
                    scenario["id"],
                )

                if key in done:
                    print(
                        f"[skip] run={run_number} "
                        f"condition={condition} "
                        f"scenario={scenario['id']}"
                    )
                    continue

                deal_possible = int(
                    scenario["reserve"] <= scenario["budget"]
                )

                log(
                    f"[scenario] id={scenario['id']} "
                    f"item={scenario['item']} "
                    f"reserve={scenario['reserve']} "
                    f"budget={scenario['budget']} "
                    f"deal_possible={deal_possible}"
                )

                try:
                    result = run_episode(
                        scenario=scenario,
                        condition=condition,
                        log=log,
                    )

                    append_row([
                        run_number,
                        condition,
                        scenario["id"],
                        deal_possible,
                        result.outcome,
                        "" if result.price is None else result.price,
                        result.correct,
                        result.violation,
                        result.turns,
                        result.format_errors,
                        result.reader_calls,
                        "",
                    ])

                    done.add(key)

                except RateLimitError as exc:
                    log(f"[rate-limit] stopping run: {exc}")
                    raise
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    log(f"[crash] {error}")

                    append_row([
                        run_number,
                        condition,
                        scenario["id"],
                        deal_possible,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        error,
                    ])

                    done.add(key)

                finally:
                    previous = ""
                    if log_path.exists():
                        previous = log_path.read_text(
                            encoding="utf-8"
                        )

                    addition = "\n".join(lines)

                    if addition:
                        if previous and not previous.endswith("\n"):
                            previous += "\n"

                        log_path.write_text(
                            previous + addition + "\n",
                            encoding="utf-8",
                        )

                    lines.clear()

            run_number += 1


if __name__ == "__main__":
    main()
