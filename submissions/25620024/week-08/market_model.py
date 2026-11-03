"""Week 08 — market_model.py: the shared market model (one SQLite file) that the shop, the buyer and the
meta-harness all read. The buyer is the shop's sensor: what it sees per order goes in here too.

  settles      every Round 3 settle on the public ledger: what sold, at what price, which grade, by which shop
  broken       goods that broke after a sale (ledger kind "broken")
  seen         per order our buyer received: the category, and each shop's stock of it (shops.json offers)
  unmet        what buyers asked our shop for that we did not stock
  quotes       every price our shop quoted: the promotion arm, and whether it became a deal (the bandit's data)
  members      member codes our shop handed out: purchases per code (repeat and volume prices)

    python market_model.py            # sync the ledger and print a summary
"""
import json
import math
import os
import sqlite3
import time
from pathlib import Path

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
SHOP_NAME = "dragonash"     # supply entries name the shop; settle entries carry its (changing) tunnel URL
DB = Path(os.environ.get("MARKET_MODEL", Path(__file__).parent / "private" / "market.db"))

SCHEMA = """
create table if not exists meta (k text primary key, v text);
create table if not exists settles (seq integer primary key, ts real, shop text, buyer text, sku text, category text,
    amount integer, list_price integer, grade text, claim text, claim_honest integer);
create table if not exists broken (seq integer primary key, ts real, buyer text, sku text);
create table if not exists seen (ts real, category text, kind text, shop text, sku text, qty integer);
create table if not exists unmet (ts real, text text, sku text, category text);
create table if not exists quotes (ts real, task text, sku text, kind text, grade text, arm text, list_price integer,
    cost integer, ask integer, price integer, outcome text);
create table if not exists members (code text primary key, purchases integer, spent integer, first real, last real);
create table if not exists settle_units (seq integer primary key, unit text);
create table if not exists our_units (unit text primary key);
create table if not exists supplies (seq integer primary key, ts real, shop text, sku text, qty integer, total integer);
create table if not exists supply_units (unit text primary key, shop text, sku text, cost real, ts real);
create index if not exists settles_sku on settles(sku);
create index if not exists settles_cat on settles(category);
"""


def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.executescript(SCHEMA)
    return c


def catalog(md=Path(".market")):
    try:
        rows = json.loads((Path(md) / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {r["sku"]: r for r in rows} if isinstance(rows, list) else rows


def sync(c=None, md=Path(".market"), budget_s=20):
    """Read the ledger from where the last sync stopped. The first sync starts at the newest entries."""
    c = c or db()
    cat = catalog(md)
    row = c.execute("select v from meta where k='ledger_seq'").fetchone()
    after = int(row[0]) if row else None
    if after is not None and not c.execute("select 1 from settle_units limit 1").fetchone():
        first = c.execute("select min(seq) from settles").fetchone()[0]
        after = first - 1 if first else after       # once: read the settles again to learn their units
    t0, n = time.time(), 0
    with httpx.Client(timeout=20) as h:
        if after is None:
            last = h.get(f"{MARKET}/api/ledger", params={"after": -500}).json()
            after = last[0]["seq"] - 1 if last else 0
        while time.time() - t0 < budget_s:
            rows = h.get(f"{MARKET}/api/ledger", params={"after": after}).json()
            if not rows:
                break
            for e in rows:
                d = e.get("data") or {}
                if e.get("kind") == "settle":
                    sku = d.get("item")
                    c.execute("insert or ignore into settles values (?,?,?,?,?,?,?,?,?,?,?)",
                              (e["seq"], e["ts"], d.get("shop"), d.get("buyer"), sku,
                               (cat.get(sku) or {}).get("category"), d.get("amount"),
                               (cat.get(sku) or {}).get("list_price"), d.get("grade"), d.get("claim"),
                               None if d.get("claim_honest") is None else int(bool(d["claim_honest"]))))
                    c.execute("insert or ignore into settle_units values (?,?)", (e["seq"], d.get("unit")))
                    n += 1
                elif e.get("kind") == "supply":
                    units = d.get("units") or []
                    c.execute("insert or ignore into supplies values (?,?,?,?,?,?)",
                              (e["seq"], e["ts"], d.get("shop"), d.get("sku"), d.get("qty"), d.get("total")))
                    per = (d.get("total") or 0) / max(1, len(units) or d.get("qty") or 1)
                    c.executemany("insert or ignore into supply_units values (?,?,?,?,?)",
                                  [(u, d.get("shop"), d.get("sku"), per, e["ts"]) for u in units])
                    if d.get("shop") == SHOP_NAME:
                        c.executemany("insert or ignore into our_units values (?)", [(u,) for u in units])
                elif e.get("kind") == "broken":
                    c.execute("insert or ignore into broken values (?,?,?,?)", (e["seq"], e["ts"], d.get("buyer"), d.get("item")))
            after = rows[-1]["seq"]
            c.execute("insert or replace into meta values ('ledger_seq', ?)", (str(after),))
            c.commit()
            if len(rows) < 500:
                break
    return n


def rates(c, half_life_h=6.0, now=None):
    """Settles per hour by SKU and by category, each settle weighted by exp(-age / half-life)."""
    now, lam = now or time.time(), math.log(2) / (half_life_h * 3600)
    by_sku, by_cat = {}, {}
    for sku, cat, ts in c.execute("select sku, category, ts from settles where ts > ?", (now - 10 * half_life_h * 3600,)):
        w = math.exp(-lam * (now - ts)) * lam * 3600      # so a steady 1/h sums to about 1
        by_sku[sku] = by_sku.get(sku, 0) + w
        by_cat[cat] = by_cat.get(cat, 0) + w
    for sku, cat, ts in c.execute("select sku, category, ts from unmet where ts > ?", (now - 10 * half_life_h * 3600,)):
        w = 0.5 * math.exp(-lam * (now - ts)) * lam * 3600   # an ask we could not serve counts half a sale
        if sku:
            by_sku[sku] = by_sku.get(sku, 0) + w
        if cat:
            by_cat[cat] = by_cat.get(cat, 0) + w
    return by_sku, by_cat


def ours(c):
    """Seq numbers of settles that shipped a unit our shop bought. Settles name the shop by its tunnel URL,
    which changes on every restart; the unit ids from our supply entries do not."""
    return "select s.seq from settle_units s join our_units o on s.unit = o.unit"


def market_low(c, sku, me=None, hours=12):
    """The lowest price another shop settled this SKU at lately, or None. Our own sales are left out: comparing
    with the name 'dragonash' against settles that carry a URL counted them as a competitor's, so each of our
    sales lowered our next hero price by one credit (found 2026-11-03)."""
    row = c.execute(f"select min(amount) from settles where sku=? and ts>? and seq not in ({ours(c)})",
                    (sku, time.time() - hours * 3600)).fetchone()
    return row[0] if row and row[0] else None


def our_sales(c, sku=None, hours=6):
    """Our settled sales lately: per SKU per hour (sku=None gives a dict for all SKUs)."""
    q = (f"select sku, count(*) from settles where ts>? and seq in ({ours(c)})"
         + (" and sku=?" if sku else "") + " group by sku")
    rows = c.execute(q, (time.time() - hours * 3600,) + ((sku,) if sku else ())).fetchall()
    rates = {k: n / hours for k, n in rows}
    return rates.get(sku, 0.0) if sku else rates


def regulars(c, min_buys=2, hours=48):
    """Our regular customers from the ledger: buyers who settled at least min_buys purchases with our shop lately.
    We cannot tell who is asking during a negotiation (the A2A request names no buyer), but the settles do, so
    the shop serves its regulars by stocking what they buy. Returns ({buyer: buys}, {category: {sku: buys}})."""
    rows = c.execute(f"select buyer, category, sku from settles where ts>? and seq in ({ours(c)})",
                     (time.time() - hours * 3600,)).fetchall()
    buys = {}
    for b, _, _ in rows:
        buys[b] = buys.get(b, 0) + 1
    regular = {b: n for b, n in buys.items() if b and n >= min_buys}
    wants = {}
    for b, cat, sku in rows:
        if b in regular and cat:
            wants.setdefault(cat, {})
            wants[cat][sku] = wants[cat].get(sku, 0) + 1
    return regular, wants


def competitor_stock(c, hours=48):
    """Units of each SKU other shops bought and have not sold yet (supplied minus settled), from the public ledger.
    Food that expired is not visible, so this is an upper bound. {sku: units}"""
    since = time.time() - hours * 3600
    rows = c.execute("""select sku, count(*) from supply_units where shop != ? and ts > ? and unit not in
                        (select unit from settle_units where unit is not null) group by sku""",
                     (SHOP_NAME, since)).fetchall()
    return dict(rows)


def shop_prices(c, hours=24, min_n=10):
    """Per shop, over its recent settles: deals, median price as a share of list, median margin over its unit cost.
    {shop name: (n, price_share, margin)}"""
    import statistics
    rows = c.execute("""select su.shop, s.amount, s.list_price, su.cost from settles s
                        join settle_units x on x.seq = s.seq join supply_units su on su.unit = x.unit
                        where s.ts > ? and s.list_price > 0 and su.cost > 0""", (time.time() - hours * 3600,)).fetchall()
    by = {}
    for shop, amount, lp, cost in rows:
        by.setdefault(shop, []).append((amount / lp, amount / cost - 1))
    return {k: (len(v), round(statistics.median(a for a, _ in v), 3), round(statistics.median(m for _, m in v), 3))
            for k, v in by.items() if len(v) >= min_n}


def hot_skus(c, hours=24, top=20):
    """The SKUs selling most in the whole market lately, with how many units competitors still hold.
    [(sku, sold, competitor_units)]"""
    sold = c.execute("select sku, count(*) from settles where ts>? group by sku order by count(*) desc limit ?",
                     (time.time() - hours * 3600, top)).fetchall()
    comp = competitor_stock(c)
    return [(sku, n, comp.get(sku, 0)) for sku, n in sold]


def shop_profile(c, shop, hours=24, top=5):
    """What one shop sold lately (by its name in supply entries): deals, distinct SKUs, mean sale, median price share
    of list, median margin, its top categories."""
    import statistics
    rows = c.execute("""select s.amount, s.list_price, su.cost, s.category, s.sku from settles s
                        join settle_units x on x.seq = s.seq join supply_units su on su.unit = x.unit
                        where su.shop = ? and s.ts > ?""", (shop, time.time() - hours * 3600)).fetchall()
    if not rows:
        return None
    cats = {}
    for _, _, _, cat, _ in rows:
        cats[cat] = cats.get(cat, 0) + 1
    shares = [a / lp for a, lp, _, _, _ in rows if lp]
    margins = [a / cost - 1 for a, _, cost, _, _ in rows if cost]
    return {"deals": len(rows), "skus": len({r[4] for r in rows}), "mean_sale": round(statistics.mean(r[0] for r in rows)),
            "price_share": round(statistics.median(shares), 3) if shares else None,
            "margin": round(statistics.median(margins), 3) if margins else None,
            "top_categories": [((k or "?").split(">")[-1], n) for k, n in sorted(cats.items(), key=lambda kv: -kv[1])[:top]]}


def buyer_profile(c, buyer, hours=24):
    """How one buyer buys: deals, median price share of list it paid, the shops it uses most (by supply name)."""
    import statistics
    rows = c.execute("""select s.amount, s.list_price, su.shop from settles s
                        left join settle_units x on x.seq = s.seq left join supply_units su on su.unit = x.unit
                        where s.buyer = ? and s.ts > ?""", (buyer, time.time() - hours * 3600)).fetchall()
    if not rows:
        return None
    shops = {}
    for _, _, sh in rows:
        shops[sh] = shops.get(sh, 0) + 1
    shares = [a / lp for a, lp, _ in rows if lp]
    return {"deals": len(rows), "paid_share_of_list": round(statistics.median(shares), 3) if shares else None,
            "top_shops": sorted(shops.items(), key=lambda kv: -kv[1])[:4]}


def remember_seen(order, shops):
    c = db()
    now = time.time()
    for s in shops:
        for o in s.get("offers") or []:
            c.execute("insert into seen values (?,?,?,?,?,?)", (now, order.get("category"), order.get("kind"),
                                                                s.get("name"), o.get("sku"), o.get("qty")))
    if not any(s.get("offers") for s in shops):
        c.execute("insert into seen values (?,?,?,?,?,?)", (now, order.get("category"), order.get("kind"), None, None, 0))
    c.commit()


def summary(c):
    n = c.execute("select count(*), count(distinct shop), count(distinct sku) from settles").fetchone()
    print(f"settles {n[0]} from {n[1]} shops, {n[2]} SKUs; quotes {c.execute('select count(*) from quotes').fetchone()[0]};"
          f" unmet {c.execute('select count(*) from unmet').fetchone()[0]}; members {c.execute('select count(*) from members').fetchone()[0]}")
    by_sku, by_cat = rates(c)
    for cat, r in sorted(by_cat.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {r:6.2f}/h  {cat}")


if __name__ == "__main__":
    c = db()
    print(f"synced {sync(c)} new settles")
    summary(c)
