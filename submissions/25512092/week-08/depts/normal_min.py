"""Normal goods lines in every run (person's decision 11/11 ~20:50): each plan the kit sends (one run, at most
MAX_LINES lines; both halves of a window's 2 x MAX_LINES plan) has at least min_lines (4) lines of normal goods
(est. cost <= max_cost, 100,000) when there are such candidates; off while more than release_skus (70) normal SKUs are
held. Rules in normal_min.json (read every run; off without it).
  reserve  line_rank.pick takes the best-scored normal candidates first, up to what is still missing after the request
           lines, then the rest by score as before
  arrange  per run-sized chunk of the plan, its share of the plan's normal lines (4 each, in plan order) goes first,
           then the other lines in plan order, so a run cut to the window's
           close keeps them
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "normal_min.json")


def rules(path=None):
    try:
        with open(path or RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def normal_skus(units, max_cost):
    return {u["sku"] for u in units if u.get("grade") != "expired" and (u.get("cost") or 0) <= max_cost}


def active(units, log=print, r=None):
    """The rules while on (enabled, normal SKUs held <= release_skus), else None."""
    r = rules() if r is None else r
    if not r.get("enabled"):
        return None
    r = dict({"min_lines": 4, "max_cost": 100_000, "release_skus": 70}, **r)
    n = len(normal_skus(units, r["max_cost"]))
    if n > r["release_skus"]:
        log(f"[normal min] off: {n} normal SKUs held > {r['release_skus']}")
        return None
    log(f"[normal min] at least {r['min_lines']} normal lines a run ({n} normal SKUs held <= {r['release_skus']})")
    return r


def arrange(plan, is_low, size, need):
    """The plan with >= need normal lines in every size-line chunk where the plan has them, those first: chunk i gets
    the plan's normal lines need*i .. need*(i+1)-1, then the other lines in plan order."""
    import math
    c = max(1, math.ceil(len(plan) / size))
    lows = [p for p in plan if is_low(p["sku"])]
    res = [lows[i * need:(i + 1) * need] for i in range(c)]
    taken = {p["sku"] for r in res for p in r}
    others = [p for p in plan if p["sku"] not in taken]
    out = []
    for r in res:
        k = size - len(r)
        out += r + others[:k]
        others = others[k:]
    return out + others
