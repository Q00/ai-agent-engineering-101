"""Week 08 collector: archives the market into SQLite so the shop's estimators read history fast and the same way twice.

    python collector.py                     # from the first entry of Round 3, then follow the market
    python collector.py --from-seq 1650000  # or from a ledger seq
    python collector.py --once              # one pass of everything, then exit (for testing)

Why: the newsvendor quantities and the Thompson-sampling prices need what sold, at what price and when. The market
publishes that only on the ledger (1.6M entries by the end of Round 2), and the kit's .market/ files hold only the
present: forum.json is the newest 100 posts and is overwritten, inventory.json is our shop right now. Reading the
ledger over HTTP on every decision is slow and changes under us; a local copy keyed by seq is neither.

What is kept (data/market.db by default):
  ledger        every entry from the start point on, by seq; resumes from MAX(seq). The start of a round is found
                by binary search over seq with after=N probes (about 21 requests), not by paging from seq 1.
  forum_posts   each post once, by id. GET /api/forum takes `limit` and `since` (both whole numbers; anything else
                is a 400), no other parameter. When this was written the forum was empty, so whether `since` is a
                post id or a time could not be checked: the collector asks with since=<newest id> for new posts and
                re-reads the newest `limit` posts every cycle for the votes, and keeps posts by id either way.
  forum_votes   (post, polled_at, likes, dislikes, score) only when the counts of a post change.
  inventory     our shop's .market/inventory.json, every 60 s, only when it changed.
  demand        GET /api/demand, only when its content hash changes.
  catalog       GET /api/catalog (SKU -> name, category, list_price, kind, shelf_hours), checked hourly,
                rewritten only when its hash changes; catalog_versions records each change.
  shops         GET /api/shops: url -> name (settle entries name the shop by URL), first and last seen.
  board         GET /api/board (current round only), every 10 min, only when changed.

Negotiations are not logged here; that belongs in the seller.
Every network or parse error is one log line and the task is tried again next cycle; the loop never stops.
"""
import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
PAGE = 500                    # the ledger returns at most 500 entries per call
LEDGER_EVERY = 30
FORUM_EVERY = 30
INVENTORY_EVERY = 60
DEMAND_EVERY = 300
CATALOG_EVERY = 3600
SHOPS_EVERY = 300
BOARD_EVERY = 600
FORUM_LIMIT = 1000            # the kit asks for 100; ask for more in case the market keeps more
PAGES_PER_CYCLE = 40          # a long ledger catch-up yields to the other tasks every 20,000 entries

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ledger (
    seq INTEGER PRIMARY KEY, ts REAL, round INTEGER, kind TEXT, data TEXT, hash TEXT);
CREATE INDEX IF NOT EXISTS ledger_round_kind ON ledger (round, kind);
CREATE INDEX IF NOT EXISTS ledger_kind_ts ON ledger (kind, ts);
CREATE TABLE IF NOT EXISTS forum_posts (
    id INTEGER PRIMARY KEY, ts REAL, author TEXT, role TEXT, thread INTEGER, text TEXT, refs TEXT, raw TEXT,
    first_seen REAL);
CREATE TABLE IF NOT EXISTS forum_votes (
    post INTEGER, polled_at REAL, likes INTEGER, dislikes INTEGER, score REAL, PRIMARY KEY (post, polled_at));
CREATE TABLE IF NOT EXISTS inventory (ts REAL PRIMARY KEY, balance REAL, n_units INTEGER, raw TEXT);
CREATE TABLE IF NOT EXISTS demand (ts REAL PRIMARY KEY, hash TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS catalog (
    sku TEXT PRIMARY KEY, name TEXT, category TEXT, list_price REAL, kind TEXT, shelf_hours REAL, updated REAL);
CREATE TABLE IF NOT EXISTS catalog_versions (ts REAL PRIMARY KEY, hash TEXT, n_skus INTEGER);
CREATE TABLE IF NOT EXISTS shops (url TEXT PRIMARY KEY, name TEXT, pubkey TEXT, first_seen REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS board (ts REAL PRIMARY KEY, round INTEGER, hash TEXT, raw TEXT);
"""


def log(msg):
    print(f"{time.strftime('%m-%d %H:%M:%S')} {msg}", flush=True)


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


class Collector:
    def __init__(self, args):
        self.base = args.market.rstrip("/")
        self.dir = Path(args.dir).resolve()
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(args.db)
        self.db.execute("PRAGMA journal_mode=WAL")   # the estimators can read while we write
        self.db.executescript(SCHEMA)
        self.c = httpx.Client(timeout=httpx.Timeout(20, connect=10))
        self.args = args
        self.cursor = None                           # last ledger seq read (stored or skipped)
        self.reset_checked = 0                       # when we last checked that the ledger did not restart its seq

    # --- meta: small state that must survive a restart (ledger cursor, round filter, last hashes)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))

    def fetch(self, path, **params):
        r = self.c.get(f"{self.base}{path}", params=params)
        r.raise_for_status()
        return r.json()

    # --- ledger

    def first_at(self, seq):
        """The first entry with seq >= `seq`, or None past the newest."""
        rows = self.fetch("/api/ledger", after=seq - 1)
        return rows[0] if rows else None

    def find_round_start(self, rnd):
        """Smallest seq whose entry is in round >= rnd, by binary search (rounds only go up along the ledger).
        None when the newest entry is still in an earlier round."""
        newest = self.fetch("/api/ledger", after=-1)[-1]
        if newest["round"] < rnd:
            return None, newest["seq"]
        lo, hi, probes = 1, newest["seq"], 0       # invariant: the answer is in [lo, hi]
        while lo < hi:
            mid = (lo + hi) // 2
            e = self.first_at(mid)
            probes += 1
            if e["round"] >= rnd:
                hi = mid
            else:
                lo = e["seq"] + 1                   # skips gaps in seq, if any
        start = self.first_at(lo)["seq"]           # lo itself may sit in a gap
        log(f"[ledger] round {rnd} starts at seq {start} ({probes + 1} probes)")
        return start, newest["seq"]

    def ledger_start(self):
        """The cursor (last seq already handled) and the lowest round to keep. Resume wins over the flags."""
        top = self.db.execute("SELECT MAX(seq) FROM ledger").fetchone()[0]
        saved = self.get("ledger_cursor")
        min_round = int(self.get("min_round", 0))
        if top is not None or saved is not None:
            cur = max(top or 0, int(saved or 0))
            log(f"[ledger] resume after seq {cur} (rounds >= {min_round})")
            return cur, min_round
        if self.args.from_seq is not None:
            self.put("min_round", 0)
            return self.args.from_seq - 1, 0
        rnd = self.args.from_round
        start, newest = self.find_round_start(rnd)
        self.put("min_round", rnd)
        if start is None:
            log(f"[ledger] round {rnd} has not started (newest seq {newest}); waiting from there")
            return newest, rnd
        return start - 1, rnd

    def ledger(self):
        """Pages forward from the cursor. Returns True when caught up (the last page was short)."""
        if self.cursor is None:
            self.cursor, self.min_round = self.ledger_start()
        stored = 0
        for _ in range(PAGES_PER_CYCLE):
            rows = self.fetch("/api/ledger", after=self.cursor)
            keep = [(e["seq"], e["ts"], e["round"], e["kind"], dumps(e["data"]), e.get("hash"))
                    for e in rows if e["round"] >= self.min_round]
            self.db.executemany("INSERT OR IGNORE INTO ledger VALUES (?, ?, ?, ?, ?, ?)", keep)
            if rows:
                self.cursor = rows[-1]["seq"]
                self.put("ledger_cursor", self.cursor)
            self.db.commit()
            stored += len(keep)
            if not rows and time.time() - self.reset_checked > 300:
                self.reset_checked = time.time()   # a new round may restart seq: then nothing comes after our cursor
                newest = self.fetch("/api/ledger", after=-1)
                if newest and newest[-1]["seq"] < self.cursor:
                    log(f"[ledger] newest seq {newest[-1]['seq']} is below our cursor {self.cursor}: the ledger "
                        "restarted, reading it from the start")
                    self.cursor = 0
                    self.put("ledger_cursor", 0)
                    self.db.commit()
                    continue
            if len(rows) < PAGE:
                if stored:
                    log(f"[ledger] +{stored} up to seq {self.cursor}")
                return True
        log(f"[ledger] +{stored} up to seq {self.cursor} (catching up)")
        return False

    # --- forum

    def forum(self):
        top = self.db.execute("SELECT MAX(id) FROM forum_posts").fetchone()[0]
        posts = {p["id"]: p for p in self.fetch("/api/forum", limit=FORUM_LIMIT)}    # newest, for the votes
        if top is not None:
            posts.update({p["id"]: p for p in self.fetch("/api/forum", since=top, limit=FORUM_LIMIT)})
        if len(posts) == FORUM_LIMIT:
            log(f"[forum] got exactly {FORUM_LIMIT} posts: older ones may be missed")
        now, new, changed = time.time(), 0, 0
        for p in posts.values():
            cur = self.db.execute("INSERT OR IGNORE INTO forum_posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                  (p["id"], p.get("ts"), p.get("author"), p.get("role"), p.get("thread"),
                                   p.get("text"), dumps(p.get("refs") or []), dumps(p), now))
            new += cur.rowcount
            votes = (p.get("likes"), p.get("dislikes"), p.get("score"))
            last = self.db.execute("SELECT likes, dislikes, score FROM forum_votes WHERE post=? "
                                   "ORDER BY polled_at DESC LIMIT 1", (p["id"],)).fetchone()
            if last != votes:
                self.db.execute("INSERT INTO forum_votes VALUES (?, ?, ?, ?, ?)", (p["id"], now, *votes))
                changed += 1
        self.db.commit()
        if new or changed:
            log(f"[forum] +{new} posts, {changed} vote changes")
        return True

    # --- our shop's inventory (a local file the kit rewrites every 10 s)

    def inventory(self):
        path = self.dir / ".market" / "inventory.json"
        if not path.exists():
            return True
        inv = json.loads(path.read_text(encoding="utf-8"))
        h = digest(inv)
        if h == self.get("inventory_hash"):
            return True
        units = inv.get("units") or []
        raw = None                                # the whole file (certificates and all) once an hour, not per change
        if time.time() - float(self.get("inventory_raw_at") or 0) >= 3600:
            raw = dumps(inv)
            self.put("inventory_raw_at", str(time.time()))
        self.db.execute("INSERT OR REPLACE INTO inventory VALUES (?, ?, ?, ?)",
                        (time.time(), inv.get("balance"), len(units), raw))
        self.put("inventory_hash", h)
        self.db.commit()
        log(f"[inventory] balance {inv.get('balance')}, {len(units)} units")
        return True

    # --- market-wide snapshots kept only when they change

    def snapshot(self, name, rows_sql, obj, extra=()):
        h = digest(obj)
        if h == self.get(f"{name}_hash"):
            return False
        self.db.execute(rows_sql, (time.time(), *extra, h, dumps(obj)))
        self.put(f"{name}_hash", h)
        self.db.commit()
        return True

    def demand(self):
        d = self.fetch("/api/demand")
        if self.snapshot("demand", "INSERT OR REPLACE INTO demand VALUES (?, ?, ?)", d):
            log(f"[demand] changed: {len(d)} categories")
        return True

    def catalog(self):
        cat = self.fetch("/api/catalog")
        h = digest(cat)
        if h == self.get("catalog_hash"):
            return True
        now = time.time()
        self.db.executemany("INSERT OR REPLACE INTO catalog VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(s["sku"], s.get("name"), s.get("category"), s.get("list_price"), s.get("kind"),
                              s.get("shelf_hours"), now) for s in cat])
        self.db.execute("INSERT INTO catalog_versions VALUES (?, ?, ?)", (now, h, len(cat)))
        self.put("catalog_hash", h)
        self.db.commit()
        log(f"[catalog] changed: {len(cat)} SKUs")
        return True

    def shops(self):
        d = self.fetch("/api/shops")
        now, new = time.time(), 0
        for s in d.get("shops", []):
            cur = self.db.execute("INSERT OR IGNORE INTO shops VALUES (?, ?, ?, ?, ?)",
                                  (s["url"], s.get("name"), s.get("pubkey"), now, now))
            new += cur.rowcount
            if not cur.rowcount:
                self.db.execute("UPDATE shops SET name=?, pubkey=?, last_seen=? WHERE url=?",
                                (s.get("name"), s.get("pubkey"), now, s["url"]))
        self.db.commit()
        if new:
            log(f"[shops] +{new} shops ({len(d.get('shops', []))} listed)")
        return True

    def board(self):
        b = self.fetch("/api/board")
        if self.snapshot("board", "INSERT OR REPLACE INTO board VALUES (?, ?, ?, ?)", b, (b.get("round"),)):
            log(f"[board] snapshot, round {b.get('round')}, {len(b.get('shops', []))} shops")
        return True

    # --- the loop

    def run(self):
        tasks = [(self.ledger, LEDGER_EVERY), (self.forum, FORUM_EVERY), (self.inventory, INVENTORY_EVERY),
                 (self.demand, DEMAND_EVERY), (self.catalog, CATALOG_EVERY), (self.shops, SHOPS_EVERY),
                 (self.board, BOARD_EVERY)]
        due = {f.__name__: 0.0 for f, _ in tasks}
        while True:
            for f, every in tasks:
                if time.time() < due[f.__name__]:
                    continue
                done = True
                try:
                    done = f()
                    while self.args.once and not done:  # --once still catches the ledger up
                        done = f()
                except Exception as e:                   # one line, try again next cycle
                    self.db.rollback()
                    log(f"[{f.__name__}] {type(e).__name__}: {str(e)[:200]}")
                due[f.__name__] = time.time() + (every if done else 0)
            if self.args.once:
                return
            time.sleep(max(1.0, min(due.values()) - time.time()))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--market", default="https://market.ourolabs.ai")
    p.add_argument("--dir", default=str(HERE), help="agent folder (its .market/ holds inventory.json)")
    p.add_argument("--db", default=str(HERE / "data" / "market.db"))
    p.add_argument("--from-round", type=int, default=3, help="start the ledger at the first entry of this round")
    p.add_argument("--from-seq", type=int, help="start the ledger at this seq instead")
    p.add_argument("--once", action="store_true", help="one pass of everything, then exit")
    args = p.parse_args()
    log(f"[collector] {args.market} -> {args.db}")
    Collector(args).run()


if __name__ == "__main__":
    main()
