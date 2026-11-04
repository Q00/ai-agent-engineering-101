"""The shop's restock hook: what to buy before any order comes. The kit runs it every 60 s in Round 3.

    python ../restock.py --state .market        (agent.json in shop/: "restock": {"cmd": "python ../restock.py --state {state}"})

Prints {"buy": [{"sku", "qty"}, ...]} last (and "bankrupt": true only when the shop is stuck below the line).

Who decides what:
- Code turns the market's public signals into a ranked list of candidate SKUs, each with a reason:
  settles on the public ledger (what sold, decayed with a 2 h half-life), our own sales, buyers' "wanted" posts on
  the forum, and how much of the SKU other shops already hold (GET /api/shops). Margin comes from the price/list
  ratio settles show per category against the expected cost of a random grade; short-lived food and goods that
  break more often are marked down.
- The harness reads the top candidates and picks the lines to buy with a reason; code then checks every line
  (only listed SKUs, a quantity cap per line, the window's spending limit) and falls back to its own ranking.
- Code keeps the money rules: buy only while a supply window is open or opens within a minute, spend at most a
  share of the cash above a safety cushion per window, never a lot whose cost could pull the balance near the
  bankruptcy line.

It also marks which of our goods broke (ledger "broken" entries) in shop_memory.json, so the seller prices warranties
from our own break rate, and posts a short stock note on the forum as the shop at most every 30 minutes.
"""
import argparse
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

from harness import ask_json
from market_state import MARKET, catalog, forum_wants, leaf, read_json, settles, write_json

sys.stdout.reconfigure(encoding="utf-8")

HALF_LIFE = 2 * 3600            # seconds; demand from settles fades with this half-life
CUSHION = 1_500_000             # cash never spent on stock (the bankruptcy line is 500,000)
SPEND_SHARE = 0.30              # of the cash above the cushion, per supply window
LINE_MAX = 0.10                 # of the cash above the cushion, per line
TARGET = {"food": 3, "goods": 2}            # base units per SKU to hold; a window lasts 20 minutes
TARGET_MAX = 8                              # most units of one SKU, however fast it turns over
STALE_H = 6                                 # a unit unsold this long stops further buying of its SKU
MAX_LINES = 8                  # the kit buys at most 10 lines per run, one call per 10 s
COST = {"food": 0.50, "goods": 0.59}        # expected cost / list price of a random grade (agent.md supply odds)
PRICE_DEFAULT = 0.70            # settled price / list price across the market in the first hour of Round 3
POST_EVERY = 1800
TOP_N = 25                      # candidates the harness reads


def decay(ts: float, now: float) -> float:
    return 0.5 ** ((now - ts) / HALF_LIFE)


def others_stock() -> dict:
    """SKU -> units other shops hold now (public)."""
    try:
        r = httpx.get(f"{MARKET}/api/shops", timeout=20)
        shops = r.json().get("shops", []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return {}
    tot = defaultdict(int)
    for s in shops:
        if s.get("name") == "moss-and-oak":
            continue
        for k, v in (s.get("stock") or {}).items():
            tot[k] += v
    return tot


def mark_broken(rows: list, mem_path: Path):
    """Our goods that broke: a ledger 'broken' entry for a unit we sold (or our SKU within 6 h of our sale)."""
    mem = read_json(mem_path, {}) or {}
    sold = mem.get("sold", {})
    if not sold:
        return
    changed = 0
    for r in rows:
        if not r.get("broken"):
            continue
        hit = sold.get(r.get("unit")) if r.get("unit") else None
        if hit is None:
            hit = next((s for s in sold.values() if s["sku"] == r.get("sku") and not s.get("broke")
                        and 0 <= r["ts"] - s["t"] <= 6 * 3600), None)
        if hit is not None and not hit.get("broke"):
            hit["broke"] = int(r["ts"])
            changed += 1
    if changed:
        write_json(mem_path, mem)
        print(f"[restock] {changed} of our goods broke since the last run", flush=True)


def market_prices(rows: list, cat: dict, path: Path, now: float):
    """What buyers pay now, as a share of list, for the seller's opening ask: per SKU and grade with 8+ settles in
    the last 2 h, per kind, grade and warranty with 5+."""
    sku, cls = defaultdict(list), defaultdict(list)
    for r in rows:
        x = cat.get(r.get("sku"))
        if r.get("broken") or not x or not r.get("amount") or now - r["ts"] > 2 * 3600 or not x.get("list_price"):
            continue
        ratio = r["amount"] / x["list_price"]
        sku[f"{r['sku']}|{r.get('grade')}"].append(ratio)
        cls[f"{x['kind']}|{r.get('grade')}|{1 if r.get('warranty_hours') else 0}"].append(ratio)
    write_json(path, {"t": int(now), "sku": {k: round(statistics.median(v), 3) for k, v in sku.items() if len(v) >= 8},
                      "class": {k: round(statistics.median(v), 3) for k, v in cls.items() if len(v) >= 5}})


def turnover(units: list, sold: dict, now: float) -> tuple[dict, set]:
    """(our sales per supply window by SKU over the last 3 h, SKUs with a unit unsold after STALE_H)."""
    rate = defaultdict(float)
    for s in sold.values():
        if now - s.get("t", 0) < 3 * 3600:
            rate[s["sku"]] += 1 / 9                                 # nine 20-minute windows in 3 h
    return rate, {u["sku"] for u in units if now - (u.get("supplied_at") or now) > STALE_H * 3600}


def target_for(c: dict, rate: dict, stale: set) -> int:
    """Units to hold of a SKU: what it takes to sell through one window, not a flat number. A SKU we hold unsold
    gets no more cash; one selling for us or fast across the market gets more."""
    if c["sku"] in stale:
        return 0
    fast = 2 if c["sold_sku"] >= 50 else 1 if c["sold_sku"] >= 20 else 0
    return min(TARGET_MAX, TARGET[c["kind"]] + fast + math.ceil(rate.get(c["sku"], 0) * 2))


def candidates(cat: dict, rows: list, mine: dict, wants: dict, others: dict, have: dict, now: float) -> list:
    """Every SKU with some sign of demand, scored by expected profit per unit held."""
    sku_d, cat_d, ratio = defaultdict(float), defaultdict(float), defaultdict(list)
    for r in rows:
        if r.get("broken") or r.get("sku") not in cat or now - r["ts"] > 12 * 3600:
            continue
        w = decay(r["ts"], now)
        x = cat[r["sku"]]
        sku_d[r["sku"]] += w
        cat_d[leaf(x["category"])] += w
        if r.get("amount") and x.get("list_price"):
            ratio[leaf(x["category"])].append(r["amount"] / x["list_price"])
    per_cat_skus = defaultdict(list)
    for s, x in cat.items():
        per_cat_skus[leaf(x["category"])].append(s)
    out = []
    pool = set(sku_d) | {s for lf in set(cat_d) | set(wants) for s in per_cat_skus.get(lf, [])}
    for s in pool:
        x = cat[s]
        lf = leaf(x["category"])
        n_cat = max(1, len(per_cat_skus[lf]))
        demand = sku_d[s] * 3 + cat_d[lf] / n_cat * 2 + wants.get(lf, 0) * 0.5 / n_cat ** 0.5 \
            + mine.get(lf, {}).get("deals", 0) * 0.2 / n_cat ** 0.5
        if demand <= 0.02:
            continue
        r = sorted(ratio[lf])
        price = r[len(r) // 2] if len(r) >= 3 else PRICE_DEFAULT
        margin = price - COST[x["kind"]]
        if x["kind"] == "food":
            sh = x.get("shelf_hours") or 24
            margin -= 0.25 if sh < 12 else 0.10 if sh < 48 else 0.0     # short-lived food is thrown away more
        else:
            margin -= 0.03                                              # storage, breaks
        if margin <= 0.02:
            continue
        crowd = others.get(s, 0)
        # profit per unit held, damped by price: a 300,000 mattress that rarely sells ties up the cash of thirty
        # kimchi that do; dividing by other shops' stock keeps us out of SKUs everyone already holds
        score = demand * margin * math.sqrt(x["list_price"]) / (1 + crowd / (1 + 3 * sku_d[s]))
        out.append({"sku": s, "name": x["name"][:40], "cat": lf, "kind": x["kind"], "list": x["list_price"],
                    "shelf_h": x.get("shelf_hours"), "score": round(score, 1), "demand": round(demand, 2),
                    "sold_sku": round(sku_d[s], 2), "price_ratio": round(price, 2), "others_hold": crowd,
                    "we_hold": have.get(s, 0), "wanted_posts": wants.get(lf, 0)})
    out.sort(key=lambda c: c["score"], reverse=True)
    return out


PICK_SYSTEM = """You plan stock purchases for an online shop agent. You get candidate products with numbers the
code computed from the public ledger (sold_sku: recent settles of that SKU, decayed; demand: combined signal;
price_ratio: what buyers paid as a share of list price in that category; others_hold: units other shops have;
we_hold: our units; target: units worth holding, from how fast it turns over) and a spending limit. We sell at a thin
margin and many units, so cash that turns over fast matters more than the margin on one unit. Buy what is likely to
sell within the next 20 minutes at a profit, up to target minus we_hold per SKU. Prefer SKUs that actually sold
recently; avoid food with a short shelf life unless it sells fast. Spread risk over several SKUs. Reply with JSON only:
{"buy": [{"sku": "<sku from the list>", "qty": <1-8>}], "reason": "<one sentence>"}"""


def harness_pick(cands: list, limit: int) -> tuple[list, str]:
    rows = [{k: c[k] for k in ("sku", "name", "cat", "kind", "list", "shelf_h", "sold_sku", "demand", "price_ratio",
                               "others_hold", "we_hold", "target")} for c in cands[:TOP_N]]
    ans = ask_json(PICK_SYSTEM, f"Spending limit: {limit} credits (cost is about 50-60% of list price).\n"
                                f"Candidates:\n{json.dumps(rows, ensure_ascii=False)}", timeout=60, attempts=1) or {}
    buy = ans.get("buy") if isinstance(ans.get("buy"), list) else []
    return buy, str(ans.get("reason", ""))[:200]


def plan(cands: list, limit: int, line_max: int, have: dict, use_harness: bool = True,
         rate: dict | None = None, stale: set | None = None) -> tuple[list, str]:
    """Lines to buy within the limit: the harness's pick, checked; else the code's ranking."""
    rate, stale = rate or {}, stale or set()
    for c in cands:
        c["target"] = target_for(c, rate, stale)
    cands = [c for c in cands if c["sku"] not in stale]
    by_sku = {c["sku"]: c for c in cands}
    est = lambda c, q: int(c["list"] * COST[c["kind"]] * 1.15 * q)          # cost estimate with a margin
    picked, why = (harness_pick(cands, limit) if use_harness and cands else ([], ""))
    lines, spent = [], 0
    for b in picked:
        c = by_sku.get(str(b.get("sku"))) if isinstance(b, dict) else None
        if c is None or any(l["sku"] == c["sku"] for l in lines):
            continue
        q = min(int(b.get("qty") or 1), c["target"] - have.get(c["sku"], 0))
        while q > 0 and (est(c, q) > line_max or spent + est(c, q) > limit):
            q -= 1                                                          # trim the line, do not drop it
        if q <= 0:
            continue
        lines.append({"sku": c["sku"], "qty": q})
        spent += est(c, q)
    src = f"harness: {why}" if lines else "code ranking"
    if not lines:
        for c in cands:
            q = c["target"] - have.get(c["sku"], 0)
            if q <= 0:
                continue
            while q > 0 and (est(c, q) > line_max or spent + est(c, q) > limit):
                q -= 1
            if q > 0:
                lines.append({"sku": c["sku"], "qty": q})
                spent += est(c, q)
            if len(lines) >= MAX_LINES:
                break
    if lines and len(lines) < MAX_LINES and random.random() < 0.25:        # explore: one SKU just below the top
        rest = [c for c in cands[len(lines):len(lines) + 30] if c["sku"] not in {l["sku"] for l in lines}
                and est(c, 1) <= line_max and spent + est(c, 1) <= limit]
        if rest:
            c = random.choice(rest)
            lines.append({"sku": c["sku"], "qty": 1})
            spent += est(c, 1)
            src += f"; exploring {c['sku']}"
    return lines[:MAX_LINES], f"{src}; about {spent} credits"


def stock_post(md: Path, inv: dict, cat: dict, mem: dict):
    """A short note as the shop on the forum: what we hold, honestly graded. At most every 30 minutes."""
    if time.time() - mem.get("last_post", 0) < POST_EVERY or not inv.get("units"):
        return
    by = defaultdict(int)
    for u in inv["units"]:
        by[leaf(u.get("category"))] += 1
    top = sorted(by.items(), key=lambda x: -x[1])[:5]
    text = ("moss-and-oak 재고: " + ", ".join(f"{k} {v}개" for k, v in top)
            + ". 모든 단위에 시장 인증서, 등급은 인증서 그대로, 식품은 남은 시간을 말씀드리고 필요한 시간만큼 가는 단위를 "
              "보냅니다. 비식품 A등급은 6시간 보증 포함, 다른 등급도 요청하면 보증을 붙입니다.")
    with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"post": text, "refs": [], "as": "shop"}, ensure_ascii=False) + "\n")
    mem["last_post"] = time.time()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--no-harness", action="store_true")
    args = ap.parse_args()
    md = Path(args.state)
    shop_dir = md.parent
    mem_path = shop_dir / "restock_memory.json"
    mem = read_json(mem_path, {}) or {}
    now = time.time()
    inv = read_json(md / "inventory.json", {}) or {}
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    balance = inv.get("balance") or 0
    window = inv.get("restock") or {}
    rows = settles(shop_dir / "settles.json")
    mark_broken(rows, shop_dir / "shop_memory.json")
    cat = catalog(md)
    if cat:
        market_prices(rows, cat, shop_dir / "market_prices.json", now)

    if balance < (inv.get("bankrupt_line") or 500_000) and not units and (inv.get("bankruptcies_left") or 0) > 0:
        print(json.dumps({"buy": [], "bankrupt": True}))
        return
    if not window.get("open") and (window.get("opens_at") or now) - now > 60:
        print(f"[restock] supply closed, opens in {((window.get('opens_at') or now) - now) / 60:.1f} min", flush=True)
        print(json.dumps({"buy": []}))
        return
    key = str(window.get("closes_at") or window.get("opens_at") or "")
    if mem.get("window") == key and mem.get("runs", 0) >= 2:
        print("[restock] already bought twice in this window", flush=True)
        print(json.dumps({"buy": []}))
        return

    if not cat:
        print("[restock] no catalog yet", flush=True)
        print(json.dumps({"buy": []}))
        return
    have = defaultdict(int)
    for u in units:
        have[u["sku"]] += 1
    free = max(0, balance - CUSHION)
    spent_window = mem.get("spent", 0) if mem.get("window") == key else 0
    limit = int(max(0, free * SPEND_SHARE - spent_window))
    if limit < 5000:
        print(f"[restock] nothing to spend (balance {balance}, cushion {CUSHION})", flush=True)
        print(json.dumps({"buy": []}))
        return
    shop_mem = read_json(shop_dir / "shop_memory.json", {}) or {}
    cands = candidates(cat, rows, shop_mem.get("cats", {}), forum_wants(md, cat), others_stock(), have, now)
    rate, stale = turnover(units, shop_mem.get("sold", {}), now)
    lines, why = plan(cands, limit, int(free * LINE_MAX), have, not args.no_harness, rate, stale)
    print(f"[restock] turnover: {len(rate)} SKUs sold in 3 h, {len(stale)} SKUs stale; "
          f"sold units held {sum(s.get('held_h', 0) for s in shop_mem.get('sold', {}).values()):.1f} h in total", flush=True)
    for c in cands[:8]:
        print(f"[restock] cand {c['score']:>9} {c['sku']} {c['kind']} {c['cat']} sold {c['sold_sku']} "
              f"others {c['others_hold']} we {c['we_hold']} ratio {c['price_ratio']}", flush=True)
    print(f"[restock] balance {balance}, limit {limit}, {len(units)} units held, {len(rows)} ledger rows; {why}",
          flush=True)
    spent = sum(int(cat[l["sku"]]["list_price"] * COST[cat[l["sku"]]["kind"]] * l["qty"]) for l in lines)
    mem.update({"window": key, "runs": (mem.get("runs", 0) + 1) if mem.get("window") == key else 1,
                "spent": spent_window + spent})
    stock_post(md, inv, cat, mem)
    write_json(mem_path, mem)
    print(json.dumps({"buy": lines}, ensure_ascii=False))


if __name__ == "__main__":
    main()
