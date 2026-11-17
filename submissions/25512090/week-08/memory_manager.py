import json
from pathlib import Path
import time

class MemoryManager:
    def __init__(self, market_dir="submissions/25512090/week-08/.market"):
        self.market_dir = Path(market_dir)
        self.store_path = Path(__file__).parent / "memory_store.json"
        self.load()

    def load(self):
        if self.store_path.exists():
            try:
                self.data = json.loads(self.store_path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {"competitor_profiles": {}, "subsidiary_registry": [], "sku_velocity": {}, "last_synced": 0}
        else:
            # subsidiary_registry MUST be a list (register_subsidiary does subs.append(...)) -- this
            # branch previously initialized it as {} (dict), which would crash with
            # AttributeError: 'dict' object has no attribute 'append' the first time a fresh
            # memory_store.json (no file on disk yet) ever registered a subsidiary.
            self.data = {"competitor_profiles": {}, "subsidiary_registry": [], "sku_velocity": {}, "last_synced": 0}

    def save(self):
        self.data["last_synced"] = time.time()
        tmp = self.store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.store_path)

    def update_competitor(self, shop_id, volume=0, refund_rate=0.0, balance=0, loan_balance=0):
        profiles = self.data.setdefault("competitor_profiles", {})
        profiles[shop_id] = {
            "volume": volume,
            "refund_rate": refund_rate,
            "estimated_balance": balance,
            "loan_balance": loan_balance,
            "updated_at": time.time()
        }
        self.save()

    def register_subsidiary(self, shop_id, debt_amount):
        subs = self.data.setdefault("subsidiary_registry", [])
        if shop_id not in [s["shop_id"] for s in subs]:
            subs.append({"shop_id": shop_id, "debt_absorbed": debt_amount, "acquired_at": time.time()})
            self.save()

    def update_sku_velocity(self, sku, price, frequency=1):
        vel = self.data.setdefault("sku_velocity", {})
        item = vel.setdefault(sku, {"frequency": 0, "last_price": price})
        item["frequency"] += frequency
        item["last_price"] = price
        self.save()

    def record_customer_activity(self, customer_id, sku, amount=0):
        """Track repeat engagement keyed by the only persistent identifiers a seller actually has
        in this market: a voucher/forward-contract chain id (redeeming our own voucher is evidence
        of the same counterparty returning for more) or a self-identified loan desk borrower name
        (forum authorship is real, unlike the anonymous A2A buy message). Used to spot what a
        repeat customer actually buys, and to tell a first-touch buyer from a returning one."""
        if not customer_id:
            return None
        customers = self.data.setdefault("customer_profiles", {})
        c = customers.setdefault(customer_id, {"visits": 0, "skus": {}, "total_spent": 0,
                                                 "first_seen": time.time()})
        c["visits"] += 1
        c["last_seen"] = time.time()
        c["total_spent"] += amount
        c["skus"][sku] = c["skus"].get(sku, 0) + 1
        self.save()
        return c

    def customer_preferred_sku(self, customer_id):
        c = self.data.get("customer_profiles", {}).get(customer_id)
        if not c or not c.get("skus"):
            return None
        return max(c["skus"].items(), key=lambda x: x[1])[0]

    def is_repeat_customer(self, customer_id):
        c = self.data.get("customer_profiles", {}).get(customer_id)
        return bool(c and c["visits"] >= 2)


if __name__ == "__main__":
    mm = MemoryManager()
    print("MemoryManager initialized successfully.")
