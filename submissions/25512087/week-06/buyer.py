"""Harness-driven A2A v1 buyer for the Week 06 trade test."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Protocol

from harness import CommandHarness
from policy import BuyerPolicy
from protocol import A2AClient, last_price, receipt_price, task_state, task_text


class BuyerClient(Protocol):
    def discover(self) -> dict:
        """Read the remote Agent Card."""

    def send(self, text: str, task_id: str | None = None) -> dict:
        """Send or continue one A2A task."""


class BuyerDecisionPolicy(Protocol):
    def respond(self, item: str, budget: int, ask: int, rounds: int):
        """Choose the buyer's next move."""


class BuyerRunner:
    """Maintain a compact task state while the harness chooses each move."""

    def __init__(
        self,
        client: BuyerClient,
        policy: BuyerDecisionPolicy,
        max_rounds: int = 6,
    ) -> None:
        self.client = client
        self.policy = policy
        self.max_rounds = max_rounds

    def run(self, item: str, budget: int) -> dict[str, object]:
        card = self.client.discover()
        self._log("card", name=card.get("name"), item=item, budget=budget)
        task = self.client.send(f"I want to buy a {item}")
        task_id = str(task.get("id", ""))
        rounds = 0
        while task_state(task) == "TASK_STATE_INPUT_REQUIRED" and rounds < self.max_rounds:
            ask = last_price(task)
            if ask is None:
                return {"outcome": "invalid", "price": None, "task_id": task_id}
            decision = self.policy.respond(item, budget, ask, rounds)
            self._log(
                "decision",
                task_id=task_id,
                ask=ask,
                action=decision.action,
                offer=decision.price,
                reason=decision.reason,
            )
            if decision.action == "walk":
                return {"outcome": "walk", "price": None, "task_id": task_id}
            offer = int(decision.price or 0)
            if offer > budget:
                raise RuntimeError(f"budget guard failed: {offer} > {budget}")
            task = self.client.send(str(offer), task_id)
            rounds += 1

        state = task_state(task)
        if state == "TASK_STATE_COMPLETED":
            price = receipt_price(task)
            if price is None or price > budget:
                raise RuntimeError(f"invalid receipt price: {price}, budget {budget}")
            return {"outcome": "deal", "price": price, "task_id": task_id}
        if state in {"TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}:
            return {"outcome": "no_deal", "price": None, "task_id": task_id}
        return {
            "outcome": "open",
            "price": None,
            "task_id": task_id,
            "last_message": task_text(task),
        }

    @staticmethod
    def _log(event: str, **values: object) -> None:
        print(
            json.dumps({"event": event, **values}, ensure_ascii=False),
            file=sys.stderr,
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--harness-cmd")
    args = parser.parse_args()
    client = A2AClient(args.url)
    try:
        result = BuyerRunner(
            client,
            BuyerPolicy(CommandHarness(args.harness_cmd)),
        ).run(args.item, args.budget)
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
