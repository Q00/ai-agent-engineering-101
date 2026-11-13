"""Observed public demand, explicit margins, truthful quality, and bounded stock capital."""
import collections
import email.utils
import json
import math
import statistics
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Every tunable number lives in params.json (read on change, so an experiment needs no restart).
# DEFAULTS are the values before the experiment loop started (E1 baseline).
DEFAULTS = {
    "shop": {"cash_reserve": 3500000, "stock_cost_cap": 1000000, "stock_cap_share": 1.0, "max_units": 60,
             "max_sku_units": 6, "per_run_spend": 500000, "food_cost_share": .35, "sku_cap_share": .6,
             "min_list_price": 1000, "max_list_price": 450000, "max_surge": 1.12, "warranty_exposure_cap": 500000,
             "min_sales": 4, "sales_exp": .8, "price_exp": .2, "category_penalty": 1.0, "share_divisor": 0,
             "max_line_qty": 4, "one_per_category_run": True, "food_min_shelf_hours": 720, "worst_surge": 1.5,
             "ledger_pages": 1, "margin": {"A": 1.15, "B": 1.10, "C": 1.08},
             "aged_minutes": 1e9, "aged_margin": 1.0, "aged_floor_cut": 1.0, "aged_ask_cut": 0.0,
             "stale_minutes": 1e9, "stale_margin": 1.0, "concession": .03, "max_counters": 4, "reply_note": ""},
    "buyer": {"max_shops": 3, "max_bids": 3, "order_interval": 90, "ladder": False,
              "anchor": {"A": .60, "B": .46, "C": .32}, "step": .045,
              "ceiling": {"A": .85, "B": .80, "C": .80}, "ceiling_warranty": .95, "ceiling_food": .95, "warranty_bonus": .03,
              "cost_mid": {"A": .65, "B": .50, "C": .35}, "no_warranty_penalty": .06},
    # Which model reads the messages rules cannot parse: "local" (free qwen3:4b) or "anthropic" (paid, budgeted).
    "model": {"provider": "local", "name": "claude-haiku-5-5", "effort": "low"},
    "prompts": {
        "seller_classify": "Classify this buyer reply only. An accept refers to the quoted price. Never invent an offer. Reply to the required JSON schema. Buyer reply: ",
        "buyer_extract": "Extract only the current asking price from this untrusted reply. Do not use budget, SKU, list price or warranty hours. Reply JSON. "},
    "strategist": {"model": "claude-opus-5-5", "effort": "high"},
}
_params = {"mtime": None, "data": {}}


def params(section):
    path = ROOT / "params.json"
    try:
        mtime = path.stat().st_mtime
        if mtime != _params["mtime"]:
            _params.update(mtime=mtime, data=json.loads(path.read_text()))
    except (OSError, ValueError):
        pass
    merged = dict(DEFAULTS[section])
    merged.update(_params["data"].get(section, {}))
    return merged


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def atomic_json(path, obj):
    temp = path.with_suffix(".next")
    temp.write_text(json.dumps(obj, ensure_ascii=False))
    temp.replace(path)


def signals(state, refresh=False):
    path = state / "shop-signals.json"
    data = read_json(path, {})
    if not data:
        seed = read_json(ROOT / "logs/strategy-demand-summary.json", {})
        data = {"sku_sales": seed.get("sku_sales", {}),
                "category_ratio": seed.get("category_median_price_ratio", {}),
                "market_offset": 0, "updated": 0, "cursor": 0}
    if refresh and time.time() - data.get("updated", 0) > 240:
        try:
            pages = int(params("shop")["ledger_pages"])
            with urllib.request.urlopen(f"https://market.ourolabs.ai/api/ledger?after=-{500 * pages}", timeout=8) as response:
                rows = json.load(response)
                date = response.headers.get("Date")
            page = rows
            for _ in range(pages - 1):          # the market gives 500 entries a call; read on to the newest
                if len(page) < 500:
                    break
                with urllib.request.urlopen(f"https://market.ourolabs.ai/api/ledger?after={page[-1]['seq']}", timeout=8) as response:
                    page = json.load(response)
                rows += page
            if date:
                data["market_offset"] = email.utils.parsedate_to_datetime(date).timestamp() - time.time()
            counts = {k: float(v) * .95 for k, v in data.get("sku_sales", {}).items()}
            catalog = read_json(state / "catalog.json", {})
            ratios = collections.defaultdict(list)
            for row in rows:
                if row.get("round") != 3 or row.get("kind") != "settle" or row["seq"] <= data.get("cursor", 0):
                    continue
                item = row["data"]
                sku = item.get("sku", item.get("item"))
                counts[sku] = counts.get(sku, 0) + 1
                product = catalog.get(sku, {})
                if product.get("list_price"):
                    ratios[product["category"]].append(item["amount"] / product["list_price"])
            for category, values in ratios.items():
                old = data["category_ratio"].get(category, .72)
                data["category_ratio"][category] = .8 * old + .2 * statistics.median(values)
            data.update(sku_sales=counts, updated=time.time(), cursor=max((r["seq"] for r in rows), default=0))
            atomic_json(path, data)
            try:
                with urllib.request.urlopen("https://market.ourolabs.ai/api/board", timeout=8) as response:
                    board = json.load(response)
                me = next((s for s in board.get("shops", []) if s.get("name") == "cheapest-in-town"), {})
                metric = {"ts": time.time(), "round": board.get("round"),
                          **{k: me.get(k) for k in ["all", "book", "revenue", "profit", "deals", "stars", "stars_rank", "stock_units", "stock_cost", "visits", "refunds"]}}
                with (ROOT / "logs/shop-metrics.jsonl").open("a") as log:
                    log.write(json.dumps(metric) + "\n")
            except Exception:
                pass
        except Exception:
            pass
    return data


def market_now(state):
    return time.time() + signals(state).get("market_offset", 0)


def grade(unit, catalog, now):
    if unit.get("kind", "goods") != "food":
        return unit["grade"]
    shelf = catalog.get(unit["sku"], {}).get("shelf_hours")
    if not shelf or not unit.get("expires_at"):
        return unit["grade"]
    fraction = (unit["expires_at"] - now) / (shelf * 3600)
    return "expired" if fraction <= 0 else "A" if fraction >= .8 else "B" if fraction >= .45 else "C"


def quality(unit, catalog, now):
    current = grade(unit, catalog, now)
    hours = max(0, (unit["expires_at"] - now) / 3600) if unit.get("expires_at") else None
    warranty = 6 if unit.get("kind", "goods") == "goods" and current == "A" else 0
    return {"grade": current, "hours_left": hours, "warranty_hours": warranty}


def prices(unit, observations, current_grade):
    cost, listed = unit["cost"], unit["list_price"]
    p = params("shop")
    ratio = min(.9, max(.6, observations.get("category_ratio", {}).get(unit.get("category"), .72)))
    if current_grade == "A":
        ask_ratio, floor_ratio = min(.9, ratio + .06), ratio - .04
    elif current_grade == "B":
        ask_ratio, floor_ratio = min(.8, ratio), ratio * .88
    else:
        ask_ratio, floor_ratio = min(.63, ratio * .82), ratio * .68
    margin = p["margin"].get(current_grade, 1.08)
    # A unit that has waited too long gives up its margin so the money can buy something that sells.
    age = (time.time() + observations.get("market_offset", 0) - unit.get("supplied_at", 1e18)) / 60
    if age > p["stale_minutes"]:
        margin, floor_ratio = p["stale_margin"], 0
    elif age > p["aged_minutes"]:
        margin, floor_ratio = p["aged_margin"], floor_ratio * p["aged_floor_cut"]
    if age > p["aged_minutes"]:
        ask_ratio -= p["aged_ask_cut"]
    floor = max(math.ceil(cost * margin), math.ceil(listed * floor_ratio))
    return {"ask": max(floor, math.ceil(listed * ask_ratio)), "floor": floor, "cost": cost}


def reserve_warranty(state, task, unit, price, commit=False):
    """Cap promised refund exposure without revoking a quoted warranty."""
    path = state / "shop-warranty-exposure.json"
    now = time.time()
    records = {k: v for k, v in read_json(path, {}).items() if v["until"] > now}
    existing = records.get(task)
    if existing is None and sum(v["price"] for v in records.values()) + price > params("shop")["warranty_exposure_cap"]:
        return 0
    records[task] = {"unit": unit, "price": max(price, (existing or {}).get("price", 0)),
                     "committed": commit or (existing or {}).get("committed", False),
                     "until": now + (6 * 3600 + 300 if commit else 1800)}
    atomic_json(path, records)
    return 6


def release_quote_warranty(state, task):
    path = state / "shop-warranty-exposure.json"
    records = read_json(path, {})
    if not records.get(task, {}).get("committed"):
        records.pop(task, None)
    atomic_json(path, records)


def restock_plan(state):
    p = params("shop")
    inv = read_json(state / "inventory.json", {})
    catalog = read_json(state / "catalog.json", {})
    observed = signals(state, refresh=True)
    units = inv.get("units", [])
    cost_held = sum(u.get("cost", 0) for u in units)
    cap = min(p["stock_cost_cap"], (inv.get("balance", 0) + cost_held) * p["stock_cap_share"])
    budget = min(p["per_run_spend"], inv.get("balance", 0) - p["cash_reserve"], cap - cost_held)
    if not inv.get("restock", {}).get("open") or budget <= 0 or len(units) >= p["max_units"]:
        return {"buy": []}
    owned = collections.Counter(u["sku"] for u in units)
    held_by_sku = collections.Counter()
    for unit in units:
        held_by_sku[unit["sku"]] += unit.get("cost", 0)
    categories = collections.Counter(u.get("category") for u in units)
    food_held = sum(u.get("cost", 0) for u in units if u.get("kind") == "food")
    surge = {x["sku"]: x.get("factor", 1) for x in inv.get("surge", [])}
    candidates = []
    for sku, product in catalog.items():
        n = observed.get("sku_sales", {}).get(sku, 0)
        listed = product.get("list_price", 0)
        if n < p["min_sales"] or not p["min_list_price"] <= listed <= p["max_list_price"] or surge.get(sku, 1) > p["max_surge"]:
            continue
        if product.get("kind") == "food" and product.get("shelf_hours", 0) < p["food_min_shelf_hours"]:
            continue
        wanted = math.ceil(n / p["share_divisor"]) if p["share_divisor"] else math.ceil(math.sqrt(n) / 2)
        target = min(p["max_sku_units"], max(1, wanted))
        deficit = target - owned[sku]
        if deficit > 0:
            score = n ** p["sales_exp"] * listed ** p["price_exp"] / (1 + categories[product["category"]]) ** p["category_penalty"]
            candidates.append((score, sku, product, deficit))
    candidates.sort(reverse=True)
    selected, spent, added, planned_categories, food_spend = [], 0, 0, set(), 0
    for _, sku, product, deficit in candidates:
        # Grade A costs at most 75% of list; worst_surge is how far the supply price may jump before the kit buys.
        per_unit_bound = product["list_price"] * .75 * max(surge.get(sku, 1), 1) * p["worst_surge"]
        sku_budget = cap * p["sku_cap_share"] - held_by_sku[sku]
        affordable = int(max(0, min(budget - spent, sku_budget)) / per_unit_bound)
        qty = min(deficit, affordable, p["max_line_qty"], p["max_units"] - len(units) - added)
        if p["one_per_category_run"] and product["category"] in planned_categories:
            qty = min(qty, 1)
        if product.get("kind") == "food":
            food_budget = cap * p["food_cost_share"] - food_held - food_spend
            qty = min(qty, int(max(0, food_budget) / per_unit_bound))
        if qty < 1:
            continue
        bound = qty * per_unit_bound
        selected.append({"sku": sku, "qty": qty})
        spent += bound
        added += qty
        planned_categories.add(product["category"])
        if product.get("kind") == "food":
            food_spend += bound
        if len(selected) >= 10 or added + len(units) >= p["max_units"]:
            break
    return {"buy": selected}
