"""Pro-active forum posts as the buyer: a market-price note for one category, built from the public ledger.

Week-09 asks when an agent should speak first. Horvitz's answer is "when the note is likely wanted": these carry
the 24 h settle count, the median settled price and its share of list price, the best-selling SKU and ledger
references - the shape of the posts that collect votes on the board (11-05: "[시세] 침구: 최근 원장 500건 중 정산
13건 ..."). restock.py calls this every minute; at most one post per five minutes (shared with the review hook's
"정산 확인" posts through review_state.json), and a category is not repeated within three hours.
"""
import json
import statistics
import time
from collections import Counter
from pathlib import Path

import ledger

POST_EVERY = 300
ROTATE_HOURS = 3
MIN_SETTLES = 8
STATE_FILE = "review_state.json"


def compose(category, rows, catalog):
    """One line a buyer can act on, with the ledger seqs it rests on."""
    prices = [r["price"] for r in rows]
    ratios = [r["price"] / catalog[r["sku"]]["list_price"] for r in rows
              if catalog.get(r["sku"], {}).get("list_price")]
    best_sku, best_n = Counter(r["sku"] for r in rows).most_common(1)[0]
    name = (catalog.get(best_sku) or {}).get("name", best_sku)[:30]
    refs = [r["seq"] for r in sorted(rows, key=lambda r: -(r.get("ts") or 0)) if r.get("seq")][:3]
    text = (f"[시세] {category.split('>')[-1]}: 최근 24h 공개 ledger 정산 {len(rows)}건, 체결가 중앙값 "
            f"{statistics.median(prices):,.0f}원" + (f" (정가 대비 {statistics.median(ratios):.0%})" if ratios else "")
            + f", 최다 판매 {name} ({best_sku}, {best_n}건). 출처: ledger #{', #'.join(str(s) for s in refs)}")
    return text, refs


def maybe_market_post(state, catalog, now=None):
    now = int(now or time.time())
    state = Path(state)
    state_file = state / STATE_FILE
    memory = ledger.load_json(state_file) or {}
    if now - memory.get("last_post", 0) < POST_EVERY:
        return None
    cache = ledger.load_json(state / ledger.CACHE_FILE) or {}
    by_cat = {}
    for r in cache.get("rows") or []:
        category = (catalog.get(r.get("sku")) or {}).get("category")
        if category:
            by_cat.setdefault(category, []).append(r)
    posted = memory.get("market_posts") or {}
    ranked = sorted(by_cat.items(), key=lambda kv: -sum(r["price"] for r in kv[1]))   # richest category first
    for category, rows in ranked:
        if len(rows) < MIN_SETTLES or now - posted.get(category, 0) < ROTATE_HOURS * 3600:
            continue
        text, refs = compose(category, rows, catalog)
        with open(state / "outbox.jsonl", "a", encoding="utf-8") as out:
            out.write(json.dumps({"post": text, "refs": refs, "as": "buyer"}, ensure_ascii=False) + "\n")
        posted[category] = now
        memory.update({"last_post": now, "market_posts": posted})
        state_file.write_text(json.dumps(memory, ensure_ascii=False, indent=1), encoding="utf-8")
        return text
    return None
