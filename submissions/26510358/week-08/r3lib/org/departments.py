"""The departments of our market agent and their skills. Each skill is a plain function (mem, args, call) -> JSON,
so the same code runs behind the internal A2A server or in-process when the server is down.

  ceo         tick: one restock cycle, asking logistics and purchasing over A2A; kpi: board + internal metrics
  purchasing  propose: what to buy (demand x share x gap x horizon, EV quantity)  [restock_r3.plan]
  logistics   window: is supply open; approve: cash, budget and line limits on a proposal; health: stock risks
  quality     warranty_policy: break rate and warranty per grade x category; harsh: review-spam styles; refunds
  marketing   stats: our posts, votes and member codes; pricing: close rate, ask levels, premium pilot;
              markdown: the unprompted markdown of overstocked SKUs (initiative.judge + the clearance bandit)
              shelf: the unprompted choice of the grade a mixed-grade SKU shows first (initiative.judge)

Selling (shop_r3.py) never goes through here: it answers buyers in milliseconds in-process and reads the same
memory and pricing rules. The departments decide on the slow path (every 60 s or less often).
`call(to, skill, args)` is how one department asks another (A2A when the server runs, in-process otherwise).
"""
import json
import time

from .. import config, initiative, market, markdown, pricing, score_model, scout, shelf

GRADES = ("A", "B", "C")
ABNORMAL_OFFER_X = 3                  # a "deal" above this multiple of list is left out of the price KPIs


# ------------------------------------------------------------------------------------------- purchasing
def purchasing_propose(mem, args, call):
    import restock_r3
    md = args.get("state") or str(config.MARKET_DIR)
    inv = market.read_json("inventory.json", {}, md) or {}
    lines, why, state = restock_r3.plan(inv, market.read_json("demand.json", [], md) or [], mem)
    return {"lines": lines, "why": why, "state": state}


# ------------------------------------------------------------------------------------------- logistics
def logistics_window(mem, args, call):
    import restock_r3
    inv = market.read_json("inventory.json", {}, args.get("state")) or {}
    return {"near": restock_r3.window_near(inv), "restock": inv.get("restock") or {}}


def logistics_approve(mem, args, call):
    """Last check before the kit spends money: the window, the cash reserve and the kit's 10-line limit, with the
    worst-case unit cost the market may draw (0.75 of list)."""
    import restock_r3
    inv = market.read_json("inventory.json", {}, args.get("state")) or {}
    if not restock_r3.window_near(inv):
        return {"buy": [], "why": ["supply window not open"]}
    cat = market.catalog()
    cash = (inv.get("balance") or 0) - config.CASH_RESERVE
    out, why = [], []
    for ln in (args.get("lines") or [])[:10]:
        info = cat.get(ln.get("sku"))
        qty = int(ln.get("qty") or 0)
        if not info or not 1 <= qty <= 20:
            why.append(f"dropped {ln}: unknown SKU or bad qty")
            continue
        cost = restock_r3.unit_cost_estimate(info) * qty
        if cost > cash:
            why.append(f"dropped {ln['sku']} x{qty}: worst-case cost {cost:,.0f} above cash over reserve")
            continue
        cash -= cost
        out.append({"sku": ln["sku"], "qty": qty})
    return {"buy": out, "why": why}


def logistics_health(mem, args, call, now=None):
    """Stock risks: food expiring within 6 h, goods held over 3 days, requests turned away for lack of stock."""
    now = now or time.time()
    inv = market.read_json("inventory.json", {}, args.get("state")) or {}
    units = [u for u in inv.get("units") or [] if u.get("grade") != "expired"]
    expiring = [u for u in units if u.get("kind") == "food" and u.get("expires_at") and u["expires_at"] - now < 6 * 3600]
    aged = [u for u in units if u.get("kind") == "goods" and now - (u.get("supplied_at") or now) > 3 * 86400]
    stock_cost = sum(u["cost"] for u in units)
    cc = mem.get("cash_cap") or {}
    budget = int(cc.get("cap") or config.STOCK_BUDGET_MAX)
    # The premium budget is separate from the cap (restock_r3.plan), so stock may reach their sum: until 2026-11-10
    # the KPI showed only the cap and stock at "1.8x its budget" looked like a fault (RL_PROACTIVE.md 5.1-C).
    return {"units": len(units), "stock_cost": stock_cost, "stock_budget": budget,
            "premium_budget": int(cc.get("premium_budget") or 0),
            "stock_ceiling": budget + int(cc.get("premium_budget") or 0),
            "expiring_6h": {"units": len(expiring), "cost": sum(u["cost"] for u in expiring)},
            "goods_over_3d": {"units": len(aged), "cost": sum(u["cost"] for u in aged)},
            "stockouts_per_h": {market.leaf(k): round(v, 2) for k, v in sorted(
                mem.stockout_rates(now=now).items(), key=lambda x: -x[1])[:8]}}


# ------------------------------------------------------------------------------------------- quality
def quality_warranty_policy(mem, args, call):
    """What the shop will do per grade for the categories we hold: p_break, warranty hours at a typical price,
    and the share of price the warranty adds."""
    inv = market.read_json("inventory.json", {}, args.get("state")) or {}
    cats = sorted({u["category"] for u in inv.get("units") or [] if u.get("kind") == "goods"})[:12]
    rows = []
    for k in cats:
        for g in GRADES:
            p = mem.break_rate_for(g, k)
            unit = {"kind": "goods", "grade": g, "cost": 0.5, "list_price": 1.0}   # margin share of a typical sale
            w = pricing.warranty_hours_for(unit, 0.75, p)
            rows.append({"category": market.leaf(k), "grade": g, "p_break": round(p, 4), "warranty_h": w,
                         "price_in": round(p, 4) if w and config.WARRANTY_PRICE_IN else 0})
    return {"policy": rows}


def quality_harsh(mem, args, call):
    import restock_r3
    restock_r3.refresh_harsh_styles(mem)
    return {"harsh_styles": mem.get("harsh_styles") or {}}


def quality_refunds(mem, args, call, now=None):
    """Our shop's goods sales and refunds in the window, by grade (refund = the market's `broken` with refunded>0)."""
    now = now or time.time()
    hours = float(args.get("hours") or 24)
    mine = mem.my_shop_urls()
    if not mine:
        return {"by_grade": {}}
    with mem.db() as c:
        refunded = {}
        for r in c.execute("SELECT data FROM ledger WHERE kind='broken' AND ts>?", (now - hours * 3600,)):
            d = json.loads(r["data"])
            if d.get("refunded"):
                refunded[d.get("order")] = d["refunded"]
        q = f"SELECT order_id, grade, amount FROM settles WHERE kind='goods' AND ts>? AND shop IN " \
            f"({','.join('?' * len(mine))})"
        out = {}
        for r in c.execute(q, (now - hours * 3600, *mine)):
            g = out.setdefault(r["grade"], {"sold": 0, "revenue": 0, "refunds": 0, "refunded": 0})
            g["sold"] += 1
            g["revenue"] += r["amount"] or 0
            if r["order_id"] in refunded:
                g["refunds"] += 1
                g["refunded"] += refunded[r["order_id"]]
    for g in out.values():
        g["refund_share"] = round(g["refunded"] / g["revenue"], 4) if g["revenue"] else 0
    return {"hours": hours, "by_grade": out}


# ------------------------------------------------------------------------------------------- marketing
def marketing_stats(mem, args, call):
    rec = mem.my_post_reception(hours=float(args.get("hours") or 24), role="buyer")
    with mem.db() as c:
        m = c.execute("SELECT COUNT(*) n, COALESCE(SUM(uses), 0) u FROM members").fetchone()
    return {"buyer_posts": rec["posts"], "likes": rec["likes"], "dislikes": rec["dislikes"],
            "member_codes": m["n"], "member_code_uses": m["u"]}


def marketing_pricing(mem, args, call, now=None):
    """How our asks do: close rate, first ask and final price (share of list) over the last hours, plus the
    premium pilot (800k+ goods) sold and held. The 2026-11-04 change (ASK_ROOM 0.05 -> 0.09) is judged on this."""
    now = now or time.time()
    hours = float(args.get("hours") or 2)
    with mem.db() as c:
        r = c.execute("SELECT COUNT(*) n, AVG(outcome='deal') close, AVG(first_ask*1.0/list_price) ask, "
                      "AVG(CASE WHEN outcome='deal' THEN final_price*1.0/list_price END) fin FROM negotiations "
                      "WHERE role='shop' AND list_price>0 AND ts>? AND COALESCE(final_price, 0) <= list_price * ?",
                      (now - hours * 3600, ABNORMAL_OFFER_X)).fetchone()
        # RL_PROACTIVE.md 5.1-G: 63 "deals" at 1,666,667 for a 12,900 SKU (G1831f2c0) lifted final_ratio; an offer
        # over 3x list is not a price, so it is counted apart and left out of the ratios.
        odd = c.execute("SELECT COUNT(*) n FROM negotiations WHERE role='shop' AND list_price>0 AND ts>? AND "
                        "final_price > list_price * ?", (now - hours * 3600, ABNORMAL_OFFER_X)).fetchone()["n"]
        mine = mem.my_shop_urls()
        prem = {"sold_24h": 0, "revenue_24h": 0}
        if mine:
            p = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(amount), 0) rev FROM settles WHERE kind='goods' AND "
                          f"list_price>=? AND ts>? AND shop IN ({','.join('?' * len(mine))})",
                          (config.PREMIUM_MIN_LIST, now - 24 * 3600, *mine)).fetchone()
            prem = {"sold_24h": p["n"], "revenue_24h": p["rev"]}
    inv = market.read_json("inventory.json", {}, args.get("state")) or {}
    held = [u for u in inv.get("units") or [] if u.get("kind") == "goods" and u.get("grade") != "expired"
            and (u.get("list_price") or 0) >= config.PREMIUM_MIN_LIST]
    prem.update(held=len(held), held_cost=sum(u["cost"] for u in held))
    rd = lambda x: round(x, 3) if x is not None else None
    return {"ask_room": config.ASK_ROOM, "hours": hours, "negotiations": r["n"], "close_rate": rd(r["close"]),
            "first_ask_ratio": rd(r["ask"]), "final_ratio": rd(r["fin"]), "abnormal_offers": odd, "premium": prem}


def marketing_markdown(mem, args, call, now=None):
    """The shop's unprompted action: every MARKDOWN_EVERY_S judge a markdown for each overstocked goods SKU
    (r3lib/markdown.py). No order starts it; the CEO tick calls it whether or not supply is open. Once a day it also
    writes down why we still do not buy a paid dislike, lend or buy a bond."""
    now = now or time.time()
    if now - (mem.get("markdown_ts") or 0) < config.MARKDOWN_EVERY_S:
        return {"due": False}
    mem.put("markdown_ts", now)
    inv = market.read_json("inventory.json", {}, args.get("state")) or {}
    board = score_model.load()
    rows = markdown.tick(mem, inv.get("units") or [], now=now, board=board)
    if now - (mem.get("refusals_ts") or 0) >= 24 * 3600:
        mem.put("refusals_ts", now)
        unit = inv.get("dislike_power_unit") or 0
        initiative.refuse("paid_dislike", "the forum part of Buyer ALL is our own rating: a rival's falling moves "
                          "no part of our score, and the second dislike of the hour costs the unit below from book",
                          "forum rating, book", C=-score_model.sale_value(board, -unit, 0, revenue=False),
                          unit=unit, free_left_fee=inv.get("next_dislike_fee"), now=now)
        initiative.refuse("loan_or_bond", "Round 3 has no way to enforce repayment; held until Round 4's wallet "
                          "settlement (loan_ledger, due_check)", "book", now=now)
    return {"due": True, "judged": len(rows), "acted": sum(r["decision"] == "act" for r in rows),
            "live": sorted(k for k, m in (mem.get("markdown") or {}).items() if m.get("arm") != "keep")}


def marketing_shelf(mem, args, call, now=None):
    """The shop's second unprompted action: every SHELF_EVERY_S judge, for each goods SKU with several grades on
    hand, whether a grade the market buys and we do not sell should be shown first (r3lib/shelf.py)."""
    now = now or time.time()
    if now - (mem.get("shelf_ts") or 0) < config.SHELF_EVERY_S:
        return {"due": False}
    mem.put("shelf_ts", now)
    inv = market.read_json("inventory.json", {}, args.get("state")) or {}
    rows = shelf.tick(mem, inv.get("units") or [], now=now, board=score_model.load())
    return {"due": True, "judged": len(rows), "acted": sum(r["decision"] == "act" for r in rows),
            "live": shelf.live(mem, now)}


# ------------------------------------------------------------------------------------------- ceo
def ceo_tick(mem, args, call):
    """One restock cycle. Returns the kit's restock answer ({"buy": [...]}, maybe "bankrupt" or "wait")."""
    md = args.get("state") or str(config.MARKET_DIR)
    inv = market.read_json("inventory.json", {}, md) or {}
    if (inv.get("round") or 0) < 3:
        return {"buy": []}
    if (inv.get("balance") or 0) < (inv.get("bankrupt_line") or 0) and not inv.get("units") \
            and (inv.get("bankruptcies_left") or 0) > 0:
        return {"buy": [], "bankrupt": True}
    try:
        call("marketing", "markdown", {"state": md})    # unprompted, also in the last hours when buying has stopped
    except Exception:
        pass
    try:
        call("marketing", "shelf", {"state": md})       # unprompted: which grade each mixed-grade SKU shows first
    except Exception:
        pass
    if config.hours_left_in_round() <= config.GOODS_STOP_BUYING_HOURS:   # food stops earlier, inside the plan
        return {"buy": [], "wait": "last hours of the round: no new stock"}
    try:
        call("quality", "harsh", {})                    # rate-limited inside; keeps the shop's guard current
    except Exception:
        pass
    if not call("logistics", "window", {"state": md}).get("near"):
        return {"buy": [], "wait": "supply window not open"}
    prop = call("purchasing", "propose", {"state": md})
    ok = call("logistics", "approve", {"state": md, "lines": prop.get("lines") or []})
    lines = ok.get("buy") or []
    if lines:
        mem.record_restock(lines, prop.get("why") or [])
        cat = market.catalog()
        for ln in lines:
            cost = (cat.get(ln["sku"]) or {}).get("list_price", 0) * config.EV_COST_RATIO * ln["qty"]
            if cost >= 300_000:                          # big bets are worth remembering when they go wrong
                mem.note("large_restock", ln["sku"], f"asked for {ln['qty']} x {ln['sku']} (~{cost:,.0f})", 0.5)
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_DIR / "restock.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time()), **(prop.get("state") or {}), "buy": lines,
                                "why": prop.get("why") or [], "logistics": ok.get("why") or []},
                               ensure_ascii=False) + "\n")
    return {"buy": lines}


SHOP_WEIGHTS = {"book": 0.5, "revenue": 0.3, "stars_rank": 0.2}   # /rules 3.3; stars_rank is the per-account mean that scores (board "stars" pools reviews)
BUYER_WEIGHTS = {"saved": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}       # /rules, patches 3.4 and 3.5


def ceo_kpi(mem, args, call, now=None):
    """Board percentiles of our shop and buyer plus each department's report; appended to logs/r3/kpi.jsonl."""
    now = now or time.time()
    board = market.get_json("/api/board") or {}
    snap = {"ts": round(now)}

    def pct(rows, weights, raw=()):
        """Our value and percentile per scored field, and parts = weight x percentile (x the value itself for
        fields in raw), so a fall in ALL can be read off the part that moved."""
        rows = [r for r in rows or [] if r.get("deals")]
        me = next((r for r in rows if r.get("name") in config.MY_NAMES), None)
        if not me:
            return None
        out, parts = {"all": me.get("all")}, {}
        for k, w in weights.items():
            vals = [r.get(k) or 0 for r in rows]
            out[k] = {"value": me.get(k), "pct": round(sum(v < (me.get(k) or 0) for v in vals) / len(vals), 3)}
            parts[k] = round(w * ((me.get(k) or 0) if k in raw else out[k]["pct"]), 4)
        out["parts"] = parts
        return out

    snap["shop"] = pct(board.get("shops"), SHOP_WEIGHTS)                        # patch 3.3: book, not cash
    snap["buyer"] = pct(board.get("buyers"), BUYER_WEIGHTS, raw=("forum",))    # 3.4 saved; 3.5 forum as itself
    players = board.get("players") or []
    me = next((i for i, p in enumerate(players) if (p.get("shop") or {}).get("name") in config.MY_NAMES), None)
    if me is not None:
        snap["player"] = {"rank": me + 1, "of": len(players), "total": players[me].get("total"),
                          "leader_total": players[0].get("total")}
    for to, skill in (("logistics", "health"), ("quality", "refunds"), ("marketing", "stats"),
                      ("marketing", "pricing")):
        try:
            snap[f"{to}.{skill}"] = call(to, skill, {})
        except Exception as e:
            snap[f"{to}.{skill}"] = {"error": str(e)[:200]}
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.LOG_DIR / "kpi.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return snap


SKILLS = {
    "ceo": {"tick": (ceo_tick, "one restock cycle: logistics window, purchasing proposal, logistics approval"),
            "kpi": (ceo_kpi, "board percentiles and department reports, appended to kpi.jsonl")},
    "purchasing": {"propose": (purchasing_propose, "what to buy now, with reasons")},
    "logistics": {"window": (logistics_window, "is the supply window open or about to open"),
                  "approve": (logistics_approve, "cash, window and line limits on a purchase proposal"),
                  "health": (logistics_health, "expiring food, aged goods, stock-outs")},
    "quality": {"warranty_policy": (quality_warranty_policy, "break rate and warranty per grade and category"),
                "harsh": (quality_harsh, "buyer styles we decline for review spam"),
                "refunds": (quality_refunds, "our goods refunds by grade")},
    "marketing": {"stats": (marketing_stats, "our buyer posts, votes and member codes"),
                  "markdown": (marketing_markdown, "unprompted markdown of overstocked SKUs, judged by p > C/(B+C)"),
                  "shelf": (marketing_shelf, "unprompted: the grade a mixed-grade SKU shows first, by p > C/(B+C)"),
                  "pricing": (marketing_pricing, "close rate, ask and final price ratios, premium pilot")},
}


def run(to, skill, args, mem, call):
    fn = (SKILLS.get(to) or {}).get(skill)
    if not fn:
        raise KeyError(f"{to} has no skill {skill}")
    return fn[0](mem, args or {}, call)
