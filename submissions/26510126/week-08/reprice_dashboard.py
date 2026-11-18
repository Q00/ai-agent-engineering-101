"""Dashboard for "값 다시 정하기": our opening ask and our deal prices before and after Round 3 hid the budgets.

    python reprice_dashboard.py            # writes logs/reprice/index.html and logs/reprice/summary.md

Reads only the meta-harness history (week-07/logs/challenge-1/strategies.jsonl): every version has its time, the
opening ask and target it set, and, in "before", how the previous version did (deals, median deal price). From
Round 3 on, market_intel counts only settled deals, so those medians are settled prices. Round start times come
from the ledger's round entries in the small cache (week-07/.market/ledger-recent.json); --round3 sets one by hand.
No full ledger is read.
"""
import argparse
import datetime
import html
import json
import os
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
# the week-07 folder the agents run in: next to this one in the submission branch, else the main checkout
W7 = next((p for p in (os.environ.get("WEEK07_DIR"), os.path.join(HERE, "..", "week-07"),
                       os.path.expanduser("~/Developer/ai-agent-engineering-101/submissions/26510126/week-07"))
           if p and os.path.isdir(p)), os.path.join(HERE, "..", "week-07"))
HISTORY = os.path.join(W7, "logs", "challenge-1", "strategies.jsonl")
RECENT = os.path.join(W7, ".market", "ledger-recent.json")
OUT = os.path.join(HERE, "logs", "reprice")
ITEMS = ("lamp", "chair", "bookshelf")
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
DAYS = 7  # how far back the charts go before Round 3 (or before now, while Round 3 has not started)


def round_starts():
    try:
        with open(RECENT) as f:
            return {e["data"].get("round", e.get("round")): e["ts"] for e in json.load(f) if e["kind"] == "round"}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load(start):
    rows = [json.loads(line) for line in open(HISTORY) if line.strip()]
    points = []
    for prev, v in zip(rows, rows[1:]):
        if v["time"] < start:
            continue
        o = v.get("before") or {}
        points.append({"t": prev["time"], "end": v["time"], "open": prev["open"], "target": prev["target"],
                       "sale": prev.get("sale"), "deals": o.get("deals", 0),
                       "median": {k: (o.get("items") or {}).get(k, {}).get("median_price") for k in ITEMS},
                       "n": {k: (o.get("items") or {}).get(k, {}).get("deals", 0) for k in ITEMS}})
    return points


def weighted(points, item, t0, t1):
    """Deal-weighted mean of the median deal price, and the time-weighted mean opening ask, between t0 and t1."""
    ps = [p for p in points if t0 <= p["t"] < t1]
    w = [(p["median"][item], p["n"][item]) for p in ps if p["median"][item] is not None and p["n"][item]]
    price = sum(m * n for m, n in w) / sum(n for _, n in w) if w else None
    span = [(p["open"][item], p["end"] - p["t"]) for p in ps]
    ask = sum(a * d for a, d in span) / sum(d for _, d in span) if span else None
    deals = sum(p["n"][item] for p in ps)
    hours = sum(p["end"] - p["t"] for p in ps) / 3600
    return ask, price, deals, hours


def svg(points, item, r3):
    """Opening ask (line), target (dashed) and median deal price (dots) over time; a rule at Round 3."""
    if not points:
        return "<p>no data</p>"
    W, H, L, B = 760, 220, 44, 24
    t0, t1 = points[0]["t"], points[-1]["end"]
    vals = [p["open"][item] for p in points] + [p["target"][item] for p in points] + \
           [p["median"][item] for p in points if p["median"][item]] + [RESERVE[item]]
    lo, hi = min(vals) - 3, max(vals) + 3
    x = lambda t: L + (t - t0) / max(t1 - t0, 1) * (W - L - 8)
    y = lambda v: 8 + (hi - v) / max(hi - lo, 1) * (H - B - 8)
    step = lambda key: " ".join(f"{x(p['t']):.1f},{y(p[key][item]):.1f} {x(p['end']):.1f},{y(p[key][item]):.1f}" for p in points)
    dots = "".join(f'<circle cx="{x((p["t"] + p["end"]) / 2):.1f}" cy="{y(p["median"][item]):.1f}" r="2.2" class="dot"/>'
                   for p in points if p["median"][item])
    rule = f'<line x1="{x(r3):.1f}" x2="{x(r3):.1f}" y1="0" y2="{H - B}" class="r3"/><text x="{x(r3) + 4:.1f}" y="12" class="lab">Round 3</text>' \
        if r3 and t0 <= r3 <= t1 else ""
    ticks = "".join(f'<text x="4" y="{y(v) + 4:.1f}" class="lab">{v}</text><line x1="{L}" x2="{W}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                    for v in sorted({RESERVE[item], round(lo + 3), round(hi - 3)}))
    days = []
    d = datetime.datetime.fromtimestamp(t0).replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
    while d.timestamp() < t1:
        days.append(f'<text x="{x(d.timestamp()):.1f}" y="{H - 6}" class="lab">{d:%m/%d}</text>')
        d += datetime.timedelta(days=1)
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{item} prices">{ticks}{"".join(days)}{rule}'
            f'<polyline points="{step("target")}" class="target"/><polyline points="{step("open")}" class="open"/>{dots}</svg>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round3", help="Round 3 start as YYYY-MM-DD HH:MM, when the ledger cache has no round entry yet")
    a = ap.parse_args()
    starts = round_starts()
    r3 = datetime.datetime.strptime(a.round3, "%Y-%m-%d %H:%M").timestamp() if a.round3 else starts.get(3)
    now = datetime.datetime.now().timestamp()
    start = (r3 or now) - DAYS * 86400
    points = load(start)
    os.makedirs(OUT, exist_ok=True)

    rows = []
    for item in ITEMS:
        b = weighted(points, item, start, r3 or now + 1)
        a3 = weighted(points, item, r3, now + 1) if r3 else (None, None, 0, 0)
        f = lambda v: f"{v:.1f}" if v is not None else "-"
        rows.append(f"| {item} | {f(b[0])} | {f(a3[0])} | {f(b[1])} | {f(a3[1])} | {b[2] / max(b[3], 1e-9):.1f} | "
                    f"{(a3[2] / a3[3]) if a3[3] else 0:.1f} |")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    r3s = datetime.datetime.fromtimestamp(r3).strftime("%Y-%m-%d %H:%M") if r3 else "not started yet"
    md = "\n".join([
        f"# Reprice: before and after Round 3 hid the budgets ({stamp})", "",
        f"Round 3 start: {r3s}. Before = the {DAYS} days up to it. Ask = time-weighted opening ask; price = deal-weighted "
        "median deal price (settled deals only in Round 3); deals/h = our deals per hour.", "",
        "| Item | Ask before | Ask after | Price before | Price after | Deals/h before | Deals/h after |",
        "|---|---|---|---|---|---|---|", *rows, ""])
    with open(os.path.join(OUT, "summary.md"), "w") as f:
        f.write(md)

    charts = "".join(f"<h2>{item}</h2>{svg(points, item, r3)}" for item in ITEMS)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reprice dashboard</title><style>
:root{{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--line:#e7e5e4;--a:#b45309;--b:#0f766e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--line:#44403c;--a:#f59e0b;--b:#2dd4bf}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0}}main{{max-width:800px;margin:0 auto;padding:24px 16px}}
svg{{width:100%;height:auto}}.open{{fill:none;stroke:var(--a);stroke-width:2}}.target{{fill:none;stroke:var(--a);stroke-width:1;stroke-dasharray:4 3}}
.dot{{fill:var(--b)}}.r3{{stroke:var(--muted);stroke-dasharray:2 2}}.grid{{stroke:var(--line)}}.lab{{fill:var(--muted);font-size:11px}}
table{{border-collapse:collapse}}td,th{{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right}}td:first-child,th:first-child{{text-align:left}}
</style></head><body><main><h1>Reprice dashboard</h1>
<p>Orange line: our opening ask. Dashed: target. Teal dots: median price of our deals in each meta-harness version
(settled deals only from Round 3). Generated {stamp} from strategies.jsonl.</p>
<pre>{html.escape(md)}</pre>{charts}</main></body></html>"""
    with open(os.path.join(OUT, "index.html"), "w") as f:
        f.write(page)
    print(md)


if __name__ == "__main__":
    main()
