"""What happened after each change the model made: a before/after check one hour later.

Every model change-set that is applied registers a check with the metrics of the 2 h before it. At the run
an hour later the same metrics are measured over the time since the apply and the result goes to
changes.jsonl and the model's history. Only buyer.cap_food and buyer.min_surplus_list are reverted
automatically, and only on food conversion (their direct, mechanical effect): both halves of the hour
after must show a drop of at least DROP against the base and a two-proportion z of at most -Z, so one
noisy half-hour is not enough.
A revert returns the key to its old value (if it still holds the value the change wrote) and blocks that
direction for BLOCK_H. Goods conversion, our share of market settles, our settled revenue per hour and posts
per hour swing too much between half-hours (goods 0.55-0.96 with no change) to judge a change by: they are
reported, never acted on. shop_rev_h is there because a seller lever's effect is in paid revenue, which the
seller log cannot show (a sign is not a sale): on 11-10 four warranty cap cuts went through with only buyer
and forum metrics in their outcome rows while settled revenue halved.
While a check is pending the model is not asked (run.py), so one change-set is judged before the next.
"""
import math

from strategist import collect, guard

CHECK_H, BASE_H, MAX_WAIT_H = 1.0, 2.0, 3.0
# the apply time is after the model call, so the run an hour later starts up to that long before the hour is
# over: a check is due that much early, and the hour after then ends at the run's start
CHECK_SLACK_S = 600
HISTORY_H = MAX_WAIT_H              # how far back run.py must keep buyer and forum rows (collect.read_own)
DROP, Z, MIN_N, BLOCK_H = 0.30, 2.5, 20, 12
METRICS = ("buyer_food_conv", "buyer_goods_conv", "shop_share", "shop_rev_h", "posts_h")
REVERT = {"buyer.cap_food": "buyer_food_conv", "buyer.min_surplus_list": "buyer_food_conv"}


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _in(r, t0, t1):
    return isinstance(r, dict) and _num(r.get("ts")) and t0 <= r["ts"] < t1


def measure(hist, buckets, m, t0, t1):
    """{num, den, value} of metric m over [t0, t1). hist: collect.read_own's "history" (buyer, forum rows);
    buckets: state["ledger_buckets"] (only buckets wholly inside the span count)."""
    hist = hist if isinstance(hist, dict) else {}
    if m in ("buyer_food_conv", "buyer_goods_conv"):
        kind = m.split("_")[1]
        rows = [r for r in hist.get("buyer") or [] if _in(r, t0, t1) and r.get("kind") == "order"
                and isinstance(r.get("inputs"), dict) and r["inputs"].get("kind") == kind]
        num, den = sum(1 for r in rows if isinstance(r.get("choice"), dict)), len(rows)
    elif m == "shop_share":
        tot = collect.bucket_total(buckets, t0, t1)
        num, den = tot["ours"], tot["settle"]
    elif m == "shop_rev_h":             # our settled (paid) revenue per hour, from the ledger's settles: what the
        tot = collect.bucket_total(buckets, t0, t1)                   # board counts, where a seller lever shows
        num, den = round(sum(v for k, v in tot.items() if k.startswith("ours_rev."))), (t1 - t0) / 3600
    else:                               # posts_h: per hour
        num = sum(1 for r in hist.get("forum") or [] if _in(r, t0, t1)
                  and isinstance(r.get("choice"), dict) and r["choice"].get("posted") is True)
        den = (t1 - t0) / 3600
    return {"num": num, "den": round(den, 4), "value": round(num / den, 4) if den else None}


def _z(base, after):
    """Two-proportion z of after against base (pooled)."""
    p = (base["num"] + after["num"]) / (base["den"] + after["den"])
    se = math.sqrt(p * (1 - p) * (1 / base["den"] + 1 / after["den"]))
    return (after["value"] - base["value"]) / se if se else 0.0


def dropped(base, halves):
    """True when every half has MIN_N orders and fell by DROP or more against base, with z <= -Z."""
    if not base or base["value"] is None or base["den"] < MIN_N:
        return False
    return all(h["den"] >= MIN_N and h["value"] <= (1 - DROP) * base["value"] and _z(base, h) <= -Z
               for h in halves)


def _enough(base, halves):
    return bool(base) and base["den"] >= MIN_N and all(h["den"] >= MIN_N for h in halves)


def register(state, run_id, applied, at, hist, buckets):
    """A pending check for the model rows applied at `at` (the time the file was written)."""
    rows = [a for a in applied if a.get("source") == "model"]
    if not rows:
        return
    base = {m: measure(hist, buckets, m, at - BASE_H * 3600, at) for m in METRICS}
    state.setdefault("checks", []).append({"run_id": run_id, "ts": at,
                                           "keys": {a["key"]: [a["old"], a["new"]] for a in rows}, "base": base})


def check(state, hist, buckets, pol, now):
    """(reverts, outcomes) for the pending checks that are due (CHECK_H after the apply, less CHECK_SLACK_S);
    those leave state["checks"].
    reverts: guard rows with source "revert". outcomes: {outcome_of, result, metrics: {m: [base, after]},
    revert: [keys], superseded: [keys]}; result is "revert", "kept", "inconclusive", "report only"
    (no key of the set is judged), "superseded" (the key moved since) or "expired" (checked too late:
    the rows of the hour after may be gone)."""
    reverts, outcomes, pending = [], [], []
    blocked = state.get("blocked") if isinstance(state.get("blocked"), dict) else {}
    blocked = {k: b for k, b in blocked.items()
               if isinstance(b, dict) and _num(b.get("until")) and b["until"] > now}
    for c in state.get("checks") or []:
        if not isinstance(c, dict) or not _num(c.get("ts")) or not isinstance(c.get("keys"), dict):
            continue
        t0 = c["ts"]
        if now < t0 + CHECK_H * 3600 - CHECK_SLACK_S:
            pending.append(c)
            continue
        end = min(t0 + CHECK_H * 3600, now)
        base = c.get("base") if isinstance(c.get("base"), dict) else {}
        after = {m: measure(hist, buckets, m, t0, end) for m in METRICS}
        out = {"outcome_of": c.get("run_id"), "metrics": {m: [(base.get(m) or {}).get("value"), after[m]["value"]]
                                                          for m in METRICS},
               "n": {m: [(base.get(m) or {}).get("den"), after[m]["den"]] for m in METRICS},
               "revert": [], "superseded": []}
        judged = [k for k in c["keys"] if k in REVERT]
        if now - t0 > HISTORY_H * 3600:
            out["result"] = "expired"
        elif not judged:
            out["result"] = "report only"
        else:
            enough = True
            for k in judged:
                m = REVERT[k]
                half = (t0 + end) / 2
                halves = [measure(hist, buckets, m, t0, half), measure(hist, buckets, m, half, end)]
                enough = enough and _enough(base.get(m), halves)
                if not dropped(base.get(m), halves):
                    continue
                old, new = c["keys"][k]
                if guard._get(pol, k) != new:
                    out["superseded"].append(k)
                    continue
                b, a = base[m]["value"], after[m]["value"]
                why = f"outcome: {m} {b}->{a} after {c.get('run_id')}"
                reverts.append({"key": k, "value": old, "source": "revert", "reason": why})
                out["revert"].append(k)
                blocked[k] = {"dir": (new > old) - (new < old), "until": now + BLOCK_H * 3600, "why": why}
            out["result"] = ("revert" if out["revert"] else "superseded" if out["superseded"]
                             else "kept" if enough else "inconclusive")
        outcomes.append(out)
    state["checks"], state["blocked"] = pending, blocked
    return reverts, outcomes


def waiting(state, now):
    """The run id of a pending check (the model waits for it), or None."""
    for c in state.get("checks") or []:
        if isinstance(c, dict) and _num(c.get("ts")) and now - c["ts"] < MAX_WAIT_H * 3600:
            return c.get("run_id")
    return None
