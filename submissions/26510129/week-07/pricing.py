"""Challenge 1: the shop's own trades and reviews decide its opening asks. The reserves never move.

    suggest(item) -> (ask or None, why)   None when there is too little history for that item; the harness opens as before

Trades come from TRADES, which seller.py appends to (open, deal, reject, cancel). Reviews of this shop come from the
market's public shop list, found by the public key of shop_key.pem, refreshed at most every REVIEW_TTL seconds and
skipped silently when the market is unreachable (the market test runs the shop offline).

The rule, in code so that it can be read back from the logs:
    base      = median of the last RECENT deal prices for the item
    ask       = base * ANCHOR                 buyers come up from their first offer, so open a little above what they paid
    * 0.95    when fewer than CLOSE_FLOOR of the last WINDOW opens for the item ended in a deal (buyers walk: too high)
    * 0.95    when my reviews that name the item average under 3.5 stars (the buyers who did buy felt squeezed)
    clamped to [reserve * 1.05, reserve * 1.5], whole number

Day 2 change (ANCHOR 1.12 -> 1.06, the close rate over a window with a 70% floor instead of all-time with 50%):
after a day with ANCHOR 1.12 the shop had 1173 opens and closed 64% of them, against 77-79% for the shops at the top
of the board; 143 buyers left at the opening ask without an offer, and the asks ratcheted (lamp 112 -> 136) because
the buyers who pay the ask without bargaining pushed the median of recent deals up, which raised the next ask. Reviews
said "Lamp at 110, above the 105 I usually pay". The old close-rate rule never fired at 64%. The board's share counts
deals, not revenue, so the ask opens closer to what buyers actually pay.
"""
import base64
import json
import os
import re
import statistics
import time
from pathlib import Path

import httpx

TRADES = Path(".market") / "shop_trades.json"
MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai").rstrip("/")
SHOP_KEY = "shop_key.pem"
RECENT = 20          # deal prices per item that count; 10 swung with one busy hour (lamp median 118 over 10, 110 over 20)
MIN_DEALS = 3        # below this the harness opens as before
ANCHOR = 1.06        # day 1 ran at 1.12; see the docstring
WINDOW = 40          # the close rate is measured over the last WINDOW opens of the item, so it reacts within hours
CLOSE_FLOOR = 0.70   # below this the ask drops 5%; the shops at the top of the board close 77-79% of their visits
REVIEW_TTL = 600

_reviews = {"at": 0.0, "list": []}


def load():
    try:
        return json.loads(TRADES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def record(**event):
    """Append one event: kind=open|deal|reject|cancel, item, and ask or price when there is one."""
    rows = load()
    rows.append(dict(event, at=time.strftime("%Y-%m-%d %H:%M:%S")))
    TRADES.parent.mkdir(exist_ok=True)
    TRADES.write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")


def my_pubkey():
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(open(SHOP_KEY, "rb").read(), password=None)
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def reviews():
    """This shop's reviews from the market, by public key. Cached; empty when offline."""
    if time.time() - _reviews["at"] < REVIEW_TTL:
        return _reviews["list"]
    _reviews["at"] = time.time()
    try:
        pub = my_pubkey()
        shops = httpx.get(f"{MARKET}/api/shops", timeout=10).json()["shops"]
        mine = next((s for s in shops if s.get("pubkey") == pub), None)
        _reviews["list"] = (mine or {}).get("reviews") or []
        print(f"[pricing] {len(_reviews['list'])} reviews of this shop from the market" + (f" (as {mine['name']})" if mine else " (not registered)"), flush=True)
    except Exception as e:  # no network, no key, market down: price from trades alone
        print(f"[pricing] reviews unavailable: {type(e).__name__}", flush=True)
    return _reviews["list"]


def suggest(item):
    """Opening ask for the item from history, or None. Returns (ask, why)."""
    iid, reserve = item["id"], item["reserve"]
    rows = [r for r in load() if r.get("item") == iid]
    deals = [r["price"] for r in rows if r.get("kind") == "deal" and isinstance(r.get("price"), (int, float))][-RECENT:]
    opens = [r for r in rows if r.get("kind") == "open"][-WINDOW:]
    if len(deals) < MIN_DEALS:
        return None, f"{iid}: {len(deals)} deal(s) on record, need {MIN_DEALS}; the harness opens"
    base = statistics.median(deals)
    ask = base * ANCHOR
    why = [f"median of last {len(deals)} deals {base:.0f} x {ANCHOR}"]
    tasks = {r.get("task") for r in opens}
    closed = sum(1 for r in rows if r.get("kind") == "deal" and r.get("task") in tasks)
    close_rate = closed / len(opens) if opens else 1.0
    why.append(f"close rate {close_rate:.0%} of the last {len(opens)} opens")
    if close_rate < CLOSE_FLOOR:
        ask *= 0.95
        why[-1] += " -> -5%"
    stars = [r["stars"] for r in reviews() if isinstance(r.get("stars"), int) and re.search(rf"\b{re.escape(item['name'])}\b", str(r.get("review", "")), re.I)]
    if stars and sum(stars) / len(stars) < 3.5:
        ask *= 0.95
        why.append(f"{len(stars)} review(s) name it, avg {sum(stars) / len(stars):.1f} stars -> -5%")
    lo, hi = reserve * 1.05, reserve * 1.5
    ask = int(round(min(max(ask, lo), hi)))
    why.append(f"clamped to [{lo:.0f}, {hi:.0f}]")
    return ask, f"{iid}: " + ", ".join(why) + f" -> opening ask {ask}"


def history_from_log(path):
    """Backfill TRADES from a kit log: the shop's own OPEN/DEAL/REJECT lines before this module existed."""
    rows, n = load(), 0
    seen = {(r.get("kind"), r.get("task")) for r in rows}
    for line in open(path, encoding="utf-8", errors="ignore"):
        m = re.search(r"\[seller\] (OPEN|DEAL|REJECT) task=(\w+) item=(\S+) reserve=\d+ (?:ask|price)=(\d+)", line)
        if not m:
            continue
        kind, task, iid, num = m.group(1).lower(), m.group(2), m.group(3), int(m.group(4))
        if (kind, task) in seen:
            continue
        rows.append({"kind": kind, "task": task, "item": iid, ("ask" if kind == "open" else "price"): num, "at": line[:8], "from": "log"})
        seen.add((kind, task)); n += 1
    TRADES.parent.mkdir(exist_ok=True)
    TRADES.write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
    return n


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(f"backfilled {history_from_log(sys.argv[1])} event(s) into {TRADES}")
    for it in json.loads(open("catalog/basic.json", encoding="utf-8").read()):
        print(suggest(it)[1])
