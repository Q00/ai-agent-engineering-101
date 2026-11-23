"""PUSH 11-16 S1: refuse sales to buyers in <md>/blocked_buyers.json ({"buyers": [...]}).
python tools/block_poller.py --md .market [--market URL] [--every 2]
Follows the market's public ledger and keeps <md>/blocked_orders.json
{"t": time of the last complete pass, "orders": {order: {"buyer", "expires"}}} for the blocked buyers' open orders.
The kit's ACP checkout (market_kit.py blocked_order) refuses those orders; it ignores the file once "t" is over 60 s
old, so a stopped or failing poller makes the shop sell to everyone again. Read-only toward the market."""
import argparse, json, os, sys, time, urllib.request

BACKFILL = 4000          # ledger entries read at start (~13 min at ~5/s; orders expire 10 min after issue)


def get(base, after):
    with urllib.request.urlopen(f"{base}/api/ledger?after={after}&limit=500", timeout=15) as r:
        return json.load(r)


def blocked(md):
    try:
        with open(os.path.join(md, "blocked_buyers.json"), encoding="utf-8") as f:
            b = json.load(f).get("buyers")
        return {x for x in b if isinstance(x, str) and x} if isinstance(b, list) else set()
    except Exception:  # noqa: BLE001
        return set()


def write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def step(base, cur, seen, now):
    """Read every ledger entry after `cur`; returns the new cursor. `seen` collects every order row
    {order: (buyer, expires)} still open."""
    while True:
        rows = get(base, cur)
        for r in rows:
            d = r.get("data") or {}
            if r.get("kind") == "order" and isinstance(d.get("order"), str):
                seen[d["order"]] = (d.get("buyer"), float(d.get("expires") or r.get("ts") or now) )
        if rows:
            cur = rows[-1]["seq"]
        if len(rows) < 500:
            return cur


def snapshot(seen, blk, now):
    for o in [o for o, (_, exp) in seen.items() if exp + 120 < now]:
        del seen[o]
    return {"t": now, "orders": {o: {"buyer": b, "expires": exp} for o, (b, exp) in seen.items() if b in blk}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=".market")
    ap.add_argument("--market", default="https://market.ourolabs.ai")
    ap.add_argument("--every", type=float, default=2.0)
    a = ap.parse_args()
    base, out = a.market.rstrip("/"), os.path.join(a.md, "blocked_orders.json")
    seen, cur = {}, None
    while True:
        try:
            if cur is None:
                rows = get(base, -BACKFILL)
                cur = rows[0]["seq"] - 1 if rows else 0
                print(time.strftime("%H:%M:%S"), "[block] start after seq", cur, flush=True)
            cur = step(base, cur, seen, time.time())
            blk = blocked(a.md)
            snap = snapshot(seen, blk, time.time())
            write(out, snap)                         # "t" moves only after a complete pass
        except Exception as e:  # noqa: BLE001 - keep polling; the kit ignores a stale file
            print(time.strftime("%H:%M:%S"), "[block] error", type(e).__name__, str(e)[:120], flush=True)
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
