"""Offline UCB readiness check for randomized opening-price arms.

This module does not assign live prices. Legacy observations are excluded because
they were not randomized across Low, Mid and High.
"""

from __future__ import annotations

import argparse
import json
import math

from pricing import PricingRecorder
from pricing_simulator import RATIOS, candidate_price


def evaluate_ucb(rows: list[dict], item: str, *, minimum_exposed: int = 30,
                 maximum_unknown_fraction: float = 0.05,
                 exploration: float = 0.1) -> dict:
    if item not in {"lamp", "chair", "bookshelf"}:
        raise ValueError("unknown item")
    if minimum_exposed < 1 or not 0 <= maximum_unknown_fraction <= 1 or exploration < 0:
        raise ValueError("invalid UCB configuration")
    arms = {arm: {"exposed": 0, "counted": 0, "zero_reward": 0,
                  "unknown": 0, "reward_sum": 0.0, "ask_mismatches": 0}
            for arm in RATIOS}
    for row in rows:
        if row["item"] != item or row["assigned_arm"] not in arms:
            continue
        arm = arms[row["assigned_arm"]]
        ask = row["opening_ask_actual"]
        if ask is None:
            continue
        arm["exposed"] += 1
        if ask != candidate_price(row["reserve"], row["assigned_arm"]):
            arm["ask_mismatches"] += 1
        if row["ledger_status"] == "COUNTED":
            price = row["final_price"]
            if type(price) is not int or price < row["reserve"]:
                arm["ask_mismatches"] += 1
                continue
            arm["counted"] += 1
            arm["reward_sum"] += min(1.0, (price - row["reserve"]) / row["reserve"])
        elif row["ledger_status"] == "NOT_COUNTED":
            arm["zero_reward"] += 1
        elif row["receipt_issued"]:
            arm["unknown"] += 1
        elif row["negotiation_status"] in {"REJECTED", "CANCELLED", "TIMED_OUT"}:
            arm["zero_reward"] += 1
        else:
            arm["unknown"] += 1
    reasons = []
    for name, arm in arms.items():
        if arm["exposed"] < minimum_exposed:
            reasons.append(f"{name}: {arm['exposed']} exposed, need {minimum_exposed}")
        if arm["exposed"] and arm["unknown"] / arm["exposed"] > maximum_unknown_fraction:
            reasons.append(f"{name}: too many pending or unknown outcomes")
        if arm["ask_mismatches"]:
            reasons.append(f"{name}: assigned ask differs from actual ask or invalid receipt")
    result = {"item": item, "mode": "offline only", "ready": not reasons,
              "reasons": reasons, "arms": arms, "recommended_arm": None,
              "scores": None,
              "method": "Counted normalized margin is capped at 1; confirmed no-deals and unknowns contribute zero to a conservative per-exposure mean. UCB is withheld while unknowns exceed the gate."}
    if reasons:
        return result
    total = sum(arm["exposed"] for arm in arms.values())
    scores = {
        name: round(arm["reward_sum"] / arm["exposed"]
                    + exploration * math.sqrt(math.log(total) / arm["exposed"]), 6)
        for name, arm in arms.items()
    }
    result["scores"] = scores
    result["recommended_arm"] = max(RATIOS, key=lambda name: (scores[name], -list(RATIOS).index(name)))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline UCB readiness; never changes the Seller")
    parser.add_argument("--db", default="state/pricing_state.sqlite")
    parser.add_argument("--item", required=True, choices=["lamp", "chair", "bookshelf"])
    parser.add_argument("--minimum-exposed", type=int, default=30)
    parser.add_argument("--maximum-unknown-fraction", type=float, default=0.05)
    parser.add_argument("--exploration", type=float, default=0.1)
    args = parser.parse_args()
    rows = PricingRecorder(args.db).all_trades()
    print(json.dumps(evaluate_ucb(
        rows, args.item, minimum_exposed=args.minimum_exposed,
        maximum_unknown_fraction=args.maximum_unknown_fraction,
        exploration=args.exploration), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
