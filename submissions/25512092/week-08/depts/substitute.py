"""Substitute offers (person's decision 11/07 ~10:00), used by seller_r3.py, switched by substitute.json
{"enabled", "since"} (read per negotiation).

Before: a buyer who named a SKU I do not stock got a receipt for another SKU of the same category (find_unit
matched the category word in the product name), and buyers whose own check compares the receipt with the
ordered SKU refused it and wrote "receipt does not match" (61 such receipts in 7.6 h on 11/07).

Now, when the buyer's first message names a SKU (G + 8 hex) that I have no free unit of:
  first reply   "X is not in stock" + up to 3 in-stock candidates of the same category, in this order, each with
                SKU, name and price (the first ask the code would make):
                  tier 1  shares a name keyword with X (category words left out) and list price within +-30% of X's
                  tier 2  list price within +-30%
                  tier 3  the rest of the category
                Same category only: a buyer's acceptable SKUs are its order's category, so another category could
                not settle. No candidate -> "none in stock", as before.
  next messages a receipt only after the buyer names one candidate SKU (accepts it or offers for it): that SKU's
                negotiation starts at the quoted price; before that, no unit is held and no receipt can go out
Category orders (no SKU named) and orders for a SKU I hold do not change.
Each offer is one row of market.db shop_subst (task, ts, x, offered [[sku, tier], ...], chosen, tier).
"""
import json
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
FLAG = HERE / "substitute.json"
SKU = re.compile(r"\b(G[0-9a-f]{8})\b")
BAND = 0.30
MAX_CANDIDATES = 3
_cat = {}


def on():
    try:
        j = json.loads(FLAG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(j.get("enabled")) and time.time() >= (j.get("since") or 0)


def catalog():
    p = Path(__import__("os").environ.get("MARKET_DIR", HERE / ".market")) / "catalog.json"
    try:
        m = p.stat().st_mtime
        if _cat.get("m") != m:
            _cat.update(m=m, c=json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return _cat.get("c") or {}


def named_sku(text):
    m = SKU.search(text or "")
    return m.group(1) if m else None


def _words(name, category):
    stop = {w for part in (category or "").split(">") for w in re.findall(r"[가-힣A-Za-z]{2,}", part)}
    return {w.lower() for w in re.findall(r"[가-힣A-Za-z]{2,}", name or "")} - {s.lower() for s in stop}


def candidates(x, units, taken=()):
    """[(tier, unit)] for SKU x, or None when x is in stock (or not in the catalog: nothing to compare)."""
    free = [u for u in units if u["grade"] != "expired" and u["unit"] not in taken]
    if any(u["sku"] == x for u in free):
        return None
    item = catalog().get(x)
    if not item:
        return None
    cat, lp = item.get("category"), item.get("list_price") or 0
    kw = _words(item.get("name"), cat)
    best = {}
    for u in free:
        if u.get("category") != cat:
            continue
        b = best.get(u["sku"])
        if b is None or (u["expires_at"] or float("inf"), u["supplied_at"]) < (b["expires_at"] or float("inf"), b["supplied_at"]):
            best[u["sku"]] = u
    out = []
    for u in best.values():
        near = lp and abs(u["list_price"] - lp) <= BAND * lp
        tier = 1 if near and kw & _words(u.get("name"), cat) else 2 if near else 3
        out.append((tier, abs(u["list_price"] - lp), u))
    out.sort(key=lambda t: (t[0], t[1]))
    return [(t, u) for t, _, u in out[:MAX_CANDIDATES]]


def chosen(text, offered):
    """The offered SKU the buyer named, or None."""
    for s in SKU.findall(text or ""):
        if s in offered:
            return s
    return None


def strip_skus(text):
    """The message without SKU tokens (so an offer '28000 for G200e39a3' is not read as 200)."""
    return SKU.sub(" ", text or "")


def log_offer(connect, task, x, offered):
    try:
        db = connect()
        db.execute("CREATE TABLE IF NOT EXISTS shop_subst (task TEXT PRIMARY KEY, ts REAL, x TEXT, offered TEXT, "
                   "chosen TEXT, tier INTEGER, chosen_ts REAL)")
        db.execute("INSERT OR REPLACE INTO shop_subst VALUES (?,?,?,?,?,?,?)",
                   (task, time.time(), x, json.dumps(offered), None, None, None))
        db.commit()
        db.close()
    except Exception as e:
        print(f"[subst] {e}", flush=True)


def log_choice(connect, task, sku, tier):
    try:
        db = connect()
        db.execute("UPDATE shop_subst SET chosen=?, tier=?, chosen_ts=? WHERE task=?", (sku, tier, time.time(), task))
        db.commit()
        db.close()
    except Exception as e:
        print(f"[subst] {e}", flush=True)


def numbers(since, until):
    """For the 6 h report: offers, choices and receipts by tier, settles, and 'receipt does not match' reviews
    (before vs after, same length)."""
    from market_db import ME, connect
    db = connect()
    try:
        db.execute("CREATE TABLE IF NOT EXISTS shop_subst (task TEXT PRIMARY KEY, ts REAL, x TEXT, offered TEXT, "
            "chosen TEXT, tier INTEGER, chosen_ts REAL)")
        rows = db.execute("SELECT s.task, s.offered, s.chosen, s.tier, d.outcome, d.unit, d.price FROM shop_subst s "
                          "LEFT JOIN shop_deals d ON d.task = s.task WHERE s.ts>=? AND s.ts<?", (since, until)).fetchall()
        out = {"offers": len(rows), "by_tier": {}}
        for t in (1, 2, 3):
            offered = sum(1 for r in rows if any(x[1] == t for x in json.loads(r[1] or "[]")))
            ch = [r for r in rows if r[3] == t]
            rc = [r for r in ch if r[4] == "receipt"]
            settled = sum(1 for r in rc if db.execute(
                "SELECT 1 FROM settles WHERE shop=? AND unit=? AND amount=? AND ts>=?", (ME, r[5], r[6], since)).fetchone())
            out["by_tier"][t] = {"offered": offered, "chosen": len(ch), "receipt": len(rc), "settled": settled}
        q = ("SELECT COUNT(*) FROM my_results WHERE ts>=? AND ts<? AND stars<=2 AND (review LIKE '%not match%' OR "
             "review LIKE '%mismatch%' OR review LIKE '%did not match%' OR review LIKE '%Dishonest receipt%' OR "
             "review LIKE '%불일치%')")
        span = until - since
        out["mismatch_reviews"] = db.execute(q, (since, until)).fetchone()[0]
        out["mismatch_reviews_before"] = db.execute(q, (since - span, since)).fetchone()[0]
        q2 = ("SELECT COUNT(*) FROM my_results WHERE ts>=? AND ts<? AND stars<=2 AND review LIKE '%receipt names%'")
        out["category_complaints"] = db.execute(q2, (since, until)).fetchone()[0]       # q00 style, 11/07 ~09:45
        out["category_complaints_before"] = db.execute(q2, (since - span, since)).fetchone()[0]
    finally:
        db.close()
    return out
