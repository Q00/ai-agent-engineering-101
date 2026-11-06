"""Buyer posts written by code from the public ledger we already saved: what a SKU or a category sold for. No model and no extra ledger call.

Every figure comes from settlement rows with the sample size and ledger refs next to it. It names no shop and leaves out our own shop's sales. It says nothing of our floors or supply,
and safe() is the last check before a post is queued.
"""

from pathlib import Path
import statistics

from forum_policy import SKU_REPEAT, enqueue, posting_limits
from forum_posts import NAME_LIMIT, checked_at, safe
from low_star_monitor import clean
from market_state import read
from purchase_monitor import settlement_price

KIND = "buyer_market_note"
WINDOW_HOURS = 6
MIN_SKU_SAMPLES, MIN_CATEGORY_SAMPLES = 5, 20
MAX_REFS = 5
MAX_TRIES = 40


def sales(rows, catalog, own_units, own_urls, now):
    """Recent goods and food sales other shops made, as (seq, sku, price, discount, grade, category)."""
    found = {}
    for row in rows:
        data = row.get("data") or {}
        product = catalog.get(data.get("item")) or {}
        price = settlement_price(data)
        value = product.get("list_price")
        if (row.get("round") != 3 or row.get("kind") != "settle" or type(row.get("seq")) is not int or not 0 <= now-row.get("ts", -1) <= WINDOW_HOURS*3600 or price is None or not value
                or data.get("unit") in own_units or data.get("shop") in own_urls):
            continue
        found[row["seq"]] = (row["seq"], data["item"], price, 1-price/value, data.get("grade"), product.get("category") or "")
    return list(found.values())


def grades(group):
    return ", ".join(f"{g} {sum(s[4] == g for s in group)}" for g in sorted({s[4] for s in group if s[4]}))


def refs(group):
    return sorted((s[0] for s in group), reverse=True)[:MAX_REFS]


def sku_note(sku, group, product, now):
    prices = [s[2] for s in group]
    middle = statistics.median(prices)
    return (f"Buyer market note {checked_at(now)}: {clean(product.get('name') or sku, NAME_LIMIT)} (SKU {sku}), list price {product['list_price']:.0f}. Last {WINDOW_HOURS} h public settlements n={len(group)}: "
            f"median paid {middle:.0f} ({(1-middle/product['list_price'])*100:.1f}% off list), range {min(prices):.0f} to {max(prices):.0f}. Grades: {grades(group)}. From a stored sample of the ledger, not all of it; later breakage not counted.")


def category_note(category, group, now):
    middle = statistics.median(s[3] for s in group)
    return (f"Buyer market note {checked_at(now)}: category {clean(category.split('>')[-1], NAME_LIMIT)}. Last {WINDOW_HOURS} h public settlements n={len(group)} across {len({s[1] for s in group})} SKUs: "
            f"median {middle*100:.1f}% off list price. Grades: {grades(group)}. From a stored sample of the ledger, not all of it; later breakage not counted.")


def recently_posted(actions, field, value, now):
    return any(a.get(field) == value and a.get("kind") in (KIND, "buyer_report") and 0 <= now-a.get("queued_at", -1) < SKU_REPEAT for a in actions.values())


def candidates(rows, catalog, own_units, own_urls, actions, now):
    """Most sampled first, turn about between SKUs and categories; nothing posted in the last SKU_REPEAT again. Yields (message, key, kind, metadata)."""
    picked = sales(rows, catalog, own_units, own_urls, now)
    by_sku, by_category = {}, {}
    for sale in picked:
        by_sku.setdefault(sale[1], []).append(sale)
        if sale[5]:
            by_category.setdefault(sale[5], []).append(sale)
    day = int(now//SKU_REPEAT)
    skus = [({"post": sku_note(sku, group, catalog[sku], now), "refs": refs(group), "as": "buyer"}, f"market-sku:{sku}:{day}", KIND, {"sku": sku, "samples": len(group)})
            for sku, group in sorted(by_sku.items(), key=lambda p: (-len(p[1]), p[0])) if len(group) >= MIN_SKU_SAMPLES and not recently_posted(actions, "sku", sku, now)]
    categories = [({"post": category_note(category, group, now), "refs": refs(group), "as": "buyer"}, f"market-category:{category}:{day}", KIND, {"category": category, "samples": len(group)})
                  for category, group in sorted(by_category.items(), key=lambda p: (-len(p[1]), p[0])) if len(group) >= MIN_CATEGORY_SAMPLES and not recently_posted(actions, "category", category, now)]
    notes = [a for a in actions.values() if a.get("kind") == KIND]
    last = max(notes, key=lambda a: a.get("queued_at", 0), default={})
    while skus or categories:
        for queue in (categories, skus) if last.get("sku") else (skus, categories):
            if queue:
                yield queue.pop(0)


def own_shop(md, snapshot):
    """Unit ids we sold and our shop's current URL, so our sales stay out of buyer posts."""
    local = read(Path(md) / "local-settlements.json", {}).get("records") or {}
    units = {r.get("unit") for r in (local.values() if isinstance(local, dict) else local) if r.get("unit")}
    shop = ((snapshot.get("competition") or {}).get("own") or {}).get("shops") or {}
    url = (shop.get("card") or {}).get("url")
    return units, {url} if url else set()


def run(md, now):
    """Queue at most one note when a post is due. Failing here never touches the shop's posts."""
    md = Path(md)
    limits = posting_limits(md, now)
    if not limits.get("buyer_posts_enabled"):
        return "off"
    state = read(md / "forum-policy.json", {})
    if now-state.get("last_at", 0) < limits["buyer_min_interval_seconds"]:
        return "rate_limited"
    snapshot = read(md / "market-intel.json", {})
    if snapshot.get("round") != 3 or not 0 <= now-snapshot.get("observed_at", 0) <= 3600:
        return "stale_market_intel"
    units, urls = own_shop(md, snapshot)
    actions = read(md / "forum-actions.json", {})
    for tries, (message, key, kind, metadata) in enumerate(candidates(snapshot.get("rows", []), read(md / "catalog.json", {}), units, urls, actions, now)):
        if tries >= MAX_TRIES:
            break
        if not safe(message["post"]):
            continue
        outcome = enqueue(md, message, key, kind, metadata, now)
        if outcome != "already_queued":
            return outcome
    return "no_candidate"
