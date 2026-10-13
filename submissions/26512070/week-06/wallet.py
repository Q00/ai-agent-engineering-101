"""The buyer's budget: tool functions the buyer's brain can call."""


class Wallet:
    def __init__(self, budget):
        self.budget = budget
        self.offers = []
        self.purchases = []

    def check_offer(self, price):
        """(ok, reason): may the buyer offer this price?"""
        if price <= 0:
            return False, f"{price} is not a price"
        if price > self.budget:
            return False, f"{price} is over the budget {self.budget}"
        return True, "ok"

    def record_offer(self, price):
        self.offers.append(price)

    def record_purchase(self, item, price, seller):
        self.purchases.append({"item": item, "price": price, "seller": seller})

    def snapshot(self):
        return {"budget": self.budget, "offers": list(self.offers), "purchases": list(self.purchases)}
