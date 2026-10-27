"""A2A v1 buyer: week-06 buyer (Round 0) and the negotiation engine of market_buyer.py.

The default policy is the rule-based LadderBuyerPolicy (no harness on the critical path).
--use-harness switches back to the harness-driven BuyerPolicy; the budget is enforced in code either way.
A bad receipt (over budget, above our last offer, wrong item, unsigned) never crashes the buyer:
it comes back as an outcome so the caller can review the shop and remember it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Protocol

from harness import CommandHarness
from negotiation import LadderBuyerPolicy
from policy import BuyerPolicy
from protocol import A2AClient, last_price, receipt_data, receipt_price, task_state, task_text


class BuyerClient(Protocol):
    def discover(self) -> dict:
        """Read the remote Agent Card."""

    def send(self, text: str, task_id: str | None = None) -> dict:
        """Send or continue one A2A task."""


class BuyerDecisionPolicy(Protocol):
    def respond(self, item: str, budget: int, ask: int, rounds: int):
        """Choose the buyer's next move."""


def check_receipt(receipt: dict[str, Any] | None, item: str, budget: int, last_offer: int | None,
                  seen_tasks: set[str] | None = None) -> str | None:
    """A receipt whose task differs from the A2A task id is not flagged: the market only requires a task id
    it has not seen, and some honest shops use their own ids. `seen_tasks` = this shop's earlier receipt
    task ids; a repeat is reported as a soft "suspect_receipt" (the market will refuse it anyway)."""
    """What is wrong with a receipt, or None. These are the dishonest-shop signals."""
    if receipt is None:
        return "completed without a receipt"
    price = receipt.get("price")
    if not isinstance(price, (int, float)):
        return "receipt has no numeric price"
    if price > budget:
        return f"receipt price {price:g} is above the budget {budget}"
    if last_offer is not None and price > last_offer:
        return f"agreed {last_offer}, the receipt said {price:g}"
    if receipt.get("item") not in (None, item):
        return f"receipt names {receipt.get('item')!r}, ordered {item!r}"
    if seen_tasks and receipt.get("task") in seen_tasks:     # same shop, same task id as an earlier deal
        return f"reused receipt: task {str(receipt.get('task'))[:40]!r} was on an earlier receipt from this shop"
    if not receipt.get("sig_seller"):
        return "receipt is not signed (no sig_seller)"
    return None


class BuyerRunner:
    """Maintain a compact task state while the policy chooses each move."""

    def __init__(self, client: BuyerClient, policy: BuyerDecisionPolicy, max_rounds: int = 6,
                 seen_tasks: set[str] | None = None) -> None:
        self.seen_tasks = seen_tasks or set()
        self.client = client
        self.policy = policy
        self.max_rounds = max_rounds

    def run(self, item: str, budget: int) -> dict[str, object]:
        started = time.time()
        card = self.client.discover()
        self._log("card", name=card.get("name"), item=item, budget=budget)
        task = self.client.send(f"I want to buy a {item}")
        task_id = str(task.get("id", ""))
        rounds, last_offer, asks = 0, None, []
        self.offers: list[int] = []
        while task_state(task) == "TASK_STATE_INPUT_REQUIRED" and rounds < self.max_rounds:
            ask = last_price(task)
            if ask is None:
                return self._done("invalid", task_id, rounds, started, asks=asks,
                                  detail=f"no price in {task_text(task)[:80]!r}")
            asks.append(ask)
            decision = self.policy.respond(item, budget, ask, rounds)
            self._log("decision", task_id=task_id, ask=ask, action=decision.action,
                      offer=decision.price, reason=decision.reason)
            if decision.action == "walk":
                return self._done("walk", task_id, rounds, started, asks=asks, detail=decision.reason)
            offer = int(decision.price or 0)
            if offer > budget:                       # last line of defence; policies already clamp
                return self._done("walk", task_id, rounds, started, asks=asks, detail="budget guard")
            task = self.client.send(str(offer), task_id)
            last_offer = offer
            self.offers.append(offer)
            rounds += 1

        state = task_state(task)
        if state == "TASK_STATE_COMPLETED":
            receipt = receipt_data(task)
            problem = check_receipt(receipt, item, budget, last_offer, self.seen_tasks)
            if problem is None:
                outcome = "deal"
            elif "not signed" in problem or "without a receipt" in problem:
                outcome = "unsigned"        # maybe honest, but the deal cannot count in Round 2
            elif problem.startswith("reused receipt"):
                outcome = "suspect_receipt" # replay, or a shop whose counter restarted: soft, never a blacklist
            else:
                outcome = "bad_receipt"     # overcharge, wrong item, over budget: dishonest
            return self._done(outcome, task_id, rounds, started, asks=asks, receipt=receipt,
                              price=receipt_price(task), detail=problem, last_offer=last_offer)
        if state in {"TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}:
            return self._done("no_deal", task_id, rounds, started, asks=asks, detail=task_text(task)[:120])
        return self._done("open", task_id, rounds, started, asks=asks, detail=task_text(task)[:120])

    def _done(self, outcome: str, task_id: str, rounds: int, started: float, **extra: Any) -> dict[str, object]:
        result = {"outcome": outcome, "price": extra.pop("price", None), "task_id": task_id,
                  "rounds": rounds, "seconds": round(time.time() - started, 1),
                  "offers": list(getattr(self, "offers", [])), **extra}
        result.setdefault("receipt", None)
        return result

    @staticmethod
    def _log(event: str, **values: object) -> None:
        print(json.dumps({"event": event, **values}, ensure_ascii=True), file=sys.stderr, flush=True)   # ascii: no U+2028 line breaks


def make_policy(use_harness: bool, harness_cmd: str | None, hint: int | None = None) -> BuyerDecisionPolicy:
    if use_harness:
        return BuyerPolicy(CommandHarness(harness_cmd))
    return LadderBuyerPolicy(hint=hint)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--harness-cmd")
    parser.add_argument("--use-harness", action="store_true")
    args = parser.parse_args()
    client = A2AClient(args.url, timeout=60)
    try:
        result = BuyerRunner(client, make_policy(args.use_harness, args.harness_cmd)).run(args.item, args.budget)
    except Exception as error:                      # never die without a result line
        result = {"outcome": "error", "price": None, "receipt": None,
                  "detail": f"{type(error).__name__}: {str(error)[:160]}"}
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
