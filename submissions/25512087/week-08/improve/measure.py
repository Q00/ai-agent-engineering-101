"""Measure Round 3 performance (week 08 self-improvement loop, step 1). Run from the week-08 folder:

    python3 improve/measure.py            -> writes improve/metrics/<KST time>.json, prints it

Two scopes: "window" = what happened since the previous run (byte offsets in improve/metrics/state.json),
"total" = everything since Round 3 opened. Read-only on logs/ and .market/ (logs are never edited).
Sources: logs/market-kit.log ([acp] settled / paid / not paying, [supply], [seller] events, [order]),
.market/inventory.json, .market/payments.jsonl, .market/buyer-memory.jsonl, .market/catalog.json.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOG = ROOT / "logs" / "market-kit.log"
MD = ROOT / ".market"
STATE = HERE / "metrics" / "state.json"
KST = timezone(timedelta(hours=9))

SETTLED = re.compile(r"\[acp\] settled (.+) at (\d+), ledger #(\d+), unit (\S+) grade (\S+)")
PAID = re.compile(r"\[acp\] paid (\d+) through (\S+), settled at ledger #(\d+)")
NOT_PAYING = re.compile(r"\[acp\] not paying: (.+)$")
SUPPLY = re.compile(r"\[supply\] (\d+) x .+ for (\d+), balance (\d+)")
SUPPLY_REFUSED = re.compile(r"\[supply\] .* refused: (\d+)")
SELLER = re.compile(r"\[seller\] (\{.*\})$")
ORDER = re.compile(r"\[order [^\]]+\] (.+?), budget (\d+), round (\d+)")


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def r3_start_offset() -> int:
    """Byte offset of the first Round 3 order in the log (cached in the state file)."""
    st = load_json(STATE, {})
    if st.get("r3_start") is not None:
        return st["r3_start"]
    off = 0
    with LOG.open("rb") as f:
        for line in f:
            if b"round 3" in line and b"[order" in line:
                return off
            off += len(line)
    return off


def parse_log(start: int) -> tuple[dict, int]:
    m = {"settled": 0, "revenue": 0, "margin": 0, "margin_known": 0, "settled_grades": Counter(),
         "openings": 0, "accepts": 0, "ends": 0, "accept_prices_over_floor": [],
         "supply_lines": 0, "supply_units": 0, "supply_spend": 0, "supply_refused": Counter(),
         "orders": 0, "orders_by_cat": Counter(), "paid": 0, "paid_sum": 0, "not_paying": Counter()}
    cost_of_unit, floor_of_task = {}, {}
    with LOG.open("rb") as f:
        f.seek(start)
        data = f.read()
    end = start + len(data)
    for raw in data.decode("utf-8", "replace").splitlines():
        if (x := SETTLED.search(raw)):
            price, unit = int(x.group(2)), x.group(4)
            m["settled"] += 1; m["revenue"] += price; m["settled_grades"][x.group(5)] += 1
            if unit in cost_of_unit:
                m["margin"] += price - cost_of_unit[unit]; m["margin_known"] += 1
        elif (x := PAID.search(raw)):
            m["paid"] += 1; m["paid_sum"] += int(x.group(1))
        elif (x := NOT_PAYING.search(raw)):
            m["not_paying"][x.group(1)[:60]] += 1
        elif (x := SUPPLY.search(raw)):
            m["supply_lines"] += 1; m["supply_units"] += int(x.group(1)); m["supply_spend"] += int(x.group(2))
        elif (x := SUPPLY_REFUSED.search(raw)):
            m["supply_refused"][x.group(1)] += 1
        elif (x := ORDER.search(raw)):
            if x.group(3) == "3":
                m["orders"] += 1; m["orders_by_cat"][x.group(1)] += 1
        elif (x := SELLER.search(raw)):
            try:
                e = json.loads(x.group(1))
            except ValueError:
                continue
            if e.get("event") == "opening":
                m["openings"] += 1
                if e.get("unit"):
                    cost_of_unit[e["unit"]] = float(e.get("cost") or 0)
                floor_of_task[e.get("task")] = e.get("floor")
            elif e.get("event") == "decision" and e.get("action") == "accept":
                m["accepts"] += 1
                fl = floor_of_task.get(e.get("task"))
                if fl:
                    m["accept_prices_over_floor"].append(round(float(e["price"]) / fl, 3))
            elif e.get("event") == "decision" and e.get("action") == "end":
                m["ends"] += 1
    a = m.pop("accept_prices_over_floor")
    m["accept_over_floor_mean"] = round(sum(a) / len(a), 3) if a else None
    m["accept_rate"] = round(m["accepts"] / m["openings"], 3) if m["openings"] else None
    for k in ("settled_grades", "supply_refused", "orders_by_cat", "not_paying"):
        m[k] = dict(Counter(m[k]).most_common(8))
    return m, end


def buyer_side(start_line: int) -> tuple[dict, int]:
    """Round 3 buyer results from buyer-memory.jsonl (lines after start_line) with value-based surplus."""
    out = {"talks": 0, "deals": 0, "spent": 0, "value": 0, "surplus": 0, "outcomes": Counter(),
           "price_share_of_value": []}
    lines = (MD / "buyer-memory.jsonl").read_text(encoding="utf-8").splitlines() if (MD / "buyer-memory.jsonl").exists() else []
    for raw in lines[start_line:]:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if r.get("round") != 3:
            continue
        out["talks"] += 1
        out["outcomes"][str(r.get("detail") or r.get("outcome"))[:50] if r.get("outcome") != "deal" else "deal"] += 1
        if r.get("outcome") == "deal" and r.get("price") and r.get("value"):
            out["deals"] += 1; out["spent"] += int(r["price"]); out["value"] += int(r["value"])
            out["surplus"] += int(r["value"]) - int(r["price"])
            out["price_share_of_value"].append(round(r["price"] / r["value"], 3))
    s = out.pop("price_share_of_value")
    out["mean_price_share"] = round(sum(s) / len(s), 3) if s else None
    out["outcomes"] = dict(out["outcomes"].most_common(8))
    return out, len(lines)


def payments(start_line: int) -> tuple[dict, int]:
    """What our buyer actually paid (payments.jsonl), valued at the SKU's list price: the market's surplus."""
    cat = load_json(MD / "catalog.json", {})
    try:
        lines = (MD / "payments.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}, start_line
    out = {"paid": 0, "refused": 0, "spent": 0, "value": 0, "surplus": 0}
    for raw in lines[start_line:]:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if not r.get("paid"):
            out["refused"] += 1
            continue
        v = int((cat.get(r.get("item")) or {}).get("list_price") or 0)
        out["paid"] += 1; out["spent"] += int(r.get("price") or 0); out["value"] += v
        out["surplus"] += v - int(r.get("price") or 0)
    out["mean_price_share"] = round(out["spent"] / out["value"], 3) if out["value"] else None
    return out, len(lines)


def shop_state(now: float) -> dict:
    inv = load_json(MD / "inventory.json", {})
    units = inv.get("units") or []
    start, inj, bal = float(inv.get("start") or 5e6), float(inv.get("injected") or 0), float(inv.get("balance") or 0)
    stock_cost = sum(float(u.get("cost") or 0) for u in units)
    ages = [(now - float(u.get("supplied_at") or now)) / 86400 for u in units if u.get("kind") != "food"]
    return {"balance": bal, "net_profit": round(bal - start - inj), "stock_units": len(units),
            "stock_at_cost": round(stock_cost), "net_worth_change": round(bal + stock_cost - start - inj),
            "categories": len({u.get("category") for u in units}), "skus": len({u.get("sku") for u in units}),
            "food_units": sum(1 for u in units if u.get("kind") == "food"),
            "food_expiring_6h": sum(1 for u in units if u.get("expires_at") and u["expires_at"] - now < 6 * 3600),
            "goods_older_3d": sum(1 for a in ages if a > 3), "bankruptcies_left": inv.get("bankruptcies_left")}


def main() -> None:
    now = time.time()
    st = load_json(STATE, {})
    r3 = r3_start_offset()
    total_log, end = parse_log(r3)
    win_log, _ = parse_log(max(r3, st.get("log_offset", r3)))
    total_buy, n = buyer_side(st.get("buyer_r3_line0", 0))
    win_buy, _ = buyer_side(st.get("buyer_line", st.get("buyer_r3_line0", 0)))
    total_pay, pn = payments(0)
    win_pay, _ = payments(st.get("pay_line", 0))
    total_buy["payments"], win_buy["payments"] = total_pay, win_pay
    label = datetime.fromtimestamp(now, KST).strftime("%Y%m%d-%H%M")
    hours = round((now - st["ts"]) / 3600, 2) if st.get("ts") else None
    out = {"ts": round(now), "kst": label, "window_hours": hours, "shop_now": shop_state(now),
           "window": {"shop": win_log, "buyer": win_buy}, "total": {"shop": total_log, "buyer": total_buy}}
    (HERE / "metrics").mkdir(exist_ok=True)
    (HERE / "metrics" / f"{label}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    st.update(ts=round(now), r3_start=r3, log_offset=end, buyer_line=n, pay_line=pn, last=label)
    st.setdefault("buyer_r3_line0", 0)
    STATE.write_text(json.dumps(st), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
