"""The seller's stock and prices: tool functions the seller's brain can call.

Loaded from inventory.json when the server starts and kept in memory, so every
server start (and every trade test) begins with the same stock.
"""
import json
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))


class Inventory:
    def __init__(self, path=os.path.join(HERE, "inventory.json")):
        with open(path, encoding="utf-8") as f:
            self.items = json.load(f)
        self.sales = []
        self._lock = threading.Lock()

    def names(self):
        return list(self.items)

    def get(self, item):
        """{"item", "reserve", "list_price", "stock"} for one item, or None if we do not sell it."""
        if item not in self.items:
            return None
        return {"item": item, **self.items[item]}

    def check_ask(self, item, price):
        """(ok, reason): may the shop ask this price for this item?"""
        info = self.get(item)
        if info is None:
            return False, f"we do not sell {item}"
        if price < info["reserve"]:
            return False, f"{price} is below the reserve {info['reserve']} for {item}"
        return True, "ok"

    def check_sale(self, item, price):
        """(ok, reason): may the shop sell this item at this price right now?"""
        ok, reason = self.check_ask(item, price)
        if not ok:
            return ok, reason
        if self.items[item]["stock"] <= 0:
            return False, f"{item} is out of stock"
        return True, "ok"

    def sell(self, item, price, buyer=""):
        """Take one unit out of stock and record the sale. Raises ValueError when out of stock."""
        with self._lock:
            if self.items.get(item, {}).get("stock", 0) <= 0:
                raise ValueError(f"{item} is out of stock")
            self.items[item]["stock"] -= 1
            sale = {"time": time.strftime("%H:%M:%S"), "item": item, "price": price, "buyer": buyer}
            self.sales.append(sale)
            return sale

    def snapshot(self):
        """Current stock and every sale since the server started."""
        return {"items": json.loads(json.dumps(self.items)), "sales": list(self.sales)}
