"""eval.*: every deterministic metric of HARNESS.md 3.3, computed from raw events (core/kinds.py).

All take since/until (unix seconds) or epoch to cut a slice, and return numbers with the sample size,
so a reader can tell a real change from noise. They read the L1 index (core.index: events filed by kind
and day, updated from a cursor), so a call opens only the kinds and days it needs.
"""
import json
import statistics

from core import atomic, index, paths
from tools.registry import Refused, tool

_SLICE = {"since": {"type": ["number", "null"]}, "until": {"type": ["number", "null"]},
          "epoch": {"type": ["string", "null"]}}
_SCOPE = {"type": "string", "enum": ["seller", "buyer", "restock", "review", "forum", "system"]}


def events(kinds=None, since=None, until=None, epoch=None):
    """Raw events of the given kinds in a slice, oldest first: from the L1 index (core.index), not a rescan of raw."""
    return index.events(kinds, since, until, epoch)


def _rate(n, d):
    return round(n / d, 4) if d else None


@tool("eval.funnel", "Where deals are lost. shop: requests -> negotiations closed -> completed -> settled. buyer: "
      "orders -> picked -> agreed -> paid -> useful. With the reasons at each step.",
      {"type": "object", "required": ["scope"], "properties": {"scope": _SCOPE, **_SLICE}})
def funnel(ctx, scope, since=None, until=None, epoch=None):
    if scope == "seller":
        ev = events(["seller.request", "seller.closed", "seller.settled"], since, until, epoch)
        tasks = {e["keys"].get("task") for e in ev if e["kind"] == "seller.request"}
        closed = [e for e in ev if e["kind"] == "seller.closed"]
        done = [e for e in closed if e["data"].get("outcome") == "completed"]
        settled = [e for e in ev if e["kind"] == "seller.settled"]
        lost = {}
        for e in closed:
            o = e["data"].get("outcome")
            if o != "completed":
                lost[o] = lost.get(o, 0) + 1
        return {"negotiations": len(tasks), "closed": len(closed), "completed": len(done), "settled": len(settled),
                "completion_rate": _rate(len(done), len(closed)), "settle_rate": _rate(len(settled), len(done)),
                "lost_by": lost}
    if scope == "buyer":
        ev = events(["buyer.order", "buyer.pick", "buyer.closed", "buyer.payment", "buyer.received"], since, until, epoch)
        count = lambda k: [e for e in ev if e["kind"] == k]
        closed, pay, rec = count("buyer.closed"), count("buyer.payment"), count("buyer.received")
        agreed = [e for e in closed if e["data"].get("outcome") == "completed"]
        paid = [e for e in pay if e["data"].get("paid")]
        useful = [e for e in rec if (e["data"].get("value") or 0) > 0]
        why_not = {}
        for e in pay:
            if not e["data"].get("paid"):
                r = (e["data"].get("reason") or "unknown")[:80]
                why_not[r] = why_not.get(r, 0) + 1
        return {"orders": len(count("buyer.order")), "picked": len(count("buyer.pick")), "agreed": len(agreed),
                "paid": len(paid), "useful": len(useful), "agree_rate": _rate(len(agreed), len(closed)),
                "pay_rate": _rate(len(paid), len(pay)), "unpaid_by": why_not}
    raise Refused(f"funnel has no scope {scope!r}", "use seller or buyer")


@tool("eval.unit_economics", "Per deal: our shop's margin (price - cost) and our buyer's surplus (value - price), with "
      "totals, medians and counts.",
      {"type": "object", "required": ["scope"], "properties": {"scope": _SCOPE, **_SLICE}})
def unit_economics(ctx, scope, since=None, until=None, epoch=None):
    if scope == "seller":
        s = [e["data"] for e in events(["seller.settled"], since, until, epoch)]
        m = [d["price"] - d["cost"] for d in s if d.get("price") is not None and d.get("cost") is not None]
        return {"deals": len(s), "revenue": sum(d.get("price") or 0 for d in s), "profit_before_losses": sum(m),
                "median_margin": statistics.median(m) if m else None, "below_cost_deals": sum(1 for x in m if x < 0),
                "exposure_cost": -sum(x for x in m if x < 0)}
    if scope == "buyer":
        r = [e["data"] for e in events(["buyer.received"], since, until, epoch)]
        br = [e["data"] for e in events(["buyer.broken"], since, until, epoch)]
        s = [d.get("surplus") or 0 for d in r]
        lost = sum(d.get("price") or 0 for d in br if d.get("matched") and not d.get("refund"))
        return {"deals": len(r), "surplus": sum(s) - lost, "surplus_before_breaks": sum(s),
                "median_surplus": statistics.median(s) if s else None,
                "worthless": sum(1 for d in r if not d.get("value")), "broken": len(br),
                "broken_matched": sum(1 for d in br if d.get("matched")), "lost_to_breaks": lost}
    raise Refused(f"unit_economics has no scope {scope!r}", "use seller or buyer")


@tool("eval.losses", "What our shop lost besides the cost of what it sold: expired food, storage, warranty refunds. "
      "Selling below cost is not here: it is exposure_cost in eval.unit_economics (scope=seller).",
      {"type": "object", "properties": _SLICE})
def losses(ctx, since=None, until=None, epoch=None):
    ev = events(["loss.expired", "loss.storage", "seller.refund"], since, until, epoch)
    tot = lambda k, f: sum(e["data"].get(f) or 0 for e in ev if e["kind"] == k)
    out = {"expired": tot("loss.expired", "cost"), "expired_units": sum(1 for e in ev if e["kind"] == "loss.expired"),
           "storage": tot("loss.storage", "amount"), "refunds": tot("seller.refund", "amount")}
    out["total"] = out["expired"] + out["storage"] + out["refunds"]
    return out


@tool("eval.stock_health", "How our stock turns: units bought, units sold, sell-through, food thrown away, and our "
      "restock plans versus refusals.",
      {"type": "object", "properties": _SLICE})
def stock_health(ctx, since=None, until=None, epoch=None):
    ev = events(["restock.bought", "seller.settled", "loss.expired", "restock.plan"], since, until, epoch)
    bought = sum(e["data"].get("qty") or 0 for e in ev if e["kind"] == "restock.bought")
    sold = sum(1 for e in ev if e["kind"] == "seller.settled")
    expired = sum(1 for e in ev if e["kind"] == "loss.expired")
    plans = [e for e in ev if e["kind"] == "restock.plan"]
    return {"bought": bought, "sold": sold, "expired": expired, "sell_through": _rate(sold, bought),
            "waste_rate": _rate(expired, bought), "plans": len(plans),
            "plans_refused": sum(1 for e in plans if e["data"].get("refused")),
            "plans_with_model": sum(1 for e in plans if e["data"].get("used_model"))}


@tool("eval.decision_quality", "How decisions were made: model calls per seat, latency (median, p90), rate limits, "
      "fallbacks to OpenRouter, failures, tool errors, refusals by the limit tools, and seller guard overrides.",
      {"type": "object", "required": ["scope"], "properties": {"scope": _SCOPE, **_SLICE}})
def decision_quality(ctx, scope, since=None, until=None, epoch=None):
    calls = [e["data"] for e in events(["model.call"], since, until, epoch)
             if scope == "system" or e["data"].get("seat") == scope]
    ms = sorted(c.get("ms") or 0 for c in calls)
    tools = [e for e in events(["tool.result"], since, until, epoch)
             if scope == "system" or e["writer"] == scope]
    out = {"model_calls": len(calls), "median_ms": statistics.median(ms) if ms else None,
           "p90_ms": ms[int(0.9 * (len(ms) - 1))] if ms else None,
           "failed": sum(1 for c in calls if not c.get("ok")), "rate_limited": sum(1 for c in calls if c.get("rate_limited")),
           "fallback": sum(1 for c in calls if c.get("fallback")), "tool_calls": len(tools),
           "tool_refusals": sum(1 for e in tools if not e["data"].get("ok"))}
    if scope in ("seller", "system"):
        dec = [e for e in events(["seller.decision"], since, until, epoch)]
        out["guard_overrides"] = sum(1 for e in dec if e["data"].get("guard"))
    return out


@tool("eval.integrity", "Honesty checks: receipts that did not match what we agreed (caught), our grade claims that "
      "turned out true, reviews whose stars differ from the fact baseline (and whether a reason was given), forum "
      "posts whose refs were real ledger entries.",
      {"type": "object", "properties": _SLICE})
def integrity(ctx, since=None, until=None, epoch=None):
    ev = events(["buyer.closed", "seller.settled", "review.written", "forum.posted"], since, until, epoch)
    bc = [e["data"] for e in ev if e["kind"] == "buyer.closed"]
    claims = [e["data"] for e in ev if e["kind"] == "seller.settled" and e["data"].get("claim")]
    rv = [e["data"] for e in ev if e["kind"] == "review.written"]
    off = [d for d in rv if d.get("fact_stars") is not None and d.get("stars") != d.get("fact_stars")]
    fp = [e["data"] for e in ev if e["kind"] == "forum.posted"]
    return {"cheats_caught": sum(1 for d in bc if str(d.get("verdict", "")).startswith("cheated")),
            "our_claims": len(claims), "our_claims_true": sum(1 for d in claims if d.get("claim_true") is True),
            "our_claims_unjudged": sum(1 for d in claims if d.get("claim_true") is None),
            "reviews": len(rv), "reviews_off_baseline": len(off),
            "reviews_off_without_reason": sum(1 for d in off if not d.get("why")),
            "posts": len(fp), "posts_with_valid_refs": sum(1 for d in fp if d.get("refs_valid"))}


@tool("eval.forum", "Our forum posts and the counted votes they received, split by shop and buyer posts (only buyer "
      "posts count for the buyer's rank).",
      {"type": "object", "properties": _SLICE})
def forum(ctx, since=None, until=None, epoch=None):
    ev = events(["forum.posted", "forum.votes"], since, until, epoch)
    posts = [e["data"] for e in ev if e["kind"] == "forum.posted"]
    latest = {}
    for e in ev:
        if e["kind"] == "forum.votes":
            latest[e["data"].get("post_id")] = e["data"]          # the newest count per post
    by = {"buyer": {"posts": 0, "likes": 0, "dislikes": 0}, "shop": {"posts": 0, "likes": 0, "dislikes": 0},
          "unknown": {"posts": 0, "likes": 0, "dislikes": 0}}
    for p in posts:
        by.setdefault(p.get("as") or "unknown", {"posts": 0, "likes": 0, "dislikes": 0})["posts"] += 1
    for pid, v in latest.items():
        b = by.setdefault(v.get("as") or "unknown", {"posts": 0, "likes": 0, "dislikes": 0})
        b["likes"] += v.get("likes") or 0
        b["dislikes"] += v.get("dislikes") or 0
    return by


@tool("eval.guess_accuracy", "How often our in-negotiation guess of the buyer turned out right once the ledger said "
      "who it was, by confidence band.",
      {"type": "object", "properties": _SLICE})
def guess_accuracy(ctx, since=None, until=None, epoch=None):
    ev = events(["guess.made", "guess.confirmed"], since, until, epoch)
    made = {e["data"].get("task"): e["data"] for e in ev if e["kind"] == "guess.made"}
    bands = {}
    for e in ev:
        if e["kind"] != "guess.confirmed":
            continue
        g = made.get(e["data"].get("task"))
        if not g or g.get("top") is None:
            continue
        c = g.get("confidence") or 0
        band = "high" if c >= 0.7 else "mid" if c >= 0.4 else "low"
        b = bands.setdefault(band, {"n": 0, "right": 0})
        b["n"] += 1
        b["right"] += int(g["top"] == e["data"].get("actual"))
    return {k: dict(v, accuracy=_rate(v["right"], v["n"])) for k, v in bands.items()}


METRICS = {"funnel": funnel, "unit_economics": unit_economics, "losses": losses, "stock_health": stock_health,
           "decision_quality": decision_quality, "integrity": integrity, "forum": forum}


def _metric(ctx, path, since, epoch):
    """'funnel.seller.completion_rate' -> the number. The first part names the tool; a scope follows when it takes one."""
    parts = path.split(".")
    fn = METRICS.get(parts[0])
    if fn is None:
        raise Refused(f"unknown metric {path!r}", f"start with one of {sorted(METRICS)}")
    takes_scope = parts[0] in ("funnel", "unit_economics", "decision_quality")
    res = fn(ctx, parts[1], since=since, epoch=epoch) if takes_scope else fn(ctx, since=since, epoch=epoch)
    for p in parts[2 if takes_scope else 1:]:
        res = res.get(p) if isinstance(res, dict) else None
    return res


_OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b}


@tool("eval.experiment", "Judge the active strategy's experiment from its own conditions: success_if and rollback_if "
      "are lists of {metric, op, value}; the sample is the metric named by min_sample.metric. Gives success, rollback "
      "or not enough data, with every number it used.",
      {"type": "object", "properties": {"experiment": {"type": "object"}, "since": {"type": ["number", "null"]}}})
def experiment(ctx, experiment=None, since=None):
    exp = experiment or ctx.strategy.get("experiment") or {}
    epoch = None if experiment else ctx.strategy.get("epoch")
    ms = exp.get("min_sample") or {}
    if not ms.get("metric"):
        raise Refused("the experiment has no min_sample.metric", "write conditions as {metric, op, value}")
    n = _metric(ctx, ms["metric"], since, epoch) or 0
    used = {ms["metric"]: n}

    def holds(conds):
        ok = bool(conds)
        for c in conds or []:
            v = _metric(ctx, c["metric"], since, epoch)
            used[c["metric"]] = v
            ok = ok and v is not None and _OPS[c["op"]](v, c["value"])
        return ok

    if n < ms.get("value", 0):
        return {"verdict": "not enough data", "sample": n, "needed": ms.get("value"), "used": used}
    if holds(exp.get("rollback_if")):
        return {"verdict": "rollback", "sample": n, "used": used}
    if holds(exp.get("success_if")):
        return {"verdict": "success", "sample": n, "used": used}
    return {"verdict": "neither yet", "sample": n, "used": used}


@tool("eval.proposals", "How the strategy agent's proposals went: how many, how many the user accepted, and how the "
      "accepted ones were judged.",
      {"type": "object", "properties": _SLICE})
def proposals(ctx, since=None, until=None, epoch=None):
    ev = events(["strategy.proposal", "strategy.decision"], since, until, epoch)
    dec = [e["data"] for e in ev if e["kind"] == "strategy.decision"]
    return {"proposals": sum(1 for e in ev if e["kind"] == "strategy.proposal"), "answered": len(dec),
            "accepted": sum(1 for d in dec if d.get("accepted")),
            "accept_rate": _rate(sum(1 for d in dec if d.get("accepted")), len(dec))}


@tool("eval.audits", "How often the audit agent disagreed with the strategy agent, and, where the outcome is known, "
      "who was right.",
      {"type": "object", "properties": _SLICE})
def audits(ctx, since=None, until=None, epoch=None):
    ev = events(["audit.opinion", "audit.outcome"], since, until, epoch)
    op = [e["data"] for e in ev if e["kind"] == "audit.opinion"]
    oc = [e["data"] for e in ev if e["kind"] == "audit.outcome"]
    return {"opinions": len(op), "disagreed": sum(1 for d in op if d.get("agrees") is False),
            "disagree_rate": _rate(sum(1 for d in op if d.get("agrees") is False), len(op)),
            "outcomes_known": len(oc), "audit_right": sum(1 for d in oc if d.get("audit_right"))}


@tool("eval.score", "Our place on the board: Players percentile, shop ALL and buyer ALL with their six parts, and the "
      "distance to the next band. Read from what the outer runtime last collected from the market's board.",
      {"type": "object", "properties": {}})
def score(ctx):
    sb = atomic.read_json(paths.memory() / "facts" / "scoreboard.json", default=None)
    if not sb:
        raise Refused("no scoreboard collected yet", "the outer runtime writes memory/facts/scoreboard.json from /api/board")
    return sb
