#!/usr/bin/env python3
"""Week-04 negotiation experiment runner.

Usage:
  python run_experiment.py --condition free --run 1
  python run_experiment.py --condition tagged --run 2
  python run_experiment.py --condition structured --run 3

One run = one condition, all scenarios, one repeat.
Appends to results.csv. Skips (run, scenario) pairs already recorded.
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

from model import call_model, Meter, MODEL, PROVIDER, TEMPERATURE
from protocol import read_message

MAX_TURNS = 8
HERE = Path(__file__).parent
RESULTS_CSV = HERE / "results.csv"
SCENARIOS_FILE = HERE / "scenarios.json"

CSV_HEADER = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "turns", "format_errors", "reader_calls", "note",
]

# ── system prompts ──

ROLE_BUYER = (
    "You are the buyer of {item}, negotiating the price with the seller. "
    "Your private limit: you can pay at most {limit}. Never reveal your limit. "
    "Never agree to a price above {limit}. Try to get the lowest price possible. "
    "Four acts are available: propose (offer a price), accept-proposal (agree to the "
    "other side's last price, which ends the negotiation with a deal), reject-proposal "
    "(decline the last price and keep negotiating), refuse (leave the negotiation for good, no deal). "
    "You open the negotiation."
)

ROLE_SELLER = (
    "You are the seller of {item}, negotiating the price with the buyer. "
    "Your private limit: you can accept at least {limit}. Never reveal your limit. "
    "Never agree to a price below {limit}. Try to get the highest price possible. "
    "Four acts are available: propose (offer a price), accept-proposal (agree to the "
    "other side's last price, which ends the negotiation with a deal), reject-proposal "
    "(decline the last price and keep negotiating), refuse (leave the negotiation for good, no deal)."
)

FORMAT_PARA = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": (
        " Start your message with exactly one performative tag in parentheses — "
        "one of (propose), (accept-proposal), (reject-proposal), (refuse) — "
        "then write one plain English sentence. Example: (propose) I offer $50 for the item."
    ),
    "structured": (
        ' Reply with exactly one JSON object and nothing else: '
        '{"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse", '
        '"content": {"price": <integer or null>}}. '
        'Set price to an integer for propose, null for other acts.'
    ),
}


def system_prompt(role, item, limit, condition):
    if role == "buyer":
        base = ROLE_BUYER.format(item=item, limit=limit)
    else:
        base = ROLE_SELLER.format(item=item, limit=limit)
    return base + FORMAT_PARA[condition]


# ── episode ──

def run_episode(scenario, condition, log):
    item = scenario["item"]
    reserve = scenario["reserve"]
    budget = scenario["budget"]
    deal_possible = 1 if reserve <= budget else 0

    buyer_sys = system_prompt("buyer", item, budget, condition)
    seller_sys = system_prompt("seller", item, reserve, condition)

    buyer_history = []
    seller_history = []
    transcript = []

    meter_agent = Meter()
    meter_reader = Meter()

    last_price = {"buyer": None, "seller": None}
    outcome = "open"
    price = ""
    format_errors = 0
    turns = 0

    role = "buyer"
    systems = {"buyer": buyer_sys, "seller": seller_sys}
    histories = {"buyer": buyer_history, "seller": seller_history}

    for turn_i in range(MAX_TURNS):
        other = "seller" if role == "buyer" else "buyer"
        text = call_model(systems[role], histories[role], meter_agent)
        turns += 1

        histories[role].append({"role": "assistant", "content": text})
        histories[other].append({"role": "user", "content": text})
        transcript.append((role, text))

        log(f"[{role}] {text}")

        perf, p, ok, rcalls = read_message(condition, text, transcript, meter_reader)
        format_errors += 0 if ok else 1
        log(f"  [reader] perf={perf} price={p} ok={ok} reader_calls={rcalls}")

        if ok:
            if perf == "propose" and p is not None:
                last_price[role] = p
            elif perf == "accept-proposal":
                outcome = "deal"
                price = last_price[other] if last_price[other] is not None else ""
                break
            elif perf == "refuse":
                outcome = "no_deal"
                break

        role = other

    correct = 0
    violation = 0
    if outcome == "deal" and price != "":
        violation = 1 if (price < reserve or price > budget) else 0
        correct = 1 if (deal_possible and not violation) else 0
    elif outcome == "no_deal":
        correct = 1 if not deal_possible else 0

    log(f"[result] outcome={outcome} price={price} correct={correct} "
        f"violation={violation} turns={turns} format_errors={format_errors} "
        f"reader_calls={meter_reader.calls}")

    return {
        "outcome": outcome,
        "price": price,
        "correct": correct,
        "violation": violation,
        "turns": turns,
        "format_errors": format_errors,
        "reader_calls": meter_reader.calls,
        "deal_possible": deal_possible,
        "agent_calls": meter_agent.calls,
    }


# ── results.csv helpers ──

def load_done_pairs():
    done = set()
    if RESULTS_CSV.exists():
        with open(RESULTS_CSV, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                done.add((row["run"], row["scenario"]))
    return done


def ensure_header():
    if not RESULTS_CSV.exists():
        with open(RESULTS_CSV, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(CSV_HEADER)


def append_row(row_dict):
    with open(RESULTS_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([row_dict.get(k, "") for k in CSV_HEADER])


# ── main ──

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", required=True, choices=["free", "tagged", "structured"])
    parser.add_argument("--run", required=True, type=int)
    args = parser.parse_args()

    scenarios = json.loads(SCENARIOS_FILE.read_text(encoding="utf-8"))
    done = load_done_pairs()
    ensure_header()

    run_label = f"{args.condition}-{args.run:02d}"
    log_path = HERE / "logs" / f"{run_label}.txt"
    log_path.parent.mkdir(exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")

    header_line = f"provider={PROVIDER} model={MODEL} temperature={TEMPERATURE} max_turns={MAX_TURNS}"
    print(header_line)
    log_file.write(header_line + "\n")

    def log(msg):
        print(msg)
        log_file.write(msg + "\n")

    for sc in scenarios:
        sid = str(sc["id"])
        if (run_label, sid) in done:
            log(f"\n--- scenario {sid} ({sc['item']}) SKIPPED (already in results.csv) ---")
            continue

        log(f"\n--- scenario {sid} ({sc['item']}), reserve={sc['reserve']}, budget={sc['budget']} ---")

        note = ""
        try:
            result = run_episode(sc, args.condition, log)
        except Exception as e:
            log(f"[error] {e}")
            note = str(e)[:200]
            result = {
                "outcome": "", "price": "", "correct": "", "violation": "",
                "turns": "", "format_errors": "", "reader_calls": "",
                "deal_possible": 1 if sc["reserve"] <= sc["budget"] else 0,
            }

        append_row({
            "run": run_label,
            "condition": args.condition,
            "scenario": sid,
            "deal_possible": result["deal_possible"],
            "outcome": result["outcome"],
            "price": result["price"],
            "correct": result["correct"],
            "violation": result["violation"],
            "turns": result["turns"],
            "format_errors": result["format_errors"],
            "reader_calls": result["reader_calls"],
            "note": note,
        })

    log_file.close()
    print(f"\nlog saved to {log_path}")


if __name__ == "__main__":
    main()
