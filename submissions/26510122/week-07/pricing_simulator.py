"""Offline-only balanced price-arm assignment; never imported by the Seller."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from decimal import Decimal, ROUND_CEILING
from pathlib import Path


RATIOS = {"Low": Decimal("1.06"), "Mid": Decimal("1.09"), "High": Decimal("1.12")}
MAX_RATIO = RATIOS["High"]


def candidate_price(reserve: int, arm: str) -> int:
    if type(reserve) is not int or reserve <= 0 or arm not in RATIOS:
        raise ValueError("positive integer reserve and known arm required")
    ask = int((Decimal(reserve) * RATIOS[arm]).to_integral_value(rounding=ROUND_CEILING))
    ceiling = int((Decimal(reserve) * MAX_RATIO).to_integral_value(rounding=ROUND_CEILING))
    if not reserve <= ask <= ceiling:
        raise ValueError("candidate ask exceeds reserve or experiment ceiling")
    return ask


class PricingSimulator:
    def __init__(self, seed: int):
        self.seed = seed
        self.assignments: dict[str, dict] = {}
        self.by_item: dict[str, list[str]] = {}
        self.outcomes: dict[str, dict] = {}

    def assign(self, task_id: str, item: str, reserve: int) -> dict:
        if not task_id or not item:
            raise ValueError("task and item required")
        previous = self.assignments.get(task_id)
        if previous is not None:
            if (previous["item"], previous["reserve"]) != (item, reserve):
                raise ValueError("task ID reused with different item or reserve")
            return previous.copy()
        assigned = self.by_item.setdefault(item, [])
        block, index = divmod(len(assigned), len(RATIOS))
        arms = list(RATIOS)
        random.Random(f"{self.seed}:{item}:{block}").shuffle(arms)
        arm = arms[index]
        result = {"task_id": task_id, "item": item, "reserve": reserve,
                  "assigned_arm": arm, "planned_ask": candidate_price(reserve, arm),
                  "opening_ask_actual": None}
        self.assignments[task_id] = result
        assigned.append(task_id)
        return result.copy()

    def verify_actual_ask(self, task_id: str, actual_ask: int) -> None:
        assignment = self.assignments[task_id]
        if type(actual_ask) is not int or actual_ask != assignment["planned_ask"]:
            raise ValueError("buyer-visible ask differs from assigned arm")
        assignment["opening_ask_actual"] = actual_ask

    def record_outcome(self, task_id: str, status: str, final_price: int | None = None) -> None:
        """Accept only externally supplied outcomes for an exposed assignment."""
        assignment = self.assignments[task_id]
        if assignment["opening_ask_actual"] is None:
            raise ValueError("cannot score an arm that was not exposed")
        allowed = {"COUNTED", "REJECTED", "TIMED_OUT", "INFRA_ERROR", "CANCELLED", "UNKNOWN"}
        if status not in allowed:
            raise ValueError(status)
        if status == "COUNTED":
            if type(final_price) is not int or final_price < assignment["reserve"]:
                raise ValueError("counted trade requires a valid final price")
        elif final_price is not None:
            raise ValueError("only counted trades have a final price")
        outcome = {"status": status, "final_price": final_price}
        previous = self.outcomes.get(task_id)
        if previous is not None and previous != outcome:
            raise ValueError("conflicting duplicate outcome")
        self.outcomes[task_id] = outcome

    def summary(self) -> list[dict]:
        groups: dict[tuple[str, str], dict] = {}
        for task_id, assignment in self.assignments.items():
            key = (assignment["item"], assignment["assigned_arm"])
            group = groups.setdefault(key, {"item": key[0], "arm": key[1],
                                            "assigned": 0, "exposed": 0, "counted": 0,
                                            "outcomes": Counter(), "counted_margin": 0})
            group["assigned"] += 1
            group["exposed"] += assignment["opening_ask_actual"] is not None
            outcome = self.outcomes.get(task_id)
            if outcome is not None:
                group["outcomes"][outcome["status"]] += 1
                if outcome["status"] == "COUNTED":
                    group["counted"] += 1
                    group["counted_margin"] += outcome["final_price"] - assignment["reserve"]
        return [{**group, "outcomes": dict(group["outcomes"])}
                for _, group in sorted(groups.items())]


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline-only pricing assignment dry run")
    parser.add_argument("--seed", type=int, default=26510122)
    parser.add_argument("--tasks", type=Path,
                        help="JSON list of task_id/item/reserve; no outcomes are inferred")
    args = parser.parse_args()
    simulator = PricingSimulator(args.seed)
    if args.tasks:
        tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
        assignments = []
        for task in tasks:
            assigned = simulator.assign(task["task_id"], task["item"], task["reserve"])
            if task.get("opening_ask_actual") is not None:
                simulator.verify_actual_ask(task["task_id"], task["opening_ask_actual"])
                assigned = simulator.assign(task["task_id"], task["item"], task["reserve"])
            assignments.append(assigned)
    else:
        assignments = []
    print(json.dumps({
        "mode": "offline only; no market outcomes created",
        "seed": args.seed,
        "candidate_prices": {item: {arm: candidate_price(reserve, arm) for arm in RATIOS}
                             for item, reserve in {"lamp": 100, "chair": 180, "bookshelf": 260}.items()},
        "assignments": assignments,
        "summary": simulator.summary(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
