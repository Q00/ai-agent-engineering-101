"""Deterministic local settlement and accounting; never calls ACP/AP2 or a network."""
from __future__ import annotations

import argparse
import copy
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mock_market import BASE_TS, _read_json, _read_jsonl, validate_scenario

from .agents import BaselineBuyer, BaselineSeller, certificate_matches
from .policies import Quote


class UnsafeScenarioError(ValueError):
    pass


@dataclass
class TradeResult:
    order: str
    status: str
    reason: str
    unit: str | None = None
    sku: str | None = None
    price: int | None = None
    seller_margin: int | None = None
    buyer_value: int = 0
    buyer_surplus: int = 0


class MockTradeEngine:
    def __init__(
        self,
        scenario: Path,
        seller: BaselineSeller | None = None,
        buyer: BaselineBuyer | None = None,
        *,
        now: int = BASE_TS,
    ) -> None:
        errors = validate_scenario(scenario)
        if errors:
            raise UnsafeScenarioError("; ".join(errors))
        self.scenario = scenario
        self.now = now
        self.catalog = copy.deepcopy(_read_json(scenario / "catalog.json"))
        self.inventory = copy.deepcopy(_read_json(scenario / "inventory.json"))
        self.orders = copy.deepcopy(_read_jsonl(scenario / "mock_orders.jsonl"))
        self.events = copy.deepcopy(_read_jsonl(scenario / "mock_events.jsonl"))
        self.seller = seller or BaselineSeller()
        self.buyer = buyer or BaselineBuyer()
        self.settled_orders: set[str] = set()
        self.sold_units: set[str] = set()
        self.results: list[TradeResult] = []
        self.purchases: dict[str, dict[str, Any]] = {}
        self.initial_balance = self.inventory["balance"]
        self.inventory_purchase_cost = int(self.inventory["start"] + self.inventory.get("injected", 0) - self.initial_balance)
        self.revenue = 0
        self.sale_margins: list[int] = []
        self.expired_loss = 0
        self.storage_cost = 0
        self.warranty_refunds = 0
        self.attempts = 0
        self.failure_reasons: dict[str, int] = {}

    def run_order(self, order: dict[str, Any]) -> TradeResult:
        self.attempts += 1
        order_id = order.get("order", "")
        before = self._financial_snapshot()
        if order_id in self.settled_orders:
            return self._fail(order_id, "duplicate_order", before)
        if not isinstance(order.get("expires"), (int, float)) or order["expires"] <= self.now:
            return self._fail(order_id, "expired_order", before)
        quote, reason = self.seller.propose(order, self.inventory, self.catalog, self.now)
        if quote is None:
            return self._fail(order_id, reason, before)
        unit = self._unit(quote.unit)
        if unit is None:
            return self._fail(order_id, "unit_unavailable", before)
        decision = self.buyer.evaluate(order, quote, unit, self.now)
        if not decision.accept:
            return self._fail(order_id, decision.reason, before, quote)
        reason = self._settlement_check(order, quote, unit)
        if reason:
            return self._fail(order_id, reason, before, quote)
        # Atomic mutation starts only after all checks have passed.
        self.inventory["units"].remove(unit)
        self.inventory["balance"] += quote.price
        self.settled_orders.add(order_id)
        self.sold_units.add(quote.unit)
        self.revenue += quote.price
        margin = quote.price - unit["cost"]
        self.sale_margins.append(margin)
        value = int(self.catalog[quote.sku]["list_price"])
        purchase = {
            "order": order_id, "unit": quote.unit, "sku": quote.sku, "price": quote.price,
            "value": value, "refund": 0, "settled_at": self.now, "warranty_hours": quote.warranty_hours,
        }
        self.purchases[quote.unit] = purchase
        result = TradeResult(order_id, "simulated_settled", "settled", quote.unit, quote.sku, quote.price,
                             margin, value, value - quote.price)
        self.results.append(result)
        self._assert_changed_once(before, quote.price, -1)
        return result

    def run_all(self) -> list[TradeResult]:
        return [self.run_order(order) for order in self.orders]

    def apply_time_costs(self, new_now: int) -> dict[str, int]:
        if new_now < self.now:
            raise ValueError("time cannot move backwards")
        elapsed_days = (new_now - self.now) / 86_400
        goods_cost = sum(u["cost"] for u in self.inventory["units"] if u["kind"] == "goods")
        storage = int(round(goods_cost * self.inventory.get("storage_per_day", 0) * elapsed_days))
        expired = [u for u in self.inventory["units"] if u["kind"] == "food" and u["expires_at"] <= new_now]
        self.inventory["balance"] -= storage
        self.storage_cost += storage
        self.expired_loss += sum(u["cost"] for u in expired)
        for unit in expired:
            self.inventory["units"].remove(unit)
        self.now = new_now
        return {"storage_cost": storage, "expired_units": len(expired), "expired_loss": sum(u["cost"] for u in expired)}

    def apply_events(self) -> list[dict[str, Any]]:
        outcomes = []
        for event in self.events:
            if event.get("event") != "broken" or event.get("unit") not in self.purchases:
                continue
            purchase = self.purchases[event["unit"]]
            purchase["value"] = 0
            covered = event["at"] <= purchase["settled_at"] + purchase["warranty_hours"] * 3600
            refund = purchase["price"] if covered else 0
            if refund and purchase["refund"] == 0:
                purchase["refund"] = refund
                self.inventory["balance"] -= refund
                self.warranty_refunds += refund
            outcomes.append({"unit": event["unit"], "covered": covered, "refund": refund})
        return outcomes

    def seller_metrics(self) -> dict[str, Any]:
        successes = len(self.settled_orders)
        return {
            "inventory_purchase_cost": self.inventory_purchase_cost,
            "revenue": self.revenue,
            "balance": self.inventory["balance"],
            "balance_profit": self.inventory["balance"] - self.inventory["start"] - self.inventory.get("injected", 0),
            "sale_margins": list(self.sale_margins),
            "sold": successes,
            "remaining_units": len(self.inventory["units"]),
            "remaining_stock_cost": sum(u["cost"] for u in self.inventory["units"]),
            "expired_loss": self.expired_loss,
            "storage_cost": self.storage_cost,
            "warranty_refunds": self.warranty_refunds,
            "failed": self.attempts - successes,
            "success_rate": successes / self.attempts if self.attempts else 0.0,
            "failure_reasons": dict(sorted(self.failure_reasons.items())),
            "official_percentile": None,
            "mock_stars": None,
        }

    def buyer_metrics(self) -> dict[str, Any]:
        successes = len(self.purchases)
        paid = sum(p["price"] for p in self.purchases.values())
        refunds = sum(p["refund"] for p in self.purchases.values())
        value = sum(p["value"] for p in self.purchases.values())
        return {
            "paid": paid, "refunds": refunds, "value": value,
            "surplus": value - paid + refunds,
            "successful": successes, "failed": self.attempts - successes,
            "settlement_rate": successes / self.attempts if self.attempts else 0.0,
            "quality_verified": successes,
            "rejection_reasons": dict(sorted(self.failure_reasons.items())),
            "official_percentile": None,
            "mock_forum_reputation": None,
        }

    def report(self) -> dict[str, Any]:
        return {
            "status": "offline_mock_only", "scenario": self.scenario.name, "now": self.now,
            "seller": self.seller_metrics(), "buyer": self.buyer_metrics(),
            "results": [asdict(row) for row in self.results],
        }

    def _settlement_check(self, order: dict[str, Any], quote: Quote, unit: dict[str, Any]) -> str | None:
        if order["order"] in self.settled_orders:
            return "duplicate_order"
        if quote.unit in self.sold_units or unit not in self.inventory["units"]:
            return "duplicate_or_missing_unit"
        if quote.sku not in order["acceptable"] or quote.sku != unit["sku"]:
            return "unacceptable_sku"
        if quote.price > order["budget"]:
            return "over_budget"
        if quote.claim is not None and quote.claim != unit["grade"]:
            return "dishonest_claim"
        if not certificate_matches(quote.cert, unit):
            return "invalid_certificate"
        if unit["kind"] == "food" and (unit["grade"] == "expired" or unit["expires_at"] <= self.now):
            return "expired_food"
        return None

    def _unit(self, unit_id: str) -> dict[str, Any] | None:
        return next((unit for unit in self.inventory["units"] if unit.get("unit") == unit_id), None)

    def _financial_snapshot(self) -> tuple[int | float, int, int]:
        return self.inventory["balance"], len(self.inventory["units"]), self.revenue

    def _fail(self, order: str, reason: str, before: tuple[int | float, int, int], quote: Quote | None = None) -> TradeResult:
        self.failure_reasons[reason] = self.failure_reasons.get(reason, 0) + 1
        result = TradeResult(order, "rejected", reason, quote.unit if quote else None,
                             quote.sku if quote else None, quote.price if quote else None)
        self.results.append(result)
        if self._financial_snapshot() != before:
            raise AssertionError("failed transaction mutated balance, inventory, or revenue")
        return result

    def _assert_changed_once(self, before: tuple[int | float, int, int], price: int, unit_delta: int) -> None:
        after = self._financial_snapshot()
        expected = (before[0] + price, before[1] + unit_delta, before[2] + price)
        if after != expected:
            raise AssertionError(f"settlement was not atomic: expected {expected}, got {after}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args(argv)
    try:
        engine = MockTradeEngine(args.scenario)
    except UnsafeScenarioError as exc:
        print(json.dumps({"status": "rejected_fixture", "reason": str(exc)}))
        return 2
    engine.run_all()
    engine.apply_events()
    print(json.dumps(engine.report(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

