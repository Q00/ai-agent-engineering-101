"""Read-only investigations from saved logs, saved public ledger rows and one saved /api/shops and /api/board.

Each section keeps its sample and limits next to its numbers. Everything here is observational: no cause is established,
and other players' buyers are not visible to us beyond what the public ledger shows.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import glob
import json
from pathlib import Path
from statistics import median

from buyer_funnel import load_runs, stamp
from rank_analysis import spearman

ROOT = Path(__file__).resolve().parent
LOGIN = "kcwarni"


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def ledger_rows(root):
    """Union of public ledger rows saved in market-intel snapshots, by sequence number."""
    seen = {}
    for path in sorted(glob.glob(str(Path(root) / "logs/market-intel/*.json"))):
        data = read(path, {})
        for row in data.get("rows") or []:
            if row.get("round") == 3 and row.get("seq") is not None:
                seen[row["seq"]] = row
    return list(seen.values())


def seller_refusal_text(root, record):
    directory = Path(root) / "logs/buyer" / Path(str(record.get("buyer_log_dir", "")).replace("\\", "/")).name
    state, text = None, ""
    for path in sorted(directory.glob("*.response.txt")):
        reply = read(path)
        result = reply.get("result") if isinstance(reply, dict) else None
        task = result.get("task", result) if isinstance(result, dict) else None
        if isinstance(task, dict) and isinstance(task.get("status"), dict):
            state = task["status"].get("state")
            message = task["status"].get("message") or {}
            text = " ".join(p.get("text", "") for p in message.get("parts", []) if isinstance(p, dict))
            if state:
                break
    return state, text


def open_rejections(root, since, rows):
    """Why a shop we selected refused at the first message, and what we could have known before asking."""
    runs = []
    for name, record in load_runs(root, since):
        if record.get("outcome") == "research_skipped" or not record.get("buyer_log_dir"):
            continue
        at = datetime.strptime(name, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp()
        state, text = seller_refusal_text(root, record)
        runs.append({"at": at, "shop": record.get("shop_name"), "url": record.get("url_at_time"), "sku": record.get("item"),
                     "state": state, "text": text, "name": name, "order": record.get("order")})
    settled = defaultdict(list)
    for row in rows:
        if row.get("kind") == "settle":
            data = row.get("data") or {}
            settled[(data.get("shop"), data.get("item"))].append(row.get("ts"))
    rejected = [r for r in runs if r["state"] == "TASK_STATE_REJECTED"]
    wording, by_shop, recent_sale = Counter(), Counter(), Counter()
    earlier_refusal = {minutes: 0 for minutes in (5, 15, 60)}
    earlier_refusal_false_skip = {minutes: 0 for minutes in (5, 15, 60)}
    for row in rejected:
        text = row["text"].lower()
        wording["no_stock" if "stock" in text else "no_message" if not text else "other"] += 1
        by_shop[row["shop"]] += 1
        sales = settled.get((row["url"], row["sku"]), [])
        recent_sale["sold_within_10_min_before"] += any(0 <= row["at"] - t <= 600 for t in sales)
        recent_sale["sold_within_60_min_before"] += any(0 <= row["at"] - t <= 3600 for t in sales)
        recent_sale["no_sale_seen_in_saved_rows"] += not sales
    for row in runs:
        earlier = [r for r in rejected if r["shop"] == row["shop"] and r["sku"] == row["sku"] and r["at"] < row["at"]]
        for minutes in earlier_refusal:
            if any(row["at"] - r["at"] <= minutes * 60 for r in earlier):
                if row["state"] == "TASK_STATE_REJECTED":
                    earlier_refusal[minutes] += 1
                else:
                    earlier_refusal_false_skip[minutes] += 1
    attempts_by_shop = Counter(r["shop"] for r in runs)
    catalog = read(Path(root) / ".market/catalog.json", {})
    by_kind = defaultdict(lambda: [0, 0])
    for r in runs:
        kind = (catalog.get(r["sku"]) or {}).get("kind", "unknown")
        by_kind[kind][0] += 1
        by_kind[kind][1] += r["state"] == "TASK_STATE_REJECTED"
    alternatives = Counter()
    for row in rejected:
        data = read(Path(root) / "logs/market-buyer" / next(iter(glob.glob(str(Path(root) / "logs/market-buyer" / f"{row['name']}*.json"))), "x"), {}) or {}
        evidence = data.get("selection_evidence") or []
        others = [e for e in evidence if e.get("support_eligible") and not (e.get("shop") == row["shop"] and e.get("sku") == row["sku"])]
        other_shops = {e.get("shop") for e in others if e.get("shop") != row["shop"]}
        alternatives["with_other_eligible_shop" if other_shops else "with_only_this_shop"] += 1
    return {"window_since": since, "negotiations": len(runs), "rejected_at_open": len(rejected), "wording": dict(wording),
            "top_shops": [{"shop": name, "refused": n, "attempts": attempts_by_shop[name]} for name, n in by_shop.most_common(8)],
            "by_kind": {k: {"attempts": v[0], "refused": v[1], "rate": round(v[1] / v[0], 3)} for k, v in by_kind.items()},
            "against_saved_public_sales": dict(recent_sale),
            "skip_after_refusal_same_shop_sku": {f"{m}_min": {"refusals_avoided": earlier_refusal[m], "quotes_lost": earlier_refusal_false_skip[m]}
                                                 for m in earlier_refusal},
            "alternatives_at_selection": dict(alternatives),
            "stock_detail_missing": "Offer quantity at selection is not logged, so stale versus promised stock cannot be told apart per case."}


def _own_units(root):
    units = {}
    for path in glob.glob(str(Path(root) / ".market/signed-offers/*.json")):
        offer = read(path, {})
        if offer.get("unit"):
            units[offer["unit"]] = offer
    return units


def quality(root, rows):
    """Breakage by SKU, grade, cost share and warranty, for our shop and the whole market, from public ledger rows."""
    catalog = read(Path(root) / ".market/catalog.json", {})
    settlements = read(Path(root) / ".market/local-settlements.json", {}).get("records") or {}
    records = list(settlements.values()) if isinstance(settlements, dict) else settlements
    signed = _own_units(root)
    own_units = {r.get("unit") for r in records} | set(signed)
    settle_rows = [r for r in rows if r.get("kind") == "settle"]
    broken_rows = [r for r in rows if r.get("kind") == "broken"]
    broken_units = {(r.get("data") or {}).get("unit") for r in broken_rows}

    def goods(item):
        return (catalog.get(item) or {}).get("kind") == "goods"
    market = defaultdict(lambda: [0, 0])
    for row in settle_rows:
        data = row.get("data") or {}
        if not goods(data.get("item")):
            continue
        bucket = market[f"grade_{data.get('grade')}"]
        bucket[0] += 1
        bucket[1] += data.get("unit") in broken_units
        warranted = market["warranted" if data.get("warranty_hours") else "no_warranty"]
        warranted[0] += 1
        warranted[1] += data.get("unit") in broken_units
    sku_rows = defaultdict(lambda: [0, 0])
    for row in settle_rows:
        data = row.get("data") or {}
        if goods(data.get("item")):
            sku_rows[data.get("item")][0] += 1
            sku_rows[data.get("item")][1] += data.get("unit") in broken_units
    settled_grade = {r.get("unit"): r.get("grade") for r in records}
    own_goods = []
    for unit, offer in signed.items():
        if goods(offer.get("sku")):
            own_goods.append({"unit": unit, "sku": offer.get("sku"), "grade": settled_grade.get(unit) or offer.get("grade"), "warranty": offer.get("warranty_hours"),
                              "cost": offer.get("cost"), "price": offer.get("price"), "broken": unit in broken_units,
                              "list": (catalog.get(offer.get("sku")) or {}).get("list_price")})
    def own_split(key):
        table = defaultdict(lambda: [0, 0])
        for deal in own_goods:
            table[str(key(deal))][0] += 1
            table[str(key(deal))][1] += deal["broken"]
        return {k: {"deals": v[0], "broken_seen": v[1], "rate": round(v[1] / v[0], 3)} for k, v in sorted(table.items(), key=lambda x: -x[1][0])}
    cost_band = lambda d: "unknown" if not d["cost"] or not d["list"] else f"cost/list<{round(int(d['cost'] / d['list'] * 5 + 1) / 5, 1)}"
    by_sku = own_split(lambda d: d["sku"])
    top_sku = {k: dict(v, market_rate=round(sku_rows[k][1] / sku_rows[k][0], 3) if sku_rows[k][0] else None, market_deals=sku_rows[k][0])
               for k, v in list(by_sku.items())[:12]}
    reviews = read(Path(root) / ".market/seller-quality.json", {})
    low = sorted(((sku, row) for sku, row in (reviews.get("skus") or {}).items() if row.get("count")), key=lambda x: (x[1].get("mean_stars", 5), -x[1]["count"]))[:10]
    stars = [row.get("stars") for row in reviews.get("reviews") or [] if isinstance(row, dict) and row.get("stars")]
    review_rows = [row for row in reviews.get("reviews") or [] if isinstance(row, dict)]
    review_by_grade = defaultdict(list)
    for row in review_rows:
        offer = signed.get(row.get("unit")) or {}
        if row.get("stars"):
            review_by_grade[str(offer.get("grade") or row.get("grade"))].append(row["stars"])
    grade_rate = {g: market[f"grade_{g}"][1] / market[f"grade_{g}"][0] for g in "ABC" if f"grade_{g}" in market}
    known = [d for d in own_goods if d["grade"] in grade_rate]
    expected = sum(grade_rate[d["grade"]] for d in known)
    board = read(sorted(glob.glob(str(Path(root) / "logs/rank-analysis/board-*.json")))[-1], {})
    own_row = next((r for r in board.get("shops", []) if r.get("name") == LOGIN), {})
    shops_file = sorted(glob.glob(str(Path(root) / "logs/rank-analysis/shops-*.json")))
    registry = read(shops_file[-1], {}) if shops_file else {}
    registry = registry.get("shops", registry) if isinstance(registry, dict) else registry
    own_shop = next((x for x in registry if x.get("name") == LOGIN), {})
    public_reviews = own_shop.get("reviews") or []
    themes = Counter()
    for row in public_reviews:
        if (row.get("stars") or 5) <= 3:
            text = str(row.get("review", "")).lower()
            themes["broke_or_defect" if any(w in text for w in ("broke", "broken", "fault", "defect", "고장", "파손")) else
                   "price" if any(w in text for w in ("price", "expensive", "가격", "비싸")) else
                   "grade_or_condition" if any(w in text for w in ("grade", "condition", "등급", "상태")) else
                   "no_deal_or_other"] += 1
    return {"goods_only_note": "Official broken/deals mixes food, which cannot break. We sell only goods, so compare against goods rates here.",
            "own_official": {"deals": own_row.get("deals"), "broken": own_row.get("broken"), "stars": own_row.get("stars"), "stars_rank": own_row.get("stars_rank")},
            "expected_broken_at_market_rates_for_our_grade_mix": {"deals_with_known_grade": len(known), "expected_broken": round(expected, 1),
                                                                 "scaled_to_official_deals": round(expected / max(len(known), 1) * (own_row.get("deals") or 0), 1)},
            "own_public_reviews": {"count": len(public_reviews), "stars": dict(sorted(Counter(r.get("stars") for r in public_reviews).items(), key=lambda x: str(x[0]))),
                                   "low_star_themes": dict(themes)},
            "sample_limit": "Public rows are the union of saved snapshots, so older break events can be missing; our board count stays the official figure.",
            "own_goods_deals_signed": len(own_goods), "own_broken_seen": sum(d["broken"] for d in own_goods),
            "own_units_in_public_broken_rows": len(own_units & broken_units),
            "own_by_grade": own_split(lambda d: d["grade"]), "own_by_warranty": own_split(lambda d: d["warranty"]),
            "own_by_cost_share": own_split(cost_band), "own_top_skus_with_market_rate": top_sku,
            "market_goods_rate": {k: {"deals": v[0], "broken": v[1], "rate": round(v[1] / v[0], 3)} for k, v in sorted(market.items())},
            "lowest_rated_own_skus": [{"sku": k, **{x: v.get(x) for x in ("count", "mean_stars", "low_reviews")}} for k, v in low],
            "reviews_linked": len(stars), "review_star_counts": dict(sorted(Counter(stars).items())),
            "review_stars_by_signed_grade": {k: {"n": len(v), "mean": round(sum(v) / len(v), 2)} for k, v in review_by_grade.items()}}


def visits(root, rows, shops_path, board_path):
    """What official shop visits go along with, using one public stock snapshot and saved public sales."""
    registry = read(shops_path, []) or []
    registry = registry.get("shops", registry) if isinstance(registry, dict) else registry
    board = read(board_path, {})
    catalog = read(Path(root) / ".market/catalog.json", {})
    board_shops = {r.get("name"): r for r in board.get("shops", []) if r.get("all") is not None and r.get("deals")}
    table = []
    for shop in registry:
        row = board_shops.get(shop.get("name"))
        if not row:
            continue
        stock = shop.get("stock") or {}
        kinds = Counter((catalog.get(sku) or {}).get("kind", "unknown") for sku in stock)
        table.append({"name": shop["name"], "visits": row.get("visits"), "deals": row.get("deals"), "skus": len(stock), "units": sum(stock.values()),
                      "food_skus": kinds["food"], "goods_skus": kinds["goods"], "categories": len({(catalog.get(s) or {}).get("category") for s in stock}),
                      "stars_rank": row.get("stars_rank"), "joined": row.get("joined")})
    settled = [r["data"] for r in rows if r.get("kind") == "settle" and r.get("data")]
    kind_counts = Counter((catalog.get(d.get("item")) or {}).get("kind", "unknown") for d in settled)
    url_names = {shop.get("url"): shop.get("name") for shop in registry}
    per_shop_kind = defaultdict(Counter)
    for data in settled:
        per_shop_kind[url_names.get(data.get("shop"))][(catalog.get(data.get("item")) or {}).get("kind", "unknown")] += 1
    top = sorted(table, key=lambda r: -(r["visits"] or 0))
    mine = next((r for r in table if r["name"] == LOGIN), None)
    own_rows = [d for d in settled if d.get("buyer") != LOGIN and url_names.get(d.get("shop")) == LOGIN]
    category_demand = Counter((catalog.get(d.get("item")) or {}).get("category", "unknown") for d in settled)
    own_stock = next((s.get("stock") or {} for s in registry if s.get("name") == LOGIN), {})
    own_categories = {(catalog.get(sku) or {}).get("category") for sku in own_stock}
    return {"definition": "Neither the market contract nor the kit defines how a visit is counted; see the note.",
            "shops_joined_with_stock": len(table), "own": mine, "top_8": top[:8], "median_skus": median(r["skus"] for r in table) if table else None,
            "median_units": median(r["units"] for r in table) if table else None,
            "rank_correlation_with_visits": {key: spearman(table, lambda r: r["visits"], lambda r, k=key: r[k])
                                             for key in ("skus", "units", "food_skus", "goods_skus", "categories", "stars_rank")},
            "market_settlements_by_kind": dict(kind_counts), "saved_settlements": len(settled),
            "top_shop_settlements_by_kind": {r["name"]: dict(per_shop_kind.get(r["name"], {})) for r in top[:8]},
            "own_settlements_by_kind_in_saved_rows": dict(per_shop_kind.get(LOGIN, {})),
            "top_categories_by_settlements": category_demand.most_common(10),
            "own_stock_category_share_of_settlements": round(sum(n for c, n in category_demand.items() if c in own_categories) / max(len(settled), 1), 3),
            "note": "The stock snapshot is one moment; visits accumulate over days, and other buyers' selection code is not public."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2026-11-04T09:39:00Z")
    parser.add_argument("--shops", type=Path, default=None)
    parser.add_argument("--board", type=Path, default=None)
    args = parser.parse_args()
    shops = args.shops or sorted(glob.glob(str(ROOT / "logs/rank-analysis/shops-*.json")))[-1]
    board = args.board or sorted(glob.glob(str(ROOT / "logs/rank-analysis/board-*.json")))[-1]
    rows = ledger_rows(ROOT)
    print(json.dumps({"saved_public_rows": len(rows), "open_rejections": open_rejections(ROOT, stamp(args.since), rows),
                      "quality": quality(ROOT, rows), "visits": visits(ROOT, rows, shops, board)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
