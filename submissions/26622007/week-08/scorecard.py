"""The numbers every meta-harness cycle starts from, written where a change can cite them line by line.

    python scorecard.py        prints one

Counts come from the logs and files as they are; nothing here judges. Astra owns this file and the grading.
"""
import collections
import json
import os
import re
import time

import httpx
import patch_notes
import profit_cohorts
import strategy_screen

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
ME = os.environ.get("SHOP_MARKET_NAME", "nocha12")
COUNTERS = {"settled_as_shop": "[acp] settled", "paid_as_buyer": "[acp] paid", "buyer_refused_to_pay": "[acp] not paying",
            "checkout_refused": "checkout refused", "settle_refused": "settle refused", "supply": "[supply]",
            "supply_refused": ("[supply]", "refused:"), "tracebacks": "Traceback", "harness_failed": "harness failed",
            "no_stock": "REJECTED (not in stock)", "bankrupt": "[bankrupt]", "broken": "[broken]", "forum": "[forum]",
            "below_cost_offer": "instead of cost", "warranty_given": "WARRANTY ", "warranty_declined": "WARRANTY declined",
            "forum_post_refused": "[forum] post as buyer: 4", "forum_vote_refused": "[forum] vote as buyer: 4",
            "sold": "-> COMPLETED", "talks_rejected": "-> REJECTED"}
SOURCES = ("logs", "memory", ".market", "meta")
# Round 3: parts are percentiles except buyer forum, which is its raw rating; Players = shop ALL + buyer ALL.
# Fitted to the board (review/W-20261104-2030, GPT Pro 3판 §2.1): every row of 268 Round 3 snapshots (13,185 shop rows,
# 12,061 buyer rows) within the board's rounding (0.0005). The rows ranked are those the board scores (all is not None:
# shops with a supply or a deal, house shops included; buyers with a deal), a tie takes the mean rank, a missing value is 0,
# the shop's stars part is stars_rank, and posts count 0.1 of the buyer's ALL. Patch 3.3 (11/05) ranks book (cash profit +
# unsold stock at cost) instead of profit: every shop row of the 80 boards from 03:47:25 KST matches book to the shown
# 3 decimals, every row of the 42 boards up to 03:42:05 matches profit (GPT Pro 4판, checked again on the collector's
# boards). profit stays the fallback for reading a board without book; patch_notes.compare never accepts it as the
# published part.
# Patch 3.4 ranks buyers by saved rate; Patch 3.5 uses the forum rating itself.
OBJECTIVE = {"shops": [(("book", "profit"), 0.5), (("revenue",), 0.3), (("stars_rank", "stars"), 0.2)],
             "buyers": [(("saved",), 0.5), (("forum", "forum_score", "votes", "score"), 0.3), (("deals",), 0.1),
                        (("posts",), 0.1)]}


def number(r, field):
    v = r.get(field)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def percentile(rows, field, mine):
    """Mean-rank percentile among the rows: (below + (ties - 1) / 2) / (n - 1), my own row among the ties."""
    if not rows:
        return None
    x = number(mine, field)
    vals = [number(r, field) for r in rows]
    if len(vals) < 2:
        return 1.0
    below, ties = sum(v < x for v in vals), sum(v == x for v in vals)
    return (below + (max(ties, 1) - 1) / 2) / (len(vals) - 1)     # not rounded: the board rounds only the weighted ALL


def objective(b):
    """My estimated shop ALL, buyer ALL and Players from the board's rows: what every change is meant to raise, and the
    board's own ALL next to it (board_all) so a gap shows. A field the board does not show is 0 for every row."""
    out = {}
    for k, parts in OBJECTIVE.items():
        every = b.get(k) or []
        rows = [r for r in every if r.get("all") is not None] if any("all" in r for r in every) else \
            [r for r in every if r.get("deals")]
        mine = next((r for r in every if r.get("name") == ME or ((r.get("owner") or {}).get("login") or "").lower() == "nocha12"), None)
        if not mine:
            out[k] = {"all": None, "why": "not on the board yet (no deal)"}
            continue
        ranked = mine in rows
        if not ranked:      # the board does not score my row (yet): what it would be if it did, marked as such
            rows = rows + [mine]
        got = {}
        for names, w in parts:
            field = next((n for n in names if n in mine), names[0])
            raw_forum = k == "buyers" and "forum" in names
            p = number(mine, field) if raw_forum else percentile(rows, field, mine)
            got[field] = {"weight": w, "percentile": None if p is None else round(p, 4), "basis": "rating" if raw_forum else "percentile", "value": mine.get(field),
                          "next": next_step(rows, field, mine, w, p, raw=raw_forum), "_p": p}
        total = sum(x["weight"] * x["_p"] for x in got.values() if x["_p"] is not None)
        for x in got.values():
            x.pop("_p")
        out[k] = {"all": round(total, 3), "board_all": mine.get("all"), "parts": got, "of": len(rows), "ranked": ranked}
    shop, buyer = out["shops"].get("all"), out["buyers"].get("all")
    out["players"] = None if shop is None and buyer is None else round((shop or 0) + (buyer or 0), 3)
    # Drift from the board's own ALL: a non-zero value means the formula above no longer matches the board.
    out["board_drift"] = {k: round(out[k]["all"] - out[k]["board_all"], 6)
                          for k in OBJECTIVE if isinstance(out[k].get("all"), (int, float))
                          and isinstance(out[k].get("board_all"), (int, float))}
    players = [r for r in b.get("players", []) if isinstance(r.get("total"), (int, float))]
    mine_player = next((r for r in players if str(r.get("player", "")).lower() == "nocha12"
                        or str(r.get("login", "")).lower() == "nocha12"), None)
    out["board_players_gap"] = (round(max(r["total"] for r in players) - mine_player["total"], 6)
                                if mine_player else None)
    # weakest: the part whose next step is worth most to Players (weight x percentile left), not just the lowest percentile
    out["weakest"] = max(((k, f, x["percentile"]) for k in OBJECTIVE for f, x in (out[k].get("parts") or {}).items()
                          if x["percentile"] is not None), key=lambda t: dict(
                              (f, w) for names, w in OBJECTIVE[t[0]] for f in names).get(t[1], 0) * (1 - t[2]), default=None)
    # next_steps: every part's next rival, how far it is (need) and what passing it adds to Players, the largest first
    # (GPT Pro 11/05 13:30 §7: weight x percentile left is the distance to first place, not the next reachable step)
    out["next_steps"] = sorted(({"part": k, "field": f, **x["next"]} for k in OBJECTIVE
                                for f, x in (out[k].get("parts") or {}).items() if x.get("next")),
                               key=lambda s: -s["delta_players"])
    return out


def next_step(rows, field, mine, weight, p, raw=False):
    """The lowest value above mine among the other rows (the next rival on this part), how much I need to pass it, and
    what reaching it adds to Players: weighted rating gain for forum, percentile gain otherwise. None when I lead."""
    if p is None:
        return None
    x = number(mine, field)
    others = [number(r, field) for r in rows if r is not mine]
    above = [v for v in others if v > x]
    if not above:
        return None
    rival = min(above)
    passed = rival if raw else sum(v <= rival for v in others) / len(others)      # mine just above the rival, tied with nobody
    return {"rival_value": rival, "need": round(rival - x, 6), "delta_players": round(weight * (passed - p), 5)}


HISTORY = os.path.join(".market", "board_history.jsonl")
GROWTH_HOURS = (3, 6, 12)  # 3 h: the Pro cycle judges every 3 h (GPT Pro 11/07 01:35 §9, 04:30 §9)
HISTORY_HOURS = 13         # the history keeps what the longest growth window needs
FORUM = next(names for names, _ in OBJECTIVE["buyers"] if "forum" in names)


def snapshot(b):
    """The board in a few numbers per row, for the growth of me and the players around me."""
    return {"ts": time.time(),
            "players": {str(r.get("player")): r.get("total") for r in b.get("players") or [] if r.get("player")},
            "shops": {r.get("name"): [r.get("book"), r.get("revenue"), r.get("all")] for r in b.get("shops") or []},
            "buyers": {r.get("name"): [r.get("saved"), next((r[n] for n in FORUM if n in r), None), r.get("all")]
                       for r in b.get("buyers") or []}}


def history(root):
    return jsonl(os.path.join(root, HISTORY))


def forum_then(past, hours, now=None):
    """My buyer forum score on the last history line at least `hours` old, or None."""
    now = time.time() if now is None else now
    old = [p for p in past if isinstance(p.get("ts"), (int, float)) and p["ts"] <= now - hours * 3600]
    row = ((old[-1].get("buyers") or {}).get(ME) if old else None) or []
    return row[1] if len(row) > 1 and isinstance(row[1], (int, float)) else None


# harness.OR_MAX_CALLS_PER_HOUR is off by default since 11/05 (a person's call: the seller gave up thousands of
# negotiations at 300 an hour). The total calls over processes is a cost shown in llm_last_hour, not an alert. If a cap
# is set again, the seller's own refusals are no alert either: on a numeric offer it answers by code (1,527 of 1,635
# refusals 11/05 04:07-11:20, "cannot answer" 0 times).
CAP_HARMLESS = ("seller-harness",)
WORST_COST_SHARE = 0.9   # 0.75 x list x restock.SURGE_HALF: a unit above it was bought deep in a surge
FORUM_DROP = 0.01


def alerts(md, card_board, now=None):
    """Signals a cycle must not pass over with no_change: a buyer or restock call refused at its cap (only when one is set
    again), stock bought above the worst cost restock reserves for, the buyer forum falling, the scorecard drifting from
    the board, the rules disagreeing with it, the LLM cache share falling (cache_drop). Each is a short sentence with its numbers. meta_harness refuses a no_change
    answer that leaves one of them open. How many calls the last hour made is no alert: there is no cap (llm_last_hour)."""
    now = time.time() if now is None else now
    out = []
    caps = [c for c in jsonl(os.path.join(md, "measure", "llm_cap.jsonl"))[-5000:]
            if (c.get("ts") or 0) >= now - 3600 and c.get("tag") not in CAP_HARMLESS]
    if caps:
        out.append(f"llm_cap: {len(caps)} calls refused at the cap in the last hour "
                   f"({collections.Counter(c.get('tag') for c in caps).most_common(3)})")
    cat = jsonl_or_json(os.path.join(md, "catalog.json")) or {}
    try:
        with open(os.path.join(md, "inventory.json"), encoding="utf-8") as f:
            units = json.load(f).get("units") or []
    except (OSError, ValueError):
        units = []
    over = []
    for u in units:
        lp = (cat.get(u.get("sku")) or {}).get("list_price") if isinstance(cat, dict) else None
        if lp and (u.get("supplied_at") or 0) >= now - 6 * 3600 and (u.get("cost") or 0) > WORST_COST_SHARE * lp:
            over.append((u.get("sku"), round(u["cost"] / lp, 3)))
    if over:
        out.append(f"surge: {len(over)} unit(s) in stock bought in the last 6 h above {WORST_COST_SHARE} x list "
                   f"(worst {max(r for _, r in over)}; {collections.Counter(s for s, _ in over).most_common(3)})")
    obj = card_board.get("objective") or {}
    parts = (obj.get("buyers") or {}).get("parts") or {}
    now_forum = next(((parts[n] or {}).get("value") for n in FORUM if n in parts), None)
    then = card_board.get("forum_6h_ago")
    if isinstance(now_forum, (int, float)) and isinstance(then, (int, float)) and now_forum < then - FORUM_DROP:
        out.append(f"forum: buyer forum {now_forum} now, {then} 6 h ago")
    drift = {k: v for k, v in (obj.get("board_drift") or {}).items() if abs(v) > 0.001}
    if drift:
        out.append(f"board_drift: scorecard ALL differs from the board by {drift}")
    cache = cache_drop(md, now)
    if cache:
        out.append(cache)
    return out


CACHE_MIN_CALLS = 50     # fewer calls in the hour than this: the share is noise (one hit is 6,912 of ~9,000 tokens)
CACHE_FLOOR = 0.10       # 11/05 measured 0.15 (random cwd) to 0.3-0.5 (fixed cwd) on 10-call runs
CACHE_DROP = 0.5         # the last hour under half of the 6 hours before it


def cache_drop(md, now=None):
    """The LLM cache hit share falling: the last hour under CACHE_FLOOR, or under CACHE_DROP of the 6 hours before. Only an
    alert to look at what changed in the prompt (a system prompt with a time or a price in it, a new codex): a person
    forbade a call cap, so the answer is never fewer calls. Only the backend in use now (the newest call's), so a switch (codex -> openrouter, 11/06) is not read as a drop."""
    now = time.time() if now is None else now
    calls = jsonl(os.path.join(md, "llm_calls.jsonl"))[-40000:]
    backend = next((c.get("backend") for c in reversed(calls) if c.get("backend")), "codex")
    rows = [c for c in calls if c.get("backend") == backend
            and isinstance(c.get("ts"), (int, float)) and c["ts"] >= now - 7 * 3600]
    hour = cache_share([c for c in rows if c["ts"] >= now - 3600])
    before = cache_share([c for c in rows if c["ts"] < now - 3600])
    if hour["calls"] < CACHE_MIN_CALLS or hour["ratio"] is None:
        return None
    low = hour["ratio"] < CACHE_FLOOR
    fell = before["calls"] >= CACHE_MIN_CALLS and before["ratio"] and hour["ratio"] < CACHE_DROP * before["ratio"]
    if not (low or fell):
        return None
    return (f"llm_cache: cached input share {hour['ratio']} over {hour['calls']} calls in the last hour, "
            f"{before['ratio']} over {before['calls']} in the 6 h before (floor {CACHE_FLOOR}); look at what changed in "
            f"the prompts, never cut calls")


def race(b, past, now=None):
    """Where Nocha12 stands among Everyone players: rank, the gap to the leader and to the player just above, and how
    fast each of the three grew over the last 3, 6 and 12 hours (from .market/board_history.jsonl, one line per cycle). A
    gap closes only if I grow faster than the player above: the growth says whether the current strategy can do it."""
    now = time.time() if now is None else now
    players = [r for r in b.get("players") or [] if isinstance(r.get("total"), (int, float))]
    players.sort(key=lambda r: -r["total"])
    idx = next((i for i, r in enumerate(players) if str(r.get("player", "")).lower() == "nocha12"), None)
    if idx is None:
        return {"rank": None, "why": "not among the players"}
    me, lead = players[idx], players[0]
    above = players[idx - 1] if idx else None
    out = {"rank": idx + 1, "of": len(players), "total": me["total"], "leader": lead.get("player"),
           "gap_to_leader": round(lead["total"] - me["total"], 3),
           "above": above.get("player") if above else None,
           "gap_to_above": round(above["total"] - me["total"], 3) if above else 0.0, "growth": {}}
    for h in GROWTH_HOURS:
        old = [p for p in past if isinstance(p.get("ts"), (int, float)) and p["ts"] <= now - h * 3600]
        if not old:
            out["growth"][f"{h}h"] = None
            continue
        then = old[-1].get("players") or {}
        g = lambda name: None if name is None or not isinstance(then.get(str(name)), (int, float)) else \
            round(next(r["total"] for r in players if r.get("player") == name) - then[str(name)], 3)
        out["growth"][f"{h}h"] = {"me": g(me.get("player")), "leader": g(lead.get("player")),
                                  "above": g(above.get("player")) if above else None,
                                  "hours": round((now - old[-1]["ts"]) / 3600, 1)}
    return out


def jsonl(path):
    """Every readable line: a line cut by a killed append is skipped, not the whole file."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for x in f:
                try:
                    r = json.loads(x)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    out.append(r)
    except OSError:
        pass
    return out


def board(root=None, record=False):
    """My rows on the board, and where they stand. The board's fields beyond week 7's show up as they are. With root, the
    race against the players around me (race); record=True also appends this board to the history (a cycle does, a look
    by hand does not)."""
    try:
        b = httpx.get(f"{MARKET}/api/board", timeout=20).json()
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = {"round": b.get("round"), "totals": b.get("totals"), "objective": objective(b)}
    if root:
        past = history(root)
        out["race"] = race(b, past)
        out["forum_6h_ago"] = forum_then(past, 6)
        if record:
            keep = [p for p in past if isinstance(p.get("ts"), (int, float)) and p["ts"] > time.time() - HISTORY_HOURS * 3600]
            path = os.path.join(root, HISTORY)
            tmp = f"{path}.{os.getpid()}.tmp"      # two recorders at once each write their own
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.writelines(json.dumps(p, ensure_ascii=False) + "\n" for p in keep + [snapshot(b)])
                os.replace(tmp, path)
            except OSError:
                pass
    for k in ("shops", "buyers", "players"):
        rows = b.get(k) or []
        mine = next((r for r in rows if r.get("name") == ME or (r.get("login") or "").lower() == "nocha12"
                     or str(r.get("player") or "").lower() == "nocha12"
                     or ((r.get("owner") or {}).get("login") or "").lower() == "nocha12"), None)
        out[k] = {"count": len(rows), "mine": {x: v for x, v in (mine or {}).items() if x not in ("card", "owner")}}
    out["rivals"] = rivals(b.get("shops") or [])
    return out


RIVAL_FIELDS = ("name", "deals", "visits", "share", "revenue", "book", "stock_cost", "profit", "stars", "refunds", "broken", "honest_claims", "claims")


def rivals(rows, top=5):
    """The shops ahead in book (Patch 3.3; profit on a board without book) and how they present themselves: the strategy
    check compares mine with theirs."""
    key = "book" if any(isinstance(r.get("book"), (int, float)) for r in rows) else "profit"
    ranked = sorted((r for r in rows if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool)),
                    key=lambda r: -r[key])
    me = next((i for i, r in enumerate(ranked, 1)
               if r.get("name") == ME or ((r.get("owner") or {}).get("login") or "").lower() == "nocha12"), None)
    card = lambda r: str((r.get("card") or {}).get("description") or "")[:240] if isinstance(r.get("card"), dict) else ""
    return {"my_rank": me, "ranked_by": key, "of": len(ranked),
            "top": [{**{f: r.get(f) for f in RIVAL_FIELDS}, "card": card(r)} for r in ranked[:top]]}


def file_index(root):
    """Every log and record the cycle may read, with its size: the meta-harness reads the raw files it wants."""
    out = {}
    for top in SOURCES:
        for base, _, names in os.walk(os.path.join(root, top)):
            if os.path.join("meta", "evidence") in base:
                continue
            for n in names:
                p = os.path.join(base, n)
                out[os.path.relpath(p, root)] = os.path.getsize(p)
    return out


# a2a's opentelemetry wrapper logs "Failed to detach context" and two tracebacks (GeneratorExit, then "ValueError: <Token
# ...> was created in a different Context") when a buyer drops a seller stream; the seller carries on. 106 such blocks made
# all 212 tracebacks in the kit log by 11/06 04:30, and Terra's tested a84f87c and dc91249 were undone as "tracebacks +24".
DETACH_NOISE = ("Failed to detach context", "was created in a different Context")
TRACE_PARTS = ("  ", "Traceback", "During handling of the above exception", "GeneratorExit")


def log_tag(line):
    """(`[seller]`, rest) from `03:28:36 [seller] rest`, so one process's block is not cut by another's lines."""
    m = re.match(r"\S+ (\[[^\]]+\]) ?(.*)", line)
    return (m.group(1), m.group(2)) if m else ("", line)


def log_counts(root, name="market-kit.log", start=0):
    """Counts of COUNTERS in the kit log from line `start` on. A tuple needle needs all its parts in the line:
    supply_refused is a refused [supply] line only, not also "[acp] checkout refused:" (11/05 22:25 Astra's watch read
    10 checkout refusals and 1 supply 502 as supply_refused +11).
    The two tracebacks of a DETACH_NOISE block through opentelemetry frames go to tracebacks_detach_noise, not tracebacks.
    A block with another count of tracebacks, a nested one ("  + Exception Group Traceback"), no opentelemetry frame or
    another ending counts as tracebacks. One still open at the end of the file goes to tracebacks_open: it is not known
    yet, and a later count must not take it back from tracebacks (GPT Pro 11/06 04:44 7-1: 2 open at the start of a watch,
    reread as noise at its end, hid 3 new real ones under the +3 limit)."""
    counts = collections.Counter()
    pending = {}  # process tag -> [tracebacks seen since its "Failed to detach context", an opentelemetry frame seen]
    try:
        with open(os.path.join(root, "logs", name), encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i < start:
                    continue
                tag, body = log_tag(line.rstrip("\n"))
                if DETACH_NOISE[0] in body:
                    counts["tracebacks"] += pending.pop(tag, [0])[0]
                    pending[tag] = [0, False]
                elif tag in pending:
                    if DETACH_NOISE[1] in body:
                        n, otel = pending.pop(tag)
                        counts["tracebacks_detach_noise" if n == 2 and otel else "tracebacks"] += n
                    elif body.startswith("Traceback"):
                        pending[tag][0] += 1
                    elif "Traceback" in body or body.strip() and not body.startswith(TRACE_PARTS):
                        counts["tracebacks"] += pending.pop(tag)[0]
                    elif "/opentelemetry/" in body:
                        pending[tag][1] = True
                for k, needle in COUNTERS.items():
                    if k == "tracebacks" and tag in pending:
                        continue
                    if all(n in line for n in needle) if isinstance(needle, tuple) else needle in line:
                        counts[k] += 1
                counts["lines"] += 1
    except OSError:
        pass
    counts["tracebacks_open"] += sum(n for n, _ in pending.values())
    return {k: counts[k] for k in ["lines", *COUNTERS, "tracebacks_detach_noise", "tracebacks_open"]}


def llm_table(md, last=2000):
    """What each kind of model call costs lately: how often, how large, how slow, how many failed."""
    table = collections.defaultdict(lambda: {"calls": 0, "failed": 0, "chars": 0, "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0})
    rows = jsonl(os.path.join(md, "llm_calls.jsonl"))[-last:]
    for c in rows:
        t = table[c.get("tag")]
        t["calls"] += 1
        t["failed"] += 0 if c.get("ok") else 1
        t["chars"] += c.get("chars") or 0
        t["seconds"] += c.get("seconds") or 0
        for k in ("prompt_tokens", "completion_tokens", "cost"):
            t[k] += c.get(k) or 0
    out = {k: {**v, "mean_chars": round(v["chars"] / v["calls"]), "mean_seconds": round(v["seconds"] / v["calls"], 2)} for k, v in table.items()}
    for k in out:
        out[k]["cache"] = cache_share([c for c in rows if c.get("tag") == k])
    return out


def cache_share(calls):
    """Cached input tokens over input tokens, on the calls that report it (lines from before 11/05 12:50 do not)."""
    count = lambda x: type(x) is int and x >= 0
    known = [c for c in calls if c.get("ok") is True and count(c.get("cached_tokens")) and count(c.get("prompt_tokens"))
             and c["prompt_tokens"] > 0]
    prompt = sum(c["prompt_tokens"] for c in known)
    return {"calls": len(known), "prompt_tokens": prompt, "cached_tokens": sum(c["cached_tokens"] for c in known),
            "ratio": round(sum(c["cached_tokens"] for c in known) / prompt, 3) if prompt else None}


def llm_last_hour(md, now=None):
    """Luna calls over the last hour, all processes, by backend (codex on the subscription reports no cost; lines from
    before 11/05 have no backend and were OpenRouter): shown, not a limit (no hourly cap)."""
    now = time.time() if now is None else now
    calls = [c for c in jsonl(os.path.join(md, "llm_calls.jsonl"))[-20000:] if (c.get("ts") or 0) >= now - 3600]
    return {"calls": len(calls), "failed": sum(1 for c in calls if not c.get("ok")),
            "cost": round(sum(c.get("cost") or 0 for c in calls), 4),
            "tags": collections.Counter(c.get("tag") for c in calls).most_common(4),
            "backends": dict(collections.Counter(c.get("backend") or "openrouter" for c in calls)),
            "cache": cache_share(calls)}


def memory_timing(md, last=500):
    """Latest size and mean milliseconds of each memory operation: does the memory slow the agent as it grows?"""
    rows, out = jsonl(os.path.join(md, "timing.jsonl"))[-last:], {}
    for r in rows:
        x = out.setdefault(r.get("what"), {"n": 0, "ms": 0, "bytes": 0})
        x["n"] += 1
        x["ms"] += r.get("ms") or 0
        x["bytes"] = r.get("bytes") or 0
    return {k: {"mean_ms": round(v["ms"] / v["n"]), "bytes": v["bytes"], "n": v["n"]} for k, v in out.items()}


def unsold(units, now=None):
    """Stock that has waited a day or more: count, cost, and the SKUs with the most money in them."""
    now = now or time.time()
    old = [u for u in units if now - (u.get("supplied_at") or now) >= 86400]
    by = collections.Counter()
    for u in old:
        by[u.get("sku")] += u.get("cost") or 0
    return {"units": len(old), "cost": round(sum(by.values())), "top": by.most_common(5)}


def live_stock_cost(units, now=None):
    """What the stock still here cost, expired food left out: the most it can bring back at cost."""
    now = now or time.time()
    return round(sum(u.get("cost") or 0 for u in units
                     if u.get("grade") != "expired" and not (u.get("expires_at") and u["expires_at"] <= now)))


def jsonl_or_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build(root, record=False):
    md = os.path.join(root, ".market")
    rules, b = patch_notes.check(root, record=record), board(root, record=record)
    warn = alerts(md, b) if "error" not in b else []
    if rules.get("objective_matches") is False:
        warn.append(f"rules: the published score disagrees with scorecard.OBJECTIVE ({rules.get('mismatch')})")
    try:
        with open(os.path.join(md, "inventory.json"), encoding="utf-8") as f:
            inv = json.load(f)
    except (OSError, ValueError):
        inv = {}
    units = inv.get("units") or []
    calls, decisions, pays = (jsonl(os.path.join(md, f)) for f in ("tool_calls.jsonl", "decisions.jsonl", "payments.jsonl"))
    tools = collections.defaultdict(lambda: {"calls": 0, "failed": 0})
    for c in calls:
        tools[c.get("tool")]["calls"] += 1
        tools[c.get("tool")]["failed"] += 0 if c.get("ok") else 1
    return {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "alerts": warn, "rules": rules, "board": b,
            "shop": {"balance": inv.get("balance"), "profit_if_stock_is_worthless":
                     None if inv.get("balance") is None else inv["balance"] - (inv.get("start") or 0) - (inv.get("injected") or 0),
                     "units": len(units), "stock_cost": round(sum(u.get("cost") or 0 for u in units)),
                     "unsold_over_24h": unsold(units), "tools_status": jsonl_or_json(os.path.join(md, "tools_status.json")),
                     "profit_if_stock_sells_at_cost": None if inv.get("balance") is None else
                     inv["balance"] - (inv.get("start") or 0) - (inv.get("injected") or 0) + live_stock_cost(units),
                     "losses": (jsonl_or_json(os.path.join(md, "loss_state.json")) or {}).get("totals", {})},
            "profit_cohorts": profit_cohorts.build(root),
            "market_opportunities": strategy_screen.build(root),
            "log_counts": log_counts(root),
            "llm": llm_table(md), "llm_last_hour": llm_last_hour(md), "memory_timing": memory_timing(md),
            "tools": dict(tools), "decisions": len(decisions), "last_decisions": decisions[-5:],
            "payments": {"paid": sum(1 for p in pays if p.get("paid")), "refused": sum(1 for p in pays if not p.get("paid")),
                         "refused_for_warranty": sum(1 for p in pays if not p.get("paid") and "warranty" in str(p.get("reason"))),
                         "refusal_reasons": collections.Counter(str(p.get("reason"))[:80] for p in pays if not p.get("paid")).most_common(10)},
            "files": file_index(root)}


if __name__ == "__main__":
    print(json.dumps(build(os.path.dirname(os.path.abspath(__file__))), ensure_ascii=False, indent=1))   # records nothing
