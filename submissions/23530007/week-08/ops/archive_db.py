"""Archive every log and data source of the yms979 agents, and the market's public ledger and board, in one SQLite DB
(user 11-11: keep all logs and data in a DB). data/market_archive.db, not in git (several GB).

    .venv/bin/python ops/archive_db.py                 # incremental: new ledger pages, new log lines, a board snapshot
    .venv/bin/python ops/archive_db.py --ledger-pages 2000   # backfill more ledger per run (default 400 = 200k entries)
    .venv/bin/python ops/archive_db.py --stats

Tables
  ledger(seq PK, ts, round, kind, buyer, shop, sku, amount, grade, claim, claim_honest, warranty_hours, expires_at,
         stars, verified, data)              the market's public ledger, every entry since seq 0 (data = full JSON)
  board(ts PK, data)                         /api/board?round=3 every run (shop book/revenue/stars, buyer saved... over time)
  lines(source, n, ts, text)  PK(source, n)  every line of our logs (.log, archived .log.gz) and JSONL records
  sync(source PK, pos, mtime, size)          how far each source has been read
Sources: logs/*.log, logs/archive/*.gz, *.jsonl in the agent folder, .market/*.jsonl, reports/*.json.
Run every 15 min from watch_round.sh with a lock; a full ledger backfill takes a few hours of runs. Read-only toward
the market (GET only); no model call.
"""
import argparse
import fcntl
import gzip
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DB = HERE / "data" / "market_archive.db"
MARKET = "https://market.ourolabs.ai"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (seq INTEGER PRIMARY KEY, ts REAL, round INTEGER, kind TEXT, buyer TEXT, shop TEXT,
  sku TEXT, amount REAL, grade TEXT, claim TEXT, claim_honest INTEGER, warranty_hours REAL, expires_at REAL,
  stars INTEGER, verified INTEGER, data TEXT);
CREATE INDEX IF NOT EXISTS ledger_ts ON ledger(ts);
CREATE INDEX IF NOT EXISTS ledger_kind_buyer ON ledger(kind, buyer);
CREATE INDEX IF NOT EXISTS ledger_kind_shop ON ledger(kind, shop);
CREATE TABLE IF NOT EXISTS board (ts REAL PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS lines (source TEXT, n INTEGER, ts REAL, text TEXT, PRIMARY KEY (source, n));
CREATE TABLE IF NOT EXISTS sync (source TEXT PRIMARY KEY, pos INTEGER, mtime REAL, size INTEGER);
"""


def _get(path, timeout=30):
    with urllib.request.urlopen(f"{MARKET}{path}", timeout=timeout) as r:
        return json.load(r)


def connect():
    DB.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(DB, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def ledger(db, pages):
    """Forward from the highest stored seq; pages x 500 entries per run."""
    last = db.execute("SELECT MAX(seq) FROM ledger").fetchone()[0]
    after = last if last is not None else 0
    got = 0
    for _ in range(pages):
        try:
            page = _get(f"/api/ledger?after={after}")
        except Exception:  # noqa: BLE001
            break
        if not page:
            break
        rows = []
        for e in page:
            d = e.get("data") or {}
            rows.append((e["seq"], e.get("ts"), e.get("round"), e.get("kind"), d.get("buyer"),
                         d.get("shop") if isinstance(d.get("shop"), str) else None, d.get("sku") or d.get("item"),
                         d.get("amount") or d.get("price"), d.get("grade"), d.get("claim"),
                         None if d.get("claim_honest") is None else int(bool(d["claim_honest"])),
                         d.get("warranty_hours"), d.get("expires_at"), d.get("stars"),
                         None if d.get("verified") is None else int(bool(d["verified"])),
                         json.dumps(e, ensure_ascii=False, separators=(",", ":"))))
        db.executemany("INSERT OR IGNORE INTO ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
        got += len(rows)
        after = page[-1]["seq"]
        if len(page) < 500:
            break
    return got


def board(db):
    try:
        b = _get("/api/board?round=3", timeout=20)
        db.execute("INSERT OR IGNORE INTO board VALUES (?,?)", (time.time(), json.dumps(b, ensure_ascii=False, separators=(",", ":"))))
        db.commit()
        return 1
    except Exception:  # noqa: BLE001
        return 0


def _sources():
    out = []
    out += sorted((HERE / "logs").glob("*.log"))
    out += sorted((HERE / "logs" / "archive").glob("*.gz"))
    out += sorted(HERE.glob("*.jsonl"))
    out += sorted((HERE / ".market").glob("*.jsonl"))
    out += sorted((HERE / "reports").glob("*.json")) if (HERE / "reports").is_dir() else []
    return out


def _ts(text):
    try:
        if text.startswith("{"):
            j = json.loads(text)
            t = j.get("t") or j.get("ts")
            return float(t) if t is not None else None
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def files(db):
    """Text sources line by line. Plain files resume from their byte offset; a file that shrank (rotated) restarts
    under a new source name (name@mtime); .gz archives are read once (they never change)."""
    added = 0
    for p in _sources():
        rel = str(p.relative_to(HERE))
        st = p.stat()
        row = db.execute("SELECT pos, mtime, size FROM sync WHERE source=?", (rel,)).fetchone()
        if p.suffix == ".gz":
            if row:
                continue
            with gzip.open(p, "rt", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            db.executemany("INSERT OR IGNORE INTO lines VALUES (?,?,?,?)",
                           [(rel, i, _ts(l), l) for i, l in enumerate(lines)])
            db.execute("INSERT OR REPLACE INTO sync VALUES (?,?,?,?)", (rel, len(lines), st.st_mtime, st.st_size))
            db.commit()
            added += len(lines)
            continue
        pos, n0 = 0, 0
        if row:
            pos, _, size = row
            if st.st_size < size:                     # rotated or truncated: keep the old rows under name@time
                db.execute("UPDATE lines SET source=? WHERE source=?", (f"{rel}@{int(time.time())}", rel))
                pos = 0
            n0 = db.execute("SELECT COALESCE(MAX(n)+1,0) FROM lines WHERE source=?", (rel,)).fetchone()[0]
        if row and pos >= st.st_size:
            continue
        with open(p, "rb") as f:
            f.seek(pos)
            chunk = f.read()
        cut = chunk.rfind(b"\n") + 1                  # only whole lines; the rest waits for the next run
        if cut <= 0:
            continue
        lines = chunk[:cut].decode("utf-8", errors="replace").splitlines()
        db.executemany("INSERT OR IGNORE INTO lines VALUES (?,?,?,?)",
                       [(rel, n0 + i, _ts(l), l) for i, l in enumerate(lines)])
        db.execute("INSERT OR REPLACE INTO sync VALUES (?,?,?,?)", (rel, pos + cut, st.st_mtime, pos + cut))
        db.commit()
        added += len(lines)
    return added


def stats(db):
    q = lambda s: db.execute(s).fetchone()
    print("db", DB, f"{DB.stat().st_size / 1e6:.0f} MB")
    print("ledger", q("SELECT COUNT(*), MIN(seq), MAX(seq) FROM ledger"))
    print("ledger round 3 settles", q("SELECT COUNT(*) FROM ledger WHERE round=3 AND kind='settle'"))
    print("board snapshots", q("SELECT COUNT(*), MIN(ts), MAX(ts) FROM board"))
    print("lines", q("SELECT COUNT(*), COUNT(DISTINCT source) FROM lines"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger-pages", type=int, default=400)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    lock = open(HERE / ".market" / "archive_db.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if not a.stats:
            raise SystemExit(0)
    db = connect()
    if a.stats:
        stats(db)
        return
    t = time.time()
    nb = board(db)
    nf = files(db)
    nl = ledger(db, a.ledger_pages)
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC archive: board +{nb}, lines +{nf}, ledger +{nl} "
          f"in {time.time() - t:.0f} s", file=sys.stderr)


if __name__ == "__main__":
    main()
