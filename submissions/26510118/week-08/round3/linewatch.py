"""Line watch (11/4 night): the meta-harness's first fast loop. Every 20 minutes, after the ledger is
collected, each premium line product (restock.LINE) is judged on the market and on our own results, and
analysis/line.json, the caps restock follows, is rewritten. The line went in hard on the user's call; this is
how it gets out fast when others crowd in.

    exit    cap 0: the market's median paid share for it over the last WINDOW is EXIT_DROP of list or more
            under its baseline, or we have held a unit of it STALL without selling one
    shrink  cap - 1, not below 1: the median is SHRINK_DROP or more under the baseline, or it had at least
            twice the baseline's sellers in WINDOW and CROWD_MORE more
    grow    up to what we sold of it in the last hour (at least cap + 1), up to restock.LINE_MAX_UNITS: we sold
            GROW_SALES or more of it in the last hour, and the market is not shrinking it
    back    cap 1 again after an exit, once EXIT_REST has passed, we hold none, and the market (MIN_N settles
            in WINDOW) still pays REENTRY_MIN of list or more; the baseline is taken again then
    keep    otherwise

New products (11/4 23:40, the person's call: the line follows unmet demand rather than a list): every run, other
shops' settles of the last FIND_WINDOW pick products of FIND_LIST_MIN or more that sold FIND_N times or more at
FIND_MEDIAN of list or more with FIND_SELLERS sellers or fewer, best expected margin first, until the line holds
LINE_MAX_SKUS products with a cap above 0. Each comes in at FIND_CAP and is judged like the rest from then on;
one out (cap 0) for FIND_DROP is let go.
The twins (11/5 01:00, the person's call): dear products our own buyer could not find in any shop come in too. A
product of UNMET_LIST_MIN or more that UNMET_MIN or more of our buyer's orders accepted in the last UNMET_WINDOW
and found nowhere (records/unmet.jsonl), with budgets of UNMET_BUDGET of its list or more, joins at UNMET_CAP, at
most UNMET_MAX at a time: the market asks every buyer for much the same, so other buyers want it too, and no shop
sells it (our own buyer cannot buy from us: same owner). Nothing selling it, no market verdict: the stall rule
(held STALL with no sale) takes it out. (11/4 night: 3 bed orders in 6 h with budgets of 3.6-5.1M for beds listed
2.0-4.0M, sold once by other shops.)

One guard (the minimum the person asked for, 11/4 23:40): no cap grows and no product comes back in while our
stars on the board (scoreboard.py's stars_rank, a fifth of the shop's score) are under STARS_MIN, and under it
every cap above 1 comes down one, at most once every STARS_REST. The cash kept is restock.RESERVE; the market
rules above are the rest. (A guard on the line's share of our stock, then of our book, ran from 23:07 to 23:40:
next to cheap goods the line was 85-89% of the stock at once, and its units sold within half an hour. The share
is still reported.)

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
from collections import defaultdict

import common
import ledger
import pricing
import restock
import scoreboard

WINDOW = 2 * 3600
SHRINK_DROP, EXIT_DROP = 0.03, 0.06
CROWD_TIMES, CROWD_MORE = 2, 3
GROW_SALES = 2
STALL = 8 * 3600         # out (cap 0) held this long with no sale (4 h until 11/6: the expected margin now exits first); restock.LINE_STALL (2 h) stops buying more sooner
EXIT_REST = 2 * 3600
REENTRY_MIN = 0.70       # back in only where others still settle at this share of list or more (A costs 0.55-0.75)
MIN_N = 20
STARS_MIN, STARS_REST = 3.50, 2 * 3600   # 4.30 until 11/6 13:10 (the person's call; ours 4.36, a few bad-faith accounts
                                         # could move it under and cut the line for no fault of the goods); 4.80 until 11/5 09:20: one buyer (totato2203-r3) gives every shop 1 star on
                                         # every purchase (67 of 67) and bought 18% of ours; our stars fell 4.84 -> 4.72
                                         # and the guard cut the line's caps 35 times in 9 h for no fault of the goods.
                                         # 4.60 until 11/5 15:00: from 12:43 shaun0927's two buyers gave us 1 star on 30 of
                                         # 30 deals and the other top shops most of theirs (inextro 17/19, yms979 10/16),
                                         # the rest almost none (kimauve 1/16); selling less cannot lift stars given
                                         # whatever we sell, so the guard is left for a real fault of the goods
FIND_WINDOW = 6 * 3600
FIND_LIST_MIN, FIND_N, FIND_MEDIAN, FIND_SELLERS = 500_000, 8, 0.66, 99    # sellers 12 until 11/6: the margin decides;
                         # 12 at 0.70 until 11/6 15:40 (29 of 40 line places used; the expected margin still gates);
                         # 20 settles at 0.74 until 11/6 13:10 (the person's call: 281M cash idle against 74M of stock,
                         # 12 buyers in 6 h turned away for want of stock, 59% of talks walked: breadth, not depth)
# Expected margin (Kiro 2.0 stage 1, 11/6): a goods product's share of list a unit earns on average, over the supply's
# grades (A 70%, B 20%, C 10%; mid costs 0.65, 0.50, 0.35 times the surge), sold at its grade's market with the
# warranty (others_6h "_w" cells, 10+; a missing B or C taken 0.05 / 0.10 under A) less the refunds (pricing.BREAK).
# A line product under EM_EXIT leaves; a new one needs EM_FIND. Unknown (no A market of its own): the old rules.
EM_EXIT, EM_FIND = 0.02, 0.04     # 0.03, 0.05 until 11/6 13:10
SUPPLY_MIX = {"A": (0.7, 0.65), "B": (0.2, 0.50), "C": (0.1, 0.35)}
                         # 0.76 and 10 until 11/5 09:20: the one-hour experiment at 0.74 / 12 / 40 found two products
                         # that sold 52 and 40 units in the next 5 h, 10.6M over cost (its own verdict, on the shop's
                         # hourly margin, had ended it)
FIND_CAP = 3             # 2 until 11/6 13:10
LINE_MAX_SKUS = 40       # 20 until 11/5 01:00, 30 until 09:20
UNMET_WINDOW, UNMET_MIN, UNMET_LIST_MIN, UNMET_CAP, UNMET_MAX = 12 * 3600, 3, 300_000, 2, 10   # 1 order until 11/6:
                         # overnight 48 products joined on one unmet order each; most left 4 h later unsold
                         # 6 h, 2 orders, 500k, cap 1, 5 until 11/5 02:20 (the first beds sold within minutes)
UNMET_BUDGET = 0.85      # ...and the orders' budgets cover this share of its list (a buyer can pay our ask)
FIND_DROP = 12 * 3600
COST_GUESS = 0.65        # a grade A unit's cost as a share of list (0.55-0.75): the margin a candidate is ranked by
# An experiment (11/5 04:00, the person's call: "one hour; if it pays, run it 5 more hours, no need to ask"):
# analysis/experiment.json {"start", "until", "params": {"find_median", "find_sellers", "line_max_skus"}} relaxes the
# discovery while start <= now < until. After TRIAL it judges itself once: kept EXTEND longer when the products it
# added sold EXP_MIN_SOLD units or more at a positive price - cost and the whole shop's price - cost per hour held at
# EXP_KEEP_RATE of the 3 hours before it; else it ends there. Either way the verdict is logged and kept in the file.
EXPERIMENT_FILE = "experiment.json"
TRIAL, EXTEND = 3600, 5 * 3600
EXP_MIN_SOLD, EXP_KEEP_RATE = 2, 0.9


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
    return {"held": len(held), "sold_1h": sum(1 for s in mine if (s.get("ts") or 0) >= now - restock.LINE_PACE),
            "idle": now - max(last, first) if first is not None else None}


def decide(cap: int, base: dict | None, now_m: dict, us: dict, exit_at: float | None, now: float,
           hold: str | None = None) -> tuple[int, str]:
    """(new cap, why) for one product; hold: why no cap may grow now (a guard), or None."""
    verdict = bool(base) and base.get("median") is not None and now_m["n"] >= MIN_N and now_m["median"] is not None
    drop = round(base["median"] - now_m["median"], 4) if verdict else 0.0
    crowded = verdict and now_m["sellers"] >= max(CROWD_TIMES * base.get("sellers", 0), base.get("sellers", 0) + CROWD_MORE)
    if cap == 0:
        if (not hold and exit_at is not None and now - exit_at >= EXIT_REST and us["held"] == 0 and now_m["n"] >= MIN_N
                and now_m["median"] is not None and now_m["median"] >= REENTRY_MIN):
            return 1, f"back in: others settle at {now_m['median']:.3f} of list and we hold none"
        return 0, "out"
    if us["idle"] is not None and us["idle"] > STALL:
        return 0, f"held {us['idle'] / 3600:.1f} h with no sale"
    if drop >= EXIT_DROP:
        return 0, f"market median {now_m['median']:.3f} is {drop:.3f} under the baseline {base['median']:.3f}"
    if drop >= SHRINK_DROP or crowded:
        why = (f"market median {now_m['median']:.3f}, {drop:.3f} under the baseline" if drop >= SHRINK_DROP
               else f"{now_m['sellers']} sellers in {WINDOW // 3600} h against {base.get('sellers')} at the baseline")
        return max(1, cap - 1), why
    if us["sold_1h"] >= GROW_SALES and cap < restock.LINE_MAX_UNITS:
        if hold:
            return cap, f"keep: sold {us['sold_1h']} in the last hour, but {hold}"
        return min(restock.LINE_MAX_UNITS, max(cap + 1, us["sold_1h"])), f"we sold {us['sold_1h']} in the last hour"
    return cap, "keep"


def guards(units: list, line: dict, stars: float | None, balance=None) -> dict:
    """{share: the line's share of our book (cash + stock at cost; reported only), stars, hold: why no cap grows
    (or None), low_stars}."""
    cash = balance if isinstance(balance, (int, float)) and not isinstance(balance, bool) and balance > 0 else 0
    book = cash + sum(u.get("cost") or 0 for u in units)
    share = round(sum(u.get("cost") or 0 for u in units if u.get("sku") in line) / book, 3) if book else 0.0
    low = stars is not None and stars < STARS_MIN
    hold = f"our stars are {stars} (under {STARS_MIN})" if low else None
    return {"share": share, "stars": stars, "hold": hold, "low_stars": low}


def unmet_wanted(unmet: list, catalog: dict, line: dict, now: float) -> dict:
    """{sku: {orders, list, budget}}: dear products in UNMET_MIN or more of our buyer's unmet orders of the last
    UNMET_WINDOW, with a median budget of UNMET_BUDGET of the list or more; most orders first."""
    seen, budgets = defaultdict(int), defaultdict(list)
    for u in unmet:
        if (u.get("ts") or 0) < now - UNMET_WINDOW:
            continue
        for sku in set(u.get("acceptable") or []):
            if sku not in line and ((catalog.get(sku) or {}).get("list_price") or 0) >= UNMET_LIST_MIN:
                seen[sku] += 1
                if isinstance(u.get("budget"), (int, float)):
                    budgets[sku].append(u["budget"])
    out = {}
    for sku, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        lp = catalog[sku]["list_price"]
        b = st.median(budgets[sku]) if budgets[sku] else 0
        if n >= UNMET_MIN and b >= UNMET_BUDGET * lp:
            out[sku] = {"orders": n, "list": lp, "budget": int(b)}
    return out


def experiment(now: float) -> dict:
    """The params of the experiment running now (analysis/experiment.json), else {}."""
    e = common.read_json(folder() / EXPERIMENT_FILE, None)
    ok = isinstance(e, dict) and (e.get("start") or 0) <= now < (e.get("until") or 0) and isinstance(e.get("params"), dict)
    return e["params"] if ok else {}


def judge(now: float, found: dict, sales: list) -> dict | None:
    """The experiment's one verdict, TRIAL after its start: {sold, margin, rate, before, kept}; None when not due."""
    path = folder() / EXPERIMENT_FILE
    e = common.read_json(path, None)
    if not isinstance(e, dict) or e.get("verdict") or now < (e.get("start") or 0) + TRIAL:
        return None
    start = e["start"]
    mine = {s for s, f in found.items() if f.get("exp")}
    sold = [s for s in sales if s.get("sku") in mine and (s.get("ts") or 0) >= start]
    margin = sum((s.get("price") or 0) - (s.get("cost") or 0) for s in sold)
    rate = lambda a, b: sum((s.get("price") or 0) - (s.get("cost") or 0) for s in sales if a <= (s.get("ts") or 0) < b) / max(1, (b - a) / 3600)
    during, before = rate(start, now), rate(start - 3 * 3600, start)
    kept = len(sold) >= EXP_MIN_SOLD and margin > 0 and during >= EXP_KEEP_RATE * before
    e["verdict"] = {"at": int(now), "products": sorted(mine), "sold": len(sold), "margin": int(margin),
                    "rate": int(during), "before": int(before), "kept": kept}
    e["until"] = int(now + EXTEND) if kept else int(now)
    common.write_json(path, e)
    return e["verdict"]


def expected_margin(sku: str, summary: dict, surge: dict):
    """The expected price - cost of one unit as a share of list (see EM_EXIT), or None without an A market."""
    cells = ((summary.get("by_sku") or {}).get(sku) or {}).get("others_6h") or {}
    ref = lambda g: (lambda d: d.get("median") if (d.get("n") or 0) >= 10 else None)(cells.get(g + "_w") or {})
    a = ref("A")
    if a is None:
        return None
    m = {"A": a, "B": ref("B") or a - 0.05, "C": ref("C") or a - 0.10}
    f = surge.get(sku, 1.0)
    return round(sum(share * (m[g] * (1 - pricing.BREAK[g]) - cost * f) for g, (share, cost) in SUPPLY_MIX.items()), 4)


def discover(settles: list, names: dict, catalog: dict, line: dict, caps: dict, now: float, unmet: list | None = None,
             params: dict | None = None, margin=None) -> dict:
    """{sku: {n, median, sellers, list, at}}: new products for the line from other shops' settles of the last
    FIND_WINDOW, best expected margin (settles x list x (median - COST_GUESS)) first, as many as leave the line at
    LINE_MAX_SKUS products with a cap above 0. params: an experiment's find_median, find_sellers, line_max_skus;
    what it adds is marked exp."""
    params = params or {}
    least, most = params.get("find_median", FIND_MEDIAN), params.get("find_sellers", FIND_SELLERS)
    room = params.get("line_max_skus", LINE_MAX_SKUS) - sum(1 for s in line if caps.get(s, line[s]) > 0)
    if room <= 0:
        return {}
    by = defaultdict(list)
    for s in settles:
        if (s.get("ts") or 0) < now - FIND_WINDOW or s.get("item") in line or not isinstance(s.get("amount"), (int, float)):
            continue
        shop = names.get(s.get("shop"), s.get("shop"))
        if not ledger.is_ours(shop):
            by[s.get("item")].append((s["amount"], shop))
    picks = []
    for sku, rows in by.items():
        lp = (catalog.get(sku) or {}).get("list_price") or 0
        if lp < FIND_LIST_MIN or len(rows) < FIND_N:
            continue
        m = st.median(a / lp for a, _ in rows)
        sellers = len({sh for _, sh in rows})
        em = margin(sku) if margin else None
        if m >= least and sellers <= most and (em is None or em >= EM_FIND):
            picks.append((len(rows) * lp * (m - COST_GUESS), sku, {"n": len(rows), "median": round(m, 4), "sellers": sellers, "list": lp}))
    picks.sort(key=lambda x: -x[0])
    out = {sku: dict(info, at=int(now), cap=FIND_CAP, source="market", **({"exp": True} if params else {}))
           for _, sku, info in picks[:room]}
    room -= len(out)
    for sku, info in list(unmet_wanted(unmet or [], catalog, line, now).items())[:max(0, min(room, UNMET_MAX))]:
        if sku not in out:
            out[sku] = dict(info, at=int(now), cap=UNMET_CAP, source="our buyer's unmet orders")
    return out


def brief(now: float, hours: float = 6) -> dict:
    """The line as the integrated analysis reads it: the caps now, the guards, and the changes of the last hours."""
    state = common.read_json(folder() / "line_state.json", {}) or {}
    line = common.read_json(folder() / "line.json", {}) or {}
    log = [c for c in common.read_jsonl(folder() / "line_log.jsonl") if (c.get("at") or 0) >= now - hours * 3600]
    return {"caps": line.get("skus") if isinstance(line, dict) else None,
            "guards": state.get("guards") if isinstance(state, dict) else None,
            f"changes_{int(hours)}h": [[c.get("sku"), c.get("from"), c.get("to"), str(c.get("why"))[:70]] for c in log[-8:]]}


def run(now: float, settles: list | None = None, inv: dict | None = None, sales: list | None = None,
        line: dict | None = None, stars: float | None = None, unmet: list | None = None) -> list:
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
    settles = settles if settles is not None else common.read_jsonl(ledger.folder() / "settles.jsonl",
                                                                    since=now - max(WINDOW, FIND_WINDOW))
    inv = inv if inv is not None else (common.market_file("inventory.json", {}) or {})
    sales = sales if sales is not None else [s for s in common.records("sales") if (s.get("ts") or 0) >= now - 86400]
    names = ledger.names()
    g = guards(inv.get("units") or [], line, stars if stars is not None else scoreboard.stars(now), inv.get("balance"))
    state = common.read_json(folder() / "line_state.json", {}) or {}
    if not isinstance(state, dict):
        state = {}
    caps, defaults, base, exits = (state.setdefault(k, {}) for k in ("caps", "defaults", "base", "exit_at"))
    found = state.setdefault("found", {})
    for sku in [s for s in list(found) if caps.get(s) == 0 and now - (exits.get(s) or now) > FIND_DROP]:
        del found[sku]                                  # out too long: let go (its state goes below, with the line's)
    line = dict(line, **{s: (f.get("cap") or FIND_CAP) for s, f in found.items() if s not in line})
    unmet = unmet if unmet is not None else [u for u in common.records("unmet") if (u.get("ts") or 0) >= now - UNMET_WINDOW]
    cut = g["low_stars"] and now - (state.get("stars_cut_at") or 0) >= STARS_REST
    changes = []
    verdict = judge(now, found, sales)
    if verdict:
        changes.append({"at": int(now), "sku": "experiment", "from": "1 h trial", "to": "5 h more" if verdict["kept"] else "ended",
                        "why": f"its {len(verdict['products'])} products sold {verdict['sold']} for {verdict['margin']:,} over cost; "
                               f"shop {verdict['rate']:,}/h against {verdict['before']:,}/h before", "market": verdict})
    summary = common.read_json(common.MARKET_DIR / "market_summary.json", {}) or {}
    surge = {s.get("sku"): float(s.get("factor") or 1.0) for s in inv.get("surge") or [] if isinstance(s, dict)}
    margin = lambda s: expected_margin(s, summary, surge)
    for sku, info in discover(settles, names, catalog, line, caps, now, unmet, experiment(now), margin).items():
        found[sku], line[sku] = info, info["cap"]
        why = (f"added: others sold it {info['n']} times in {FIND_WINDOW // 3600} h at {info['median']:.3f} of list, {info['sellers']} sellers"
               if info["source"] == "market" else
               f"added: {info['orders']} of our buyer's orders in {UNMET_WINDOW // 3600} h wanted it and no shop had it "
               f"(budget {info['budget']}, list {info['list']})")
        changes.append({"at": int(now), "sku": sku, "from": 0, "to": info["cap"], "market": info, "why": why})
    for sku, default in line.items():
        lp = (catalog.get(sku) or {}).get("list_price")
        if not lp or not isinstance(default, int):
            continue
        fresh = defaults.get(sku) != default
        if fresh:                                         # new, or restock.LINE changed: a person's decision
            caps[sku], defaults[sku] = default, default
            base.pop(sku, None)
            exits.pop(sku, None)
        now_m = market(sku, settles, names, lp, now - WINDOW)
        if sku not in base and now_m["n"] >= MIN_N:
            base[sku] = {"median": now_m["median"], "sellers": now_m["sellers"], "at": int(now)}
        us = ours(sku, inv.get("units") or [], sales, now)
        new, why = decide(caps.get(sku, default), base.get(sku), now_m, us, exits.get(sku), now, g["hold"])
        em = margin(sku)
        if new > 0 and em is not None and em < EM_EXIT:
            new, why = 0, f"expected margin {em:.3f} of list, under {EM_EXIT}"
        if cut and new > 1 and new >= caps.get(sku, default) and not fresh:   # one step down, not on top of a shrink
            new, why = new - 1, f"our stars {g['stars']} are under {STARS_MIN}" + ("" if why.startswith("keep") else f"; {why}")
        if new != caps.get(sku, default):
            changes.append({"at": int(now), "sku": sku, "from": caps.get(sku, default), "to": new, "why": why,
                            "market": now_m, "baseline": base.get(sku), "ours": us, "guards": {k: g[k] for k in ("share", "stars")}})
            if new == 0:
                exits[sku] = int(now)
            elif caps.get(sku, default) == 0:                # back in: judged from here on against today's market
                base[sku] = {"median": now_m["median"], "sellers": now_m["sellers"], "at": int(now)}
            caps[sku] = new
    if cut:
        state["stars_cut_at"] = int(now)
    state["guards"] = {"at": int(now), **{k: g[k] for k in ("share", "stars", "hold")}}
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
