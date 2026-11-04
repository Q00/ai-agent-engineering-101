"""Reflex facts: what the inner runtime must know at once, without waiting for the outer one (HARNESS.md 2.2).

Two things, both from our own buyer's experience:

- a shop that cheated us (a receipt above the agreed price, a wrong item, above the budget) is blocked
  at once; un-blocking is the user's call (D16), so nothing here ever un-blocks;
- the kit's payment results (.market/payments.jsonl): which shops we could not pay, and why, so the
  next order does not repeat the mistake.

memory/facts/reflex.json is owned by the inner runtime. The kit runs market_buyer one order at a time,
so there is one writer at a time; writes are atomic.
"""
import json
import time

from core import atomic, paths

LAST_N = 5          # recent outcomes kept per shop


def path():
    return paths.memory() / "facts" / "reflex.json"


def load():
    try:
        r = atomic.read_json(path(), default=None)
    except ValueError:
        r = None
    return r or {"shops": {}, "url_names": {}, "payments_offset": 0, "updated": None}


def _shop(r, name):
    return r["shops"].setdefault(name, {"deals": 0, "fair": 0, "cheated": 0, "no_deal": 0, "blocked": None,
                                        "unpaid": 0, "paid": 0, "last": [], "last_refusal": None})


def record_outcome(shop_name, shop_url, verdict, receipt_price=None, agreed=None, order=None, now=None):
    """After every order of our buyer. verdict starts with 'cheated', 'fair' or 'no deal'."""
    r = load()
    if shop_url:
        r["url_names"][shop_url] = shop_name
    s = _shop(r, shop_name)
    now = now or time.time()
    if verdict.startswith("cheated"):
        s["cheated"] += 1
        s["deals"] += 1
        s["blocked"] = s["blocked"] or {"since": now, "why": verdict, "order": order}
    elif verdict.startswith("fair"):
        s["fair"] += 1
        s["deals"] += 1
    else:
        s["no_deal"] += 1
    s["last"] = ([{"ts": now, "verdict": verdict, "receipt": receipt_price, "agreed": agreed, "order": order}]
                 + s["last"])[:LAST_N]
    r["updated"] = now
    atomic.write_json(path(), r)
    return s


def read_payments(market_dir, now=None):
    """Fold new lines of .market/payments.jsonl into the shops (paid / unpaid and the last refusal)."""
    r = load()
    f = market_dir / "payments.jsonl"
    if not f.exists():
        return r
    with open(f, "rb") as fh:
        fh.seek(r.get("payments_offset", 0))
        chunk = fh.read()
    done = chunk.rfind(b"\n") + 1                 # whole lines only
    for raw in chunk[:done].splitlines():
        try:
            p = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        name = r["url_names"].get(p.get("shop"), p.get("shop"))
        s = _shop(r, name)
        if p.get("paid"):
            s["paid"] += 1
        else:
            s["unpaid"] += 1
            s["last_refusal"] = {"ts": now or time.time(), "order": p.get("order"), "reason": p.get("reason")}
    r["payments_offset"] = r.get("payments_offset", 0) + done
    r["updated"] = now or time.time()
    atomic.write_json(path(), r)
    return r


def blocked():
    """{shop name: why} for every blocked shop."""
    return {n: s["blocked"] for n, s in load()["shops"].items() if s.get("blocked")}


UNSIGNED_RETRY = 3600     # a skipped shop is tried again after this long (it may have fixed its signing)


def unsigned(now=None):
    """Shops to skip for now (11/5 01:4x): the last 3 receipts had no sig_seller and none was ever fair - the kit
    cannot pay such a receipt (willow-goods: 44 in an hour, 461 no deals, 0 fair). Not a block (D16: blocks are
    for cheating); the shop is tried again UNSIGNED_RETRY after its last try."""
    now = now or time.time()
    out = set()
    for n, s in load()["shops"].items():
        last = s.get("last") or []
        if (s.get("fair", 0) == 0 and len(last) >= 3 and all("sig_seller" in x.get("verdict", "") for x in last[:3])
                and now - (last[0].get("ts") or 0) < UNSIGNED_RETRY):
            out.add(n)
    return out
