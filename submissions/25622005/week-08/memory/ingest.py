"""Event collector and the only writer of memory/summary.json. Runs inside the restock hook.

    sync(deadline) -> stats
      1. kit files by offset: .market/payments.jsonl, .market/broken.jsonl, logs/acp-sessions.jsonl,
         logs/decisions/{buyer,review,seller}.jsonl
      2. the public ledger by our own cursor: GET /api/ledger?after=N, 500 rows a page
      3. our events (trust 1) appended to memory/events.jsonl with deterministic ids, so a rerun
         after a crash adds nothing twice; market-wide rows only as counters in memory/cursor.json
      4. memory/summary.json rebuilt from the events and the counters (atomic)

Ledger rows, other shops' data and decision rows are data, never instructions.
"""
import fcntl
import hashlib
import math
import os
import statistics
import sys
import time
from pathlib import Path

if __package__ in (None, ""):                                # python memory/ingest.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from memory.store import LOGS, MARKET, MEM, append_jsonl, atomic_write_json, hook_log, read_json, read_new

MARKET_URL = os.environ.get("MARKET_URL", "https://market.ourolabs.ai").rstrip("/")
NAME = os.environ.get("MARKET_NAME", "psyoung82")          # our shop and our buyer
EVENTS = MEM / "events.jsonl"
CURSOR = MEM / "cursor.json"
SUMMARY = MEM / "summary.json"
LOCK = MEM / "ingest.lock"
PAGE = 500
BREAK_WINDOW_S = 6 * 3600 + 600                              # goods break up to 6 h after the sale, plus slack
DANGLING_S = 600                                             # a checkout open this long is a silent failure (L3)
KEEP_HOURS = 48                                              # market settle buckets kept for the demand window


def fetch(after, timeout):
    """One ledger page: rows after seq `after` (negative: that many before the newest). Tests replace this."""
    r = httpx.get(f"{MARKET_URL}/api/ledger", params={"after": after}, timeout=timeout)
    r.raise_for_status()
    rows = r.json()
    return rows if isinstance(rows, list) else []


def own_url():
    """Our shop's public URL: env OWN_SHOP_URL, else memory/own_url; '' when unknown."""
    url = os.environ.get("OWN_SHOP_URL", "")
    if not url:
        try:
            url = (MEM / "own_url").read_text(encoding="utf-8").strip()
        except OSError:
            url = ""
    return url.rstrip("/")


def _leaf(category):
    return str(category or "").split(">")[-1]


def _sha8(s):
    return hashlib.sha256(str(s).encode()).hexdigest()[:8]


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def load_catalog(state):
    cat = read_json(Path(state) / "catalog.json", {})
    if isinstance(cat, list):
        cat = {x.get("sku"): x for x in cat if isinstance(x, dict)}
    return cat if isinstance(cat, dict) else {}


def load_demand(state):
    dem = read_json(Path(state) / "demand.json", [])
    return [d for d in dem if isinstance(d, dict)] if isinstance(dem, list) else []


# --- kit files -> our events

def _order_info(row):
    """Order fields from a buyer decision row; the buyer may put them in ref, inputs or inputs.order."""
    ref, inp = row.get("ref") or {}, row.get("inputs") or {}
    o = inp.get("order") if isinstance(inp.get("order"), dict) else {}
    pick = lambda *keys: next((src[k] for k in keys for src in (o, inp, ref, row)
                               if isinstance(src, dict) and src.get(k) is not None), None)
    oid = ref.get("order") if isinstance(ref.get("order"), str) else pick("order")
    return {"order": oid if isinstance(oid, str) else None, "category": pick("category", "item"),
            "budget": _num(pick("budget")), "uwh": _num(pick("use_within_hours", "uwh")),
            "food": pick("kind") == "food", "budget_ratio": _num(pick("budget_ratio")),
            "shop": (row.get("ref") or {}).get("shop")}


def file_events(name, rows, now):
    """Our events from new rows of one kit file."""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if name == "payments" and r.get("order"):
            out.append({"id": f"e-pay-{r['order']}", "ts": now, "source": "payments", "trust": 1, "kind": "payment",
                        "order": r["order"], "shop": str(r.get("shop") or "").rstrip("/"), "item": r.get("item"),
                        "price": r.get("price"), "paid": bool(r.get("paid")), "ledger": r.get("ledger"),
                        "reason": r.get("reason")})
        elif name == "broken" and r.get("name"):
            ts = _num(r.get("broke_at")) or now
            out.append({"id": f"e-brokenfile-{int(ts)}-{_sha8(r['name'])}", "ts": ts, "source": "broken",
                        "trust": 1, "kind": "broken_file", "name": r["name"]})
        elif name == "acp" and r.get("event"):
            key = r.get("sid") or r.get("task") or "none"
            ts = _num(r.get("ts")) or now
            ev = {"id": f"e-acp-{key}-{r['event']}-{int(ts * 1000)}", "ts": ts, "source": "acp-sessions",
                  "trust": 1, "kind": "acp", "ev": r["event"]}
            for k in ("sid", "task", "sku", "unit", "price", "grade", "hours_left", "warranty_h", "cert",
                      "claim", "ledger", "error"):
                if r.get(k) is not None:
                    ev[k] = r[k]
            if r.get("kind") is not None:
                ev["goods_kind"] = r["kind"]
            out.append(ev)
        elif name == "buyer" and r.get("kind") == "order":
            info = _order_info(r)
            if info["order"]:
                out.append({"id": f"e-order-{info['order']}", "ts": _num(r.get("ts")) or now, "source": "decisions",
                            "trust": 1, "kind": "order_seen", "decision": r.get("id"),
                            **{k: v for k, v in info.items() if k != "order" and v is not None},
                            "order": info["order"]})
        elif name == "review" and r.get("kind") not in ("policy_error",):
            oid = (r.get("ref") or {}).get("order") or (r.get("inputs") or {}).get("order")
            if isinstance(oid, dict):
                oid = oid.get("order")
            if not isinstance(oid, str):
                continue
            deal = read_json(MEM / "deals" / f"{oid}.json", {}) or {}
            st = deal.get("settled") if isinstance(deal.get("settled"), dict) else (r.get("inputs") or {})
            ev = {"id": f"e-deal-{oid}", "ts": _num(r.get("ts")) or now, "source": "review", "trust": 1,
                  "kind": "deal", "order": oid, "decision": r.get("id"),
                  "stars": (r.get("choice") or {}).get("stars") if isinstance(r.get("choice"), dict) else None}
            for k in ("shop", "unit", "sku", "name", "grade", "claim", "claim_honest", "expires_at",
                      "warranty_hours", "value", "ledger"):
                if st.get(k) is not None:
                    ev[k] = st[k].rstrip("/") if k == "shop" and isinstance(st[k], str) else st[k]
            rc = deal.get("receipt") if isinstance(deal.get("receipt"), dict) else {}
            price = rc.get("price", (r.get("inputs") or {}).get("price"))
            if price is not None:
                ev["price"] = price
            out.append(ev)
        elif name == "seller" and r.get("kind") in ("sign", "reject") and r.get("id"):
            ref = r.get("ref") or {}
            out.append({"id": f"e-dec-{r['id']}", "ts": _num(r.get("ts")) or now, "source": "decisions",
                        "trust": 1, "kind": f"seller_{r['kind']}",
                        **{k: ref[k] for k in ("task", "sku", "unit", "order") if ref.get(k) is not None},
                        "choice": r.get("choice")})
    return out


def kit_files(state):
    return [("payments", Path(state) / "payments.jsonl"), ("broken", Path(state) / "broken.jsonl"),
            ("acp", LOGS / "acp-sessions.jsonl"), ("buyer", LOGS / "decisions" / "buyer.jsonl"),
            ("review", LOGS / "decisions" / "review.jsonl"), ("seller", LOGS / "decisions" / "seller.jsonl")]


# --- ledger -> our events + market counters

def ledger_rows(row, catalog, market, me, our_ledgers):
    """Count one public ledger row into `market` and return our events from it."""
    d = row.get("data") if isinstance(row.get("data"), dict) else {}
    kind, seq, ts = row.get("kind"), row.get("seq"), _num(row.get("ts")) or time.time()
    shop = str(d.get("shop") or d.get("seller") or "").rstrip("/")
    ours_shop = bool(shop) and shop in me or seq in our_ledgers
    base = {"id": f"e-ledger-{seq}", "ts": ts, "source": "ledger", "trust": 1, "seq": seq}
    sku = d.get("item")
    leaf = _leaf((catalog.get(sku) or {}).get("category")) if sku in catalog else None
    if kind == "settle":
        if leaf:
            hour = str(int(ts // 3600))
            bucket = market.setdefault("cat_hour", {}).setdefault(leaf, {})
            bucket[hour] = bucket.get(hour, 0) + 1
            if (catalog.get(sku) or {}).get("kind") == "goods":
                g = market.setdefault("cat_goods", {}).setdefault(leaf, [0, 0])
                g[0] += 1
            if d.get("buyer"):
                bc = market.setdefault("buyer_cats", {}).setdefault(d["buyer"], {})
                bc[leaf] = bc.get(leaf, 0) + 1
        fields = {k: d.get(k) for k in ("order", "buyer", "amount", "name", "grade", "claim", "claim_honest")
                  if d.get(k) is not None}
        if ours_shop:
            return [{**base, "kind": "sale", "sku": sku, "category": leaf, "shop": shop, **fields}]
        if d.get("buyer") == NAME:
            return [{**base, "kind": "purchase", "sku": sku, "category": leaf, "shop": shop, **fields}]
        return []
    if kind == "broken":
        if leaf and (catalog.get(sku) or {}).get("kind") == "goods":
            g = market.setdefault("cat_goods", {}).setdefault(leaf, [0, 0])
            g[1] += 1
        if d.get("buyer") == NAME:
            return [{**base, "kind": "broken", "sku": sku, "category": leaf, "shop": shop or None}]
        if ours_shop:
            return [{**base, "kind": "sold_broken", "sku": sku, "category": leaf, "buyer": d.get("buyer")}]
        return []
    if kind == "result":
        if ours_shop:
            return [{**base, "kind": "rated", "buyer": d.get("buyer"), "order": d.get("order"),
                     "stars": d.get("stars"), "verified": d.get("verified")}]
        return []
    if d.get("buyer") == NAME or (shop and shop in me) or d.get("name") == NAME:
        return [{**base, "kind": f"ledger_{kind}", "raw": d}]       # unknown kinds that name us: keep as is
    return []


def pull_ledger(cursor, deadline, catalog, me, our_ledgers):
    """New ledger rows since the cursor, page by page until caught up or out of time. First run: note the newest
    seq as round3_start_seq and backfill only the newest 500 rows (never walk earlier rounds)."""
    market = cursor.setdefault("market", {})
    events, pages = [], 0
    while time.time() < deadline - 1:
        timeout = max(1.0, min(5.0, deadline - time.time() - 0.5))
        try:
            if cursor.get("ledger_seq") is None:
                newest = fetch(-1, timeout)
                cursor["round3_start_seq"] = newest[-1]["seq"] if newest else 0
                rows = fetch(-PAGE, timeout) if newest else []
            else:
                rows = fetch(cursor["ledger_seq"], timeout)
        except Exception as e:                                      # network or a bad page: keep what we have
            hook_log("ingest", f"ledger fetch: {type(e).__name__}: {e}")
            break
        pages += 1
        rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get("seq"), int)]
        for r in rows:
            if not isinstance(r.get("round"), int) or r["round"] >= 3:
                events += ledger_rows(r, catalog, market, me, our_ledgers)
        if rows:
            cursor["ledger_seq"] = max(cursor.get("ledger_seq") or 0, rows[-1]["seq"])
        elif cursor.get("ledger_seq") is None:
            cursor["ledger_seq"] = cursor.get("round3_start_seq", 0)
        if len(rows) < PAGE:
            break
    cut = str(int(time.time() // 3600) - KEEP_HOURS)
    for leaf, b in list(market.get("cat_hour", {}).items()):
        for h in [h for h in b if int(h) < int(cut)]:
            del b[h]
        if not b:
            del market["cat_hour"][leaf]
    return events, pages


# --- attribution and the summary

def attribute_broken(row, purchases):
    """Which purchase broke: confirmed (one candidate), inferred (several), none.
    row: {"sku" or "name", "ts"}; purchases: [{"order", "sku", "name", "shop", "ts"}]."""
    ts = row.get("ts") or 0
    if row.get("sku"):
        same = lambda p: p.get("sku") == row["sku"]
    else:
        same = lambda p: bool(row.get("name")) and p.get("name") == row.get("name")
    cands = [p for p in purchases if same(p) and ts - BREAK_WINDOW_S <= (p.get("ts") or 0) <= ts + 60]
    shops = {p.get("shop") for p in cands}
    status = "confirmed" if len(cands) == 1 else "inferred" if cands else "none"
    return {"status": status, "candidates": [p.get("order") for p in cands],
            "shop": next(iter(shops)) if len(shops) == 1 else None, "shops": sorted(s for s in shops if s)}


def _wilson_upper(k, n, z=1.645):
    if n <= 0:
        return None
    p = k / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(min(1.0, (centre + spread) / (1 + z * z / n)), 4)


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (i - lo), 4)


def _l3_bin(c):
    kind, grade, hl = c.get("goods_kind"), c.get("grade"), c.get("hours_left")
    if kind == "goods" and grade in ("A", "B") and not c.get("warranty_h"):
        return "goods_ab_no_warranty"
    if kind == "goods" and grade == "C":
        return "goods_c"
    if kind == "food" and isinstance(hl, (int, float)) and hl < 3:
        return "food_lt3h"
    if kind == "food" and grade == "C":
        return "food_c"
    return "other"


def build_summary(events, cursor, catalog, demand, window_h, now):
    """The whole summary from our events and the market counters. Pure: same input, same output."""
    by = {}
    for e in events:
        by.setdefault(e.get("kind"), []).append(e)
    median_price = {_leaf(d.get("category")): d.get("median_price") for d in demand}

    # buyer: shops
    shops = {}
    for p in by.get("payment", []):
        s = shops.setdefault(p.get("shop") or "", {"attempts": 0, "paid": 0, "refused": {}, "bind_mismatch": 0,
                                                    "broken_confirmed": 0, "broken_inferred": 0})
        s["attempts"] += 1
        if p.get("paid"):
            s["paid"] += 1
        else:
            reason = str(p.get("reason") or "unknown")
            reason = reason.removeprefix("not paying: ")     # acp_pay's accept/binding refusals (kit why[])
            key = reason.split(":")[0].strip()[:60] or "unknown"
            s["refused"][key] = s["refused"].get(key, 0) + 1
            if reason.startswith("binding"):
                s["bind_mismatch"] += 1

    # buyer: purchases and breakage attribution
    deals = {e["order"]: e for e in by.get("deal", []) if e.get("order")}
    led = {e["order"]: e for e in by.get("purchase", []) if e.get("order")}
    purchases = []
    for p in by.get("payment", []):
        if not p.get("paid"):
            continue
        dl, lg = deals.get(p["order"], {}), led.get(p["order"], {})
        sku = dl.get("sku") or lg.get("sku") or (p.get("item") if p.get("item") in catalog else None)
        purchases.append({"order": p["order"], "shop": p.get("shop") or dl.get("shop"), "sku": sku,
                          "name": dl.get("name") or lg.get("name") or (catalog.get(sku) or {}).get("name"),
                          "ts": lg.get("ts") or dl.get("ts") or p.get("ts")})
    broken = [{"sku": e.get("sku"), "ts": e.get("ts"), "id": e["id"]} for e in by.get("broken", [])]
    for f in by.get("broken_file", []):
        if not any(abs((b["ts"] or 0) - f["ts"]) <= 2 and (catalog.get(b["sku"]) or {}).get("name") == f["name"]
                   for b in broken):
            broken.append({"name": f["name"], "ts": f["ts"], "id": f["id"]})
    attributions = []
    for b in broken:
        a = attribute_broken(b, purchases)
        attributions.append({"event": b["id"], **a})
        if a["status"] == "confirmed" and a["shop"] in shops:
            shops[a["shop"]]["broken_confirmed"] += 1
        elif a["status"] == "inferred":
            for s in a["shops"]:
                if s in shops:
                    shops[s]["broken_inferred"] += 1

    # buyer: categories
    cats = {}
    for o in by.get("order_seen", []):
        leaf = _leaf(o.get("category"))
        if not leaf:
            continue
        c = cats.setdefault(leaf, {"uwh": [], "ratio": []})
        if o.get("uwh") is not None:
            c["uwh"].append(o["uwh"])
        ratio = o.get("budget_ratio")
        if ratio is None and o.get("budget") and median_price.get(leaf):
            ratio = o["budget"] / median_price[leaf]
        if ratio is not None:
            c["ratio"].append(ratio)
    category = {}
    for leaf, c in cats.items():
        category[leaf] = {"uwh": {"n": len(c["uwh"]), "median": statistics.median(c["uwh"]) if c["uwh"] else None},
                          "budget_ratio": {"n": len(c["ratio"]), "p25": _pct(c["ratio"], 0.25),
                                           "p50": _pct(c["ratio"], 0.5)}}
    for leaf, (n, k) in cursor.get("market", {}).get("cat_goods", {}).items():
        category.setdefault(leaf, {})["q"] = {"n": n, "upper": _wilson_upper(min(k, n), n)}

    # seller: sales, one per ledger seq (the kit's complete row and the public settle row are the same sale)
    sales = {}
    for e in by.get("acp", []):
        if e.get("ev") == "complete":
            key = e.get("ledger") or e["id"]
            sales[key] = {"sku": e.get("sku"), "ts": e["ts"], "price": e.get("price"), "buyer": None}
    for e in by.get("sale", []):
        old = sales.get(e["seq"], {})
        sales[e["seq"]] = {"sku": e.get("sku") or old.get("sku"), "ts": old.get("ts") or e["ts"],
                           "price": e.get("amount") or old.get("price"), "buyer": e.get("buyer")}
    started = cursor.get("started_at") or now
    hours = min(24.0, max(1.0, (now - started) / 3600))
    skus = {}
    for s in sales.values():
        k = skus.setdefault(s["sku"], {"settled": 0, "last_sale_ts": None, "recent": 0})
        k["settled"] += 1
        k["last_sale_ts"] = max(k["last_sale_ts"] or 0, s["ts"])
        if s["ts"] >= now - 24 * 3600:
            k["recent"] += 1
    for k in skus.values():
        k["sales_per_h"] = round(k.pop("recent") / hours, 4)
    customers = {}
    for s in sales.values():
        if s.get("buyer"):
            customers[s["buyer"]] = customers.get(s["buyer"], 0) + 1
    weight = {}
    buyer_cats = cursor.get("market", {}).get("buyer_cats", {})
    for b, n in customers.items():
        if n < 2:
            continue
        top = sorted(buyer_cats.get(b, {}).items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        for leaf, m in top:
            weight[leaf] = weight.get(leaf, 0) + m
    regular = [leaf for leaf, _ in sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))][:10]

    # seller: L3 checkout outcomes per session
    sess = {}
    for e in by.get("acp", []):
        if e.get("ev") == "refuse":
            sess[e["id"]] = {"refused": True}
            continue
        s = sess.setdefault(e.get("sid") or e["id"], {})
        if e.get("ev") == "create":
            s.update(create=e)
        s.setdefault("evs", set()).add(e.get("ev"))
    l3 = {}
    for s in sess.values():
        if s.get("refused"):
            t = l3.setdefault("all", {})
            t["refused"] = t.get("refused", 0) + 1
            continue
        c = s.get("create")
        if not c:
            continue
        evs = s.get("evs", set())
        outcome = "completed" if "complete" in evs else "canceled" if "cancel" in evs else \
                  "expired" if "expire" in evs else "dangling" if now - c["ts"] > DANGLING_S else "open"
        for b in ("all", _l3_bin(c)):
            t = l3.setdefault(b, {})
            t["created"] = t.get("created", 0) + 1
            t[outcome] = t.get(outcome, 0) + 1

    # market: L1 demand = public settles + our buyer's orders in the window; supply unknown in P0
    hour_cut = int((now - window_h * 3600) // 3600)
    market_cats = {}
    for leaf, b in cursor.get("market", {}).get("cat_hour", {}).items():
        market_cats.setdefault(leaf, {"demand": 0.0, "supply": None})["demand"] += \
            sum(n for h, n in b.items() if int(h) >= hour_cut)
    for o in by.get("order_seen", []):
        leaf = _leaf(o.get("category"))
        if leaf and o["ts"] >= now - window_h * 3600:
            market_cats.setdefault(leaf, {"demand": 0.0, "supply": None})["demand"] += 1

    last_sale = max((s["ts"] for s in sales.values()), default=None)
    return {"built_at": round(now, 3), "ledger_seq": cursor.get("ledger_seq"), "started_at": started,
            "events": len(events),
            "buyer": {"shops": shops, "category": category, "broken": attributions[-200:]},
            "seller": {"skus": {k: v for k, v in skus.items() if k}, "customers": customers, "regular_cats": regular,
                       "l3": l3, "last_sale_ts": last_sale,
                       "sold_24h": sum(1 for s in sales.values() if s["ts"] >= now - 24 * 3600),
                       "stars": [e.get("stars") for e in by.get("rated", []) if e.get("stars")][-50:]},
            "market": {"categories": market_cats, "window_h": window_h}}


# --- the run

def sync(deadline, state=None, window_h=6):
    """Collect new events and rebuild the summary before `deadline` (epoch s). Skips when another ingest holds
    the lock. Returns a small stats dict for the restock decision row."""
    state = Path(state or MARKET)
    MEM.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            hook_log("ingest", "another ingest holds the lock, skipped")
            return {"skipped": "locked"}
        now = time.time()
        cursor = read_json(CURSOR, {}) or {}
        cursor.setdefault("started_at", now)
        cursor.setdefault("files", {})
        old, _ = read_new(EVENTS, 0)
        known = {e.get("id") for e in old if isinstance(e, dict)}
        catalog = load_catalog(state)

        new = []
        for name, path in kit_files(state):
            rows, cursor["files"][name] = read_new(path, cursor["files"].get(name, 0))
            new += file_events(name, rows, now)
        url = own_url()
        me = {NAME} | ({url} if url else set())
        our_ledgers = {e.get("ledger") for e in old + new if e.get("kind") == "acp" and e.get("ev") == "complete"}
        led, pages = pull_ledger(cursor, deadline, catalog, me, our_ledgers - {None})
        new += led

        added = []
        for e in new:
            if e["id"] not in known:
                known.add(e["id"])
                added.append(e)
                append_jsonl(EVENTS, e)                     # events first: a rerun dedupes by id
        atomic_write_json(CURSOR, cursor)                   # then the cursor with the market counters
        summary = build_summary([e for e in old if isinstance(e, dict)] + added, cursor, catalog,
                                load_demand(state), window_h, time.time())
        atomic_write_json(SUMMARY, summary)
        stats = {"new_events": len(added), "events": summary["events"], "ledger_seq": cursor.get("ledger_seq"),
                 "pages": pages, "ms": int((time.time() - now) * 1000)}
        hook_log("ingest", f"ok {stats}")
        return stats
    finally:
        lock.close()


if __name__ == "__main__":
    import json
    print(json.dumps(sync(time.time() + 20)))
