"""Independent Seller and Buyer strategies; all truth/safety checks stay external."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def _grade_factor(grade: str) -> float:
    return {"A": 1.0, "B": 0.90, "C": 0.72}.get(grade, 0.0)


@dataclass(frozen=True)
class SellerOfferPolicy:
    ask: int
    floor: int
    concession: float
    claim: str | None
    warranty_hours: float
    include_cert: bool = True


class SellerStrategy:
    code = "baseline"
    label = "Baseline"

    def __init__(self) -> None:
        self.observed_orders: dict[str, int] = {}
        self.attempts: dict[str, int] = {}
        self.sales: dict[str, int] = {}

    def offer(self, unit: dict[str, Any], product: dict[str, Any], now: int) -> SellerOfferPolicy:
        ask = max(unit["cost"], product["list_price"])
        return SellerOfferPolicy(int(ask), int(unit["cost"]), 0.08, None, 0)

    def restock(self, sku: str, stock: int, product: dict[str, Any], balance: int) -> int:
        return 1 if stock == 0 else 0

    max_purchase_share = 1.0
    max_stock_share = 1.0

    def observe_order(self, sku: str) -> None:
        self.observed_orders[sku] = self.observed_orders.get(sku, 0) + 1

    def observe_result(self, sku: str, sold: bool, margin: int = 0) -> None:
        self.attempts[sku] = self.attempts.get(sku, 0) + 1
        if sold:
            self.sales[sku] = self.sales.get(sku, 0) + 1


class StableSeller(SellerStrategy):
    code, label = "A", "Stable"

    def offer(self, unit, product, now):
        floor = math.ceil(unit["cost"] * 1.20)
        ask = max(floor, round(product["list_price"] * 0.98))
        return SellerOfferPolicy(ask, floor, 0.05, None, 0)

    def restock(self, sku, stock, product, balance):
        # Small lots; avoid food unless an order for it has already been observed twice.
        if stock or (product["kind"] == "food" and self.observed_orders.get(sku, 0) < 2):
            return 0
        return 1


class DiscountSeller(SellerStrategy):
    code, label = "B", "Discount"

    def offer(self, unit, product, now):
        ask = round(product["list_price"] * 0.82)
        floor = round(unit["cost"] * 0.95)  # explicit, bounded loss experiment
        return SellerOfferPolicy(max(ask, floor), floor, 0.14, None, 0)

    def restock(self, sku, stock, product, balance):
        return max(0, 4 - stock)


class SafeDiscountSeller1(DiscountSeller):
    code, label = "B-safe-1", "Discount with balanced caps"
    max_purchase_share, max_stock_share = 0.10, 0.20

    def offer(self, unit, product, now):
        floor = math.ceil(unit["cost"] * 1.05)
        return SellerOfferPolicy(max(round(product["list_price"] * 0.82), floor), floor, 0.12, None, 0)

    def restock(self, sku, stock, product, balance):
        desired = 1 if product["kind"] == "food" else 3
        return max(0, desired - stock)


class SafeDiscountSeller2(DiscountSeller):
    code, label = "B-safe-2", "Discount with strict caps"
    max_purchase_share, max_stock_share = 0.05, 0.10

    def offer(self, unit, product, now):
        floor = math.ceil(unit["cost"] * 1.12)
        return SellerOfferPolicy(max(round(product["list_price"] * 0.85), floor), floor, 0.09, None, 0)

    def restock(self, sku, stock, product, balance):
        observed = self.observed_orders.get(sku, 0)
        desired = 1 if product["kind"] == "food" or observed < 3 else 2
        return max(0, desired - stock)


class TrustSeller(SellerStrategy):
    code, label = "C", "Trust"

    def offer(self, unit, product, now):
        quality = {"A": 1.05, "B": 0.93, "C": 0.78}[unit["grade"]]
        floor = math.ceil(unit["cost"] * 1.12)
        ask = max(floor, round(product["list_price"] * quality))
        warranty = 6 if product["kind"] == "goods" else 0
        return SellerOfferPolicy(ask, floor, 0.07, unit["grade"], warranty, True)

    def restock(self, sku, stock, product, balance):
        return max(0, 2 - stock)


class AdaptiveSeller(SellerStrategy):
    code, label = "D", "Adaptive"

    def offer(self, unit, product, now):
        attempts = self.attempts.get(unit["sku"], 0)
        conversion = self.sales.get(unit["sku"], 0) / attempts if attempts else 0.5
        multiplier = 1.07 if conversion > 0.70 else 0.90 if conversion < 0.35 else 0.98
        floor = math.ceil(unit["cost"] * 1.05)
        ask = max(floor, round(product["list_price"] * multiplier))
        return SellerOfferPolicy(ask, floor, 0.10, unit["grade"], 3 if product["kind"] == "goods" else 0, True)

    def restock(self, sku, stock, product, balance):
        # Uses observed order counts only; no future order list is available here.
        desired = min(4, 1 + self.observed_orders.get(sku, 0) // 5)
        if product["kind"] == "food":
            desired = min(desired, 2)
        return max(0, desired - stock)


class BuyerStrategy:
    code = "baseline"
    label = "Baseline"

    def __init__(self) -> None:
        self.history: dict[str, dict[str, float]] = {}
        self.observation_count = 0

    def rank(self, order: dict[str, Any], offers: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any], str]]:
        rows = [(-offer["ask"], offer, "lowest safe ask") for offer in offers]
        return sorted(rows, key=lambda row: (-row[0], row[1]["shop"]))

    def bid(self, order: dict[str, Any], offer: dict[str, Any], turn: int, max_turns: int) -> int:
        low = round(offer["list_price"] * 0.78)
        return min(order["budget"], round(low + (order["budget"] - low) * (turn + 1) / max_turns))

    def observe(self, shop: str, settled: bool, surplus: int, reason: str) -> None:
        row = self.history.setdefault(shop, {"attempts": 0, "settled": 0, "surplus": 0})
        row["attempts"] += 1
        row["settled"] += int(settled)
        row["surplus"] += surplus
        self.observation_count += 1


class StableBuyer(BuyerStrategy):
    code, label = "A", "Stable"

    def rank(self, order, offers):
        rows = []
        for offer in offers:
            past = self.history.get(offer["shop"], {})
            rate = past.get("settled", 0) / past.get("attempts", 1) if past else offer["reliability"]
            score = rate * 100 + _grade_factor(offer["grade"]) * 20 - offer["ask"] / offer["list_price"] * 10
            rows.append((score, offer, "verified reliability and quality"))
        return sorted(rows, key=lambda row: (-row[0], row[1]["shop"]))

    def bid(self, order, offer, turn, max_turns):
        ceiling = min(order["budget"], round(offer["list_price"] * 0.96))
        start = round(offer["list_price"] * 0.82)
        return round(start + (ceiling - start) * (turn + 1) / max_turns)


class DiscountBuyer(BuyerStrategy):
    code, label = "B", "Discount"

    def rank(self, order, offers):
        return sorted(
            [(-o["ask"], o, "lowest verified price") for o in offers],
            key=lambda row: (-row[0], row[1]["shop"]),
        )

    def bid(self, order, offer, turn, max_turns):
        ceiling = min(order["budget"], round(offer["list_price"] * 0.90))
        start = round(offer["list_price"] * 0.68)
        return round(start + (ceiling - start) * (turn + 1) / max_turns)


class QualityDiscountBuyer(DiscountBuyer):
    code, label = "B-quality", "Discount with quality weighting"

    def rank(self, order, offers):
        rows = []
        for offer in offers:
            past = self.history.get(offer["shop"])
            success = past["settled"] / past["attempts"] if past else offer["reliability"]
            price_value = (offer["list_price"] - offer["ask"]) / offer["list_price"]
            quality = _grade_factor(offer["grade"])
            warranty = min(offer["warranty_hours"], 6) / 6
            score = 55 * price_value + 22 * quality + 13 * warranty + 10 * success
            rows.append((score, offer, "safe price, verified grade, warranty and past settlement"))
        return sorted(rows, key=lambda row: (-row[0], row[1]["shop"]))


class TrustBuyer(BuyerStrategy):
    code, label = "C", "Trust"

    def rank(self, order, offers):
        rows = []
        for offer in offers:
            trust = 35 * int(offer["cert_valid"]) + 8 * offer["warranty_hours"] + 25 * _grade_factor(offer["grade"])
            value = (offer["list_price"] - offer["ask"]) / offer["list_price"] * 20
            rows.append((trust + value, offer, "certificate, grade and warranty value"))
        return sorted(rows, key=lambda row: (-row[0], row[1]["shop"]))

    def bid(self, order, offer, turn, max_turns):
        premium = 1.02 if offer["cert_valid"] and offer["warranty_hours"] >= 3 else 0.90
        ceiling = min(order["budget"], round(offer["list_price"] * premium))
        start = round(offer["list_price"] * 0.82)
        return round(start + (ceiling - start) * (turn + 1) / max_turns)


class AdaptiveBuyer(BuyerStrategy):
    code, label = "D", "Adaptive"

    def rank(self, order, offers):
        rows = []
        total = max(1, self.observation_count)
        for offer in offers:
            past = self.history.get(offer["shop"])
            if not past:
                score = 1_000.0  # deterministic exploration of unseen shops
                reason = "unseen shop exploration"
            else:
                mean_surplus = past["surplus"] / past["attempts"] / offer["list_price"]
                success = past["settled"] / past["attempts"]
                bonus = math.sqrt(2 * math.log(total + 1) / past["attempts"])
                score = success + mean_surplus + bonus
                reason = "past-only UCB score"
            rows.append((score, offer, reason))
        return sorted(rows, key=lambda row: (-row[0], row[1]["shop"]))

    def bid(self, order, offer, turn, max_turns):
        past = self.history.get(offer["shop"])
        learned = past["surplus"] / past["attempts"] if past else 0
        ceiling = min(order["budget"], round(offer["list_price"] - max(0, learned) * 0.25))
        start = round(offer["list_price"] * 0.76)
        return round(start + (ceiling - start) * (turn + 1) / max_turns)


SELLER_STRATEGIES = {c.code: c for c in (StableSeller, DiscountSeller, TrustSeller, AdaptiveSeller)}
BUYER_STRATEGIES = {c.code: c for c in (StableBuyer, DiscountBuyer, TrustBuyer, AdaptiveBuyer)}

