"""Future food-only SKU study. OFF by default; no current-price or acceptance claims.

The snapshot is published before any quote threads, using atomic link, not a
new lock/thread. A retained file is never reassigned, including after END.
"""
import hashlib
import json
import math
import os
import statistics
import tempfile

ENABLED = True
START = 1794058800  # 2026-11-07 22:40 KST
END = 1794062400  # 2026-11-07 23:40 KST
STUDY = "food-sku-20261107-2240"
MAX_BYTES = 8 << 20


def number(v):
    return type(v) in (int, float) and 0 <= v < 10**12 and math.isfinite(v)


def integer(v):
    return type(v) is int and 0 < v < 10**12


def allocation(oid):
    return "AB"[hashlib.sha256((STUDY + "\0" + oid).encode()).digest()[0] & 1]


def active(order, now):
    t = order.get("_buyer_started_at")
    return (ENABLED is True and order.get("kind") == "food" and
            isinstance(order.get("order"), str) and 0 < len(order["order"]) <= 96 and
            all(number(x) for x in (START, END, t, now)) and
            0 < START < END and END - START in (1800, 3600) and START <= t <= now < END)


def evidence(payments, settled, orders, started, need):
    """Own paid True + review-hook settled.time, joined with completed measure order.
    Strict settlement-before-current-start and prior need >= current need.
    Duplicate IDs are ambiguous, hence excluded. Never consult receipt memory.
    """
    def unique(rows):
        out, duplicate = {}, set()
        for r in rows:
            if not isinstance(r, dict) or not isinstance(r.get("order"), str):
                continue
            k = r["order"]
            if k in out:
                duplicate.add(k)
            out[k] = r
        return {k: v for k, v in out.items() if k not in duplicate}
    p, s, m = map(unique, (payments, settled, orders))
    out = {}
    if not number(started) or not number(need):
        return out
    for oid, r in s.items():
        q = m.get(oid, {})
        seconds = q.get("seconds")
        ts, elapsed = q.get("ts"), seconds.get("end") if isinstance(seconds, dict) else None
        t, price, hours = r.get("time"), r.get("price"), r.get("use_within_hours")
        if not (p.get(oid, {}).get("paid") is True and q.get("kind") == "food" and
                q.get("passed_to_kit") is True and q.get("sku") == r.get("sku") and
                number(q.get("use_within_hours")) and q["use_within_hours"] >= need and
                all(number(x) for x in (ts, elapsed, t, hours)) and integer(price) and
                started - 10800 <= ts - elapsed < t < started and ts < started and
                hours >= need and r.get("shop") and r.get("sku")):
            continue
        out.setdefault((r["shop"], r["sku"]), []).append(price)
    return out


def candidate(offers, baseline, shop, history, cap):
    def median(o):
        prices = history.get((shop, o["sku"]), [])
        return statistics.median(prices) if len(prices) >= 2 else None
    base = median(baseline)
    threshold = 1 - base / baseline["list_price"] if base is not None else .30
    eligible = []
    for o in offers:
        lp = o.get("list_price")
        if o.get("sku") == baseline.get("sku") or not integer(lp):
            continue
        paid = median(o)
        if paid is not None and paid <= cap(o) and 1 - paid / lp >= threshold:
            eligible.append((1 - paid / lp, o))
    return max(eligible, key=lambda pair: pair[0])[1] if eligible else None


def read_rows(path):
    # Truncation, missing files, malformed input: whole study safely falls back.
    with open(path, "rb") as src:
        data = src.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or (data and not data.endswith(b"\n")):
        raise ValueError("incomplete history")
    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
    if any(not isinstance(r, dict) for r in rows):
        raise ValueError("invalid rows")
    return rows


def read_order_rows(path):
    """Read a fixed-size snapshot, projecting only policy inputs.
    Bounds are local to this reader; other ledgers retain their 8 MiB contract.
    Reject the entire history on incomplete/malformed/oversize input.
    """
    max_snapshot, max_line, max_rows = 128 << 20, 128 << 10, 50000
    rows = []
    with open(path, "rb") as src:
        remaining = os.fstat(src.fileno()).st_size
        if remaining > max_snapshot:
            raise ValueError("order history too large")
        while remaining:
            line = src.readline(min(remaining, max_line + 1))
            remaining -= len(line)
            if not line or len(line) > max_line or not line.endswith(b"\n"):
                raise ValueError("incomplete order history")
            if not line.strip():
                continue
            if len(rows) >= max_rows:
                raise ValueError("too many orders")
            try:
                r = json.loads(line)
            except RecursionError as exc:
                raise ValueError("invalid order nesting") from exc
            if not isinstance(r, dict):
                raise ValueError("invalid order")
            seconds = r.get("seconds")
            rows.append({**{k: r.get(k) for k in
                         ("order", "kind", "passed_to_kit", "sku", "ts", "use_within_hours")},
                         "seconds": {"end": seconds.get("end")} if isinstance(seconds, dict) else None})
        if os.fstat(src.fileno()).st_size < src.tell():
            raise ValueError("truncated order history")
    return rows


def reserve(md, settled_path, order, shops, budget, baseline, cap, now):
    """Freeze the whole shop map serially before any decision. Retry reads only it.
    No assignment if there is no distinct pre-treatment alternative in any shop.
    """
    if not isinstance(order, dict) or order.get("kind") != "food" or not isinstance(order.get("order"), str):
        return None
    oid = order["order"]
    path = os.path.join(md, ".food-sku-" + hashlib.sha256((STUDY + "\0" + oid).encode()).hexdigest() + ".json")
    def retained():
        with open(path, encoding="utf-8") as src:
            value = json.load(src)
        if not isinstance(value, dict) or value.get("study") != STUDY or value.get("order") != oid or not isinstance(value.get("shops"), dict):
            raise ValueError("invalid snapshot")
        return value
    try:
        if os.path.lexists(path):
            return retained()
        if not active(order, now):
            return None
        # A tombstone survives a missing/corrupt snapshot: never choose a new SKU
        # on retry. Concurrent initial callers fail closed rather than wait.
        os.mkdir(path + ".seen")
        h = evidence(read_rows(os.path.join(md, "payments.jsonl")), read_rows(settled_path),
                     read_order_rows(os.path.join(md, "measure", "orders.jsonl")),
                     order["_buyer_started_at"], order.get("use_within_hours", 0))
        choices, different = {}, False
        arm = allocation(oid)
        for s in shops:
            b = baseline(s["usable"], budget)
            c = candidate(s["usable"], b, s["url"], h, cap)
            different |= c is not None
            choices[s["name"]] = dict(url=s["url"], baseline=b["sku"],
                candidate=c["sku"] if c else None, selected=(c if arm == "B" and c else b)["sku"],
                baseline_known=len(h.get((s["url"], b["sku"]), [])) >= 2)
        value = dict(study=STUDY, order=oid, arm=arm if different else None,
                     started=order["_buyer_started_at"], assigned_at=now, shops=choices)
        fd, tmp = tempfile.mkstemp(prefix=".food-sku-tmp-", dir=md)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as dst:
                json.dump(value, dst)
                dst.flush()
                os.fsync(dst.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(tmp)
        return retained()
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return dict(study=STUDY, order=oid, arm=None, shops={}, fallback="history-or-snapshot-unavailable")


def offer(shop, budget, baseline):
    if "_food_sku" not in shop:
        return baseline(shop["usable"], budget)
    entry = shop["_food_sku"]
    # Missing/changed SKU: baseline, never a newly calculated alternative.
    if isinstance(entry, dict) and entry.get("url") == shop.get("url"):
        selected = [o for o in shop["usable"] if o.get("sku") == entry.get("selected")]
        if len(selected) == 1:
            return selected[0]
    return baseline(shop["usable"], budget)
