from model_client import call_model
from prompts import build_buyer_prompt, build_seller_prompt
from protocol import parse_message
from reader import reader_call


def create_histories():
    return [], []


def save_message(speaker, message, buyer_history, seller_history):
    if speaker == "buyer":
        buyer_history.append({
            "role": "assistant",
            "content": message,
        })
        seller_history.append({
            "role": "user",
            "content": message,
        })

    elif speaker == "seller":
        seller_history.append({
            "role": "assistant",
            "content": message,
        })
        buyer_history.append({
            "role": "user",
            "content": message,
        })

    else:
        raise ValueError(f"Unknown speaker: {speaker}")


def judge_result(outcome, price, scenario):
    deal_possible = scenario["reserve"] <= scenario["budget"]

    violation = int(
        outcome == "deal"
        and (
            price < scenario["reserve"]
            or price > scenario["budget"]
        )
    )

    correct = int(
        (outcome == "deal" and deal_possible and violation == 0)
        or (outcome == "no_deal" and not deal_possible)
    )

    return int(deal_possible), violation, correct


def run_episode(scenario, condition):
    buyer_prompt = build_buyer_prompt(
        scenario["item"],
        scenario["budget"],
        condition,
    )

    seller_prompt = build_seller_prompt(
        scenario["item"],
        scenario["reserve"],
        condition,
    )

    buyer_history, seller_history = create_histories()
    transcript = []

    last_proposal = {
        "buyer": None,
        "seller": None,
    }

    outcome = "open"
    deal_price = None
    format_errors = 0
    reader_calls = 0
    log_lines = []

    for turn in range(8):
        speaker = "buyer" if turn % 2 == 0 else "seller"

        if speaker == "buyer":
            message = call_model(buyer_prompt, buyer_history)
        else:
            message = call_model(seller_prompt, seller_history)

        parsed = parse_message(
            condition,
            message,
            transcript,
            reader_call,
        )

        format_errors += parsed["format_error"]
        reader_calls += parsed["reader_calls"]

        log_lines.append(f"[{speaker}] {message}")
        log_lines.append(f"  [parsed] {parsed}")

        save_message(
            speaker,
            message,
            buyer_history,
            seller_history,
        )

        transcript.append({
            "role": speaker,
            "content": message,
        })

        performative = parsed["performative"]
        price = parsed["price"]

        if performative == "propose":
            last_proposal[speaker] = price

        elif performative == "accept-proposal":
            opponent = (
                "seller"
                if speaker == "buyer"
                else "buyer"
            )

            accepted_price = last_proposal[opponent]

            if accepted_price is not None:
                deal_price = accepted_price
                outcome = "deal"
                break

        elif performative == "refuse":
            outcome = "no_deal"
            break

    deal_possible, violation, correct = judge_result(
        outcome,
        deal_price,
        scenario,
    )

    result = {
        "scenario": scenario["id"],
        "deal_possible": deal_possible,
        "outcome": outcome,
        "price": deal_price,
        "correct": correct,
        "violation": violation,
        "turns": len(transcript),
        "format_errors": format_errors,
        "reader_calls": reader_calls,
        "note": "",
    }

    log_lines.append(f"[result] {result}")

    return result, log_lines

import csv
import json
from pathlib import Path


RESULT_FIELDS = [
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


def load_completed_results(result_path):
    completed = set()

    if not result_path.exists():
        return completed

    with result_path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        for row in csv.DictReader(file):
            completed.add((
                int(row["run"]),
                row["condition"],
                int(row["scenario"]),
            ))

    return completed


def append_result(result_path, row):
    file_exists = result_path.exists()

    with result_path.open(
        "a",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=RESULT_FIELDS,
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)


def main():
    base_dir = Path(__file__).parent
    scenario_path = base_dir / "scenarios.json"
    result_path = base_dir / "results.csv"
    log_dir = base_dir / "logs"

    log_dir.mkdir(exist_ok=True)

    with scenario_path.open("r", encoding="utf-8") as file:
        scenarios = json.load(file)

    completed = load_completed_results(result_path)

    conditions = ["free", "tagged", "structured"]

    for condition in conditions:
        for run_number in range(1, 4):
            log_lines = [
                f"condition={condition}",
                f"run={run_number}",
            ]

            for scenario in scenarios:
                key = (
                    run_number,
                    condition,
                    scenario["id"],
                )

                if key in completed:
                    log_lines.append(
                        f"[skip] scenario={scenario['id']}"
                    )
                    continue

                try:
                    result, episode_log = run_episode(
                        scenario,
                        condition,
                    )

                    row = {
                        "run": run_number,
                        "condition": condition,
                        **result,
                    }

                    log_lines.extend(episode_log)

                except Exception as error:
                    row = {
                        "run": run_number,
                        "condition": condition,
                        "scenario": scenario["id"],
                        "deal_possible": "",
                        "outcome": "",
                        "price": "",
                        "correct": "",
                        "violation": "",
                        "turns": "",
                        "format_errors": "",
                        "reader_calls": "",
                        "note": repr(error),
                    }

                    log_lines.append(
                        f"[error] scenario={scenario['id']} "
                        f"{repr(error)}"
                    )

                append_result(result_path, row)

            log_path = (
                log_dir
                / f"{condition}-{run_number:02d}.txt"
            )

            log_path.write_text(
                "\n".join(log_lines),
                encoding="utf-8",
            )

            print(f"saved: {log_path}")


if __name__ == "__main__":
    main()