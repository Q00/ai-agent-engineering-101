"""Bounded settlement memory, observed refunds and explicitly estimated costs.

Only matched kit/ledger confirmations count as sales. Unit-level estimates
can miss public refunds; only the official wallet supplies aggregate profit.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median

from feedback import atomic_json, load_json, number
from payment_memory import recent_jsonl
from market_evidence import read as read_market

WINDOW = 7 * 86400
MAX_SALES = 1000
MARKET_FUTURE_TOLERANCE = 5
RECENT_OWN_SECONDS = 3600
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


def update(root, rnd, now, *, public=None, inventory=None):
    path = root / ".market/sales-performance.json"
    old = load_json(path) if path.exists() else {"schema": 1, "round": rnd, "sales": []}
    if not isinstance(old, dict) or old.get("schema") != 1 or not isinstance(old.get("sales"), list):
        raise ValueError("Invalid sales memory; refusing to erase it")
    if len(old["sales"]) > MAX_SALES:
        raise ValueError("Oversized sales memory")
    public = public if public is not None else read_market(root / ".market", rnd, now)
    if public and public.get("round") != rnd:
        public = None
    reset_at = (public.get("reset_at") if public is not None else
                old.get("reset_at") if old.get("round") == rnd else None)
    sales = {s["unit"]: s for s in old["sales"] if old.get("round") == rnd and valid_sale(s, now)
             and (not number(reset_at) or s["receipt_time"] >= reset_at)}
    # Every receipt is recorded locally with a timestamp, round, SKU and unit.
    receipts = {}
    events = recent_jsonl(root / "logs/inventory-seller-events.jsonl", limit=1_000_000, rows=2000)
    for e in events:
        if (e.get("outcome") == "receipt_issued" and e.get("round") == rnd
                and number(e.get("time")) and 0 <= now - e["time"] <= WINDOW
                and (not number(reset_at) or e["time"] >= reset_at)
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
    public_rows = (public or {}).get("events", [])
    for e in public_rows:
        receipt = receipts.get(e.get("unit"))
        if (e["kind"] == "settle" and receipt and e["sku"] == receipt["sku"]
                and e["price"] == receipt["price"] and e["ts"] >= receipt["time"]
                and e["unit"] not in sales and e["seq"] not in seqs):
            sales[e["unit"]] = {"ledger": e["seq"], "unit": e["unit"], "sku": receipt["sku"],
                                "category": receipt["category"], "price": e["price"],
                                "receipt_time": receipt["time"], "observed_at": now}
            seqs.add(e["seq"])
    by_seq = {e["seq"]: e for e in public_rows if e["kind"] == "settle"}
    broken_by_sku = {}
    for e in public_rows:
        if e["kind"] == "broken":
            broken_by_sku.setdefault(e["sku"], []).append(e)
    for u, s in sales.items():
        r = receipts.get(u, {})
        for k in ("cost", "kind", "grade", "supplied_at", "warranty_hours"):
            if k not in s and r.get(k) is not None:
                s[k] = r[k]
        e = by_seq.get(s["ledger"], {})
        if (e.get("sku") == s["sku"] and e.get("price") == s["price"]
                and e.get("unit") == u):
            # The confirmed sequence and exact unit identify this settlement
            # even when the host clock runs ahead of the market. Do not mix
            # that earlier timestamp into local warranty-maturity accounting.
            s["shop"] = e["shop"]
            if number(e.get("ts")) and e["ts"] > 0:
                # Keep the raw market clock separate from local warranty and
                # storage accounting. The exact confirmed ledger/unit match
                # makes this usable for market-clock price recency later.
                s["market_settled_at"] = e["ts"]
            if e.get("ts", 0) >= s["receipt_time"]:
                s["settled_at"] = e["ts"]
        refunds = {e["seq"]: e for e in s.get("refunds", [])}
        for e in broken_by_sku.get(s["sku"], []):
            # Ledger sequence orders market events without comparing its
            # timestamps to receipt timestamps from a different clock.
            if e["seq"] <= s["ledger"] or e.get("refunded", 0) <= 0:
                continue
            if e.get("unit") == u:
                refunds[e["seq"]] = {"seq": e["seq"], "amount": e["refunded"], "ts": e["ts"]}
            elif not e.get("unit") and e["shop"] == s.get("shop"):
                s["ambiguous_refund"] = True  # Never guess which identical SKU broke.
        s["refunds"] = sorted(refunds.values(), key=lambda e: e["seq"])[-10:]
    result = {"schema": 1, "round": rnd, "updated_at": now, "reset_at": reset_at,
              "sales": sorted(sales.values(), key=lambda s: s["receipt_time"])[-MAX_SALES:],
              "funnel": funnel([e for e in events if not number(reset_at) or e.get("time", 0) >= reset_at],
                               sales, rnd, now)}
    inv = inventory or {}
    if all(number(inv.get(k)) for k in ("balance", "start", "injected")):
        result["wallet_profit"] = inv["balance"] - inv["start"] - inv["injected"]
    atomic_json(path, result)
    return result


def economics(sale, now):
    """Per-unit estimates; the official wallet is the only exact total profit."""
    cost, supplied = sale.get("cost"), sale.get("supplied_at")
    sold = sale.get("settled_at", sale.get("observed_at"))
    if (type(cost) is not int or cost <= 0 or not number(supplied) or not number(sold)
            or not 0 <= supplied <= sold <= now or sale.get("kind") not in ("goods", "food")):
        return {"status": "unknown_cost_or_time"}
    hours = (sold - supplied) / 3600
    storage = math.ceil(cost * .01 * hours / 24) if sale["kind"] == "goods" else 0
    refund = min(sale["price"], sum(r["amount"] for r in sale.get("refunds", [])))
    warranty = sale.get("warranty_hours")
    pending = (sale["kind"] == "goods" and
               (type(warranty) is not int or now < sold + warranty * 3600))
    status = "pending_warranty" if pending else "mature_estimate"
    if sale.get("ambiguous_refund"):
        status = "ambiguous_refund"
    net = sale["price"] - cost - storage - refund
    return {"status": status, "net_estimate": net, "observed_refund": refund,
            "storage_estimate": storage, "hours_to_sell": round(hours, 2),
            "roi_per_day_estimate": net / cost / max(hours / 24, 1 / 24),
            "time_basis": "ledger" if "settled_at" in sale else "observation_upper_bound"}


def recent_own_counts(memory, now):
    """Count unique confirmed units by their original local receipt time.

    A confirmation discovered late is not new demand. Neither observed_at nor
    a raw market-clock timestamp may move a sale into this window. Conflicting
    ledger/unit identities and inconsistent duplicate rows provide no credit.
    """
    records, units, ledgers = {}, {}, {}
    for sale in memory.get("sales", []):
        if (not isinstance(sale, dict) or type(sale.get("ledger")) is not int
                or sale["ledger"] <= 0 or not isinstance(sale.get("unit"), str)
                or not sale["unit"]):
            continue
        seq, unit = sale["ledger"], sale["unit"]
        records.setdefault((seq, unit), []).append(sale)
        units.setdefault(unit, set()).add(seq)
        ledgers.setdefault(seq, set()).add(unit)
    reset = memory.get("reset_at")
    counts = Counter()
    for (seq, unit), rows in records.items():
        if len(units[unit]) != 1 or len(ledgers[seq]) != 1:
            continue
        if not all(valid_sale(s, now) and 0 <= now - s["receipt_time"] <= RECENT_OWN_SECONDS
                   and (not number(reset) or s["receipt_time"] >= reset)
                   and ("settled_at" not in s or number(s["settled_at"])
                        and s["receipt_time"] <= s["settled_at"] <= now)
                   for s in rows):
            continue
        if len({(s["sku"], s["category"], s["price"], s["receipt_time"]) for s in rows}) != 1:
            continue
        counts[rows[0]["sku"]] += 1
    return counts


def sku_metrics(memory, now):
    recent = recent_own_counts(memory, now)
    groups = {}
    for s in memory.get("sales", []):
        g = groups.setdefault(s["sku"], {"settled": 0, "mature": 0, "pending": 0, "unknown": 0,
                                       "net_estimate": 0, "speed_score": 0, "hours_sum": 0,
                                       "refunded": 0})
        g["settled"] += 1
        e = economics(s, now)
        if e["status"] == "mature_estimate":
            g["mature"] += 1
            g["net_estimate"] += e["net_estimate"]
            g["speed_score"] += e["roi_per_day_estimate"]
            g["hours_sum"] += e["hours_to_sell"]
        else:
            g["pending" if e["status"] == "pending_warranty" else "unknown"] += 1
        if e.get("observed_refund", 0) > 0:
            g["refunded"] += 1
    for g in groups.values():
        g["speed_score"] /= max(1, g["mature"])
        g["mean_hours"] = g.pop("hours_sum") / max(1, g["mature"])
    for sku, g in groups.items():
        g.update(recent_own_confirmed_1h=recent[sku], recent_own_window_seconds=RECENT_OWN_SECONDS,
                 recent_own_time_basis="local_receipt_time")
    return groups


def own_price(memory, product, warranty, rnd, now, *, market_now=None):
    """Price actually paid to us, with exact terms and a known settlement time.

    `update` admits a sale only after matching its local receipt to a kit or
    ledger confirmation. A receipt or opening quote alone is not price evidence.
    Raw market timestamps require an independently trusted market_now. Legacy
    confirmations retain their existing local-time checks. Neither price path
    rewrites the timestamps used by warranty/storage accounting.
    """
    if (not isinstance(memory, dict) or memory.get("schema") != 1 or memory.get("round") != rnd
            or not number(memory.get("updated_at")) or not 0 <= now - memory["updated_at"] <= 86400
            or not isinstance(memory.get("sales"), list) or len(memory["sales"]) > MAX_SALES):
        return None
    reset = memory.get("reset_at")
    rows, units, seqs = [], set(), set()
    for s in memory["sales"]:
        if (not valid_sale(s, now) or s["sku"] != product["sku"] or s.get("grade") != product["grade"]
                or type(s.get("warranty_hours")) is not int or s["warranty_hours"] != warranty
                or number(reset) and s["receipt_time"] < reset
                or s["unit"] in units or s["ledger"] in seqs
                or s.get("ambiguous_refund") or s.get("refunds")):
            continue
        if number(market_now) and number(s.get("market_settled_at")):
            observed = s["market_settled_at"]
            if observed <= 0 or not -MARKET_FUTURE_TOLERANCE <= market_now - observed <= 86400:
                continue
        else:
            observed = s.get("settled_at")
            if (not number(observed) or not 0 <= now - observed <= 86400
                    or observed < s["receipt_time"]):
                continue
        rows.append((s, observed))
        units.add(s["unit"])
        seqs.add(s["ledger"])
    if len(rows) < 3:
        return None
    identity = sorted((s["ledger"], s["unit"], s["price"], observed) for s, observed in rows)
    return {"price": int(median(s["price"] for s, _ in rows)), "samples": len(rows),
            "evidence_id": hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest(),
            "latest_at": max(observed for _, observed in rows)}


def funnel(events, sales, rnd, now):
    tasks = {}
    for e in events:
        if (e.get("round") != rnd or not number(e.get("time")) or not 0 <= now - e["time"] <= 86400
                or not isinstance(e.get("task"), str)):
            continue
        t = tasks.setdefault(e["task"], {})
        if e.get("outcome") == "opened":
            t["opened"] = e
        elif e.get("outcome") == "quote" and e.get("stage") == "opening":
            t["quote"] = e
        elif e.get("outcome") in ("receipt_issued", "rejected", "error", "stock_unavailable", "cancelled", "expired"):
            t["end"] = e
    counts, groups = Counter(), {}
    sold_units = set(sales)
    for t in tasks.values():
        opened, end, quote = t.get("opened"), t.get("end", {}), t.get("quote", {})
        if not opened:
            continue  # A tail missing its start is not a complete conversion observation.
        counts["opened"] += 1
        outcome = end.get("outcome", "open")
        counts[outcome] += 1
        if outcome == "receipt_issued":
            counts["settled" if end.get("unit") in sold_units else "receipt_without_observed_settlement"] += 1
        if not all(opened.get(k) is not None for k in ("sku", "grade", "warranty_hours")):
            continue
        key = f"{opened['sku']}:{opened['grade']}:{opened['warranty_hours']}"
        g = groups.setdefault(key, {"completed": 0, "settled": 0, "rejected": 0, "unknown_exit": 0,
                                    "errors": 0, "opening_prices": [], "tasks": []})
        if outcome == "open":
            continue
        if outcome in ("error", "stock_unavailable"):
            g["errors"] += 1
            continue
        g["completed"] += 1
        g["tasks"].append(opened["task"])
        if end.get("unit") in sold_units and outcome == "receipt_issued":
            g["settled"] += 1
        elif outcome == "rejected" and end.get("reason_code") == "price_rejected":
            g["rejected"] += 1
        else:
            g["unknown_exit"] += 1
        if type(quote.get("price")) is int:
            g["opening_prices"].append(quote["price"])
    return {"counts": dict(counts), "groups": dict(list(groups.items())[-100:]),
            "limits": "24h bounded complete task samples; unpaid receipts and exits have unknown causes"}


def summary(memory, rnd, now):
    if (not isinstance(memory, dict) or memory.get("round") != rnd
            or not number(memory.get("updated_at")) or not 0 <= now - memory["updated_at"] <= 86400):
        return {"status": "no_recent_settlement_memory"}
    rows = [s for s in memory.get("sales", []) if valid_sale(s, now)]
    counts = Counter(s["sku"] for s in rows)
    stats = sku_metrics({"sales": rows}, now)
    return {"window": "<=1000 own sales / 7d; observed refunds only, estimated storage, unobserved refunds possible",
            "count": len(rows), "wallet_profit": memory.get("wallet_profit"),
            "top_skus": [{"sku": sku, "settled": count, **stats[sku]} for sku, count in counts.most_common(5)],
            "funnel": memory.get("funnel", {}).get("counts", {})}
