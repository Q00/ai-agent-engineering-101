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
   - opening ask at least the upper quartile and above the target, at most UPPER x the upper quartile
   - each price moves at most STEP (15%) per cycle, so one bad answer cannot swing the shop
4. PUT the strategy to the Worker (/admin/strategy, token in .market/admin_token) and append the
   decision with its evidence to logs/challenge-1/strategies.jsonl (raw evidence, as in market_buyer.py).

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
SHOP_URL = "https://a2a-lamp-shop.thisjonghoon.workers.dev"
SHOP_NAME = "thisjonghoon"
TOKEN_FILE = os.path.join(HERE, ".market", "admin_token")
HISTORY = os.path.join(HERE, "logs", "challenge-1", "strategies.jsonl")
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}   # same as the Worker; never changed
MARGIN = 5        # the target stays at least this much above the reserve
UPPER = 1.15      # the opening ask stays within 15% above the market's upper quartile
STEP = 0.15       # most a price may move in one cycle
WINDOW = 3 * 3600 # going prices from the last three hours

PROMPT = """You set the prices of our furniture shop in an agent market. Buyers are other AI agents;
each picks a shop for its order, haggles, and leaves a review. Many shops sell the same items.
Our goal: the most profit per hour, where profit is (price - reserve) for every deal.
Asking too much loses buyers to cheaper shops; asking too little gives away margin.
Do not undercut the market: settle around the going price, not below it.

Reserves (our cost; never sell below): {reserve}

Going prices in the market, last {hours} hours (deals by everyone):
{market}

Our strategy versions so far, oldest first, and how each did:
{history}

Propose the next version. For each item set:
- "open": the first price we ask
- "target": the price we try to settle at (the shop concedes toward it)
Change what the evidence says to change; keep what works.

Answer with one JSON object and nothing else:
{{"open": {{"lamp": <int>, "chair": <int>, "bookshelf": <int>}}, "target": {{"lamp": <int>, "chair": <int>, "bookshelf": <int>}}, "reason": "<one or two sentences>"}}"""


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


def guard(proposal, previous, going):
    """Clamp the harness's prices. Returns (open, target, notes)."""
    open_, target, notes = {}, {}, []
    for item, reserve in RESERVE.items():
        g = going.get(item, {"p75": reserve * 1.3})
        t = market_intel_int((proposal.get("target") or {}).get(item))
        o = market_intel_int((proposal.get("open") or {}).get(item))
        lo_t = max(reserve + MARGIN, round(g.get("median", 0)))
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
        lo_o = max(t + 5, round(g["p75"]))
        hi_o = max(hi_o, lo_o)
        if not lo_o <= o <= hi_o:
            notes.append(f"{item}: opening ask {o} outside [{lo_o}, {hi_o}] (market p75 and target+5, {UPPER} x market p75), clamped")
            o = min(max(o, lo_o), hi_o)
        open_[item], target[item] = o, t
    return open_, target, notes


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
    prompt = PROMPT.format(reserve=json.dumps(RESERVE), hours=WINDOW // 3600, market=market, history=hist)
    log("META", "market:\n" + market + "\nhistory:\n" + hist)

    proposal = await harness.ask(prompt) or {}
    open_, target, notes = guard(proposal, previous, going)
    for n in notes:
        log("GUARD", n)
    strategy = {
        "version": (previous["version"] + 1) if previous else 1,
        "open": open_, "target": target,
        "market": {k: round(g["median"]) for k, g in going.items()},
        "reason": str(proposal.get("reason", "")).strip() or "(no reason from the harness)",
    }
    log("META", f"v{strategy['version']}: open {open_} target {target}: {strategy['reason']}")
    if dry_run:
        return
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
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


async def main(args):
    if args.wait_first:
        await asyncio.sleep(args.every)
    while True:
        try:
            await cycle(dry_run=args.dry_run)
        except Exception as e:  # keep the loop alive; the shop keeps its last strategy
            log("ERROR", f"cycle failed: {e!r}")
        if args.once:
            return
        await asyncio.sleep(args.every)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="decide and print, change nothing")
    ap.add_argument("--every", type=int, default=1800, help="seconds between cycles")
    ap.add_argument("--wait-first", action="store_true", help="sleep one interval before the first cycle")
    asyncio.run(main(ap.parse_args()))
