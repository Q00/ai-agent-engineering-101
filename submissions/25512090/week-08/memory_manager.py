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
            self.data = {"competitor_profiles": {}, "subsidiary_registry": {}, "sku_velocity": {}, "last_synced": 0}

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

if __name__ == "__main__":
    mm = MemoryManager()
    print("MemoryManager initialized successfully.")
