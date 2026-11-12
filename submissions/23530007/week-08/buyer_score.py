"""Scorekeeper for the buyer (11-11 restructure, user: saved first).

Every 15 min (watch_round.sh) it writes .market/buyer_score.json from buyer_memory.json and the board:

  m_now        the board's saved (the mean every new deal is measured against today)
  m_star       the mean our saved will END at if the last 24 h continue (a deal below it lowers the final score);
               market_buyer.py uses it for the goods price ceilings and the skip bar
  v6 / v24     our per-deal score over the last 6 / 24 h, by kind, scored exactly as agent.md does:
               v = 1 - price/list; a broken unit 0 when refunded (warranty >= 6 h) else -1
  ours         our realized v per shop|kind over 24 h (market_buyer blends it into the market expectation)
  calibration  realized minus expected at order time, by kind and by shop|kind (the goods gap was .04-.08)
  reconcile    the board-implied per-deal v since the oldest snapshot >= 3 h old vs our own records; a gap above
               RECONCILE_TOL marks "suspect" (the +1 refund scoring would have shown here within an hour)
  skips        skipped orders in 6 h by kind with the expected value they were skipped at
  breaks       break share of goods deals over 3 h and 6 h
  arms         per-order score by arm since the last restart of the arm fields (deal ? v : 0)

Reads only; the strategists and guards.py read the file. python buyer_score.py --print shows it.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEM = HERE / "buyer_memory.json"
OUT = HERE / ".market" / "buyer_score.json"
MARKET = "https://market.ourolabs.ai"
END = 1794927540            # 2026-11-17 23:59 KST
# Patch 3.6 (11-12 ~08:40 UTC): the board's saved is the sum of min(1, (list - price)/(list - unit cost)) / (deals + 100),
# all past deals rescored (ours .3081 -> .7163). Every number here is on that scale since 11-12 ~10:30 UTC: our deals
# get their unit's exact cost from the ledger (settle -> unit -> supply, cost_model.py), else the buyer's estimate at
# the deal ("c"), else the SKU+grade estimate. v_old (1 - price/list) is kept for comparison.
RECONCILE_TOL = 0.03        # per-deal v: ours vs board-implied over the same window
HIST_KEEP = 96              # snapshots kept (24 h at 15 min)
KINDS = ("food", "goods")


def score_old(t):
    if t.get("broken"):
        return 0.0 if (t.get("warranty") or 0) >= 6 else -1.0
    return 1 - t["price"] / t["list"]


_COST = {}


def costs(trades, now):
    """order -> cost / list for our deals of the last 25 h: exact from the ledger where archived, else estimates."""
    import sqlite3
    import cost_model
    try:
        arc = sqlite3.connect(f"file:{HERE / 'data' / 'market_archive.db'}?mode=ro", uri=True, timeout=30)
        rows = [json.loads(d).get("data") or {} for (d,) in arc.execute(
            "SELECT data FROM ledger WHERE kind='settle' AND buyer='yms979' AND ts > ?", (now - 25 * 3600,))]
    except Exception:
        rows = []
    uc = cost_model.unit_costs([r.get("unit") for r in rows])
    by_order = {r.get("order"): r for r in rows}
    for t in trades:
        if t.get("outcome") != "deal" or not t.get("list") or now - t["t"] > 25 * 3600:
            continue
        r = by_order.get(t.get("order")) or {}
        if r.get("unit") in uc:
            _COST[t["order"]] = (uc[r["unit"]] / t["list"], "exact")
        elif t.get("c") is not None:
            _COST[t["order"]] = (t["c"], "est")
        else:
            _COST[t["order"]] = (cost_model.cost_share(t.get("item"), r.get("grade") or (t.get("graded") or [None])[0],
                                                       t.get("kind") or "food"), "est")


def score(t):
    """Patch 3.6 score of one of our deals."""
    if t.get("broken"):
        return 0.0 if (t.get("warranty") or 0) >= 6 else -1.0
    c = (_COST.get(t.get("order")) or (None,))[0]
    if c is None:
        import cost_model
        c = cost_model.cost_share(t.get("item"), None, t.get("kind") or "food")
    return 1.0 if c >= 1 else max(-1.0, min(1.0, (1 - t["price"] / t["list"]) / (1 - c)))


def mean(xs):
    return round(sum(xs) / len(xs), 4) if xs else None


def board():
    import board_api
    b = board_api.fetch(timeout=15, tries=2, wait=12)           # 11-11: see board_api.py
    me = next(x for x in b["buyers"] if x["name"] == "yms979")
    return {"saved": me["saved"], "deals": me["deals"], "served": me.get("served"), "orders": me.get("orders")}


def build(now=None):
    now = now or time.time()
    trades = json.load(open(MEM, encoding="utf-8")).get("trades", [])
    old = {}
    try:
        old = json.loads(OUT.read_text())
    except (OSError, ValueError):
        pass
    try:
        bd = board()
    except Exception as e:                       # the board is down: keep the last snapshot's numbers
        bd = dict(old.get("board") or {}, error=repr(e))
    hist = [h for h in old.get("hist", []) if now - h["t"] < HIST_KEEP * 900 and h.get("saved", 0) >= 0.5]
    costs(trades, now)
    if bd.get("saved") is not None:
        hist.append({"t": round(now), "saved": bd["saved"], "deals": bd["deals"]})
    out = {"t": round(now), "board": bd, "hist": hist[-HIST_KEEP:]}

    deals = [t for t in trades if t.get("outcome") == "deal" and t.get("signed") is not False and t.get("list") and t.get("price")]
    orders24 = {t["order"] for t in trades if now - t["t"] < 86400 and t.get("order")}
    d24 = [t for t in deals if now - t["t"] < 86400]
    d6 = [t for t in deals if now - t["t"] < 6 * 3600]
    out["v24"] = {"all": mean([score(t) for t in d24]), **{k: mean([score(t) for t in d24 if t.get("kind") == k]) for k in KINDS},
                  "deals": len(d24), "orders": len(orders24), "deals_h": round(len(d24) / 24, 1)}
    out["v6"] = {"all": mean([score(t) for t in d6]), **{k: mean([score(t) for t in d6 if t.get("kind") == k]) for k in KINDS},
                 "deals": len(d6), "deals_h": round(len(d6) / 6, 1)}

    # m_now and m_star
    m_now = bd.get("saved")
    n = bd.get("deals") or 0
    hours_left = max(0.0, (END - now) / 3600)
    if m_now is not None and out["v24"]["all"] is not None and n:
        s = m_now * (n + 100)
        add = out["v24"]["deals_h"] * hours_left
        proj = (s + out["v24"]["all"] * add) / (n + 100 + add)
        m_star = round(min(max(proj, m_now), m_now + 0.15), 4)
    else:
        m_star = old.get("m_star")
    out.update(m_now=m_now, m_star=m_star, hours_left=round(hours_left, 1), scale="patch3.6",
               v24_old={"all": mean([score_old(t) for t in d24]), **{k: mean([score_old(t) for t in d24 if t.get("kind") == k]) for k in KINDS}},
               cost_exact_share_24h=round(sum(1 for t in d24 if (_COST.get(t.get("order")) or (0, ""))[1] == "exact") / max(1, len(d24)), 3))

    # our realized v per shop|kind (24 h) and calibration vs the expectation at order time
    ours, cal = {}, {}
    for t in d24:
        k = f"{t.get('shop')}|{t.get('kind')}"
        ours.setdefault(k, []).append(score(t))
        if t.get("expected") is not None and t.get("scale") == 36:
            cal.setdefault(t.get("kind"), []).append(score(t) - t["expected"])
            cal.setdefault(k, []).append(score(t) - t["expected"])
    out["ours"] = {k: {"n": len(v), "v": mean(v)} for k, v in ours.items() if len(v) >= 3}
    out["calibration"] = {k: {"n": len(v), "gap": mean(v)} for k, v in cal.items() if len(v) >= 5}

    # reconcile with the board: per-deal v implied by two snapshots vs our records in between
    rec = None
    base = next((h for h in hist if now - h["t"] >= 3 * 3600), None)
    if base and bd.get("saved") is not None and bd["deals"] > base["deals"] + 20:
        s0, s1 = base["saved"] * (base["deals"] + 100), bd["saved"] * (bd["deals"] + 100)
        implied = (s1 - s0) / (bd["deals"] - base["deals"])
        mine = [score(t) for t in deals if base["t"] <= t["t"] <= now]
        rec = {"since": base["t"], "board_deals": bd["deals"] - base["deals"], "our_deals": len(mine),
               "implied_v": round(implied, 4), "our_v": mean(mine),
               "gap": round((mean(mine) or 0) - implied, 4) if mine else None}
        rec["suspect"] = bool(mine) and abs(rec["gap"]) > RECONCILE_TOL
    out["reconcile"] = rec

    # skips (6 h) and breaks
    sk = [t for t in trades if now - t["t"] < 6 * 3600 and str(t.get("outcome", "")).startswith("skipped")]
    out["skips"] = {k: {"n": sum(1 for t in sk if t.get("kind") == k),
                        "expected_median": sorted([t["expected"] for t in sk if t.get("kind") == k and t.get("expected") is not None] or [0])[
                            len([t for t in sk if t.get("kind") == k and t.get("expected") is not None]) // 2]}
                    for k in KINDS}
    # 11-11 (user: 'why must I tell you the obvious'): skipped orders whose best expected v was still above m* (each
    # such deal would have raised the final saved): the gains left on the table, per kind and per hour
    ms_ = out.get("m_star")
    if ms_:
        out["skips"]["above_mstar_per_h"] = {k: round(sum(1 for t in sk if t.get("kind") == k and (t.get("expected") or 0) > ms_) / 6, 1)
                                             for k in KINDS}
    out["skips"]["share_of_orders_6h"] = round(len({t["order"] for t in sk if t.get("order")}) /
                                               max(1, len({t["order"] for t in trades if now - t["t"] < 6 * 3600 and t.get("order")})), 3)
    g3 = [t for t in deals if now - t["t"] < 3 * 3600 and t.get("kind") == "goods"]
    g6 = [t for t in d6 if t.get("kind") == "goods"]
    out["breaks"] = {"goods_3h": {"n": len(g3), "share": round(sum(1 for t in g3 if t.get("broken")) / len(g3), 3) if g3 else None},
                     "goods_6h": {"n": len(g6), "share": round(sum(1 for t in g6 if t.get("broken")) / len(g6), 3) if g6 else None},
                     "minus_one_24h": sum(1 for t in d24 if score(t) == -1.0)}

    # per-order arm scores over 24 h (deal ? v : 0 for the order's last record)
    last = {}
    for t in trades:
        if now - t["t"] < 86400 and t.get("order"):
            last[t["order"]] = t
    arms = {}
    for o, t in last.items():
        v = score(t) if t.get("outcome") == "deal" and t.get("signed") is not False and t.get("list") and t.get("price") else 0.0
        for flag in ("aimtop", "aim15", "cheapfood", "cheapgoods", "pricefirst", "talk", "soft"):
            if flag in t:
                arms.setdefault(f"{flag}={bool(t[flag])}", []).append(v)
        if t.get("push"):
            arms.setdefault(f"push={t['push']}", []).append(v)
    out["arms_per_order_24h"] = {k: {"orders": len(v), "score": mean(v), "deals_per_order": round(sum(1 for x in v if x != 0) / len(v), 3)}
                                 for k, v in sorted(arms.items())}
    return out


def main():
    out = build()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    if "--print" in sys.argv:
        print(json.dumps({k: v for k, v in out.items() if k != "hist"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
