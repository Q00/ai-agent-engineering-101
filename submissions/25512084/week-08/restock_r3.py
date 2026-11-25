import json
import re
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
MARKET = BASE / ".market"

inventory = json.loads(
    (MARKET / "inventory.json").read_text(encoding="utf-8")
)

catalog = json.loads(
    (MARKET / "catalog.json").read_text(encoding="utf-8")
)

demand = json.loads(
    (MARKET / "demand.json").read_text(encoding="utf-8")
)

units = [
    u for u in inventory.get("units", [])
    if u.get("grade") != "expired"
]

current = Counter(u["sku"] for u in units)

current_categories = {
    u.get("category")
    for u in units
    if u.get("category")
}

sales = Counter()
profits = defaultdict(int)

log_file = BASE / "logs" / "market-kit.log"

if log_file.exists():
    pattern = re.compile(
        r"\[seller\] \[strategy\] ACCEPT "
        r"sku=(\S+) price=(\d+) cost=(\d+) profit=(-?\d+)"
    )

    for line in log_file.read_text(
        encoding="utf-8",
        errors="ignore",
    ).splitlines():
        match = pattern.search(line)

        if not match:
            continue

        sku = match.group(1)
        profit = int(match.group(4))

        if profit > 0:
            sales[sku] += 1
            profits[sku] += profit


MAX_TOTAL_UNITS = 24
MAX_PROVEN_SKUS = 4

total_stock = len(units)
max_new_units = max(0, MAX_TOTAL_UNITS - total_stock)

if max_new_units <= 0:
    print(json.dumps({"buy": []}))
    raise SystemExit


balance = int(inventory.get("balance", 0))

# Keep the experiment conservative.
restock_budget = min(
    int(balance * 0.10),
    300_000,
)

buy = []
units_planned = 0
planned_categories = set(current_categories)


def product_price(product):
    try:
        return int(product.get("list_price", 0))
    except (TypeError, ValueError):
        return 0


def estimated_cost(product):
    price = product_price(product)
    return max(1, int(price * 0.75))


# --------------------------------------------------
# 1. EXPLOIT:
# Keep a small reserve of proven profitable sellers.
# --------------------------------------------------

top_skus = sorted(
    sales,
    key=lambda sku: (sales[sku], profits[sku]),
    reverse=True,
)[:MAX_PROVEN_SKUS]

for sku in top_skus:
    if units_planned >= max_new_units or restock_budget <= 0:
        break

    product = catalog.get(sku)

    if not product:
        continue

    # Proven goods can keep a small reserve.
    # Short-lived food should stay at one unit even if it sold well.
    kind = product.get("kind", "goods")
    category = product.get("category")

    row = next(
        (
            x for x in demand
            if x.get("category") == category
        ),
        {}
    )

    shelf_hours = row.get("shelf_hours")

    if (
        kind == "food"
        and shelf_hours is not None
        and shelf_hours < 168
    ):
        target_stock = 1
    else:
        target_stock = 3 if sales[sku] >= 5 else 2

    need = max(
        0,
        target_stock - current.get(sku, 0),
    )

    if need <= 0:
        continue

    cost = estimated_cost(product)
    affordable = restock_budget // cost

    qty = min(
        need,
        2,
        max_new_units - units_planned,
        affordable,
    )

    if qty <= 0:
        continue

    buy.append({
        "sku": sku,
        "qty": int(qty),
    })

    units_planned += qty
    restock_budget -= qty * cost

    category = product.get("category")
    if category:
        planned_categories.add(category)


# --------------------------------------------------
# 2. EXPLORE:
# Fill remaining slots with categories that appear
# most often in REAL observed orders.
# --------------------------------------------------

order_counts = Counter()

if log_file.exists():
    order_pattern = re.compile(
        r"\[order [^\]]+\] (.*?), budget "
    )

    for line in log_file.read_text(
        encoding="utf-8",
        errors="ignore",
    ).splitlines():
        match = order_pattern.search(line)

        if match:
            order_counts[match.group(1)] += 1


demand_by_item = {
    row.get("item"): row
    for row in demand
    if row.get("item")
}

exploration = []

for item, order_count in order_counts.most_common():
    row = demand_by_item.get(item)

    if not row:
        continue

    category = row.get("category")

    if not category or category in planned_categories:
        continue

    kind = row.get("kind", "goods")
    shelf_hours = row.get("shelf_hours")

    # Do not experiment with fast-expiring food.
    # Kimchi (168h) is allowed; 24/48/96h foods are not.
    if (
        kind == "food"
        and shelf_hours is not None
        and shelf_hours < 168
    ):
        continue

    try:
        median_price = int(row.get("median_price", 0))
    except (TypeError, ValueError):
        continue

    if median_price <= 0:
        continue

    # Avoid expensive experimental inventory.
    # This filters things like sofas and mattresses.
    if median_price > 60_000:
        continue

    products = []

    for sku, product in catalog.items():
        if product.get("category") != category:
            continue

        price = product_price(product)

        if price <= 0 or price > 60_000:
            continue

        products.append((sku, product))

    if not products:
        continue

    # Pick a representative SKU around the category's
    # normal price, but never target above 40k.
    target_price = min(median_price, 40_000)

    sku, product = min(
        products,
        key=lambda x: (
            abs(product_price(x[1]) - target_price),
            product_price(x[1]),
        ),
    )

    exploration.append({
        "sku": sku,
        "item": item,
        "category": category,
        "orders": order_count,
        "cost": estimated_cost(product),
        "price": product_price(product),
    })


# Already ordered by REAL demand frequency.
for item in exploration:
    if units_planned >= max_new_units or restock_budget <= 0:
        break

    if item["category"] in planned_categories:
        continue

    if item["cost"] > restock_budget:
        continue

    buy.append({
        "sku": item["sku"],
        "qty": 1,
    })

    units_planned += 1
    restock_budget -= item["cost"]
    planned_categories.add(item["category"])


print(json.dumps({"buy": buy}))