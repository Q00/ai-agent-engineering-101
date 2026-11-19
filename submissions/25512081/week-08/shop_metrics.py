"""Read-only market diagnostics; append evidence and keep a resumable ledger cursor.

python shop_metrics.py --dir . --pages 120

No model calls or market mutations. The first run covers at most pages*500 recent
entries; later runs continue from the cursor. Observed reviewer means are NOT the
market's lifetime stars_rank. Raw observations and snapshots are append-only logs.
"""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import math
from pathlib import Path
import re
import statistics
import time
import urllib.request

MARKET = "https://market.ourolabs.ai"


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def get(path):
    with urllib.request.urlopen(MARKET + path, timeout=20) as response:
        return json.load(response)


def save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def append(path, rows):
    with path.open("a", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


def archive(path):
    out = {}
    if path.exists():
        for line in path.open(encoding="utf-8"):
            try:
                row = json.loads(line)
                out[row["seq"]] = row
            except (ValueError, KeyError):
                continue
    return out


def owner(agent):
    value = agent.get("owner") or {}
    return value.get("login") if isinstance(value, dict) else value


def groups(rows, key):
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return [{"group": label, "n": len(rs),
             "mean_stars": round(statistics.mean(r["stars"] for r in rs), 3),
             "low_reviews": sum(r["stars"] <= 3 for r in rs)}
            for label, rs in sorted(grouped.items())]


def summarize(events, catalog, buyer_owners):
    settles = {r["data"].get("order"): r for r in events.values() if r["kind"] == "settle"}
    reviews = []
    for row in events.values():
        d = row["data"]
        if row["kind"] != "result" or not d.get("verified") or type(d.get("stars")) not in (int, float):
            continue
        s = settles.get(d.get("order"), {}).get("data", {})
        receipt = d.get("receipt") or {}
        sku = s.get("item") or receipt.get("item")
        product = catalog.get(sku, {})
        price = s.get("amount", receipt.get("price"))
        lp = product.get("list_price")
        ratio = price / lp if lp and isinstance(price, (int, float)) else None
        text = d.get("review") or ""
        reviews.append({"seq": row["seq"], "ts": row["ts"], "buyer": d.get("buyer"),
                        "account": buyer_owners.get(d.get("buyer")) or d.get("buyer") or "unknown",
                        "stars": d["stars"], "sku": sku, "category": product.get("category", "unknown"),
                        "kind": product.get("kind", "unknown"), "grade": s.get("grade", "unknown"),
                        "price_ratio": ratio, "warranty_hours": s.get("warranty_hours") or 0,
                        "short_shelf_mentioned": bool(re.search(
                            r"(?i)(only .{0,50}(?:needed|need)|shelf.{0,30}(?:short|insufficient)|유통기한.{0,15}부족)", text)),
                        "review": text[:600]})
    reviewers = groups(reviews, lambda r: r["account"])
    return {"matched_settlements": sum(r["grade"] != "unknown" for r in reviews),
            "review_count": len(reviews),
            "observed_account_mean": round(statistics.mean(r["mean_stars"] for r in reviewers), 4) if reviewers else None,
            "reviewers": sorted(reviewers, key=lambda r: (r["mean_stars"], -r["n"])),
            "by_price_ratio": groups(reviews, lambda r: "unknown" if r["price_ratio"] is None else
                                     f"{math.floor(r['price_ratio'] * 20) * 5:03d}-{(math.floor(r['price_ratio'] * 20) + 1) * 5:03d}%"),
            "by_condition": groups(reviews, lambda r: f"{r['kind']}/{r['grade']}/warranty={r['warranty_hours']:g}h"),
            "short_shelf_reviews": [r for r in reviews if r["short_shelf_mentioned"]],
            "low_review_examples": sorted((r for r in reviews if r["stars"] <= 3), key=lambda r: -r["ts"])[:30]}


def stock_report(inv, catalog, state, now):
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    held = collections.defaultdict(list)
    for unit in units:
        held[unit["sku"]].append(unit)
    items = []
    for sku, us in held.items():
        px = state.get("sku_px", {}).get(sku, [])
        lp = catalog.get(sku, {}).get("list_price") or us[0].get("list_price", 0)
        sale = statistics.median(px) * lp if len(px) >= 3 else None
        items.append({"sku": sku, "name": catalog.get(sku, {}).get("name", sku), "units": len(us),
                      "cost": sum(u["cost"] for u in us), "market_median": sale,
                      "above_mixed_grade_market": sum(u["cost"] * 1.05 > sale for u in us) if sale else None,
                      "over_24h": sum(now - u.get("supplied_at", now) > 86400 for u in us)})
    # A diagnostic alternative list only: no purchases, prices or live selection change.
    candidates = collections.defaultdict(list)
    recent = collections.defaultdict(list)
    for r in state.get("recent", []):
        if now - r.get("ts", 0) < 3600:
            recent[r["sku"]].append(r)
    for sku, rs in recent.items():
        product = catalog.get(sku, {})
        lp = product.get("list_price")
        if not lp:
            continue
        price = statistics.median(r["amount"] for r in rs)
        seller_count = len({r["shop"] for r in rs}) or 1
        factor = next((s.get("factor", 1) for s in inv.get("surge", []) if s.get("sku") == sku), 1)
        estimate = lp * 0.6 * factor
        candidates[product.get("category", "unknown")].append({
            "sku": sku, "name": product.get("name"), "sample_sales": len(rs),
            "seller_count": seller_count, "median_price": price,
            "estimated_cost": round(estimate), "estimated_margin": round(price - estimate),
            "margin_per_seller_sample": round((price - estimate) * len(rs) / seller_count),
            "note": "mixed grades; 0.6*list*surge cost proxy; excludes refunds/storage; not a purchase recommendation"})
    return {"balance": inv.get("balance"), "stock_cost": sum(u["cost"] for u in units),
            "units": len(units), "by_sku": sorted(items, key=lambda r: -r["cost"]),
            "shadow_alternatives": {cat: sorted(rs, key=lambda r: -r["margin_per_seller_sample"])[:3]
                                    for cat, rs in candidates.items()}}


def observation_windows(path, board, events, catalog, owners, now):
    snapshots = []
    if path.exists():
        for line in path.open(encoding="utf-8"):
            try:
                snapshots.append(json.loads(line))
            except ValueError:
                continue
    windows = {}
    for label, seconds in (("40m", 2400), ("2h", 7200)):
        previous = [s for s in snapshots if s.get("ts", now) <= now - seconds]
        base = max(previous, key=lambda s: s["ts"]) if previous else None
        start = base["ts"] if base else now - seconds
        recent = {seq: r for seq, r in events.items() if r["ts"] >= start}
        rv = summarize(recent, catalog, owners)
        windows[label] = {"baseline_ready": base is not None,
                          "minutes": round((now - start) / 60, 1),
                          "book_change": board.get("book", 0) - base.get("book", 0) if base else None,
                          "revenue_change": board.get("revenue", 0) - base.get("revenue", 0) if base else None,
                          "deals_change": board.get("deals", 0) - base.get("deals", 0) if base else None,
                          "reviews": rv["review_count"], "observed_account_mean": rv["observed_account_mean"],
                          "short_shelf_complaints": len(rv["short_shelf_reviews"])}
    return windows


def run(folder, pages, name):
    md, logs = folder / ".market", folder / "logs"
    md.mkdir(exist_ok=True)
    logs.mkdir(exist_ok=True)
    with (md / "shop-metrics.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"skipped": "another collector is running"}
        board = get("/api/board")
        shop = next(s for s in board["shops"] if s["name"] == name)
        aliases = set(load(md / "shop-metrics-cursor.json", {}).get("aliases", []))
        aliases.update((name, (shop.get("card") or {}).get("url")))
        # Recover this shop's older tunnel URLs from its own kit output.
        for path in logs.glob("market-kit*.log*"):
            if not path.is_file():
                continue
            for line in path.open(encoding="utf-8", errors="replace"):
                if "[shop] your seller" in line or f"shop registered again as {name} at " in line:
                    aliases.update(re.findall(r"https?://[^\s]+", line))
        aliases.discard(None)
        cursor = load(md / "shop-metrics-cursor.json", {})
        events = archive(logs / "shop-metrics-events.jsonl")
        newest = get("/api/ledger?after=-1")
        end = newest[-1]["seq"] if newest else 0
        start = cursor.get("seq", max(0, end - pages * 500))
        stop = min(end, start + pages * 500)
        offsets = list(range(start, stop, 500))

        def page(seq):
            try:
                return get(f"/api/ledger?after={seq}"), None
            except Exception as exc:
                return [], f"{type(exc).__name__}: {exc}"

        saved, errors, scanned, seq = [], [], 0, start
        with ThreadPoolExecutor(max_workers=4) as pool:
            for offset, (rows, error) in zip(offsets, pool.map(page, offsets)):
                if error:
                    errors.append({"after": offset, "error": error})
                    break  # never move the cursor across a failed page
                rows = [r for r in rows if r["seq"] <= stop]
                if not rows:
                    break
                for r in rows:
                    d = r.get("data") or {}
                    receipt = d.get("receipt") or {}
                    ours = d.get("shop") in aliases or (isinstance(receipt, dict) and receipt.get("shop") in aliases)
                    if r.get("round", 3) == 3 and ours and r["kind"] in ("settle", "result", "refund", "expired"):
                        if r["seq"] not in events:
                            # Only public shop facts; do not retain other buyers' signatures.
                            data = {k: v for k, v in d.items() if k not in ("sig_buyer", "checkout_hash")}
                            if isinstance(receipt, dict):
                                data["receipt"] = {k: receipt[k] for k in ("shop", "item", "price", "task") if k in receipt}
                            row = {"seq": r["seq"], "ts": r["ts"], "kind": r["kind"], "data": data}
                            saved.append(row)
                            events[r["seq"]] = row
                scanned += len(rows)
                seq = rows[-1]["seq"]
        append(logs / "shop-metrics-events.jsonl", saved)
        save(md / "shop-metrics-cursor.json", {"seq": seq, "aliases": sorted(aliases)})
        catalog = load(md / "catalog.json", {})
        if isinstance(catalog, list):
            catalog = {x["sku"]: x for x in catalog}
        owners = {a["name"]: owner(a) for a in board["buyers"]}
        now = time.time()
        report = {"ts": now, "scope": "observations since bounded bootstrap; not lifetime reviewer averages",
                  "ledger_seq": seq, "ledger_latest": end, "backlog": end - seq,
                  "scanned": scanned, "new_observations": len(saved), "errors": errors,
                  "earliest_observation_ts": min((r["ts"] for r in events.values()), default=None),
                  "board": {k: shop.get(k) for k in ("all", "book", "revenue", "stars", "stars_rank", "deals", "visits")},
                  "reviews": summarize(events, catalog, owners),
                  "inventory": stock_report(load(md / "inventory.json", {}), catalog,
                                             load(md / "restock_state.json", {}), now)}
        report["windows"] = observation_windows(logs / "shop-metrics-snapshots.jsonl", report["board"],
                                                 events, catalog, owners, now)
        save(md / "shop-metrics.json", report)
        append(logs / "shop-metrics-snapshots.jsonl", [{"ts": now, "ledger_seq": seq, **report["board"],
                                                       "observed_reviews": report["reviews"]["review_count"],
                                                       "observed_account_mean": report["reviews"]["observed_account_mean"]}])
        return {"scanned": scanned, "new": len(saved), "backlog": end - seq,
                "reviews": report["reviews"]["review_count"], "errors": errors, "board": report["board"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path("."))
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--name", default="kimauve")
    args = parser.parse_args()
    if not 1 <= args.pages <= 120:
        parser.error("--pages must be 1..120")
    print(json.dumps(run(args.dir.resolve(), args.pages, args.name), ensure_ascii=False))
