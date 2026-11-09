"""The objective (Kiro 2.0, 11/6, the person's call: "build stages 1 and 2 now; let the analysis turn on what needs data
once it is there"). Every 20 minutes, from the class board (the midterm's rank: students among students), it prices
what each part of our total is worth now and picks the buyer's least share of list a deal must keep, by projecting
the round's end. analysis/objective.json {"at", "lambda", "buyer_min_share", "options", "basis"}; market_buyer reads
buyer_min_share while it is fresh (else its MIN_SHARE), the experiments read lambda.

The buyer's threshold: Patch 3.4 scores saved = sum of shares kept / (deals + 100) at 0.5 and deals at 0.1. Taking a
deal that keeps less than our running saved lowers the first and raises the second. For each threshold t in OPTIONS,
our last WINDOW of purchases says how many deals a day we would keep (those that paid at most 1 - t of list) and the
mean share they kept (breaks and refunds included); projected to the round's end against every other student's
saved (held where it is: a mean moves slowly) and deals (at its pace since the history line nearest 3 h ago, else its
average since the round began), the t with the highest 0.5 x saved percentile + 0.1 x deals percentile wins. With
fewer than MIN_DEALS purchases in the window it says nothing and the buyer keeps its own MIN_SHARE. Since 11/8 a third
of the orders run PROBE_DROP under the pick ("probe" in objective.json), and a threshold is judged only on the orders
that ran at it or lower, so the thresholds under the pick are seen too.

lambda: what one more unit of each part adds to our total now (the next row's gain over the need, scoreboard.parts),
for the experiments' reward (a sale: lambda_book x (price - cost) + lambda_revenue x price)."""
import json
import time
from collections import Counter

import breakage
import common
import scoreboard

OPTIONS = (0.0, 0.15, 0.20, 0.24, 0.27, 0.30, 0.33, 0.36, 0.40, 0.45)   # 0.20-0.36 until 11/6 13:10
WINDOW = 12 * 3600
MIN_DEALS = 100          # 200 until 11/6 13:10
CLAMP = (0.15, 0.45)     # (0.20, 0.36) until 11/6 13:10
BAND = 0.005             # 11/6 15:40: thresholds within this of the best projected score count as tied (on 11/6 the pick
                         # flipped 0.33 -> 0.15 -> 0.33 on differences of 0.0024). Since 11/10 00:00 (the person's call)
                         # a tie keeps the threshold in force and only a pick BAND better moves it, to the best (pick());
                         # with none in force the highest of the tied is taken, as from 11/6. The highest-of-tied rule
                         # sent the pick from 0.27 to 0.36-0.40 now and then, and over 36 h, at each pick against its
                         # own probe in the same hours, 0.27 paid 57% of orders and added 0.038 a order to saved against
                         # 28% and 0.029 at 0.36 and 20% and 0.031 at 0.40
ROUND_START = 1793707260          # 11/3 21:01 KST (1793793660 until 11/6 17:45: a day late; a fallback only)
PROBE_SHARE, PROBE_DROP, PROBE_MIN = 0.33, 0.10, 80
# 11/8 (the person's call, "more aggressive"): a third of the orders (by a hash of the order id) run at PROBE_DROP under
# the pick, so a threshold under it is judged on orders that ran at it or lower. Until then the window held only deals
# the pick allowed, so every threshold under it scored the same (0.0-0.40 all 0.5262 on 11/8) and the pick won ties:
# 0.40 since 11/7 08:00, with deals down from 53 to 24-31 an hour and saved rising 0.003-0.004 per 6 h against 0.0058 at
# 0.30. A threshold is scored only with PROBE_MIN orders in the window that ran at it or lower.


def folder():
    return common.MARKET_DIR / "analysis"


def shares(purchases: list, breaks: list) -> list:
    """[(paid share of list, kept share)] of our purchases: kept = (list - price)/list, a break under a warranty 0,
    without one -1 (Patch 3.4's saved)."""
    broke = {b.get("order") for b in breaks}
    out = []
    for p in purchases:
        lp, price = p.get("list_price") or 0, p.get("price") or 0
        if not lp:
            continue
        if p.get("order") in broke:
            kept = 0.0 if (p.get("warranty_hours") or 0) > 0 else -1.0
        elif p.get("value") == 0:
            kept = -1.0
        else:
            kept = (lp - price) / lp
        out.append((price / lp, kept))
    return out


def pct(values: list, v) -> float:
    return scoreboard.pct(values, v)


def assigned(purchases: list, breaks: list, orders: list) -> tuple[list, list]:
    """([(paid share, kept share, the threshold its order ran at)], [every order's threshold]) over the same orders. An
    order without one (no shop had the product: no deal at any threshold) is left out of both, so a threshold's deals
    per hour are scaled to the orders that traded; so is a purchase whose order is not among them."""
    ran = {o.get("order"): float(o["min_share"]) for o in orders
           if o.get("order") and isinstance(o.get("min_share"), (int, float))}
    mine = [p for p in purchases if p.get("order") in ran and p.get("list_price")]
    return [(paid, kept, ran[p["order"]]) for p, (paid, kept) in zip(mine, shares(mine, breaks))], list(ran.values())


def choose(ours: dict, others: list, recent: list, hours_left: float, window_h: float,
           w_saved: float = 0.5, w_deals: float = 0.1, orders_u: list | None = None,
           current: float | None = None) -> tuple[float | None, dict]:
    """(the best threshold, {t: projected 0.5 saved pct + 0.1 deals pct}). ours: {saved, deals}; others: [(saved_end,
    deals_end)]; recent: [(paid share, kept share)] over window_h hours, or with orders_u (the threshold every order in
    the window ran at) [(paid share, kept share, its order's threshold)]: then t is judged only on the orders that ran at t
    or lower (PROBE_MIN of them at least), its deals per hour scaled from them to every order."""
    n, s_now = ours["deals"], ours["saved"] * (ours["deals"] + 100)
    scores = {}
    for t in OPTIONS:
        if orders_u is not None:
            valid = sum(1 for u in orders_u if u <= t + 1e-9)
            if valid < PROBE_MIN:
                continue
            kept = [k for paid, k, u in recent if u <= t + 1e-9 and paid <= 1 - t]
            rate = len(kept) / valid * len(orders_u) / window_h
        else:
            kept = [k for paid, k, *_ in recent if paid <= 1 - t] if t else [k for _, k, *_ in recent]
            rate = len(kept) / window_h
        mean = sum(kept) / len(kept) if kept else 0.0
        add = rate * hours_left
        saved_end = (s_now + mean * add) / (n + add + 100)
        deals_end = n + add
        col_s = [x[0] for x in others] + [saved_end]
        col_d = [x[1] for x in others] + [deals_end]
        scores[t] = round(w_saved * pct(col_s, saved_end) + w_deals * pct(col_d, deals_end), 4)
    return pick(scores, current), scores


def pick(scores: dict, current: float | None = None) -> float | None:
    """The threshold to run: the one in force (current) while its score stays within BAND of the best, else the best;
    with none in force, the highest within BAND of the best (see BAND)."""
    if not scores:
        return None
    top = max(scores.values())
    cur = next((t for t in scores if current is not None and abs(t - current) < 1e-9), None)
    if cur is not None:
        return cur if scores[cur] >= top - BAND else max(scores, key=lambda t: (scores[t], t))
    return max(t for t, s in scores.items() if s >= top - BAND)


def run(now: float | None = None, board: dict | None = None) -> dict:
    """objective.json from the board (fetched when not given), our purchases and the scoreboard history."""
    now = now or time.time()
    prev = common.read_json(folder() / "objective.json", None)          # the threshold in force (11/10)
    current = prev.get("buyer_min_share") if isinstance(prev, dict) and now - (prev.get("at") or 0) < 2 * 3600 else None
    if board is None:
        import analysis
        board = analysis.market_get("/api/board") or {}
    shops, buyers, players, key, label = scoreboard.view(board)
    me_b = next((b for b in buyers if common.is_ours(b.get("name") or "")), None)
    me_s = next((s for s in shops if common.is_ours(s.get("name") or "")), None)
    out = {"at": int(now), "view": label, "lambda": {}, "buyer_min_share": None, "options": {}, "basis": {}}
    sw, bw = scoreboard.weights()                     # the board's formula in force (refit after a patch)
    for rows, me, weights in ((shops, me_s, sw), (buyers, me_b, bw)):
        if not me:
            continue
        for part, v in scoreboard.parts(me, rows, weights).items():
            steps = v["next"][:2]
            need, gain = sum(s["need"] for s in steps), sum(s["gain"] for s in steps)
            out["lambda"][part] = round(gain / need, 12) if need else 0.0
    if me_b and "saved" in bw:
        bought = common.read_jsonl(common.MARKET_DIR / "buyer" / "purchases.jsonl")
        broken = common.read_jsonl(common.MARKET_DIR / "buyer" / "breaks.jsonl") + \
            [{"order": o} for o in breakage.our_broken(common.read_json(breakage.path(), None))]   # 11/7: by order, the ledger's
        bought = [p for p in bought if (p.get("settled_at") or 0) >= now - WINDOW]
        orders = common.read_jsonl(common.MARKET_DIR / "records" / "orders.jsonl", since=now - WINDOW)
        recent, orders_u = assigned(bought, broken, orders)
        if not orders_u:                                    # no order records (tests, a fresh start): the purchases alone
            recent, orders_u = shares(bought, broken), None
        hours_left = max(0.0, (common.ROUND_END - now) / 3600)
        then = scoreboard.history_since(now)
        h_then = (now - then["at"]) / 3600 if then else 0
        idx = list(bw).index("deals") if "deals" in bw else None
        others = []
        for b in buyers:
            if b is me_b:
                continue
            before = ((then or {}).get("buyers") or {}).get(b["name"]) if idx is not None else None
            pace = (scoreboard.num(b, "deals") - before[idx]) / h_then if before and h_then else \
                scoreboard.num(b, "deals") / max(1.0, (now - ROUND_START) / 3600)
            others.append((scoreboard.num(b, "saved"), scoreboard.num(b, "deals") + max(0.0, pace) * hours_left))
        out["basis"] = {"purchases_in_window": len(recent), "hours_left": round(hours_left), "pace_from": "history" if then else "average",
                        "orders_in_window": len(orders_u) if orders_u is not None else None,
                        "orders_by_threshold": {str(u): c for u, c in sorted(Counter(orders_u or []).items())}}
        if len(recent) >= MIN_DEALS:
            best, scores = choose({"saved": scoreboard.num(me_b, "saved"), "deals": scoreboard.num(me_b, "deals")},
                                  others, recent, hours_left, WINDOW / 3600, bw.get("saved", 0), bw.get("deals", 0), orders_u,
                                  current)
            out["options"] = {str(t): s for t, s in scores.items()}
            out["buyer_min_share"] = min(CLAMP[1], max(CLAMP[0], best)) if best is not None else None
            if out["buyer_min_share"] is not None:
                out["probe"] = {"share": PROBE_SHARE, "min_share": round(max(CLAMP[0], out["buyer_min_share"] - PROBE_DROP), 2)}
    common.write_json(folder() / "objective.json", out)
    return out


def brief(now: float) -> dict | None:
    o = common.read_json(folder() / "objective.json", None)
    if not isinstance(o, dict):
        return None
    return {k: o.get(k) for k in ("at", "view", "buyer_min_share", "probe", "options", "lambda", "basis")}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
