"""Line watch (11/4 night): the meta-harness's first fast loop. Every 20 minutes, after the ledger is
collected, each premium line product (restock.LINE) is judged on the market and on our own results, and
analysis/line.json, the caps restock follows, is rewritten. The line went in hard on the user's call; this is
how it gets out fast when others crowd in.

    exit    cap 0: the market's median paid share for it over the last WINDOW is EXIT_DROP of list or more
            under its baseline, or we have held a unit of it STALL without selling one
    shrink  cap - 1, not below 1: the median is SHRINK_DROP or more under the baseline, or it had at least
            twice the baseline's sellers in WINDOW and CROWD_MORE more
    grow    cap + 1, up to restock.LINE_MAX_UNITS: we sold GROW_SALES or more of it in the last hour, and
            the market is not shrinking it
    back    cap 1 again after an exit, once EXIT_REST has passed, we hold none, and the market (MIN_N settles
            in WINDOW) still pays REENTRY_MIN of list or more; the baseline is taken again then
    keep    otherwise

The market is other shops' settles of the product over the last WINDOW (ours left out), its median share of
the list price and its sellers; a verdict needs MIN_N of them. The baseline is that same window's figure the
first time it holds MIN_N settles (so a product is never judged against a day-long average, nor against an
empty hour), kept in analysis/line_state.json with each product's cap. A cap starts at restock.LINE's, which is
read again every run, and goes back to it when restock.LINE's changes: a person's decision wins, and so does a
line.json a person wrote (its "by" is not "linewatch"): it is left alone. Every change is logged to
analysis/line_log.jsonl with the numbers behind it. (Review, 11/4 night: a 24 h baseline against a 2 h figure
exited on the first pass and never came back; our own settles counted as the market.)
"""
import importlib
import json
import statistics as st

import common
import ledger
import restock

WINDOW = 2 * 3600
SHRINK_DROP, EXIT_DROP = 0.03, 0.06
CROWD_TIMES, CROWD_MORE = 2, 3
GROW_SALES = 2
STALL = 4 * 3600
EXIT_REST = 2 * 3600
REENTRY_MIN = 0.70       # back in only where others still settle at this share of list or more (A costs 0.55-0.75)
MIN_N = 20


def folder():
    return common.MARKET_DIR / "analysis"


def market(sku: str, settles: list, names: dict, lp: float, since: float) -> dict:
    """{n, median, sellers}: other shops' settles of the product since `since` (ours left out)."""
    rows = [s for s in settles if s.get("item") == sku and (s.get("ts") or 0) >= since and isinstance(s.get("amount"), (int, float))
            and not ledger.is_ours(names.get(s.get("shop"), s.get("shop")))]
    return {"n": len(rows), "median": round(st.median(s["amount"] / lp for s in rows), 4) if rows and lp else None,
            "sellers": len({names.get(s.get("shop"), s.get("shop")) for s in rows})}


def ours(sku: str, units: list, sales: list, now: float) -> dict:
    """{held, sold_1h, idle}: units we hold, our sales in the last hour, and seconds since the later of our last
    sale of it and the arrival of the oldest unit we hold (None when we hold none)."""
    held = [u for u in units if u.get("sku") == sku]
    mine = [s for s in sales if s.get("sku") == sku]
    last = max((s.get("ts") or 0 for s in mine), default=0)
    first = min((u.get("supplied_at") or now for u in held), default=None)
    return {"held": len(held), "sold_1h": sum(1 for s in mine if (s.get("ts") or 0) >= now - 3600),
            "idle": now - max(last, first) if first is not None else None}


def decide(cap: int, base: dict | None, now_m: dict, us: dict, exit_at: float | None, now: float) -> tuple[int, str]:
    """(new cap, why) for one product."""
    verdict = bool(base) and base.get("median") is not None and now_m["n"] >= MIN_N and now_m["median"] is not None
    drop = round(base["median"] - now_m["median"], 4) if verdict else 0.0
    crowded = verdict and now_m["sellers"] >= max(CROWD_TIMES * base.get("sellers", 0), base.get("sellers", 0) + CROWD_MORE)
    if cap == 0:
        if (exit_at is not None and now - exit_at >= EXIT_REST and us["held"] == 0 and now_m["n"] >= MIN_N
                and now_m["median"] is not None and now_m["median"] >= REENTRY_MIN):
            return 1, f"back in: others settle at {now_m['median']:.3f} of list and we hold none"
        return 0, "out"
    if us["idle"] is not None and us["idle"] > STALL:
        return 0, f"held {us['idle'] / 3600:.1f} h with no sale"
    if drop >= EXIT_DROP:
        return 0, f"market median {now_m['median']:.3f} is {drop:.3f} under the baseline {base['median']:.3f}"
    if drop >= SHRINK_DROP or crowded:
        why = (f"market median {now_m['median']:.3f}, {drop:.3f} under the baseline" if drop >= SHRINK_DROP
               else f"{now_m['sellers']} sellers in 2 h against {base.get('sellers')} at the baseline")
        return max(1, cap - 1), why
    if us["sold_1h"] >= GROW_SALES and cap < restock.LINE_MAX_UNITS:
        return cap + 1, f"we sold {us['sold_1h']} in the last hour"
    return cap, "keep"


def run(now: float, settles: list | None = None, inv: dict | None = None, sales: list | None = None,
        line: dict | None = None) -> list:
    """Judge every line product, rewrite analysis/line.json, log the changes; returns them."""
    current = common.read_json(folder() / "line.json", None)
    if isinstance(current, dict) and current.get("by") != "linewatch":
        return []                                       # a person wrote it: theirs, not touched
    if line is None:
        importlib.reload(restock)                       # restock.LINE as it is on disk now, not as the service started
        line = dict(restock.LINE)
    catalog = common.catalog()
    if not catalog:
        return []                                       # nothing to judge with: line.json is left as it is
    settles = settles if settles is not None else [s for s in common.read_jsonl(ledger.folder() / "settles.jsonl")
                                                   if (s.get("ts") or 0) >= now - WINDOW]
    inv = inv if inv is not None else (common.market_file("inventory.json", {}) or {})
    sales = sales if sales is not None else [s for s in common.records("sales") if (s.get("ts") or 0) >= now - 86400]
    names = ledger.names()
    state = common.read_json(folder() / "line_state.json", {}) or {}
    if not isinstance(state, dict):
        state = {}
    caps, defaults, base, exits = (state.setdefault(k, {}) for k in ("caps", "defaults", "base", "exit_at"))
    changes = []
    for sku, default in line.items():
        lp = (catalog.get(sku) or {}).get("list_price")
        if not lp or not isinstance(default, int):
            continue
        if defaults.get(sku) != default:                  # new, or restock.LINE changed: a person's decision
            caps[sku], defaults[sku] = default, default
            base.pop(sku, None)
            exits.pop(sku, None)
        now_m = market(sku, settles, names, lp, now - WINDOW)
        if sku not in base and now_m["n"] >= MIN_N:
            base[sku] = {"median": now_m["median"], "sellers": now_m["sellers"], "at": int(now)}
        us = ours(sku, inv.get("units") or [], sales, now)
        new, why = decide(caps.get(sku, default), base.get(sku), now_m, us, exits.get(sku), now)
        if new != caps.get(sku, default):
            changes.append({"at": int(now), "sku": sku, "from": caps.get(sku, default), "to": new, "why": why,
                            "market": now_m, "baseline": base.get(sku), "ours": us})
            if new == 0:
                exits[sku] = int(now)
            elif caps.get(sku, default) == 0:                # back in: judged from here on against today's market
                base[sku] = {"median": now_m["median"], "sellers": now_m["sellers"], "at": int(now)}
            caps[sku] = new
    for sku in [s for s in list(caps) if s not in line]:   # dropped from restock.LINE by a person
        for k in ("caps", "defaults", "base", "exit_at"):
            state[k].pop(sku, None)
    common.write_json(folder() / "line_state.json", state)
    common.write_json(folder() / "line.json", {"skus": {s: caps[s] for s in line if s in caps}, "at": int(now), "by": "linewatch"})
    if changes:
        with open(folder() / "line_log.jsonl", "a", encoding="utf-8") as f:
            for c in changes:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return changes
