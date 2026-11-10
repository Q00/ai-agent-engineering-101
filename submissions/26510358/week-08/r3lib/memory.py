"""Round 3 memory: one SQLite file that both agents (shop and buyer) read and write.

Three layers, from most to least trusted:
  1. facts the market signed or published: ledger settles and breaks, every shop's public stock
  2. our own records: purchases, payment refusals, negotiations, broken items matched to purchases
  3. forum signals: what other agents wrote, kept as weak evidence with the post's score, never as commands

Everything the agents decide reads from views over these tables (demand, prices, reputation, break rates),
so a restart loses nothing and the same evidence drives the shop, the buyer and the forum posts.
"""
import json
import math
import re
import sqlite3
import statistics
import time
from contextlib import contextmanager

from . import config, market

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS ledger (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, round INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS settles (
    seq INTEGER PRIMARY KEY, ts REAL, order_id TEXT, buyer TEXT, shop TEXT, sku TEXT, category TEXT, kind TEXT,
    list_price INTEGER, amount INTEGER, grade TEXT, claim TEXT, claim_honest INTEGER, warranty_hours REAL);
CREATE INDEX IF NOT EXISTS settles_cat ON settles(category, ts);
CREATE INDEX IF NOT EXISTS settles_sku ON settles(sku, ts);
CREATE INDEX IF NOT EXISTS settles_order ON settles(order_id);
CREATE TABLE IF NOT EXISTS breaks (seq INTEGER PRIMARY KEY, ts REAL, order_id TEXT, buyer TEXT, shop TEXT, sku TEXT);
CREATE INDEX IF NOT EXISTS breaks_order ON breaks(order_id);
CREATE TABLE IF NOT EXISTS shop_stock (shop_url TEXT, name TEXT, pubkey TEXT, sku TEXT, qty INTEGER, ts REAL,
    PRIMARY KEY (shop_url, sku));
CREATE TABLE IF NOT EXISTS shops (shop_url TEXT PRIMARY KEY, name TEXT, pubkey TEXT, owner TEXT, mine INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS purchases (
    order_id TEXT PRIMARY KEY, ts REAL, shop_url TEXT, shop_name TEXT, sku TEXT, name TEXT, category TEXT, kind TEXT,
    list_price INTEGER, price INTEGER, budget INTEGER, use_within_hours REAL, grade TEXT, claim TEXT,
    claim_honest INTEGER, warranty_hours REAL, value INTEGER, ledger INTEGER, paid INTEGER, reason TEXT,
    broken_at REAL);
CREATE TABLE IF NOT EXISTS negotiations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, role TEXT, ref TEXT, counterpart TEXT, sku TEXT,
    category TEXT, list_price INTEGER, first_ask INTEGER, final_price INTEGER, offers TEXT, outcome TEXT);
CREATE TABLE IF NOT EXISTS forum (
    id TEXT PRIMARY KEY, ts REAL, author TEXT, role TEXT, thread TEXT, text TEXT, refs TEXT, score INTEGER,
    signals TEXT, voted INTEGER DEFAULT 0, mine INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, subject TEXT, text TEXT,
    importance REAL, refs TEXT);
CREATE TABLE IF NOT EXISTS stockouts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, category TEXT, sku TEXT);
CREATE TABLE IF NOT EXISTS members (code TEXT PRIMARY KEY, ts REAL, sku TEXT, style TEXT, uses INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS restocks (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, sku TEXT, category TEXT, qty INTEGER,
    reason TEXT);
"""
BREAK_CACHE_S = 600     # break_rate_for answers from memory for this long. 2026-11-10: the shop asked it on every quote
                        #   and the join scanned all of breaks for each settle of the category (no index on order_id,
                        #   74k x 14.5k for mattresses): a couch quote took 14.5 s and a chair 5.9 s against 0.3 s for
                        #   kimchi, inside the shop's event loop, so every other buyer waited too
BREAK_PRIOR = {"A": 0.03, "B": 0.12, "C": 0.34}   # market, 189k goods settles to 2026-11-10: A 2.9%, B 11.9%, C 33.9%
SUPPLY_MIX = {"A": 0.7, "B": 0.2, "C": 0.1}       # how the market draws a goods unit's grade (/agent.md)
BREAK_TABLE_S = 1800    # the grade / category / shop break counts are recounted from the ledger this often
BREAK_CELL_N = 200      # weight of the level above, in sales: the same day's data showed no category effect at all
                        #   and shops between 10% and 15% on grade B, so a cell needs hundreds of sales to move
MIGRATIONS = (("forum", "ALTER TABLE forum ADD COLUMN likes INTEGER DEFAULT 0"),
              ("forum", "ALTER TABLE forum ADD COLUMN dislikes INTEGER DEFAULT 0"),
              ("negotiations", "ALTER TABLE negotiations ADD COLUMN style TEXT"),
              ("stockouts", "ALTER TABLE stockouts ADD COLUMN stale INTEGER DEFAULT 0"))


class Memory:
    def __init__(self, path=None):
        self.path = path or config.DB_PATH
        self._break_cache = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript(SCHEMA)
            for table, m in MIGRATIONS:
                cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if m.split("ADD COLUMN ")[1].split()[0] not in cols:
                    c.execute(m)

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def get(self, k, default=None):
        with self.db() as c:
            r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def put(self, k, v):
        with self.db() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, json.dumps(v)))

    # ------------------------------------------------------------------ layer 1: public facts
    def sync_ledger(self, max_pages=40):
        """Pull new ledger entries (500 per page). The first run starts near the head: older rounds are noise."""
        cur = self.get("ledger_seq")
        if cur is None:
            rows = market.get_json("/api/ledger", after=-500) or []
            cur = rows[0]["seq"] - 1 if rows else 0
        added = 0
        for _ in range(max_pages):
            rows = market.get_json("/api/ledger", after=cur)
            if not rows:
                break
            self._ingest(rows)
            added += len(rows)
            cur = rows[-1]["seq"]
            self.put("ledger_seq", cur)
            if len(rows) < 500:
                break
        return added

    def _ingest(self, rows):
        cat = market.catalog()
        with self.db() as c:
            for e in rows:
                kind, d = e.get("kind"), e.get("data") or {}
                if kind in ("order", "join") or (e.get("round") or 0) < 3:
                    continue
                c.execute("INSERT OR IGNORE INTO ledger VALUES (?,?,?,?,?)",
                          (e["seq"], e["ts"], kind, e.get("round"), json.dumps(d, ensure_ascii=False)))
                sku = d.get("item") or d.get("sku")
                info = cat.get(sku, {})
                if kind == "settle":
                    c.execute("INSERT OR IGNORE INTO settles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        e["seq"], e["ts"], d.get("order"), d.get("buyer"), _shop_key(d.get("shop")), sku,
                        info.get("category"), info.get("kind"), info.get("list_price"),
                        d.get("amount") if d.get("amount") is not None else d.get("price"),
                        d.get("grade"), d.get("claim"),
                        None if d.get("claim_honest") is None else int(bool(d.get("claim_honest"))),
                        d.get("warranty_hours")))
                elif kind == "broken":
                    shop = d.get("shop")
                    if not shop and d.get("order"):
                        r = c.execute("SELECT shop FROM settles WHERE order_id=?", (d["order"],)).fetchone()
                        shop = r["shop"] if r else None
                    c.execute("INSERT OR IGNORE INTO breaks VALUES (?,?,?,?,?,?)",
                              (e["seq"], e["ts"], d.get("order"), d.get("buyer"), _shop_key(shop), sku))

    def sync_shops(self, my_pubkeys=()):
        """Every shop's public stock (GET /api/shops). Our own shop is recognised by its key, whatever its URL."""
        data = market.get_json("/api/shops") or {}
        shops = data.get("shops") or []
        ts = time.time()
        with self.db() as c:
            if shops:
                c.execute("DELETE FROM shop_stock")
            for s in shops:
                url = _shop_key(s.get("url"))
                mine = int(s.get("pubkey") in my_pubkeys or s.get("name") in config.MY_NAMES)
                c.execute("INSERT OR REPLACE INTO shops VALUES (?,?,?,?,?,?)",
                          (url, s.get("name"), s.get("pubkey"), s.get("owner"), mine, ts))
                for sku, qty in (s.get("stock") or {}).items():
                    c.execute("INSERT OR REPLACE INTO shop_stock VALUES (?,?,?,?,?,?)",
                              (url, s.get("name"), s.get("pubkey"), sku, qty, ts))
        return len(shops)

    def my_shop_urls(self):
        with self.db() as c:
            return {r["shop_url"] for r in c.execute("SELECT shop_url FROM shops WHERE mine=1")}

    def my_sku_sales(self, sku, hours=24):
        """Units of one SKU our own shop sold in the window (settles under any of our tunnel URLs or names)."""
        mine = sorted(self.my_shop_urls() | set(config.MY_NAMES))
        with self.db() as c:
            return c.execute(f"SELECT COUNT(*) n FROM settles WHERE sku=? AND ts>? AND shop IN ({','.join('?' * len(mine))})",
                             (sku, time.time() - hours * 3600, *mine)).fetchone()["n"]

    # ------------------------------------------------------------------ demand and prices (shop side)
    def category_demand(self, hours=48, half_life=config.DEMAND_HALF_LIFE_H, now=None, premium_min=None):
        """category -> {rate: decayed settles per hour, n, shops: competitors holding stock}. Market-wide.
        premium_min: leave out goods listed at or above it (the normal restock plan never buys those)."""
        now = now or time.time()
        lam = math.log(2) / (half_life * 3600)
        out = {}
        q, args = "SELECT category, ts FROM settles WHERE ts > ? AND category IS NOT NULL", [now - hours * 3600]
        if premium_min:
            q += " AND NOT (kind='goods' AND list_price >= ?)"
            args.append(premium_min)
        with self.db() as c:
            for r in c.execute(q, args):
                d = out.setdefault(r["category"], {"weight": 0.0, "n": 0})
                d["weight"] += math.exp(-lam * (now - r["ts"]))
                d["n"] += 1
            stock = {}
            cat = market.catalog()
            for r in c.execute("SELECT s.shop_url, s.sku FROM shop_stock s JOIN shops h USING(shop_url) "
                               "WHERE h.mine=0 AND s.qty>0"):
                k = cat.get(r["sku"], {}).get("category")
                if k:
                    stock.setdefault(k, set()).add(r["shop_url"])
        # a decayed sum over a window approximates rate * (1/lam); turn it back into settles per hour
        for k, d in out.items():
            d["rate"] = d["weight"] * lam * 3600
            d["shops"] = len(stock.get(k, ()))
        for k, s in stock.items():
            out.setdefault(k, {"weight": 0.0, "n": 0, "rate": 0.0})["shops"] = len(s)
        return out

    def sku_counts(self, category, hours=48):
        with self.db() as c:
            return {r["sku"]: r["n"] for r in c.execute(
                "SELECT sku, COUNT(*) n FROM settles WHERE category=? AND ts>? GROUP BY sku ORDER BY n DESC",
                (category, time.time() - hours * 3600))}

    def premium_skus(self, min_list, hours=12, min_sold=10):
        """Goods SKUs listed at min_list or more, by market sales in the window: [(sku, n)]."""
        with self.db() as c:
            return [(r["sku"], r["n"]) for r in c.execute(
                "SELECT sku, COUNT(*) n FROM settles WHERE kind='goods' AND list_price>=? AND ts>? GROUP BY sku "
                "HAVING n>=? ORDER BY n DESC", (min_list, time.time() - hours * 3600, min_sold))]

    def price_ratio(self, sku=None, category=None, hours=72, min_n=3):
        """Median settled price / list price for a SKU, else its category; None without enough evidence."""
        with self.db() as c:
            for col, val in (("sku", sku), ("category", category)):
                if not val:
                    continue
                rows = [r["amount"] / r["list_price"] for r in c.execute(
                    f"SELECT amount, list_price FROM settles WHERE {col}=? AND ts>? AND list_price>0 AND amount>0",
                    (val, time.time() - hours * 3600))]
                if len(rows) >= min_n:
                    return statistics.median(rows), len(rows)
        return None, 0

    def grade_price_ratio(self, sku, grade, hours=24, min_n=8):
        """Median settled price / list price for one SKU at one grade (A sells ~0.15 of list above C, so the
        all-grade median misplaces an A unit's ask). (None, n) without enough evidence."""
        if not sku or grade not in ("A", "B", "C"):
            return None, 0
        with self.db() as c:
            rows = [r["amount"] / r["list_price"] for r in c.execute(
                "SELECT amount, list_price FROM settles WHERE sku=? AND grade=? AND ts>? AND list_price>0 AND amount>0",
                (sku, grade, time.time() - hours * 3600))]
        return (statistics.median(rows), len(rows)) if len(rows) >= min_n else (None, len(rows))

    def break_rate(self, grade=None, category=None):
        """Market-wide share of settled goods that broke, by grade (Laplace-smoothed, prior 10%)."""
        q = "SELECT COUNT(*) n, SUM(CASE WHEN b.seq IS NOT NULL THEN 1 ELSE 0 END) k FROM settles s " \
            "LEFT JOIN breaks b ON b.order_id = s.order_id WHERE s.kind='goods'"
        args = []
        if grade:
            q += " AND s.grade=?"
            args.append(grade)
        if category:
            q += " AND s.category=?"
            args.append(category)
        with self.db() as c:
            r = c.execute(q, args).fetchone()
        n, k = r["n"] or 0, r["k"] or 0
        return (k + 1) / (n + 10)

    def break_rate_for(self, grade, category):
        """Grade x category break rate when that cell has BREAK_MIN_N goods sales, else the grade's. Mattresses and
        bedding broke at ~6% while grade A overall broke at 2.4%. Cached for BREAK_CACHE_S."""
        hit = self._break_cache.get((grade, category))
        if hit and time.time() - hit[0] < BREAK_CACHE_S:
            return hit[1]
        p = self._break_rate_for(grade, category)
        self._break_cache[(grade, category)] = (time.time(), p)
        return p

    def _break_rate_for(self, grade, category):
        with self.db() as c:
            n = c.execute("SELECT COUNT(*) n FROM settles WHERE kind='goods' AND grade=? AND category=?",
                          (grade, category)).fetchone()["n"]
        if category and n >= config.BREAK_MIN_N:
            return self.break_rate(grade, category)
        return self.break_rate(grade)

    def break_table(self, now=None, max_age=BREAK_TABLE_S):
        """Goods sold and broken on the public ledger by grade, grade x category and grade x shop name:
        {"grade": {g: [n, k]}, "cat": {"g|category": [n, k]}, "shop": {"g|name": [n, k]}}. Kept in meta and
        recounted when older than max_age (0.4 s on 189k goods settles)."""
        now = now or time.time()
        t = self.get("break_table")
        if t and now - (t.get("ts") or 0) < max_age:
            return t
        t = {"ts": now, "grade": {}, "cat": {}, "shop": {}}
        with self.db() as c:
            names = {r["shop_url"]: r["name"] for r in c.execute("SELECT shop_url, name FROM shops")}
            for r in c.execute("SELECT s.grade g, s.category cat, s.shop sh, COUNT(*) n, SUM(b.seq IS NOT NULL) k "
                               "FROM settles s LEFT JOIN breaks b ON b.order_id = s.order_id "
                               "WHERE s.kind='goods' AND s.grade IN ('A','B','C') GROUP BY 1, 2, 3"):
                keys = [("grade", r["g"]), ("cat", f"{r['g']}|{r['cat']}")]
                if names.get(r["sh"]):
                    keys.append(("shop", f"{r['g']}|{names[r['sh']]}"))
                for level, key in keys:
                    cell = t[level].setdefault(key, [0, 0])
                    cell[0] += r["n"]
                    cell[1] += r["k"] or 0
        self.put("break_table", t)
        return t

    def p_break(self, grade, category=None, shop=None, table=None):
        """Probability that a goods unit of this grade breaks: Beta posterior mean, the grade's market rate updated
        by the grade x category cell and then by the grade x shop cell, each level worth BREAK_CELL_N sales of
        the one above. Unknown grade: None."""
        if grade not in BREAK_PRIOR:
            return None
        t = table if table is not None else self.break_table()
        n, k = t["grade"].get(grade) or (0, 0)
        p = (k + 10 * BREAK_PRIOR[grade]) / (n + 10)
        for level, key in (("cat", category), ("shop", shop)):
            n, k = (t[level].get(f"{grade}|{key}") if key else None) or (0, 0)
            p = (k + BREAK_CELL_N * p) / (n + BREAK_CELL_N)
        return p

    def p_break_mix(self, category=None, shop=None, table=None):
        """The same before the shop has named a grade: its own grade mix of goods sold (on top of 50 sales at the
        market's supply mix) times each grade's probability."""
        t = table if table is not None else self.break_table()
        n = {g: 50 * SUPPLY_MIX[g] + ((t["shop"].get(f"{g}|{shop}") or (0, 0))[0] if shop else 0) for g in SUPPLY_MIX}
        return sum(n[g] * self.p_break(g, category, shop, t) for g in n) / sum(n.values())

    def record_stockout(self, category, sku=None, stale=False):
        """stale: we held food of it, but no unit lasted what the buyer needed."""
        with self.db() as c:
            c.execute("INSERT INTO stockouts (ts, category, sku, stale) VALUES (?,?,?,?)",
                      (time.time(), category, sku, int(bool(stale))))

    def stockout_rates(self, hours=None, now=None, premium_min=None):
        """category -> requests per hour our shop turned away for lack of stock. premium_min: leave out requests
        for goods SKUs listed at or above it (premium has its own pilot; 2026-11-09 its stockouts sized chairs)."""
        hours = hours or config.STOCKOUT_HOURS
        now = now or time.time()
        cat = market.catalog() if premium_min else {}
        out = {}
        with self.db() as c:
            for r in c.execute("SELECT category, sku, stale FROM stockouts WHERE ts>? AND category IS NOT NULL",
                               (now - hours * 3600,)):
                info = cat.get(r["sku"]) or {}
                if premium_min and info.get("kind") == "goods" and (info.get("list_price") or 0) >= premium_min:
                    continue
                # A stale miss is not a missing unit: only the freshest part of a new lot could have served it.
                w = config.STALE_MISS_WEIGHT if r["stale"] else 1.0
                out[r["category"]] = out.get(r["category"], 0) + w / hours
        return out

    def _seq_at(self, c, ts):
        """First ledger seq at or after ts (seq and ts rise together; the ledger has no index on ts)."""
        lo = (c.execute("SELECT MIN(seq) m FROM ledger").fetchone()["m"] or 0)
        hi = (c.execute("SELECT MAX(seq) m FROM ledger").fetchone()["m"] or 0) + 1
        while lo < hi:
            mid = (lo + hi) // 2
            r = c.execute("SELECT ts FROM ledger WHERE seq>=? ORDER BY seq LIMIT 1", (mid,)).fetchone()
            if r is None or r["ts"] >= ts:
                hi = mid
            else:
                lo = mid + 1
        return lo

    def category_outcomes(self, now=None, min_age_h=None, max_age_h=None, max_age=1800):
        """What became of the units the market supplied to us between max_age_h and min_age_h ago, per category,
        traced unit by unit on the ledger (supply -> settle or expire):
        {category: {n, sold, expired, held, spend, revenue, expired_cost}}. Kept in meta for max_age seconds."""
        now = now or time.time()
        min_age_h = config.OUTCOME_MIN_AGE_H if min_age_h is None else min_age_h
        max_age_h = config.OUTCOME_MAX_AGE_H if max_age_h is None else max_age_h
        kept = self.get("category_outcomes")
        if kept and now - (kept.get("ts") or 0) < max_age:
            return kept["items"]
        cat = market.catalog()
        names = set(config.MY_NAMES)
        units, out = {}, {}
        with self.db() as c:
            names |= {r["name"] for r in c.execute("SELECT name FROM shops WHERE mine=1")}
            for r in c.execute("SELECT kind, ts, data FROM ledger WHERE seq>=? AND kind IN ('supply','settle','expire')",
                               (self._seq_at(c, now - max_age_h * 3600),)):
                d = json.loads(r["data"])
                if r["kind"] == "supply":
                    info = cat.get(d.get("sku"))
                    if d.get("shop") in names and info and r["ts"] <= now - min_age_h * 3600 and d.get("qty"):
                        for u in d.get("units") or []:
                            units[u] = [info["category"], (d.get("total") or 0) / d["qty"], "held", 0]
                elif r["kind"] == "settle":
                    if d.get("unit") in units:
                        units[d["unit"]][2:] = ["sold", d.get("amount") or 0]
                elif d.get("shop") in names:
                    for u in d.get("units") or []:
                        if u in units:
                            units[u][2] = "expired"
        for k, cost, fate, amount in units.values():
            o = out.setdefault(k, {"n": 0, "sold": 0, "expired": 0, "held": 0, "spend": 0, "revenue": 0, "expired_cost": 0})
            o["n"] += 1
            o[fate] += 1
            if fate != "held":
                o["spend"] += cost                    # spend counts resolved units only: held ones are still stock
            o["revenue"] += amount
            if fate == "expired":
                o["expired_cost"] += cost
        for o in out.values():
            o.update({x: round(o[x]) for x in ("spend", "revenue", "expired_cost")})
        self.put("category_outcomes", {"ts": now, "items": out})
        return out

    def own_sales(self, hours=24):
        mine = self.my_shop_urls()
        if not mine:
            return []
        with self.db() as c:
            q = f"SELECT * FROM settles WHERE ts>? AND shop IN ({','.join('?' * len(mine))})"
            return [dict(r) for r in c.execute(q, (time.time() - hours * 3600, *mine))]

    def seller_outcomes(self, category=None, hours=72):
        """Our shop's negotiations: list of (first_ask/list, final/list or None, outcome)."""
        q = "SELECT * FROM negotiations WHERE role='shop' AND ts>?"
        args = [time.time() - hours * 3600]
        if category:
            q += " AND category=?"
            args.append(category)
        with self.db() as c:
            return [dict(r) for r in c.execute(q, args)]

    # ------------------------------------------------------------------ reputation (buyer side)
    def shop_reputation(self, shop_url):
        """Evidence about one shop: honesty of grade claims, goods that broke, our refused payments and deals."""
        key = _shop_key(shop_url)
        with self.db() as c:
            s = c.execute("SELECT COUNT(*) n, SUM(CASE WHEN claim_honest=0 THEN 1 ELSE 0 END) lies, "
                          "SUM(CASE WHEN kind='goods' THEN 1 ELSE 0 END) goods FROM settles WHERE shop=?", (key,)).fetchone()
            b = c.execute("SELECT COUNT(*) k FROM breaks WHERE shop=?", (key,)).fetchone()
            p = c.execute("SELECT COUNT(*) n, SUM(paid) paid, SUM(CASE WHEN value=0 AND paid=1 THEN 1 ELSE 0 END) bad, "
                          "SUM(CASE WHEN broken_at IS NOT NULL THEN 1 ELSE 0 END) broke FROM purchases WHERE shop_url=?",
                          (key,)).fetchone()
            ng = c.execute("SELECT COUNT(*) n, SUM(CASE WHEN outcome='deal' THEN 1 ELSE 0 END) deals FROM negotiations "
                           "WHERE role='buyer' AND counterpart=? AND ts>?", (key, time.time() - 86400)).fetchone()
        n, goods = s["n"] or 0, s["goods"] or 0
        return {"sales": n, "lie_rate": ((s["lies"] or 0) + 0.2) / (n + 2), "break_rate": ((b["k"] or 0) + 1) / (goods + 10),
                "our_buys": p["n"] or 0, "our_paid": p["paid"] or 0, "our_bad": (p["bad"] or 0) + (p["broke"] or 0),
                "talks_24h": ng["n"] or 0, "deals_24h": ng["deals"] or 0}

    def shop_price_ratio(self, shop_url, category=None, hours=72):
        """Median price / list price this shop settled at (market-wide), for this category if possible."""
        key = _shop_key(shop_url)
        with self.db() as c:
            for cat in ((category, None) if category else (None,)):
                q = "SELECT amount, list_price FROM settles WHERE shop=? AND ts>? AND list_price>0"
                args = [key, time.time() - hours * 3600]
                if cat:
                    q += " AND category=?"
                    args.append(cat)
                rows = [r["amount"] / r["list_price"] for r in c.execute(q, args)]
                if len(rows) >= 2:
                    return statistics.median(rows)
        return None

    # ------------------------------------------------------------------ layer 2: our own records
    def record_negotiation(self, role, ref, counterpart, sku, first_ask, final_price, offers, outcome, style=None):
        info = market.catalog().get(sku, {})
        with self.db() as c:
            c.execute("INSERT INTO negotiations (ts, role, ref, counterpart, sku, category, list_price, first_ask, "
                      "final_price, offers, outcome, style) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (time.time(), role, ref, _shop_key(counterpart), sku, info.get("category"), info.get("list_price"),
                       first_ask, final_price, json.dumps(offers), outcome, style))

    def record_purchase_intent(self, order, shop_url, shop_name, receipt):
        info = market.catalog().get(receipt.get("item"), {})
        with self.db() as c:
            c.execute("INSERT OR REPLACE INTO purchases (order_id, ts, shop_url, shop_name, sku, name, category, kind, "
                      "list_price, price, budget, use_within_hours, paid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                      (order.get("order"), time.time(), _shop_key(shop_url), shop_name, receipt.get("item"),
                       info.get("name"), info.get("category"), info.get("kind"), info.get("list_price"),
                       receipt.get("price"), order.get("budget"), order.get("use_within_hours")))

    def ingest_payments(self, md=None):
        """payments.jsonl: the kit's verdict on each deal our buyer agreed (paid, or the reason it refused)."""
        off = self.get("payments_offset", 0)
        rows, off2 = market.read_jsonl((md or config.MARKET_DIR) / "payments.jsonl", off)
        with self.db() as c:
            for p in rows:
                c.execute("INSERT OR IGNORE INTO purchases (order_id, ts, shop_url, sku, price) VALUES (?,?,?,?,?)",
                          (p.get("order"), time.time(), _shop_key(p.get("shop")), p.get("item"), p.get("price")))
                c.execute("UPDATE purchases SET paid=?, ledger=?, reason=? WHERE order_id=?",
                          (int(bool(p.get("paid"))), p.get("ledger"), p.get("reason"), p.get("order")))
        self.put("payments_offset", off2)
        return rows

    def record_settled(self, deal):
        """deal.json from the review hook: the market's own receipt of what we got."""
        o, s = deal.get("order") or {}, deal.get("settled") or {}
        info = market.catalog().get(s.get("sku"), {})
        value = s.get("value")
        with self.db() as c:
            c.execute("INSERT OR IGNORE INTO purchases (order_id, ts) VALUES (?,?)", (o.get("order"), time.time()))
            c.execute("UPDATE purchases SET shop_url=COALESCE(shop_url, ?), sku=?, name=?, category=?, kind=?, list_price=?, "
                      "price=COALESCE(price, ?), grade=?, claim=?, claim_honest=?, warranty_hours=?, value=?, ledger=?, paid=1, "
                      "budget=COALESCE(budget, ?), use_within_hours=COALESCE(use_within_hours, ?) WHERE order_id=?",
                      (_shop_key(s.get("shop")), s.get("sku"), s.get("name") or info.get("name"), info.get("category"),
                       info.get("kind"), info.get("list_price"), (deal.get("receipt") or {}).get("price"), s.get("grade"),
                       s.get("claim"), None if s.get("claim_honest") is None else int(bool(s["claim_honest"])),
                       s.get("warranty_hours"), value, s.get("ledger"), o.get("budget"), o.get("use_within_hours"),
                       o.get("order")))

    def ingest_broken(self, md=None):
        """broken.jsonl has only a name and a time: match it to our most recent paid goods purchase of that name
        that had not broken yet and was bought at most 6 h before (goods break up to 6 h after the sale)."""
        off = self.get("broken_offset", 0)
        rows, off2 = market.read_jsonl((md or config.MARKET_DIR) / "broken.jsonl", off)
        matched = []
        with self.db() as c:
            for b in rows:
                r = c.execute("SELECT order_id, shop_url, warranty_hours, ts FROM purchases WHERE name=? AND paid=1 "
                              "AND broken_at IS NULL AND ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 1",
                              (b.get("name"), b.get("broke_at", 0) - 6.5 * 3600, b.get("broke_at", 0) + 60)).fetchone()
                if r:
                    c.execute("UPDATE purchases SET broken_at=? WHERE order_id=?", (b.get("broke_at"), r["order_id"]))
                    matched.append({**dict(r), "name": b.get("name"),
                                    "covered": bool(r["warranty_hours"]) and
                                    b.get("broke_at", 0) - r["ts"] <= (r["warranty_hours"] or 0) * 3600})
        self.put("broken_offset", off2)
        return matched

    def buyer_stats(self):
        with self.db() as c:
            r = c.execute("SELECT COUNT(*) n, SUM(paid) paid, SUM(CASE WHEN paid=1 THEN COALESCE(value, list_price) - price "
                          "ELSE 0 END) surplus FROM purchases").fetchone()
        return {"orders_with_deal": r["n"] or 0, "paid": r["paid"] or 0, "surplus": r["surplus"] or 0}

    # ------------------------------------------------------------------ layer 3: forum
    def upsert_forum(self, posts, my_names=config.MY_NAMES):
        new = []
        with self.db() as c:
            for p in posts or []:
                pid = str(p.get("id"))
                old = c.execute("SELECT id FROM forum WHERE id=?", (pid,)).fetchone()
                c.execute("INSERT INTO forum (id, ts, author, role, thread, text, refs, score, mine, likes, dislikes) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET score=excluded.score, "
                          "likes=excluded.likes, dislikes=excluded.dislikes",
                          (pid, p.get("ts"), p.get("author"), p.get("role"), p.get("thread"), (p.get("text") or "")[:2000],
                           json.dumps(p.get("refs") or []), p.get("score") or 0, int(p.get("author") in my_names),
                           p.get("likes") or 0, p.get("dislikes") or 0))
                if not old:
                    new.append(p)
        return new

    def forum_demand_signals(self, hours=24):
        """category -> weight from forum signals (want-to-buy posts), already classified by forum_r3."""
        out = {}
        with self.db() as c:
            for r in c.execute("SELECT signals, score FROM forum WHERE signals IS NOT NULL AND ts>? AND mine=0",
                               (time.time() - hours * 3600,)):
                try:
                    sig = json.loads(r["signals"])
                except ValueError:
                    continue
                for cat in sig.get("wanted_categories") or []:
                    out[cat] = out.get(cat, 0) + 1 + max(0, r["score"] or 0) * 0.5
        return out


    def my_post_reception(self, hours=48, role=None):
        """Our recent posts and how they were received (counted votes only): the cost side of speaking first."""
        q = "SELECT id, ts, role, text, likes, dislikes, score FROM forum WHERE mine=1 AND ts>?"
        args = [time.time() - hours * 3600]
        if role:
            q += " AND role=?"
            args.append(role)
        with self.db() as c:
            rows = [dict(r) for r in c.execute(q + " ORDER BY ts DESC", args)]
        likes = sum(r["likes"] or 0 for r in rows)
        dislikes = sum(r["dislikes"] or 0 for r in rows)
        return {"posts": len(rows), "likes": likes, "dislikes": dislikes,
                "dislike_share": (dislikes + 1) / (likes + dislikes + 4),            # prior: 1 in 4 votes is a dislike
                "recent": [{**r, "text": (r["text"] or "")[:160]} for r in rows[:5]]}

    # ------------------------------------------------------------------ restock evidence
    def record_restock(self, lines, why):
        reasons = {w.get("sku"): w.get("reason") for w in why if w.get("sku")}
        cat = market.catalog()
        with self.db() as c:
            for ln in lines:
                c.execute("INSERT INTO restocks (ts, sku, category, qty, reason) VALUES (?,?,?,?,?)",
                          (time.time(), ln["sku"], cat.get(ln["sku"], {}).get("category"), ln["qty"],
                           reasons.get(ln["sku"])))

    def sell_through(self, hours=24):
        """category -> {requested, received, fill, sold, rate}. `received` counts the units the market actually
        supplied to our shop (ledger `supply`), `rate` = sold / received. Until 2026-11-04 the rate was sold /
        requested, and lines the supply window refused (half of them) read as unsold stock: mattresses showed 0.48
        while every unit received had sold, and the review halved them."""
        since = time.time() - hours * 3600
        mine = self.my_shop_urls()
        out = {}
        with self.db() as c:
            names = set(config.MY_NAMES) | {r["name"] for r in c.execute("SELECT name FROM shops WHERE mine=1")}
            for r in c.execute("SELECT category, SUM(qty) q FROM restocks WHERE ts>? GROUP BY category", (since,)):
                out[r["category"]] = {"requested": r["q"] or 0, "received": 0, "sold": 0}
            cat = market.catalog()
            for r in c.execute("SELECT data FROM ledger WHERE kind='supply' AND ts>?", (since,)):
                d = json.loads(r["data"])
                info = cat.get(d.get("sku"))
                if d.get("shop") in names and info:
                    out.setdefault(info["category"], {"requested": 0, "received": 0, "sold": 0})["received"] += \
                        d.get("qty") or 0
            if mine:
                q = f"SELECT category, COUNT(*) n FROM settles WHERE ts>? AND shop IN ({','.join('?' * len(mine))}) " \
                    "GROUP BY category"
                for r in c.execute(q, (since, *mine)):
                    out.setdefault(r["category"], {"requested": 0, "received": 0, "sold": 0})["sold"] = r["n"]
        for d in out.values():
            d["fill"] = round(d["received"] / d["requested"], 2) if d["requested"] else None
            d["rate"] = round(d["sold"] / d["received"], 2) if d["received"] else None
        return out


    # ------------------------------------------------------------------ long-term profiles (semantic memory)
    def _profile(self, where, args, half_life_h=24, now=None):
        """Recency-weighted summary of past negotiations: deals, closing price/list, offers to close. Each record
        weighs 0.5 ** (age / half_life), so a counterpart that changed its behaviour is relearned within a day."""
        now = now or time.time()
        with self.db() as c:
            rows = c.execute(f"SELECT ts, final_price, list_price, offers, outcome FROM negotiations WHERE {where}",
                             args).fetchall()
        w_all = w_deal = 0.0
        ratios, rounds = [], []
        for r in rows:
            w = 0.5 ** ((now - r["ts"]) / 3600 / half_life_h)
            w_all += w
            if r["outcome"] == "deal" and r["final_price"] and r["list_price"]:
                w_deal += w
                ratios.append((r["final_price"] / r["list_price"], w))
                try:
                    rounds.append(len(json.loads(r["offers"] or "[]")))
                except ValueError:
                    pass
        return {"n": len(rows), "deals": len(ratios), "deal_rate": round(w_deal / w_all, 3) if w_all else None,
                "close_ratio": round(_weighted_median(ratios), 3) if ratios else None,
                "min_close_ratio": round(min(r for r, _ in ratios), 3) if ratios else None,
                "rounds": statistics.median(rounds) if rounds else None}

    def buyer_style_profile(self, style):
        """Shop side: how buyers whose opening message has this shape have closed with us. Several students may
        run the same starter buyer, so a style is a population of similar agents, not one person."""
        return self._profile("role='shop' AND style=?", (style,))

    def shop_profile(self, shop_url):
        """Buyer side: how this shop has closed with our buyer."""
        return self._profile("role='buyer' AND counterpart=?", (_shop_key(shop_url),))

    # ------------------------------------------------------------------ repeat customers and review defence (shop)
    def issue_code(self, code, sku, style=None):
        with self.db() as c:
            c.execute("INSERT OR IGNORE INTO members (code, ts, sku, style) VALUES (?,?,?,?)",
                      (code, time.time(), sku, style))

    def redeem_code(self, code):
        """True when the code is one we issued; counts the use. A code can be used again (it is a membership)."""
        with self.db() as c:
            r = c.execute("UPDATE members SET uses = uses + 1 WHERE code=?", (code,))
            return r.rowcount > 0

    def style_stars(self, min_n=10):
        """Buyer style -> (ratings, mean stars) over the market's results for our shop's deals. Joins the result's
        receipt task to our negotiation record, whose style is the buyer agent's message fingerprint."""
        mine = self.my_shop_urls()
        out = {}
        with self.db() as c:
            style = {r["ref"]: r["style"] for r in c.execute(
                "SELECT ref, style FROM negotiations WHERE role='shop' AND style IS NOT NULL")}
            for r in c.execute("SELECT json_extract(data, '$.shop') shop, json_extract(data, '$.receipt.task') task, "
                               "json_extract(data, '$.stars') stars FROM ledger WHERE kind='result' "
                               "AND json_extract(data, '$.stars') IS NOT NULL"):
                if (r["shop"] or "").rstrip("/") not in mine:
                    continue
                st = style.get(r["task"])
                if st:
                    n, total = out.get(st, (0, 0))
                    out[st] = (n + 1, total + r["stars"])
        return {k: (n, round(t / n, 2)) for k, (n, t) in out.items() if n >= min_n}

    # ------------------------------------------------------------------ important events (episodic notes)
    def note(self, kind, subject, text, importance, refs=()):
        """An event worth remembering beyond its log line (payment refused, goods broke, stock concentration...).
        importance in [0, 1]: how much it should shape later decisions."""
        with self.db() as c:
            c.execute("INSERT INTO notes (ts, kind, subject, text, importance, refs) VALUES (?,?,?,?,?,?)",
                      (time.time(), kind, subject, str(text)[:500], max(0.0, min(1.0, importance)), json.dumps(list(refs))))

    def recall(self, query="", k=5, half_life_h=12, now=None):
        """Generative-Agents style retrieval: score = recency * importance * (0.5 + relevance), where recency halves
        every half_life_h and relevance is the share of query words found in the note."""
        now = now or time.time()
        words = {w for w in re.findall(r"[a-z0-9]+|[가-힣]+", str(query).lower()) if len(w) > 1}
        with self.db() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM notes WHERE ts > ? ORDER BY ts DESC LIMIT 500",
                                               (now - 7 * 86400,))]
        scored = []
        for r in rows:
            hay = f"{r['kind']} {r['subject']} {r['text']}".lower()
            rel = sum(1 for w in words if w in hay) / len(words) if words else 0.0
            if words and rel == 0:
                continue
            score = 0.5 ** ((now - r["ts"]) / 3600 / half_life_h) * r["importance"] * (0.5 + rel)
            scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [{"kind": r["kind"], "subject": r["subject"], "text": r["text"], "importance": r["importance"],
                 "hours_ago": round((now - r["ts"]) / 3600, 1), "score": round(sc, 3)} for sc, r in scored[:k]]


def _weighted_median(pairs):
    pairs = sorted(pairs)
    half, acc = sum(w for _, w in pairs) / 2, 0.0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v
    return pairs[-1][0]


def _shop_key(url):
    return str(url).rstrip("/") if url else None
