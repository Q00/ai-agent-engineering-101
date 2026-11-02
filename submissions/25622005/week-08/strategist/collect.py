"""What the strategist looks at once an hour: the public market API and the shop's own files.

Every source is optional. One failing endpoint or a missing file is recorded and the rest of the run
goes on, because a brief with a hole is still better than no brief. A run stays under ~50 requests:
the ledger is paged from a cursor kept in state (at most 40 pages, then a jump to the newest rows,
flagged as a gap), and the 1.2 MB catalog and the demand table change rarely, so they are cached
for 24 h. The shop's files are on a shared volume and are only read here.
"""
import os
from pathlib import Path

from memory.store import atomic_write_json, read_json, tail_jsonl

ME = "psyoung82"
PAGE = 500                  # the ledger's page size and the backfill on a first run
MAX_PAGES = 40
CACHE_S = 24 * 3600
ROLES = ("seller", "buyer", "restock", "review")
TAIL_BYTES = 256 * 1024     # what memory.store.tail_jsonl reads from the end of a file
TAIL_ROWS = 3000
FORUM_KEYS = ("id", "author", "role", "owner", "thread", "ts", "title", "body", "text",
              "votes", "likes", "dislikes", "score")
_memo = {}                  # the 24 h cache when no cache_dir is given (one long-running process)


def _err(name, e):
    return f"{name}: {type(e).__name__}: {str(e)[:200]}"


def _get(client, path, params=None, want=(dict, list)):
    r = client.get(path, params=params)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, want):
        raise TypeError(f"unexpected JSON {type(data).__name__}")
    return data


def _current_round(stats):
    r = stats.get("round")
    rounds = stats.get("rounds") if isinstance(stats.get("rounds"), dict) else {}
    cur = rounds.get(str(r))
    return {"round": r, **cur} if isinstance(cur, dict) else {"round": r}


def _forum(data):
    if isinstance(data, dict):           # shape not fixed yet: accept {"posts": [...]} and friends
        data = next((data[k] for k in ("posts", "items", "data") if isinstance(data.get(k), list)), [])
    return [{k: p[k] for k in FORUM_KEYS if k in p} for p in data if isinstance(p, dict)]


def _catalog(rows):
    """sku -> [name, category, list_price, kind]: the 1.2 MB list kept as a small lookup."""
    return {r["sku"]: [r.get("name"), r.get("category"), r.get("list_price"), r.get("kind")]
            for r in rows if isinstance(r, dict) and isinstance(r.get("sku"), str)}


def _cached(name, now, cache_dir, fetch, errors):
    """Fresh copy if younger than 24 h, else fetch; on a failed fetch an old copy is better than none."""
    path = Path(cache_dir) / f"{name}.json" if cache_dir else None
    hit = read_json(path, None) if path else _memo.get(name)
    hit = hit if isinstance(hit, dict) and "data" in hit and isinstance(hit.get("ts"), (int, float)) else None
    if hit and now - hit["ts"] < CACHE_S:
        return hit["data"]
    try:
        data = fetch()
    except Exception as e:
        if not hit:
            raise
        errors.append(_err(name, e) + f" (using the cache from {(now - hit['ts']) / 3600:.0f} h ago)")
        return hit["data"]
    entry = {"ts": now, "data": data}
    if path:
        try:
            atomic_write_json(path, entry)
        except OSError as e:
            errors.append(_err(f"{name} cache", e))
    else:
        _memo[name] = entry
    return data


def _page(client, after):
    rows = _get(client, "/api/ledger", {"after": after}, list)
    rows = [r for r in rows if isinstance(r, dict) and type(r.get("seq")) is int and (after < 0 or r["seq"] > after)]
    return sorted(rows, key=lambda r: r["seq"])


def _ledger(client, state, raw, errors):
    """New ledger rows since state["ledger_seq"]; advances the cursor over what was read, even on an error.
    A page that starts past after+1 (the market trimmed old rows or ignored `after`) is a gap too."""
    cursor, rows, gap = state.get("ledger_seq"), [], False
    try:
        if cursor is None:
            rows = _page(client, -PAGE)
        else:
            for _ in range(MAX_PAGES):
                after = max([cursor] + [r["seq"] for r in rows])
                page = _page(client, after)
                gap = gap or bool(page) and page[0]["seq"] > after + 1
                rows += page
                if len(page) < PAGE:
                    break
            else:                                       # still behind: skip to the newest rows
                latest = _page(client, -PAGE)
                top = max([cursor] + [r["seq"] for r in rows])
                gap = gap or bool(latest) and latest[0]["seq"] > top + 1
                rows += [r for r in latest if r["seq"] > top]
    except Exception as e:
        errors.append(_err("ledger", e))
    if rows:
        state["ledger_seq"] = max([cursor or 0] + [r["seq"] for r in rows])
    raw["ledger"] = rows
    raw["ledger_gap"] = gap
    raw["ledger_first"] = cursor is None                # only the newest PAGE rows: minutes, not the window


def fetch_public(client, state, now, cache_dir=None, me=ME):
    """(raw, errors). client: httpx.Client(base_url=market). state["ledger_seq"] is read and advanced.
    cache_dir: where catalog.json/demand.json are kept for 24 h (None: in this process only).
    A failed endpoint leaves its key out of raw and adds "<name>: <ExcType>: msg" to errors."""
    raw, errors = {"fetched_at": now}, []

    def take(name, fn):
        try:
            raw[name] = fn()
        except Exception as e:
            errors.append(_err(name, e))

    take("board", lambda: _get(client, "/api/board", want=dict))
    take("stats", lambda: _current_round(_get(client, "/api/stats", want=dict)))
    take("trades", lambda: _get(client, "/api/trades", want=list))
    _ledger(client, state, raw, errors)
    take("forum", lambda: _forum(_get(client, "/api/forum", {"limit": 100})))
    take("shop_reviews", lambda: _get(client, "/api/shop-reviews", {"shop": me}, list))
    take("demand", lambda: _cached("demand", now, cache_dir,
                                   lambda: _get(client, "/api/demand", want=list), errors))
    take("catalog", lambda: _cached("catalog", now, cache_dir,
                                    lambda: _catalog(_get(client, "/api/catalog", want=list)), errors))
    raw["errors"] = errors
    return raw, errors


# --- the shop's own files (shared volume, read-only)

def _recent(rows, since):
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("ts"), (int, float)) and r["ts"] >= since]


def _summary(s):
    """The few parts of memory/summary.json the brief uses: market demand by category and seller counters."""
    if not isinstance(s, dict) or not s:
        return {}
    market = s.get("market") if isinstance(s.get("market"), dict) else {}
    cats = market.get("categories") if isinstance(market.get("categories"), dict) else {}
    demand = lambda v: v.get("demand") if isinstance(v.get("demand"), (int, float)) else 0
    top = sorted(((k, v) for k, v in cats.items() if isinstance(v, dict)), key=lambda kv: -demand(kv[1]))[:30]
    seller = s.get("seller") if isinstance(s.get("seller"), dict) else {}
    stars = [x for x in seller.get("stars") or [] if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return {"built_at": s.get("built_at"),
            "market_categories": {k: {"demand": v.get("demand"), "supply": v.get("supply")} for k, v in top},
            "seller": {"sold_24h": seller.get("sold_24h"), "last_sale_ts": seller.get("last_sale_ts"),
                       "l3": seller.get("l3"), "regular_cats": list(seller.get("regular_cats") or [])[:10],
                       "stars_n": len(stars), "stars_mean": round(sum(stars) / len(stars), 2) if stars else None}}


def _window_rows(path, since, name, truncated):
    """Rows of the window from the file's tail. When even the oldest row read is inside the window and
    the tail was cut (by bytes or rows), the window started earlier: name goes into truncated."""
    rows = tail_jsonl(path, TAIL_ROWS)
    ts = [r["ts"] for r in rows if isinstance(r, dict) and isinstance(r.get("ts"), (int, float))]
    try:
        cut = os.path.getsize(path) > TAIL_BYTES or len(rows) >= TAIL_ROWS
    except OSError:
        cut = False
    if cut and ts and min(ts) >= since:
        truncated.append(name)
    return _recent(rows, since)


def read_own(shop_home, now, window_h=1):
    """The shop's summary and its log rows of the last window_h hours. Never raises; missing parts are empty.
    tail_jsonl reads only the last 256 KB of a file, so a very busy hour is counted from its newest rows;
    own["truncated"] then names that log, and the brief says its counts are too low."""
    home, since = Path(shop_home), now - window_h * 3600
    own = {"window_h": window_h, "summary": {}, "decisions": {}, "acp": [],
           "policy_changes": [], "truncated": []}
    try:
        own["summary"] = _summary(read_json(home / "memory" / "summary.json", {}))
        for role in ROLES:
            own["decisions"][role] = _window_rows(home / "logs" / "decisions" / f"{role}.jsonl", since, role,
                                                  own["truncated"])
        own["acp"] = _window_rows(home / "logs" / "acp-sessions.jsonl", since, "acp", own["truncated"])
        own["policy_changes"] = [r for r in tail_jsonl(home / "logs" / "policy-changes.jsonl", 5) if isinstance(r, dict)]
    except Exception as e:                               # a strange file must not stop the run
        own["error"] = _err("own", e)
    return own
