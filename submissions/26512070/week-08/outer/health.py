"""Health metrics, kept running by the outer loop (HARNESS 14.4, D34 step 1; user 11/4 08:4x).

    py -m outer.health            # one snapshot: memory/facts/health.json, alerts over the thresholds

Every problem of the night of 11/3-11/4 was found by reading logs by hand: restock buying nothing for hours,
cash held by the limits, buyers leaving at the opening ask, the best seller sold out, payment refusals, slow
model calls. This measures them every HEALTH_EVERY over the last WINDOW and raises an alert (bus, URGENT.md via
watch) when a threshold is crossed; the strategy brief reads the latest snapshot.
"""
import collections
import json
import re
import statistics as st
import time

from core import atomic, bus, paths

WINDOW = 1800
THRESHOLDS = {"shop_settled_min": 1, "no_stock_share_max": 0.4, "left_at_opening_share_max": 0.5,
              "buyer_paid_min": 1, "unpaid_share_max": 0.5, "supply_windows_without_buy_max": 2,
              "model_timeout_share_max": 0.5, "tool_refusal_share_max": 0.2}


def _events(source, since):
    out = []
    for f in (paths.memory() / "raw" / source).glob("*.jsonl"):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("ts", 0) >= since:
                    out.append(d)
    return sorted(out, key=lambda d: d["ts"])


def seller(since):
    """Each negotiation since `since`: completed, quote-only survey, left at our opening, offered then left,
    no stock (and the SKUs asked), rejected, open."""
    by = collections.defaultdict(list)
    for d in _events("seller", since):
        by[d.get("keys", {}).get("task")].append(d)
    out, asked, rev, cost = collections.Counter(), collections.Counter(), 0, 0
    for es in by.values():
        cl = [e["data"] for e in es if e["kind"] == "seller.closed"]
        req = [e["data"] for e in es if e["kind"] == "seller.request"]
        missed = [e["data"] for e in es if e["kind"] == "seller.missed"]
        if missed:
            out["no_stock"] += 1
            for s in missed[0].get("skus") or []:
                asked[s] += 1
            continue
        o = cl[0]["outcome"] if cl else "open"
        if o == "canceled":
            if any("Quote only" in (r.get("excerpt") or "") for r in req):
                o = "quote_only"
            else:
                o = "offered_then_left" if any(r.get("price") for r in req[1:]) else "left_at_opening"
        out[o] += 1
        if o == "completed" and cl[0].get("price"):
            rev += cl[0]["price"]
            cost += cl[0].get("cost") or 0
    visits = sum(out.values())
    real = visits - out["quote_only"]
    return {"visits": visits, "outcomes": dict(out), "revenue": rev, "cost_of_sales": cost,
            "no_stock_share": round(out["no_stock"] / visits, 2) if visits else None,
            "left_at_opening_share": round(out["left_at_opening"] / real, 2) if real else None,
            "missing_skus": asked.most_common(6)}


def buyer(since, state):
    by = collections.defaultdict(list)
    for d in _events("buyer", since):
        by[d.get("keys", {}).get("order")].append(d)
    out, kinds = collections.Counter(), collections.Counter()
    for es in by.values():
        od = [e["data"] for e in es if e["kind"] == "buyer.order"]
        cl = [e["data"] for e in es if e["kind"] == "buyer.closed"]
        if od:
            kinds[od[0].get("kind")] += 1
        if cl:
            out[cl[0]["outcome"][:40]] += 1
    p = paths.root() / ".market" / "payments.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    start = state.get("payments_line", max(0, len(lines) - 50))
    now = time.time()
    seen = [x for x in state.get("payments", []) if x[0] >= since]       # [seen_at, paid, reason], kept for WINDOW
    for x in (json.loads(y) for y in lines[start:] if y.strip()):     # payments.jsonl has no time: when we saw it
        r = (x.get("reason") or "").split(":", 1)[1].strip() if ":" in (x.get("reason") or "") else (x.get("reason") or "?")
        seen.append([now, bool(x.get("paid")), re.sub(r"[\d.]+", "N", " ".join(r.split()[:6]))[:50]])
    state["payments_line"], state["payments"] = len(lines), seen
    paid = sum(1 for x in seen if x[1])
    reasons = collections.Counter(x[2] for x in seen if not x[1])
    new = seen
    return {"orders": sum(kinds.values()), "kinds": dict(kinds), "outcomes": dict(out), "paid": paid,
            "unpaid": len(new) - paid, "unpaid_share": round((len(new) - paid) / len(new), 2) if new else None,
            "unpaid_reasons": reasons.most_common(4)}


def restock(since):
    ev = _events("restock", since)
    windows, bought = set(), set()
    for d in ev:
        x = d["data"]
        if x.get("window") in ("open",) or x.get("rules"):
            windows.add(int(d["ts"] // 1200))
        if x.get("lines"):
            bought.add(int(d["ts"] // 1200))
    anomalies = collections.Counter(d["data"].get("kind") for d in ev if d["kind"] == "exec.anomaly")
    return {"windows": len(windows), "windows_with_buy": len(bought & windows),
            "windows_without_buy": len(windows - bought), "anomalies": dict(anomalies)}


def models(since):
    g = collections.defaultdict(list)
    for d in _events("models", since):
        x = d["data"]
        g[(x["seat"], x["model"].split(":")[0])].append((x["ms"], x["ok"], "within" in (x.get("error") or "")))
    return {f"{s}/{m}": {"n": len(v), "median_s": round(st.median([a for a, _, _ in v]) / 1000, 1),
                         "timeouts": sum(1 for _, _, t in v if t)} for (s, m), v in g.items()}


def tools(since):
    tot, bad = collections.Counter(), collections.Counter()
    for d in _events("tools", since):
        if d["kind"] != "tool.result":
            continue
        t = d["data"].get("tool") or "?"
        tot[t] += 1
        if not d["data"].get("ok", True):
            bad[t] += 1
    n = sum(tot.values())
    return {"calls": n, "refused": sum(bad.values()), "refusal_share": round(sum(bad.values()) / n, 3) if n else None,
            "by_tool": bad.most_common(4)}


def check(now=None):
    now = now or time.time()
    since = now - WINDOW
    sp = paths.memory() / "facts" / "health_state.json"
    state = atomic.read_json(sp, default=None) or {}
    doc = {"ts": now, "window_s": WINDOW, "seller": seller(since), "buyer": buyer(since, state),
           "restock": restock(since), "models": models(since), "tools": tools(since)}
    t, s, b = THRESHOLDS, doc["seller"], doc["buyer"]
    found = []
    if s["visits"] and s["outcomes"].get("completed", 0) < t["shop_settled_min"]:
        found.append(f"the shop sold nothing in {WINDOW // 60} min ({s['visits']} visits)")
    if (s["no_stock_share"] or 0) > t["no_stock_share_max"]:
        found.append(f"{s['no_stock_share']:.0%} of visits found no stock; most asked {s['missing_skus'][:3]}")
    if (s["left_at_opening_share"] or 0) > t["left_at_opening_share_max"]:
        found.append(f"{s['left_at_opening_share']:.0%} of real buyers left at our opening ask")
    if b["orders"] and b["paid"] < t["buyer_paid_min"]:
        found.append(f"the buyer paid nothing in {WINDOW // 60} min ({b['orders']} orders)")
    if (b["unpaid_share"] or 0) > t["unpaid_share_max"]:
        found.append(f"{b['unpaid_share']:.0%} of agreed deals were not paid: {b['unpaid_reasons'][:2]}")
    if doc["restock"]["windows_without_buy"] > t["supply_windows_without_buy_max"]:
        found.append(f"restock bought nothing in {doc['restock']['windows_without_buy']} supply windows")
    for k, m in doc["models"].items():
        if m["n"] >= 5 and m["timeouts"] / m["n"] > t["model_timeout_share_max"]:
            found.append(f"{k}: {m['timeouts']} of {m['n']} calls timed out")
    if (doc["tools"]["refusal_share"] or 0) > t["tool_refusal_share_max"]:
        found.append(f"{doc['tools']['refusal_share']:.0%} of tool calls refused: {doc['tools']['by_tool']}")
    doc["findings"] = found
    for f in found:                                  # one alert per finding and hour, so URGENT.md stays readable
        key = f.split(":")[0][:40]
        if now - state.get("alerted", {}).get(key, 0) > 3600:
            bus.alert("health", "warn", "health", f)
            state.setdefault("alerted", {})[key] = now
    atomic.write_json(sp, state)
    atomic.write_json(paths.memory() / "facts" / "health.json", doc)
    return doc


def summary(doc):
    if not doc:
        return "no health snapshot yet"
    s, b, r = doc["seller"], doc["buyer"], doc["restock"]
    return json.dumps({"findings": doc.get("findings"), "seller": {k: s[k] for k in ("visits", "outcomes", "revenue",
                       "cost_of_sales", "missing_skus")}, "buyer": {k: b[k] for k in ("orders", "kinds", "paid", "unpaid",
                       "unpaid_reasons")}, "restock": r}, ensure_ascii=False)


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=1)[:4000])
