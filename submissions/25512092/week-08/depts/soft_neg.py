"""Buyer negotiation experiment "soft_neg", 24 h (person's decision 11/09 ~14:40), switched by soft_neg.json (read per
order: market_buyer.py runs once per order, no restart).

Half the orders (md5 of the order id, case normal, the SKU has a market average) negotiate the soft way, decided by
code with no LLM call:
  - the shop's ask <= the market average (and >= 50% of it, within the cap) -> accept at once
  - the opening offer is FIRST (75%) of the market average (within the cap, below the ask)
  - at most HAGGLES (2) offers; the second is halfway between my last offer and the ask (<= the average, the cap);
    after that, an ask still above the average -> walk (the accept rule then sees the same bound)
The other half keeps the current way. The grade rules, the precheck, the receipt checks, the injection handling (the
code finishes those the old way) and the curtain / break rule (it lowers the cap before this runs) do not change.
Soft orders are recorded in buyer_deals with buyer_case 'soft_neg' (out of the variant / tactic experiments), and
both arms one line per order in logs/soft_neg.jsonl (tools/soft_neg_watch.py reads it).
  11/09 replay of the last 24 h (2,284 negotiations): close 54.1% vs 48.2%, 11.5 vs 28.0 s per negotiation, saving
  per deal 0.414 vs 0.466; on half the orders ~+67 deals a day, saved ~0.426 vs ~0.435 after a day
"""
import hashlib
import json
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "soft_neg.json")
LOG = os.path.join(HERE, "logs", "soft_neg.jsonl")
TAG = "soft_neg"
FIRST, HAGGLES, ACCEPT, LOW, SHARE = 0.75, 2, 1.00, 0.50, 0.5


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def on(now=None, r=None):
    r, now = r if r is not None else rules(), now or time.time()
    return bool(r.get("enabled")) and (r.get("since") or 0) <= now < (r.get("until") or 0)


def arm(order_id, case="normal", r=None, now=None):
    """'soft' / 'control' while the experiment is on (case normal only), else None."""
    r = r if r is not None else rules()
    if case != "normal" or not order_id or not on(now, r):
        return None
    x = int(hashlib.md5(str(order_id).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "soft" if x < r.get("share", SHARE) else "control"


def move(ask, last, n_offers, budget, market_avg, r=None):
    """The soft move: {"action": "accept"} / {"action": "offer", "price": p} / {"action": "walk"}, or None (no usable
    ask or market average, or an ask under 50% of the average: the current way decides that turn)."""
    r = r if r is not None else rules()
    if ask is None or not market_avg:
        return None
    if ask < r.get("low", LOW) * market_avg:
        return None
    lim = min(budget, r.get("accept", ACCEPT) * market_avg)
    if ask <= lim:
        return {"action": "accept", "why": f"ask <= {r.get('accept', ACCEPT)} x market avg"}
    if n_offers == 0:
        p = min(int(r.get("first", FIRST) * market_avg), int(budget), ask - 1)
        return {"action": "offer", "price": max(1, p), "why": "first"}
    if n_offers < r.get("haggles", HAGGLES):
        p = min(int(lim), (int(last or 0) + ask) // 2, ask - 1)
        return {"action": "offer", "price": max(int(last or 0) + 1, p), "why": "haggle"}
    return {"action": "walk", "why": f"{n_offers} offers, ask {ask} > {int(lim)}"}


def log(**kw):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(ts=round(time.time(), 1), tag=TAG, **kw), ensure_ascii=False) + "\n")
    except OSError:
        pass
