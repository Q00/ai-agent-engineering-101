"""Strategy-neutral baseline Seller and Buyer with fail-closed input handling."""
from __future__ import annotations

from typing import Any

from .policies import (
    BudgetAndQualityBuyer,
    BuyerContext,
    BuyerNegotiationPolicy,
    ConservativeListPrice,
    Decision,
    Quote,
    SellerContext,
    SellerInformationPolicy,
    SellerPricePolicy,
    TruthfulMinimalTerms,
)

UNIT_FIELDS = {
    "unit", "sku", "name", "category", "list_price", "kind", "cost", "grade",
    "supplied_at", "expires_at", "cert",
}


def certificate_matches(cert: Any, unit: dict[str, Any]) -> bool:
    """Validate the explicit Mock certificate shape, not a real market signature."""
    return (
        isinstance(cert, dict)
        and cert.get("mock") is True
        and cert.get("unit") == unit.get("unit")
        and cert.get("sku") == unit.get("sku")
    )


class BaselineSeller:
    def __init__(
        self,
        price_policy: SellerPricePolicy | None = None,
        information_policy: SellerInformationPolicy | None = None,
        *,
        allow_below_cost: bool = False,
    ) -> None:
        self.price_policy = price_policy or ConservativeListPrice()
        self.information_policy = information_policy or TruthfulMinimalTerms()
        self.allow_below_cost = allow_below_cost
        self.history = {"attempts": 0, "quoted": 0, "rejected": 0}

    def propose(
        self, order: dict[str, Any], inventory: dict[str, Any], catalog: dict[str, Any], now: int,
    ) -> tuple[Quote | None, str]:
        self.history["attempts"] += 1
        acceptable = order.get("acceptable")
        units = inventory.get("units")
        if not isinstance(acceptable, list) or not isinstance(units, list):
            return self._reject("invalid_inventory_or_order")
        eligible = []
        for unit in units:
            if not isinstance(unit, dict) or not UNIT_FIELDS <= unit.keys():
                continue
            product = catalog.get(unit["sku"])
            if not isinstance(product, dict) or unit["sku"] not in acceptable:
                continue
            if unit["grade"] == "expired":
                continue
            if unit["kind"] == "food" and (not isinstance(unit["expires_at"], (int, float)) or unit["expires_at"] <= now):
                continue
            if not isinstance(unit["cost"], (int, float)) or unit["cost"] < 0:
                continue
            if any(unit.get(key) != product.get(key) for key in ("name", "category", "list_price", "kind")):
                continue
            eligible.append(unit)
        if not eligible:
            return self._reject("no_safe_inventory")
        unit = min(eligible, key=lambda row: (row["expires_at"] or float("inf"), row["supplied_at"], row["unit"]))
        context = SellerContext(now, order, unit, dict(self.history))
        try:
            price = self.price_policy.price(context)
            terms = self.information_policy.terms(context)
        except Exception:
            return self._reject("seller_policy_error")
        if not isinstance(price, int) or isinstance(price, bool) or price < 0:
            return self._reject("invalid_price")
        if not self.allow_below_cost and price < unit["cost"]:
            return self._reject("below_cost_baseline_policy")
        if terms.claim is not None and terms.claim != unit["grade"]:
            return self._reject("dishonest_claim")
        if not isinstance(terms.warranty_hours, (int, float)) or terms.warranty_hours < 0:
            return self._reject("invalid_warranty")
        quote = Quote(
            order=order["order"], unit=unit["unit"], sku=unit["sku"], price=price,
            grade=unit["grade"], expires_at=unit["expires_at"],
            cert=unit["cert"] if terms.include_cert else None,
            claim=terms.claim, warranty_hours=float(terms.warranty_hours),
        )
        self.history["quoted"] += 1
        return quote, "quoted"

    def _reject(self, reason: str) -> tuple[None, str]:
        self.history["rejected"] += 1
        return None, reason


class BaselineBuyer:
    def __init__(self, negotiation_policy: BuyerNegotiationPolicy | None = None) -> None:
        self.negotiation_policy = negotiation_policy or BudgetAndQualityBuyer()
        self.history = {"attempts": 0, "accepted": 0, "rejected": 0}

    def evaluate(self, order: dict[str, Any], quote: Quote, unit: dict[str, Any], now: int) -> Decision:
        self.history["attempts"] += 1
        reason = self._common_checks(order, quote, unit, now)
        if reason:
            self.history["rejected"] += 1
            return Decision(False, reason)
        try:
            decision = self.negotiation_policy.decide(BuyerContext(now, order, quote, dict(self.history)))
        except Exception:
            decision = Decision(False, "buyer_policy_error")
        self.history["accepted" if decision.accept else "rejected"] += 1
        return decision

    @staticmethod
    def _common_checks(order: dict[str, Any], quote: Quote, unit: dict[str, Any], now: int) -> str | None:
        if quote.order != order.get("order"):
            return "wrong_order"
        if quote.sku not in (order.get("acceptable") or []):
            return "unacceptable_sku"
        if quote.unit != unit.get("unit") or quote.sku != unit.get("sku"):
            return "unit_mismatch"
        if not isinstance(quote.price, int) or quote.price < 0:
            return "invalid_price"
        if quote.price > order.get("budget", -1):
            return "over_budget"
        if quote.grade != unit.get("grade") or quote.grade == "expired":
            return "invalid_grade"
        if quote.claim is not None and quote.claim != unit.get("grade"):
            return "dishonest_claim"
        if not certificate_matches(quote.cert, unit):
            return "invalid_certificate"
        if unit.get("kind") == "food":
            expires = unit.get("expires_at")
            needed = order.get("use_within_hours")
            if not isinstance(expires, (int, float)) or expires <= now:
                return "expired_food"
            if isinstance(needed, (int, float)) and expires < now + needed * 3600:
                return "insufficient_shelf_life"
        if quote.warranty_hours < 0:
            return "invalid_warranty"
        return None

