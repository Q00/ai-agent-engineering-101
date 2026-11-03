"""The buyer's Round 3 memory, beyond week 7's trade log (.market/buyer_memory.json).

- purchases.json: every settled purchase (shop, SKU, name, price, grade, warranty, ledger number, time), written
  by the review hook.
- broken.jsonl (from the kit): goods that broke, with only a name and a time. sync() matches each one to the
  latest purchase of that name before it broke and decides whether the shop's warranty covered it.
- payments.jsonl (from the kit): deals the kit refused to pay, with the reason (no certificate, too little shelf
  life, grade). sync() keeps those too.
notes(shop) turns all of it into one line for the shop-choosing prompt.
"""
import json
import re
import time
from pathlib import Path

import httpx

MD = Path(".market")
PURCHASES = MD / "purchases.json"
CURSOR = MD / "memory_cursor.json"
MARKET = "https://market.ourolabs.ai"
BREAK_WINDOW = 6 * 3600       # goods break at most 6 h after the sale


def _load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(p, data):
    p.parent.mkdir(exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _lines(p, start):
    try:
        rows = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], start
    out = []
    for line in rows[start:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out, len(rows)


def shop_name(url):
    """Settlements name the shop by URL; URLs change when a shop rejoins, so remember shops by name."""
    cache = MD / "shop_names.json"
    names = _load(cache, {})
    url = (url or "").rstrip("/")
    if url not in names:
        try:
            for s in httpx.get(f"{MARKET}/api/shops", timeout=10).json().get("shops", []):
                names[s["url"].rstrip("/")] = s["name"]
            _save(cache, names)
        except (httpx.HTTPError, ValueError):
            pass
    return names.get(url, url)


def add_purchase(p):
    rows = _load(PURCHASES, [])
    rows.append(p)
    _save(PURCHASES, rows[-2000:])


def sync():
    """Fold new broken.jsonl and payments.jsonl lines into purchases.json and refusals. Returns new events."""
    cur = _load(CURSOR, {"broken": 0, "payments": 0, "refusals": []})
    rows = _load(PURCHASES, [])
    events = []
    broken, cur["broken"] = _lines(MD / "broken.jsonl", cur["broken"])
    for b in broken:
        cands = [p for p in rows if p.get("name") == b["name"] and not p.get("broke_at")
                 and 0 <= b["broke_at"] - p["ts"] <= BREAK_WINDOW + 600]
        if not cands:
            events.append(f"broken {b['name']}: no matching purchase")
            continue
        p = max(cands, key=lambda p: p["ts"])
        p["broke_at"] = b["broke_at"]
        p["covered"] = (b["broke_at"] - p["ts"]) <= (p.get("warranty_hours") or 0) * 3600
        events.append(f"broken {p['name'][:30]} from {p['shop']}, warranty {'covered' if p['covered'] else 'none'}")
    pays, cur["payments"] = _lines(MD / "payments.jsonl", cur["payments"])
    for x in pays:
        if not x.get("paid"):
            m = re.search(r"([\d.]+) h of shelf life left", x.get("reason") or "")
            cur["refusals"].append({"shop": shop_name(x.get("shop")), "reason": x.get("reason"),
                                    "ts": int(time.time()), "item": x.get("item"),
                                    "hours": float(m.group(1)) if m else None})
            events.append(f"refused to pay {x.get('shop')}: {x.get('reason')}")
    cur["refusals"] = cur["refusals"][-300:]
    _save(PURCHASES, rows)
    _save(CURSOR, cur)
    return events


def notes(shop):
    """One line of what this buyer saw from `shop` in Round 3."""
    rows = [p for p in _load(PURCHASES, []) if p.get("shop") == shop]
    refusals = [r for r in _load(CURSOR, {}).get("refusals", []) if r["shop"] == shop]
    if not rows and not refusals:
        return "none in round 3"
    bits = [f"{len(rows)} settled"]
    lies = sum(1 for p in rows if p.get("claim_honest") is False)
    if lies:
        bits.append(f"{lies} FALSE grade claims")
    bad = [p for p in rows if p.get("broke_at") and not p.get("covered")]
    good = [p for p in rows if p.get("broke_at") and p.get("covered")]
    if bad:
        bits.append(f"{len(bad)} broke without warranty")
    if good:
        bits.append(f"{len(good)} broke but warranty refunded")
    if rows:
        off = [1 - p["price"] / p["value"] for p in rows if p.get("value") and p.get("price")]
        if off:
            bits.append(f"avg {sum(off) / len(off):.0%} below list")
    if refusals:
        bits.append(f"{len(refusals)} checkouts my kit refused ({refusals[-1]['reason']})")
    return ", ".join(bits)


def avoid():
    """Shops to leave out in code: a false grade claim, two items that broke without warranty, or two deals in
    12 h our kit would not pay for lack of the market's certificate (round 3's first 20 minutes: 6 such deals)."""
    out = set()
    by = {}
    for p in _load(PURCHASES, []):
        if p.get("claim_honest") is False:
            out.add(p["shop"])
        if p.get("broke_at") and not p.get("covered"):
            by[p["shop"]] = by.get(p["shop"], 0) + 1
    nocert = {}
    for r in _load(CURSOR, {}).get("refusals", []):          # deals our kit would not pay: no certificate
        if "no certificate" in (r.get("reason") or "") and time.time() - r.get("ts", 0) < 12 * 3600:
            nocert[r["shop"]] = nocert.get(r["shop"], 0) + 1
    return out | {s for s, n in by.items() if n >= 2} | {s for s, n in nocert.items() if n >= 2}


SHORT_SHELF_WINDOW = 3600     # Nov 4 00:40-02:17: 24 of 216 orders agreed a deal the kit refused for short shelf
                              # life, often the same shop and SKU again (one shop 7 times); replaying the payments
                              # log, a refusal under 1 h old predicted 20 of 56 such refusals, but 5 paid deals too


def short_shelf(shop, item, need):
    """Hours left on the unit `shop` last offered of `item`, aged since, when our kit refused it for short shelf
    life within the last hour and it would not last `need` hours now; else None."""
    now = time.time()
    for r in reversed(_load(CURSOR, {}).get("refusals", [])):
        if r.get("shop") == shop and r.get("item") == item and r.get("hours") is not None:
            if now - r["ts"] > SHORT_SHELF_WINDOW:
                return None
            left = r["hours"] - (now - r["ts"]) / 3600
            return left if left < need else None
    return None


def break_rate(grade=None):
    """Chance a goods unit breaks after the sale (the market does not publish it). The market-wide count first:
    restock.py tallies "broken" entries on the ledger against goods settles, per grade (break_rates.json); then our
    own purchases old enough to have broken (6 h). Prior: 1 in 10."""
    rates = _load(MD / "break_rates.json", {})
    rows = [rates[grade]] if grade in rates else list(rates.values())
    broken, sold = sum(r[0] for r in rows), sum(r[1] for r in rows)
    old = [p for p in _load(PURCHASES, []) if p.get("kind") == "goods" and time.time() - p.get("ts", 0) > BREAK_WINDOW + 600
           and (grade is None or p.get("grade") == grade)]
    broken += sum(1 for p in old if p.get("broke_at"))
    return (1 + broken) / (10 + sold + len(old))
