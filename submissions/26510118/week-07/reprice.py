"""Week 07, challenge 1 -- the shop reprices itself from its own trades and reviews.

    python reprice.py --since 1792492430 --until 1792500825   # read a window of the ledger
    python reprice.py --hours 6                               # read the last six hours
    python reprice.py                                         # reprice from what was read
    python reprice.py --report                                # before/after numbers only

Where the facts come from. The market's ledger is public and hash-chained:
every result names the shop, the receipt, and the buyer's stars and review,
and the order before it carries the buyer's budget. So it holds this shop's
own trades and reviews *and* what each customer could have paid, which the
shop never sees while it negotiates. Only this shop's results are kept, in
logs/challenge-1/ledger-mine.json, so a window is read once.

What the trades and reviews said before the change (Rounds 0-2):
    every list price (lamp 150, chair 270, bookshelf 340-370) sat above the
    largest budget any customer brought (140, 250, 360), so every deal was
    haggled up to about 95% of the buyer's budget, and buyers who pay their
    budget write 3 stars ("Paid 195.0 of 205"). The 4- and 5-star reviews came
    from deals that left 14-17% of the budget unspent.

How the new ask is chosen: replay my own customers.
    For each item and each candidate list price A between the reserve and the
    largest budget seen, replay every past customer whose budget cleared the
    reserve: they would have paid HAGGLE x min(A, budget), measured from my
    deals and never below the reserve, and given the stars my reviews give that
    discount (a line fitted to my reviewed deals). The shop takes the most
    profitable A whose replayed stars stay within STAR_SLACK of the best any
    price achieves: profit, but not bought with reputation, because buyers pick
    shops by stars. A shop that cannot be seen cannot sell.

The reserves are not touched. seller.py still clamps every ask to them; this
file only moves the list price, inside [reserve, largest budget seen].
"""
import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

import httpx

import catalog

MARKET = "https://market.ourolabs.ai"
NAME = "Kiro729"                    # this shop's market name (the kit's --name)
ITEMS = ("lamp", "chair", "bookshelf")
STAR_SLACK = 0.2                    # stars the shop may give up, against the best price, for profit
MIN_CUSTOMERS = 5                   # fewer than this and the harness keeps setting the list price
HERE = Path(__file__).resolve().parent
CACHE = HERE / "logs" / "challenge-1" / "ledger-mine.json"   # public ledger rows, kept as evidence
ASKS = HERE / "asks.json"


# ----------------------------------------------------------- reading the ledger

def page(c: httpx.Client, after: int) -> list:
    r = c.get(f"{MARKET}/api/ledger", params={"after": after})
    r.raise_for_status()
    return r.json()


def seq_before(c: httpx.Client, ts: float) -> int:
    """A ledger seq just before time `ts`. The ledger is in time order, so a
    gallop then a binary search finds it in about 40 requests instead of
    reading every page from the start."""
    lo, hi = 0, 512
    while (p := page(c, hi)) and p[0]["ts"] < ts:
        lo, hi = hi, hi * 2
    while hi - lo > 500:
        mid = (lo + hi) // 2
        p = page(c, mid)
        if p and p[0]["ts"] < ts:
            lo = mid
        else:
            hi = mid
    return lo


def my_url_now(c: httpx.Client) -> str | None:
    """The shop's current card URL, from the dashboard: a window that starts
    after this session's join entry would otherwise not know the URL."""
    for s in c.get(f"{MARKET}/api/board").json().get("shops", []):
        if s.get("name") == NAME:
            return (s.get("card") or {}).get("url", "").rstrip("/") or None
    return None


def scan(cache: dict, since: float, until: float | None) -> dict:
    """Read the ledger between two times; keep this shop's results, with the
    budget from their order, and count the whole market's deals per round so
    a share can be computed for the same window."""
    urls = set(cache["urls"])
    seen = {r["seq"] for r in cache["results"]}
    win = {"from_ts": since, "to_ts": since, "market_deals": {}, "market_results": {}}
    orders: dict[str, dict] = {}
    with httpx.Client(timeout=30) as c:
        if url := my_url_now(c):
            urls.add(url)
        after = seq_before(c, since)
        while p := page(c, after):
            for e in p:
                if e["ts"] < since:
                    continue
                if until and e["ts"] > until:
                    p = []
                    break
                d, rnd = e["data"], str(e["round"])
                if e["kind"] == "join" and d.get("name") == NAME and d.get("role") == "shop" and d.get("url"):
                    urls.add(d["url"].rstrip("/"))
                elif e["kind"] == "order":
                    orders[d["order"]] = d
                elif e["kind"] == "result":
                    rec = d.get("receipt") if isinstance(d.get("receipt"), dict) else None
                    counted = bool(rec) and (e["round"] < 2 or bool(d.get("verified")))
                    win["market_results"][rnd] = win["market_results"].get(rnd, 0) + 1
                    win["market_deals"][rnd] = win["market_deals"].get(rnd, 0) + counted
                    if (d.get("shop") or "").rstrip("/") in urls and e["seq"] not in seen:
                        o = orders.pop(d.get("order"), {})
                        cache["results"].append({
                            "seq": e["seq"], "ts": e["ts"], "round": e["round"], "buyer": d.get("buyer"),
                            "item": o.get("item") or (rec or {}).get("item"), "budget": o.get("budget"),
                            "price": (rec or {}).get("price"), "counted": counted,
                            "verified": bool(d.get("verified")),
                            "stars": d.get("stars"), "review": d.get("review")})
                win["to_ts"] = e["ts"]
            if not p:
                break
            after = p[-1]["seq"]
            time.sleep(0.2)                             # a public endpoint: read it gently
    cache["urls"] = sorted(urls)
    cache["windows"].append(win)
    return win


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001  (missing -> start empty)
        return {"urls": [], "results": [], "windows": []}


# -------------------------------------------------------------- the repricing

def reserve_of(item: str) -> int:
    return catalog.exact(item)["reserve"]


def fit_stars(results: list) -> tuple[float, float, int]:
    """stars = a + b * discount, fitted to my reviewed deals, where discount is
    the share of the buyer's budget left unspent. With fewer than three
    reviews, or a fit that says discounts do not matter, fall back to one star
    per 10% of discount from a base of 3 -- what my reviews show by eye."""
    pts = [((r["budget"] - r["price"]) / r["budget"], r["stars"]) for r in results
           if r["price"] and r["budget"] and isinstance(r["stars"], (int, float))]
    if len(pts) >= 3:
        mx = statistics.fmean(x for x, _ in pts)
        my = statistics.fmean(y for _, y in pts)
        sxx = sum((x - mx) ** 2 for x, _ in pts)
        b = sum((x - mx) * (y - my) for x, y in pts) / sxx if sxx else 0.0
        if b > 0:
            return my - b * mx, b, len(pts)
    return 3.0, 10.0, len(pts)


def haggle(results: list) -> float:
    """How far buyers talked me down: the median of price / budget over my
    deals, all of which were made with the list price above the budget."""
    r = [x["price"] / x["budget"] for x in results if x["price"] and x["budget"]]
    return statistics.median(r) if r else 0.95


def replay(ask: int, budgets: list, reserve: int, h: float, a: float, b: float) -> tuple[float, float]:
    """Average profit per sale and average stars if my past customers had met
    list price `ask`. Every one of them is assumed to buy, so this compares
    prices, not demand; a lower ask also closes faster, which is not credited."""
    profit = stars = 0.0
    for B in budgets:
        p = max(reserve, round(h * min(ask, B)))
        profit += p - reserve
        stars += min(5.0, max(1.0, a + b * (B - p) / B))
    return profit / len(budgets), stars / len(budgets)


def reprice(cache: dict) -> dict:
    res = [r for r in cache["results"] if r["item"] in ITEMS]
    deals = [r for r in res if r["price"] and r["counted"]]
    a, b, n_rev = fit_stars(deals)
    h = haggle(deals)
    print(f"star fit from {n_rev} reviewed deal(s): stars = {a:.2f} + {b:.2f} x discount "
          f"(+1 star per {100 / b:.0f}% of budget left unspent)")
    print(f"haggle: my deals closed at a median {h:.0%} of the buyer's budget")
    out = {"changed_at": time.time(), "asks": {}, "why": {},
           "star_fit": {"a": round(a, 3), "b": round(b, 3), "reviews": n_rev}, "haggle": round(h, 3)}
    for item in ITEMS:
        reserve = reserve_of(item)
        budgets = [r["budget"] for r in res if r["item"] == item and r["budget"] and r["budget"] >= reserve]
        if len(budgets) < MIN_CUSTOMERS:
            print(f"\n{item}: only {len(budgets)} customer(s) who could afford it; leaving the price to the harness")
            continue
        top = max(budgets)
        rows = [(A, *replay(A, budgets, reserve, h, a, b)) for A in range(reserve, top + 1)]
        best = max(s for _, _, s in rows)
        ok = [r for r in rows if r[2] >= best - STAR_SLACK]
        ask, profit, stars = max(ok, key=lambda r: (r[1], -r[0]))
        old_profit, old_stars = rows[-1][1], rows[-1][2]  # an ask above every budget behaves like `top`
        print(f"\n{item}: reserve {reserve}, {len(budgets)} past customer(s) who could afford it, "
              f"budgets {min(budgets)}-{top}")
        print(f"  {'list price':>10}  {'profit/sale':>11}  {'stars':>5}")
        shown = set(r[0] for r in rows[:: max(1, len(rows) // 12)]) | {ask, top}
        for A, pr, st in rows:
            if A in shown:
                mark = " <- new" if A == ask else " <- as before (above every budget)" if A == top else ""
                print(f"  {A:>10}  {pr:>11.1f}  {st:>5.2f}{mark}")
        print(f"  best stars {best:.2f}; within {STAR_SLACK} of it the most profit is at {ask}")
        out["asks"][item] = ask
        out["why"][item] = {"reserve": reserve, "customers": len(budgets), "top_budget": top,
                            "before": {"profit_per_sale": round(old_profit, 1), "stars": round(old_stars, 2)},
                            "after": {"profit_per_sale": round(profit, 1), "stars": round(stars, 2)}}
    return out


# ------------------------------------------------------------------ the report

def report_rows(cache: dict, changed_at: float | None) -> list[dict]:
    """Share and average price, per window and round, labelled before or after
    the change. make_report.py prints the same rows into REPORT.md."""
    rows = []
    for w in cache["windows"]:
        for rnd in sorted(w["market_results"]):
            mine = [r for r in cache["results"] if w["from_ts"] <= r["ts"] <= w["to_ts"] and str(r["round"]) == rnd]
            if not mine:
                continue
            deals = [r for r in mine if r["counted"] and r["price"]]
            stars = [r["stars"] for r in mine if isinstance(r["stars"], (int, float))]
            rows.append({
                "tag": "after" if changed_at and w["from_ts"] >= changed_at else "before",
                "from": datetime.fromtimestamp(w["from_ts"]).strftime("%m-%d %H:%M"),
                "to": datetime.fromtimestamp(w["to_ts"]).strftime("%H:%M"),
                "round": rnd, "visits": len(mine), "deals": len(deals),
                "share": len(deals) / max(1, w["market_deals"].get(rnd, 0)),
                "price_reserve": statistics.fmean(r["price"] / reserve_of(r["item"]) for r in deals) if deals else None,
                "paid_budget": statistics.fmean(r["price"] / r["budget"] for r in deals if r["budget"]) if deals else None,
                "stars": statistics.fmean(stars) if stars else None, "reviews": len(stars)})
    return rows


def report(cache: dict, changed_at: float | None) -> None:
    def f3(v):
        return f"{v:.3f}" if v is not None else "-"
    print(f"\n{'window':<24} {'round':>5} {'visits':>6} {'deals':>5} {'share':>8} "
          f"{'price/reserve':>13} {'paid/budget':>11} {'stars':>11}")
    for r in report_rows(cache, changed_at):
        stars = f"{r['stars']:.2f} ({r['reviews']})" if r["stars"] is not None else "-"
        print(f"{r['tag'] + ' ' + r['from'] + '-' + r['to']:<24} {r['round']:>5} {r['visits']:>6} "
              f"{r['deals']:>5} {r['share']:>8.3%} {f3(r['price_reserve']):>13} {f3(r['paid_budget']):>11} "
              f"{stars:>11}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=float, help="read the ledger from this unix time")
    ap.add_argument("--until", type=float, help="... up to this unix time (default: now)")
    ap.add_argument("--hours", type=float, help="read the last N hours of the ledger")
    ap.add_argument("--report", action="store_true", help="print the before/after table only")
    a = ap.parse_args()

    cache = load_cache()
    since = a.since or (time.time() - a.hours * 3600 if a.hours else None)
    if since:
        w = scan(cache, since, a.until)
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"read the ledger {datetime.fromtimestamp(w['from_ts'])} .. {datetime.fromtimestamp(w['to_ts'])}: "
              f"{len(cache['results'])} result(s) at my shop so far")

    prev = json.loads(ASKS.read_text(encoding="utf-8")) if ASKS.exists() else None
    if not a.report:
        out = reprice(cache)
        # changed_at stays the moment the shop first left the harness's prices,
        # so "before" and "after" keep meaning the same thing across reprices.
        out["changed_at"] = prev["changed_at"] if prev else out["changed_at"]
        out["history"] = (prev or {}).get("history", [])
        if not out["history"] or out["history"][-1]["asks"] != out["asks"]:
            out["history"].append({"at": time.time(), "asks": out["asks"]})
        ASKS.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {ASKS.name}: {out['asks']}")
        prev = out
    report(cache, (prev or {}).get("changed_at"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
