"""The numbers every meta-harness cycle starts from, written where a change can cite them line by line.

    python scorecard.py        prints one

Counts come from the logs and files as they are; nothing here judges. Astra owns this file and the grading.
"""
import collections
import json
import os
import time

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
ME = os.environ.get("SHOP_MARKET_NAME", "nocha12")
COUNTERS = {"settled_as_shop": "[acp] settled", "paid_as_buyer": "[acp] paid", "buyer_refused_to_pay": "[acp] not paying",
            "checkout_refused": "checkout refused", "settle_refused": "settle refused", "supply": "[supply]",
            "supply_refused": "refused:", "tracebacks": "Traceback", "harness_failed": "harness failed",
            "no_stock": "REJECTED (not in stock)", "bankrupt": "[bankrupt]", "broken": "[broken]", "forum": "[forum]"}
SOURCES = ("logs", "memory", ".market", "meta")
# The midterm's Round 3 rank (week 9, FIG. 07): each part a percentile among shops or buyers, Players = shop ALL + buyer ALL.
OBJECTIVE = {"shops": [(("profit",), 0.5), (("revenue",), 0.3), (("stars",), 0.2)],
             "buyers": [(("surplus", "saved"), 0.5), (("forum", "forum_score", "votes", "score"), 0.3), (("deals",), 0.2)]}


def percentile(rows, field, mine):
    vals = [r[field] for r in rows if isinstance(r.get(field), (int, float)) and not isinstance(r.get(field), bool)]
    if not vals or not isinstance(mine.get(field), (int, float)):
        return None
    return round(sum(v <= mine[field] for v in vals) / len(vals), 3)


def objective(b):
    """My estimated shop ALL, buyer ALL and Players from the board's rows: what every change is meant to raise. A part the
    board does not show yet is None and left out of the sum (the weights of the parts shown are kept as they are)."""
    out = {}
    for k, parts in OBJECTIVE.items():
        rows = [r for r in b.get(k) or [] if r.get("deals")]
        mine = next((r for r in b.get(k) or [] if r.get("name") == ME or ((r.get("owner") or {}).get("login") or "").lower() == "nocha12"), None)
        if not mine:
            out[k] = {"all": None, "why": "not on the board yet (no deal)"}
            continue
        got = {}
        for names, w in parts:
            field = next((n for n in names if n in mine), names[0])
            got[field] = {"weight": w, "percentile": percentile(rows, field, mine), "value": mine.get(field)}
        out[k] = {"all": round(sum(x["weight"] * x["percentile"] for x in got.values() if x["percentile"] is not None), 3),
                  "parts": got, "of": len(rows)}
    shop, buyer = out["shops"].get("all"), out["buyers"].get("all")
    out["players"] = None if shop is None and buyer is None else round((shop or 0) + (buyer or 0), 3)
    out["weakest"] = min(((k, f, x["percentile"]) for k in OBJECTIVE for f, x in (out[k].get("parts") or {}).items()
                          if x["percentile"] is not None), key=lambda t: t[2], default=None)
    return out


def jsonl(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip().startswith("{")]
    except (OSError, ValueError):
        return []


def board():
    """My rows on the board, and where they stand. The board's fields beyond week 7's show up as they are."""
    try:
        b = httpx.get(f"{MARKET}/api/board", timeout=20).json()
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = {"round": b.get("round"), "totals": b.get("totals"), "objective": objective(b)}
    for k in ("shops", "buyers", "players"):
        rows = b.get(k) or []
        mine = next((r for r in rows if r.get("name") == ME or (r.get("login") or "").lower() == "nocha12"), None)
        out[k] = {"count": len(rows), "mine": {x: v for x, v in (mine or {}).items() if x not in ("card", "owner")}}
    return out


def file_index(root):
    """Every log and record the cycle may read, with its size: the meta-harness reads the raw files it wants."""
    out = {}
    for top in SOURCES:
        for base, _, names in os.walk(os.path.join(root, top)):
            if os.path.join("meta", "evidence") in base:
                continue
            for n in names:
                p = os.path.join(base, n)
                out[os.path.relpath(p, root)] = os.path.getsize(p)
    return out


def log_counts(root):
    counts = collections.Counter()
    try:
        with open(os.path.join(root, "logs", "market-kit.log"), encoding="utf-8", errors="replace") as f:
            for line in f:
                for k, needle in COUNTERS.items():
                    if needle in line:
                        counts[k] += 1
                counts["lines"] += 1
    except OSError:
        pass
    return {k: counts[k] for k in ["lines", *COUNTERS]}


def llm_table(md, last=2000):
    """What each kind of model call costs lately: how often, how large, how slow, how many failed."""
    table = collections.defaultdict(lambda: {"calls": 0, "failed": 0, "chars": 0, "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0})
    for c in jsonl(os.path.join(md, "llm_calls.jsonl"))[-last:]:
        t = table[c.get("tag")]
        t["calls"] += 1
        t["failed"] += 0 if c.get("ok") else 1
        t["chars"] += c.get("chars") or 0
        t["seconds"] += c.get("seconds") or 0
        for k in ("prompt_tokens", "completion_tokens", "cost"):
            t[k] += c.get(k) or 0
    return {k: {**v, "mean_chars": round(v["chars"] / v["calls"]), "mean_seconds": round(v["seconds"] / v["calls"], 2)} for k, v in table.items()}


def memory_timing(md, last=500):
    """Latest size and mean milliseconds of each memory operation: does the memory slow the agent as it grows?"""
    rows, out = jsonl(os.path.join(md, "timing.jsonl"))[-last:], {}
    for r in rows:
        x = out.setdefault(r.get("what"), {"n": 0, "ms": 0, "bytes": 0})
        x["n"] += 1
        x["ms"] += r.get("ms") or 0
        x["bytes"] = r.get("bytes") or 0
    return {k: {"mean_ms": round(v["ms"] / v["n"]), "bytes": v["bytes"], "n": v["n"]} for k, v in out.items()}


def build(root):
    md = os.path.join(root, ".market")
    try:
        with open(os.path.join(md, "inventory.json"), encoding="utf-8") as f:
            inv = json.load(f)
    except (OSError, ValueError):
        inv = {}
    units = inv.get("units") or []
    calls, decisions, pays = (jsonl(os.path.join(md, f)) for f in ("tool_calls.jsonl", "decisions.jsonl", "payments.jsonl"))
    tools = collections.defaultdict(lambda: {"calls": 0, "failed": 0})
    for c in calls:
        tools[c.get("tool")]["calls"] += 1
        tools[c.get("tool")]["failed"] += 0 if c.get("ok") else 1
    return {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "board": board(),
            "shop": {"balance": inv.get("balance"), "profit_if_stock_is_worthless":
                     None if inv.get("balance") is None else inv["balance"] - (inv.get("start") or 0) - (inv.get("injected") or 0),
                     "units": len(units), "stock_cost": round(sum(u.get("cost") or 0 for u in units))},
            "log_counts": log_counts(root),
            "llm": llm_table(md), "memory_timing": memory_timing(md),
            "tools": dict(tools), "decisions": len(decisions), "last_decisions": decisions[-5:],
            "payments": {"paid": sum(1 for p in pays if p.get("paid")), "refused": sum(1 for p in pays if not p.get("paid")),
                         "refusal_reasons": collections.Counter(str(p.get("reason"))[:80] for p in pays if not p.get("paid")).most_common(10)},
            "files": file_index(root)}


if __name__ == "__main__":
    print(json.dumps(build(os.path.dirname(os.path.abspath(__file__))), ensure_ascii=False, indent=1))
