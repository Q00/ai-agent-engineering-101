"""The deployed shop's and buyer's records, as log files in logs/.

    python export_logs.py --since <epoch>      # e.g. when Round 3 opened

The assignment asks for the deploying computer's logs/market-kit.log with its
settlement lines. This shop and buyer run on Cloudflare without the kit, so
there is no such file: the Worker keeps each order's log lines (the buyer's
half, GET /buyer/log) and each settlement (the shop's half, GET /shop/log).
This script pages through both and writes them in the kit's line format:

    logs/cloudflare-buyer.log   every order from --since: the trade, [sign], [acp] paid, [market] ledger
    logs/cloudflare-shop.log    every settlement or refusal at this shop's checkout: [acp] settled / refused

Nothing is edited after it is written; a later export is a new pair of files
when --suffix is given.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

WORKER = "https://kiro-shop.kiro729.workers.dev"
LOGS = Path(__file__).resolve().parent / "logs"


def stamp(t: float) -> str:
    return time.strftime("%m-%d %H:%M:%S", time.localtime(t))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", type=float, required=True)
    ap.add_argument("--suffix", default="")
    a = ap.parse_args()
    c = httpx.Client(timeout=60)

    orders, after = [], a.since
    while True:
        page = c.get(f"{WORKER}/buyer/log", params={"since": after, "n": 200}).json()
        if not page:
            break
        orders += page
        after = page[-1]["t"]
    buyer = [f"# Cloudflare buyer Kiro729, exported {stamp(time.time())}: {len(orders)} order(s) from {stamp(a.since)}"]
    for o in orders:
        buyer.append(f"{stamp(o['t'])} " + f"\n{stamp(o['t'])} ".join(o.get("log") or [f"[order {o['order']}] (no log)"]))
    settled = sum(1 for o in orders if o.get("settled"))
    buyer.append(f"# {settled} settled of {sum(1 for o in orders if (o.get('round') or 0) >= 3)} Round 3 order(s), "
                 f"{sum(1 for o in orders if o['deal'] != 'no deal')} deal(s)")

    shop_log = c.get(f"{WORKER}/shop/log", params={"since": a.since}).json()   # oldest first, all-time totals
    rows = shop_log["recent"]
    shop = [f"# Cloudflare shop Kiro729, exported {stamp(time.time())}: {len(rows)} checkout completion(s) "
            f"from {stamp(a.since)}"]
    shop += [f"{stamp(s['t'])} {s['line']}  order {s.get('order')}" for s in rows]
    shop.append(f"# all time: {shop_log['settled']} settled, {shop_log['refused']} refused "
                f"{shop_log['refused_checks']}, {shop_log['credits']} credits")

    LOGS.mkdir(exist_ok=True)
    for name, body in (("cloudflare-buyer", buyer), ("cloudflare-shop", shop)):
        path = LOGS / f"{name}{a.suffix}.log"
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        print(f"{path.name}: {len(body) - 2} line(s); last: {body[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
