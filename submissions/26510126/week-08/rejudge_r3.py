"""Re-judge the team's past number verdicts on the measure the board pays for, and mark what cannot be re-judged.

11/9: the 2 h verdicts on the team's numbers compared our raw margin/h before and after a change until 11:13 that day,
so each read the hour of day as much as the number. The team kept them as facts: 11 manager decisions and 10 specialist proposals held a number
because it had been "judged in both directions". An agent's memory carries a wrong note into every later session
until something re-checks it (Anthropic, "components of agent memory", 2026: a mislabelled map note sent the agent
down the same ledge three times until a review of the transcript fixed the note).

Each verdict row is re-judged once its two hours have run: our share of the market's paid value over the same
hours, and board points/h net of the market's own change (metrics_r3.shop_side: the ledger cache, else the
10-minute buckets). The result is VERIFIED (confirmed, reversed, or flat within NOISE) or UNVERIFIED when no data
reaches back to the row (the cache keeps ~6 h; the buckets start 11/9). Results are cached in logs/rejudged_r3.json;
the verdict ledger itself is never edited. memory_text() is what the team reads each cycle.
"""
import json
import time
from pathlib import Path

import metrics_r3

HERE = Path(__file__).resolve().parent
# v2 (11/9 13:xx): judged on the shop's points only, and a verdict with another change in its hours is MIXED, not
# VERIFIED (the fresh review: the buyer's saved was 82-91% of the total, and 87 of 90 windows held another change);
# the first pass stays in logs/rejudged_r3.json as it was
OUT = Path("logs") / "rejudged_r3_v2.json"
CHANGES = Path("logs") / "code_changes_r3.jsonl"
SPAN = 2 * 3600          # the number verdict's span (meta_r3.NUMBER_CHECK)
NOISE = 0.30             # flat under 30% either way: two 2 h windows of our value share differ by sd ~0.32 with no change (11/9 review)


def key(r):
    return f"{r.get('t', 0):.0f}|{r.get('change')}"


def rows(base=HERE):
    """The number verdict rows (fast guard reverts are not verdicts on a change)."""
    return [x for x in metrics_r3.jl(Path(base) / CHANGES) if x.get("source") == "inner loop (number)"
            and not str(x.get("change", "")).startswith("fast guard")]


def side(t0, t1, base):
    """Board points/h, our value share and the market's paid value over [t0, t1), or None without data."""
    s = metrics_r3.shop_side(t0, t1, base)
    if not s or not s["mkt_value"]:
        return None
    w = metrics_r3.window(t0, t1, base)
    hours = max((t1 - t0 - (w.get("outage_min") or 0) * 60) / 3600, 0.25)
    pts = metrics_r3.shop_points_h(s["ours_margin"] / hours, s["ours_value"] / hours)
    return {"pts": pts, "share_value": s["ours_value"] / s["mkt_value"], "mkt_value": s["mkt_value"],
            "ours_n": s["ours_n"], "mkt_n": s["mkt_n"]}


def others(r, rows):
    """The other changes inside r's before and after hours: a number, a code change or a supervisor's change."""
    return [x.get("change", "")[:60] for x in rows if key(x) != key(r) and abs(x.get("t", 0) - r["t"]) < SPAN
            and x.get("source") in ("inner loop (number)", "meta (auto)", "supervisor")
            and not str(x.get("change", "")).startswith("fast guard")]


def judge(r, base=HERE, rows=()):
    """The re-judged result for one verdict row, or None when no data reaches back to it."""
    t = r["t"]
    a, b = side(t - SPAN, t, base), side(t, t + SPAN, base)
    if not a or not b:
        return None
    share = (b["share_value"] - a["share_value"]) / a["share_value"] if a["share_value"] else None
    net = None
    if a["pts"] and b["pts"] is not None:          # board points net of the market's own change in paid value
        net = (b["pts"] / (b["mkt_value"] / a["mkt_value"]) - a["pts"]) / abs(a["pts"])
    aim = net if net is not None else share
    orig = r.get("measured") if isinstance(r.get("measured"), (int, float)) else r.get("share")
    if aim is None:
        return None
    mixed = others(r, rows)
    if mixed:
        return {"status": "mixed", "aim": round(aim, 3), "share": round(share, 3) if share is not None else None,
                "net_pts": round(net, 3) if net is not None else None, "orig": orig, "with": mixed[:3],
                "sales": f"{a['ours_n']}/{a['mkt_n']} -> {b['ours_n']}/{b['mkt_n']}"}
    if abs(aim) < NOISE:
        status = "flat"
    elif not isinstance(orig, (int, float)) or abs(orig) < 0.05:   # the raw verdict's own sign counts from 5%
        status = "up" if aim > 0 else "down"
    else:
        status = "confirmed" if (aim > 0) == (orig > 0) else "reversed"
    return {"status": status, "aim": round(aim, 3), "share": round(share, 3) if share is not None else None,
            "net_pts": round(net, 3) if net is not None else None, "orig": orig,
            "sales": f"{a['ours_n']}/{a['mkt_n']} -> {b['ours_n']}/{b['mkt_n']}"}


def earliest(base=HERE):
    """The earliest time any data reaches back to: the first bucket or the cache's first settle."""
    # all rows, not the first: the backfill appends older buckets after the ones filed live
    ts = [min((x.get("t") for x in metrics_r3.jl(Path(base) / metrics_r3.BUCKETS) if x.get("t")), default=None)]
    ts += [min((e["ts"] for e in metrics_r3._settles(base)), default=None)]
    ts = [x for x in ts if x]
    return min(ts) if ts else None


def update(base=HERE, now=None):
    """Re-judge the rows whose span has run and are not in the cache yet; mark those no data reaches as UNVERIFIED.
    Returns how many rows were added."""
    base, now = Path(base), now or time.time()
    path = base / OUT
    try:
        done = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        done = {}
    first, added = earliest(base), 0
    every = metrics_r3.jl(base / CHANGES)
    for r in rows(base):
        k = key(r)
        covered = bool(first) and r["t"] - SPAN >= first
        # an UNVERIFIED row is tried again once data reaches back to it (11/9: the ledger backfill, backfill_r3.py)
        if (k in done and not (done[k].get("status") == "unverified" and covered)) or now - r["t"] < SPAN:
            continue
        res = judge(r, base, every) if covered else None
        if res is None and covered:
            continue                                    # covered but not measurable yet (no market sales): retry
        if res is None and k in done:
            continue
        done[k] = res or {"status": "unverified", "orig": r.get("measured")}
        added += 1
    if added:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    return added


def note(res):
    """One verdict's re-judged words for the prompt."""
    if not res:
        return "not re-judged yet"
    o = res.get("orig")
    raw = f"raw margin {o:+.0%}" if isinstance(o, (int, float)) else "raw verdict"
    if res["status"] == "unverified":
        return f"UNVERIFIED ({raw})"
    if res["status"] == "mixed":
        return f"MIXED, no verdict: {', '.join(res.get('with') or [])} changed in the same hours ({raw})"
    # say what the change did, not "confirmed -20%": on 11/9 a model read "VERIFIED confirmed: board -20%" for
    # dear_depth 5 -> 4 as "hold dear_depth at 4"
    aim, what = res.get("aim"), "shop points/h net of the market" if res.get("net_pts") is not None else "value share"
    did = ("made no difference beyond noise" if res["status"] == "flat" else
           f"{'raised' if aim > 0 else 'lowered'} {what} by {abs(aim):.0%}")
    agree = {"confirmed": "; the raw verdict agreed", "reversed": "; the raw verdict said the opposite"}.get(res["status"], "")
    return f"VERIFIED: this change {did}{agree} (value share {res['share']:+.0%}, {raw}; {res['sales']} sales)" \
        if res.get("share") is not None else f"VERIFIED: this change {did}{agree} ({raw})"


def memory_text(base=HERE, per_key=3, max_chars=2500):
    """Per number, its last `per_key` judged changes with their re-judged status, newest numbers first."""
    try:
        done = json.loads((Path(base) / OUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    by = {}
    for r in rows(base):
        for part in str(r.get("change", "")).split(", "):
            if " -> " in part:
                k, _, rest = part.partition(" ")
                by.setdefault(k, []).append((r["t"], f"{time.strftime('%m/%d %H:%M', time.localtime(r['t']))} "
                                                     f"{rest}: {note(done.get(key(r)))}"))
    # the value now, beside the past: on 11/9 12:06 the manager read the 06:43 bundle's ask_frac_food 0.7 -> 0.85 as
    # the value now (a supervisor undo at 09:21 had set it back to 0.7) and raised it while meaning to lower it
    try:
        now_vals = json.loads((Path(base) / "strategy_r3.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        now_vals = {}
    lines = []
    for k, items in sorted(by.items(), key=lambda kv: -kv[1][-1][0]):
        lines.append(f"- {k} (now {now_vals.get(k, '?')}): " + "; ".join(x for _, x in items[-per_key:]))
    head = ("\nYour numbers' past verdicts, re-judged (memory check): VERIFIED rows were judged again on our share of "
            "the market's paid sales over the same hours; MIXED rows had another change in the same hours, so they are no verdict; UNVERIFIED rows were judged on raw "
            "margin/h before 11/9 11:13 and read the hour of day, so they are weak evidence, not a reason to hold a number "
            "(\"judged in both directions\" on UNVERIFIED rows proves nothing); \"now\" is the value in force, later "
            "supervisor changes included:\n")
    out = head
    for line in lines:
        if len(out) + len(line) + 1 > max_chars:
            break
        out += line + "\n"
    return out if out != head else ""                   # not even one number fits: say nothing rather than half


if __name__ == "__main__":
    print(metrics_r3.record_buckets(HERE), "buckets filed;", update(HERE), "verdicts re-judged")
    print(memory_text(HERE))
