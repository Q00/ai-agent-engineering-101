"""The fixed metrics (design 17-2, R6) and the hourly summary (summary.hourly, 10장), computed by code from the
trajectories and the kept board - never by a model (R14). A role's self-report (report.submit) is these numbers for
its own role over 1 h and 24 h; the metric list changes only with the user's approval (R6).

    py -m harness.metrics 20261105            # the day's hourly summary -> memory/harness/summary/<day>.json
    py -m harness.metrics report shop         # the shop's report for the last 1 h and 24 h
"""
import json
import sys
import time
from collections import defaultdict

from core import paths
from harness import out_dir, trajectory
from outer.position import snapshots

SHOP_NAME = "gom31-certified-goods"

# The metric list (17-2). Changing it needs the user's approval (R6): a role that edits its own metric puts the
# judge inside the loop (W02).
METRICS = {
    "buyer": ["saved_share", "surplus", "orders", "settled", "success_rate", "agreed_unpaid_rate", "order_seconds_median",
              "posts", "votes_per_post"],
    "shop": ["book_change", "revenue", "margin_rate", "new_stars_mean", "no_stock_rate", "visits", "sales",
             "waste", "storage"],
    "process": ["model_calls", "anomalies", "tool_failures", "tool_failures_ours"],
}


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def _saved_share(paid):
    shares = []
    for t in paid:
        a = t["after"]
        if a.get("value") is None:
            continue                               # not reviewed yet: worth unknown
        shares.append(-1.0 if not a["value"] else (a.get("surplus") or 0) / a["value"])
    return round(sum(shares) / len(shares), 4) if shares else None


def _per_post(votes):
    last = {}
    for e in votes:
        last[e["data"].get("post_id")] = (e["data"].get("likes") or 0) - (e["data"].get("dislikes") or 0)
    return round(sum(last.values()) / len(last), 3) if last else None


def _hour(ts):
    return time.strftime("%Y%m%d%H", time.localtime(ts))


def _events(day):
    """The non-trade events the metrics need: forum, storage, anomalies, model calls."""
    return [e for e in trajectory.read_day(day) if e["kind"] in
            ("forum.posted", "forum.votes", "loss.storage", "exec.anomaly", "model.call", "seller.missed",
             "seller.refund", "tool.result")]


def compute(trs, evs, since, until, book=None):
    """The fixed metrics over [since, until) from trajectories (by when the trade started) and events."""
    T = [t for t in trs if since <= t["start"] < until]
    E = [e for e in evs if since <= e["ts"] < until]
    b = [t for t in T if t["role"] == "buyer"]
    s = [t for t in T if t["role"] == "seller"]
    agreed = [t for t in b if t["result"].get("agreed") and not t["result"].get("cause")]
    paid = [t for t in agreed if t["result"].get("paid")]
    sold = [t for t in s if t["result"].get("settled")]
    revenue = sum(t["result"].get("settled_price") or 0 for t in sold)
    refunds = sum(e["data"].get("amount") or 0 for e in E if e["kind"] == "seller.refund")
    cost = sum(t["result"].get("cost") or 0 for t in sold)
    stars = [t["after"]["stars"] for t in sold if isinstance(t["after"].get("stars"), (int, float))]
    posts = [e for e in E if e["kind"] == "forum.posted" and e["data"].get("as") == "buyer"]
    votes = [e for e in E if e["kind"] == "forum.votes" and e["data"].get("as") == "buyer"]
    tool_fail = [e for e in E if e["kind"] == "tool.result" and not e["data"].get("ok")]
    return {
        "since": since, "until": until,
        "buyer": {
            # patch 3.4: the score is the share of list kept per deal (surplus / worth; a deal that served
            # nothing, worth 0, counts -1); over a window without the board's +100 smoothing
            "saved_share": _saved_share(paid),
            "surplus": sum(t["after"].get("surplus") or 0 for t in paid),
            "orders": len(b), "settled": len(paid),
            "success_rate": round(len(paid) / len(b), 3) if b else None,
            "agreed_unpaid_rate": round(1 - len(paid) / len(agreed), 3) if agreed else None,
            "order_seconds_median": _median([round(t["end"] - t["start"], 1) for t in b if t["result"].get("outcome")]),
            "posts": len(posts),
            # forum.votes is a snapshot of a post's running count, read again and again: the last one per post
            "votes_per_post": _per_post(votes)},
        "shop": {
            "book_change": book, "revenue": revenue - refunds,
            "margin_rate": round((revenue - cost) / revenue, 3) if revenue else None,
            "new_stars_mean": round(sum(stars) / len(stars), 3) if stars else None,
            "no_stock_rate": round(sum(1 for e in E if e["kind"] == "seller.missed") / len(s), 3) if s else None,
            "visits": len(s), "sales": len(sold),
            "waste": None,                         # spoiled food: the collector does not write it yet (D-12)
            "storage": sum(e["data"].get("amount") or 0 for e in E if e["kind"] == "loss.storage")},
        "process": {
            "model_calls": sum(1 for e in E if e["kind"] == "model.call"),
            "anomalies": sum(1 for e in E if e["kind"] == "exec.anomaly"),
            "tool_failures": len(tool_fail),
            "tool_failures_ours": sum(1 for e in tool_fail if trajectory.cause(e["data"].get("reason")) == "ours")},
    }


def book_series(since, until):
    """{hour: our shop's book at the last snapshot of that hour} from the kept board."""
    out = {}
    for snap in snapshots(since - 3600):
        if snap.get("ts", 0) >= until:
            break
        row = next((r for r in snap.get("shops") or [] if r.get("name") == SHOP_NAME), None)
        if row and isinstance(row.get("book"), (int, float)):
            out[_hour(snap["ts"])] = row["book"]
    return out


def hourly(day):
    """summary.hourly: the fixed metrics for each hour of the day."""
    trs = list(trajectory.build(day).values())
    evs = _events(day)
    t0 = time.mktime(time.strptime(day, "%Y%m%d"))
    books = book_series(t0, t0 + 86400)
    out = {}
    for h in range(24):
        a = t0 + h * 3600
        key, prev = _hour(a), _hour(a - 3600)
        change = books[key] - books[prev] if key in books and prev in books else None
        out[key] = compute(trs, evs, a, a + 3600, book=change)
    return out


# 11/7 11:xx (user: what goes wrong between the harness and the buyer): the buyer's failures by cause, the kit's
# payment refusals by cause, and the deals that served nothing (-1 in saved) were not in any report - they were
# found only by reading tool logs and the ledger by hand
WHY_NO_DEAL = (("fair", "deal"), ("no stock", "shop had none"), ("value", "under our value bar"),
               ("shelf", "shelf too short (claim)"), ("could not reach", "shop unreachable"),
               ("timeout", "shop too slow"), ("failed", "shop too slow"), ("no shop", "no shop lists it"),
               ("rejected", "shop refused"), ("walked", "we walked (price)"), ("expired", "order time ran out"),
               ("cheated", "receipt over what we agreed"))
WHY_UNPAID = (("shelf life", "food hours short"), ("grade", "grade below policy"), ("not in stock", "unit gone"),
              ("certificate", "no certificate"), ("warranty", "warranty short"))


def _label(text, table):
    t = (text or "").lower()
    return next((lab for key, lab in table if key in t), "other")


def buyer_detail(trs, since, until):
    """Why our buyer's orders ended without a deal, why agreed deals were not paid, and the deals that served
    nothing on the ledger (goods broken without a refund, by when they broke; food settled with fewer hours than
    the order's use window) - each of those is -1 in saved (agent.md)."""
    from collections import Counter
    days = sorted({time.strftime("%Y%m%d", time.localtime(t)) for t in (since, until - 1)})
    closed, need = Counter(), {}
    for d in days:
        last = {}
        for e in trajectory.read_day(d):
            if e["kind"] == "buyer.order":
                need[(e.get("keys") or {}).get("order")] = e["data"].get("use_within_hours")
            elif e["kind"] == "buyer.closed" and since <= e["ts"] < until:
                last[(e.get("keys") or {}).get("order")] = e["data"].get("verdict")
        closed.update(_label(v, WHY_NO_DEAL) for v in last.values())     # each order by how it ended
    unpaid = Counter(_label(t["result"].get("pay_reason"), WHY_UNPAID) for t in trs
                     if t["role"] == "buyer" and since <= t["start"] < until and t["result"].get("paid") is False)
    broken = short = 0
    for d in days:
        f = paths.memory() / "market" / f"ledger-{d}.jsonl"
        if not f.exists():
            continue
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if SHOP_NAME not in line or ('"kind": "broken"' not in line and '"kind": "settle"' not in line):
                    continue
                x = json.loads(line)
                if not since <= x["ts"] < until or (x["data"].get("buyer") != SHOP_NAME):
                    continue
                if x["kind"] == "broken" and not x["data"].get("refunded"):
                    broken += 1
                elif x["kind"] == "settle" and x["data"].get("expires_at"):
                    h = need.get(x["data"].get("order"))
                    if h and (x["data"]["expires_at"] - x["ts"]) / 3600 < h:
                        short += 1
    return {"orders_ended": dict(closed.most_common()), "unpaid_why": dict(unpaid.most_common()),
            "unfilled": broken + short, "broken_unrefunded": broken, "food_short": short}


def report(role, now=None):
    """report.submit (R6): the role's fixed metrics over the last 1 h and 24 h. Written, never edited."""
    now = now or time.time()
    days = sorted({time.strftime("%Y%m%d", time.localtime(now - 86400)), time.strftime("%Y%m%d", time.localtime(now))})
    trs, evs = [], []
    for d in days:
        trs += list(trajectory.build(d).values())
        evs += _events(d)
    books = book_series(now - 86400, now + 1)
    keys = sorted(books)
    side = "shop" if role in ("shop", "seller", "restock") else role
    rep = {"role": side, "ts": now, "metrics": METRICS[side]}
    for label, span in (("1h", 3600), ("24h", 86400)):
        old = [k for k in keys if k <= _hour(now - span)]
        change = books[keys[-1]] - books[old[-1]] if keys and old else None
        rep[label] = compute(trs, evs, now - span, now, book=change)[side]
        if side == "buyer":
            try:
                rep[label]["detail"] = buyer_detail(trs, now - span, now)
            except Exception as e:                  # a report never fails for its detail
                rep[label]["detail"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    d = out_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{side}-{time.strftime('%Y%m%d-%H%M', time.localtime(now))}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "report":
        print(json.dumps(report(args[1]), ensure_ascii=False, indent=1))
    else:
        day = args[0] if args else time.strftime("%Y%m%d")
        h = hourly(day)
        p = out_dir() / "summary"
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{day}.json").write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")
        for k, v in h.items():
            print(k, json.dumps({"buyer": {x: v["buyer"][x] for x in ("saved_share", "surplus", "settled")},
                                 "shop": {x: v["shop"][x] for x in ("book_change", "revenue", "new_stars_mean",
                                                                    "no_stock_rate")}}, ensure_ascii=False))
