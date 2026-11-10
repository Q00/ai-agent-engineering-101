"""Observation: copy what the market makes public into the shared DB.

    python collector.py            # one pass (the restock hook calls this every 60 s)
    python collector.py --loop 30  # keep going, one pass every 30 s

Settles say what sold, to whom, at what price and grade; broken entries, joined to settles by unit, give the
break rate per grade that the market does not publish. Category and kind come from the kit's catalog.json.
"""
import argparse
import json
import time
from pathlib import Path

import httpx

from db import DB

MARKET = "https://market.ourolabs.ai"
HERE = Path(__file__).resolve().parent
FIRST_BACKFILL = 20000      # on the very first pass, start this many ledger entries before the newest
MAX_PAGES = 40              # 500 entries a page; the rest waits for the next pass

SETTLE_SQL = ("INSERT INTO settles VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) "
              "ON CONFLICT DO NOTHING")
BROKEN_SQL = "INSERT INTO broken VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING"


def load_catalog() -> dict:
    p = HERE / ".market" / "catalog.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def sync_shops(c, d) -> int:
    r = c.get(f"{MARKET}/api/shops")
    r.raise_for_status()
    now = time.time()
    shops = r.json().get("shops", [])
    d.batch(("INSERT INTO shops (url, name, seen) VALUES ($1, $2, $3) ON CONFLICT (url) "
             "DO UPDATE SET name = EXCLUDED.name, seen = EXCLUDED.seen", (s["url"], s["name"], now))
            for s in shops)
    return len(shops)


def sync_ledger(c, d, catalog) -> tuple[int, int]:
    after = int(d.get_state("ledger_seq", -FIRST_BACKFILL))
    settles = broken = 0
    for _ in range(MAX_PAGES):
        r = c.get(f"{MARKET}/api/ledger", params={"after": after})
        r.raise_for_status()
        page = r.json()
        if not page:
            break
        rows = []
        for e in page:
            x = e.get("data") or {}
            if e.get("kind") == "settle":
                cat = catalog.get(x.get("sku"), {})
                rows.append((SETTLE_SQL, (e["seq"], e["ts"], x.get("shop"), x.get("buyer"), x.get("sku"),
                                          x.get("name"), cat.get("category"), cat.get("kind"),
                                          cat.get("list_price"), x.get("amount"), x.get("grade"), x.get("claim"),
                                          x.get("claim_honest"), x.get("warranty_hours"), x.get("unit"))))
                settles += 1
            elif e.get("kind") == "broken":
                rows.append((BROKEN_SQL, (e["seq"], e["ts"], x.get("shop"), x.get("buyer"), x.get("item"),
                                          x.get("unit"), x.get("hours_after_sale"), x.get("warranty_hours"),
                                          x.get("refunded"))))
                broken += 1
        after = page[-1]["seq"]
        rows.append(("INSERT INTO state (key, value) VALUES ('ledger_seq', $1) "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (str(after),)))
        d.batch(rows)                      # the rows and the new position land together or not at all
        if len(page) < 500:
            break
    return settles, broken


def sync_forum(c, d) -> int:
    r = c.get(f"{MARKET}/api/forum", params={"limit": 100})
    r.raise_for_status()
    posts = r.json()
    d.batch(("INSERT INTO forum VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT (id) DO UPDATE SET "
             "likes = EXCLUDED.likes, dislikes = EXCLUDED.dislikes, score = EXCLUDED.score",
             (p["id"], p["ts"], p.get("author"), p.get("role"), p.get("thread"), p.get("text"),
              p.get("likes"), p.get("dislikes"), p.get("score"))) for p in posts)
    return len(posts)


def run_once() -> dict:
    with DB() as d, httpx.Client(timeout=20) as c:
        d.init()
        shops = sync_shops(c, d)
        settles, broken = sync_ledger(c, d, load_catalog())
        forum = sync_forum(c, d)
    return {"shops": shops, "settles": settles, "broken": broken, "forum": forum}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="seconds between passes; 0 = one pass")
    every = ap.parse_args().loop
    while True:
        try:
            print(f"[collector] {json.dumps(run_once())}", flush=True)
        except (httpx.HTTPError, OSError, RuntimeError) as e:
            print(f"[collector] {type(e).__name__}: {e}", flush=True)
        if not every:
            break
        time.sleep(every)
