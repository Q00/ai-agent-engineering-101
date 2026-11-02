"""The shop's own sales history: one JSON line per event in sales_log.jsonl.

{"kind": "open", "task", "item", "ask"}                              the opening ask of a negotiation
{"kind": "end",  "task", "item", "result": "deal"|"reject", "price", "offers": [...]}

The seller reads the recent history before every opening ask and every counter, so its
prices move with what actually sold, not with what a model guesses each time.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# SHOP_SALES points tests at a scratch file so test buyers do not shape real prices.
SALES = Path(os.environ.get("SHOP_SALES") or Path(__file__).with_name("sales_log.jsonl"))
WINDOW = 60   # recent negotiations per item


def record(row):
    row = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row}
    with open(SALES, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rows():
    if not SALES.exists():
        return []
    rows = []
    for line in SALES.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def stats(item, reserve):
    """Numbers about the last WINDOW negotiations for this item, or None if there are none."""
    rows = _rows()
    opens = [r for r in rows if r.get("kind") == "open" and r.get("item") == item][-WINDOW:]
    if not opens:
        return None
    tasks = {r["task"] for r in opens}
    ends = {r["task"]: r for r in rows if r.get("kind") == "end" and r.get("task") in tasks}
    deals = [e["price"] for e in ends.values() if e.get("result") == "deal" and e.get("price") is not None]
    first_offers = [e["offers"][0] for e in ends.values() if e.get("offers")]
    asks = [r["ask"] for r in opens]
    return {
        "negotiations": len(opens),
        "deals": len(deals),
        "deal_rate": len(deals) / len(opens),
        "avg_first_ask": sum(asks) / len(asks),
        "avg_price": sum(deals) / len(deals) if deals else None,
        "min_price": min(deals) if deals else None,
        "max_price": max(deals) if deals else None,
        "at_reserve": sum(1 for p in deals if p == reserve),
        "avg_first_offer": sum(first_offers) / len(first_offers) if first_offers else None,
    }


def describe(item, s):
    if s is None:
        return f"You have no sales history for {item} yet."
    lines = [f"Your last {s['negotiations']} negotiations for {item}: {s['deals']} deals ({s['deal_rate']:.0%}).",
             f"Your opening asks averaged {s['avg_first_ask']:.0f}."]
    if s["avg_price"] is not None:
        lines.append(f"Sale prices averaged {s['avg_price']:.0f} (from {s['min_price']} to {s['max_price']}); "
                     f"{s['at_reserve']} sold exactly at your lowest price.")
    if s["avg_first_offer"] is not None:
        lines.append(f"Buyers' first offers averaged {s['avg_first_offer']:.0f}.")
    return " ".join(lines)
