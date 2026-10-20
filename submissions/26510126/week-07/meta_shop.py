"""The meta-harness: watches the market and our shop's results, and sets the Worker shop's prices.

    python meta_shop.py --once              # one decision
    python meta_shop.py --every 1800        # a decision every 30 minutes, forever

Each cycle:
1. Read the market's public ledger (market_intel.py): the going price of each item over the
   last few hours, and our shop's own results under each strategy version so far.
2. Ask the harness (claude -p, haiku) for a new opening ask and target per item, with the
   history of every earlier version and how it did (deals per hour, price, margin over reserve).
3. Code guards, whatever the harness says (we never sell at a loss and never price far above the market):
   - target at least the market median (and reserve + MARGIN), at most the market's upper quartile:
     we do not undercut the market, a race to the bottom only gives margin away
   - opening ask at least the market median and above the target, at most UPPER x the upper quartile
   - each price moves at most STEP (15%) per cycle, so one bad answer cannot swing the shop
4. PUT the strategy to the Worker (/admin/strategy, token in .market/admin_token) and append the
   decision with its evidence to logs/challenge-1/strategies.jsonl (raw evidence, as in market_buyer.py).

Launch sale: until the shop has LAUNCH_DEALS deals this round on the board, the cycle skips the
harness and copies a benchmark shop (BENCHMARK, dragonash: 48 of 61 visits sold in Round 1, all signed,
mostly 5 stars): its recent median price per item becomes our target, its upper quartile our opening ask,
so the gap is small and deals close in two or three offers. Buyers rate by how much of their budget they
kept (dragonash leaves 10-15%). An item the benchmark has fewer than 3 recent deals for falls back to the
market's lower quartile (target) and median (opening ask). Never below reserve + MARGIN. New shops get no visits until they have reviews, and buyers that read the ledger skip shops
priced above the market; cheap, quick first deals earn the first reviews. The sale ends by itself.

The Worker itself refuses any price below the reserve, so a bug here cannot make the shop sell at a loss.
"""
import argparse
import asyncio
import json
import os
import statistics
import time

import httpx

import harness
import market_intel
from harness import log

HERE = os.path.dirname(os.path.abspath(__file__))
SHOP_URL = "https://fair-price-signed-receipts.thisjonghoon.workers.dev"  # renamed from a2a-lamp-shop in Round 2: the URL is one of three things a buyer sees before it picks
SHOP_NAME = "thisjonghoon"
TOKEN_FILE = os.path.join(HERE, ".market", "admin_token")
HISTORY = os.path.join(HERE, "logs", "challenge-1", "strategies.jsonl")
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}   # same as the Worker; never changed
MARGIN = 5        # the target stays at least this much above the reserve
UPPER = 1.15      # the opening ask stays within 15% above the market's upper quartile
STEP = 0.15       # most a price may move in one cycle
WINDOW = 3 * 3600 # going prices from the last three hours
LOYALTY_LAUNCH = [0, 0.03, 0.05, 0.07]   # coupon discount by tier during the launch sale: prices already sit near the floor
LOYALTY_NORMAL = [0, 0.05, 0.08, 0.10]   # after it, asks sit higher, so returning buyers get more
PROMO = "LAUNCH SALE: fair prices for our first customers. Please leave an honest review."
SALE_PROMO = "SALE: lower prices for now. Please leave an honest review."  # fixed text: the harness turns a sale on, never writes the line
MAX_LOYALTY = 0.15   # same cap as the Worker
MAX_TIERS = 6        # same as the Worker's MAX_TIER
LOYALTY_STEP = 0.03  # most a tier's discount may move in one cycle
TOP_SHOPS = 6        # competitors shown to the harness
BENCHMARK = "dragonash"  # the shop the launch sale copies (user's pick, see the Round 1 analysis in REPORT.md)
ROUND_POLL = 60   # seconds between round checks while waiting for the next cycle
LAUNCH_MARGIN = 0  # Round 2 opened as a price war at the reserve (dragonash, kimauve at 100/180/260): the launch sale
                   # may sell at the reserve itself (zero margin, never a loss) to earn its first reviews
LAUNCH_DEALS = 25  # launch sale until the board shows this many deals of our shop this round (was 10 reviews)

PROMPT = """You set the prices of our furniture shop in an agent market. Buyers are other AI agents;
each picks a shop for its order, haggles, and leaves a review. Many shops sell the same items.
Our goal: the most profit per hour, where profit is (price - reserve) for every deal.
Asking too much loses buyers to cheaper shops; asking too little gives away margin.
Do not undercut the market: settle around the going price, not below it.

Reserves (our cost; never sell below): {reserve}

Going prices in the market, last {hours} hours (deals by everyone):
{market}

This round so far (buyers pick shops by their reviews; a shop without reviews gets few visits):
{situation}

Our strategy versions so far, oldest first, and how each did:
{history}

Propose the next version:
- "open" per item: the first price we ask
- "target" per item: the price we settle at (an offer at or above it is accepted at once)
- "sale": true to run a sale (targets may go down to the market's lower quartile and the shop greets
  buyers with a sale line), false for normal prices (targets at least the market median). Use a sale to
  win visits and reviews when we get few; end it when visits are steady.
- "loyalty": coupon discount by tier, a list starting with 0 (tier 0 = no coupon), not decreasing, each at
  most {max_loyalty}, up to {max_tiers} tiers. Every deal gives the buyer a code one tier up, so a longer list
  rewards buyers who keep coming back. Raise it when returning buyers matter, lower it when it only gives margin away.
Change what the evidence says to change; keep what works.

Answer with one JSON object and nothing else:
{{"open": {{"lamp": <int>, "chair": <int>, "bookshelf": <int>}}, "target": {{"lamp": <int>, "chair": <int>, "bookshelf": <int>}}, "sale": <true|false>, "loyalty": [0, ...], "reason": "<two or three sentences>"}}"""


def board_row():
    """Our shop's row on the market board (deals, share, visits), or None."""
    try:
        board = httpx.get(f"{market_intel.MARKET}/api/board", timeout=10).json()
        return next((r for r in board["shops"] if r["name"] == SHOP_NAME), None)
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def first_result(entries):
    """When our shop's first result landed: the start of the no-strategy baseline (version 0)."""
    names = {e["data"]["url"].rstrip("/"): e["data"]["name"] for e in entries
             if e["kind"] == "join" and e["data"].get("role") == "shop" and e["data"].get("url")}
    ts = [e["ts"] for e in entries if e["kind"] == "result" and names.get((e["data"].get("shop") or "").rstrip("/")) == SHOP_NAME]
    return min(ts) if ts else None


def load_history():
    try:
        with open(HISTORY) as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def our_results(entries, start, end):
    """Our shop's deals between two times, per item: count and prices."""
    _, shops = market_intel.summarize([e for e in entries if e["kind"] != "result" or start <= e["ts"] < end])
    s = shops.get(SHOP_NAME, {"results": 0, "deals": 0, "prices": {}})
    return s


def outcome(entries, start, end):
    """How a version did between start and end: per item deals, median price, profit per hour."""
    s = our_results(entries, start, end)
    hours = max((end - start) / 3600, 1e-6)
    per_item = {}
    for item in RESERVE:
        prices = s["prices"].get(item, [])
        per_item[item] = {
            "deals": len(prices),
            "median_price": statistics.median(prices) if prices else None,
            "profit_per_hour": round(sum(p - RESERVE[item] for p in prices) / hours, 1),
        }
    return {"hours": round(hours, 2), "results": s["results"], "deals": s["deals"], "items": per_item}


def history_text(history, entries, now):
    if not history:
        start = first_result(entries)
        if start is None:
            return "(none yet: the shop has not sold anything)"
        o = outcome(entries, start, now)
        items = "; ".join(f"{k}: {o['items'][k]['deals']} deals, median {o['items'][k]['median_price']}, "
                          f"profit/h {o['items'][k]['profit_per_hour']}" for k in RESERVE)
        return (f"v0, no strategy: the model picked opening prices itself, mostly around 250 for a lamp "
                f"({o['hours']} h, {o['results']} visits ending in a result, {o['deals']} deals): {items}")
    lines = []
    for i, v in enumerate(history):
        end = history[i + 1]["time"] if i + 1 < len(history) else now
        o = outcome(entries, v["time"], end)
        items = "; ".join(f"{k} open {v['open'][k]} target {v['target'][k]}: {o['items'][k]['deals']} deals, "
                          f"median {o['items'][k]['median_price']}, profit/h {o['items'][k]['profit_per_hour']}"
                          for k in RESERVE)
        lines.append(f"v{v['version']} ({o['hours']} h, {o['results']} visits ending in a result, {o['deals']} deals): {items}")
    return "\n".join(lines)


def guard(proposal, previous, going, sale=False):
    """Clamp the harness's prices. A sale lets targets go down to the market's lower quartile and opening
    asks down to the median; otherwise the floors are the median and the upper quartile. Returns (open, target, notes)."""
    open_, target, notes = {}, {}, []
    for item, reserve in RESERVE.items():
        g = going.get(item, {"p75": reserve * 1.3})
        t = market_intel_int((proposal.get("target") or {}).get(item))
        o = market_intel_int((proposal.get("open") or {}).get(item))
        lo_t = max(reserve + MARGIN, round(g.get("p25" if sale else "median", 0)))
        hi_t = max(lo_t, round(g["p75"]))
        if t is None:
            t = previous["target"][item] if previous else round((lo_t + hi_t) / 2)
            notes.append(f"{item}: no target from the harness, keeping {t}")
        if previous:
            step_lo, step_hi = round(previous["target"][item] * (1 - STEP)), round(previous["target"][item] * (1 + STEP))
            if not step_lo <= t <= step_hi:
                notes.append(f"{item}: target {t} moves more than {STEP:.0%}, clamped")
                t = min(max(t, step_lo), step_hi)
        if not lo_t <= t <= hi_t:
            notes.append(f"{item}: target {t} outside [{lo_t}, {hi_t}] (market median, market p75), clamped")
            t = min(max(t, lo_t), hi_t)
        hi_o = max(t + 5, round(g["p75"] * UPPER))
        if o is None:
            o = previous["open"][item] if previous else hi_o
            notes.append(f"{item}: no opening ask from the harness, using {o}")
        if previous:
            step_lo, step_hi = round(previous["open"][item] * (1 - STEP)), round(previous["open"][item] * (1 + STEP))
            if not step_lo <= o <= step_hi:
                notes.append(f"{item}: opening ask {o} moves more than {STEP:.0%}, clamped")
                o = min(max(o, step_lo), step_hi)
        # the opening ask only sets how long the haggle runs; the target guard above keeps the price.
        # (was the upper quartile: in Round 2 it lifted the harness's chair 192 to 200, above dragonash's 192)
        lo_o = max(t + 5, round(g.get("median", 0)))
        hi_o = max(hi_o, lo_o)
        if not lo_o <= o <= hi_o:
            notes.append(f"{item}: opening ask {o} outside [{lo_o}, {hi_o}] (market median and target+5, {UPPER} x market p75), clamped")
            o = min(max(o, lo_o), hi_o)
        open_[item], target[item] = o, t
    return open_, target, notes


def guard_loyalty(proposed, previous_table):
    """Clamp the harness's coupon table: starts at 0, not decreasing, each step at most MAX_LOYALTY, at most
    MAX_TIERS tiers, and each tier within LOYALTY_STEP of its previous value (a new tier within LOYALTY_STEP
    of the last one). Returns (table, notes)."""
    prev = list(previous_table or LOYALTY_NORMAL)
    notes = []
    if not isinstance(proposed, list) or not proposed:
        return prev, ["loyalty: no table from the harness, keeping the last one"]
    table = [0.0]
    for i, v in enumerate(proposed[1:MAX_TIERS + 1], start=1):
        try:
            v = float(v)
        except (TypeError, ValueError):
            notes.append(f"loyalty: tier {i} is not a number, table cut there")
            break
        ref = prev[i] if i < len(prev) else prev[-1]
        clamped = min(max(v, ref - LOYALTY_STEP, table[-1]), ref + LOYALTY_STEP, MAX_LOYALTY)
        if abs(clamped - v) > 1e-9:
            notes.append(f"loyalty: tier {i} {v:.2f} clamped to {clamped:.2f}")
        table.append(round(clamped, 3))
    if len(proposed) > MAX_TIERS + 1:
        notes.append(f"loyalty: more than {MAX_TIERS} tiers, cut")
    if len(table) < 2:
        return prev, notes + ["loyalty: empty table, keeping the last one"]
    return table, notes


def situation(entries, now, token):
    """This round: our board row, the top shops' visits, stars and prices, and our coupon use."""
    lines = []
    try:
        stats = httpx.get(f"{market_intel.MARKET}/api/stats", timeout=10).json()
        rnd = stats["rounds"][str(stats["round"])]
        shops = sorted(rnd["shops"], key=lambda s: -s["deals"])
        round_start = max([e["ts"] for e in entries if e["kind"] == "round"] or [now - WINDOW])
        _, recent = market_intel.summarize(entries, since=round_start)
        def row(s):
            med = {k: statistics.median(v) for k, v in recent.get(s["shop"], {}).get("prices", {}).items()}
            return (f"- {s['shop']}: {s['deals']} deals, {s['visits']} visits, share {s['share']:.1%}, "
                    f"{s['reviews']} reviews, stars {s['stars']}, median prices {med}")
        lines.append(f"Round {stats['round']}, {rnd['deals']} deals in the market so far. Top shops:")
        lines += [row(s) for s in shops[:TOP_SHOPS]]
        ours = next((s for s in shops if s["shop"] == SHOP_NAME), None)
        lines.append("Our shop: " + (row(ours)[2:] if ours else "no results this round"))
    except (httpx.HTTPError, ValueError, KeyError) as e:
        lines.append(f"(round stats unavailable: {e!r})")
    try:
        coupons = httpx.get(f"{SHOP_URL}/admin/coupons", headers={"Authorization": f"Bearer {token}"}, timeout=10).json()
        lines.append(f"Our coupons so far (issued and redeemed per tier): {coupons or 'none yet'}")
    except (httpx.HTTPError, ValueError) as e:
        lines.append(f"(coupon stats unavailable: {e!r})")
    return "\n".join(lines)


def market_intel_int(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


async def cycle(dry_run=False):
    now = time.time()
    entries = market_intel.load_ledger()
    going, _ = market_intel.summarize(entries, since=now - WINDOW)
    if not going:
        log("META", "no market prices yet, skipping")
        return
    history = load_history()
    previous = history[-1] if history else None
    market = "\n".join(f"- {k}: median {g['median']:g}, middle half {g['p25']:g}-{g['p75']:g}, range {g['min']:g}-{g['max']:g}, {g['n']} deals"
                       for k, g in sorted(going.items()))
    hist = history_text(history, entries, now)
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    sit = situation(entries, now, token)
    prompt = PROMPT.format(reserve=json.dumps(RESERVE), hours=WINDOW // 3600, market=market, history=hist,
                           situation=sit, max_loyalty=MAX_LOYALTY, max_tiers=MAX_TIERS)
    log("META", "market:\n" + market + "\nsituation:\n" + sit + "\nhistory:\n" + hist)

    row = board_row() or {}
    launch = (row.get("deals") or 0) < LAUNCH_DEALS
    if launch:
        # the benchmark's prices since the current round began (its strategy may differ between rounds)
        round_start = max([e["ts"] for e in entries if e["kind"] == "round"] or [now - WINDOW])
        _, recent = market_intel.summarize(entries, since=round_start)
        peer = recent.get(BENCHMARK, {"prices": {}})["prices"]
        target, open_, notes = {}, {}, []
        for k, r in RESERVE.items():
            prices = sorted(peer.get(k, []))
            if len(prices) >= 3:
                target[k] = max(r + LAUNCH_MARGIN, round(statistics.median(prices)))
                open_[k] = max(target[k] + 5, round(prices[(3 * len(prices)) // 4]))
                notes.append(f"{k}: copying {BENCHMARK} ({len(prices)} recent deals, median {statistics.median(prices):g})")
            else:
                g = going.get(k, {"p25": r, "median": r})
                target[k] = max(r + LAUNCH_MARGIN, round(g["p25"]))
                open_[k] = max(target[k] + 5, round(g["median"]))
                notes.append(f"{k}: {BENCHMARK} has {len(prices)} recent deals, using the market's lower quartile")
        proposal = {"reason": f"launch sale: {row.get('deals') or 0} deals this round (< {LAUNCH_DEALS}), "
                              f"copying {BENCHMARK}'s prices to earn the first reviews"}
        log("META", f"launch sale, {row.get('deals') or 0} deals, {row.get('reviews') or 0} reviews, {row.get('visits') or 0} visits this round")
        sale, loyalty = True, LOYALTY_LAUNCH
    else:
        proposal = await harness.ask(prompt) or {}
        sale = proposal.get("sale") is True
        open_, target, notes = guard(proposal, previous, going, sale=sale)
        loyalty, lnotes = guard_loyalty(proposal.get("loyalty"), (previous or {}).get("loyalty"))
        notes += lnotes
    for n in notes:
        log("GUARD", n)
    strategy = {
        "version": (previous["version"] + 1) if previous else 1,
        "open": open_, "target": target,
        "market": {k: round(g["median"]) for k, g in going.items()},
        "reason": str(proposal.get("reason", "")).strip() or "(no reason from the harness)",
        # the greeting is one of two fixed lines chosen by mode, never text from the harness
        "promo": PROMO if launch else SALE_PROMO if sale else "",
        "sale": sale,
        "loyalty": loyalty,
        "margin": LAUNCH_MARGIN if launch else MARGIN,
    }
    log("META", f"v{strategy['version']}: {'launch' if launch else 'sale' if sale else 'normal'}, open {open_} "
                f"target {target} loyalty {loyalty}: {strategy['reason']}")
    if dry_run:
        return
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.put(f"{SHOP_URL}/admin/strategy", json=strategy, headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        log("ERROR", f"Worker refused the strategy: {r.status_code} {r.text[:200]}")
        return
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    with open(HISTORY, "a") as f:
        f.write(json.dumps({**strategy, "time": now, "proposal": proposal, "guards": notes,
                            "going": going, "board": {k: v for k, v in (board_row() or {}).items() if k in ("deals", "share", "visits", "revenue", "stars", "reviews")},
                            "before": outcome(entries, previous["time"] if previous else (first_result(entries) or now - 1), now)},
                           ensure_ascii=False) + "\n")


def market_round():
    try:
        return httpx.get(f"{market_intel.MARKET}/api/stats", timeout=10).json()["round"]
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def wait(seconds, start_round):
    """Sleep up to `seconds`, waking at once when the market moves to a new round: the first minutes of a
    round decide who gets the reviews, so the launch sale must be on before the first buyers pick."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        await asyncio.sleep(min(ROUND_POLL, max(1, deadline - time.time())))
        r = market_round()
        if r is not None and start_round is not None and r != start_round:
            log("META", f"round {start_round} -> {r}, deciding now")
            return


async def main(args):
    if args.wait_first:
        await wait(args.every, market_round())
    while True:
        try:
            await cycle(dry_run=args.dry_run)
        except Exception as e:  # keep the loop alive; the shop keeps its last strategy
            log("ERROR", f"cycle failed: {e!r}")
        if args.once:
            return
        await wait(args.every, market_round())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="decide and print, change nothing")
    ap.add_argument("--every", type=int, default=600, help="seconds between cycles")
    ap.add_argument("--wait-first", action="store_true", help="sleep one interval before the first cycle")
    asyncio.run(main(ap.parse_args()))
