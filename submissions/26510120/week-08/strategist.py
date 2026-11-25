"""Strategist: the one place this agent calls a model. restock.py starts it when policy.json is older than
strategist_every_min (about every 20 minutes).

    python strategist.py            # one round: read the context, ask the model, write policy.json
    python strategist.py --dry      # print the context and the prompt, call nothing

Context (built by code, so the model reads a page, not the raw files):
  market    market_tool.summary(): top SKUs and categories by settled revenue, goods breaks by grade, my share
  shop      inventory (balance, units, stock at cost, food close to its date), my sales (logs/seller_r3.jsonl),
            my refunds and expired stock (the ledger)
  buyer     attempts (buyer_memory.jsonl), paid and refused payments with reasons (.market/payments.jsonl),
            saved share per deal (state/purchases.jsonl)
  board     my shop ALL and buyer ALL with their parts next to the student medians (/api/board)
  forum     the highest-scored posts
  self      the current policy and the last three decisions with their reasons (logs/strategist.jsonl)
The model answers with the policy keys it wants to change and why. Code clamps every value to RANGES, drops unknown
keys, writes policy.json and appends the whole round (context digest, raw answer, applied changes) to
logs/strategist.jsonl. A failed call keeps the old policy and is logged too.
"""
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

import market_tool as mt
from harness import ask

HERE = Path(__file__).resolve().parent
LOG = HERE / "logs" / "strategist.jsonl"
LOCK = mt.STATE / "strategist.pid"
MD = HERE / ".market"
ACCEPT_SINCE = "2026-11-06 13:34 (this machine's clock)"   # market_buyer 7e0a823 went live

RANGES = {
    "lookback_h": (0.5, 6), "min_sales": (2, 20), "max_unit_list": (100000, 4000000), "max_surge": (1.0, 1.3),
    "min_shelf_h": (6, 400), "cash_reserve": (600000, 5000000), "window_spend_frac": (0.1, 0.9),
    "share_guess": (0.005, 0.2), "cover_h": (0.3, 3), "anchor_ratio": (0.4, 0.95), "ask_premium": (0, 0.2),
    "floor_below_anchor": (0, 0.2), "min_margin": (0, 0.3), "clearance_h": (0, 12), "warranty_h": (0, 72),
    "buyer_open": (0.3, 0.8), "buyer_max": (0.5, 0.95), "buyer_hours_margin": (0, 12), "buyer_shops_tried": (1, 5),
}
INTS = {"min_sales", "max_unit_list", "cash_reserve", "buyer_shops_tried"}

PROMPT = """You set the policy of a shop and a buyer agent in a simulated market (credits = won). Code runs every
order and every restock with the numbers you set; you are asked again in about 20 minutes with fresh numbers.

How they are ranked (each part is a percentile among all agents):
  shop  = 0.5 book (balance - start - bankruptcy aid + unsold stock at cost) + 0.3 revenue + 0.2 stars
  buyer = 0.5 saved (sum over deals of (list - price)/list, -1 for food that did not last or goods that broke
          without warranty, divided by deals + 100) + 0.3 forum rating + 0.1 deals + 0.1 posts
Rules that matter: stock is paid when it arrives; food grade falls with shelf life and food past its date is lost
at cost; goods may break within 6 h of the sale and a warranty refunds the buyer from the shop's balance; goods
pay 1% of cost a day in storage; a supply window opens every 20 min for 3 min; buying more than 50 units of one
SKU in an hour across the market raises its cost; below 500,000 the shop may go bankrupt (twice).

What each policy key does:
{doc}

Current policy:
{policy}

Your last decisions (oldest first):
{history}

Now:
{context}

Change only what the numbers support. Answer with one JSON object and nothing else:
{{"changes": {{"<key>": <value>, ...}}, "reason": "<two or three sentences: what you saw and why these changes>"}}"""

DOC = """lookback_h, min_sales: restock ranks SKUs by settled revenue over the last lookback_h hours; needs min_sales deals
max_unit_list: restock skips SKUs whose list price is above this
max_surge: restock skips SKUs whose supply cost factor is above this
min_shelf_h: restock skips food that keeps for less than this many hours
cash_reserve, window_spend_frac: each supply window spends at most window_spend_frac x (balance - cash_reserve)
share_guess, cover_h: restock buys about market deals/h x share x cover_h of each SKU (share is measured once I sell)
anchor_ratio {A,B,C}: seller's price/list when the ledger has no median for the SKU
ask_premium, floor_below_anchor, min_margin: first ask = median + premium; lowest = median - floor_below, >= cost x (1+min_margin)
clearance_h: food this close to its date may sell below cost
warranty_h {A,B,C}: hours of warranty the seller gives on goods of that grade (0 = none)
buyer_open, buyer_max: buyer opens at open x list and never pays above max x list (or the budget)
buyer_grades_goods: goods grades the buyer accepts, e.g. ["A","B"]
buyer_hours_margin: food must last use_within_hours + this
buyer_shops_tried: shops the buyer tries per order"""


def load_jsonl(p, since=None):
    out = []
    if p.exists():
        for l in p.read_text(encoding="utf-8").splitlines():
            try:
                x = json.loads(l)
            except ValueError:
                continue
            out.append(x)
    if since:
        out = [x for x in out if x.get("time", "") >= since]
    return out


def context():
    cat = mt.catalog()
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 2 * 3600))
    ctx = {"market_2h": mt.summary(2.0)}
    for x in ctx["market_2h"]["top_skus"]:
        x.pop("mine", None)
    try:
        inv = json.loads((MD / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inv = {"units": [], "balance": None}
    now = mt.market_now()
    units = inv.get("units", [])
    sales = load_jsonl(HERE / "logs" / "seller_r3.jsonl", since)
    me = [e["data"] for e in mt.entries(2.0, ("broken", "expire"))]
    ctx["shop"] = {
        "balance": inv.get("balance"), "units": len(units), "stock_cost": sum(u["cost"] for u in units),
        "goods_units": sum(1 for u in units if u["kind"] == "goods"),
        "food_units": sum(1 for u in units if u["kind"] == "food"),
        "food_within_6h_of_date": sum(1 for u in units if u.get("expires_at") and u["expires_at"] - now < 6 * 3600),
        "deals_agreed_2h": len(sales),
        "avg_price_over_list": round(statistics.mean(s["price"] / s["list"] for s in sales), 3) if sales else None,
        "avg_margin_over_cost": round(statistics.mean(s["price"] / s["cost"] - 1 for s in sales), 3) if sales else None,
        "refunds_2h": sum(1 for d in me if d.get("refunded") and mt.name_of(d.get("shop")) == mt.ME),
        "expired_units_2h": sum(len(d.get("units", [])) for d in me if d.get("shop") == mt.ME),
    }
    tries = load_jsonl(HERE / "buyer_memory.jsonl", since)
    pays = load_jsonl(MD / "payments.jsonl")[-200:]
    buys = load_jsonl(mt.STATE / "purchases.jsonl", since)
    reasons = {}
    for p in pays:
        if not p.get("paid"):
            r = (p.get("reason") or "?")[:60]
            reasons[r] = reasons.get(r, 0) + 1
    outcomes = {}
    for t in tries:
        k = t["result"].split(":")[0].split(" (")[0]
        outcomes[k] = outcomes.get(k, 0) + 1
    every = load_jsonl(mt.STATE / "purchases.jsonl")           # all deals since the review hook (after ACCEPT_SINCE)
    ctx["buyer"] = {
        "attempts_2h": len(tries), "attempt_outcomes": outcomes,
        "paid_last_200": sum(1 for p in pays if p.get("paid")), "refused_reasons_last_200": reasons,
        "deals_2h": len(buys),
        "avg_saved_share": round(statistics.mean(1 - b["price"] / b["list"] for b in buys if b.get("list")), 3) if buys else None,
        "broken_reports": len(load_jsonl(MD / "broken.jsonl")),
        "note": f"the accept policy (food shelf life, goods grades) has run since {ACCEPT_SINCE}; deals before it "
                "had no checks, and the board's served/deals still counts them",
        "since_accept_policy": {
            "deals": len(every),
            "food_deals": sum(1 for b in every if b.get("kind") == "food"),
            "food_short_of_use_within": sum(1 for b in every if b.get("kind") == "food" and b.get("hours_left") is not None
                                            and b.get("use_within_hours") and b["hours_left"] < b["use_within_hours"]),
            "goods_grade_C": sum(1 for b in every if b.get("kind") == "goods" and b.get("grade") == "C"),
        },
    }
    try:
        b = httpx.get(mt.MARKET + "/api/board", timeout=20).json()
        shops = [s for s in b["shops"] if not s["house"] and s.get("deals")]
        buyers = [x for x in b["buyers"] if not x["house"] and x.get("deals")]
        mine_s = next((s for s in b["shops"] if s["name"] == mt.ME), {})
        mine_b = next((x for x in b["buyers"] if x["name"] == mt.ME), {})
        med = lambda xs, k: statistics.median([x[k] for x in xs if x.get(k) is not None]) if xs else None
        ctx["board"] = {
            "my_shop": {k: mine_s.get(k) for k in ("deals", "revenue", "book", "stars", "stock_units", "refunds", "all")},
            "student_shop_median": {k: med(shops, k) for k in ("deals", "revenue", "book", "stars")},
            "my_buyer": {k: mine_b.get(k) for k in ("orders", "deals", "served", "saved", "forum", "posts", "all")},
            "student_buyer_median": {k: med(buyers, k) for k in ("deals", "saved", "forum", "posts")},
        }
    except (httpx.HTTPError, ValueError, KeyError) as e:
        ctx["board"] = f"unavailable: {e!r}"[:100]
    try:
        posts = json.loads((MD / "forum.json").read_text(encoding="utf-8"))
        ctx["forum_top"] = [f"{p['role']} {p['author']} score {p['score']}: {p['text'][:140]}"
                            for p in sorted(posts, key=lambda p: -(p.get("score") or 0))[:5]]
    except (OSError, ValueError):
        ctx["forum_top"] = []
    return ctx


def clamp(key, v, cur):
    lo, hi = RANGES[key]
    if isinstance(cur, dict):
        if not isinstance(v, dict):
            return None
        return {g: round(min(hi, max(lo, float(x))), 3) for g, x in v.items() if g in cur and isinstance(x, (int, float))}
    if not isinstance(v, (int, float)):
        return None
    v = min(hi, max(lo, float(v)))
    return int(v) if key in INTS else round(v, 3)


def apply(changes, pol):
    applied = {}
    for k, v in (changes or {}).items():
        if k == "buyer_grades_goods":
            if isinstance(v, list) and v and all(g in ("A", "B", "C") for g in v):
                applied[k] = sorted(set(v))
            continue
        if k not in RANGES or k not in pol:
            continue
        c = clamp(k, v, pol[k])
        if c is not None:
            applied[k] = c
    return applied


def main():
    dry = "--dry" in sys.argv
    mt.STATE.mkdir(exist_ok=True)
    if not dry:
        LOCK.write_text(str(__import__("os").getpid()))
    try:
        mt.sync()
        pol = mt.policy()
        hist = [f"{h['time']}: {h.get('applied')} because {h.get('reason', '')[:300]}"
                for h in load_jsonl(LOG)[-3:] if h.get("applied") is not None]
        ctx = context()
        prompt = PROMPT.format(doc=DOC, policy=json.dumps(pol, ensure_ascii=False), history="\n".join(hist) or "(none)",
                               context=json.dumps(ctx, ensure_ascii=False, indent=1))
        if dry:
            print(prompt)
            print(f"\n[{len(prompt)} characters]")
            return
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "prompt_chars": len(prompt), "context": ctx}
        try:
            raw = ask(prompt, timeout=240)
            obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            applied = apply(obj.get("changes"), pol)
            entry.update(raw=raw[:3000], reason=str(obj.get("reason", ""))[:1000], applied=applied)
        except Exception as e:                       # no answer or no JSON: keep the old policy
            entry.update(error=repr(e)[:500], applied=None)
            applied = {}
        old = json.loads(mt.POLICY.read_text(encoding="utf-8")) if mt.POLICY.exists() else {"policy": {}}
        new = dict(old.get("policy", {}))
        for k, v in applied.items():
            new[k] = dict(pol[k], **v) if isinstance(pol[k], dict) else v
        mt.POLICY.write_text(json.dumps({"written_at": time.time(), "policy": new,
                                         "reason": entry.get("reason") or entry.get("error")},
                                        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[strategist] applied {applied}", flush=True)
    finally:
        if not dry:
            LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
