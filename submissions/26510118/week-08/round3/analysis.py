"""The analysis runner: the analysis side of the agents, apart from the kit's trading hooks.

    python analysis.py             (a server service; wakes every 10 minutes)
    python analysis.py --once      (one pass, for a test or by hand)

Each pass does only what is due, in this order:
    collect      1 h    the public ledger -> market_summary.json; a snapshot of our business
                        (analysis/business.jsonl, for the briefings); the file check (contract.py:
                        the fields each part counts on, problems logged); shops' stock lists if the
                        market shows them; the wanted categories restock reads (analysis/wanted.json)
    opening     10 min  once, our shop's opening post, when the first day's stock is in
                        (OPEN_CATEGORIES categories, or OPEN_MIN after OPEN_WAIT)
    index        3 h    the market index on the forum, as our buyer (forum.py): what moved since
                        the last one and a checked reading by MODEL_MID; skipped until the ledger
                        holds MIN_INDEX settles of the last day
    replies     10 min  other agents' "wanted" posts: a data reply as our buyer and a stock reply
                        as our shop, each at most once every 30 minutes
    votes        3 h    at most one dislike (a post steering AI agents or a repeat) and one like (the
                        most useful post) on the last 3 hours' posts, as our buyer (forum.pick_votes)
    seller       3 h    the seller analysis (analysts.py, MODEL_MID + ledger_query)
    buyer        3 h    the buyer analysis
    integrated  12 h    adopt, keep or revert rule changes (MODEL_STRONG); applied in the focus phase
                        and keep the insights (both phases)
Trading modules only read what this writes. A lock keeps two passes from overlapping; every
step is logged to logs/analysis.log, which goes with the submission.
"""
import argparse
import os
import time
from collections import Counter
from datetime import datetime

import httpx

import analysts
import common
import context
import contract
import forum
import harness
import ledger

EVERY = {"collect": 3600, "opening": 600, "index": 3 * 3600, "replies": 600, "votes": 3 * 3600, "seller": 3 * 3600, "buyer": 3 * 3600,
         "integrated": 12 * 3600}
REPLY_GAP = 1800
MIN_INDEX = 20
OPEN_CATEGORIES = 100    # categories in stock before our shop announces itself (day one stocks about 116)
OPEN_MIN, OPEN_WAIT = 20, 1800   # or at least this many, half an hour after the first stock came in
SLEEP = 600
LOCK_STALE = 1800
LOG = common.HERE / "logs" / "analysis.log"


def log(line: str) -> None:
    stamp = datetime.now(common.KST).strftime("%m-%d %H:%M:%S")
    print(f"{stamp} {line}", flush=True)
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{stamp} {line}\n")


def folder():
    return common.MARKET_DIR / "analysis"


def market_get(path: str):
    r = httpx.get(f"{ledger.URL}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def collect(now: float, get_ledger=None, get_json=None) -> str:
    n = ledger.collect(get_ledger or ledger.fetch)
    catalog = common.catalog()
    stock_note, shops = "shops' stock not shown", []
    try:
        shops = (get_json or market_get)("/api/shops")
        shops = shops.get("shops", shops) if isinstance(shops, dict) else shops
        ledger.learn_names(shops)                               # the ledger names shops by address
    except Exception as e:                                       # noqa: BLE001
        stock_note = f"shops' stock unavailable ({type(e).__name__})"
    summary = ledger.summarize(catalog, now)
    common.write_json(common.MARKET_DIR / "market_summary.json", summary)
    context.snapshot(now)                                       # what the next briefings compare against
    problems = contract.check()["problems"]
    for p in problems[:8]:
        log(f"[contract] {p}")
    try:
        if any("stock" in s for s in shops):
            stocked = {(catalog.get(k) or {}).get("category") for s in shops if not forum.own(s.get("name"))
                       for k, q in (s.get("stock") or {}).items() if q}
            common.write_json(folder() / "shops_stock.json", {"at": int(now), "stocked": sorted(c for c in stocked if c)})
            stock_note = f"{len(stocked)} categories stocked by other shops"
    except Exception as e:                                       # noqa: BLE001
        stock_note = f"shops' stock unavailable ({type(e).__name__})"
    wanted = Counter(u.get("category") for u in common.records("unmet") if (u.get("ts") or 0) >= now - 86400)
    for _, cat in forum.wanted(common.market_file("forum.json", []) or [], common.market_file("demand.json", []) or [], set()):
        wanted[cat] += 1
    common.write_json(folder() / "wanted.json", {"at": int(now), "categories": dict(wanted)})
    return (f"{n} new ledger entries, {summary.get('settles_24h', 0)} settles in 24 h, {stock_note}, {len(wanted)} wanted categories, "
            f"file check: {len(problems)} problem(s)")


def no_seller(now: float) -> tuple[list, str]:
    demand = common.market_file("demand.json", []) or []
    stock = common.read_json(folder() / "shops_stock.json", {}) or {}
    if stock and now - stock.get("at", 0) < 2 * 3600:
        have = set(stock.get("stocked") or [])
        return [d["category"].split(">")[-1] for d in sorted(demand, key=lambda d: -d.get("skus", 0))
                if d["category"] not in have], "shops' stock lists"
    unmet = Counter(u.get("category") for u in common.records("unmet") if (u.get("ts") or 0) >= now - 86400)
    return [forum.leaf(c) for c, _ in unmet.most_common() if c], "our buyer's orders"


def opening(now: float, state: dict) -> str | None:
    """Our shop's one opening post, once the first day's stock is in; None until then."""
    inv = common.market_file("inventory.json", {}) or {}
    cats = {u.get("category") for u in inv.get("units") or [] if u.get("category") and u.get("grade") != "expired"}
    if cats and not state.get("first_stock"):
        state["first_stock"] = now
    if not (len(cats) >= OPEN_CATEGORIES or (len(cats) >= OPEN_MIN and now - state.get("first_stock", now) >= OPEN_WAIT)):
        return None
    forum.outbox(post=forum.opening_post(inv), **{"as": "shop"})
    state["opened"] = now
    return f"opening post as our shop ({len(cats)} categories in stock)"


def index(now: float, state: dict, ask=None) -> str | None:
    summary = common.market_file("market_summary.json", {}) or {}
    if summary.get("settles_24h", 0) < MIN_INDEX:
        return None                                             # not enough to publish; try again next pass
    n = state.get("index_n", 0) + 1
    missing, source = no_seller(now)
    prev = common.read_json(folder() / "last_index.json") or None
    text, refs, record = forum.index_post(summary, n, missing, source, now, prev=prev, ask=ask or harness.ask)
    forum.outbox(post=text, refs=refs, **{"as": "buyer"})
    common.write_json(folder() / "last_index.json", record)
    state["index_n"] = n
    return f"market index #{n} posted ({len(text)} chars, {len(refs)} refs)"


def replies(now: float, state: dict) -> str:
    posts = common.market_file("forum.json", []) or []
    seen = set(state.get("answered") or [])
    found = forum.wanted(posts, common.market_file("demand.json", []) or [], seen)
    done = []
    for pid, cat in found[:1]:
        summary = common.market_file("market_summary.json", {}) or {}
        did_buyer = did_shop = False
        if now - state.get("buyer_reply", 0) >= REPLY_GAP:
            text = forum.data_reply(cat, summary, common.read_jsonl(ledger.folder() / "settles.jsonl"), common.catalog(), now,
                                    ledger.names())
            if text:
                forum.outbox(post=text, thread=pid, **{"as": "buyer"})
                state["buyer_reply"] = now
                done.append(f"buyer data reply to #{pid} ({cat})")
            did_buyer = True
        if now - state.get("shop_reply", 0) >= REPLY_GAP:
            text = forum.shop_reply(cat, common.market_file("inventory.json", {}) or {})
            if text:
                forum.outbox(post=text, thread=pid, **{"as": "shop"})
                state["shop_reply"] = now
                done.append(f"shop reply to #{pid} ({cat})")
            did_shop = True
        if did_buyer and did_shop:
            state["answered"] = (list(seen) + [pid])[-500:]
    return "; ".join(done) or f"{len(found)} wanted post(s) waiting"


def votes(now: float, state: dict, decide=None) -> str:
    voted = set(state.get("voted") or [])
    picked = forum.pick_votes(common.market_file("forum.json", []) or [], voted, now, decide or harness.decide)
    for v in picked:
        forum.outbox(vote=v["post"], value=v["value"], **{"as": "buyer"})
        common.record("votes", **v)
    state["voted"] = (list(voted) + [v["post"] for v in picked])[-500:]
    return "; ".join(f"{v['value']:+d} on #{v['post']} ({v['reason']})" for v in picked) or "nothing qualified, no vote"


def run_once(now: float | None = None, chat_fn=None, get_ledger=None, get_json=None) -> list:
    """One pass; returns the names of the steps done."""
    now = now or time.time()
    folder().mkdir(parents=True, exist_ok=True)
    lock = folder() / "lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_STALE:     # the lock's age is wall-clock time
        log("[analysis] another pass is running; skipped")
        return []
    lock.write_text(str(os.getpid()), encoding="utf-8")
    state = common.read_json(folder() / "state.json", {}) or {}
    done = []
    try:
        for name, every in EVERY.items():
            if now - state.get(name, 0) < every:
                continue
            try:
                if name == "collect":
                    note = collect(now, get_ledger, get_json)
                elif name == "opening":
                    if state.get("opened"):
                        continue
                    note = opening(now, state)
                    if note is None:
                        continue                                # stock not in yet; look again next pass
                elif name == "index":
                    note = index(now, state)
                    if note is None:
                        log("[analysis] index: not enough settles yet")
                        continue
                elif name == "replies":
                    note = replies(now, state)
                elif name == "votes":
                    note = votes(now, state)
                elif name in ("seller", "buyer"):
                    r = analysts.role_analysis(name, chat_fn, now)
                    note = f"{len(r['proposals'])} proposal(s), {sum(p['valid'] for p in r['proposals'])} within range"
                else:
                    e = analysts.integrated(chat_fn, now)
                    k = e["insights"]
                    note = (f"{len(e['changes'])} change(s), {len(e['reverted'])} revert(s), "
                            + ("applied" if e["applied"] else "recorded only (exploration phase)")
                            + f"; insights: {len(k['added'])} new, {len(k['confirmed'])} confirmed, "
                              f"{len(k['refuted'])} refuted, {len(k['retired'])} retired, {len(k['refused'])} refused")
                state[name] = now
                done.append(name)
                log(f"[analysis] {name}: {note}")
            except Exception as e:                               # noqa: BLE001  (one step failing must not stop the rest)
                log(f"[analysis] {name} failed: {type(e).__name__}: {e}")
    finally:
        common.write_json(folder() / "state.json", state)
        lock.unlink(missing_ok=True)
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    log(f"[analysis] start, market dir {common.MARKET_DIR}, phase {common.strategy()['phase']}")
    while True:
        run_once()
        if a.once:
            return 0
        time.sleep(SLEEP)


if __name__ == "__main__":
    raise SystemExit(main())
