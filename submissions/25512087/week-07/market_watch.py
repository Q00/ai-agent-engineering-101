"""Snapshot the public market dashboard for this shop and buyer (challenge 1 and REPORT.md evidence).

    python market_watch.py --every 600        # loop: one snapshot every 10 minutes
    python market_watch.py --once             # one snapshot
    python market_watch.py --summary          # table of the snapshots taken so far

Reads only the public endpoints the dashboard itself uses (/api/board, /api/stats, /api/trades).
Writes logs/challenge-1/dashboard.jsonl (one line per snapshot) and .market/own-reputation.json.
Each poll also runs the market price scan (market_prices.py): every --scan-hours, and at once when a
competitor's share of recent counted deals surges.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

from market_prices import step as price_step

MARKET = "https://market.ourolabs.ai"
SHOP = "https://ggari0703-shop.ggari0703.workers.dev"        # the Worker shop (customer watch lives there)
ALERTS = Path("logs/challenge-1/customer-alerts.jsonl")
SEEN = Path(".market/seen-alerts.json")
NAME = "ggari0703"
OUT = Path("logs/challenge-1/dashboard.jsonl")
REPUTATION = Path(".market/own-reputation.json")


def snapshot(market: str = MARKET, name: str = NAME) -> dict:
    with httpx.Client(timeout=20) as c:
        board = c.get(f"{market}/api/board").json()
        stats = c.get(f"{market}/api/stats").json()
    rnd = str(board.get("round"))
    shop = next((s for s in board.get("shops", []) if s.get("name") == name), {})
    buyer = next((b for b in board.get("buyers", []) if b.get("name") == name), {})
    rstats = (stats.get("rounds") or {}).get(rnd, {})
    row = next((s for s in rstats.get("shops", []) if s.get("shop") == name), {})
    return {
        "ts": round(time.time()), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "round": board.get("round"),
        "shop": {"alive": shop.get("alive"), "owner": (shop.get("owner") or {}).get("login"),
                 "deals": row.get("deals", shop.get("deals")), "share": row.get("share", shop.get("share")),
                 "visits": row.get("visits", shop.get("visits")), "stars": shop.get("stars"),
                 "reviews": shop.get("reviews"), "revenue": shop.get("revenue")},
        "buyer": {"alive": buyer.get("alive"), "orders": buyer.get("orders"), "deals": buyer.get("deals"),
                  "paid": buyer.get("paid"), "held": buyer.get("held")},
        "market_items": rstats.get("items"),
        "market_deals": rstats.get("deals"),
    }


def record(snap: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    REPUTATION.parent.mkdir(parents=True, exist_ok=True)
    REPUTATION.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    s = snap["shop"]
    print(f"{snap['time']} round {snap['round']}: shop deals {s['deals']} share {s['share']} "
          f"stars {s['stars']} ({s['reviews']} reviews), alive {s['alive']}; "
          f"buyer deals {snap['buyer']['deals']} paid {snap['buyer']['paid']}", flush=True)


def customer_alerts() -> None:
    """Print each new customer alert from the Worker once (churn / back / new) and keep it in the logs."""
    token_file = Path(".market/admin-token")              # same value as the Worker's ADMIN_TOKEN secret
    if not token_file.exists():
        return
    token = token_file.read_text(encoding="utf-8").strip()
    with httpx.Client(timeout=20) as c:
        r = c.get(f"{SHOP}/customers.json", headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            print(f"{time.strftime('%H:%M:%S')} customer watch: HTTP {r.status_code} (check .market/admin-token)", flush=True)
            return
        data = r.json()
    try:
        seen = set(json.loads(SEEN.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    for a in data.get("alerts", []):
        key = f"{a.get('kind')}|{a.get('buyer')}|{a.get('at')}|{a.get('recovered', '')}"
        if key in seen:
            continue
        seen.add(key)
        ALERTS.parent.mkdir(parents=True, exist_ok=True)
        with ALERTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
        if a.get("kind") == "churn" and not a.get("recovered"):
            last = a.get("last") or {}
            print(f"!! customer lost: {a['buyer']} ({a.get('visits_before')} visits in the 3 h before, "
                  f"last {last.get('stars')} stars: {str(last.get('review', ''))[:120]})", flush=True)
        elif a.get("recovered"):
            print(f"   customer back: {a['buyer']} at {a['recovered']}", flush=True)
        elif a.get("kind") == "new":
            print(f"   new customer: {a['buyer']} ({a.get('visits_last_hours')} visits)", flush=True)
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(seen)[-500:]), encoding="utf-8")


def summary() -> None:
    if not OUT.exists():
        print("no snapshots yet")
        return
    print("time                 round  deals  share  stars  reviews  buyer_paid")
    for line in OUT.read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        print(f"{s['time']}  {s['round']!s:>5}  {s['shop']['deals']!s:>5}  {s['shop']['share']!s:>5}  "
              f"{s['shop']['stars']!s:>5}  {s['shop']['reviews']!s:>7}  {s['buyer']['paid']!s:>10}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=600)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--scan-hours", type=float, default=1.0, help="scheduled market price scan interval")
    a = ap.parse_args()
    if a.summary:
        return summary()
    while True:
        try:
            record(snapshot())
        except Exception as error:      # noqa: BLE001 - keep watching through network errors
            print(f"{time.strftime('%H:%M:%S')} snapshot failed: {type(error).__name__}: {error}", flush=True)
        try:
            with httpx.Client(timeout=20) as c:
                trades = c.get(f"{MARKET}/api/trades").json()
            scan = price_step(trades, NAME, a.scan_hours)
            if scan:
                items = ", ".join(f"{k} min {v['min']:g} med {v['median']:g}" for k, v in scan["items"].items())
                print(f"{scan['time']} market scan ({scan['reason']}): {items}", flush=True)
        except Exception as error:      # noqa: BLE001
            print(f"{time.strftime('%H:%M:%S')} market scan failed: {type(error).__name__}: {error}", flush=True)
        try:
            customer_alerts()
        except Exception as error:      # noqa: BLE001
            print(f"{time.strftime('%H:%M:%S')} customer watch failed: {type(error).__name__}: {error}", flush=True)
        if a.once:
            return
        time.sleep(a.every)


if __name__ == "__main__":
    main()
