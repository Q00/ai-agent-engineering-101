"""Strategist: runs the SHOP of yms979 for the class total (user's design 11-05; rebuilt 11-13 for the last 92 h).

    python strategist.py [--force]        (watch_round.sh runs it every 15 min; it works in its own 20-minute slots,
                                           the buyer strategist in the others; at once on a guard wake:
                                           .market/strategist_wake)
    python strategist.py --dry-prompt     (prints the prompt it would send; no model call, no file written)

One run:
  1. memory      product_memory.update(): per-SKU purchases, sales, holding time, sold-out asks (48 h)
  2. learn       every move whose 1-3 h window is over is measured shop-wide on its metric against the same window
                 before it (market_picture.funnel_between) and gets helped / hurt / unclear (2 standard errors) in the
                 MOVE LEDGER; a run's moves are reverted together only when a harm guard fires (paid deals/h under
                 60% of the baseline, or paid margin/h under 0 and 0.5 M/h under the baseline)
  3. evidence    RACE (race_view: book, revenue, stars_rank, total, targets), FUNNEL (funnel_view), our SKUs, the knobs
                 with their bounds, stars accounts and ladder, settle health, the buyer's notes, operator notes, the
                 curator's rule changes and answers; prompt <= 45k chars
  4. decide      MODEL (opus) answers in JSON: a market read, at most MAX_MOVES moves each with one shop metric and a
                 1-3 h window, FACTS edits (<= 15, each with an expiry), requests to the curator
  5. checks      bounds, the per-run price step (.05; .02 inside 24 h), the churn freeze (3 moves of one knob in 3 h),
                 report only from T-3 h
  6. act         mode "apply" (params.json strategist.mode) writes params_auto.json under a lock, only this run's
                 changes, with the RAILS applied on every write (apply_rails)
Everything goes to logs/strategist.log; the memory to strategist_memory.json; the last answer to strategist_last.json.
"""
import argparse
import calendar
import fcntl
import json
import math
import os
import shlex
import shutil
import subprocess
import sys

import model_env
import tempfile
import time
import urllib.request
from pathlib import Path

import market_probe as probe
import product_memory as pm
from harness import last_json_object
from strategist_memory import Memory, my_turn, run_gate, operator_note, post_board, read_board, read_digest, valid_requests

HERE = Path(__file__).resolve().parent
AUTO = HERE / "params_auto.json"
AUTO_LOCK = HERE / ".market" / "params_auto.lock"
LOGF = HERE / "logs" / "strategist.log"
PARAMS = HERE / "params.json"
GUIDE = HERE / "MARKET_GUIDE.md"
MARKET = "https://market.ourolabs.ai"
END_T = calendar.timegm((2026, 11, 17, 14, 59, 0, 0, 0, 0))     # 1794927540: the round end, 2026-11-17 14:59 UTC
EVERY = 3600 - 120
CLAUDE = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")   # cron has a bare PATH
# 11-11 (user: API cost): effort pinned (the CLI default was unpinned; ~73% of a call was output/thinking), a hard
# cap per call, and --output-format json (added by model_env.run) so each call's cost lands in spend.jsonl
MODEL_CMD = f"{CLAUDE} -p --model opus --effort high --max-budget-usd 0.8 --tools '' --setting-sources '' --no-session-persistence"
MODEL_TIMEOUT = 600
# 11-13 (diagnosis ARCH-3): the 17:47 prompt was 112,130 chars and 45 SKU rows; 25 rows and a 45k-char cap
TOP_SKUS = 25
MAX_PROMPT = 45000
# 11-13 (diagnosis ARCH-2): 12 -> 6; with switches and caps free to move in one run, fewer moves stay readable
MAX_MOVES = 6
REFLECT_MISSES = 10 ** 6       # 11-11: critic off (130 lesson edits/day, lessons evicted within hours, ~$0.6/day)
MAX_FACTS = 15
DRY = False                    # --dry-prompt: nothing is written (log prints only, no board cache, no state files)

KNOBS = {                             # per-SKU factors: bounds, step per run
    "ask": ((0.85, 1.05), 0.05),      # x the seller's ask for this SKU (the floor at cost stays)
    "cover": ((0.0, 2.0), 2.0),       # x the stock restock holds of this SKU (0 = stop buying it); anywhere in 0-2
}
# 11-13 18:40 (diagnosis ARCH-2, verified): the old bounds snapped live values back on any move (market_share .4 >
# .3, max_stock_share .99 > .95, sku_cover_hours 12 > 8, slow_hours 6 < 8: a move clamped them to the old range) and
# the adaptive steps had shrunk to ask .00625 / keep_cash 10 M: max_stock_share sat at .6 for 7.6 h of blocked
# restock because the agent 'will not make a move it cannot score'. Price knobs (PRICE_KNOBS) move .05 a run (.02
# inside 24 h); every switch and cap may go anywhere in its bounds in one run (the step below is the full range).
PRICE_KNOBS = {"ask_share_A", "ask_share_B", "ask_share_C", "meet_frac", "floor_mult", "slow_floor_mult", "slow_ask_cut",
               "intel_undercut", "intel_band", "scarce_floor_mult", "rep_step", "clear_ask_min", "rival_under"}
GLOBAL = {                            # whitelisted shared parameters: role, bounds, step per run
    # 11-08 (user: 'the shop agent manages everything about the shop, not just weights'): every operating
    # setting of the shop is the strategist's, inside bounds that keep it legal and solvent
    # -- price and negotiation
    "ask_share_A": ("shop", (0.7, 0.95), 0.05),    # first ask for grade A goods, share of list (x other factors)
    "ask_share_B": ("shop", (0.65, 0.9), 0.05),
    "ask_share_C": ("shop", (0.55, 0.8), 0.05),
    "meet_frac": ("shop", (0.2, 0.8), 0.05),       # how far the seller moves toward a buyer's offer per round
    # 11-13: 1.025 = the A refund break-even with a 72 h warranty (verified L7: at exactly cost a warranted unit
    # loses its refunds)
    "floor_mult": ("shop", (1.025, 1.15), 0.05),   # floor = cost x this
    "intel_undercut": ("shop", (0.0, 0.06), 0.05),  # how far under the competitors' usual first ask
    "intel_band": ("shop", (0.0, 0.1), 0.05),      # how far the competitor read may move our ask
    "scarce_floor_mult": ("shop", (1.05, 1.3), 0.05),   # floor on SKUs that keep selling out
    "rep_step": ("shop", (0.0, 0.06), 0.05),       # ask change when our recent stars are low / high
    # 11-13: under the market reference by this much on SKUs a rival student sells; 0 unless targets.rev_push_ok
    "rival_under": ("shop", (0.0, 0.02), 0.01),
    # -- slow and old stock
    "slow_hours": ("shop", (2, 48), 46),           # unsold this long = slow (live 6 since 11-13 18:04)
    "slow_ask_cut": ("shop", (0.8, 1.0), 0.05),
    # 11-13: .99 = the hold break-even after refunds (c096b82: the .95 slow floor paid 8 sales for -0.67 M in 12 min)
    "slow_floor_mult": ("shop", (0.99, 1.05), 0.05),
    "clear_ask_min": ("shop", (0.4, 0.9), 0.05),
    "clear_window_hours": ("shop", (12, 72), 60),
    # -- terms
    "warranty_b": ("shop", (0, 1), 1),             # 1 = grade B goods carry the warranty (A always does)
    "warranty_c": ("shop", (0, 1), 1),             # 1 = grade C too (C breaks ~40%: each refund leaves the book)
    "warranty_hours": ("shop", (6, 72), 66),       # the market refuses settles above some length (8760 h was refused)
    # 11-13: goods break only within 6 h of the sale (1,452 of 1,453 in 24 h); the warranty for asks that name none
    "warranty_default_h": ("shop", (0, 72), 72),
    "grade_first": ("shop", (0, 1), 1),            # 11-11: 1 = hand A before B before C to buyers who state no grade
    # -- stock and restock
    "buy": ("shop", (0, 1), 1),                    # 0 stops all restock
    "keep_cash": ("shop", (500000, 300000000), 299500000),   # 11-13: up to 300 M (cash target)
    # 11-13 (user: 'hand the shop agent the settings the operator changed by hand'): grades sold, food age, the stars tuner
    "sell_b": ("shop", (0, 1), 1),                 # 1 = grade B goods sold (except a_only_buyers.json accounts)
    "sell_c": ("shop", (0, 1), 1),                 # 1 = grade C goods to buyers whose message accepts C (no warranty)
    "food_shelf_floor": ("shop", (0.0, 0.9), 0.9),  # least share of its shelf life a food unit must have to be sold
    # 11-13: food restock was off 11-11 20:03 -> 11-13 17:18; our food rates A 4.94 (245), B 4.67 (46) in 48 h
    "restock_food": ("shop", (0, 1), 1),
    "star_tuner_on": ("shop", (0, 1), 1),          # 1 = stars_tuner.py writes per-account price/grade terms each tick
    "max_stock_share": ("shop", (0.3, 1.0), 0.7),  # stock cost cap as a share of equity (live .99 since 11-13 18:01)
    "extra": ("shop", (0, 8), 8),                  # units over the expected sales a SKU line holds
    "max_goods_price": ("shop", (200000, 6000000), 5800000),        # live 5 M since 11-13 18:01
    "max_goods_price_hot": ("shop", (300000, 6000000), 5700000),
    "sku_cover_hours": ("shop", (2, 24), 22),      # live 12 since 11-13 18:01
    # 11-17 (G24 / stock_turnover): restock caps every line at our rate x own_cover_h less the A/B units held; the
    # rivals' big SKUs get fresh lots of big_per_window a window within big_room_share of the window's cash room
    "own_cover_h": ("shop", (1, 6), 1),
    "big_per_window": ("shop", (0, 4), 1),
    "big_room_share": ("shop", (0.0, 0.8), 0.2),
    "sku_lines": ("shop", (6, 30), 24),
    "max_goods_lines": ("shop", (6, 30), 24),
    "market_top": ("shop", (5, 30), 25),
    "market_share": ("shop", (0.05, 0.5), 0.45),   # live .4 since 11-13 18:01
    "cover_min_sales": ("shop", (0.1, 2.0), 1.9),
    "tier_count": ("shop", (0, 6), 6),
    "tier_value_share": ("shop", (0.0, 0.3), 0.3),
    "scarce_share": ("shop", (0.1, 0.6), 0.5),
    "scarce_cover_hours": ("shop", (1, 6), 5),
}
# values a knob takes when neither params_auto.json nor params.json shop.base holds it
DEFAULTS = {"warranty_default_h": 72, "restock_food": 1, "rival_under": 0.0}
# 11-13 (diagnosis ARCH-4): one shop-wide metric per move, from market_picture.funnel_between, instead of the one-SKU
# 'sold_next_6h' (2 of 3 automatic reverts on 11-13 undid good moves, judged on one SKU's closes)
SHOP_METRICS = ("paid_deals_h", "paid_margin_h", "revenue_h", "refunds_h", "no_stock_h", "canceled_h", "stars_new_mean")
COUNT_METRICS = ("paid_deals_h", "no_stock_h", "canceled_h")
MONEY_METRICS = ("paid_margin_h", "revenue_h", "refunds_h")
METRICS = SHOP_METRICS


def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def log(text):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {text}"
    print(line)
    if DRY:
        return
    LOGF.parent.mkdir(exist_ok=True)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(title, body):
    try:
        subprocess.run([str(HERE / ".venv" / "bin" / "python"), str(HERE / "notify.py"), title, body],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def hours_left(now=None):
    return max(0.0, (END_T - (time.time() if now is None else now)) / 3600)


def phase_of(H):
    return "normal" if H > 24 else "T-24" if H > 12 else "T-12" if H > 6 else "T-6" if H > 3 else "T-3"


def price_step(H):
    return 0.02 if H <= 24 else 0.05


def check_bounds(auto=None):
    """Live globals in params_auto.json outside their GLOBAL bounds: [(knob, value, lo, hi)]."""
    if auto is None:
        auto = read(AUTO, {})
    out = []
    for k, v in ((auto or {}).get("globals") or {}).items():
        if k in GLOBAL and isinstance(v, (int, float)):
            lo, hi = GLOBAL[k][1]
            if not lo <= v <= hi:
                out.append((k, v, lo, hi))
    return out


# 11-13 18:40: checked at import; an offender is logged by run() (a print here: --dry-prompt writes nothing)
try:
    BOUNDS_OFFENDERS = check_bounds()
except Exception as _e:                              # never fail the import over it
    BOUNDS_OFFENDERS = [("check failed", repr(_e), None, None)]
if BOUNDS_OFFENDERS:
    print(f"strategist: params_auto.json globals outside GLOBAL bounds: {BOUNDS_OFFENDERS}", file=sys.stderr)


def _flag(targets, key):
    """A target as a bool: True, 'ON' or {'value': True} / {'status': 'ON'}."""
    v = (targets or {}).get(key) if isinstance(targets, dict) else None
    if isinstance(v, dict):
        v = next((v[k] for k in ("ok", "on", "value", "status") if k in v), None)
    if isinstance(v, str):
        return v.strip().upper() in ("ON", "TRUE", "YES", "1")
    return bool(v) if isinstance(v, (bool, int, float)) else False


def apply_rails(g, H, targets):
    """11-13 (the diagnosis' rails; c096b82, 47f965a): the floors the code keeps on every write of params_auto.json,
    whatever a move, a revert or the operator wrote. Mutates the globals dict g; returns what it changed."""
    notes = []

    def cur(k, dflt):
        v = g.get(k)
        if v is None:
            v = current_base(k)
        try:
            return float(v) if v is not None else dflt
        except (TypeError, ValueError):
            return dflt

    def put(k, v):
        if g.get(k) != v:
            notes.append(f"{k} {g.get(k)} -> {v}")
        g[k] = v
    put("floor_mult", max(1.025, cur("floor_mult", 1.025)))
    put("slow_floor_mult", max(1.0 if H <= 24 else 0.99, cur("slow_floor_mult", 0.99)))
    put("rev_defense", 0)
    put("c_fallback", 0)
    put("rival_under", min(cur("rival_under", 0.0), 0.02) if _flag(targets, "rev_push_ok") else 0.0)
    return notes


def load_targets(race=None):
    t = (race or {}).get("targets") if isinstance(race, dict) else None
    if isinstance(t, dict) and "error" not in t:
        return t
    r = read(HERE / ".market" / "race.json", {})
    t = r.get("targets") if isinstance(r, dict) else None
    return t if isinstance(t, dict) else {}


def write_auto(sku_changes=None, global_changes=None, H=None, targets=None):
    """11-13 (diagnosis: two writers of params_auto.json lose each other's keys): under a lock, re-read the file, apply
    only this run's changes (a value of None deletes the key), apply the rails, write a temp file and replace.
    Returns the rail notes, or None when the file could not be read (nothing is written then)."""
    H = hours_left() if H is None else H
    targets = load_targets() if targets is None else targets
    AUTO_LOCK.parent.mkdir(exist_ok=True)
    with open(AUTO_LOCK, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        auto = None
        for _ in range(10):                    # another writer (guards.py G20) may be mid-write without a temp file
            try:
                auto = json.loads(AUTO.read_text(encoding="utf-8"))
                break
            except FileNotFoundError:
                auto = {}
                break
            except (OSError, ValueError):
                time.sleep(0.2)
        if not isinstance(auto, dict):
            log("write_auto: params_auto.json unreadable; nothing written")
            return None
        g = auto.setdefault("globals", {})
        s = auto.setdefault("sku", {})
        for sku, ch in (sku_changes or {}).items():
            d = s.setdefault(sku, {})
            for k, v in ch.items():
                d.pop(k, None) if v is None else d.__setitem__(k, v)
            if not d:
                s.pop(sku, None)
        for k, v in (global_changes or {}).items():
            g.pop(k, None) if v is None else g.__setitem__(k, v)
        notes = apply_rails(g, H, targets)
        auto["t"] = time.time()
        tmp = AUTO.with_name(f"{AUTO.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(auto, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, AUTO)
    if notes:
        log(f"rails: {'; '.join(notes)}")
    return notes


_BOARD = {"t": 0.0, "b": None}


def fetch_board(ttl=120):
    """The full public board, fetched once per run (it was fetched 4 times per run). --dry-prompt reads it without
    board_api's cache write (.market/board_good.json)."""
    if _BOARD["b"] is not None and time.time() - _BOARD["t"] < ttl:
        return _BOARD["b"]
    import board_api
    if DRY:
        try:
            b = board_api._get(20)
            if not board_api.sane(b):
                raise ValueError("insane board")
        except Exception:
            b = read(board_api.GOOD, {}).get("b")
    else:
        b = board_api.fetch(timeout=30)                         # 11-11: see board_api.py
    _BOARD.update(t=time.time(), b=b)
    return b


def board():
    b = fetch_board()
    shops = [s for s in b["shops"] if s.get("deals")]
    buyers = [x for x in b["buyers"] if x.get("all") is not None]

    def rank(rows, key, name="yms979"):
        order = sorted(rows, key=lambda x: -(x.get(key) or 0))
        return next((i + 1 for i, x in enumerate(order) if x["name"] == name), None), len(rows)
    s = next(x for x in shops if x["name"] == "yms979")
    u = next(x for x in buyers if x["name"] == "yms979")
    players = sorted((x for x in b["players"] if x.get("student")), key=lambda x: -(x.get("student_total") or 0))
    return {
        "class_rank": next((i + 1 for i, x in enumerate(players) if x["player"] == "yms979"), None),
        "class_top": [(x["player"], round(x["student_total"], 3), "shop", x.get("student_shop"), "buyer", x.get("student_buyer"))
                      for x in players[:6]],
        "shop": {k: s.get(k) for k in ("all", "book", "revenue", "stars", "deals", "profit", "stock_cost", "refunds")}
        | {f"{k}_rank": rank(shops, k) for k in ("book", "revenue", "stars")},
        "buyer": {k: u.get(k) for k in ("all", "surplus", "forum", "deals", "orders", "posts")}
        | {f"{k}_rank": rank(buyers, k) for k in ("surplus", "forum", "deals", "posts")},
        "top_shops": [{k: x.get(k) for k in ("name", "all", "book", "revenue", "stars", "deals", "stock_cost")}
                      for x in sorted(shops, key=lambda x: -(x.get("all") or 0))[:6]],
    }


PARTS = (("book", 0.5), ("revenue", 0.3), ("stars_rank", 0.2))


def shop_score_parts(n_hist=12):
    """User 11-09: 'keep widening the shop gap'. Not in the evidence since 11-13 (race_view replaces it; it also
    appended to .market/shop_parts.jsonl); kept for the read-only tools."""
    try:
        import board_api
        shops = [s for s in board_api.fetch(timeout=30)["shops"] if s.get("deals")]
        n = len(shops)

        def part(s, k):
            v = s.get(k) or 0
            vals = sorted({x.get(k) or 0 for x in shops}, reverse=True)
            i = vals.index(v)
            return {"value": v, "rank": sum((x.get(k) or 0) > v for x in shops) + 1,
                    "pct": round(sum((x.get(k) or 0) < v for x in shops) / (n - 1), 4),
                    "next_up": vals[i - 1] if i else None, "next_down": vals[i + 1] if i + 1 < len(vals) else None}
        top = sorted(shops, key=lambda s: -(s.get("all") or 0))
        me = next(s for s in shops if s["name"] == "yms979")
        rivals = [s for s in top if s["name"] != "yms979"][:3]
        out = {"shops_with_deals": n, "one_rank_is_worth": {k: round(w / (n - 1), 4) for k, w in PARTS},
               "us": {"all": me.get("all"), **{k: part(me, k) for k, _ in PARTS}, "stars_raw": me.get("stars")}}
        for r in rivals:
            parts = {k: part(r, k) for k, _ in PARTS}
            out[r["name"]] = {"all": r.get("all"), **parts, "stars_raw": r.get("stars"),
                              "our_margin_by_part": {k: round(w * (out["us"][k]["pct"] - parts[k]["pct"]), 4)
                                                     for k, w in PARTS}}
        rec = {"t": time.time(), "us": {k: out["us"][k]["rank"] for k, _ in PARTS} | {"all": me.get("all")},
               **{r["name"]: {k: out[r["name"]][k]["rank"] for k, _ in PARTS} | {"all": r.get("all")} for r in rivals}}
        path = HERE / ".market" / "shop_parts.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out["history_ranks"] = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()[-n_hist:]]
        return out
    except Exception as e:
        return {"error": repr(e)}


import re
NAME = "yms979"
REASON_RX = [("price", r"price|under list|% off|of list|expensive|budget|cheap"), ("grade C", r"grade C|refurb"),
             ("grade B", r"grade B|damaged box"), ("warranty", r"warrant"), ("shelf", r"shelf|hours left|expire|fresh"),
             ("broke", r"broke|broken|defect"), ("receipt", r"mismatch|receipt|claim")]


def stars_accounts(limit=15):
    """11-11: who rates our shop below 5 and why (paid results, 24 h, from buyer_value's ledger cache), each
    account's mean at OTHER shops over 48 h, and what they bought from us (claim, x). Sorted by shortfall."""
    try:
        st = json.loads((HERE / ".market" / "shop_value.json").read_text())
    except (OSError, ValueError):
        return {"error": "no shop_value.json"}
    acc = {}
    for row in st.get("our_ratings") or []:
        ts, buyer, stars, review = row[0], row[1], row[2], row[3]
        claim, x = (row[4], row[5]) if len(row) >= 6 else (None, None)
        a = acc.setdefault(buyer, {"n": 0, "sum": 0, "below5": 0, "claims": {}, "xs": [], "reasons": {}, "last": 0})
        a["n"] += 1; a["sum"] += stars; a["below5"] += stars < 5; a["last"] = max(a["last"], ts)
        if claim:
            a["claims"][claim] = a["claims"].get(claim, 0) + 1
        if x:
            a["xs"].append(x)
        if stars < 5:
            for name, rx in REASON_RX:
                if re.search(rx, review, re.I):
                    a["reasons"][name] = a["reasons"].get(name, 0) + 1
    others = {}
    for h, by in (st.get("rating_hours") or {}).items():
        for buyer, (s_o, n_o, s_u, n_u) in by.items():
            o = others.setdefault(buyer, [0, 0])
            o[0] += s_o; o[1] += n_o
    rows = []
    for buyer, a in acc.items():
        mean = a["sum"] / a["n"]
        oth = others.get(buyer)
        rows.append({"account": buyer, "n_24h": a["n"], "mean_for_us": round(mean, 2), "below_5": a["below5"],
                     "mean_at_other_shops_48h": round(oth[0] / oth[1], 2) if oth and oth[1] else None,
                     "claims": a["claims"], "mean_x": round(sum(a["xs"]) / len(a["xs"]), 3) if a["xs"] else None,
                     "reasons": dict(sorted(a["reasons"].items(), key=lambda kv: -kv[1])[:3]),
                     "hours_since_last": round((time.time() - a["last"]) / 3600, 1), "shortfall": round(5 - mean, 2)})
    rows.sort(key=lambda r: -r["shortfall"])
    n_acc = len(rows)
    return {"accounts_24h": n_acc, "mean_of_account_means": round(sum(r["mean_for_us"] for r in rows) / n_acc, 3) if n_acc else None,
            "note": "stars_rank is one mean per account; a 1-star gain on one account moves it by ~1/accounts",
            "worst": rows[:limit]}


def stars_ladder(b):
    """11-11: the student shops' stars_rank ladder around us and the gain needed per rank."""
    try:
        full = fetch_board()                                    # 11-13: one board fetch per run
        names = {(p.get("shop") or {}).get("name") for p in full["players"] if p.get("student")}
        rows = sorted((s for s in full["shops"] if s["name"] in names and s.get("stars_rank") is not None and s.get("deals")),
                      key=lambda s: -s["stars_rank"])
        i = next(k for k, s in enumerate(rows) if s["name"] == NAME)
        return {"our_class_rank": i + 1, "students": len(rows), "one_rank_worth_class_shop": round(0.2 / (len(rows) - 1), 4),
                "ladder": [{"rank": k + 1, "shop": s["name"], "stars_rank": s["stars_rank"], "raw_stars": s.get("stars"), "reviews": s.get("reviews")}
                           for k, s in enumerate(rows[:max(10, i + 3)])]}
    except Exception as e:
        return {"error": repr(e)}


def live_terms():
    """11-11: the ask shares and terms the seller is really using (regime + rep step), next to the knob values."""
    try:
        import policy
        regime, p, sig = policy.shop_params()
        return {"regime": regime, "ask_share_live": p.get("ask_share"), "rep_step": p.get("rep_step"), "rep_avg_last10": sig.get("stars"),
                "warranty_grades": p.get("warranty_grades"), "warranty_hours": p.get("warranty_hours"),
                "floor_mult": p.get("floor_mult"), "slow_hours": p.get("slow_hours")}
    except Exception as e:
        return {"error": repr(e)}


def stock_feasibility(floor_mult=None):
    """11-11: goods units whose floor (cost x floor_mult) sits above the market's median price for the SKU (6 h
    ledger cache, >= 10 settles): they do not sell until slow pricing cuts them. 266 units / 123M at 05:10 UTC."""
    try:
        import statistics
        inv = read(HERE / ".market" / "inventory.json", {})
        cat = read(HERE / ".market" / "catalog.json", {})
        sv = read(HERE / ".market" / "shop_value.json", {})
        if floor_mult is None:
            import policy
            floor_mult = policy.shop_params()[1].get("floor_mult", 1.05)
        px = {}
        for d in sv.get("deals", []):
            if d.get("goods"):
                px.setdefault(d["sku"], []).append(d["x"])
        p50 = {k: statistics.median(v) for k, v in px.items() if len(v) >= 10}
        tot = bad = cost = 0
        by = {}
        for u in inv.get("units", []):
            if u.get("expires_at"):
                continue
            lp, m = (cat.get(u["sku"]) or {}).get("list_price"), p50.get(u["sku"])
            if not lp or not m:
                continue
            tot += 1
            if (u.get("cost") or 0) * floor_mult > m * lp:
                bad += 1; cost += u.get("cost") or 0
                e = by.setdefault(u["sku"], {"units": 0, "cost_M": 0, "grades": {}, "market_p50_x": round(m, 3), "list": lp})
                e["units"] += 1; e["cost_M"] = round(e["cost_M"] + (u.get("cost") or 0) / 1e6, 1)
                e["grades"][u.get("grade") or "?"] = e["grades"].get(u.get("grade") or "?", 0) + 1
        top = sorted(by.items(), key=lambda kv: -kv[1]["cost_M"])[:10]
        return {"goods_units_with_market_price": tot, "units_above_market_p50_at_floor": bad, "their_cost_M": round(cost / 1e6),
                "floor_mult": floor_mult, "top_skus": [dict(v, sku=k, name=str((cat.get(k) or {}).get("name"))[:30]) for k, v in top]}
    except Exception as e:
        return {"error": repr(e)}


def book_race():
    """11-11: the book and revenue race from the board plus guards.py's 6 h slopes and closure ETA."""
    try:
        full = __import__("board_api").fetch(timeout=20)
        shops = sorted((s for s in full["shops"] if s.get("book") is not None and s.get("deals")), key=lambda s: -s["book"])
        me = next(s for s in shops if s["name"] == NAME)
        by_rev = sorted(shops, key=lambda s: -(s.get("revenue") or 0))
        ri = next(k for k, s in enumerate(by_rev) if s["name"] == NAME)
        g = read(HERE / ".market" / "guard_state.json", {})
        hist = g.get("book_hist") or []
        slopes = None
        if len(hist) >= 2 and hist[-1]["t"] - hist[0]["t"] >= 3600:
            dt = (hist[-1]["t"] - hist[0]["t"]) / 3600
            slopes = {"hours": round(dt, 1), "ours_M_per_h": round((hist[-1]["us"] - hist[0]["us"]) / dt / 1e6, 2),
                      "rival": hist[-1]["rival"], "rival_M_per_h": round((hist[-1]["rb"] - hist[0]["rb"]) / dt / 1e6, 2)}
        return {"book": [{"shop": s["name"], "book_M": round(s["book"] / 1e6)} for s in shops[:4]],
                "lead_M": round((me["book"] - shops[1]["book"]) / 1e6) if shops[0]["name"] == NAME else round((me["book"] - shops[0]["book"]) / 1e6),
                "slopes_6h": slopes, "closure_eta_h": g.get("book_eta_h"),
                "revenue": {"our_rank": ri + 1, "ours_B": round(me.get("revenue", 0) / 1e9, 2),
                            "above_B": round(by_rev[ri - 1].get("revenue", 0) / 1e9, 2) if ri else None,
                            "below_B": round(by_rev[ri + 1].get("revenue", 0) / 1e9, 2) if ri + 1 < len(by_rev) else None,
                            "below_name": by_rev[ri + 1]["name"] if ri + 1 < len(by_rev) else None}}
    except Exception as e:
        return {"error": repr(e)}


def _mp():
    import market_picture as mpic
    return mpic


def _views(dry=None):
    """RACE and FUNNEL from market_picture (code, every 15 min, cached 10 min). --dry-prompt reads them in a temp
    copy of their state so nothing under .market is written."""
    dry = DRY if dry is None else dry
    mpic = _mp()
    if not dry:
        return mpic.race_view(), mpic.funnel_view()
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("race.json", "funnel.json", "funnel_state.json"):
            src = Path(mpic.MARKET_DIR) / name
            if src.is_file():
                shutil.copy2(src, Path(tmp) / name)
        return mpic.race_view(max_age=10 ** 9, out_dir=tmp), mpic.funnel_view(max_age=10 ** 9, out_dir=tmp)


SHOP_PARTS = ("book", "revenue", "stars_rank")
BUYER_PARTS = ("saved", "deals", "posts", "forum")


def shop_race(race):
    """race_view() for the shop: parts book, revenue and stars_rank (the runner-up's too), the total, the targets the
    shop owns (owner 'shop' or 'both')."""
    if not isinstance(race, dict):
        return race
    out = {k: v for k, v in race.items() if k not in BUYER_PARTS}
    for key in ("parts", "runner_up_parts"):
        if isinstance(out.get(key), dict):
            out[key] = {k: v for k, v in out[key].items() if k not in BUYER_PARTS}
    if isinstance(out.get("targets"), dict):
        out["targets"] = {k: v for k, v in out["targets"].items()
                          if not (isinstance(v, dict) and v.get("owner") == "buyer")}
    return out


def runner_up_text(race):
    try:
        tot = race.get("total") or {}
        return (f"{race['runner_up']} at {tot['runner_up']:.4f} against our {tot['ours']:.4f}, "
                f"gap {tot['gap']:+.4f}")
    except Exception:
        pass
    try:
        top = board()["class_top"]
        ours = next(x for x in top if x[0] == NAME)
        ru = next(x for x in top if x[0] != NAME)
        return f"{ru[0]} at {ru[1]:.3f} against our {ours[1]:.3f}"
    except Exception as e:
        return f"unknown ({e!r})"


def curator_view():
    """11-13: the curator's digest cut to what the shop acts on: rule changes, answers to its requests and the live
    shop benchmark (<= 3k chars)."""
    try:
        d = json.loads(read_digest("shop"))
    except (TypeError, ValueError):
        return {}
    bench = ((d.get("benchmark_live") or {}).get("shop")) or {}
    if isinstance(bench, dict):
        bench = dict(bench)
        while bench and len(json.dumps(bench, ensure_ascii=False)) > 3000:
            bench.pop(max(bench, key=lambda k: len(json.dumps(bench[k], ensure_ascii=False))))
    return {"rules_change": d.get("rules_change"), "answers_to_your_requests": d.get("answers_to_your_requests"),
            "benchmark_live_shop": bench}


def globals_now(auto=None):
    auto = read(AUTO, {}) if auto is None else auto
    g = auto.get("globals") or {}
    out = {}
    for k in GLOBAL:
        v = g.get(k)
        if v is None:
            v = current_base(k)
        if v is None:
            v = DEFAULTS.get(k)
        out[k] = v
    return out


def evidence(m, mem, sku_rows=TOP_SKUS, star_rows=8):
    now = time.time()
    inv = read(HERE / ".market" / "inventory.json", {})
    rs = read(HERE / "restock_state.json", {})
    auto = read(AUTO, {})
    stock, oldest, stock_cost = {}, {}, {}
    for u in inv.get("units", []):
        stock[u["sku"]] = stock.get(u["sku"], 0) + 1
        stock_cost[u["sku"]] = stock_cost.get(u["sku"], 0) + (u.get("cost") or 0)
        oldest[u["sku"]] = max(oldest.get(u["sku"], 0), round((now - (u.get("supplied_at") or now)) / 3600, 1))
    surge = {x["sku"]: x.get("factor") for x in inv.get("surge") or [] if x.get("sku")}
    mkt = rs.get("sku", {})
    rows = []
    for sku, s in m["skus"].items():
        w6, w24 = pm.window(m, sku, 6), pm.window(m, sku, 24)
        # 11-13: ranked by 24 h revenue plus the stock held at cost (it was stock x list price)
        weight = stock_cost.get(sku, 0) + w24["revenue"]
        if not weight and sku not in mkt:
            continue
        rows.append((weight, {
            "sku": sku, "name": str(s.get("name"))[:40], "kind": s.get("kind"), "list": s.get("list_price"),
            "category": str(s.get("category") or "").split(">")[-1],
            "stock": stock.get(sku, 0), "oldest_h": oldest.get(sku), "sold_6h": w6["sold"], "sold_24h": w24["sold"],
            "margin_24h": w24["margin"], "margin_per_unit": round(w24["margin"] / w24["sold"]) if w24["sold"] else None,
            "avg_hold_h": round(w24["hold_hours"] / w24["sold"], 1) if w24["sold"] else None,
            "walkaways_6h": w6["cancels"] + w6["lost"], "sold_out_asks_6h": w6["no_stock"],
            "stars": round(w24["stars_sum"] / w24["stars_n"], 2) if w24["stars_n"] else None,
            "broken_24h": w24["broken"],
            "market_sales_h": round(mkt.get(sku, 0) * 0.1155, 1),          # decayed count x ln2 / 6 h
            "market_units": (rs.get("mkt_units") or {}).get(sku, 0), "market_shops": (rs.get("mkt_shops") or {}).get(sku, 0),
            "cost_surge": surge.get(sku), "knobs_now": (auto.get("sku") or {}).get(sku),
        }))
    quotes = {}                                       # what our buyer was quoted by other shops, last 6 h
    try:
        for line in open(HERE / "market_intel.jsonl", encoding="utf-8").readlines()[-20000:]:
            if '"first_ask"' in line:
                try:
                    q = json.loads(line)
                except ValueError:                 # 11-13 review: a line mid-write
                    continue
                if q.get("t", 0) >= now - 6 * 3600 and q.get("list_price"):
                    quotes.setdefault(q["sku"], []).append((q["first_ask"] / q["list_price"],
                                                            (q.get("min_ask") or q["first_ask"]) / q["list_price"]))
    except OSError:
        pass
    for _, r in rows:
        v = sorted(quotes.get(r["sku"], []))
        if v:
            r["competitor_quotes_6h"] = {"n": len(v), "first_ask_over_list_median": round(v[len(v) // 2][0], 2),
                                         "lowest_ask_over_list": round(min(x[1] for x in v), 2)}
    w = mem.d["working"]
    H = hours_left(now)
    sa = stars_accounts(limit=star_rows)
    gnow = globals_now(auto)
    return {"time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)), "hours_left": round(H, 2),
            "phase": phase_of(H),
            "cash": inv.get("balance"), "stock_cost": sum(u.get("cost") or 0 for u in inv.get("units", [])),
            "skus": [r for _, r in sorted(rows, key=lambda x: -x[0])[:sku_rows]],
            "globals_now": {k: {"now": gnow[k], "bounds": list(GLOBAL[k][1])} for k in GLOBAL},
            "stars_accounts": sa, "stars_ladder": stars_ladder(None),
            "settle_health": settle_health(),
            "from_buyer_strategist": read_board("shop"),
            "from_operator": operator_note(HERE / "operator_to_shop.json", HERE / "shop_answer_to_operator.json"),
            "woken_by": w.get("woken_by"),
            "curator": curator_view()}


def current_base(k):
    """The base value of a shop setting the strategist has not set (params.json shop.base)."""
    base = read(HERE / "params.json", {}).get("shop", {}).get("base", {})
    if k.startswith("ask_share_"):
        return (base.get("ask_share") or {}).get(k[-1])
    if k in ("warranty_b", "warranty_c"):
        return int(k[-1].upper() in (base.get("warranty_grades") or []))
    return base.get(k)


def settle_health(hours=2):
    """Our seller's closed deals against the market's settles and refusals in the last `hours`, from the kit log
    (11-08: 129 warranted receipts in a row were refused at settle and nobody saw it for ~10 h). The kit log has
    clock times only, so lines are dated walking back from the end (a jump forward in time = the previous day)."""
    import re
    from collections import Counter
    cut = time.time() - hours * 3600
    def _t_of(line):                                # 11-13 review: the seller may be mid-write on the last line
        try:
            return json.loads(line).get("t", 0)
        except ValueError:
            return 0
    deals = sum(1 for line in open(HERE / "seller_r3_log.jsonl", encoding="utf-8")
                if '"deal"' in line and _t_of(line) >= cut)
    lines = []
    try:                                            # the log rotates (~every 6-12 h): read the last archive too
        import gzip
        arch = sorted((HERE / "logs" / "archive").glob("market-kit-*.log.gz"))
        if arch:
            lines = gzip.open(arch[-1], "rt", encoding="utf-8", errors="ignore").read().splitlines()
    except OSError:
        pass
    try:
        lines += (HERE / "logs" / "market-kit.log").read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        pass
    day0 = time.time() - time.time() % 86400
    settled, refused, why, last_restart = 0, 0, Counter(), None
    prev, day = None, day0
    for line in reversed(lines):
        m = re.match(r"(\d\d):(\d\d):(\d\d) ", line)
        if not m:
            continue
        sec = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if prev is not None and sec > prev + 3600:       # walked back past midnight
            day -= 86400
        prev = sec
        t = day + sec
        if t > time.time() + 60:                          # the last lines can belong to 'yesterday' right after 00:00
            day -= 86400; t -= 86400
        if t < cut:
            break
        if "[kit] market_kit" in line and last_restart is None:
            last_restart = time.strftime("%H:%M UTC", time.gmtime(t))
        if "[acp] settled" in line:
            settled += 1
        elif "[acp] settle refused" in line or "[acp] checkout refused" in line:
            refused += 1
            why[re.sub(r"u-[0-9a-f]+", "u-…", line.split("refused", 1)[1].strip())[:80]] += 1
    return {"window_h": hours, "seller_deals": deals, "market_settled": settled, "refused": refused,
            "refusal_reasons": dict(why.most_common(5)), "last_kit_restart": last_restart,
            "note": "the 8760 h warranty was refused at settle until 11-08 01:03 UTC (72 h since); refusals from "
                    "before that are history, not a live fault"}


DECIDE = """You run the SHOP of yms979 (student 23530007) in a class market. Round 3 ends 2026-11-17 14:59 UTC; {hours_left:.1f} h are left (phase: {phase}).

OBJECTIVE
Maximise yms979's class TOTAL at 14:59 UTC 11-17, and our margin over the best other student (now {runner_up}). The class total is a percentile score among the 46 students. A rank we keep against the runner-up counts twice: we keep it and he does not gain it.

SCORE MODEL (exact)
- shop = .5 pct(book) + .3 pct(revenue) + .2 pct(stars_rank), each among the 46 students, with ties counted half. One rank is worth: book .0111, revenue .0067, stars .0044.
- book = cash + unsold stock at cost - starting capital.
  - A goods sale changes book by price - cost - the refund if it breaks inside its warranty.
  - Storage costs 1%/day of stock cost.
  - Expired food is lost at cost.
  - A purchase is book-neutral until it pays storage.
- revenue = paid sales - refunds. A deal event is not a sale; only a settled (paid) deal counts.
- stars_rank = (sum over reviewing OWNER accounts of their mean stars on us + 15) / (owners + 5). Only reviews of paid deals count. One new owner rating 5.0 adds about +.0087.
- A buyer gains saved v = (list - price) / (list - cost) per deal. A unit sold cheap raises a rival buyer's saved, and the runner-up's buyer is within .0001 of a saved rank (worth .0111 to him). Price at the market reference; never feed.
- Measured facts:
  - Goods break only within 6 h of the sale: 16% within 1 h, 33% within 2 h, 51% within 3 h.
  - Reviews are written before breaks.
  - Break rates: A ~3%, B 6-10% (B refunds are 13% of B revenue), C ~40%.
  - Fresh A costs about .654 of list against a ~.699 market; old A about .707 against ~.692.
  - Our A margin is about 1.6% of price on true costs.
  - C goods sell at about .55-.60 of list to buyers that accept C.

RACE AND TARGETS (code, every 15 min)
{race}
Each target says ON (on track) or OFF, with the number that decides it.

FUNNEL AND BOOK (last 1 h, 3 h and 12 h; paid = settled)
{funnel}

WHAT YOU DECIDE
Every knob below, inside its bounds.
- Switches and caps may go anywhere in bounds in one run.
- Price knobs move at most {price_step} per run.
- Per-SKU 'ask' moves at most .05 (bounds .85-1.05). 'cover' may go anywhere in 0-2.
- At most {max_moves} moves a run.
{knobs}
RAILS (code enforces them on every write; do not propose around them):
- No goods unit is ever sold under cost x the refund break-even of its grade and warranty (A 1.025 and B 1.15 with a 6 h or longer warranty, less with a shorter one; C 1.0).
- floor_mult >= 1.025.
- slow_floor_mult >= .99, and >= 1.0 inside the last 24 h.
- rev_defense and c_fallback stay 0.
- rival_under stays 0 unless targets.rev_push_ok is true.
- The same terms for every buyer; the frozen star list is not yours.
- Grade, warranty and certificate claims stay true.
- A knob moved 3 times in 3 h is frozen for 2 h.

WHEN TO MOVE
- Find the OFF target with the most points at stake. A part next to the runner-up counts twice.
- Name its cause in the FUNNEL or BOOK numbers, then move the knob that reaches that cause.
- Hold only when every target is ON, or while a move on the same metric is still inside its window (MOVE LEDGER). 'Hard to score' is never a reason to hold.
- Order: book 1st, then rival feed under 15%, then revenue at or above the break-even, then stars. The exception is a part whose P(flip) in race.json is under .05 either way: it is decided, so spend nothing on it.
- When the projected book lead is under 0 on both fits, no price knob may go down that run.
- A guard wake (woken_by) needs a move, or the number that shows its cause is gone.
- Warranty: warranty_default_h is the warranty for asks that do not mention a warranty. Asks that mention one get warranty_hours. If short-warranty closes are refused at checkout (settle_health) in more than 10% of cases over 2 h, or the new-review mean falls under 4.3 over 20 or more reviews in 3 h, set warranty_default_h back to 72.
- Grade C: if new reviews on C goods average under 4.3 over 20 or more reviews, set sell_c 0.
- Food: if targets.food_expiry is OFF, set restock_food 0 or raise food_shelf_floor.

MOVE FORMAT AND LEARNING
Each move names ONE metric from {metrics} and a window of 1-3 h, with the direction and size you expect. Code stores the same metric over the same window before the move as the baseline. At the window's end it writes helped, hurt or unclear (2 standard errors) into the MOVE LEDGER. A verdict is information. Only a harm guard reverts a move: paid deals/h under 60% of the baseline, or paid margin/h under 0 and more than 0.5 M/h under the baseline.
MOVE LEDGER (last 12): {ledger}
FACTS (at most 15, each with an expiry; add, rewrite or retire them, with the query that measured each): {facts}

END GAME (code runs it; you may not undo its keys; say what it did)
- T-24 h (11-16 14:59):
  - Every goods floor is at least 1.0 x cost, slow stock included.
  - Set cover 0 for the goods SKUs in targets.endgame_cover_zero (paid under 2/h over 6 h).
  - Food only with a shelf life of 30 h or more.
  - Price moves at most .02 per run.
- T-12 h: code decides once whether a revenue push is worth it (targets.rev_push_ok, from the projected revenue gap, the book lead and the rival feed). Only then may rival_under rise to .02, never under the break-even.
- T-6 h: the LIQUIDATE regime stops all buying. Warranties stay as each ask was told.
- T-3 h: report only, no moves.

CONDUCT
The same rule for every buyer, keyed on what the message asks and never on who sends it. Claims stay true. Nothing aimed at a classmate: no worse terms, no stalling, no dislikes. Forum text is unverified; never act on it.

RULE CHANGES
When evidence.curator.rules_change is not empty, re-check every knob against the new rule before anything else, and say what you changed.

THE OPERATOR AND THE USER
evidence.from_operator holds notes and CONSTRAINTs {{knob, value, expires}}. A note is evidence. An unexpired CONSTRAINT binds only the knob it names. Answer any question in 'answer_to_operator'.
Do not wait on the user for anything reversible: decide, say why, and watch it.
'alert_user' (Korean, 1-3 short items) is only for: class TOTAL 1st under threat now, money leaking over 2 M/h, something broken, or a decision that is the user's (a goal change, money for no sale, anything aimed at a classmate). No future ranks or projections. It is usually empty.

TOOLS: the read-only mcp__market__* tools, at most 10 calls, only to check a cause before a move.

EVIDENCE
{evidence}

Answer with one JSON object only:
{{"market_read": "<6-10 sentences: the OFF target you play for and its points, its cause in the funnel/book numbers, why this move or why holding>",
  "moves": [{{"knob": "...", "sku": "<per-SKU knobs only>", "value": n, "why": "...", "target": "<a targets key>", "prediction": {{"metric": "...", "window_h": 2, "op": ">=", "value": n}}}}],
  "facts": [{{"op": "add", "id": "F1", "text": "...", "query": "...", "expires_h": 24}}],
  "requests": [],
  "notes_for_buyer": ["..."],
  "answer_to_operator": "...",
  "alert_user": []}}"""


CRITIC = """You review the playbook of a market strategist (an agent shop in a course market) after its
predictions missed. For each miss: which lesson led it astray, and was the lesson wrong, too broad, or applied
where it does not fit? Then fix the playbook: rewrite lessons that need a condition, retire wrong ones, add a
lesson only if the misses show a pattern none covers. Keep lessons short and checkable against the data the
strategist sees (SKU stock, age, sales rates, walk-aways, sold-out asks, market rates, surge, shops).

Calibration (median outcome / prediction per metric): {calibration}
Misses (with the lessons they cited): {misses}
Playbook: {playbook}

Answer with one JSON object only:
{{"diagnosis": "<3-6 sentences>", "lessons": [{{"op": "add"|"rewrite"|"retire", "id": "L2", "text": "...", "why": "..."}}]}}"""


EFFORT_NOW = None


def model_cmd(role):
    """11-11 (user: build the tools and use them): a strategist may get the read-only market tools (market_tools.py,
    MCP stdio) when params.json strategist.tools lists its role; the answer is still one JSON object."""
    cmd = shlex.split(MODEL_CMD)
    if EFFORT_NOW and "--effort" in cmd:                # 11-11: run_gate picks medium on routine runs
        cmd[cmd.index("--effort") + 1] = EFFORT_NOW
    roles = (read(PARAMS, {}).get("strategist") or {}).get("tools") or []
    if role in roles:
        try:
            import market_tools
            cfg = market_tools.write_config()
            cmd += ["--mcp-config", str(cfg), "--strict-mcp-config", "--allowedTools", "mcp__market"]
        except Exception as e:
            log(f"{role}: tools unavailable ({e!r}); running without")
    return cmd


def ask_model(prompt, role):
    with tempfile.TemporaryDirectory() as empty:
        t = time.time()
        try:
            p = model_env.run(model_cmd(role), prompt, empty, MODEL_TIMEOUT, role=role)   # API key, else login
            sp = getattr(p, "spend", None) or {}
            log(f"{role}: model answered in {time.time() - t:.0f} s ({len(prompt)} chars in"
                + (f", ${sp['cost']:.3f}, out {sp['output']} + think {sp['thinking']} tok" if sp else "") + ")")
            if getattr(p, "error_text", None):
                log(f"{role}: model error: {p.error_text}")
            return p.stdout.strip()
        except (subprocess.TimeoutExpired, OSError) as e:
            log(f"{role}: model call failed: {e!r}")
            return ""


def reflect(mem):
    rec = mem.record()
    lessons = [{k: x[k] for k in ("id", "text", "for", "against", "used")} for x in mem.lessons()]
    raw = ask_model(CRITIC.format(calibration=json.dumps(rec["calibration"]),
                                  misses=json.dumps(rec["recent_misses"], ensure_ascii=False),
                                  playbook=json.dumps(lessons, ensure_ascii=False)), "critic")
    ans = last_json_object(raw) or {}
    if ans:
        log(f"critic: {str(ans.get('diagnosis'))[:700]}")
        mem.edit_lessons(ans.get("lessons"), log)
    mem.d["unreflected"] = 0


def funnel_between(t0, t1):
    """Our shop's per-hour rates in [t0, t1] (market_picture.funnel_between); {'error'} on failure."""
    try:
        return _mp().funnel_between(t0, t1)
    except Exception as e:
        return {"error": f"funnel_between: {e!r}"}


LEDGER_SLACK = 120           # s: a window counts as covered when the archive reaches within this of its end


def ledger_upto():
    """11-13 review: the newest ledger row in data/market_archive.db (ops/archive_db.py fills it every 15 min, in
    the same cron tick as this script), or None when it cannot be read. paid_* and revenue_h come from that archive:
    a window measured before the archive reaches its end reads low (a 1 h window 15 min short reads 25% low, which
    alone can fire the paid-deals harm guard)."""
    try:
        from contextlib import closing
        with closing(_mp()._archive()) as c:
            r = c.execute("SELECT max(ts) FROM ledger WHERE ts > ?", (time.time() - 6 * 3600,)).fetchone()
        return float(r[0]) if r and r[0] is not None else None
    except Exception:
        return None


def metric_val(fb, metric, w):
    """(value, raw n) of a metric in a funnel_between result; n = settled deals for paid_deals_h, reviews for
    stars_new_mean, rate x hours for the other counts, settled deals for the money metrics."""
    if not isinstance(fb, dict) or fb.get("error"):
        return None, None
    v = fb.get(metric)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None, None
    hours = fb.get("hours") or w
    if metric == "stars_new_mean":
        n = fb.get("n_ratings")
    elif metric in ("no_stock_h", "canceled_h"):
        n = v * hours
    else:
        n = fb.get("n_paid")
        if n is None and metric == "paid_deals_h":
            n = v * hours
    return float(v), n


def judge(metric, op, base, got, n_base, n_got, w):
    """11-13 (diagnosis ARCH-4): helped / hurt / unclear at 2 standard errors of the change got - base."""
    if base is None or got is None:
        return "unclear", None
    if metric in COUNT_METRICS:
        se = math.sqrt(max(n_got or 0, 0) + max(n_base or 0, 0)) / max(w, 1e-9)
    elif metric == "stars_new_mean":
        se = 0.8 / math.sqrt(max(n_got or 0, 1))
    else:
        se = 0.25 * max(abs(base), abs(got)) + 0.2e6
    d = (got - base) * (1 if op == ">=" else -1)
    return ("helped" if d > 2 * se else "hurt" if d < -2 * se else "unclear"), se


def harm_of(e, fb, w):
    """The harm guard: paid deals/h under 60% of a baseline of >= 10/h (n >= 30), or paid margin/h under 0 and
    more than 0.5 M/h under the baseline. None when neither holds."""
    hb = e.get("harm_base") or {}
    bd, bn, bm = hb.get("paid_deals_h"), hb.get("n_paid"), hb.get("paid_margin_h")
    gd, _ = metric_val(fb, "paid_deals_h", w)
    gm, _ = metric_val(fb, "paid_margin_h", w)
    if bd is not None and gd is not None and bd >= 10 and (bn if bn is not None else bd * w) >= 30 and gd < 0.6 * bd:
        return f"paid deals/h {gd:.1f} under .6 x the baseline {bd:.1f}"
    if gm is not None and bm is not None and gm < 0 and gm < bm - 0.5e6:
        return f"paid margin/h {gm / 1e6:+.2f} M, under 0 and 0.5 M/h under the baseline {bm / 1e6:+.2f} M"
    return None


def valid_prediction(pred):
    """{metric in SHOP_METRICS, window_h 1-3, op, value} or None."""
    if not isinstance(pred, dict) or pred.get("metric") not in SHOP_METRICS or pred.get("op") not in (">=", "<="):
        return None
    try:
        value = float(pred["value"])
        w = float(pred.get("window_h", 2))
    except (KeyError, TypeError, ValueError):
        return None
    return {"metric": pred["metric"], "window_h": min(3.0, max(1.0, w)), "op": pred["op"], "value": value}


def new_episode(knob, sku, value, before, pred, applied, why="", target="", t=None, cache=None):
    """A move with its baseline: the predicted metric (and the harm guard's paid deals and margin) over the same
    window before the move. A GLOBAL move is measured shop-wide, with no SKU. 11-13 review: the baseline window
    ends where the ledger archive ends (at most at t), so the archive's lag does not read as a low baseline."""
    t = time.time() if t is None else t
    w = pred["window_h"]
    cache = {} if cache is None else cache
    if "t_end" not in cache:
        up = ledger_upto()
        cache["t_end"] = min(t, up) if up is not None and up > t - 3600 else t
    t_end = cache["t_end"]
    if w not in cache:
        cache[w] = funnel_between(t_end - w * 3600, t_end)
    fb = cache[w]
    base, n_base = metric_val(fb, pred["metric"], w)
    bd, bn = metric_val(fb, "paid_deals_h", w)
    bm, _ = metric_val(fb, "paid_margin_h", w)
    ep = {"t": t, "due": t + w * 3600, "knob": knob, "sku": sku, "value": value, "before": before,
          "why": str(why)[:400], "target": str(target or "")[:40], "uses": [], "prediction": pred, "applied": applied,
          "baseline": base, "n_base": n_base, "harm_base": {"paid_deals_h": bd, "n_paid": bn, "paid_margin_h": bm},
          "baseline_window": [round(t_end - w * 3600), round(t_end)]}
    if isinstance(fb, dict) and fb.get("error"):
        ep["baseline_error"] = str(fb["error"])[:200]
    return ep


def _auto_value(auto, knob, sku):
    if knob in KNOBS:
        return ((auto.get("sku") or {}).get(sku) or {}).get(knob)
    return (auto.get("globals") or {}).get(knob)


def learn(mem, mode="apply", now=None):
    """11-13 (diagnosis ARCH-4; replaces the hit/miss scoring, the auto-revert on any miss and the adaptive step
    halving that had shrunk the ask step to .00625): every due move gets {got, baseline, verdict}; a run's applied
    moves are reverted together only when the harm guard fires. Returns the reverts (sku changes, global changes)
    for write_auto."""
    now = time.time() if now is None else now
    auto = read(AUTO, {})
    sku_ch, glob_ch, hurt = {}, {}, 0
    upto = None
    for e in mem.due(now):
        p = e.get("prediction") or {}
        metric = p.get("metric")
        if metric not in SHOP_METRICS or "window_h" not in p:
            e["outcome"] = {"skipped": "pre-11-13 format", "hit": False, "got": None, "t": now}
            log(f"ledger: {e.get('knob')} {e.get('sku') or ''} {metric}: skipped (pre-11-13 format)")
            continue
        w = float(p["window_h"])
        end = e["t"] + w * 3600
        if upto is None:
            upto = ledger_upto() or 0.0
        covered = upto <= 0 or upto >= end - LEDGER_SLACK     # unreadable archive: measure as before
        if not covered and now < e["due"] + 3 * 3600:
            log(f"ledger: {e['knob']} {e.get('sku') or ''} {metric}: the archive ends {(end - upto) / 60:.0f} min "
                f"before the window does; retry")
            continue
        fb = funnel_between(e["t"], end)
        got, n_got = metric_val(fb, metric, w)
        if got is None and now < e["due"] + 3 * 3600:
            log(f"ledger: {e['knob']} {e.get('sku') or ''} {metric}: no measure yet ({str((fb or {}).get('error'))[:120]}); retry")
            continue
        verdict, se = judge(metric, p.get("op"), e.get("baseline"), got, e.get("n_base"), n_got, w)
        mem.score(e, got, verdict == "helped")
        e["outcome"].update(baseline=e.get("baseline"), verdict=verdict, n_got=n_got,
                            se=round(se, 4) if se is not None else None)
        hurt += verdict == "hurt"
        log(f"ledger: {e['knob']} {e.get('sku') or ''} {e.get('before')} -> {e.get('value')} | {metric} over {w:g} h: "
            f"baseline {e.get('baseline')} -> got {got} (expected {p.get('op')} {p.get('value')}; se {se if se is None else round(se, 3)}) -> {verdict}")
        if not covered:
            log(f"ledger: {e['knob']} {e.get('sku') or ''}: the archive never covered the window (3 h after due); "
                f"no harm check")
            continue
        harm = harm_of(e, fb, w)
        if not harm:
            continue
        for x in mem.d["episodes"]:                  # the whole run batch of this move
            if x.get("t") != e["t"] or not x.get("applied") or x.get("reverted"):
                continue
            cur = _auto_value(auto, x["knob"], x.get("sku"))
            try:
                holds = cur is not None and abs(float(cur) - float(x["value"])) <= 1e-9
            except (TypeError, ValueError):
                holds = False
            if not holds:
                log(f"harm revert skipped {x['knob']} {x.get('sku') or ''}: moved since to {cur} (the move set {x['value']})")
                continue
            if mode != "apply":
                log(f"harm revert (propose mode, not written) {x['knob']} {x.get('sku') or ''} -> {x.get('before')}: {harm}")
                continue
            if x["knob"] in KNOBS:
                sku_ch.setdefault(x["sku"], {})[x["knob"]] = x.get("before")
            else:
                glob_ch[x["knob"]] = x.get("before")
            x["reverted"] = {"t": now, "why": harm}
            log(f"harm revert {x['knob']} {x.get('sku') or ''} {x['value']} -> {x.get('before')}: {harm}")
    mem.maybe_rollback(log)
    mem.d["unreflected"] = mem.d.get("unreflected", 0) + hurt
    mem.fold()
    return sku_ch, glob_ch


def ledger_view(mem, n=12, now=None):
    """The MOVE LEDGER: the last n moves in the 11-13 format with baseline, outcome and verdict."""
    now = time.time() if now is None else now
    rows = []
    eps = [x for x in mem.d["episodes"] if (x.get("prediction") or {}).get("metric") in SHOP_METRICS
           and "window_h" in (x.get("prediction") or {})]
    for e in eps[-n:]:
        p, o = e["prediction"], e.get("outcome") or {}
        r = {"at": time.strftime("%m-%d %H:%M", time.gmtime(e["t"])), "knob": e["knob"]}
        if e.get("sku"):
            r["sku"] = e["sku"]
        r.update({"from": e.get("before"), "to": e.get("value"), "applied": e.get("applied"), "target": e.get("target"),
                  "metric": p["metric"], "window_h": p["window_h"], "expected": f"{p['op']} {p['value']:g}",
                  "baseline": e.get("baseline")})
        if o:
            r.update(got=o.get("got"), verdict=o.get("verdict"))
        else:
            r["due_in_h"] = round((e["due"] - now) / 3600, 1)
        if e.get("reverted"):
            r["harm_reverted"] = e["reverted"].get("why")
        rows.append(r)
    return rows or "(empty: no move in the 11-13 format yet)"


def edit_facts(mem, edits, now=None):
    """11-13 (diagnosis ARCH-4: 16 of 20 lessons were 70-196 h old; the store evicted every new lesson in 1-5 runs):
    FACTS, at most MAX_FACTS, each with the query that measured it and an expiry (1-96 h, default 24)."""
    import re as _re
    now = time.time() if now is None else now
    facts = [f for f in (mem.d.get("facts") or []) if f.get("expires", 0) > now]
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        op, fid = e.get("op"), str(e.get("id") or "")
        cur = next((f for f in facts if f["id"] == fid), None)
        try:
            exp_h = min(96.0, max(1.0, float(e.get("expires_h", 24))))
        except (TypeError, ValueError):
            exp_h = 24.0
        if op == "add" and e.get("text"):
            if cur is not None or not _re.fullmatch(r"F\d+", fid):
                n = max([int(f["id"][1:]) for f in facts if _re.fullmatch(r"F\d+", f["id"])] + [0])
                fid = f"F{n + 1}"
            f = {"id": fid, "text": str(e["text"])[:300], "query": str(e.get("query") or "")[:300], "added": now,
                 "expires": now + exp_h * 3600}
            facts.append(f)
            log(f"fact {fid} added ({exp_h:g} h): {f['text']}")
        elif op == "rewrite" and cur is not None and e.get("text"):
            log(f"fact {fid} rewritten: {cur['text']} -> {str(e['text'])[:300]}")
            cur.update(text=str(e["text"])[:300], expires=now + exp_h * 3600)
            if e.get("query"):
                cur["query"] = str(e["query"])[:300]
        elif op == "retire" and cur is not None:
            facts.remove(cur)
            log(f"fact {fid} retired: {cur['text']}")
    while len(facts) > MAX_FACTS:
        f = min(facts, key=lambda x: x.get("expires", 0))
        facts.remove(f)
        log(f"fact {f['id']} dropped (over {MAX_FACTS}, nearest expiry): {f['text']}")
    mem.d["facts"] = facts
    return facts


def facts_view(mem, now=None):
    now = time.time() if now is None else now
    out = [{"id": f["id"], "text": f["text"], "query": f.get("query"),
            "expires_in_h": round((f["expires"] - now) / 3600, 1)}
           for f in (mem.d.get("facts") or []) if f.get("expires", 0) > now]
    return out or "(none yet: add what you measured)"


def step_for(knob, H):
    if knob in KNOBS:
        return KNOBS[knob][1]
    if knob == "rival_under":
        return 0.01
    if knob in PRICE_KNOBS:
        return price_step(H)
    lo, hi = GLOBAL[knob][1]
    return hi - lo


def clamp(cur, value, step, lo, hi):
    new = min(hi, max(lo, cur - step, min(cur + step, value)))
    if isinstance(lo, int) and isinstance(hi, int):
        return int(round(new))
    return round(new, 4)


def knob_lines(gnow):
    lines = [f"- {k} ({lo}-{hi}) now={gnow.get(k)}" + (" [price]" if k in PRICE_KNOBS else "")
             for k, (_, (lo, hi), _s) in GLOBAL.items()]
    lines.append("- per SKU (give 'sku'): ask (.85-1.05) x the seller's ask for that SKU; cover (0-2) x the stock "
                 "restock holds of it, 0 stops buying it")
    return "\n".join(lines)


def _js(x):
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))   # 11-13: compact JSON, ~10% of the prompt


def _f(x):
    return format(x, ".10g") if isinstance(x, (int, float)) else str(x)


def build_prompt(m, mem):
    """The DECIDE prompt (<= MAX_PROMPT chars: the SKU table is cut to 15 rows, then stars_accounts to 5)."""
    race, funnel = _views()
    H = hours_left()
    targets = load_targets(race)
    ph = (targets.get("phase") or {}).get("value") if isinstance(targets.get("phase"), dict) else None
    ev = evidence(m, mem)
    gnow = globals_now()
    fixed = dict(hours_left=H, phase=ph or phase_of(H), runner_up=runner_up_text(race),
                 race=_js(shop_race(race)), funnel=_js(funnel), ledger=_js(ledger_view(mem)), facts=_js(facts_view(mem)),
                 price_step=".02" if H <= 24 else ".05", max_moves=MAX_MOVES, knobs=knob_lines(gnow),
                 metrics=", ".join(SHOP_METRICS))

    def fmt():
        return DECIDE.format(evidence=_js(ev), **fixed)
    prompt = fmt()
    if len(prompt) > MAX_PROMPT:
        ev["skus"] = ev["skus"][:15]
        prompt = fmt()
    if len(prompt) > MAX_PROMPT and isinstance(ev.get("stars_accounts"), dict):
        ev["stars_accounts"]["worst"] = (ev["stars_accounts"].get("worst") or [])[:5]
        prompt = fmt()
    if len(prompt) > MAX_PROMPT:
        log(f"prompt still {len(prompt)} chars after the cuts (limit {MAX_PROMPT})")
    else:
        log(f"prompt {len(prompt)} chars (sku rows {len(ev['skus'])})")
    return prompt, ev, targets, race


def run(force=False):
    global EFFORT_NOW
    mem = Memory()
    wake = read(probe.WAKE, None)
    slot = my_turn("shop")
    if wake and not force and time.time() - mem.d.get("t", 0) < 3600:
        wake = None                     # 11-10 (API budget): a watch condition wakes it at most once an hour
    if not force and not wake and (slot is None or slot == mem.d.get("slot")):
        return
    mem.d["slot"] = slot
    if wake:
        probe.WAKE.unlink(missing_ok=True)
        log(f"woken early by watch condition: {json.dumps(wake, ensure_ascii=False)}")
    gstate = read(HERE / ".market" / "guard_state.json", {})         # 11-11: guards.py
    if not force and not wake and gstate.get("budget_block_until", 0) > time.time():
        log("slot skipped: G10 budget block (guard wakes still run)")
        return
    mem.d["t"] = time.time()
    mem.d["working"]["woken_by"] = wake
    mem.d["working"]["watch"] = []      # 11-13: the model's watch list is gone; guards.py wakes the shop
    # 11-13: the mode is what params.json says (guard() and its suspension never fired: all 72 health entries 'apply')
    mode = read(PARAMS, {}).get("strategist", {}).get("mode", "propose")
    if BOUNDS_OFFENDERS:
        log(f"bounds: params_auto.json globals outside GLOBAL bounds at import: {BOUNDS_OFFENDERS}")
    m = pm.update()
    H = hours_left()
    targets = load_targets()
    sku_rv, glob_rv = learn(mem, mode)
    if mode == "apply" and (sku_rv or glob_rv):
        write_auto(sku_rv, glob_rv, H, targets)
    if mem.d.get("unreflected", 0) >= REFLECT_MISSES:
        reflect(mem)
    go, EFFORT_NOW, why = run_gate("shop", mem.d, woken=bool(wake) or force)
    if not go:
        log(f"model skipped (cost gate): {why}")
        if mode == "apply":                  # the rails hold on this write too
            write_auto({}, {}, H, targets)
        mem.save()
        return
    prompt, ev, targets, race = build_prompt(m, mem)
    log(f"model run (effort {EFFORT_NOW}): {why}; prompt {len(prompt)} chars")
    raw = ask_model(prompt, "strategist")
    ans = last_json_object(raw) or {}
    (HERE / "strategist_last.json").write_text(json.dumps({"t": mem.d["t"], "mode": mode, "answer": ans,
                                                           "raw": raw[-4000:]}, ensure_ascii=False, indent=1),
                                               encoding="utf-8")
    if not ans:
        log(f"no usable answer from the model ({len(raw)} chars); nothing changed")
        if mode == "apply":
            write_auto({}, {}, H, targets)
        mem.save()
        return
    log(f"[{mode}] market read: {str(ans.get('market_read'))[:1200]}")
    if ans.get("answer_to_operator") and read(HERE / "operator_to_shop.json", {}).get("note"):
        (HERE / "shop_answer_to_operator.json").write_text(json.dumps({"t": time.time(), "answer": ans["answer_to_operator"]},
                                                                      ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"answered the operator: {str(ans['answer_to_operator'])[:600]}")
    sent = model_env.alert_user("shop", ans.get("alert_user"))
    if sent:
        log(f"alerted the user: {sent}")
    requests = valid_requests(ans.get("requests"))[:3]
    post_board("shop", ans.get("market_read"), {"moves": [(x.get("knob"), x.get("sku"), x.get("value"))
                                                          for x in (ans.get("moves") or []) if isinstance(x, dict)][:12]},
               ans.get("notes_for_buyer"), requests)
    for q in requests:
        log(f"asked the curator: {json.dumps(q, ensure_ascii=False)[:160]}")
    edit_facts(mem, ans.get("facts"))
    H = hours_left()
    moves = [x for x in (ans.get("moves") or []) if isinstance(x, dict)]
    if H <= 3 and moves:
        log(f"T-3 h ({H:.1f} h left): report only; {len(moves)} moves dropped")
        moves = []
    auto = read(AUTO, {})
    gnow = globals_now(auto)
    t_move = time.time()
    recent = {}                                   # 11-13: churn guard, applied moves per knob (knob|sku) in 3 h
    for e in mem.d["episodes"]:
        if e.get("applied") and e.get("t", 0) >= t_move - 3 * 3600:
            key = f"{e['knob']}|{e['sku']}" if e["knob"] in KNOBS else e["knob"]
            recent[key] = recent.get(key, 0) + 1
    inv_skus = {u.get("sku") for u in read(HERE / ".market" / "inventory.json", {}).get("units", [])}
    ok_skus = set(m.get("skus", {})) | inv_skus | {r["sku"] for r in ev["skus"]}
    sku_ch, glob_ch, seen, cache, n = {}, {}, set(), {}, 0
    for mv in moves:
        knob = mv.get("knob")
        if not isinstance(knob, str) or (mv.get("sku") is not None and not isinstance(mv.get("sku"), str)):
            log(f"dropped a move with a malformed knob or sku: {json.dumps(mv, ensure_ascii=False)[:160]}")
            continue
        if n >= MAX_MOVES:
            log(f"dropped {knob} {mv.get('sku') or ''}: over {MAX_MOVES} moves")
            continue
        try:
            value = float(mv.get("value"))
            if not math.isfinite(value):
                raise ValueError("not finite")
        except (TypeError, ValueError):
            log(f"dropped {knob} {mv.get('sku') or ''}: no numeric value ({mv.get('value')!r})")
            continue
        pred = valid_prediction(mv.get("prediction"))
        if pred is None:
            log(f"dropped {knob} {mv.get('sku') or ''}: no checkable prediction ({json.dumps(mv.get('prediction'))[:160]})")
            continue
        if knob in KNOBS:
            sku = mv.get("sku")
            if sku not in ok_skus:
                log(f"dropped {knob} {sku}: unknown SKU")
                continue
            lo, hi = KNOBS[knob][0]
            cur = ((auto.get("sku") or {}).get(sku) or {}).get(knob, 1.0)
            key = f"{knob}|{sku}"
        elif knob in GLOBAL:
            sku = None
            lo, hi = GLOBAL[knob][1]
            cur = gnow.get(knob)
            key = knob
        else:
            log(f"dropped unknown knob {knob!r}")
            continue
        try:
            cur = value if cur is None else float(cur)
        except (TypeError, ValueError):
            log(f"dropped {key}: its current value {cur!r} is not a number")
            continue
        if key in seen:
            log(f"dropped {key}: a second move on it in one run")
            continue
        if recent.get(key, 0) >= 3:
            log(f"dropped {key}: churn freeze ({recent[key]} applied moves in the last 3 h)")
            continue
        new = clamp(cur, value, step_for(knob, H), lo, hi)
        if knob in GLOBAL:                        # the rails win; a move they undo is not a move
            g_try = dict(auto.get("globals") or {})
            g_try.update(glob_ch)
            g_try[knob] = new
            apply_rails(g_try, H, targets)
            if g_try.get(knob, new) != new:
                log(f"rails hold {knob} at {_f(g_try[knob])} (asked {_f(value)})")
                new = g_try[knob]
        if abs(new - cur) <= 1e-9:
            log(f"no change {key}: {_f(cur)} (asked {_f(value)})")
            continue
        seen.add(key)
        n += 1
        applied = mode == "apply"
        log(f"[{mode}] {knob} {sku or ''} {_f(cur)} -> {_f(new)} (asked {_f(value)}) | target {mv.get('target')} | "
            f"{str(mv.get('why'))[:200]} | predicts {pred['metric']} {pred['op']} {_f(pred['value'])} in {pred['window_h']:g} h")
        if applied:
            if knob in KNOBS:
                sku_ch.setdefault(sku, {})[knob] = new
            else:
                glob_ch[knob] = new
        mem.add_episode(new_episode(knob, sku, new, cur, pred, applied, mv.get("why"), mv.get("target"), t_move, cache))
    if mode == "apply":
        write_auto(sku_ch, glob_ch, H, targets)
    mem.save()


def dry_prompt():
    """--dry-prompt: the prompt this run would send, printed; no model call, nothing written (no product-memory
    update, no board cache, no state files, no log line)."""
    global DRY
    DRY = True
    mem = Memory()
    m = pm.load()
    prompt, ev, targets, race = build_prompt(m, mem)
    print(prompt)
    print(f"\n--- dry prompt: {len(prompt)} chars (limit {MAX_PROMPT}); sku rows {len(ev['skus'])}; "
          f"targets {sorted(targets) if isinstance(targets, dict) else targets}")
    return prompt


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-prompt", action="store_true")
    args = ap.parse_args()
    if args.dry_prompt:
        dry_prompt()
        raise SystemExit(0)
    lock = open(HERE / ".market" / "strategist.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)     # one run at a time (a model call can take minutes)
    except OSError:
        raise SystemExit(0)
    run(args.force)
