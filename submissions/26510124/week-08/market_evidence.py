"""Sample three ledger pages and one stock snapshot per 15-minute polling slot.

This is deliberately a sample, not a complete ledger or proof of no refunds.
Only structured settlement/breakage facts survive; order budgets and prose do not.
The restock caller may request one extra opening-time attempt after unusable
preparation evidence; the persisted opening marker prevents retry storms.
"""
from collections import Counter
import hashlib
import json
import re
from statistics import median
import time

import httpx

from feedback import MAX_BYTES, append_event, atomic_json, load_json, number

ORIGIN = "https://market.ourolabs.ai"
TTL = 3600
WINDOW = 7 * 86400
LIMIT = 2000
FUTURE_TOLERANCE = 5
MAX_PAGES = 3
POLL_INTERVAL = 900


def normalize_stock(payload, rnd, now):
    """Public advertised stock only; reservations can still make it unavailable."""
    if (not isinstance(payload, dict) or payload.get("round") != rnd
            or not isinstance(payload.get("shops"), list) or len(payload["shops"]) > 2000):
        raise ValueError("Invalid public stock snapshot")
    totals, rows_read, seen = {}, 0, set()
    for shop in payload["shops"]:
        if (not isinstance(shop, dict) or not isinstance(shop.get("stock"), dict)
                or not isinstance(shop.get("name"), str) or not 0 < len(shop["name"]) <= 200
                or shop["name"] in seen):
            raise ValueError("Invalid shop stock")
        seen.add(shop["name"])
        rows_read += len(shop["stock"])
        if rows_read > 20000:
            raise ValueError("Public stock exceeds row limit")
        for sku, qty in shop["stock"].items():
            if (not isinstance(sku, str) or not re.fullmatch(r"[GF][0-9a-fA-F]{8}", sku)
                    or type(qty) is not int or not 0 <= qty <= 1000000):
                raise ValueError("Invalid public stock quantity")
            if qty:
                item = totals.setdefault(sku, {"units": 0, "shops": 0})
                item["units"] += qty
                item["shops"] += 1
    snapshot = {"round": rnd, "observed_at": now, "by_sku": totals,
                "coverage": "public_advertised_stock_may_include_reservations"}
    if len(json.dumps(snapshot).encode()) > 200000:
        raise ValueError("Public stock exceeds cache budget")
    return snapshot


def fetch_json(client, url, *, deadline):
    """A bounded stream; short individual I/O waits also bound deadline overrun."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("Ledger deadline reached")
    with client.stream("GET", url, timeout=min(2.0, remaining),
                       headers={"Accept-Encoding": "identity"}) as response:
        response.raise_for_status()
        content = bytearray()
        # No large chunk aggregation: inspect the deadline at every network
        # chunk even when a server sends a slow, continuously trickling body.
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > MAX_BYTES or time.monotonic() > deadline:
                raise ValueError("Ledger response exceeds size/time budget")
    return json.loads(content)


def recent(ts, now, window):
    return number(ts) and -FUTURE_TOLERANCE <= now - ts <= window


def normalize(rows, rnd, now):
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError("Invalid ledger page")
    result, seen = [], set()
    for e in rows:
        if (not isinstance(e, dict) or type(e.get("seq")) is not int or e["seq"] <= 0
                or e["seq"] in seen or not number(e.get("ts"))):
            raise ValueError("Invalid/duplicate ledger sequence")
        seen.add(e["seq"])
        if e.get("round") != rnd or not recent(e["ts"], now, WINDOW):
            continue
        d = e.get("data")
        if not isinstance(d, dict) or e.get("kind") not in ("settle", "broken"):
            continue
        if not all(isinstance(d.get(k), str) and 0 < len(d[k]) <= 500 for k in ("item", "shop")):
            continue
        row = {"seq": e["seq"], "ts": e["ts"], "kind": e["kind"], "sku": d["item"], "shop": d["shop"]}
        for key in ("unit", "buyer", "order"):
            if isinstance(d.get(key), str) and 0 < len(d[key]) <= 200:
                row[key] = d[key]
        if e["kind"] == "settle":
            # /dashboard uses data.amount, not a guessed data.price field.
            if type(d.get("amount")) is not int or d["amount"] <= 0:
                continue
            row["price"] = d["amount"]
            if d.get("grade") in ("A", "B", "C"):
                row["grade"] = d["grade"]
            # Actual settlements use null for no warranty, and numeric hours
            # (including 24.0). Missing/malformed terms remain unknown.
            if "warranty_hours" in d and d["warranty_hours"] is None:
                row["warranty_hours"] = 0
            elif number(d.get("warranty_hours")) and 0 <= d["warranty_hours"] <= 168:
                hours = d["warranty_hours"]
                row["warranty_hours"] = int(hours) if int(hours) == hours else hours
            if type(d.get("claim_honest")) is bool:
                row["claim_honest"] = d["claim_honest"]
        else:
            # Missing/boolean 'refunded' is unknown, never evidence of zero cost.
            if type(d.get("refunded")) is int and d["refunded"] >= 0:
                row["refunded"] = d["refunded"]
        result.append(row)
    return result


def read(folder, rnd, now):
    state = load_json(folder / "market-evidence.json")
    if (not isinstance(state, dict) or state.get("schema") != 1 or state.get("round") != rnd
            or not recent(state.get("updated_at"), now, TTL)
            or not isinstance(state.get("events"), list) or len(state["events"]) > LIMIT):
        return None
    return state


def refresh(root, rnd, now, *, client=None, deadline_seconds=12, include_stock=True,
            opening_retry=None):
    """Poll with an optional, once-per-opening retry controlled by restock.

    Only the caller knows its focus SKUs and validated supply-window state. It
    passes ``opening_retry=opens_at`` only in an open window when preparation
    yielded no usable demand for those SKUs. Ordinary callers omit the argument.
    Failed attempts consume both their polling slot and their opening marker.
    """
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
    same_round = old_control.get("round", rnd) == rnd
    last_retry = old_control.get("last_retry_opening") if same_round else None
    if last_retry is not None and not number(last_retry):
        raise ValueError("Invalid opening retry marker")
    if opening_retry is not None and (not number(opening_retry) or not 0 < opening_retry <= now + 5):
        raise ValueError("Opening retry requires an already opened window")
    retry = opening_retry is not None and (last_retry is None or opening_retry > last_retry)
    if number(last) and same_round and -5 <= now - last < POLL_INTERVAL and not retry:
        return read(md, rnd, now)
    if not number(deadline_seconds) or not 0 < deadline_seconds <= 12:
        raise ValueError("Ledger deadline must be within 12 seconds")
    atomic_json(control, {"last_poll": now, "round": rnd,
                         "last_retry_opening": opening_retry if retry else last_retry})
    owned = client is None
    client = client or httpx.Client(timeout=2, follow_redirects=False)
    deadline = time.monotonic() + deadline_seconds
    try:
        rows = fetch_json(client, ORIGIN + "/api/ledger?after=-500", deadline=deadline)
        incoming = normalize(rows, rnd, now)
        # The ledger is append-only within a round. Request older pages by their
        # fixed sequence boundary; concurrent new events cannot shift the page.
        pages, rows_read, page_error = 1, len(rows), None
        sampled = {r["seq"]: r for r in rows}
        oldest = min(sampled, default=0)
        while pages < MAX_PAGES and len(rows) == 500 and oldest > 1:
            if time.monotonic() >= deadline:
                page_error = "deadline"
                break
            try:
                rows = fetch_json(client, ORIGIN + f"/api/ledger?after={max(0, oldest - 501)}", deadline=deadline)
                older = normalize(rows, rnd, now)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                page_error = type(exc).__name__
                break  # Keep the successfully checked head; do not retry.
            pages += 1
            rows_read += len(rows)
            new_rows = [r for r in rows if r["seq"] < oldest]
            if not new_rows:
                break
            allowed = {r["seq"] for r in new_rows}
            sampled.update({r["seq"]: r for r in new_rows})
            incoming.extend(r for r in older if r["seq"] in allowed)
            oldest = min(allowed)
        previous = load_json(md / "market-evidence.json")
        previous = previous if isinstance(previous, dict) and previous.get("round") == rnd else {}
        stock, stock_error = None, None
        if include_stock:
            try:
                # Shares the ledger deadline. It never delays a second polling
                # slot or triggers independent retries after a failed request.
                if deadline - time.monotonic() < 1:
                    raise ValueError("No stock deadline remaining")
                stock = normalize_stock(fetch_json(client, ORIGIN + "/api/shops", deadline=deadline), rnd, now)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                stock_error = type(exc).__name__
        if stock is None:
            old_stock = previous.get("market_stock")
            if (isinstance(old_stock, dict) and old_stock.get("round") == rnd
                    and recent(old_stock.get("observed_at"), now, TTL)):
                stock = old_stock  # Retain its actual timestamp, never refresh it.
        newest = max(sampled, default=previous.get("newest_seq", 0))
        reset = bool(sampled) and newest < previous.get("newest_seq", 0)
        if reset and stock is previous.get("market_stock"):
            stock = None
        events = {} if reset else {e["seq"]: e for e in previous.get("events", [])
                                  if isinstance(e, dict) and type(e.get("seq")) is int
                                  and recent(e.get("ts"), now, WINDOW)}
        events.update({e["seq"]: e for e in incoming})
        kept, size = [], 0
        event_budget = 800000 - len(json.dumps(stock, ensure_ascii=False).encode()) - 4000
        for e in sorted(events.values(), key=lambda e: e["seq"], reverse=True)[:LIMIT]:
            size += len(json.dumps(e, ensure_ascii=False).encode()) + 2
            if size > event_budget:
                break  # Stay below load_json's byte limit, including long UTF-8 fields.
            kept.append(e)
        state = {"schema": 1, "round": rnd, "updated_at": now, "newest_seq": newest,
                 "reset_at": now if reset else previous.get("reset_at"),
                 "events": list(reversed(kept)),
                 "market_stock": stock, "stock_error": stock_error,
                 "coverage": "sampled_up_to_3x500_per_poll; gaps and unobserved refunds are possible",
                 "sample": {"pages": pages, "rows": len(sampled), "first_seq": min(sampled, default=None),
                            "last_seq": max(sampled, default=None),
                            "first_ts": min((r["ts"] for r in sampled.values()), default=None),
                            "last_ts": max((r["ts"] for r in sampled.values()), default=None),
                            "partial_error": page_error}}
        atomic_json(md / "market-evidence.json", state)
        append_event(root / "logs/market-evidence.jsonl", {"time": now, "round": rnd,
                     "rows_read": rows_read, "pages": pages, "facts_kept": len(incoming),
                     "reset": reset, "newest_seq": newest, "partial_error": page_error,
                     "stock_error": stock_error, "stock_observed_at": (stock or {}).get("observed_at")})
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
        if e["kind"] == "settle" and recent(e["ts"], now, 86400):
            groups.setdefault(e["sku"], []).append(e)
    # Evidence from multiple shops; cap any single shop's influence.
    return {sku: sum(min(n, 3) for n in Counter(e["shop"] for e in rows).values())
            for sku, rows in groups.items() if len(rows) >= 3 and len({e["shop"] for e in rows}) >= 2}


def comparable_price(state, product, warranty, now):
    rows = [e for e in settlements(state, now, 86400) if e["sku"] == product["sku"]
            and e.get("grade") == product["grade"] and e.get("warranty_hours") == warranty]
    return price_summary(rows)


def settlements(state, now, window):
    """Count observed orders once; absent order IDs fall back to ledger sequence."""
    rows, seen_seq, seen_orders = [], set(), set()
    for e in sorted((state or {}).get("events", []), key=lambda row: row.get("seq", 0)):
        if (e.get("kind") != "settle" or not recent(e.get("ts"), now, window)
                or type(e.get("seq")) is not int or e["seq"] in seen_seq
                or type(e.get("price")) is not int or e["price"] <= 0
                or not isinstance(e.get("sku"), str) or not isinstance(e.get("shop"), str)):
            continue
        order = e.get("order")
        if order and order in seen_orders:
            continue
        seen_seq.add(e["seq"])
        if order:
            seen_orders.add(order)
        rows.append(e)
    return rows


def evidence_id(rows):
    facts = sorted((e["seq"], e["ts"], e["shop"], e["price"]) for e in rows)
    return hashlib.sha256(json.dumps(facts, separators=(",", ":")).encode()).hexdigest()[:20]


def price_summary(rows):
    rows = [e for e in rows if e.get("claim_honest") is not False]
    if len(rows) < 3 or len({e["shop"] for e in rows}) < 2:
        return None
    # Median across shop medians prevents one shop's volume dominating the quote.
    prices = {shop: median(e["price"] for e in rows if e["shop"] == shop) for shop in {e["shop"] for e in rows}}
    return {"price": int(median(prices.values())), "samples": len(rows), "shops": len(prices),
            "evidence_id": evidence_id(rows), "first_ts": min(e["ts"] for e in rows),
            "last_ts": max(e["ts"] for e in rows), "latest_at": max(e["ts"] for e in rows)}


def demand_profiles(state, now, window=3600):
    """Recent observed demand, never an extrapolated hourly sales forecast.

    Missing buyers cannot establish diverse demand. No stock absence, quantity,
    intent, or unseen purchase budget is inferred from a settlement sample.
    """
    if not isinstance(state, dict) or not recent(state.get("updated_at"), now, TTL):
        return {}
    groups = {}
    for e in settlements(state, now, window):
        groups.setdefault(e["sku"], []).append(e)
    result = {}
    for sku, rows in groups.items():
        buyers = Counter(e["buyer"] for e in rows if isinstance(e.get("buyer"), str) and e["buyer"])
        terms = {}
        for e in rows:
            if e.get("grade") in ("A", "B", "C") and number(e.get("warranty_hours")):
                terms.setdefault((e["grade"], e["warranty_hours"]), []).append(e)
        comparables = []
        for (grade, hours), same_terms in sorted(terms.items()):
            summary = price_summary(same_terms)
            if summary:
                comparables.append({"grade": grade, "warranty_hours": hours, **summary})
        first, last = min(e["ts"] for e in rows), max(e["ts"] for e in rows)
        public_stock, ratio = None, None
        stock = state.get("market_stock")
        if (isinstance(stock, dict) and stock.get("round") == state.get("round")
                and recent(stock.get("observed_at"), now, TTL)):
            item = stock.get("by_sku", {}).get(sku)
            if (isinstance(item, dict) and type(item.get("units")) is int and item["units"] > 0
                    and type(item.get("shops")) is int and item["shops"] > 0):
                public_stock = {**item, "observed_at": stock["observed_at"],
                                "age_seconds": max(0, now - stock["observed_at"])}
                ratio = len(rows) / item["units"]
        result[sku] = {"sales": len(rows), "buyers": len(buyers),
                       "buyer_known_sales": sum(buyers.values()),
                       "max_buyer_share": max(buyers.values()) / sum(buyers.values()) if buyers else None,
                       "shops": len({e["shop"] for e in rows}),
                       "median_price": int(median(e["price"] for e in rows)),
                       "first_ts": first, "last_ts": last, "age_seconds": max(0, now - last),
                       "window_seconds": window, "coverage": "sampled_observed_settlements",
                       "observed_stock": public_stock, "sample_to_stock_ratio": ratio,
                       "comparables": comparables, "evidence_id": evidence_id(rows)}
    return result
