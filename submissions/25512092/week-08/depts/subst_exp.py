"""Substitute offers, option A as a 6 h experiment (person's decision 11/08 ~13:30), switched by subst_exp.json
(read per negotiation; seller_r3.py).

Before: a buyer naming a SKU I do not hold got up to 3 same-category alternatives and had to name one; in 7,031
offers none was named (62% of buyers left at once, 38% answered with a number only).
  mode "A_exp"  between since and until, SHARE of the offers are arm A, the rest arm "control" (as before):
                  A  candidates: same category and list price within BAND (+-30%) of the requested SKU (tiers 1-2)
                     a reply with a number and no offered SKU = an offer for the first candidate (its negotiation
                     starts at the quoted price, as when the buyer names it)
                     the receipt's item = the order's category name (the market accepts the order's item for any SKU
                     shipped; a named SKU could fail the buyer's acceptable list)
  mode "B"      no alternatives: "not in stock" at once (the stop rule of tools/subst_watch.py switches to it)
  mode "off"    as before (control for every offer)
Every offer and choice: one line in logs/subst_exp.jsonl (task, arm, event).
"""
import json
import os
import random
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "subst_exp.json")
LOG = os.path.join(HERE, "logs", "subst_exp.jsonl")
SHARE = 0.5
BAND = 0.30


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def arm(now=None):
    """'A', 'control' or 'B' for a new substitute offer; None when the experiment is not on (as before)."""
    r, now = rules(), now or time.time()
    if r.get("mode") == "B":
        return "B"
    if r.get("mode") == "A_exp" and (r.get("since") or 0) <= now < (r.get("until") or 0):
        return "A" if random.random() < float(r.get("share", SHARE)) else "control"
    return None


def near(cands):
    """Arm A candidates: tiers 1-2 of substitute.candidates (same category, list price within +-30%)."""
    return [(t, u) for t, u in cands if t in (1, 2)]


def number_offer(text):
    """The buyer's offer in a reply that names no SKU, or None (depts/receipt_check.buyer_offer: no quoted prices,
    no units such as hours)."""
    from depts import receipt_check, substitute
    if substitute.named_sku(text):
        return None
    return receipt_check.buyer_offer(substitute.strip_skus(text))


def category_name(unit):
    return (unit.get("category") or "").split(">")[-1] or None


def log(task, arm_, event, **kw):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(ts=round(time.time(), 1), task=task, arm=arm_, event=event, **kw),
                               ensure_ascii=False) + "\n")
    except OSError:
        pass
