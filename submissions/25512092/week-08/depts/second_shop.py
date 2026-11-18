"""second_shop, plan D (person's decision 11/09 ~17:40; on at the 20:27 soft_neg check only if soft_neg went to 100%):
a soft_neg order that ended at its first shop on price (walk / turn limit), no stock or the shop ending it, with at
least 2 shops in stock, tries the next candidate shop once, the soft_neg way again (first offer 75% of the market
average, accept an ask <= the average, at most 2 offers, no LLM) - only if the whole order stays within LIMIT_S (30 s)
from the order's start: elapsed in the buyer + TRY_S + PRE_S (kit before the buyer) + POST_S (result and ledger) <=
LIMIT_S, i.e. the first shop ended within ~17 s (11/09 ~18:00 person; was: under the ~20 s order spacing, ~7 s).
Switched by second_shop.json (read per order); tools/saved_guard_watch.py switches it off when my saved < the 2nd
best saved + 0.015 (depts/saved_guard.py). Tag second_shop (buyer_deals case, soft_neg.jsonl field).
  11/09 analysis (tools/order_speed2.py): one soft try 8.9 s; 57% of the soft orders qualify, ~75% of those fit
"""
import json
import re
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "second_shop.json")
TAG = "second_shop"
LIMIT_S, TRY_S, PRE_S, POST_S = 30.0, 9.0, 1.5, 2.5
ENDS = {"walk", "turn_limit", "seller_ended", "time_limit"}   # price, the shop ended it (no stock is a shop-ended
# reply), and from 11/09 ~20:00 a timeout (75 s: never within 30 s in practice). A first shop that did not answer at
# all (card / first reply, an exception in the buyer) is handled in buyer_biz.main as "no_reply".
STOCK = re.compile(r"not in stock|none of that|out of stock|sold out|no stock|품절|재고", re.I)


def reason(res):
    """The first shop's end for the 6 h report: 가격 / 재고 / 끊김 / 무응답."""
    e = res.get("ended")
    if e in ("time_limit", "no_reply") or res.get("error"):
        return "무응답"
    if e == "seller_ended":
        return "재고" if STOCK.search(res.get("end_text") or "") else "끊김"
    return "가격"


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def on(r=None, now=None):
    r = rules() if r is None else r
    return bool(r.get("enabled")) and (r.get("since") or 0) <= (now or time.time())


def qualifies(res):
    """The first shop's result is one D retries: no deal, ended on price / no stock / by the shop."""
    return (not res.get("deal") and not res.get("error") and not res.get("skip") and not res.get("injection")
            and res.get("ended") in ENDS)


def fits(elapsed, r=None):
    r = rules() if r is None else r
    return elapsed + r.get("try_s", TRY_S) + r.get("pre_s", PRE_S) + r.get("post_s", POST_S) <= r.get("limit_s", LIMIT_S)
