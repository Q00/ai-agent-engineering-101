"""M2b replayer: more keys, a demand response for price, walk-forward windows. Read-only on v120c; no network.

Walk-forward: a decision at t_d is judged only on events in [t_d, t_d + H). Anything the RULE estimates
(price elasticity) is fitted only on data with ts < t_d. The EVALUATOR (how buyers/shops would have reacted:
buyer_replay shop CDF within +-12 h, replay.Eval warranty calibration) is the environment model and may use the
full records - stated as a limit, not hidden.

Keys (see KEYS_STATUS for the reason of every key):
  price_mult        demand model: logistic P(settle | open) on log(ask/list) per kind, fitted before t_d [estimate]
  open_frac         buyer: paid ratio shifts by the open change; lower -> renegotiate P = F(r1)/F(r0), higher -> pays more
  max_pay_frac      buyer: lower only (buyer_replay.lever_cap logic); raise -> not replayable
  min_margin        shop: raise -> sales below the new floor are lost (one-sided, conservative); lower -> 0 credit
  warranty_on / warranty_exposure_cap_6h   replay.f_warranty(both) with a rolling 6 h exposure cap (placeholder keys)
"""
import math
import random
from bisect import bisect_left
from collections import defaultdict

import numpy as np

from rlpo import config as C

KEYS_STATUS = {
    "price_mult": "modelled: logistic demand on log(ask / market ref) with grade and list-price controls [estimate], walk-forward fit; unsupported when the slope CI before t_d is not < 0",
    "open_frac": "modelled: shift of paid ratio [estimate], evaluator = other buyers shop CDF (+-12 h)",
    "max_pay_frac": "modelled for a LOWER cap only; a raise is not replayable (failed negotiations have no recorded final ask)",
    "min_margin": "modelled one-sided: a raise loses sales below the new floor; a lower floor gets 0 credit (rejected offers below floor are not recorded)",
    "warranty_on": "modelled: replay.f_warranty(both, lam) + 6 h exposure cap; key stays enabled=false (A_for_RLPO.md)",
    "warranty_exposure_cap_6h": "modelled with warranty_on",
    "step_frac": "NOT modelled: per-negotiation rounds and offers of our buyer are not in the records (metrics has deal/price/tried only)",
    "concession": "NOT modelled: seller round-by-round counters are not recorded (opens carry ask/floor only)",
    "max_rounds": "NOT modelled: same reason (no round counts per negotiation), shop and buyer",
    "stock_target": "NOT modelled yet (priority 3): needs per-category supply draws; replay.f_restock models a SKU swap, not quantity",
    "qty_per_buy": "NOT modelled yet: same as stock_target",
}


def _sig(x):
    return 1.0 / (1.0 + math.exp(-x))


# ---------------- price elasticity ------------------------------------------------------------------------------
def open_rows(rec, n_ref=20, min_ref=3):
    """one row per open of our seller with a market reference: ts, kind, f = controls [food, goods, grade B,
    grade C, log list], x = log(ask / median of the last n_ref OTHER-shop settle amounts of the same SKU before the
    open), y = 1 if this open led to the unit sale. Opens without >= min_ref earlier references are dropped.
    (M2b r1 used x = log(ask/list) with no controls and got a POSITIVE slope = confounding; kept in RESULTS.)"""
    from bisect import bisect_left as bl
    sale_ts = {s["unit"]: s["ts"] for s in rec.sales if s.get("unit")}
    ref = defaultdict(list)
    for m in rec.market:
        if not m.get("ours") and m.get("amount"):
            ref[m["sku"]].append((m["ts"], m["amount"]))
    for v in ref.values():
        v.sort()
    by_unit = defaultdict(list)
    for o in rec.opens:
        by_unit[o["unit"]].append(o)
    rows = []
    for u, os_ in by_unit.items():
        os_.sort(key=lambda o: o["ts"])
        st = sale_ts.get(u)
        info = rec.units.get(u) or {}
        lp = info.get("list_price") or (rec.cat.get(os_[0]["sku"]) or {}).get("list_price")
        if not lp:
            continue
        g = info.get("grade")
        for i, o in enumerate(os_):
            nxt = os_[i + 1]["ts"] if i + 1 < len(os_) else float("inf")
            y = 1 if (st is not None and o["ts"] <= st < nxt) else 0
            v = ref.get(o["sku"], [])
            j = bl(v, (o["ts"],))
            prev = sorted(a for _, a in v[max(0, j - n_ref):j])
            if not (o.get("ask") and o["ask"] > 0) or len(prev) < min_ref:
                continue
            med = prev[len(prev) // 2]
            kind = o.get("kind") or info.get("kind")
            f = [1.0 if kind == "food" else 0.0, 0.0 if kind == "food" else 1.0, 1.0 if g == "B" else 0.0,
                 1.0 if g == "C" else 0.0, math.log(lp)]
            rows.append({"ts": o["ts"], "kind": kind, "f": f, "x": math.log(o["ask"] / med), "y": y,
                         "category": info.get("category"), "ask": o["ask"], "cost": o.get("cost") or info.get("cost") or 0,
                         "list": lp})
    rows.sort(key=lambda r: r["ts"])
    return rows


def _eta(beta, r):
    return float(sum(b * f for b, f in zip(beta[:-1], r["f"])) + beta[-1] * r["x"])


def fit_logit(rows, iters=60, ridge=1e-3):
    """IRLS on f + [x] -> (beta, n). beta[-1] = slope on log(ask / market reference)."""
    if len(rows) < 30:
        return None, len(rows)
    X = np.array([list(r["f"]) + [r["x"]] for r in rows])
    y = np.array([r["y"] for r in rows], dtype=float)
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ b))
        W = p * (1 - p) + 1e-9
        H = X.T @ (X * W[:, None]) + ridge * np.eye(X.shape[1])
        step = np.linalg.solve(H, X.T @ (y - p) - ridge * b)
        b = b + step
        if np.max(np.abs(step)) < 1e-8:
            break
    return b, len(rows)


def elasticity_report(rows, t_d=None, n_boot=200, seed=C.SEED):
    """slope b and conversion elasticity e = b (1 - mean p), with an hour-block bootstrap 95 % CI. [estimate]"""
    rs = [r for r in rows if t_d is None or r["ts"] < t_d]
    b, n = fit_logit(rs)
    if b is None:
        return {"n": n, "slope": None}
    t0 = rs[0]["ts"]
    blocks = defaultdict(list)
    for r in rs:
        blocks[int((r["ts"] - t0) // 3600)].append(r)
    keys = sorted(blocks)
    rng = random.Random(seed)
    sl, el = [], []
    for _ in range(n_boot):
        samp = [r for k in (rng.choice(keys) for _ in keys) for r in blocks[k]]
        bb, _ = fit_logit(samp)
        if bb is None:
            continue
        p = np.array([_sig(_eta(bb, r)) for r in samp])
        sl.append(bb[-1])
        el.append(bb[-1] * (1 - p.mean()))
    p = np.array([_sig(_eta(b, r)) for r in rs])
    sl.sort()
    el.sort()
    q = lambda xs, a: xs[int(a * (len(xs) - 1))]
    if not sl:
        sl, el = [float(b[-1])], [float(b[-1] * (1 - p.mean()))]
    return {"n": n, "n_hours": len(keys), "slope": float(b[-1]), "slope_ci": [q(sl, .025), q(sl, .975)],
            "elasticity": float(b[-1] * (1 - p.mean())), "elasticity_ci": [q(el, .025), q(el, .975)],
            "conv_mean": float(p.mean()), "beta": [float(x) for x in b], "n_boot": len(sl), "tag": "[estimate]"}


def d_price(rows, beta, base_pm, cand_pm, a, b):
    """per open in [a, b): expected GP change when the ask is scaled by k = cand/base. Final price = ask x rho,
    rho = median(sale amount / ask) is folded into the ask (the final/ask ratio is kept)."""
    out = []
    for r in rows:
        if not a <= r["ts"] < b:
            continue
        kb, kc = base_pm(r), cand_pm(r)
        if kb is None or kc is None or kb == kc:
            continue
        k = kc / kb
        eta = _eta(beta, r)
        p0, p1 = _sig(eta), _sig(eta + beta[-1] * math.log(k))
        out.append((r["ts"], p1 * (r["ask"] * k - r["cost"]) - p0 * (r["ask"] - r["cost"])))
    return out


# ---------------- other keys ------------------------------------------------------------------------------------
def d_min_margin(sales, m0, m1, a, b):
    if m1 <= m0:
        return []          # lower floor: no credit (one-sided)
    out = []
    for s in sales:
        if a <= s["ts"] < b and s["amount"] < s["cost"] * (1 + m1) and s["amount"] >= s["cost"] * (1 + m0) - 1:
            g = -s["cost"] if s["refunded"] else s["amount"] - s["cost"]
            out.append((s["ts"], -g))
    return out


def d_open(buys, idx, BR, o0, o1, a, b):
    out = []
    dlt = o1 - o0
    if dlt == 0:
        return out
    for x in buys:
        if not a <= x["ts"] < b:
            continue
        xs = BR.eval_F(idx, x)
        if xs is None:
            continue
        r1 = max(0.0, x["r0"] + dlt)
        if dlt < 0:
            if BR.cdf(xs, r1) >= BR.cdf(xs, x["r0"]):   # M2r C5: P = 1 = no observed ratio in [r1, r0) -> no evidence, left out
                continue
            out.append(BR.renegotiate(x, r1, xs, [], "open")[:2])
        else:
            out.append((x["ts"], -(r1 - x["r0"]) * x["list"]))
    return out


def d_cap(buys, idx, BR, c0, c1, a, b):
    if c1 >= c0:
        return []
    out = []
    for x in buys:
        if a <= x["ts"] < b and x["r0"] > c1:
            xs = BR.eval_F(idx, x)
            if xs is not None and BR.cdf(xs, c1) < BR.cdf(xs, x["r0"]):   # M2r C5: drop P = 1 (no evidence)
                out.append(BR.renegotiate(x, c1, xs, [], "cap")[:2])
    return out


def d_warranty(wdeltas, wlog, unit_price, on, cap, a, b):
    """wdeltas/wlog = replay.f_warranty(both) output and log. on = False -> []; additions are skipped once the
    rolling 6 h exposure (sum of added-warranty prices) would exceed cap."""
    if not on:
        return []
    out, added = [], []
    for (ts, d, *_), lg in zip(wdeltas, wlog):
        if not a <= ts < b:
            continue
        if lg["h_act"] == 0 and lg["h_new"] > 0:
            pr = unit_price.get(lg["unit"], 0)
            added = [(t, p) for t, p in added if ts - t < 6 * 3600]
            if sum(p for _, p in added) + pr > cap:
                continue
            added.append((ts, pr))
        out.append((ts, d))
    return out
