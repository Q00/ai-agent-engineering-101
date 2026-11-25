"""Jev-style router (user 11/10 21h; week-04 Jev: a classifier that takes a state and questions and returns
calibrated probabilities, as one cheap call instead of a full LLM call). Built here without a model: the answers
are the frequencies our own trades showed in states like this one, shrunk toward coarser states when a state is rare
(hierarchical backoff), so a probability of 0.3 means about 3 in 10 such turns went that way. A lookup, < 1 ms.

    py -m harness.jev fit           # refit from the last 2 days of trajectories and routes (the runner: every 30 min)
    py -m harness.jev show          # the tables' sizes and the states with no tool that fits

Questions per role and tool (design: they come from the score, not from 'did it close'):
- seller: settle (the visit settles), walk (the buyer leaves: canceled / abandoned), value (per settled visit,
  (0.5 margin + 0.3 price) / list, shop ALL's book and revenue weights). EV = settle x value x list price.
route() picks the tool with the highest EV among the tools it knows; EXPLORE of the turns go to another tool (so
every tool keeps being measured); when even the best tool's settle probability is under NO_FIT_P on a state seen
NO_FIT_N times, the turn is still played by the best tool and the state is written as 'no tool fits' - the forge
queue (harness.forge) reads those states to make a new tool.
"""
import json
import math
import random
import sys
import time

from core import atomic, paths
from harness import out_dir

PRIOR = 10                    # shrinkage weight toward the parent state
EXPLORE = 0.1
NO_FIT_P = 0.12
NO_FIT_N = 40
QUESTIONS = {"seller": ("settle", "walk", "value")}
DEFAULT_TOOL = {"seller": "trade.seller_rules"}
KNOWN_AT = 0.5              # agents.seller_core.KNOWN_AT: a guess this sure counts as a known buyer


def _path(role):
    return out_dir() / "jev" / f"{role}.json"


def band(lp):
    lp = lp or 0
    return "<30k" if lp < 30000 else "<100k" if lp < 100000 else "<300k" if lp < 300000 else "<1M" if lp < 1e6 else "1M+"


def gap(offer, ask):
    if offer is None or not ask:
        return "none"
    r = offer / ask
    return "<0.8" if r < 0.8 else "<0.9" if r < 0.9 else "<0.97" if r < 0.97 else "0.97+"


def seller_view(kind, list_price, turn, offer, ask, known):
    return {"kind": kind or "?", "band": band(list_price), "turn": min(int(turn or 1), 4), "gap": gap(offer, ask),
            "known": bool(known), "list_price": list_price}


def keys(view):
    """From the finest state to the coarsest."""
    k, b, t, g, n = view["kind"], view["band"], view["turn"], view["gap"], view["known"]
    return [f"{k}|{b}|{t}|{g}|{n}", f"{k}|{b}|{t}|{g}", f"{k}|{b}|{t}", f"{k}|{b}", f"{k}", "*"]


# --- fitting

def _seller_samples(days):
    from harness import trajectory
    from tools import market_files as mf
    from tools.registry import Ctx
    ctx = Ctx(caller="jev")
    cat = mf.catalog(ctx)
    u2s = {u["unit"]: u["sku"] for u in mf.inventory(ctx).get("units", []) if u.get("unit")}
    out = []
    for day in days:
        for t in trajectory.build(day).values():
            if t["role"] != "seller":
                continue
            r = t["result"]
            if r.get("unit") and r.get("sku"):
                u2s[r["unit"]] = r["sku"]
            settled = bool(r.get("settled"))
            walk = r.get("outcome") in ("canceled", "abandoned")
            # 11/10 21:4x: counterpart.sure is set after the visit (a settled deal names its buyer) and leaked the
            # outcome (every 'known' state settled 100%); the guess the seller had at the first message is used
            known = False
            offer, prev, k = None, None, 0
            rows = []
            for s in t["steps"]:
                if s.get("kind") == "tool.result" and s.get("tool") == "calc.guess_counterpart" and k == 0:
                    try:
                        top = (json.loads(s.get("summary") or "{}").get("candidates") or [{}])[0]
                    except ValueError:
                        top = {}
                    known = (top.get("confidence") or 0) >= KNOWN_AT
                elif s.get("kind") == "tool.result" and s.get("tool") == "calc.extract":
                    try:
                        p = json.loads(s.get("summary") or "{}").get("price")
                    except ValueError:
                        p = None
                    if p is not None:
                        offer = p
                elif s.get("kind") == "seller.decision":
                    k += 1
                    rows.append((k, offer, prev, s.get("unit"), s.get("by") or "rules", s.get("tool")))
                    prev = s.get("price")
            for k, off, ask, unit, by, tool in rows:
                sku = r.get("sku") or u2s.get(unit)
                c = cat.get(sku) or {}
                if not c.get("list_price"):
                    continue
                v = seller_view(c.get("kind"), c["list_price"], k, off, ask, known)
                val = None
                if settled and r.get("price") is not None and r.get("cost") is not None:
                    val = (0.5 * (r["price"] - r["cost"]) + 0.3 * r["price"]) / c["list_price"]
                out.append({"tool": tool or DEFAULT_TOOL["seller"], "view": v, "settle": settled, "walk": walk,
                            "value": val})
    return out


def fit(role="seller", days=None, now=None):
    now = now or time.time()
    days = days or sorted({time.strftime("%Y%m%d", time.localtime(now - h * 3600)) for h in (0, 24)})
    samples = _seller_samples(days)
    tables = {}
    for s in samples:
        tb = tables.setdefault(s["tool"], {})
        for k in keys(s["view"]):
            c = tb.setdefault(k, {"n": 0, "settle": 0, "walk": 0, "vn": 0, "vsum": 0.0})
            c["n"] += 1
            c["settle"] += s["settle"]
            c["walk"] += s["walk"]
            if s["value"] is not None:
                c["vn"] += 1
                c["vsum"] += s["value"]
    doc = {"ts": now, "role": role, "days": days, "samples": len(samples), "tables": tables}
    atomic.write_json(_path(role), doc)
    _CACHE.pop(role, None)
    return {"samples": len(samples), "tools": {t: tb.get("*", {}).get("n") for t, tb in tables.items()}}


# --- asking

_CACHE = {}


def _tables(role):
    c = _CACHE.get(role)
    p = _path(role)
    mt = p.stat().st_mtime if p.exists() else 0
    if c is None or c[0] != mt:
        c = _CACHE[role] = (mt, (atomic.read_json(p, default=None) or {}).get("tables") or {})
    return c[1]


def ask(role, view, tools=None):
    """{tool: {settle, walk, value, ev, n}} - probabilities shrunk from the coarsest state to the finest."""
    tables = _tables(role)
    out = {}
    for tool in tools or list(tables) or [DEFAULT_TOOL[role]]:
        tb = tables.get(tool) or {}
        p = {"settle": 0.1, "walk": 0.5, "value": 0.2}
        n = 0
        for k in reversed(keys(view)):
            c = tb.get(k)
            if not c:
                continue
            p = {"settle": (c["settle"] + PRIOR * p["settle"]) / (c["n"] + PRIOR),
                 "walk": (c["walk"] + PRIOR * p["walk"]) / (c["n"] + PRIOR),
                 "value": (c["vsum"] + PRIOR * p["value"]) / (c["vn"] + PRIOR)}
            n = c["n"]
        out[tool] = {**{q: round(v, 4) for q, v in p.items()}, "n": n,
                     "ev": round(p["settle"] * p["value"] * (view.get("list_price") or 0))}
    return out


def route(role, view, tools, rng=random.random):
    """(tool, answers, note): the tool to play this turn with. note: 'explore', 'no_fit' or None."""
    ans = ask(role, view, tools)
    best = max(tools, key=lambda t: ans[t]["ev"])
    if len(tools) > 1 and rng() < EXPLORE:
        return random.choice([t for t in tools if t != best]), ans, "explore"
    if ans[best]["settle"] < NO_FIT_P and ans[best]["n"] >= NO_FIT_N:
        return best, ans, "no_fit"
    return best, ans, None


def no_fit_states(role="seller", min_n=NO_FIT_N):
    """The states where no tool we have settles NO_FIT_P of the time - what the forge should make a tool for."""
    tables = _tables(role)
    rows = []
    for k in {k for tb in tables.values() for k in tb if k.count("|") == 4}:
        best = max((tb[k]["settle"] / tb[k]["n"], t, tb[k]) for t, tb in tables.items() if k in tb)
        if best[2]["n"] >= min_n and best[0] < NO_FIT_P:
            rows.append({"state": k, "best_tool": best[1], "settle": round(best[0], 3), "n": best[2]["n"],
                         "walk": round(best[2]["walk"] / best[2]["n"], 3)})
    return sorted(rows, key=lambda r: -r["n"])


if __name__ == "__main__":
    if sys.argv[1:2] == ["fit"]:
        print(json.dumps(fit(), ensure_ascii=False))
    else:
        t = _tables("seller")
        print(json.dumps({"tools": {k: len(v) for k, v in t.items()}, "no_fit": no_fit_states()[:15]},
                         ensure_ascii=False, indent=1))
