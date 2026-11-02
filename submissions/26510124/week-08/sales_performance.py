"""Bounded own-shop settlement memory from official kit confirmations.

Match an ACP confirmation to our receipt's unit AND price AND round. A missing
unit or a receipt alone is not a sale. Never change the original logs. This is
a sampled view of gross settlements, not net profit after refunds/storage.
"""
from collections import Counter
from pathlib import Path
import re

from feedback import atomic_json, load_json, number
from payment_memory import recent_jsonl

WINDOW = 7 * 86400
MAX_SALES = 1000
SETTLED = re.compile(r"^\d{2}:\d{2}:\d{2} \[acp\] settled .+ at ([0-9]+), ledger #([0-9]+), unit ([A-Za-z0-9_.:-]+) grade [ABC]$")


def log_tail(path, limit=2_000_000):
    try:
        with Path(path).open("rb") as stream:
            start = max(0, stream.seek(0, 2) - limit)
            stream.seek(start)
            raw = stream.read(limit)
    except FileNotFoundError:
        return []
    lines = raw.splitlines()
    if start:
        lines = lines[1:]
    if raw and not raw.endswith(b"\n"):
        lines = lines[:-1]
    return [line.decode("utf-8", errors="replace") for line in lines[-4000:]]


def valid_sale(row, now):
    return (isinstance(row, dict) and type(row.get("ledger")) is int and row["ledger"] > 0
            and all(isinstance(row.get(k), str) and row[k] for k in ("unit", "sku", "category"))
            and type(row.get("price")) is int and row["price"] > 0
            and number(row.get("receipt_time")) and 0 <= now - row["receipt_time"] <= WINDOW)


def update(root, rnd, now):
    path = root / ".market/sales-performance.json"
    old = load_json(path) if path.exists() else {"schema": 1, "round": rnd, "sales": []}
    if not isinstance(old, dict) or old.get("schema") != 1 or not isinstance(old.get("sales"), list):
        raise ValueError("Invalid sales memory; refusing to erase it")
    if len(old["sales"]) > MAX_SALES:
        raise ValueError("Oversized sales memory")
    sales = {s["unit"]: s for s in old["sales"] if old.get("round") == rnd and valid_sale(s, now)}
    # Every receipt is recorded locally with a timestamp, round, SKU and unit.
    receipts = {}
    for e in recent_jsonl(root / "logs/inventory-seller-events.jsonl", limit=1_000_000, rows=2000):
        if (e.get("outcome") == "receipt_issued" and e.get("round") == rnd
                and number(e.get("time")) and 0 <= now - e["time"] <= WINDOW
                and all(isinstance(e.get(k), str) and e[k] for k in ("unit", "sku", "category"))
                and type(e.get("price")) is int and e["price"] > 0):
            receipts[e["unit"]] = e
    seqs = {s["ledger"] for s in sales.values()}
    for line in log_tail(root / "logs/market-kit.log"):
        match = SETTLED.fullmatch(line)
        if match is None:
            continue
        price, seq, unit = int(match[1]), int(match[2]), match[3]
        e = receipts.get(unit)
        if not e or e["price"] != price or unit in sales or seq in seqs or seq <= 0:
            continue
        sales[unit] = {"ledger": seq, "unit": unit, "sku": e["sku"], "category": e["category"],
                       "price": price, "receipt_time": e["time"], "observed_at": now}
        seqs.add(seq)
    result = {"schema": 1, "round": rnd, "updated_at": now,
              "sales": sorted(sales.values(), key=lambda s: s["receipt_time"])[-MAX_SALES:]}
    atomic_json(path, result)
    return result


def summary(memory, rnd, now):
    if (not isinstance(memory, dict) or memory.get("round") != rnd
            or not number(memory.get("updated_at")) or not 0 <= now - memory["updated_at"] <= 86400):
        return {"status": "no_recent_settlement_memory"}
    rows = [s for s in memory.get("sales", []) if valid_sale(s, now)]
    counts = Counter(s["sku"] for s in rows)
    return {"window": "sample of <=1000 own gross settlements / 7 days; refunds and storage not deducted",
            "count": len(rows), "top_skus": [{"sku": sku, "settled": count} for sku, count in counts.most_common(5)]}
