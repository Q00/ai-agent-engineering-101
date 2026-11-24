"""Unmodified default_restock function from official kit v4.
Source Git blob aa218565f383bfa02f24c4a623e03fc2330ac00c."""
RESTOCK_LOW = 20


def default_restock(inv, demand):
    """The kit's restock when agent.json has none: when stock runs low, a few units of random SKUs in random
    categories, spending at most a tenth of the balance on each. It knows nothing about what sells. Not clever."""
    if len(inv["units"]) >= RESTOCK_LOW or not demand or (inv.get("balance") or 0) <= 0:
        return []
    picks = random.sample(demand, k=min(3, len(demand)))
    buy = []
    for p in picks:
        skus = [s for s, x in CATALOG.items() if x["category"] == p["category"]]
        if not skus:
            continue
        sku = random.choice(skus)
        qty = min(2, int(inv["balance"] * 0.1 / (CATALOG[sku]["list_price"] * 0.75)))
        if qty >= 1:
            buy.append({"sku": sku, "qty": qty})
    return buy
