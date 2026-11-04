"""State summarizer: pure function of already-read inputs (no I/O, no network).
Sources (DESIGN_v1 sec 1): balance/inventory from events + inventory; price/velocity from policy.demand_ref /
own_velocity; score components from a box copy of the board; memory from memory/graph.py active_edges."""
import hashlib
import json

SCORE_KEYS = ("profit", "revenue", "stars_rank", "surplus", "forum", "deals", "posts", "total")


def summarize(t, balance, inventory, market, score, memory=(), policy_sha="", t_rem=None,
              score_t=None, board_refresh_s=None, max_mem=10):
    """inventory: {sku: {qty, hours_left}}; market: {sku: {price_ref, velocity_h}}; score: {key: pct};
    memory: [{kind, key, a, b}]. A score older than 2 x board_refresh_s is marked stale."""
    stale = None
    if score_t is not None and board_refresh_s:
        stale = (t - score_t) > 2 * board_refresh_s
    s = {
        "t": t,
        "t_rem": t_rem,
        "balance": balance,
        "inventory": {k: {"qty": v.get("qty"), "hours_left": v.get("hours_left")}
                      for k, v in sorted((inventory or {}).items())},
        "market": {k: {"price_ref": v.get("price_ref"), "velocity_h": v.get("velocity_h")}
                   for k, v in sorted((market or {}).items())},
        "score": {k: (score or {}).get(k) for k in SCORE_KEYS},
        "score_t": score_t,
        "score_stale": stale,
        "memory": [{k: m.get(k) for k in ("kind", "key", "a", "b")} for m in list(memory)[:max_mem]],
        "policy_sha": policy_sha,
    }
    return s


def sha(s):
    return hashlib.sha256(json.dumps(s, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
