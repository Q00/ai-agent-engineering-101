"""Event collector and the only writer of memory/summary.json. Runs inside the restock hook.

    sync(deadline) -> stats
      1. kit files by offset: .market/payments.jsonl, .market/broken.jsonl, logs/acp-sessions.jsonl,
         logs/decisions/{buyer,review,seller}.jsonl
      2. the public ledger by our own cursor: GET /api/ledger?after=N, 500 rows a page
      3. our events (trust 1) appended to memory/events.jsonl with deterministic ids, so a rerun
         after a crash adds nothing twice; market-wide rows only as counters in memory/cursor.json
      4. memory/summary.json rendered from a summary state kept in cursor.json: each event is folded in once,
         aged parts are pruned into counters, so a run never re-reads events.jsonl (only a crashed run's tail)

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
STATE_V = 1                                                  # layout of cursor.json's summary_state
IDS_KEEP_S = 2 * 3600                                        # dedupe horizon for event ids (see fold)
PURCHASE_KEEP_S = BREAK_WINDOW_S + 3 * 3600                  # a breakage frozen at BROKEN_FINAL_S still sees its buy
BROKEN_FINAL_S = 2 * 3600                                    # a breakage's payment, deal and ledger rows are in by then
SALE_KEEP_S = 25 * 3600                                      # sales_per_h and sold_24h look back 24 h
SESSION_FINAL_S = 2 * 3600                                   # a checkout has its outcome long before
PRUNE_EVERY_S = 3600                                         # prune even while the ledger is behind
MIGRATE_HOOK_BYTES = 20 * 2 ** 20                            # a first fold this big waits for python memory/ingest.py
# tools/negotiation.NO_SIGNED_RECEIPT (a test keeps them equal); importing negotiation would load model.harness,
# which makes a temp dir on import, into every restock run
NO_SIGNED_RECEIPT = "completed without a signed receipt"


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
            # tries that agreed but left no receipt the kit could pay: failed shop attempts (payments never sees them)
            fails = [{"shop": str(t["shop"]).rstrip("/"),
                      "why": "receipt" if t.get("receipt_problem") else "no signed receipt"}
                     for t in (r["tried"] if isinstance(r.get("tried"), list) else [])
                     if isinstance(t, dict) and t.get("shop")
                     and (t.get("receipt_problem") or t.get("why") == NO_SIGNED_RECEIPT)]
            if info["order"]:
                out.append({"id": f"e-order-{info['order']}", "ts": _num(r.get("ts")) or now, "source": "decisions",
                            "trust": 1, "kind": "order_seen", "decision": r.get("id"),
                            **{k: v for k, v in info.items() if k != "order" and v is not None},
                            **({"try_fail": fails} if fails else {}), "order": info["order"]})
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
    """New ledger rows since the cursor, page by page until caught up or out of time: (events, pages, caught_up).
    First run: note the newest seq as round3_start_seq and backfill only the newest 500 rows (never walk earlier
    rounds)."""
    market = cursor.setdefault("market", {})
    events, pages, caught_up = [], 0, False
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
            caught_up = True
            break
    cut = str(int(time.time() // 3600) - KEEP_HOURS)
    for leaf, b in list(market.get("cat_hour", {}).items()):
        for h in [h for h in b if int(h) < int(cut)]:
            del b[h]
        if not b:
            del market["cat_hour"][leaf]
    return events, pages, caught_up


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


# --- the summary state: fold each event once, prune aged parts into counters, render the summary

def new_state():
    """Hot parts (joins still waiting for partner rows) and frozen counters. Lives in cursor.json."""
    return {"v": STATE_V, "offset": 0, "n": 0, "pruned_at": 0, "ids": {},
            "shops": {}, "pay": {}, "deal": {}, "led": {}, "broken": [], "bshops": {}, "attr": [],
            "uwh": {}, "ratio": {}, "orders": [],
            "sales": {}, "skus": {}, "customers": {}, "last_sale": None,
            "sess": {}, "l3": {}, "stars": []}


def _add(d, k, n=1):
    d[k] = d.get(k, 0) + n


def _shop(st, shop):
    return st["shops"].setdefault(shop, {"attempts": 0, "paid": 0, "refused": {}, "bind_mismatch": 0,
                                         "broken_confirmed": 0, "broken_inferred": 0})


def _medians(demand):
    return {_leaf(d.get("category")): d.get("median_price") for d in demand}


def fold(st, e, demand_median, now):
    """One event into the state; False when its id was already folded. Ids are kept IDS_KEEP_S only: the same
    id comes back minutes apart (a crashed run's tail, a reread kit file), not hours, unless a kit file is
    replaced (read_new restarts it at 0); that case would count rows older than 2 h twice (accepted)."""
    if not isinstance(e, dict) or not e.get("id") or e["id"] in st["ids"]:
        return False
    st["ids"][e["id"]] = now
    st["n"] += 1
    k = e.get("kind")
    if k == "payment":
        s = _shop(st, e.get("shop") or "")
        s["attempts"] += 1
        if e.get("paid"):
            s["paid"] += 1
            if e.get("order"):
                st["pay"][e["order"]] = {"shop": e.get("shop"), "item": e.get("item"), "ts": e.get("ts")}
        else:
            reason = str(e.get("reason") or "unknown").removeprefix("not paying: ")  # acp_pay's refusals (kit why[])
            _add(s["refused"], reason.split(":")[0].strip()[:60] or "unknown")
            if reason.startswith("binding"):
                s["bind_mismatch"] += 1
    elif k == "deal" and e.get("order"):
        st["deal"][e["order"]] = {x: e.get(x) for x in ("sku", "shop", "name", "ts")}
    elif k == "purchase" and e.get("order"):
        st["led"][e["order"]] = {x: e.get(x) for x in ("sku", "name", "ts")}
    elif k == "broken":
        st["broken"].append({"sku": e.get("sku"), "ts": e.get("ts"), "id": e["id"]})
    elif k == "broken_file":
        st["broken"].append({"name": e.get("name"), "ts": e.get("ts"), "id": e["id"], "file": True})
    elif k == "order_seen":
        for f in e.get("try_fail") or []:
            s = _shop(st, f.get("shop") or "")
            s["attempts"] += 1
            _add(s["refused"], f.get("why") or "unknown")
        leaf = _leaf(e.get("category"))
        if leaf:
            u, r = st["uwh"].setdefault(leaf, {}), st["ratio"].setdefault(leaf, {})
            if e.get("uwh") is not None:
                _add(u, repr(float(e["uwh"])))
            ratio = e.get("budget_ratio")
            if ratio is None and e.get("budget") and demand_median.get(leaf):
                ratio = e["budget"] / demand_median[leaf]
            if ratio is not None:
                _add(r, repr(round(float(ratio), 3)))        # 3 decimals keep the counter bounded
            st["orders"].append([leaf, e.get("ts")])
    elif k == "acp":
        if e.get("ev") == "complete":
            st["sales"].setdefault(str(e.get("ledger") or e["id"]), {})["a"] = \
                [e.get("sku"), e.get("ts"), e.get("price"), e.get("ledger")]
        if e.get("ev") == "refuse":
            _add(st["l3"].setdefault("all", {}), "refused")
        else:
            s = st["sess"].setdefault(e.get("sid") or e["id"], {"t": e.get("ts"), "evs": []})
            if e.get("ev") == "create":
                s["c"] = {x: e.get(x) for x in ("goods_kind", "grade", "hours_left", "warranty_h", "ts")}
            if e.get("ev") not in s["evs"]:
                s["evs"].append(e.get("ev"))
    elif k == "sale":
        st["sales"].setdefault(str(e.get("seq")), {})["l"] = [e.get("sku"), e.get("ts"), e.get("amount"),
                                                              e.get("buyer")]
    elif k == "rated" and e.get("stars"):
        st["stars"] = (st["stars"] + [e["stars"]])[-50:]
    return True


def _sale(s):
    """One sale, one per ledger seq: the kit's complete row (a) and the public settle row (l) are the same sale."""
    a, l = s.get("a"), s.get("l")
    if l is None:
        return {"sku": a[0], "ts": a[1], "price": a[2], "buyer": None}
    return {"sku": l[0] or (a and a[0]), "ts": (a and a[1]) or l[1], "price": l[2] or (a and a[2]), "buyer": l[3]}


def _purchases(st, catalog):
    out = []
    for order, p in st["pay"].items():
        dl, lg = st["deal"].get(order, {}), st["led"].get(order, {})
        sku = dl.get("sku") or lg.get("sku") or (p.get("item") if p.get("item") in catalog else None)
        out.append({"order": order, "shop": p.get("shop") or dl.get("shop"), "sku": sku,
                    "name": dl.get("name") or lg.get("name") or (catalog.get(sku) or {}).get("name"),
                    "ts": lg.get("ts") or dl.get("ts") or p.get("ts")})
    return out


def _attribute(brokens, purchases, catalog):
    """Attributions of ledger breakages and of kit rows with no ledger twin within 2 s; purchases indexed by sku
    and by name, so the cost is not breakages x purchases."""
    led = [b for b in brokens if not b.get("file")]
    rows = led + [f for f in brokens if f.get("file") and not any(
        abs((b["ts"] or 0) - (f["ts"] or 0)) <= 2 and (catalog.get(b.get("sku")) or {}).get("name") == f["name"]
        for b in led)]
    by_sku, by_name = {}, {}
    for p in purchases:
        by_sku.setdefault(p.get("sku"), []).append(p)
        if p.get("name"):
            by_name.setdefault(p["name"], []).append(p)
    return [{"event": b["id"], **attribute_broken(b, by_sku.get(b["sku"], []) if b.get("sku") else
                                                  by_name.get(b.get("name"), []))} for b in rows]


def _count_attr(a, counts):
    if a["status"] == "confirmed":
        counts.setdefault(a["shop"], [0, 0])[0] += 1
    elif a["status"] == "inferred":
        for s in a["shops"]:
            counts.setdefault(s, [0, 0])[1] += 1


def _outcome(s, now):
    evs = s["evs"]
    return "completed" if "complete" in evs else "canceled" if "cancel" in evs else \
           "expired" if "expire" in evs else "dangling" if now - s["c"]["ts"] > DANGLING_S else "open"


def _count_l3(l3, s, now):
    for b in ("all", _l3_bin(s["c"])):
        t = l3.setdefault(b, {})
        _add(t, "created")
        _add(t, _outcome(s, now))


def prune(st, catalog, now):
    """Freeze what can no longer change into counters and drop what no render needs."""
    # a ledger breakage stays 5 s longer than a kit row, so a kit row never outlives its ledger twin (2 s apart)
    old = [b for b in st["broken"] if (b.get("ts") or 0) < now - BROKEN_FINAL_S - (0 if b.get("file") else 5)]
    if old:
        gone = {b["id"] for b in old}
        hot = [b for b in st["broken"] if b["id"] not in gone]
        for a in _attribute(old + [b for b in hot if not b.get("file")], _purchases(st, catalog), catalog):
            if a["event"] in gone:
                _count_attr(a, st["bshops"])
                st["attr"] = (st["attr"] + [a])[-200:]
        st["broken"] = hot
    for name in ("pay", "deal", "led"):
        st[name] = {o: p for o, p in st[name].items() if (p.get("ts") or 0) >= now - PURCHASE_KEEP_S}
    for key in [k for k, s in st["sales"].items() if (_sale(s)["ts"] or 0) < now - SALE_KEEP_S]:
        s = _sale(st["sales"].pop(key))
        k = st["skus"].setdefault(s["sku"] or "", [0, None])
        k[0] += 1
        k[1] = max(k[1] or 0, s["ts"] or 0)
        if s["buyer"]:
            _add(st["customers"], s["buyer"])
        st["last_sale"] = max(st["last_sale"] or 0, s["ts"] or 0)
    for sid in [k for k, s in st["sess"].items()
                if ((s.get("c") or {}).get("ts") or s["t"] or 0) < now - SESSION_FINAL_S]:
        s = st["sess"].pop(sid)
        if s.get("c"):
            _count_l3(st["l3"], s, now)
    st["orders"] = [o for o in st["orders"] if (o[1] or 0) >= now - KEEP_HOURS * 3600]
    st["ids"] = {i: t for i, t in st["ids"].items() if t >= now - IDS_KEEP_S}
    st["pruned_at"] = now


def _values(counter):
    return [float(v) for v, n in counter.items() for _ in range(n)]


def render(st, cursor, catalog, window_h, now):
    """The summary dict from the state and the market counters. Pure: the state is not changed."""
    shops = {k: dict(v, refused=dict(v["refused"])) for k, v in st["shops"].items()}
    counts = {k: list(v) for k, v in st["bshops"].items()}
    hot_attr = _attribute(st["broken"], _purchases(st, catalog), catalog)
    for a in hot_attr:
        _count_attr(a, counts)
    for s, (c, i) in counts.items():
        if s in shops:
            shops[s]["broken_confirmed"] += c
            shops[s]["broken_inferred"] += i

    category = {}
    for leaf in set(st["uwh"]) | set(st["ratio"]):
        u, r = _values(st["uwh"].get(leaf, {})), _values(st["ratio"].get(leaf, {}))
        category[leaf] = {"uwh": {"n": len(u), "median": statistics.median(u) if u else None},
                          "budget_ratio": {"n": len(r), "p25": _pct(r, 0.25), "p50": _pct(r, 0.5)}}
    for leaf, (n, k) in cursor.get("market", {}).get("cat_goods", {}).items():
        category.setdefault(leaf, {})["q"] = {"n": n, "upper": _wilson_upper(min(k, n), n)}

    started = cursor.get("started_at") or now
    hours = min(24.0, max(1.0, (now - started) / 3600))
    skus = {k: {"settled": v[0], "last_sale_ts": v[1], "recent": 0} for k, v in st["skus"].items()}
    customers = dict(st["customers"])
    hot = [_sale(s) for s in st["sales"].values()]
    for s in hot:
        k = skus.setdefault(s["sku"] or "", {"settled": 0, "last_sale_ts": None, "recent": 0})
        k["settled"] += 1
        k["last_sale_ts"] = max(k["last_sale_ts"] or 0, s["ts"])
        if s["ts"] >= now - 24 * 3600:
            k["recent"] += 1
        if s["buyer"]:
            _add(customers, s["buyer"])
    for k in skus.values():
        k["sales_per_h"] = round(k.pop("recent") / hours, 4)
    weight = {}
    buyer_cats = cursor.get("market", {}).get("buyer_cats", {})
    for b, n in customers.items():
        if n < 2:
            continue
        top = sorted(buyer_cats.get(b, {}).items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        for leaf, m in top:
            weight[leaf] = weight.get(leaf, 0) + m
    regular = [leaf for leaf, _ in sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))][:10]

    l3 = {b: dict(t) for b, t in st["l3"].items()}
    for s in st["sess"].values():
        if s.get("c"):
            _count_l3(l3, s, now)

    # market: L1 demand = public settles + our buyer's orders in the window; supply unknown in P0
    hour_cut = int((now - window_h * 3600) // 3600)
    market_cats = {}
    for leaf, b in cursor.get("market", {}).get("cat_hour", {}).items():
        market_cats.setdefault(leaf, {"demand": 0.0, "supply": None})["demand"] += \
            sum(n for h, n in b.items() if int(h) >= hour_cut)
    for leaf, ts in st["orders"]:
        if ts >= now - window_h * 3600:
            market_cats.setdefault(leaf, {"demand": 0.0, "supply": None})["demand"] += 1

    last_sale = max([s["ts"] for s in hot] + ([st["last_sale"]] if st["last_sale"] else []), default=None)
    return {"built_at": round(now, 3), "ledger_seq": cursor.get("ledger_seq"), "started_at": started,
            "events": st["n"],
            "buyer": {"shops": shops, "category": category, "broken": (st["attr"] + hot_attr)[-200:]},
            "seller": {"skus": {k: v for k, v in skus.items() if k}, "customers": customers, "regular_cats": regular,
                       "l3": l3, "last_sale_ts": last_sale,
                       "sold_24h": sum(1 for s in hot if s["ts"] >= now - 24 * 3600),
                       "stars": list(st["stars"])},
            "market": {"categories": market_cats, "window_h": window_h}}


def build_summary(events, cursor, catalog, demand, window_h, now):
    """The whole summary from a list of events and the market counters (tests and one-off rebuilds)."""
    st, med = new_state(), _medians(demand)
    for e in events:
        fold(st, e, med, now)
    return render(st, cursor, catalog, window_h, now)


# --- the run

def _size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def sync(deadline, state=None, window_h=6, migrate=False):
    """Collect new events and render the summary before `deadline` (epoch s). Skips when another ingest holds
    the lock, and in the hook (migrate False) when there is no summary state yet and events.jsonl is too big to
    fold in one hook run: `python memory/ingest.py` does that once. Returns a small stats dict for the restock
    decision row."""
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
        st, size = cursor.get("summary_state"), _size(EVENTS)
        if not isinstance(st, dict) or st.get("v") != STATE_V or st.get("offset", 0) > size:
            if size > MIGRATE_HOOK_BYTES and not migrate:
                hook_log("ingest", f"no summary state and events.jsonl has {size} bytes: run python memory/ingest.py")
                return {"skipped": "migration_pending"}
            st = new_state()
        catalog = load_catalog(state)
        med = _medians(load_demand(state))
        rows, st["offset"] = read_new(EVENTS, st["offset"])    # all on the first run, else a crashed run's tail
        for e in rows:
            fold(st, e, med, now)

        new = []
        for name, path in kit_files(state):
            rows, cursor["files"][name] = read_new(path, cursor["files"].get(name, 0))
            new += file_events(name, rows, now)
        url = own_url()
        me = {NAME} | ({url} if url else set())
        our_ledgers = {s["a"][3] for s in st["sales"].values() if s.get("a")} | \
                      {e.get("ledger") for e in new if e.get("kind") == "acp" and e.get("ev") == "complete"}
        led, pages, caught_up = pull_ledger(cursor, deadline, catalog, me, our_ledgers - {None})
        new += led

        added = 0
        for e in new:
            if fold(st, e, med, now):
                added += 1
                append_jsonl(EVENTS, e)                     # events first: a rerun folds this tail once
        st["offset"] = _size(EVENTS)
        if caught_up or now - st["pruned_at"] > PRUNE_EVERY_S:   # a ledger backlog may still hold partner rows
            prune(st, catalog, now)
        cursor["summary_state"] = st
        atomic_write_json(CURSOR, cursor)                   # then the cursor: counters and state commit together
        summary = render(st, cursor, catalog, window_h, time.time())
        atomic_write_json(SUMMARY, summary)
        stats = {"new_events": added, "events": summary["events"], "ledger_seq": cursor.get("ledger_seq"),
                 "pages": pages, "ms": int((time.time() - now) * 1000)}
        hook_log("ingest", f"ok {stats}")
        return stats
    finally:
        lock.close()


if __name__ == "__main__":
    import json
    print(json.dumps(sync(time.time() + 20, migrate=True)))       # also the one-time fold of an old events.jsonl
