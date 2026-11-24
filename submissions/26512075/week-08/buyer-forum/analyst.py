"""Observe local forum snapshots and public ledger entries without trading."""

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from statistics import median
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

if __package__:
    from .storage import read_json, write_json
else:
    from storage import read_json, write_json

DB_NAME = "forum-analysis.sqlite3"
REPORT_NAME = "forum-candidates.json"
SKU_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,63}")


def market_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("market must be an HTTPS origin")
    return value.rstrip("/")


def connect(state, market):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    db_path = state / DB_NAME
    # The ledger is public, but the agent's analysis and selection history are private.
    db_path.touch(mode=0o600, exist_ok=True)
    db = sqlite3.connect(db_path, timeout=10)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY, ts REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS mentions (
            post_id INTEGER NOT NULL, sku TEXT NOT NULL, PRIMARY KEY (post_id, sku)
        );
        CREATE TABLE IF NOT EXISTS post_conditions (
            post_id INTEGER NOT NULL, sku TEXT NOT NULL, grade TEXT NOT NULL,
            warranty REAL NOT NULL, PRIMARY KEY (post_id, sku, grade, warranty)
        );
        CREATE TABLE IF NOT EXISTS pinned_settlements (seq INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS settlements (
            seq INTEGER PRIMARY KEY, ts REAL NOT NULL, sku TEXT NOT NULL,
            category TEXT NOT NULL, amount INTEGER NOT NULL, grade TEXT,
            warranty REAL, buyer TEXT, shop TEXT, order_id TEXT
        );
        CREATE INDEX IF NOT EXISTS posts_ts_idx ON posts(ts);
        CREATE INDEX IF NOT EXISTS settlements_ts_idx ON settlements(ts);
    """)
    current = db.execute("SELECT value FROM meta WHERE key = 'market'").fetchone()
    if current and current[0] != market:
        db.close()
        raise ValueError("analysis database belongs to a different market")
    db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('market', ?)", (market,))
    db.commit()
    return db


def ledger_batch(market, after):
    query = urlencode({"after": after})
    request = Request(f"{market}/api/ledger?{query}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=12) as response:
        rows = json.load(response)
    if not isinstance(rows, list):
        raise ValueError("ledger response must be an array")
    return rows


def catch_up_ledger(market, after, max_pages=20):
    """Bounded pagination; do not mistake a full page for the latest ledger."""
    combined = []
    for page_index in range(max_pages):
        page = ledger_batch(market, after)
        sequences = [r.get("seq") for r in page if isinstance(r, dict)]
        if (len(sequences) != len(page) or any(type(s) is not int for s in sequences)
                or any(b <= a for a, b in zip(sequences, sequences[1:]))
                or (after >= 0 and sequences and sequences[0] <= after)):
            raise ValueError("ledger pagination did not advance monotonically")
        combined.extend(page)
        if len(page) < 500:
            return combined, True, page_index + 1
        after = sequences[-1]
    return combined, False, max_pages


def store_forum(db, posts, catalog, now):
    if not isinstance(posts, list):
        raise ValueError("forum.json must contain an array")
    for post in posts:
        if not isinstance(post, dict) or type(post.get("id")) is not int:
            continue
        timestamp, text = post.get("ts"), post.get("text")
        if (not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
                or not math.isfinite(timestamp) or not 0 <= timestamp <= now + 60
                or not isinstance(text, str) or post.get("round") != 3):
            continue
        mentioned = set(SKU_TOKEN.findall(text)) & set(catalog)
        conditions = set()
        refs = post.get("refs") if isinstance(post.get("refs"), list) else []
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("found") is not True:
                continue
            data = ref.get("data")
            if isinstance(data, dict):
                sku = data.get("sku") or data.get("item")
                if isinstance(sku, str) and sku in catalog:
                    mentioned.add(sku)
                    grade, warranty = data.get("grade"), data.get("warranty_hours")
                    if "warranty_hours" in data and warranty is None:
                        warranty = -1
                    if (ref.get("kind") == "settle" and isinstance(grade, str) and grade in {"A", "B", "C"}
                            and isinstance(warranty, (int, float)) and not isinstance(warranty, bool)
                            and math.isfinite(warranty) and (warranty == -1 or 0 <= warranty <= 72)):
                        conditions.add((sku, grade, float(warranty)))
        db.execute("INSERT OR REPLACE INTO posts(id, ts) VALUES (?, ?)", (post["id"], timestamp))
        db.execute("DELETE FROM mentions WHERE post_id = ?", (post["id"],))
        db.execute("DELETE FROM post_conditions WHERE post_id = ?", (post["id"],))
        db.executemany("INSERT INTO mentions(post_id, sku) VALUES (?, ?)",
                       [(post["id"], sku) for sku in sorted(mentioned)])
        db.executemany("INSERT INTO post_conditions(post_id, sku, grade, warranty) VALUES (?, ?, ?, ?)",
                       [(post["id"], sku, grade, warranty) for sku, grade, warranty in sorted(conditions)])


def store_ledger(db, rows, catalog, now, advance_cursor=True):
    if not isinstance(rows, list):
        raise ValueError("ledger must contain an array")
    sequences = [row.get("seq") if isinstance(row, dict) else None for row in rows]
    if any(type(seq) is not int or seq < 0 for seq in sequences):
        raise ValueError("invalid ledger sequence")
    if sequences != sorted(set(sequences)):
        raise ValueError("ledger entries must have unique increasing sequences")
    for row in rows:
        data = row.get("data")
        if row.get("kind") != "settle" or row.get("round") != 3 or not isinstance(data, dict):
            continue
        sku = data.get("sku") or data.get("item")
        ts, amount = row.get("ts"), data.get("amount")
        if (not isinstance(sku, str) or sku not in catalog or type(amount) is not int or amount < 0
                or not isinstance(ts, (int, float)) or isinstance(ts, bool)
                or not math.isfinite(ts) or not 0 <= ts <= now + 60):
            continue
        product = catalog[sku]
        category = product.get("category", "") if isinstance(product, dict) else ""
        if not isinstance(category, str):
            category = ""
        if any(not isinstance(data.get(key), str) for key in ("buyer", "shop", "order")):
            continue
        warranty = data.get("warranty_hours")
        if "warranty_hours" in data and warranty is None:
            warranty = -1
        if (not isinstance(warranty, (int, float)) or isinstance(warranty, bool)
                or not math.isfinite(warranty) or not (warranty == -1 or 0 <= warranty <= 72)):
            warranty = None
        grade = data.get("grade")
        if not isinstance(grade, str) or grade not in {"A", "B", "C"}:
            grade = None
        db.execute("""INSERT OR REPLACE INTO settlements
                   (seq, ts, sku, category, amount, grade, warranty, buyer, shop, order_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (row["seq"], ts, sku, category, amount, grade, warranty,
                    data.get("buyer"), data.get("shop"), data.get("order")))
    if sequences and advance_cursor:
        db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('ledger_cursor', ?)",
                   (str(sequences[-1]),))


def create_report(db, catalog, now, forum_mtime, hours, min_settles, max_mentions, market, volume_min=3, volume_max=8):
    since = now - hours * 3600
    mentions = dict(db.execute("""SELECT m.sku, COUNT(*) FROM mentions m JOIN posts p ON p.id=m.post_id
                                 WHERE p.ts >= ? AND p.ts <= ? GROUP BY m.sku""", (since, now + 60)))
    grouped = {}
    grouped_conditions = {}
    for seq, ts, sku, amount, grade, warranty in db.execute(
            "SELECT seq, ts, sku, amount, grade, warranty FROM settlements WHERE ts >= ? AND ts <= ?",
            (since, now + 60)):
        grouped.setdefault(sku, []).append((seq, ts, amount, grade, warranty))
        if grade in {"A", "B", "C"} and warranty is not None:
            grouped_conditions.setdefault((sku, grade, float(warranty)), []).append((seq, ts, amount))
    candidates = []
    for sku, deals in grouped.items():
        count = mentions.get(sku, 0)
        if len(deals) < min_settles:
            continue
        product = catalog[sku]
        candidates.append({
            "sku": sku,
            "category": product.get("category", "") if isinstance(product, dict) else "",
            "sample_settlements": len(deals),
            "forum_posts_mentioning_sku": count,
            "sample_median_settled_price": median([row[2] for row in deals]),
            "last_settlement_at": max(row[1] for row in deals),
            "sample_ledger_refs": sorted((row[0] for row in deals), reverse=True)[:3],
        })
    candidates.sort(key=lambda row: (row["forum_posts_mentioning_sku"], -row["sample_settlements"], row["sku"]))
    condition_mentions = {
        (sku, grade, warranty): count
        for sku, grade, warranty, count in db.execute(
            """SELECT c.sku, c.grade, c.warranty, COUNT(*)
               FROM post_conditions c JOIN posts p ON p.id = c.post_id
               WHERE p.ts >= ? AND p.ts <= ? GROUP BY c.sku, c.grade, c.warranty""", (since, now + 60))
    }
    condition_candidates = []
    for (sku, grade, warranty), deals in grouped_conditions.items():
        count = condition_mentions.get((sku, grade, warranty), 0)
        if len(deals) < min_settles:
            continue
        condition_candidates.append({
            "sku": sku, "grade": grade, "warranty_hours": None if warranty == -1 else warranty,
            "warranty_is_null": warranty == -1, "warranty_status": "unknown" if warranty == -1 else "known",
            "sample_settlements": len(deals), "posts_with_matching_settlement_ref": count,
            "sample_median_settled_price": median([row[2] for row in deals]),
        })
    condition_candidates.sort(key=lambda row: (row["posts_with_matching_settlement_ref"],
                                                -row["sample_settlements"], row["sku"],
                                                row["grade"], row["warranty_hours"] or 0))
    ledger_span = db.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM settlements").fetchone()
    forum_span = db.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM posts").fetchone()
    observation = dict(db.execute("SELECT key, value FROM meta"))
    started = float(observation.get("coverage_since", now))
    coverage_ready = now - started >= hours * 3600
    medium = [row for row in candidates if volume_min <= row["sample_settlements"] <= volume_max] if coverage_ready else []
    # Volume band first, forum mentions only a tie-breaker. No popularity cutoff.
    medium.sort(key=lambda row: (abs(row["sample_settlements"] - (volume_min + volume_max) / 2),
                                 row["forum_posts_mentioning_sku"], row["sku"]))
    unknown = [{"sku": sku, "grade": grade, "warranty_status": "unknown", "sample_settlements": count}
               for sku, grade, count in db.execute("SELECT sku, grade, COUNT(*) FROM settlements WHERE ts >= ? AND (warranty IS NULL OR warranty=-1) GROUP BY sku, grade", (since,))]
    return {
        "coverage_since": started, "coverage_ready": coverage_ready,
        "volume_band": {"min": volume_min, "max": volume_max, "hours": hours},
        "medium_candidates": medium, "unknown_conditions": unknown,
        "market": market, "generated_at": now, "forum_snapshot_mtime": forum_mtime,
        "window_hours": hours, "minimum_sample_settlements": min_settles,
        "maximum_forum_mentions": max_mentions,
        "ledger_observed": {"earliest": ledger_span[0], "latest": ledger_span[1], "count": ledger_span[2]},
        "forum_observed": {"earliest": forum_span[0], "latest": forum_span[1], "count": forum_span[2]},
        "limitations": ["Only observed snapshots and ledger entries are counted.",
                        "Settled prices are historical, not current offers.",
                        "Grade and warranty mentions count structured settlement references only.",
                        "The initial ledger fetch covers at most 500 entries."],
        "candidates": candidates,
        "condition_candidates": condition_candidates,
    }


def prune_old_data(db, cutoff):
    db.execute("DELETE FROM mentions WHERE post_id IN (SELECT id FROM posts WHERE ts < ?)", (cutoff,))
    db.execute("DELETE FROM post_conditions WHERE post_id IN (SELECT id FROM posts WHERE ts < ?)", (cutoff,))
    db.execute("DELETE FROM posts WHERE ts < ?", (cutoff,))
    db.execute("DELETE FROM settlements WHERE ts < ? AND seq NOT IN (SELECT seq FROM pinned_settlements)",
               (cutoff,))


def run_once(state, market, hours=3, min_settles=2, max_mentions=1, ledger_file=None, now=None, volume_min=3, volume_max=8):
    state = Path(state)
    now = time.time() if now is None else now
    if hours <= 0 or min_settles < 1 or max_mentions < 0 or not 1 <= volume_min <= volume_max:
        raise ValueError("invalid analysis thresholds")
    catalog = read_json(state / "catalog.json")
    forum_path = state / "forum.json"
    posts = read_json(forum_path)
    if not isinstance(catalog, dict) or not isinstance(posts, list):
        raise ValueError("catalog.json and forum.json from the kit are required")
    market = market_origin(market)
    with closing(connect(state, market)) as db:
        cursor = db.execute("SELECT value FROM meta WHERE key = 'ledger_cursor'").fetchone()
        after = int(cursor[0]) if cursor else -500
        if ledger_file:
            rows = read_json(ledger_file)
            caught_up, pages = isinstance(rows, list) and len(rows) < 500, 1
        else:
            rows, caught_up, pages = catch_up_ledger(market, after)
        if not isinstance(rows, list):
            raise ValueError("ledger must contain an array")
        if cursor and rows and (not isinstance(rows[0], dict) or rows[0].get("seq", -1) <= after):
            raise ValueError("ledger batch must start after the saved cursor")
        with db:
            meta = dict(db.execute("SELECT key, value FROM meta"))
            # Truncated batches or long polling gaps cannot certify a full observation window.
            lagging = not caught_up
            started = float(meta.get("coverage_since", now))
            if now - float(meta.get("last_poll", now)) > 90 or lagging:
                started = now
            db.execute("INSERT OR REPLACE INTO meta VALUES ('coverage_since', ?)", (str(started),))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_poll', ?)", (str(now),))
            store_forum(db, posts, catalog, now)
            store_ledger(db, rows, catalog, now)
            prune_old_data(db, now - max(24, hours * 2) * 3600)
            report = create_report(db, catalog, now, forum_path.stat().st_mtime,
                                   hours, min_settles, max_mentions, market, volume_min, volume_max)
            report.update(ledger_caught_up=caught_up, ledger_pages_fetched=pages)
    write_json(state / REPORT_NAME, report)
    return report


def backfill_settlement(state, market, seq, now=None):
    """Read one older public ledger record without moving the live cursor."""
    state = Path(state)
    now = time.time() if now is None else now
    if type(seq) is not int or seq <= 0:
        raise ValueError("sequence must be a positive integer")
    catalog = read_json(state / "catalog.json")
    if not isinstance(catalog, dict):
        raise ValueError("catalog.json from the kit is required")
    market = market_origin(market)
    rows = ledger_batch(market, seq - 1)
    matches = [row for row in rows if isinstance(row, dict) and row.get("seq") == seq]
    if len(matches) != 1 or matches[0].get("kind") != "settle":
        raise ValueError("requested settlement not found")
    with closing(connect(state, market)) as db:
        with db:
            store_ledger(db, matches, catalog, now, advance_cursor=False)
            if not db.execute("SELECT 1 FROM settlements WHERE seq=?", (seq,)).fetchone():
                raise ValueError("settlement is incomplete or outside the catalog")
            db.execute("INSERT OR IGNORE INTO pinned_settlements(seq) VALUES (?)", (seq,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path(".market"))
    parser.add_argument("--market", default="https://market.ourolabs.ai")
    parser.add_argument("--window-hours", type=float, default=3)
    parser.add_argument("--min-settles", type=int, default=3)
    parser.add_argument("--volume-min", type=int, default=3)
    parser.add_argument("--volume-max", type=int, default=8)
    parser.add_argument("--max-mentions", type=int, default=1)
    parser.add_argument("--interval", type=int, default=30, help="watch interval in seconds, at least 30")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--ledger-file", type=Path, help="local JSON fixture, only with --once")
    parser.add_argument("--backfill-seq", type=int, help="fetch one older public settlement and exit")
    args = parser.parse_args()
    if args.interval < 30 or (args.ledger_file and not args.once) or (args.ledger_file and args.backfill_seq):
        parser.error("interval must be at least 30 seconds; --ledger-file requires --once")
    if args.backfill_seq is not None:
        try:
            backfill_settlement(args.state, args.market, args.backfill_seq)
        except (OSError, ValueError, sqlite3.Error) as exc:
            parser.exit(1, f"forum analyst: {exc}\n")
        print("settlement added to the local analysis database")
        return
    while True:
        try:
            report = run_once(args.state, args.market, args.window_hours, args.min_settles,
                              args.max_mentions, args.ledger_file, volume_min=args.volume_min, volume_max=args.volume_max)
            print(f"forum analyst: {len(report['candidates'])} candidates", flush=True)
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"forum analyst: {exc}", file=sys.stderr, flush=True)
            if args.once:
                raise SystemExit(1) from exc
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
