"""Market price DB (SQLite) — every settled deal in the market, refreshed at most once per 5 minutes.

    python market_db.py            refresh now and print the hottest SKUs
    python market_db.py --report   print my buys and sales with the market average at that time

Who uses it
  restock.py  calls refresh() every run (the kit runs it every 60 s); refresh() does nothing if the
              last refresh is younger than REFRESH_S. Only restock.py writes the DB.
  market_buyer.py, seller_r3.py  read market_stats.json (written by refresh) through price_of().

Tables
  settles    one row per settled deal in the ledger: seq, ts, buyer, shop (name, if the shop is still
             registered), sku, name, grade, amount, claim_honest, market_avg_at (the SKU's average
             before this deal, so my own rows show what the market price was when I bought or sold)
  snapshots  every refresh: per SKU the average price and how many sold in the last 5 and 30 minutes
  my_stock   every unit my shop bought from supply: unit, sku, cost, grade, supplied_at, market_avg_at
  broken     every goods unit that broke after a sale: shop, sku, hours after sale, warranty, refund

Signals in market_stats.json (code-made; forum text never goes to a harness)
  shop_flags  per shop (name and URL): deals, dishonest grade claims, broken units — from the ledger, plus
              forum posts whose ledger refs show a dishonest claim
  sku_break   per SKU: share of sold units that broke
  wanted      SKUs that buyers asked for on the forum ("구합니다", "looking for" ...)
"""
import json
import os
import re
import sqlite3
import sys
import time

import httpx

import dbutil  # 11/12 ~15:15: busy 10 s + retries while db_safe.json is on

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "market.db")
STATS_PATH = os.path.join(HERE, "market_stats.json")
MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
ME = "choo-chu"

REFRESH_S = 300          # refresh at most every 5 minutes
AVG_WINDOW_S = 2 * 3600  # the average price uses deals of the last 2 hours ...
MIN_N = 3                # ... and needs at least this many deals, else all deals of that SKU
HOT_N = 30          # buy-plan hot list: the SKUs most sold in 5 / 30 min (11/08 ~13:45 person: 30 -> 60; ~16:50 back to 30)
BOOTSTRAP = 20000        # ledger entries read back on the first refresh


def connect():
    db = dbutil.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS settles (seq INTEGER PRIMARY KEY, ts REAL, buyer TEXT, shop_url TEXT, shop TEXT,
        sku TEXT, name TEXT, grade TEXT, amount INTEGER, claim_honest INTEGER, market_avg_at REAL);
    CREATE INDEX IF NOT EXISTS settles_sku ON settles (sku, ts);
    CREATE TABLE IF NOT EXISTS snapshots (ts REAL, sku TEXT, avg_price REAL, n INTEGER, sold_5m INTEGER,
        sold_30m INTEGER, PRIMARY KEY (ts, sku));
    CREATE TABLE IF NOT EXISTS my_stock (unit TEXT PRIMARY KEY, sku TEXT, name TEXT, cost INTEGER, grade TEXT,
        supplied_at REAL, market_avg_at REAL);
    CREATE TABLE IF NOT EXISTS broken (seq INTEGER PRIMARY KEY, ts REAL, buyer TEXT, shop_url TEXT, shop TEXT,
        sku TEXT, hours_after_sale REAL, warranty_hours REAL, refunded INTEGER);
    CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS hourly (hour REAL PRIMARY KEY, sales INTEGER, revenue INTEGER, profit INTEGER,
        low_cost INTEGER, low_profit INTEGER, high_cost INTEGER, high_profit INTEGER, cats TEXT,
        stock_cost INTEGER, cash INTEGER);
    CREATE TABLE IF NOT EXISTS policy (ts REAL PRIMARY KEY, high_share REAL, high_max INTEGER, reason TEXT);
    CREATE TABLE IF NOT EXISTS cases (ts REAL PRIMARY KEY, shop_case TEXT, buyer_case TEXT, cash INTEGER,
        stock_cost INTEGER);
    CREATE TABLE IF NOT EXISTS buyer_deals (ts REAL, shop TEXT, shop_url TEXT, sku TEXT, list_price INTEGER,
        buyer_case TEXT, variant_key TEXT, variant TEXT, min_offers INTEGER, offers INTEGER, outcome TEXT,
        reason TEXT, price INTEGER, warranty_hours REAL);
    CREATE TABLE IF NOT EXISTS shop_deals (task TEXT PRIMARY KEY, ts REAL, shop_case TEXT, variant_key TEXT,
        variant TEXT, sku TEXT, unit TEXT, cost INTEGER, first_ask INTEGER, floor INTEGER, outcome TEXT,
        price INTEGER, warranty_hours REAL);
    CREATE TABLE IF NOT EXISTS my_broken (broke_at INTEGER, name TEXT, buy_seq INTEGER, sku TEXT, shop TEXT,
        paid INTEGER, warranty_hours REAL, hours_after_buy REAL, in_warranty INTEGER, refunded INTEGER,
        PRIMARY KEY (broke_at, name));
    CREATE TABLE IF NOT EXISTS want_replies (ts REAL, post_id INTEGER PRIMARY KEY, category TEXT, sku TEXT);
    CREATE TABLE IF NOT EXISTS variant_weights (ts REAL, key TEXT, variant TEXT, weight REAL, metric REAL,
        n INTEGER, reason TEXT);
    CREATE TABLE IF NOT EXISTS cash (ts REAL PRIMARY KEY, balance INTEGER, stock_cost INTEGER, units INTEGER,
        storage_per_day INTEGER);
    """)
    cols = [r[1] for r in db.execute("PRAGMA table_info(settles)")]
    if "unit" not in cols:
        db.execute("ALTER TABLE settles ADD COLUMN unit TEXT")       # links my sales to my_stock (cost)
    if "warranty_hours" not in cols:
        db.execute("ALTER TABLE settles ADD COLUMN warranty_hours REAL")
    db.execute("CREATE INDEX IF NOT EXISTS settles_unit ON settles (unit)")     # perishable / unpaid lookups
    if "extra" not in [r[1] for r in db.execute("PRAGMA table_info(hourly)")]:
        db.execute("ALTER TABLE hourly ADD COLUMN extra TEXT")
    if "mo_variant" not in [r[1] for r in db.execute("PRAGMA table_info(buyer_deals)")]:
        db.execute("ALTER TABLE buyer_deals ADD COLUMN mo_variant TEXT")    # the min-offers variant
    db.execute("CREATE TABLE IF NOT EXISTS my_results (seq INTEGER PRIMARY KEY, ts REAL, buyer TEXT, ord TEXT, "
               "stars INTEGER, review TEXT, has_receipt INTEGER)")          # reviews of my shop (ledger 'result')
    db.execute("CREATE TABLE IF NOT EXISTS board_hist (ts REAL PRIMARY KEY, shop TEXT, buyer TEXT)")  # board rows
    if "reason" not in [r[1] for r in db.execute("PRAGMA table_info(cases)")]:
        db.execute("ALTER TABLE cases ADD COLUMN reason TEXT")
    return db


def meta(db, k, default=None):
    row = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else default


def sku_avg(db, sku, now):
    row = db.execute("SELECT AVG(amount), COUNT(*) FROM settles WHERE sku=? AND ts>=?",
                     (sku, now - AVG_WINDOW_S)).fetchone()
    if row[1] >= MIN_N:
        return row[0], row[1]
    row = db.execute("SELECT AVG(amount), COUNT(*) FROM settles WHERE sku=?", (sku,)).fetchone()
    return (row[0], row[1]) if row[1] else (None, 0)


def fetch_ledger(c, after):
    """All ledger entries after `after` (negative: that many before the newest), 500 per call."""
    out = []
    while True:
        r = c.get(f"{MARKET}/api/ledger", params={"after": after})
        r.raise_for_status()
        page = r.json()
        out += page
        if len(page) < 500:
            return out
        after = page[-1]["seq"]


def backfill_units(db):
    """Settles of my shop stored before the unit column existed: read their ledger entry again."""
    rows = db.execute("SELECT seq FROM settles WHERE unit IS NULL AND (shop=? OR shop_url IN "
                      "(SELECT shop_url FROM settles WHERE shop=?)) LIMIT 50", (ME, ME)).fetchall()
    if not rows:
        return
    with httpx.Client(timeout=30) as c:
        for (seq,) in rows:
            try:
                page = c.get(f"{MARKET}/api/ledger", params={"after": seq - 1}).json()
            except Exception:
                continue
            e = next((e for e in page if e["seq"] == seq), None)
            unit = ((e or {}).get("data") or {}).get("unit") or "-"
            db.execute("UPDATE settles SET unit=? WHERE seq=?", (unit, seq))


def refresh(force=False, log=print):
    db = connect()
    now = time.time()
    if not force and now - float(meta(db, "refreshed", 0)) < REFRESH_S:
        db.close()
        return False
    with httpx.Client(timeout=30) as c:
        last = int(meta(db, "last_seq", 0))
        entries = fetch_ledger(c, last if last else -BOOTSTRAP)
        try:
            names = {s["url"].rstrip("/"): s["name"] for s in c.get(f"{MARKET}/api/shops").json()["shops"]}
        except Exception:
            names = {}
    added = 0
    mine = {u for (u,) in db.execute("SELECT DISTINCT shop_url FROM settles WHERE shop=?", (ME,))} | \
        {u for u, n in names.items() if n == ME}
    for e in entries:
        if e.get("kind") == "result":
            d = e["data"]
            if (d.get("shop") or "").rstrip("/") in mine or (d.get("receipt") or {}).get("shop") == SHOP_DISPLAY:
                db.execute("INSERT OR IGNORE INTO my_results VALUES (?,?,?,?,?,?,?)",
                           (e["seq"], e["ts"], d.get("buyer"), d.get("order"), d.get("stars"),
                            (d.get("review") or "")[:300], int(bool(d.get("receipt")))))
            continue
        if e.get("kind") == "broken":
            d = e["data"]
            url = (d.get("shop") or "").rstrip("/")
            db.execute("INSERT OR IGNORE INTO broken VALUES (?,?,?,?,?,?,?,?,?)",
                       (e["seq"], e["ts"], d.get("buyer"), url, names.get(url), d.get("item"),
                        d.get("hours_after_sale"), d.get("warranty_hours"), d.get("refunded")))
            continue
        if e.get("kind") != "settle":
            continue
        d = e["data"]
        sku = d.get("sku") or d.get("item")
        avg, _ = sku_avg(db, sku, e["ts"])
        url = (d.get("shop") or "").rstrip("/")
        db.execute("INSERT OR IGNORE INTO settles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (e["seq"], e["ts"], d.get("buyer"), url, names.get(url), sku, d.get("name"), d.get("grade"),
                    d.get("amount"), None if d.get("claim_honest") is None else int(bool(d["claim_honest"])), avg,
                    d.get("unit"), d.get("warranty_hours")))
        added += 1
    if entries:
        db.execute("INSERT OR REPLACE INTO meta VALUES ('last_seq', ?)", (str(entries[-1]["seq"]),))
    if dbutil.on():                                   # 11/12 ~15:15: the new rows committed before the long part
        db.commit()
    backfill_units(db)
    match_broken(db)
    board_snapshot(db, now, log)

    # snapshot: per SKU sold in the last 30 minutes, plus every SKU I hold or bought before
    stats = {"ts": now, "skus": {}, "hot": [], "my_buys": {}}
    rows = db.execute("""SELECT sku, MAX(name), SUM(ts>=?), COUNT(*) FROM settles WHERE ts>=? GROUP BY sku""",
                      (now - 300, now - 1800)).fetchall()
    for sku, name, s5, s30 in rows:
        avg, n = sku_avg(db, sku, now)
        stats["skus"][sku] = {"name": name, "avg": round(avg), "n": n, "sold_5m": s5, "sold_30m": s30}
        db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?)", (now, sku, avg, n, s5, s30))
    for (sku,) in db.execute("SELECT DISTINCT sku FROM settles WHERE buyer=? OR shop=? "
                             "UNION SELECT DISTINCT sku FROM my_stock", (ME, ME)).fetchall():
        if sku not in stats["skus"]:
            avg, n = sku_avg(db, sku, now)
            if avg is not None:
                stats["skus"][sku] = {"name": None, "avg": round(avg), "n": n, "sold_5m": 0, "sold_30m": 0}
    stats["hot"] = sorted(stats["skus"], key=lambda k: (stats["skus"][k]["sold_5m"], stats["skus"][k]["sold_30m"]),
                          reverse=True)[:HOT_N]
    for sku, paid, k in db.execute("SELECT sku, AVG(amount), COUNT(*) FROM settles WHERE buyer=? GROUP BY sku", (ME,)):
        stats["my_buys"][sku] = {"avg_paid": round(paid), "n": k}

    stats.update(signals(db, names))
    try:                                              # 11/11 ~20:10: market break rate per leaf category (top_high warranty)
        from depts import top_high
        with open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8") as f:
            cat = json.load(f)
        stats["cat_break"], stats["cat_n"] = top_high.cat_break(db, cat)
        stats["cat_break_a"], stats["cat_n_a"] = top_high.cat_break_a_cached(db, cat)   # 11/11 ~20:50: the A grade rate
    except Exception as e:
        log(f"[market_db] category break rates: {type(e).__name__}: {e}")
    db.execute("INSERT OR REPLACE INTO meta VALUES ('refreshed', ?)", (str(now),))
    db.commit()
    db.close()
    tmp = STATS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False)
    os.replace(tmp, STATS_PATH)                       # readers never see a half-written file
    bad = [k for k, v in stats["shop_flags"].items() if v["bad"] and not k.startswith("http")]
    log(f"[market_db] forum wanted {len(stats['wanted'])} SKUs, categories {stats['wanted_cats'][:5]}; shops to avoid: {', '.join(bad) or 'none'}")
    log(f"[market_db] +{added} settles, {len(stats['skus'])} SKUs priced, hot: "
        + ", ".join(f"{stats['skus'][k]['name'] or k}({stats['skus'][k]['sold_30m']})" for k in stats["hot"][:3]))
    return True


SHOP_DISPLAY = "Choo Shop"
BOARD_EVERY_S = 1800


def board_snapshot(db, now, log=print):
    """Every 30 min: my shop's and my buyer's board rows (stars, deals, book, profit, surplus, all) -> board_hist."""
    if now - float(meta(db, "board_ts", 0)) < BOARD_EVERY_S:
        return
    early = not dbutil.on()                           # 11/12 ~15:15: on -> the board call outside the write
    if early:
        db.execute("INSERT OR REPLACE INTO meta VALUES ('board_ts', ?)", (str(now),))
    try:
        j = httpx.get(f"{MARKET}/api/board", params={"round": 3}, timeout=30).json()
    except Exception as e:
        log(f"[market_db] board unavailable: {e}")
        if not early:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('board_ts', ?)", (str(now),))
        return
    if not early:
        db.execute("INSERT OR REPLACE INTO meta VALUES ('board_ts', ?)", (str(now),))
    keep = lambda x: {k: v for k, v in (x or {}).items() if k not in ("owner", "card")}
    shop = next((x for x in j.get("shops", []) if x.get("name") == ME), None)
    buyer = next((x for x in j.get("buyers", []) if x.get("name") == ME), None)
    rank = lambda rows, x: 1 + sum(1 for r in rows if (r.get("all") or 0) > (x or {}).get("all", 0)) if x else None
    s, b = keep(shop), keep(buyer)
    s["rank"], b["rank"] = rank(j.get("shops", []), shop), rank(j.get("buyers", []), buyer)
    if buyer:                                      # the buyer's place by Saved alone (buyer min-offers guard, 11/07)
        b["saved_rank"] = 1 + sum(1 for r in j.get("buyers", []) if (r.get("saved") or 0) > (buyer.get("saved") or 0))
    db.execute("INSERT OR REPLACE INTO board_hist VALUES (?,?,?)", (now, json.dumps(s), json.dumps(b)))


WANT = re.compile(r"구합니다|구해요|찾습니다|찾아요|삽니다|wanted|looking for|want to buy|need", re.I)
SKU_RE = re.compile(r"\bG[0-9a-f]{8}\b")   # 11/11 ~21:50: was backspace characters (never matched)
FORUM_PATH = os.path.join(HERE, ".market", "forum.json")
BAD_DISHONEST = 2          # a shop with this many dishonest grade claims is avoided ...
BAD_DISHONEST_SHARE = 0.40  # 11/14 ~03:00 (person): 0.05 -> 0.40 with the shop_avoid grade rule (20-40%: negotiate, grade
#                            one lower; ovotnwjd-maker 12% was blocked). ... at least this share of its settles (person's decision 11/07
#                            ~16:10: a count alone blocked Kiro729 / 11im / winun1127 at 0.1-0.5% untrue claims)
BAD_BROKEN_SHARE = 0.10    # ... or with this share of its sold units broken (at least 3)


def is_bad(f):
    """A shop the buyer avoids on the ledger: dishonest grade claims (BAD_DISHONEST and BAD_DISHONEST_SHARE) or broken units."""
    d = f["dishonest"] + f.get("forum_dishonest", 0)
    return bool((d >= BAD_DISHONEST and d / max(f["deals"], 1) >= BAD_DISHONEST_SHARE)
                or (f["broken"] >= 3 and f["broken"] / max(f["deals"], 1) >= BAD_BROKEN_SHARE))


def catalog_leaves():
    try:
        with open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8") as f:
            return {(c.get("category") or "").split(">")[-1] for c in json.load(f).values()} - {""}
    except (OSError, ValueError):
        return set()


def load_forum():
    try:
        with open(FORUM_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def demand_posts(posts=None, leaves=None):
    """Buyer posts that ask for something ('구합니다', 'wanted', ...): [{id, ts, author, skus, cats}].
    The one keyword extractor: restock (what to buy) and forum_post (what to answer) both use it.
    Only codes and catalog category names come out of a post; its text never reaches a harness."""
    posts = load_forum() if posts is None else posts
    leaves = catalog_leaves() if leaves is None else leaves
    out = []
    for p in posts:
        text = str(p.get("text", ""))
        if p.get("role") != "buyer" or not WANT.search(text):
            continue
        cats = [leaf for leaf in leaves if any(part and part in text for part in leaf.split("/"))]
        out.append({"id": p.get("id"), "ts": p.get("ts") or 0, "author": p.get("author"),
                    "skus": SKU_RE.findall(text), "cats": cats})
    return out


def match_broken(db):
    """broken.jsonl (name, broke_at: goods I bought that broke) matched to my purchase of that name before it;
    in warranty when it broke within the warranty hours; refunded from the market's broken entry."""
    try:
        with open(os.path.join(HERE, ".market", "broken.jsonl"), encoding="utf-8") as f:
            news = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return
    for n in news:
        if db.execute("SELECT 1 FROM my_broken WHERE broke_at=? AND name=?", (n["broke_at"], n["name"])).fetchone():
            continue
        row = db.execute("SELECT seq, ts, sku, COALESCE(shop, shop_url), amount, warranty_hours FROM settles "
                         "WHERE buyer=? AND name=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                         (ME, n["name"], n["broke_at"])).fetchone()
        if not row:
            continue
        seq, ts, sku, shop, paid, wh = row
        after = (n["broke_at"] - ts) / 3600
        ref = db.execute("SELECT refunded FROM broken WHERE buyer=? AND sku=? AND ABS(ts-?)<120",
                         (ME, sku, n["broke_at"])).fetchone()
        db.execute("INSERT INTO my_broken VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (n["broke_at"], n["name"], seq, sku, shop, paid, wh, after,
                    int(bool(wh) and after <= wh), None if ref is None else int(bool(ref[0]))))


def signals(db, names):
    """Shop flags, SKU break rates and forum demand. Everything is checked against the ledger."""
    flags = {}

    def flag(key, field, k=1):
        if key:
            f = flags.setdefault(key, {"deals": 0, "dishonest": 0, "broken": 0, "forum_dishonest": 0})
            f[field] += k

    for url, shop, n, bad in db.execute("SELECT shop_url, MAX(shop), COUNT(*), SUM(claim_honest=0) FROM settles "
                                        "GROUP BY shop_url"):
        for key in (url, shop):
            flag(key, "deals", n)
            flag(key, "dishonest", bad or 0)
    for url, shop, n in db.execute("SELECT shop_url, MAX(shop), COUNT(*) FROM broken GROUP BY shop_url"):
        for key in (url, shop):
            flag(key, "broken", n)

    sku_break = {}
    for sku, sold, broke in db.execute("SELECT s.sku, COUNT(*), (SELECT COUNT(*) FROM broken b WHERE b.sku=s.sku) "
                                       "FROM settles s GROUP BY s.sku HAVING COUNT(*) >= 5"):
        if broke:
            sku_break[sku] = round(broke / sold, 3)

    posts = load_forum()
    wanted, wanted_cats = {}, {}
    for d in demand_posts(posts):
        # 11/11 ~21:50 (person, option 1): the SKUs a post names do not reach the buying plan (stats "wanted" stays
        # as it was while SKU_RE never matched); they serve the forum replies (depts/cs) and forum_watch only
        for leaf in d["cats"]:
            wanted_cats[leaf] = wanted_cats.get(leaf, 0) + 1
    for p in posts:
        for r in p.get("refs") or []:            # a ref the market resolved: trust its data, not the post text
            if not (isinstance(r, dict) and r.get("found")):
                continue
            d = r.get("data") or {}
            if r.get("kind") == "settle" and d.get("claim_honest") is False:
                url = (d.get("shop") or "").rstrip("/")
                flag(url, "forum_dishonest")
                flag(names.get(url), "forum_dishonest")
    for f in flags.values():
        f["bad"] = is_bad(f)
    sku_n = {sku: n for sku, n in db.execute("SELECT sku, COUNT(*) FROM settles GROUP BY sku HAVING COUNT(*) >= 5")}
    grade_break = {}                              # per grade: breaks / units sold, over SKUs that ever broke;
    sold = dict(db.execute("SELECT grade, COUNT(*) FROM settles WHERE sku IN (SELECT DISTINCT sku FROM broken) "
                           "GROUP BY grade"))     # each break counts once, on the buyer's last purchase before it
    broke = dict(db.execute("SELECT (SELECT s.grade FROM settles s WHERE s.buyer=b.buyer AND s.sku=b.sku AND s.ts<=b.ts "
                            "ORDER BY s.ts DESC LIMIT 1) g, COUNT(*) FROM broken b GROUP BY g"))
    for g, n in sold.items():
        if g:
            grade_break[g] = round(broke.get(g, 0) / n, 3)
    return {"shop_flags": flags, "sku_break": sku_break, "sku_n": sku_n, "grade_break": grade_break,
            "shop_score": shop_scores(db),
            "wanted": sorted(wanted, key=wanted.get, reverse=True)[:20],
            "wanted_cats": sorted(wanted_cats, key=wanted_cats.get, reverse=True)[:10]}


def shop_scores(db):
    """[15] per shop name: cheap (its prices vs the SKU average when sold), honest (share of honest grade
    claims), success (my negotiations there that ended in a deal), each 0..1; score = their mean.
    Also how many of my failures there came while the 3-offer rule forced my counters."""
    out = {}
    for shop, ratio, honest, n in db.execute(
            "SELECT shop, AVG(amount / market_avg_at), AVG(claim_honest), COUNT(*) FROM settles "
            "WHERE shop IS NOT NULL AND market_avg_at > 0 GROUP BY shop"):
        out[shop] = {"ratio": round(ratio, 3), "cheap": round(min(1, max(0, (1.2 - ratio) / 0.4)), 3),
                     "honest": round(honest if honest is not None else 0.75, 3), "deals": n}
    for shop, tries, ok, guard in db.execute(
            "SELECT shop, COUNT(*), SUM(outcome='deal'), SUM(reason='haggle_guard') FROM buyer_deals "
            "WHERE outcome != 'skip' GROUP BY shop"):
        o = out.setdefault(shop, {"ratio": None, "cheap": 0.5, "honest": 0.75, "deals": 0})
        o.update(tries=tries, ok=ok or 0, guard_fails=guard or 0)
    for o in out.values():
        o["success"] = round((o.get("ok", 0) + 1) / (o.get("tries", 0) + 2), 3)     # 0.5 with no history
        o["score"] = round((o["cheap"] + o["honest"] + o["success"]) / 3, 3)
    return out


def record_stock(units, log=print):
    """Units my shop bought from supply, with the market average when they arrived."""
    db = connect()
    new = 0
    for u in units:
        if db.execute("SELECT 1 FROM my_stock WHERE unit=?", (u["unit"],)).fetchone():
            continue
        avg, _ = sku_avg(db, u["sku"], u.get("supplied_at") or time.time())
        db.execute("INSERT INTO my_stock VALUES (?,?,?,?,?,?,?)", (u["unit"], u["sku"], u.get("name"), u.get("cost"),
                                                                  u.get("grade"), u.get("supplied_at"), avg))
        new += 1
    db.commit()
    db.close()
    if new:
        log(f"[market_db] recorded {new} new stock units")


CASH_EVERY_S = 300


def record_cash(inv, log=print):
    """Every 5 minutes: cash, stock at cost and the storage it costs per day (rate from inventory.json)."""
    db = connect()
    now = time.time()
    try:
        if now - float(meta(db, "cash_ts", 0)) < CASH_EVERY_S:
            return None
        units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
        stock = sum(u.get("cost", 0) for u in units)
        storage = round(stock * float(inv.get("storage_per_day") or 0.01))
        row = (now, int(inv.get("balance", 0)), stock, len(units), storage)
        db.execute("INSERT OR REPLACE INTO cash VALUES (?,?,?,?,?)", row)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('cash_ts', ?)", (str(now),))
        db.commit()
        log(f"[cash] balance {row[1]:,}, stock at cost {stock:,} ({len(units)} units), storage ~{storage:,}/day")
        return row
    finally:
        db.close()


def load_stats():
    try:
        with open(STATS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"skus": {}, "hot": [], "my_buys": {}}


def price_of(sku, stats=None):
    """(market average, deals behind it) for a SKU, or (None, 0) when the market has too few deals."""
    s = (stats or load_stats())["skus"].get(sku)
    if not s or s["n"] < MIN_N:
        return None, (s or {}).get("n", 0)
    return s["avg"], s["n"]


def report():
    db = connect()
    print("my buys (buyer):  ts | sku | paid | market avg then")
    for r in db.execute("SELECT datetime(ts,'unixepoch','localtime'), name, amount, market_avg_at FROM settles "
                        "WHERE buyer=? ORDER BY ts DESC LIMIT 20", (ME,)):
        print("  ", r)
    print("my sales (shop):")
    for r in db.execute("SELECT datetime(ts,'unixepoch','localtime'), name, amount, market_avg_at FROM settles "
                        "WHERE shop=? ORDER BY ts DESC LIMIT 20", (ME,)):
        print("  ", r)
    print("my stock bought:", db.execute("SELECT COUNT(*), SUM(cost) FROM my_stock").fetchone())


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        refresh(force=True)
        st = load_stats()
        for k in st["hot"][:10]:
            print(k, st["skus"][k])
