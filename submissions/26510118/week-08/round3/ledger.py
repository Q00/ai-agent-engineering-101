"""Ledger collection: the market's public ledger, read on from where the last run stopped,
kept as compact rows and summed up into market_summary.json.

    python ledger.py            (the analysis runner, analysis.py, runs it once an hour)

Code only, no model. The summary says what sells in the whole market, at what share of the
list price and in which grades, which shops' grade claims were true, and what broke. The
seller's restock reads it to pick each category's representative product; the market index
on the forum is written from it; the analysis models query the rows through the ledger tool.

Only settle and broken entries are kept: a Round 3 order entry carries no item or budget.
The first run starts from the newest 500 entries, not from the beginning of the ledger. The ledger
runs on across rounds and each entry carries its round, so entries of another round (Round 2's last
deals, when we join) are skipped: they would show as this round's market.

Result entries (one per order: the receipt, whether the deal was verified and paid, and the market's
checks) come by the ten thousand an hour, so they are counted, not kept: results_hourly.json holds,
per clock hour, the deals agreed (a receipt with a price), those paid, the checks the unpaid ones
failed, the unpaid ones that passed every check (the buyer's own conditions refused them), and a few
sample entries. The journal's investigation desk (journal.py) reads it.
"""
import json
import os
import time

import httpx

import common

URL = os.environ.get("MARKET_URL", "https://market.ourolabs.ai").rstrip("/")
EVERY = 3600                     # seconds between collections
PAGES = 80                       # 500 entries a page; a run stops here and the next one goes on (11/3: ~14k entries an hour)
RESULT_HOURS = 48                # hours of result counts kept
SETTLE_KEYS = ("order", "buyer", "shop", "item", "amount", "name", "grade", "claim", "claim_honest",
               "warranty_hours", "unit")
BROKEN_KEYS = ("buyer", "shop", "item", "unit", "order")


def folder():
    return common.MARKET_DIR / "ledger"


def fetch(after: int) -> list:
    r = httpx.get(f"{URL}/api/ledger", params={"after": after}, timeout=30)
    r.raise_for_status()
    return r.json()


def collect(get=fetch, pages: int = PAGES) -> int:
    """Append new settle and broken entries to ledger/*.jsonl and count the result entries by hour.
    Returns how many settle and broken entries were new."""
    f = folder()
    f.mkdir(parents=True, exist_ok=True)
    state = common.read_json(f / "state.json", {}) or {}
    hourly = common.read_json(f / "results_hourly.json", {}) or {}
    after, n = state.get("seq", -500), 0
    for _ in range(pages):
        rows = get(after)
        if not rows:
            break
        with open(f / "settles.jsonl", "a", encoding="utf-8") as s, open(f / "broken.jsonl", "a", encoding="utf-8") as b:
            for e in rows:
                d, keys, out = e.get("data") or {}, None, None
                if e.get("round", common.ROUND) != common.ROUND:
                    continue
                if e.get("kind") == "result":
                    count_result(hourly, e)
                elif e.get("kind") == "settle":
                    keys, out = SETTLE_KEYS, s
                elif e.get("kind") == "broken":
                    keys, out = BROKEN_KEYS, b
                if out is not None:
                    out.write(json.dumps({"seq": e.get("seq"), "ts": e.get("ts"), **{k: d.get(k) for k in keys}},
                                         ensure_ascii=False) + "\n")
                    n += 1
        after = rows[-1]["seq"]
        if len(rows) < 500:
            break
    state.update(seq=after, at=time.time())
    common.write_json(f / "state.json", state)
    keep = int(time.time() // 3600 * 3600) - RESULT_HOURS * 3600
    common.write_json(f / "results_hourly.json", {h: v for h, v in hourly.items() if int(h) >= keep})
    return n


def count_result(hourly: dict, e: dict) -> None:
    """One result entry into its clock hour's counts (results_hourly.json)."""
    d = e.get("data") or {}
    h = hourly.setdefault(str(int((e.get("ts") or 0) // 3600 * 3600)),
                          {"results": 0, "agreed": 0, "paid": 0, "failed": {}, "passed_unpaid": 0, "samples": []})
    h["results"] += 1
    if not isinstance((d.get("receipt") or {}).get("price"), (int, float)):
        return                                                  # no agreement: the talk ended without a receipt
    h["agreed"] += 1
    if d.get("verified"):
        h["paid"] += 1
        return
    failed = [k for k, v in (d.get("checks") or {}).items() if v is False and k != "settled"]
    for k in failed:
        h["failed"][k] = h["failed"].get(k, 0) + 1
    h["passed_unpaid"] += not failed
    h["samples"] = (h["samples"] + [e.get("seq")])[-3:]


def names() -> dict:
    """Shop address -> shop name. A settle entry names its shop by address (a tunnel URL, or the market's
    /shop/<name> for a house shop), while reviews, our buyer's notebook and the forum use names."""
    return common.read_json(folder() / "shop_names.json", {}) or {}


def learn_names(shops: list) -> dict:
    """Add the market's shop list (GET /api/shops: name and url) to the names kept. Old addresses stay:
    a shop that restarts gets a new tunnel URL, and its earlier settles still carry the old one."""
    known = names()
    known.update({s["url"]: s["name"] for s in shops if s.get("url") and s.get("name")})
    folder().mkdir(parents=True, exist_ok=True)
    common.write_json(folder() / "shop_names.json", known)
    return known


def _mean(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def summarize(catalog: dict, now: float | None = None) -> dict:
    """market_summary.json: the last 24 h (and 6 h) of settles by SKU, category and shop (by name,
    where the shop list gave one), and breakdowns by grade over everything kept (a broken entry
    names the buyer and the item, so it is matched to that buyer's last settle of the item before it)."""
    now = now or time.time()
    settles = common.read_jsonl(folder() / "settles.jsonl")
    broken = common.read_jsonl(folder() / "broken.jsonl")
    named = names()
    by_sku, by_cat, by_shop = {}, {}, {}
    for s in settles:
        ts = s.get("ts") or 0
        if ts < now - 24 * 3600:
            continue
        c = catalog.get(s.get("item")) or {}
        lp = c.get("list_price")
        for key, table in ((s.get("item"), by_sku), (c.get("category"), by_cat)):
            if not key:
                continue
            t = table.setdefault(key, {"sold_24h": 0, "sold_6h": 0, "ratios": [], "grades": {}, "last_seq": 0})
            t["sold_24h"] += 1
            t["last_seq"] = max(t["last_seq"], s.get("seq") or 0)        # a sample the forum can show
            t["sold_6h"] += ts >= now - 6 * 3600
            if lp and isinstance(s.get("amount"), (int, float)):
                t["ratios"].append(s["amount"] / lp)
            t["grades"][s.get("grade")] = t["grades"].get(s.get("grade"), 0) + 1
        if s.get("shop"):
            sh = by_shop.setdefault(named.get(s["shop"], s["shop"]), {"settled_24h": 0, "claims": 0, "honest": 0})
            sh["settled_24h"] += 1
            if s.get("claim"):
                sh["claims"] += 1
                sh["honest"] += s.get("claim_honest") is True
    for table in (by_sku, by_cat):
        for t in table.values():
            t["price_ratio"] = _mean(t.pop("ratios"))
    sold_by_grade, broke_by_grade = {}, {}
    last = {}                                           # (buyer, item) -> grade of their latest settle
    events = sorted([(s.get("ts") or 0, 0, s) for s in settles] + [(b.get("ts") or 0, 1, b) for b in broken],
                    key=lambda x: (x[0], x[1]))
    for _, is_broken, e in events:
        key = (e.get("buyer"), e.get("item"))
        if not is_broken:
            if (catalog.get(e.get("item")) or {}).get("kind") == "goods":
                sold_by_grade[e.get("grade")] = sold_by_grade.get(e.get("grade"), 0) + 1
            last[key] = e.get("grade")
        elif key in last:
            broke_by_grade[last[key]] = broke_by_grade.get(last[key], 0) + 1
    return {"at": int(now), "settles_24h": sum(t["sold_24h"] for t in by_sku.values()),
            "by_sku": by_sku, "by_category": by_cat, "by_shop": by_shop,
            "goods_sold_by_grade": sold_by_grade, "broken_by_grade": broke_by_grade}


def collect_if_due(catalog: dict, now: float | None = None, get=fetch) -> bool:
    """Collect and rewrite market_summary.json when the last collection is an hour old."""
    now = now or time.time()
    state = common.read_json(folder() / "state.json", {}) or {}
    if now - state.get("at", 0) < EVERY:
        return False
    n = collect(get)
    common.write_json(common.MARKET_DIR / "market_summary.json", summarize(catalog, now))
    print(f"[ledger] {n} new settle/broken entries; market_summary.json rewritten", flush=True)
    return True


if __name__ == "__main__":
    print(collect_if_due(common.catalog(), now=None) or "[ledger] collected less than an hour ago")
