"""One-way copy of OUR market data from the server (memory.sqlite + logs/r3/*.jsonl) to Supabase Postgres, so local
agents (Grok, Codex, Claude Code, ...) can read it through the Supabase MCP server in read-only mode.

Run every 2 min by week08-supabase-sync.timer (ops/install_supabase_sync.sh). The market never waits on it: the shop
and the buyer keep using the local sqlite, and a Supabase outage only delays the copy.

What is copied (schema `market`; public market data is not copied: agents read /api/board and /api/ledger live):
  purchases        our buyer's orders and how they ended (upserted again for 8 h: paid, broken_at change)
  negotiations     every negotiation, ours as buyer and as shop
  our_settles      settles where we are the buyer or the shop (shop URLs from shops.mine=1)
  our_breaks       broken goods we bought or sold
  restocks, stockouts, notes   shop restock decisions, refusals for lack of stock, agent notes
  our_forum        our posts with their likes/dislikes (upserted again for 48 h)
  kpi, buyer_decisions, decisions, forum_log   logs/r3/*.jsonl, one row per line (data jsonb)
  board_snapshots  the public board every 10 min, compact (the market keeps no history of it)

Security: tables live in schema `market`, which the Supabase Data API does not expose, with RLS on and no policy,
and anon/authenticated get nothing; only the database owner (this script) writes. Agents connect through MCP with
?project_ref=<ref>&read_only=true. The connection string is read from .market/supabase.env (SUPABASE_DB_URL=...),
never from the repo and never printed.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

WEEK08 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEEK08))
from r3lib import config                                     # noqa: E402

ENV = WEEK08 / ".market" / "supabase.env"
STATE = WEEK08 / ".market" / "supabase-sync.json"
LOG = WEEK08 / "logs" / "deploy" / "supabase-sync.jsonl"
SCHEMA = "market"
MARKET = "https://market.ourolabs.ai"
REUPSERT_H = {"purchases": 8, "our_forum": 48}   # paid/broken settle within ~6 h; likes keep coming for days
LOG_EVERY_S = 1800
BOARD_EVERY_S = 600
BATCH = 2000
JSONL = {"kpi": "kpi.jsonl", "buyer_decisions": "buyer-decisions.jsonl", "decisions": "decisions.jsonl",
         "forum_log": "forum.jsonl"}
JSONL_FIRST_H = {"buyer_decisions": 72}       # 83 MB since 11-03: first copy keeps 72 h, then everything new

# name -> (sqlite table, primary key, how new rows are found, extra WHERE for "ours")
TABLES = {
    "purchases": ("purchases", "order_id", "recent", ""),
    "negotiations": ("negotiations", "id", "id", ""),
    "restocks": ("restocks", "id", "id", ""),
    "stockouts": ("stockouts", "id", "id", ""),
    "notes": ("notes", "id", "id", ""),
    "our_settles": ("settles", "seq", "id", "ours"),
    "our_breaks": ("breaks", "seq", "id", "ours"),
    "our_forum": ("forum", "id", "recent", "mine=1"),
}
PG_TYPE = {"INTEGER": "bigint", "REAL": "double precision", "TEXT": "text", "": "text"}


def log(event, **kw):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": round(time.time(), 1), "at": time.strftime("%m-%d %H:%M:%S"), "event": event, **kw}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def db_url():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url and ENV.exists():
        for line in ENV.read_text().splitlines():
            k, _, v = line.strip().removeprefix("export ").partition("=")
            if k.strip() == "SUPABASE_DB_URL":
                url = v.strip().strip('"').strip("'")
    if not url:
        raise SystemExit(f"no SUPABASE_DB_URL: put it in {ENV} (gitignored)")
    return url


def state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def ours_where(lite):
    names = sorted(config.MY_NAMES)
    urls = sorted(r[0] for r in lite.execute("SELECT shop_url FROM shops WHERE mine=1"))
    q = lambda xs: ",".join("?" * len(xs)) or "NULL"
    return f"(buyer IN ({q(names)}) OR shop IN ({q(urls)}))", names + urls


def columns(lite, table):
    return [(r[1], PG_TYPE.get((r[2] or "").upper(), "text")) for r in lite.execute(f"PRAGMA table_info({table})")]


def ensure_schema(pg, lite):
    with pg.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        for name, (table, pk, _, _) in TABLES.items():
            cols = ", ".join(f'"{c}" {t}' + (" PRIMARY KEY" if c == pk else "") for c, t in columns(lite, table))
            cur.execute(f'CREATE TABLE IF NOT EXISTS {SCHEMA}."{name}" ({cols})')
            have = {r[0] for r in cur.execute("SELECT column_name FROM information_schema.columns "
                                              "WHERE table_schema=%s AND table_name=%s", (SCHEMA, name))}
            for c, t in columns(lite, table):              # sqlite migrations add columns: follow them
                if c not in have:
                    cur.execute(f'ALTER TABLE {SCHEMA}."{name}" ADD COLUMN "{c}" {t}')
        for name in JSONL:
            cur.execute(f'CREATE TABLE IF NOT EXISTS {SCHEMA}."{name}" (line bigint PRIMARY KEY, ts double precision,'
                        f' data jsonb)')
        cur.execute(f"CREATE TABLE IF NOT EXISTS {SCHEMA}.board_snapshots (ts double precision PRIMARY KEY, "
                    f"round jsonb, shops jsonb, buyers jsonb, players jsonb)")
        cur.execute(f"""CREATE OR REPLACE VIEW {SCHEMA}.readme AS SELECT * FROM (VALUES
            ('purchases', 'our buyer orders: price, list_price, value, grade, warranty_hours, paid, reason, broken_at'),
            ('negotiations', 'every negotiation: role buyer|shop, first_ask, final_price, offers (json text), outcome'),
            ('our_settles', 'ledger settles where we are buyer or shop; amount = price paid'),
            ('our_breaks', 'goods that broke after the sale, ours as buyer or shop'),
            ('restocks', 'what the shop bought from the market and why'),
            ('stockouts', 'buyer asked for something we did not have'),
            ('notes', 'agent notes (kind, subject, text, importance)'),
            ('our_forum', 'our forum posts with likes/dislikes; role buyer|shop'),
            ('kpi', 'logs/r3/kpi.jsonl: our scores over time (data jsonb)'),
            ('buyer_decisions', 'logs/r3/buyer-decisions.jsonl: each buyer decision with the talk (data jsonb)'),
            ('decisions', 'logs/r3/decisions.jsonl: model calls and answers (data jsonb)'),
            ('forum_log', 'logs/r3/forum.jsonl'),
            ('board_snapshots', 'public /api/board every 10 min, compact')) AS t(table_name, what)""")
        tables = list(TABLES) + list(JSONL) + ["board_snapshots"]
        for name in tables:
            cur.execute(f'ALTER TABLE {SCHEMA}."{name}" ENABLE ROW LEVEL SECURITY')
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
        for (role,) in cur.fetchall():
            cur.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM {role}")
            cur.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {SCHEMA} FROM {role}")
    pg.commit()


def upsert(pg, name, cols, pk, rows):
    if not rows:
        return 0
    names = ", ".join(f'"{c}"' for c in cols)
    sets = ", ".join(f'"{c}"=EXCLUDED."{c}"' for c in cols if c != pk) or f'"{pk}"=EXCLUDED."{pk}"'
    sql = (f'INSERT INTO {SCHEMA}."{name}" ({names}) VALUES ({", ".join(["%s"] * len(cols))}) '
           f'ON CONFLICT ("{pk}") DO UPDATE SET {sets}')
    with pg.cursor() as cur:
        for i in range(0, len(rows), BATCH):
            cur.executemany(sql, rows[i:i + BATCH])
    pg.commit()
    return len(rows)


def sync_tables(pg, lite, st):
    done = {}
    now = time.time()
    for name, (table, pk, mode, extra) in TABLES.items():
        cols = [c for c, _ in columns(lite, table)]
        where, args = [], []
        if extra == "ours":
            w, a = ours_where(lite)
            where.append(w)
            args += a
        elif extra:
            where.append(extra)
        if mode == "id":
            where.append(f'"{pk}" > ?')
            args.append(st.get(name, -1))
        elif st.get(name + "_full"):                       # recent: re-upsert the last 48 h
            where.append("ts > ?")
            args.append(now - REUPSERT_H[name] * 3600)
        q = f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM {table}'
        q += (" WHERE " + " AND ".join(where)) if where else ""
        rows = [tuple(r) for r in lite.execute(q + f' ORDER BY "{pk}"', args)]
        done[name] = upsert(pg, name, cols, pk, rows)
        if mode == "id" and rows:
            st[name] = rows[-1][cols.index(pk)]
        st[name + "_full"] = True
    return done


def sync_jsonl(pg, st):
    done = {}
    for name, fname in JSONL.items():
        path = config.LOG_DIR / fname
        if not path.exists():
            continue
        line_no, off = st.get(name + "_line", 0), st.get(name + "_off", 0)
        if path.stat().st_size < off:                      # rotated or truncated: start over
            line_no, off = 0, 0
        cutoff = time.time() - JSONL_FIRST_H[name] * 3600 if name in JSONL_FIRST_H and not off else None
        rows = []
        with path.open("rb") as f:
            f.seek(off)
            for raw in f:
                if not raw.endswith(b"\n"):                # a line still being written
                    break
                off += len(raw)
                line_no += 1
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                ts = d.get("ts") if isinstance(d, dict) else None
                if cutoff and (not isinstance(ts, (int, float)) or ts < cutoff):
                    continue
                rows.append((line_no, ts if isinstance(ts, (int, float)) else None, Jsonb(d)))
        done[name] = upsert(pg, name, ["line", "ts", "data"], "line", rows)
        st[name + "_line"], st[name + "_off"] = line_no, off
    return done


def sync_board(pg, st):
    if time.time() - st.get("board_ts", 0) < BOARD_EVERY_S:
        return 0
    req = urllib.request.Request(MARKET + "/api/board", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        b = json.loads(r.read())
    keep = lambda rows, ks: [{k: x.get(k) for k in ks} for x in rows or []]
    shop_k = ("name", "house", "alive", "deals", "share", "stars", "stars_rank", "reviews", "revenue", "profit",
              "book", "stock_units", "stock_cost", "broken", "refunds", "all")
    buyer_k = ("name", "house", "alive", "orders", "deals", "paid", "saved", "surplus", "forum", "posts", "all")
    player_k = ("player", "total", "student", "student_total", "student_pct")
    ts = time.time()
    with pg.cursor() as cur:
        cur.execute(f"INSERT INTO {SCHEMA}.board_snapshots VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (ts, Jsonb(b.get("round")), Jsonb(keep(b.get("shops"), shop_k)),
                     Jsonb(keep(b.get("buyers"), buyer_k)), Jsonb(keep(b.get("players"), player_k))))
    pg.commit()
    st["board_ts"] = ts
    return 1


def main():
    t0 = time.time()
    st = state()
    lite = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=30)
    with psycopg.connect(db_url(), connect_timeout=20, autocommit=False,
                         prepare_threshold=None) as pg:  # no server-side prepares: works through the 6543 transaction pooler
        ensure_schema(pg, lite)
        rows = sync_tables(pg, lite, st)
        rows.update(sync_jsonl(pg, st))
        rows["board_snapshots"] = sync_board(pg, st)
    STATE.write_text(json.dumps(st))
    for k, v in rows.items():
        st.setdefault("since_log", {})[k] = st.get("since_log", {}).get(k, 0) + v
    if time.time() - st.get("last_log", 0) >= LOG_EVERY_S:        # one line per 30 min, not per run
        log("synced", seconds=round(time.time() - t0, 1), rows_since_last_log={k: v for k, v in st["since_log"].items() if v})
        st["last_log"], st["since_log"] = time.time(), {}
    STATE.write_text(json.dumps(st))


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    try:
        main()
    except psycopg.Error as e:                              # never print the URL; the message has no password
        log("error", why=f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
        sys.exit(1)
