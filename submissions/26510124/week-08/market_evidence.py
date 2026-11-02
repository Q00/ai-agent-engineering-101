"""Sample the official ledger: one bounded GET per 15-minute restock cycle.

This is deliberately a sample, not a complete ledger or proof of no refunds.
Only structured settlement/breakage facts survive; order budgets and prose do not.
"""
from collections import Counter
import json
from statistics import median

import httpx

from collect_feedback import fetch_json
from feedback import append_event, atomic_json, load_json, number

ORIGIN = "https://market.ourolabs.ai"
TTL = 3600
WINDOW = 7 * 86400
LIMIT = 2000


def normalize(rows, rnd, now):
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError("Invalid ledger page")
    result, seen = [], set()
    for e in rows:
        if (not isinstance(e, dict) or type(e.get("seq")) is not int or e["seq"] <= 0
                or e["seq"] in seen or not number(e.get("ts"))):
            raise ValueError("Invalid/duplicate ledger sequence")
        seen.add(e["seq"])
        if e.get("round") != rnd or not 0 <= now - e["ts"] <= WINDOW:
            continue
        d = e.get("data")
        if not isinstance(d, dict) or e.get("kind") not in ("settle", "broken"):
            continue
        if not all(isinstance(d.get(k), str) and 0 < len(d[k]) <= 500 for k in ("item", "shop")):
            continue
        row = {"seq": e["seq"], "ts": e["ts"], "kind": e["kind"], "sku": d["item"], "shop": d["shop"]}
        for key in ("unit", "buyer"):
            if isinstance(d.get(key), str) and 0 < len(d[key]) <= 200:
                row[key] = d[key]
        if e["kind"] == "settle":
            # /dashboard uses data.amount, not a guessed data.price field.
            if type(d.get("amount")) is not int or d["amount"] <= 0:
                continue
            row["price"] = d["amount"]
            if d.get("grade") in ("A", "B", "C"):
                row["grade"] = d["grade"]
            if type(d.get("warranty_hours")) is int and 0 <= d["warranty_hours"] <= 6:
                row["warranty_hours"] = d["warranty_hours"]
        else:
            # Missing/boolean 'refunded' is unknown, never evidence of zero cost.
            if type(d.get("refunded")) is int and d["refunded"] >= 0:
                row["refunded"] = d["refunded"]
        result.append(row)
    return result


def read(folder, rnd, now):
    state = load_json(folder / "market-evidence.json")
    if (not isinstance(state, dict) or state.get("schema") != 1 or state.get("round") != rnd
            or not number(state.get("updated_at")) or not 0 <= now - state["updated_at"] <= TTL
            or not isinstance(state.get("events"), list) or len(state["events"]) > LIMIT):
        return None
    return state


def refresh(root, rnd, now, *, client=None):
    if rnd < 3:
        return None
    md = root / ".market"
    control = md / "market-evidence-control.json"
    old_control = load_json(control) if control.exists() else {}
    if not isinstance(old_control, dict):
        raise ValueError("Invalid ledger poll control")
    last = old_control.get("last_poll")
    if last is not None and not number(last):
        raise ValueError("Invalid ledger poll time")
    if number(last) and now - last < 900:
        return read(md, rnd, now)
    atomic_json(control, {"last_poll": now})  # Failures also consume the polling slot.
    owned = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False)
    try:
        rows = fetch_json(client, ORIGIN + "/api/ledger?after=-500")
        incoming = normalize(rows, rnd, now)
        previous = load_json(md / "market-evidence.json")
        previous = previous if isinstance(previous, dict) and previous.get("round") == rnd else {}
        newest = max((r["seq"] for r in rows), default=0)
        reset = newest < previous.get("newest_seq", 0)
        events = {} if reset else {e["seq"]: e for e in previous.get("events", [])
                                  if number(e.get("ts")) and 0 <= now - e["ts"] <= WINDOW}
        events.update({e["seq"]: e for e in incoming})
        kept, size = [], 0
        for e in sorted(events.values(), key=lambda e: e["seq"], reverse=True)[:LIMIT]:
            size += len(json.dumps(e, ensure_ascii=False).encode()) + 2
            if size > 800_000:
                break  # Stay below load_json's byte limit, including long UTF-8 fields.
            kept.append(e)
        state = {"schema": 1, "round": rnd, "updated_at": now, "newest_seq": newest,
                 "reset_at": now if reset else previous.get("reset_at"),
                 "events": list(reversed(kept)),
                 "coverage": "sampled_latest_500_per_poll; gaps and unobserved refunds are possible"}
        atomic_json(md / "market-evidence.json", state)
        append_event(root / "logs/market-evidence.jsonl", {"time": now, "round": rnd,
                     "rows_read": len(rows), "facts_kept": len(incoming), "reset": reset, "newest_seq": newest})
        return state
    except (httpx.HTTPError, ValueError, OSError) as exc:
        append_event(root / "logs/market-evidence.jsonl", {"time": now, "round": rnd,
                     "status": "unavailable", "error": type(exc).__name__})
        return read(md, rnd, now)
    finally:
        if owned:
            client.close()


def demand_scores(state, now):
    groups = {}
    for e in (state or {}).get("events", []):
        if e["kind"] == "settle" and 0 <= now - e["ts"] <= 86400:
            groups.setdefault(e["sku"], []).append(e)
    # Evidence from multiple shops; cap any single shop's influence.
    return {sku: sum(min(n, 3) for n in Counter(e["shop"] for e in rows).values())
            for sku, rows in groups.items() if len(rows) >= 3 and len({e["shop"] for e in rows}) >= 2}


def comparable_price(state, product, warranty, now):
    rows = [e for e in (state or {}).get("events", []) if e["kind"] == "settle"
            and 0 <= now - e["ts"] <= 86400 and e["sku"] == product["sku"]
            and e.get("grade") == product["grade"] and e.get("warranty_hours") == warranty]
    if len(rows) < 3 or len({e["shop"] for e in rows}) < 2:
        return None
    # Median across shop medians prevents one shop's volume dominating the quote.
    prices = {shop: median(e["price"] for e in rows if e["shop"] == shop) for shop in {e["shop"] for e in rows}}
    return {"price": int(median(prices.values())), "samples": len(rows), "shops": len(prices)}
