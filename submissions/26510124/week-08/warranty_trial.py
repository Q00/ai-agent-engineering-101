"""Local, bounded B-grade warranty trial. No network or model calls.

Historic no-warranty profits do not measure insured failure risk. A configurable
revenue reserve is a pilot allowance, never an estimated failure probability.
"""
from copy import deepcopy
import json

from sales_performance import economics, valid_sale
from feedback import number


class WarrantyTrial:
    def __init__(self, folder):
        self.path = folder / "sales-performance.json"
        self.checked = None
        self.memory = None

    def read(self, now, rnd):
        if self.checked is None or not 0 <= now - self.checked < 60:
            self.checked, self.memory = now, None
            try:
                with self.path.open("rb") as stream:
                    raw = stream.read(1_000_001)
                if len(raw) <= 1_000_000:
                    self.memory = json.loads(raw)
            except (OSError, ValueError):
                pass
        m = self.memory
        if (not isinstance(m, dict) or m.get("schema") != 1 or m.get("round") != rnd
                or not number(m.get("updated_at")) or not 0 <= now - m["updated_at"] <= 1800
                or not isinstance(m.get("sales"), list) or len(m["sales"]) > 1000):
            return None
        return m

    def terms(self, product, terms, policy, rnd, now):
        rules = policy.get("b_warranty_trial", {})
        if (rules.get("enabled") is not True or product.get("grade") != "B"
                or product.get("kind") != "goods"):
            return terms
        memory = self.read(now, rnd)
        if memory is None:
            return terms
        mature, revenue, net, refunds = 0, 0, 0, 0
        units, ledgers = set(), set()
        for sale in memory["sales"]:
            if not isinstance(sale, dict):
                return terms
            if sale.get("sku") != product["sku"] or sale.get("grade") != "B":
                continue
            if (not valid_sale(sale, now) or sale.get("kind") != "goods"
                    or sale["unit"] in units or sale["ledger"] in ledgers
                    or number(memory.get("reset_at")) and sale["receipt_time"] < memory["reset_at"]):
                return terms
            units.add(sale["unit"]); ledgers.add(sale["ledger"])
            try:
                result = economics(sale, now)
            except (KeyError, TypeError, ValueError):
                return terms
            if result["status"] not in ("mature_estimate", "pending_warranty"):
                return terms
            # Wait the entire breakage window even when the old warranty was zero.
            sold = sale.get("settled_at", sale.get("observed_at"))
            if now - sold < 6 * 3600 or result["status"] == "pending_warranty":
                continue
            mature += 1
            revenue += sale["price"]
            net += result["net_estimate"]
            refunds += int(result["observed_refund"] > 0)
        reserve, margin = rules["reserve_percent"], rules["min_net_margin_percent"]
        if (mature < rules["min_mature_sales"] or revenue <= 0
                or refunds * 100 > mature * rules["max_refund_percent"]
                or net * 100 < revenue * (reserve + margin)):
            return terms
        adjusted = deepcopy(terms)
        denominator = 100 - reserve - margin
        floor = (product["cost"] * 100 + denominator - 1) // denominator
        adjusted.update(warranty_hours=6,
                        price_floor=max(terms["price_floor"], floor),
                        closing_price_floor=max(terms["closing_price_floor"], floor),
                        warranty_trial={"mature_B_sales": mature, "reserve_percent": reserve,
                                        "basis": "bounded own history; reserve is not a measured failure rate"})
        return adjusted
