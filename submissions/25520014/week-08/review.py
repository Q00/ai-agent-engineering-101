"""Week 08 review hook: stars for a paid deal, from the market's receipt and the market's own prices only.

    python review.py --deal .market/deal.json

The kit runs it after each settled deal and its stars replace market_buyer's. The stars come from facts the market
signed (deal.json `settled`) and the market's break record, never from anything the shop wrote. One rubric for every
shop, the buyer's own score (owner, 11-06 16:44; RUBRIC = "saved"): the deal's expected saved rate
    s = 1 - price / list, less, for goods sold without a warranty, the grade's break rate x price / list
        (a break without a warranty loses the price; rates from model.Estimates, about A 0.03, B 0.12, C 0.34)
against the buyer's target T = buyer_policy min_saved, read on every deal, so the stars follow the manager's target:
    5  s >= T + STEP          4  s >= T          3  s >= T - STEP          2  s >= 0
    1  s < 0, or the shop claimed a grade the unit did not have (claim_honest false), or the unit is worth nothing to
       the order (value 0: food that will not last; our accept asked for enough hours)
Replay on our 2,017 purchases of 11-06 (24 h, T 0.25): mean 4.11 -> 3.70, 5/4/3/2 = 249/1020/646/102; yms979 4.28 ->
3.53, Kiro729 4.23 -> 3.88, moss-and-oak 3.91 -> 3.42, olstjr 4.78 -> 4.56. RUBRIC = "ratio" is the old rubric:
price / list against the market's mean ratio, 5/4/3.
The review states the same facts in one line. The purchase is added to data/purchases.jsonl, where a later break
(.market/broken.jsonl names only the item and the time) is matched to the shop and the warranty.
"""
import argparse
import json
import os
import time
from pathlib import Path

import model
import policy

PURCHASES = policy.DATA / "purchases.jsonl"
BAND = 0.05                     # "ratio" rubric: within 5 points of the market's mean ratio is an ordinary price
RUBRIC = "saved"                # "saved": expected saved rate against the buyer's target (11-06 16:44); "ratio": old
STEP = 0.10                     # a star per 10 points of saved rate around the target
T_DEFAULT = 0.25
# Goods sold without a warranty: the stars above, NO_WARRANTY_DROP lower, never under 2 (1 stays for a false grade,
# a useless unit or a loss), and the review says why (owner, 11-06 19 h). Since patch 3.4 a break without a refund
# counts -1 in Saved, the buyer's main score, so a warranty is worth more to this buyer than its break rate alone.
# Known and accepted: yms979 sells every goods unit without one (179 of our 188 deals there in 24 h); replay on 24 h
# to 11-17: its stars_rank -0.028 (4.37 -> about 4.34), Lee-sungsoo -0.019, Choo-chu -0.022, 11im -0.010, shops
# that give warranties about 0. 69% of our reviews at yms979 become 2 stars.
NO_WARRANTY_DROP = 2
BREAK_PATH = policy.DATA / "review_breaks.json"
BREAK_TTL = 600                 # break rates rebuilt from the ledger at most every 10 minutes


def market_ratio():
    try:
        return model.Prices(half_life_h=policy.load()["half_life_h"]).market
    except Exception:
        return model.PRICE_R0


def break_rates(now=None):
    """Goods break rate per grade (model.Estimates), cached BREAK_TTL; the model's priors if the ledger is unreadable."""
    now = now or time.time()
    try:
        c = json.loads(BREAK_PATH.read_text(encoding="utf-8"))
        if now - c.get("at", 0) < BREAK_TTL:
            return c["r"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        r = dict(model.Estimates().break_rate)
    except Exception:
        return dict(model.BREAK_PRIOR)
    try:
        tmp = BREAK_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": now, "r": r}), encoding="utf-8")
        tmp.replace(BREAK_PATH)
    except OSError:
        pass
    return r


def target():
    """The buyer's min_saved now (buyer_manager moves it within its range)."""
    try:
        import buyer_policy
        return float(buyer_policy.load()["min_saved"])
    except Exception:
        return T_DEFAULT


def stars_saved(price, list_price, settled, kind, rates, t):
    """The "saved" rubric (module docstring): (stars, review line)."""
    name = settled.get("name") or settled.get("sku") or "the item"
    if settled.get("claim_honest") is False:
        return 1, f"{name}: claimed grade {settled.get('claim')}, the market's record says {settled.get('grade')}."
    if settled.get("value") == 0:
        return 1, f"{name}: not usable for this order (shelf life too short)."
    if not list_price or not price:
        return 4, f"{name} at {price}."
    r = price / list_price
    s = 1 - r
    g = settled.get("grade")
    said = [f"grade {g}" + (", as claimed" if settled.get("claim_honest") else "")] if g else []
    said.append(f"paid {round(100 * r)}% of list")
    no_w = kind == "goods" and not settled.get("warranty_hours")
    if kind == "goods":
        if settled.get("warranty_hours"):
            said.append(f"{settled['warranty_hours']} h warranty")
        else:
            s -= rates.get(g, max(rates.values())) * r
    stars = 5 if s >= t + STEP else 4 if s >= t else 3 if s >= t - STEP else 2 if s >= 0 else 1
    off = min(NO_WARRANTY_DROP, stars - 2) if no_w and stars > 2 else 0
    stars -= off
    text = f"{name}: " + ", ".join(said) + "."
    if no_w:
        text += (" No warranty" + (f", so {off} star{'s' if off > 1 else ''} off" if off else "")
                 + ": goods can break within 6 h of the sale and without a warranty the whole price is lost.")
    return stars, text


def stars_for(price, list_price, settled, market):
    name = settled.get("name") or settled.get("sku") or "the item"
    if settled.get("claim_honest") is False:
        return 1, f"{name}: claimed grade {settled.get('claim')}, the market's record says {settled.get('grade')}."
    if settled.get("value") == 0:
        return 1, f"{name}: not usable for this order (shelf life too short)."
    if not list_price:
        return 4, f"{name} at {price}."
    r = price / list_price
    stars = 5 if r < market - BAND else 4 if r <= market + BAND else 3
    extra = (f", grade {settled['grade']}" if settled.get("grade") else "") \
        + (", as claimed" if settled.get("claim_honest") else "") \
        + (f", {settled['warranty_hours']} h warranty" if settled.get("warranty_hours") else "")
    return stars, f"{name}: paid {round(100 * r)}% of list{extra}."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    a = ap.parse_args()
    try:
        deal = json.loads(open(a.deal, encoding="utf-8").read())
    except (OSError, ValueError):
        print(json.dumps({"stars": 3, "review": "Deal completed."}))
        return
    settled, receipt = deal.get("settled") or {}, deal.get("receipt") or {}
    price = receipt.get("price") or settled.get("amount") or 0
    list_price = settled.get("value") or None          # value is the SKU's list price when the unit is usable
    if not list_price:
        try:
            cat = json.loads((Path(os.environ.get("MARKET_DIR", ".market")) / "catalog.json").read_text(encoding="utf-8"))
            list_price = (cat.get(settled.get("sku")) or {}).get("list_price")
        except (OSError, ValueError):
            pass
    kind = None
    try:
        cat = json.loads((Path(os.environ.get("MARKET_DIR", ".market")) / "catalog.json").read_text(encoding="utf-8"))
        kind = (cat.get(settled.get("sku")) or {}).get("kind")
    except (OSError, ValueError):
        pass
    if kind is None:
        kind = "food" if settled.get("expires_at") else "goods"
    if RUBRIC == "saved":
        t = target()
        stars, text = stars_saved(price, list_price, settled, kind, break_rates(), t)
    else:
        stars, text = stars_for(price, list_price, settled, market_ratio())
    try:
        policy.DATA.mkdir(parents=True, exist_ok=True)
        with open(PURCHASES, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "order": (deal.get("order") or {}).get("order"),
                                "shop": settled.get("shop"), "sku": settled.get("sku"), "name": settled.get("name"),
                                "price": price, "list": list_price, "grade": settled.get("grade"),
                                "claim_honest": settled.get("claim_honest"), "warranty_hours":
                                settled.get("warranty_hours"), "expires_at": settled.get("expires_at"),
                                "ledger": settled.get("ledger"), "stars": stars, "rubric": RUBRIC,
                                "target": t if RUBRIC == "saved" else None}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    print(json.dumps({"stars": stars, "review": text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
