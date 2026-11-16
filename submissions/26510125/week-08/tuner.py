"""Meta-harness: once an hour the agent reads its own log, measures what happened, and lets the model move three
knobs within fixed bounds. This is the loop I ran by hand for ten days (grep the log, change a constant, restart);
now it is the agent's. Every decision and its reason go to logs/tuner.log; params.json is what seller.py,
buyer.py and market_buyer.py read.

    python tuner.py            # measure the last hour and decide
    python tuner.py --dry      # measure only
"""
import json, re, statistics, sys, time
from pathlib import Path

import harness

PARAMS = Path("params.json")
LOG = Path("logs/market-kit.log")
BOUNDS = {"ask_factor": (0.85, 1.05), "first_offer": (0.75, 0.95), "price_cap": (0.65, 0.80)}   # above 0.80 a deal lowers Saved (mean discount share)
DEFAULT = {"ask_factor": 0.95, "first_offer": 0.85, "price_cap": 0.75}


def params() -> dict:
    try:
        p = json.loads(PARAMS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        p = {}
    out = {}
    for k, v in DEFAULT.items():
        try:
            out[k] = min(max(float(p.get(k, v)), BOUNDS[k][0]), BOUNDS[k][1])
        except (ValueError, TypeError):
            out[k] = v
    return out


def measure(lines, since: float) -> dict:
    """Counts from the kit log lines newer than `since` (log lines start with HH:MM:SS of today)."""
    now = time.localtime(); keep = []
    for l in lines:
        m = re.match(r"(\d\d):(\d\d):(\d\d) ", l)
        if not m:
            continue
        t = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, int(m[1]), int(m[2]), int(m[3]), 0, 0, -1))
        if t > time.time() + 60:                       # a line from before midnight
            t -= 86400
        if t >= since:
            keep.append(l)
    deals = [(int(a), int(c)) for a, c in re.findall(r"\[deal\] \S+ grade \w at (\d+) \(cost (\d+)\)", "\n".join(keep))]
    walks = [int(o) / int(f) for o, f in re.findall(r"rejected at offer (\d+) vs floor (\d+)", "\n".join(keep)) if int(f)]
    n = lambda pat: sum(1 for l in keep if pat in l)
    return {"visits": n("[visit]"), "deals": len(deals), "walks": len(walks),
            "margin_pct": round(100 * (sum(a for a, _ in deals) - sum(c for _, c in deals)) / max(sum(c for _, c in deals), 1), 1),
            "walk_offer_over_floor": round(statistics.median(walks), 2) if walks else None,
            "orders": n("[order o-"), "bought": n("[done] bought"), "no_offer": n("[done] no_offer"),
            "buyer_rejected": n("[done] rejected") + n("[done] walked"), "paid": n("[acp-buyer] done: paid"),
            "refused_grade": n("not paying: grade"), "refused_shelf": n("h of shelf life left")}


def history(n=4):
    """The last decisions with the metrics of the hour that followed: the tuner's own memory, so it does not
    repeat a move that just failed (it flip-flopped ask_factor three times in its first hours without this)."""
    rows = []
    try:
        rows = [json.loads(l) for l in Path("logs/tuner.log").read_text(encoding="utf-8").splitlines()[-n - 1:]]
    except OSError:
        pass
    out = []
    for prev, nxt in zip(rows, rows[1:]):
        ch = {k: (prev["from"][k], prev["to"][k]) for k in prev["to"] if prev["from"][k] != prev["to"][k]}
        out.append({"at": prev["t"], "changed": ch or "kept", "next_hour": {k: nxt["metrics"][k] for k in ("deals", "margin_pct", "paid", "walks")}})
    return out


def rollback(metrics: dict, cur: dict):
    """Code, not the model: if the hour after a change lost more than half its deals AND paid deals, the change is
    undone. The last line of tuner.log holds the previous knobs and the hour that preceded the change."""
    try:
        last = json.loads(Path("logs/tuner.log").read_text(encoding="utf-8").splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return None
    if last["from"] == last["to"] or last["to"] != cur:
        return None
    before = last["metrics"]
    if before["deals"] >= 20 and metrics["deals"] < before["deals"] / 2 and metrics["paid"] < max(before["paid"], 2) / 2:
        return dict(last["from"], why=f"rollback: deals {before['deals']}->{metrics['deals']}, paid {before['paid']}->{metrics['paid']} after {last['to']}")
    return None


def decide(metrics: dict, cur: dict, log=print) -> dict:
    """The model moves the knobs; code clamps them to BOUNDS and falls back to the current values."""
    rb = rollback(metrics, cur)
    if rb:
        return rb
    data = harness.ask_json(
        "You tune a shop and a buyer agent in a market where only settled deals count. Shop score: book (cash + stock at "
        "cost) 0.5, revenue 0.3, stars 0.2. Buyer score: Saved = mean discount share per deal 0.5, deals 0.1. A deal whose "
        "discount share is below the running mean lowers Saved.\n"
        f"Your last decisions and what the next hour did: {json.dumps(history())}\n"
        f"Last hour: {json.dumps(metrics)}\nCurrent knobs: {json.dumps(cur)}\nKnobs: ask_factor = shop's opening ask as a "
        "share of the market median (lower: more deals, less margin; the floor is cost anyway); first_offer = buyer's "
        "first offer as a share of the median (lower: better Saved, more walk-aways); price_cap = the most the buyer pays "
        f"as a share of list (lower: better Saved, fewer deals). Bounds: {json.dumps(BOUNDS)}. Move at most one knob by at "
        'most 0.05, or keep all. Reply with exactly one JSON object: {"ask_factor": x, "first_offer": y, "price_cap": z, '
        '"why": "<one sentence>"}.', log, model="opus") or {}      # 24 calls a day: the strongest model
    new = {}
    for k, (lo, hi) in BOUNDS.items():
        v = data.get(k)
        v = float(v) if isinstance(v, (int, float)) else cur[k]
        new[k] = round(min(max(v, lo, cur[k] - 0.05), hi, cur[k] + 0.05), 2)     # one step, inside the bounds
    new["why"] = str(data.get("why", "no answer; kept"))[:200]
    return new


def main():
    cur = params()
    lines = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-60000:] if LOG.exists() else []
    m = measure(lines, time.time() - 3600)
    if "--dry" in sys.argv:
        print(json.dumps({"metrics": m, "params": cur}, ensure_ascii=False)); return
    new = decide(m, cur, log=lambda s: None)
    PARAMS.write_text(json.dumps({k: new[k] for k in DEFAULT}, indent=1), encoding="utf-8")
    with Path("logs/tuner.log").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "metrics": m, "from": cur, "to": {k: new[k] for k in DEFAULT},
                            "why": new["why"]}, ensure_ascii=False) + "\n")
    changed = {k: (cur[k], new[k]) for k in DEFAULT if cur[k] != new[k]}
    print(f"[tuner] {changed or 'kept'}: {new['why']}", flush=True)


if __name__ == "__main__":
    main()
