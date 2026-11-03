"""v1.19 next-hour category forecasts, pure functions of the graph's settles with ts < t.

  f = forecast(G, t, catalog)   # {"A": {cat: p}, "B": {cat: p}, "buyers", "lam", "lam_new"}, None before any data

A (taste): sum over the buyers active in the last WINDOW_H hours of lam_b * theta_b,c, plus a new-buyer residual
lam_new at the market share. lam_b = b's settles in the last WINDOW_H h / WINDOW_H (expected orders next hour).
theta_b,c = (count_b,c + ALPHA * share_c) / (n_b + ALPHA): b's current-trajectory counts (Graph.buyer_taste)
Dirichlet-smoothed toward the market share. lam_new = (buyers whose first settle is in the window + settles with no
buyer in the window) / WINDOW_H. share_c = B before the floor.
B (baseline = restock's score today): v_c = sum exp(-(t - ts) / TAU_H) / TAU_H over every market settle, i.e.
aggregates velocity_per_h + policy own_velocity (both tau 24 h; the graph keeps our shop's settles too).
Both are normalized over the catalog's categories plus the ones seen, with an EPS floor: p = (q + EPS) / (1 + K EPS);
no mass at all -> uniform. A refunded settle counts until its refund ts (Graph._valid).
"""
import math

from memory import ABSTAIN

ALPHA = 2.0
EPS = 1e-3
WINDOW_H = 24.0
TAU_H = 24.0


def settles(G, t):
    """Market settle observations valid at t (ts < t, not refunded before t)."""
    return [o for o in G.obs.values() if o["ts"] < t and (o["to"] is None or o["to"] >= t)]


def categories(catalog, obs=()):
    cats = {(x or {}).get("category") for x in (catalog or {}).values()} | {o["cat"] for o in obs}
    return sorted(c for c in cats if c)


def normalize(raw, cats, eps=EPS):
    s = sum(max(0.0, raw.get(c, 0.0)) for c in cats)
    k = len(cats)
    q = {c: (max(0.0, raw.get(c, 0.0)) / s if s > 0 else 1.0 / k) for c in cats}
    return {c: (q[c] + eps) / (1 + k * eps) for c in cats}


def velocity(obs, t, tau_h=TAU_H):
    v = {}
    for o in obs:
        if o["cat"]:
            v[o["cat"]] = v.get(o["cat"], 0.0) + math.exp(-(t - o["ts"]) / (tau_h * 3600)) / tau_h
    return v


def forecast(G, t, catalog, alpha=ALPHA, eps=EPS, window_h=WINDOW_H, tau_h=TAU_H):
    obs = settles(G, t)
    act = G.active_buyers(t - window_h * 3600, at=t)
    if act == ABSTAIN or not obs:
        return None
    cats = categories(catalog, obs)
    vel = velocity(obs, t, tau_h)
    share = normalize(vel, cats, eps=0.0)
    raw, lam_all = dict.fromkeys(cats, 0.0), 0.0
    for b, n in sorted(act.items()):
        lam = n / window_h
        taste = G.buyer_taste(b, at=t)
        taste = taste if isinstance(taste, dict) else {}
        nb = sum(taste.values())
        for c in cats:
            raw[c] += lam * (taste.get(c, 0) + alpha * share[c]) / (nb + alpha)
        lam_all += lam
    since = t - window_h * 3600
    first = {}
    for o in obs:
        if o["buyer"]:
            first[o["buyer"]] = min(first.get(o["buyer"], o["ts"]), o["ts"])
    new = sum(1 for ts in first.values() if ts >= since) + sum(1 for o in obs if not o["buyer"] and o["ts"] >= since)
    lam_new = new / window_h
    for c in cats:
        raw[c] += lam_new * share[c]
    return {"A": normalize(raw, cats, eps), "B": normalize(vel, cats, eps), "buyers": len(act),
            "lam": round(lam_all, 6), "lam_new": round(lam_new, 6)}
