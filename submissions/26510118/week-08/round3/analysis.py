"""The analysis runner: the analysis side of the agents, apart from the kit's trading hooks.

    python analysis.py             (a server service; wakes every 5 minutes)
    python analysis.py --once      (one pass, for a test or by hand)

Each pass does only what is due, in this order:
    collect     20 min  the public ledger -> market_summary.json (every hour until 11/4 night) (rows and records over 48 h archived as
                        .jsonl.gz every few hours, so the files read every hour stay small); a snapshot of our business
                        (analysis/business.jsonl, for the briefings); the file check (contract.py:
                        the fields each part counts on, problems logged); shops' stock lists if the
                        market shows them; the wanted categories restock reads (analysis/wanted.json);
                        our scores and places on the board (analysis/board_history.jsonl)
    opening     10 min  once, our shop's opening post, when the first day's stock is in
                        (OPEN_CATEGORIES categories, or OPEN_MIN after OPEN_WAIT)
    journal      1 h    the Kiro Market Journal on the forum, as our buyer (journal.py): one issue
                        from the next of four desks with something new, the 해설 by MODEL_MID
                        and checked; an hour with nothing new is skipped. (Until 11/4 this was
                        the market index every 3 hours, index() below, no longer scheduled.)
    replies     10 min  other agents' "wanted" posts: a stock reply as our shop, at most once an hour
                        (the data reply as our buyer stopped on 11/4: thread replies drew about one
                        like, and a buyer post's score enters the buyer's ALL)
    linewatch   20 min  the premium line's caps (linewatch.py): out fast when other shops crowd in or the
                        price falls, up when ours sell
    notes        5 min  a Kiro 데이터 노트 as our buyer (notes.py) when no buyer post of ours waits in
                        the outbox and the last went out 5 minutes ago; the votes on our posts are read
                        into analysis/post_votes.json first, and low scores pause the notes
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
import json
import os
import time
from collections import Counter
from datetime import datetime

import httpx

import analysts
import common
import linewatch
import notes
import context
import contract
import forum
import harness
import journal
import ledger

EVERY = {"collect": 1200, "linewatch": 1200, "opening": 600, "journal": 3600, "replies": 600, "notes": notes.GAP,
         "votes": 3 * 3600, "seller": 3 * 3600, "buyer": 3 * 3600, "integrated": 12 * 3600}
RIVALS = 12             # the top players whose shops our buyer passes over in a near tie (market_buyer.RIVAL)
BUYER_REPLIES = False    # data replies as our buyer (stopped 11/4: about one like each, against 5-6 for a top-level post)
REPLY_GAP = 3600         # the forum takes ~270 posts an hour (11/3): ours stay few
ARCHIVED = ("negotiations", "requests", "orders", "refusals", "reviews", "unmet", "unpaid", "unpaid_buys", "votes",
            "sales", "restock", "stock_flow")    # records whose readers look back 48 h at most; deals.jsonl is read by offset instead
MIN_INDEX = 20
OPEN_CATEGORIES = 100    # categories in stock before our shop announces itself (day one stocks about 116)
OPEN_MIN, OPEN_WAIT = 20, 1800   # or at least this many, half an hour after the first stock came in
SLEEP = 300              # 600 until 11/4: the notes post every 5 minutes
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
    moved = ledger.archive(now) + sum(common.archive_old(common.MARKET_DIR / "records" / f"{s}.jsonl", now - ledger.KEEP,
                                                         ledger.SLACK) for s in ARCHIVED)
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
            per = Counter(c for s in shops if not forum.own(s.get("name"))
                          for c in {(catalog.get(k) or {}).get("category") for k, q in (s.get("stock") or {}).items() if q} if c)
            stocked = set(per)
            common.write_json(folder() / "shops_stock.json", {"at": int(now), "stocked": sorted(stocked), "shops_per": dict(per)})
            stock_note = f"{len(stocked)} categories stocked by other shops"
    except Exception as e:                                       # noqa: BLE001
        stock_note = f"shops' stock unavailable ({type(e).__name__})"
    wanted = Counter(u.get("category") for u in common.records("unmet") if (u.get("ts") or 0) >= now - 86400)
    for _, cat in forum.wanted(common.market_file("forum.json", []) or [], common.market_file("demand.json", []) or [], set()):
        wanted[cat] += 1
    common.write_json(folder() / "wanted.json", {"at": int(now), "categories": dict(wanted)})
    try:
        board_note = board_snapshot(now, get_json or market_get)
    except Exception as e:                                       # noqa: BLE001
        board_note = f"board unavailable ({type(e).__name__})"
    return (f"{n} new ledger entries, {summary.get('settles_24h', 0)} settles in 24 h, {stock_note}, {len(wanted)} wanted categories, "
            f"{board_note}, file check: {len(problems)} problem(s)" + (f", {moved} rows over 48 h archived" if moved else ""))


def board_snapshot(now: float, get_json) -> str:
    """One line an hour in analysis/board_history.jsonl: our player's, shop's and buyer's scores and places on
    the market's board, and the leader's total. The board shows only the present; this is how a change (the
    focus phase, a fix) shows in the ranking afterwards. Places count shops and buyers with a deal (house
    shops left out), stars also among shops with 100 reviews or more."""
    b = get_json("/api/board")
    if not isinstance(b, dict) or not b.get("players"):
        return "board not shown"
    shops = [s for s in b.get("shops") or [] if not s.get("house") and (s.get("deals") or 0) > 0]
    buyers = [x for x in b.get("buyers") or [] if not x.get("house") and (x.get("deals") or 0) > 0]
    players = b["players"]
    place = lambda rows, row, k: 1 + sum(1 for r in rows if (r.get(k) or 0) > (row.get(k) or 0))
    me_p = next((p for p in players if forum.own(p.get("player"))), None)
    me_s = next((s for s in shops if forum.own(s.get("name"))), None)
    me_b = next((x for x in buyers if forum.own(x.get("name"))), None)
    line = {"ts": int(now), "players": len(players), "shops": len(shops), "buyers": len(buyers),
            "leader": max(({"player": p.get("player"), "total": p.get("total")} for p in players), key=lambda x: x["total"] or 0)}
    if me_p:
        line["player"] = {"total": me_p.get("total"), "place": place(players, me_p, "total")}
    if me_s:
        big = [s for s in shops if (s.get("reviews") or 0) >= 100]
        line["shop"] = dict({k: me_s.get(k) for k in ("all", "deals", "revenue", "profit", "stars", "reviews")},
                            **{f"place_{k}": place(shops, me_s, k) for k in ("all", "deals", "revenue", "profit", "stars")},
                            place_stars_100=place(big, me_s, "stars") if me_s in big else None)
    if me_b:
        line["buyer"] = dict({k: me_b.get(k) for k in ("all", "surplus", "deals", "forum", "posts")},
                             **{f"place_{k}": place(buyers, me_b, k) for k in ("all", "surplus", "deals", "forum")})
    with open(folder() / "board_history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    ranked = sorted(players, key=lambda p: -(p.get("total") or 0))[:RIVALS]   # the players ahead of and around us
    rivals = sorted({p["shop"].get("name") for p in ranked if isinstance(p.get("shop"), dict)
                     and not forum.own(p.get("player")) and not forum.own(p["shop"].get("name"))} - {None})
    common.write_json(folder() / "rivals.json", {"at": int(now), "shops": rivals})
    return f"board: {line.get('player', {}).get('place', '?')} of {len(players)} players"


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


def journal_step(now: float, state: dict, ask=None) -> str | None:
    """The next journal issue, posted as our buyer; None when no desk has anything new."""
    got = journal.next_issue(journal.gather(now), now, state.setdefault("journal_issues", {}), ask or harness.ask)
                                                                # ("journal" in state is the step's own clock)
    if not got:
        return None
    text, refs, desk, n = got
    forum.outbox(post=text, refs=refs, **{"as": "buyer"})
    return f"journal 제{n}호 ({desk}) posted ({len(text)} chars, {len(refs)} refs)"


def replies(now: float, state: dict) -> str:
    posts = common.market_file("forum.json", []) or []
    seen = set(state.get("answered") or [])
    found = forum.wanted(posts, common.market_file("demand.json", []) or [], seen)
    done = []
    for pid, cat in found[:1]:
        summary = common.market_file("market_summary.json", {}) or {}
        did_buyer, did_shop = not BUYER_REPLIES, False
        if BUYER_REPLIES and now - state.get("buyer_reply", 0) >= REPLY_GAP:
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


def buyer_post_waiting() -> bool:
    """A post as our buyer still in the outbox (the kit keeps one the market refused for the 5-minute limit),
    or in outbox.taking, the file the kit moves it to while it sends."""
    return any(x.get("post") and x.get("as") == "buyer" for name in ("outbox.jsonl", "outbox.taking")
               for x in common.read_jsonl(common.MARKET_DIR / name))


def notes_step(now: float, state: dict) -> str | None:
    """Read the votes on our posts, then post the next Kiro 데이터 노트 if there is room; None when nothing went out."""
    ns = state.setdefault("notes_state", {})
    posts = common.market_file("forum.json", []) or []
    if not isinstance(posts, list):
        posts = []
    try:
        seen_at = (common.MARKET_DIR / "forum.json").stat().st_mtime
    except OSError:
        seen_at = None
    book = notes.track_votes(posts, now, seen_at)
    notes.remember_posts(posts, common.catalog(), now)      # others' quoted prices, for the follow-up desk
    low = notes.guard(book, now, ns.get("resumed_at", 0))
    if low is not None and now >= ns.get("paused_until", 0):
        ns["paused_until"] = ns["resumed_at"] = now + notes.PAUSE
        log(f"[analysis] notes paused for {notes.PAUSE // 60} min: the last {notes.GUARD_N} scored notes average {low:.3f}")
        return None
    if now < ns.get("paused_until", 0) or buyer_post_waiting() or now - ns.get("last_post", 0) < notes.GAP:
        return None
    note = notes.next_note(notes.gather(now), posts, now, ns, state.get("journal_issues"))
    if not note:
        return None
    forum.outbox(post=note["text"], refs=note["refs"], **{"as": "buyer"})
    ns["last_post"] = now
    common.write_json(folder() / "state.json", state)      # now, not at the pass's end: a kill during the long steps
                                                            # after this (model calls) must not post the same note again
    return f"note {ns['n']} ({note['key']}) posted ({len(note['text'])} chars, {len(note['refs'])} refs)"


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
                elif name == "journal":
                    note = journal_step(now, state)
                    if note is None:
                        log("[analysis] journal: no desk has anything new; skipped")
                        state[name] = now                       # look again in an hour
                        continue
                elif name == "replies":
                    note = replies(now, state)
                elif name == "notes":
                    note = notes_step(now, state)
                    if note is None:
                        state[name] = now
                        continue
                elif name == "linewatch":
                    changed = linewatch.run(now)
                    if not changed:
                        state[name] = now
                        continue
                    note = "; ".join(f"{c['sku']} cap {c['from']}->{c['to']} ({c['why']})" for c in changed)
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
