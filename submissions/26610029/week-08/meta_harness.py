"""The outer loop: reads what the inner loops (seller_r3's pricing, restock's buying, market_buyer's
haggling, review's forum posts) actually did and tunes their shared policy knobs in the DB.

Runs every ~20 min (self-throttled; restock.py calls maybe_tune(d, now) on every invocation, which the
kit already runs every ~60s). Every consumer already reads its policy section fresh each call
(seller_r3.Market.refresh every 10 min, the others every subprocess run), so a change here reaches them
without a kit restart.

Two-stage model: rules look at the stats first and propose a direction (bounded, auditable even if the
LLM step fails); sonnet then gets the stats AND the rule proposal and may adjust it, but the result is
clamped to BOUNDS in code either way -- the LLM can never push a knob outside the safe range.
"""
import json
import re
import time

from harness import call_harness

INTERVAL = 1200          # seconds between tuning passes
LOOKBACK = 2100           # how far back the stats look -- kept close to INTERVAL (not much longer) so a
                         # tune's own effect shows up in the very next cycle instead of being averaged
                         # away with 1-2 hours of whatever the knob was set to before (measured live:
                         # cutting open_premium to 0 actually lifted the deal rate to 50%, but a 2-hour
                         # lookback kept diluting that with the worse 0.06/0.04/0.02 periods, so the next
                         # cycle "saw" no improvement and kept cutting a knob that had already worked)

# (min, max) for every knob a human already chose a starting value for; the LLM's answer is clamped here
BOUNDS = {
    "seller": {
        "undercut": (0.0, 0.06), "open_premium": (0.0, 0.08), "min_margin": (0.03, 0.12),
        "clearance_hours": (3, 12), "clearance_floor": (0.5, 0.8), "warranty_max_break": (0.02, 0.13),
        # upper bound raised 0.10 -> 0.13: measured live, grade B's market-wide break rate sits at
        # 0.115-0.117, just above the old 0.10 ceiling, so grade B goods could never clear
        # warranty_max_break and never got a warranty -- which then made every grade-B unit of a
        # high-list-price SKU (e.g. G81ade83e, list 34,740) unsellable to any buyer requiring one,
        # with buyers cancelling before even making an offer
        "warranty_hours": (6, 72), "concede_threshold": (0.08, 0.30),
    },
    "restock": {
        "window_budget": (0.05, 0.25), "lookback_h": (1, 6), "max_skus": (3, 14),
        # max_skus upper bound raised 10 -> 14: already pinned at 10 (the old ceiling), capping how many
        # categories we can be a candidate in at once
        "goods_target": (2, 10), "food_target": (1, 4), "food_min_sales": (3, 10), "goods_min_sales": (2, 6),
        "max_units": (60, 550),
        # upper bound raised 350 -> 550: already pinned at 350 (the old ceiling) again, this time with
        # 121 "stock is full" log lines in a single hour and balance still sitting at 4.28M won against a
        # 500k bankrupt_line -- most of that capital is doing nothing while every held unit sells at a
        # positive margin on average (measured live: +4,958 won/deal over 6,174 sell deals this round).
        # Raising the ceiling again lets restock keep deploying idle cash instead of refusing to buy
    },
    "shop_forum": {"threshold": (0.3, 0.7), "quiet_after": (300, 1800)},
    "buyer": {"target_ratio": (0.35, 0.75), "rounds": (4, 8), "tries": (1, 3)},
}
INT_KEYS = {"max_skus", "goods_target", "food_target", "food_min_sales", "goods_min_sales", "max_units",
           "rounds", "tries", "quiet_after", "clearance_hours", "lookback_h"}


def clamp(section, key, value):
    lo, hi = BOUNDS[section][key]
    value = max(lo, min(hi, value))
    return int(round(value)) if key in INT_KEYS else round(float(value), 4)


def gather_stats(d, now):
    since = now - LOOKBACK
    out = {}

    rows = d.q("SELECT outcome->>'result' r FROM trajectory WHERE role='shop' AND decision='sell' AND ts > $1",
               since)
    n = len(rows)
    counts = {"deal": 0, "cancel": 0, "reject": 0}
    for r in rows:
        if r["r"] in counts:
            counts[r["r"]] += 1
    out["shop_negotiations"] = dict(counts, total=n,
                                    deal_rate=round(counts["deal"] / n, 3) if n else None)

    broken = d.q("""SELECT sum(b.refunded) lost, count(*) n FROM broken b JOIN shops sh ON sh.url=b.shop_url
                    WHERE sh.name='real_yellow' AND b.ts > $1""", since)[0]
    out["shop_warranty_losses"] = {"refunded_won": int(broken["lost"] or 0), "n": int(broken["n"] or 0)}

    rr = d.q("""SELECT count(*) n FROM trajectory WHERE role='shop' AND decision='restock' AND ts > $1
               AND jsonb_array_length(outcome->'buy') = 0""", since)[0]
    rtot = d.q("SELECT count(*) n FROM trajectory WHERE role='shop' AND decision='restock' AND ts > $1",
               since)[0]
    out["restock_runs"] = {"total": int(rtot["n"]), "bought_nothing": int(rr["n"])}

    bd = d.q("""SELECT count(*) n, avg((outcome->>'deal')::boolean::int) deal_rate
               FROM trajectory WHERE role='buyer' AND decision='buy' AND ts > $1""", since)[0]
    saved = d.q("""SELECT avg((outcome->>'saved')::float) saved, count(*) n
                   FROM trajectory WHERE role='buyer' AND decision='settled' AND ts > $1""", since)[0]
    out["buyer"] = {"orders": int(bd["n"] or 0), "deal_rate": round(float(bd["deal_rate"] or 0), 3),
                    "avg_saved": round(float(saved["saved"] or 0), 3), "settled_n": int(saved["n"] or 0)}

    posts = d.q("""SELECT count(*) n FROM trajectory WHERE role='shop' AND decision='forum_post' AND ts > $1""",
               since)[0]
    out["shop_posts"] = int(posts["n"] or 0)
    return out


def rule_proposal(stats, policy):
    """A simple, explainable first pass: large, specific triggers only -- no proposal when signals are
    thin or ambiguous, so the LLM step (and the clamp) are the only things that can still move a knob."""
    p = {"seller": {}, "restock": {}, "shop_forum": {}, "buyer": {}}
    reasons = []

    neg = stats["shop_negotiations"]
    cur_premium = policy["seller"].get("open_premium", 0.06)
    if (neg["total"] >= 20 and neg["deal_rate"] is not None and neg["deal_rate"] < 0.35
            and cur_premium > BOUNDS["seller"]["open_premium"][0]):   # no room left: let the LLM pick
        p["seller"]["open_premium"] = max(BOUNDS["seller"]["open_premium"][0], cur_premium - 0.02)
        reasons.append(f"shop deal_rate {neg['deal_rate']} over {neg['total']} negotiations is low: "
                       f"open_premium {cur_premium} -> {p['seller']['open_premium']}")
    elif neg["total"] >= 20 and neg["deal_rate"] is not None and neg["deal_rate"] < 0.35:
        # price is already at its floor and the rate is still low: try a different lever -- concede to
        # target more readily, in case the stuck negotiations are lowball-and-vanish buyers, not price
        cur_c = policy["seller"].get("concede_threshold", 0.15)
        if cur_c > BOUNDS["seller"]["concede_threshold"][0]:
            p["seller"]["concede_threshold"] = max(BOUNDS["seller"]["concede_threshold"][0], cur_c - 0.03)
            reasons.append(f"shop deal_rate {neg['deal_rate']} is still low but open_premium is already at "
                           f"its floor ({cur_premium}): trying concede_threshold {cur_c} -> "
                           f"{p['seller']['concede_threshold']} instead")
        else:
            reasons.append(f"shop deal_rate {neg['deal_rate']} is still low but both open_premium and "
                           f"concede_threshold are already at their floors -- likely not fixable by price")

    loss = stats["shop_warranty_losses"]
    if loss["n"] >= 3 and loss["refunded_won"] > 100000:
        cur = policy["seller"].get("warranty_max_break", 0.06)
        p["seller"]["warranty_max_break"] = cur * 0.7
        reasons.append(f"{loss['n']} warranty refunds cost {loss['refunded_won']} won: "
                       f"warranty_max_break {cur} -> {p['seller']['warranty_max_break']:.4f}")

    rs = stats["restock_runs"]
    if rs["total"] >= 5 and rs["bought_nothing"] / rs["total"] > 0.5:
        cur = policy["restock"].get("max_units", 150)
        p["restock"]["max_units"] = cur + 40
        reasons.append(f"{rs['bought_nothing']}/{rs['total']} restock runs bought nothing (stock full): "
                       f"max_units {cur} -> {p['restock']['max_units']}")

    buyer = stats["buyer"]
    if buyer["orders"] >= 20 and buyer["deal_rate"] < 0.3:
        cur = policy["buyer"].get("target_ratio", 0.55)
        p["buyer"]["target_ratio"] = cur + 0.05
        reasons.append(f"buyer deal_rate {buyer['deal_rate']} over {buyer['orders']} orders is low: "
                       f"target_ratio {cur} -> {p['buyer']['target_ratio']}")

    return {k: v for k, v in p.items() if v}, reasons


PROMPT = """You tune policy knobs for an autonomous shop and buyer in an online market, once every ~20 minutes.
Stats are from the last 2 hours. A rule-based first pass already proposed some changes; you may keep,
adjust, or drop them, and you may propose a different knob instead if the stats justify it better. Only
change a knob when the stats clearly justify it -- an empty object for a section is a valid, often correct,
answer. Never invent a key that is not in "bounds".

Stats:
{stats}

Current policy (only overrides; anything missing uses the code's own default):
{policy}

Rule-based proposal and its reasoning:
{rule_proposal}
{rule_reasons}

Bounds (your answer will be clamped to these regardless):
{bounds}

Answer with JSON only, shaped exactly like {{"seller": {{}}, "restock": {{}}, "shop_forum": {{}}, "buyer": {{}},
"reason": "one sentence"}}. Omit a key from a section if you are not changing it."""


def ask_llm(stats, policy, proposal, reasons):
    prompt = PROMPT.format(stats=json.dumps(stats, ensure_ascii=False),
                           policy=json.dumps(policy, ensure_ascii=False),
                           rule_proposal=json.dumps(proposal, ensure_ascii=False),
                           rule_reasons="\n".join(reasons) or "(none this cycle)",
                           bounds=json.dumps(BOUNDS, ensure_ascii=False))
    raw = call_harness(prompt, model="sonnet", timeout=90)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None, raw
    try:
        return json.loads(m.group()), raw
    except json.JSONDecodeError:
        return None, raw


def maybe_tune(d, now):
    last = float(d.get_state("last_meta_tune", 0))
    if now - last < INTERVAL:
        return
    d.set_state("last_meta_tune", now)

    stats = gather_stats(d, now)
    policy = {k: d.get_policy(k, {}) for k in BOUNDS}
    proposal, reasons = rule_proposal(stats, policy)

    final, raw = ask_llm(stats, policy, proposal, reasons)
    if final is None:
        final = dict(proposal)              # LLM unparseable: fall back to the rule-only proposal
        llm_reason = "(harness answer unparseable, used the rule-based proposal as-is)"
    else:
        llm_reason = final.pop("reason", "(no reason given)")

    changed = {}
    for section in BOUNDS:
        edits = final.get(section) or {}
        if not isinstance(edits, dict):
            continue
        clamped = {}
        for key, value in edits.items():
            if key not in BOUNDS[section] or not isinstance(value, (int, float)):
                continue
            clamped[key] = clamp(section, key, value)
        if clamped:
            merged = dict(policy[section], **clamped)
            d.set_policy(section, merged, llm_reason[:500], now)
            changed[section] = clamped

    d.log_trajectory(now, "meta", "tune", {"stats": stats, "rule_proposal": proposal, "rule_reasons": reasons},
                     {"applied": changed, "reason": llm_reason})
    print(f"[meta] tuned {changed or '(nothing this cycle)'} -- {llm_reason}", flush=True)
