"""What the strategist looks at every 30 minutes: the public market API and the shop's own files.

Every source is optional. One failing endpoint or a missing file is recorded and the rest of the run
goes on, because a brief with a hole is still better than no brief. A run stays under ~50 requests:
the ledger is paged from a cursor kept in state (at most 40 pages, then a jump to the newest rows,
flagged as a gap), and the 1.2 MB catalog changes rarely, so it is cached for 24 h. /api/trades (the last
~4 minutes) and /api/demand (SKU counts, not demand) are no longer read. The ledger rows a run reads are the
ones since the last run (raw["ledger_h"] says how many hours that is), and their settles are also kept in
10-minute buckets for 6 h (ledger_buckets), so an hour or two of market settles can be counted after the rows
themselves are gone; sku_tables keeps the same per SKU (market and our settles, paid/list, market supply) for
6 h and per kind for 24 h. /api/shops gives the units on every shop's shelf per SKU (shelf), /api/stats the
deals per category leaf since the last run (leaf_deltas), and the reviews of the top 3 shops by ALL are
reduced to counts per catalog SKU and leaf (top_reviews): their text is never kept. The shop's files are on a
shared volume and are only read here. The kit's own log
(logs/market-kit.log: supply buys, payments, restarts, errors) is reduced to counts. Of the shop's
.market/ folder only inventory.json is read: the other files there hold keys and the claim code.
"""
import bisect
import json
import os
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from memory.store import atomic_write_json, read_json, tail_jsonl

ME = "psyoung82"
PAGE = 500                  # the ledger's page size and the backfill on a first run
MAX_PAGES = 40
CACHE_S = 24 * 3600
ROLES = ("seller", "buyer", "restock", "review", "forum")
TAIL_BYTES = 256 * 1024     # what is read from the end of a log file
TAIL_ROWS = 3000
# seller.jsonl grows ~1.4 MB (2,350 rows) an hour, buyer.jsonl ~0.4 MB: 2 MB covers the hour and buyer ~5 h
TAIL_BYTES_BY_ROLE = {"seller": 2 << 20, "buyer": 2 << 20}
TAIL_ROWS_BY_ROLE = {"seller": 6000, "buyer": 6000}
HISTORY_ROLES = ("buyer", "forum")      # also kept for history_h hours (strategist/outcome.py)
BUCKET_S = 600
BUCKETS_KEEP_S = 6 * 3600
BANDS = (20000, 50000, 100000, 200000, 500000, 1000000)    # list price band i = bisect(BANDS, list price)
KIT_TAIL_BYTES = 1024 * 1024    # market-kit.log grows ~0.5 MB an hour
CLOCK_SLACK_S = 120         # a kit line may be stamped a little after `now`, or out of order across threads
OLD_RUN = 20                # consecutive kit lines before the window that end the walk back
FORUM_KEYS = ("id", "author", "role", "owner", "thread", "ts", "title", "body", "text",
              "votes", "likes", "dislikes", "score")
UNIT_KEYS = ("unit", "sku", "kind", "cost", "grade", "supplied_at", "expires_at")
SKU_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
G_SKU = re.compile(r"(?<![A-Za-z0-9])G[0-9a-f]{8}(?![A-Za-z0-9])")
TOP_SHOPS = 3
# review texts name a product as "Paid N for <name>, ...", "<name>: N (..% under list)", "<name> at N (list M)"
# or "<name> grade A ..."; the price is the number after "Paid ", " at " or ": "
PAID_FOR = re.compile(r"Paid \d+(?: of \d+)? for (.{2,200}?)(?:[,;]| grade | \(list |$)")
NAME_LEAD = re.compile(r"^(.{2,200}?)(?:: \d| at \d|,? grade [ABC]\b| \(list )")
PRICE = re.compile(r"(?:Paid |\bat |: )(\d{2,})\b")
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
    """sku -> [name, category, list_price, kind, shelf_hours]: the 1.2 MB list kept as a small lookup."""
    return {r["sku"]: [r.get("name"), r.get("category"), r.get("list_price"), r.get("kind"), r.get("shelf_hours")]
            for r in rows if isinstance(r, dict) and isinstance(r.get("sku"), str)}


def _catalog_ok(data):
    """A cached catalog from before shelf_hours was kept is fetched again."""
    return isinstance(data, dict) and all(isinstance(v, list) and len(v) >= 5 for v in data.values())


def _cached(name, now, cache_dir, fetch, errors, ok=lambda data: True):
    """Fresh copy if younger than 24 h (and ok), else fetch; on a failed fetch an old copy is better than none."""
    path = Path(cache_dir) / f"{name}.json" if cache_dir else None
    hit = read_json(path, None) if path else _memo.get(name)
    hit = hit if isinstance(hit, dict) and "data" in hit and isinstance(hit.get("ts"), (int, float)) else None
    if hit and now - hit["ts"] < CACHE_S and ok(hit["data"]):
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


def _ledger(client, state, raw, errors, now):
    """New ledger rows since state["ledger_seq"]; advances the cursor over what was read, even on an error.
    A page that starts past after+1 (the market trimmed old rows or ignored `after`) is a gap too.
    raw["ledger_h"]: hours since the last complete read (state["ledger_ts"]), the span the rows cover;
    None on a first read or when that time is unknown."""
    cursor, rows, gap = state.get("ledger_seq"), [], False
    last = state.get("ledger_ts")
    raw["ledger_h"] = (round((now - last) / 3600, 4) if cursor is not None and isinstance(last, (int, float))
                       and not isinstance(last, bool) and now > last else None)
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
        state["ledger_ts"] = now                        # a failed read leaves the span to the next run
    except Exception as e:
        errors.append(_err("ledger", e))
    if rows:
        state["ledger_seq"] = max([cursor or 0] + [r["seq"] for r in rows])
    raw["ledger"] = rows
    raw["ledger_gap"] = gap
    raw["ledger_first"] = cursor is None                # only the newest PAGE rows: minutes, not the window


def fetch_public(client, state, now, cache_dir=None, me=ME):
    """(raw, errors). client: httpx.Client(base_url=market). state["ledger_seq"] is read and advanced.
    cache_dir: where catalog.json is kept for 24 h (None: in this process only). Also advances
    state["stats_items"] (leaf_deltas).
    A failed endpoint leaves its key out of raw and adds "<name>: <ExcType>: msg" to errors."""
    raw, errors = {"fetched_at": now}, []

    def take(name, fn):
        try:
            raw[name] = fn()
        except Exception as e:
            errors.append(_err(name, e))

    take("board", lambda: _get(client, "/api/board", want=dict))
    take("stats", lambda: _current_round(_get(client, "/api/stats", want=dict)))
    raw["leaf_deltas"] = leaf_deltas(state, raw.get("stats"), now)
    _ledger(client, state, raw, errors, now)
    take("forum", lambda: _forum(_get(client, "/api/forum", {"limit": 100})))
    take("shop_reviews", lambda: _get(client, "/api/shop-reviews", {"shop": me}, list))
    take("catalog", lambda: _cached("catalog", now, cache_dir,
                                    lambda: _catalog(_get(client, "/api/catalog", want=list)), errors, _catalog_ok))
    take("shelf", lambda: shelf(_get(client, "/api/shops", want=(dict, list)), me))
    if isinstance(raw.get("catalog"), dict) and isinstance(raw.get("board"), dict):
        raw["top_reviews"] = top_reviews(client, raw["board"], raw.get("stats"), raw["catalog"], me, errors)
    raw["errors"] = errors
    return raw, errors


# --- market structure: shelves (/api/shops), category deals (/api/stats), the leaders' reviews

def shelf(data, me=ME):
    """/api/shops as {"units": {sku: units on all shelves}, "shops": {sku: other shops holding it}, "n_shops",
    "skipped"}: a shop whose stock is not a {sku: count} map is skipped. Names and reviews are dropped."""
    rows = data.get("shops") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise TypeError("no shops list")
    units, shops, n, skipped = Counter(), Counter(), 0, 0
    for r in rows:
        st = r.get("stock") if isinstance(r, dict) else None
        if not isinstance(st, dict) or not all(isinstance(k, str) and SKU_RE.fullmatch(k) and _number(v) and v >= 0
                                               for k, v in st.items()):
            skipped += 1
            continue
        n += 1
        mine = r.get("name") == me
        for k, v in st.items():
            if v > 0:
                units[k] += int(v)
                if not mine:
                    shops[k] += 1
    return {"units": dict(units), "shops": dict(shops), "n_shops": n, "skipped": skipped}


def leaf_deltas(state, stats, now):
    """Deals per category leaf since the last run, from the cumulative /api/stats items (state["stats_items"]
    keeps the last counts): {"h": hours, "n": {leaf: deals}} or None on a first run, a new round or bad data."""
    items = stats.get("items") if isinstance(stats, dict) else None
    if not isinstance(items, dict):
        return None
    cur = {k: v["n"] for k, v in items.items() if isinstance(k, str) and isinstance(v, dict) and _number(v.get("n"))}
    prev = state.get("stats_items") if isinstance(state.get("stats_items"), dict) else {}
    state["stats_items"] = {"ts": now, "round": stats.get("round"), "n": cur}
    pn, pts = prev.get("n"), prev.get("ts")
    if not isinstance(pn, dict) or not _number(pts) or now <= pts or prev.get("round") != stats.get("round"):
        return None
    out = {k: v - pn.get(k, 0) for k, v in cur.items() if _number(pn.get(k, 0)) and v - pn.get(k, 0) > 0}
    return {"h": round((now - pts) / 3600, 3), "n": out}


def _names(catalog):
    return {c[0]: sku for sku, c in catalog.items() if isinstance(c, list) and isinstance(c[0], str)}


def review_hits(reviews, catalog, names=None):
    """Counts from review texts, never the text itself: {"reviews", "sku": {sku: n}, "leaf": {leaf: n},
    "paid_list": [paid / list]}. A review names a SKU by its id or by an exact catalog name (PAID_FOR, NAME_LEAD);
    paid/list is taken when it names exactly one SKU and gives a price (PRICE)."""
    names = names if names is not None else _names(catalog)
    skus, leaves, pl, n = Counter(), Counter(), [], 0
    for r in reviews if isinstance(reviews, list) else []:
        t = r.get("review") if isinstance(r, dict) else None
        if not isinstance(t, str):
            continue
        n += 1
        t = " ".join(t.split())[:600]
        found = {g for g in G_SKU.findall(t) if g in catalog}
        for rx in (PAID_FOR, NAME_LEAD):
            m = rx.search(t)
            if m and m.group(1).strip() in names:
                found.add(names[m.group(1).strip()])
        for sku in found:
            skus[sku] += 1
            leaf = str(catalog[sku][1] or "").split(">")[-1]
            if leaf:
                leaves[leaf] += 1
        m = PRICE.search(t)
        if m and len(found) == 1:
            lp = catalog[next(iter(found))][2]
            if _number(lp) and lp > 0:
                pl.append(round(int(m.group(1)) / lp, 4))
    return {"reviews": n, "sku": dict(skus), "leaf": dict(leaves), "paid_list": pl}


def top_reviews(client, board, stats, catalog, me, errors):
    """The reviews of this round of the TOP_SHOPS real shops by ALL (us left out), as review_hits counts summed
    over them; None when the board has no ALL."""
    shops = [s for s in board.get("shops") or [] if isinstance(s, dict) and not s.get("house")
             and s.get("name") != me and isinstance(s.get("name"), str) and _number(s.get("all"))]
    top = sorted(shops, key=lambda s: -s["all"])[:TOP_SHOPS]
    if not top:
        return None
    rnd = stats.get("round") if isinstance(stats, dict) else None
    names, out = _names(catalog), {"shops": 0, "reviews": 0, "sku": Counter(), "leaf": Counter(), "paid_list": []}
    for s in top:
        try:
            params = {"shop": s["name"]} | ({"round": rnd} if rnd is not None else {})
            h = review_hits(_get(client, "/api/shop-reviews", params, list), catalog, names)
        except Exception as e:
            errors.append(_err("top_reviews", e))
            continue
        out["shops"] += 1
        out["reviews"] += h["reviews"]
        out["sku"].update(h["sku"])
        out["leaf"].update(h["leaf"])
        out["paid_list"] += h["paid_list"]
    out["sku"], out["leaf"] = dict(out["sku"].most_common(30)), dict(out["leaf"].most_common(15))
    pl = out.pop("paid_list")
    out["paid_list_median"] = round(statistics.median(pl), 3) if pl else None
    return out


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
            "market_breakage": market.get("breakage") if isinstance(market.get("breakage"), dict) else {},
            "hour_profile": market.get("hour_profile") if isinstance(market.get("hour_profile"), dict) else {},
            "seller": {"sold_24h": seller.get("sold_24h"), "last_sale_ts": seller.get("last_sale_ts"),
                       "l3": seller.get("l3"), "regular_cats": list(seller.get("regular_cats") or [])[:10],
                       "stars_n": len(stars), "stars_mean": round(sum(stars) / len(stars), 2) if stars else None}}


def _tail(path, n, max_bytes):
    """(the last n parseable rows within the file's last max_bytes, cut): cut when rows were left out."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - max_bytes))
            lines = f.read().split(b"\n")
    except OSError:
        return [], False
    if size > max_bytes:
        lines = lines[1:]                               # the first line read may start mid-line
    rows = []
    for line in reversed(lines):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
        if len(rows) >= n:
            break
    return rows[::-1], size > max_bytes or len(rows) >= n


def _window_rows(path, since, name, truncated, keep_since=None):
    """Rows of the window from the file's tail (rows since keep_since when that is earlier). When even the
    oldest row read is inside the window and the tail was cut (by bytes or rows), the window started
    earlier: name goes into truncated."""
    rows, cut = _tail(path, TAIL_ROWS_BY_ROLE.get(name, TAIL_ROWS), TAIL_BYTES_BY_ROLE.get(name, TAIL_BYTES))
    ts = [r["ts"] for r in rows if isinstance(r, dict) and isinstance(r.get("ts"), (int, float))]
    if cut and ts and min(ts) >= since:
        truncated.append(name)
    return _recent(rows, min(since, keep_since if keep_since is not None else since))


# --- the kit's log (logs/market-kit.log): "HH:MM:SS [tag] text", local time, no date

_STAMP = re.compile(r"(\d\d):(\d\d):(\d\d) (.*)")
_BOUGHT = re.compile(r"\[supply\] (\d+) x .* for (\d+(?:\.\d+)?), balance ")
_REFUSED = re.compile(r"\[supply\] .* refused: (\d{3})\b")
_PAID = re.compile(r"\[acp\] paid (\d+(?:\.\d+)?) through ")
# [acp] lines the shop side writes; every other [acp] line is our buyer not paying (market_kit.acp_pay)
_SHOP_ACP = re.compile(r"\[acp\] (settled |settle refused|checkout refused: (?!\d{3}\b))")
_ERROR = re.compile(r"\b\w+(?:Error|Exception):")
_URL = re.compile(r"https?://\S+")


def _kit_lines(path, now, since, truncated):
    """Texts of the kit log lines stamped since `since`, oldest first. A stamp has no date, so the walk goes
    back from the file's mtime and steps back a day when a stamp is later than the line after it (midnight).
    Lines without a stamp are skipped. Missing file: []."""
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            f.seek(max(0, st.st_size - KIT_TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    cut = st.st_size > KIT_TAIL_BYTES
    if cut:
        lines = lines[1:]                               # the first line read may start mid-line
    ref, out, old = min(now, st.st_mtime) + CLOCK_SLACK_S, [], 0
    for line in reversed(lines):
        m = _STAMP.match(line)
        if not m or int(m[1]) > 23 or int(m[2]) > 59 or int(m[3]) > 59:
            continue
        h, mi, s = int(m[1]), int(m[2]), int(m[3])
        day = datetime.fromtimestamp(ref)
        ts = day.replace(hour=h, minute=mi, second=s, microsecond=0).timestamp()
        if ts > ref:
            ts = (day - timedelta(days=1)).replace(hour=h, minute=mi, second=s, microsecond=0).timestamp()
        if ts < since:                                  # one odd stamp must not end the window early
            old += 1
            if old >= OLD_RUN:
                break
            continue
        old = 0
        out.append(m[4])
        ref = ts + CLOCK_SLACK_S
    else:
        # never got past the window's start: the tail was cut, or the kit rotated the log inside the window
        try:
            rotated = any(p.stat().st_mtime >= since for p in Path(path).parent.glob("market-kit.*.log"))
        except OSError:
            rotated = False
        if out and (cut or rotated):
            truncated.append("kit")
    return out[::-1]


def _kit(lines):
    """Counts over the window's kit lines. pay_refused_why: why our buyer did not pay, URLs removed, 80 chars."""
    k = {"lines": len(lines), "starts": 0, "orders": 0, "errors": 0, "tracebacks": 0,
         "supply_bought": 0, "supply_units": 0, "supply_cost": 0,
         "supply_refused": {"429_window": 0, "402_balance": 0, "other": 0},
         "paid": 0, "paid_total": 0, "pay_refused": 0, "pay_refused_why": Counter()}
    for t in lines:
        if "Traceback (most recent call last)" in t:
            k["tracebacks"] += 1
        elif not t.startswith(("[buyer]", "[seller]")) and _ERROR.search(t):    # hooks print other agents' text
            k["errors"] += 1
        if t.startswith("[kit] market_kit "):
            k["starts"] += 1
        elif t.startswith("[order "):
            k["orders"] += 1
        elif m := _BOUGHT.match(t):
            k["supply_bought"] += 1
            k["supply_units"] += int(m.group(1))
            k["supply_cost"] += float(m.group(2))
        elif m := _REFUSED.match(t):
            key = {"429": "429_window", "402": "402_balance"}.get(m.group(1), "other")
            k["supply_refused"][key] += 1
        elif m := _PAID.match(t):
            k["paid"] += 1
            k["paid_total"] += float(m.group(1))
        elif t.startswith("[acp] ") and not t.startswith("[acp] checkout ships unit") and not _SHOP_ACP.match(t):
            k["pay_refused"] += 1
            k["pay_refused_why"][" ".join(_URL.sub("URL", t[6:]).split())[:80]] += 1
    k["supply_cost"], k["paid_total"] = round(k["supply_cost"]), round(k["paid_total"])
    k["pay_refused_why"] = dict(k["pay_refused_why"].most_common(10))
    return k


def _inventory(inv):
    """.market/inventory.json as {"balance", "units": [{UNIT_KEYS}]}; None when missing or not that shape."""
    if not isinstance(inv, dict) or not isinstance(inv.get("units"), list):
        return None
    return {"balance": inv.get("balance"),
            "units": [{k: u.get(k) for k in UNIT_KEYS} for u in inv["units"] if isinstance(u, dict)]}


def read_own(shop_home, now, window_h=1, history_h=0):
    """The shop's summary, its log rows and the kit log's counts of the last window_h hours, and its inventory
    now. Never raises; missing parts are empty (a missing or broken inventory.json: {} and own["inventory_error"]).
    Only a file's tail is read (seller and buyer logs: 2 MB, other logs 256 KB, the kit log 1 MB), so a very
    busy hour is counted from its newest rows; own["truncated"] then names that log, and the brief says its
    counts are too low. own["history"]: the buyer and forum rows of the last history_h hours, as far as
    the tail reaches (for outcome checks; never in the brief)."""
    home, since = Path(shop_home), now - window_h * 3600
    own = {"window_h": window_h, "summary": {}, "decisions": {}, "acp": [],
           "policy_changes": [], "kit": {}, "truncated": [], "inventory": {}, "history": {}}
    inv = _inventory(read_json(home / ".market" / "inventory.json", None))
    if inv is None:
        own["inventory_error"] = "inventory: .market/inventory.json missing or not an inventory"
    else:
        own["inventory"] = inv
    try:
        own["summary"] = _summary(read_json(home / "memory" / "summary.json", {}))
        keep = now - history_h * 3600
        for role in ROLES:
            rows = _window_rows(home / "logs" / "decisions" / f"{role}.jsonl", since, role, own["truncated"],
                                keep if role in HISTORY_ROLES else None)
            own["decisions"][role] = _recent(rows, since)
            if role in HISTORY_ROLES:
                own["history"][role] = rows
        own["acp"] = _window_rows(home / "logs" / "acp-sessions.jsonl", since, "acp", own["truncated"])
        own["policy_changes"] = [r for r in tail_jsonl(home / "logs" / "policy-changes.jsonl", 5) if isinstance(r, dict)]
        own["kit"] = _kit(_kit_lines(home / "logs" / "market-kit.log", now, since, own["truncated"]))
    except Exception as e:                               # a strange file must not stop the run
        own["error"] = _err("own", e)
    return own


# --- market settles in 10-minute buckets (state["ledger_buckets"]), for counts over more than one run

def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def band(list_price):
    """The list price band index (bisect over BANDS), or None without a price."""
    return bisect.bisect(BANDS, list_price) if _number(list_price) else None


def ledger_buckets(lb, ledger, catalog, ours, now):
    """lb (state["ledger_buckets"], or None) with this run's settle rows added: bucket start (str) ->
    {settle, ours, settle.b<i>, rev.b<i>, ours.b<i>, ours_rev.b<i>}, i the list price band. Every ledger
    row is read once (the cursor), so each settle is counted once. ours(data) -> bool. Buckets older than
    BUCKETS_KEEP_S are dropped."""
    lb = {k: v for k, v in (lb.items() if isinstance(lb, dict) else ()) if isinstance(v, dict)
          and k.isdigit() and int(k) + BUCKET_S > now - BUCKETS_KEEP_S}
    catalog = catalog if isinstance(catalog, dict) else {}
    for r in ledger or []:
        if not isinstance(r, dict) or r.get("kind") != "settle" or not isinstance(r.get("data"), dict):
            continue
        ts = r.get("ts")
        if not _number(ts) or ts <= now - BUCKETS_KEEP_S:
            continue
        d = r["data"]
        b = lb.setdefault(str(int(ts // BUCKET_S * BUCKET_S)), {})
        mine = bool(ours(d))
        amount = d["amount"] if _number(d.get("amount")) else 0
        c = catalog.get(d.get("sku") or d.get("item"))
        i = band(c[2]) if isinstance(c, list) and len(c) > 2 else None
        for k in ("settle", "ours") if mine else ("settle",):
            b[k] = b.get(k, 0) + 1
        if i is not None:
            for k, v in (("settle", 1), ("rev", amount)) + ((("ours", 1), ("ours_rev", amount)) if mine else ()):
                b[f"{k}.b{i}"] = b.get(f"{k}.b{i}", 0) + v
    return lb


def bucket_total(lb, t0, t1):
    """Counter of the buckets that lie wholly in [t0, t1)."""
    out = Counter()
    for k, v in (lb or {}).items():
        if t0 <= int(k) and int(k) + BUCKET_S <= t1:
            out.update(v)
    return out


# --- per SKU market tables (state["sku_tables"]): 10-minute buckets for 6 h, hourly buckets for 24 h

TBL_KEEP_S = 6 * 3600
DAY_S = 24 * 3600
HOUR_S = 3600
C_FALLBACK = {"food": 0.51, "goods": 0.61}      # our mean supply cost / list before any supply row of ours


def _add(d, k, v=1):
    d[k] = d.get(k, 0) + v


def _hist_add(h, kind, x):
    _add(h.setdefault(kind, {}), f"{min(max(x, 0.0), 2.0):.2f}")


def sku_tables(t, ledger, catalog, ours, me, cost_of, now):
    """t (state["sku_tables"], or None) with this run's ledger rows folded in. Every ledger row is read once (the
    cursor), so each is counted once. ours(data) -> bool says a settle is ours; our supply and expire rows carry
    our shop name (me). cost_of(unit id) -> the unit's cost or None (then list x our mean cost/list of its kind).
      b: 10-minute bucket start (str) -> {"s": {sku: market settles}, "o": {sku: our settles},
         "pl": {sku: [sum paid/list, n]}, "k": {kind: {paid/list bin: n}}, "u": {sku: market supply units},
         "ou": {sku: our supply units}}, kept TBL_KEEP_S;
      h: hour start (str) -> {"k": {kind: {bin: n}}, "c": {kind: [sum cost/list, units]} (our supply),
         "cogs", "rev" (our settles' cost and amount), "exp" (our expired cost), "o": {sku: our settles}}, kept 24 h;
      since: when the counts began (the oldest row of the first fold); h_since: the same for the hourly buckets."""
    t = t if isinstance(t, dict) else {}
    b = {k: v for k, v in (t.get("b") or {}).items() if isinstance(v, dict) and str(k).isdigit()
         and int(k) + BUCKET_S > now - TBL_KEEP_S}
    h = {k: v for k, v in (t.get("h") or {}).items() if isinstance(v, dict) and str(k).isdigit()
         and int(k) + HOUR_S > now - DAY_S}
    catalog = catalog if isinstance(catalog, dict) else {}
    since = t.get("since") if b and _number(t.get("since")) else None
    h_since = t.get("h_since") if h and _number(t.get("h_since")) else None
    c_kind = table_view({"b": {}, "h": h, "since": since}, now)["c_kind"]
    first, first_h = [], []
    for r in ledger or []:
        if not isinstance(r, dict) or not isinstance(r.get("data"), dict) or not _number(r.get("ts")):
            continue
        ts, d, kind_ = r["ts"], r["data"], r.get("kind")
        if ts <= now - DAY_S or kind_ not in ("settle", "supply", "expire"):
            continue
        hb = h.setdefault(str(int(ts // HOUR_S * HOUR_S)), {})
        first_h.append(ts)
        bb = b.setdefault(str(int(ts // BUCKET_S * BUCKET_S)), {}) if ts > now - TBL_KEEP_S else None
        if bb is not None:
            first.append(ts)
        if kind_ == "expire":
            if d.get("shop") == me and _number(d.get("cost")):
                _add(hb, "exp", d["cost"])
            continue
        sku = d.get("sku") or d.get("item")
        if not isinstance(sku, str) or not SKU_RE.fullmatch(sku):
            continue
        c = catalog.get(sku)
        lp = c[2] if isinstance(c, list) and len(c) > 3 and _number(c[2]) and c[2] > 0 else None
        kind = c[3] if isinstance(c, list) and len(c) > 3 and c[3] in C_FALLBACK else None
        if kind_ == "settle":
            mine = bool(ours(d))
            amount = d["amount"] if _number(d.get("amount")) else None
            if bb is not None:
                _add(bb.setdefault("s", {}), sku)
                if mine:
                    _add(bb.setdefault("o", {}), sku)
            if mine:
                _add(hb.setdefault("o", {}), sku)
                cost = cost_of(str(d.get("unit")))
                if not _number(cost) and lp and kind:
                    cost = lp * (c_kind.get(kind) or C_FALLBACK[kind])
                _add(hb, "cogs", round(cost) if _number(cost) else 0)
                _add(hb, "rev", amount or 0)
            if amount and lp and kind:
                x = amount / lp
                _hist_add(hb.setdefault("k", {}), kind, x)
                if bb is not None:
                    _hist_add(bb.setdefault("k", {}), kind, x)
                    pl = bb.setdefault("pl", {}).setdefault(sku, [0, 0])
                    pl[0], pl[1] = round(pl[0] + x, 4), pl[1] + 1
        else:                                           # supply
            qty = d["qty"] if _number(d.get("qty")) else 0
            if bb is not None:
                _add(bb.setdefault("u", {}), sku, qty)
                if d.get("shop") == me:
                    _add(bb.setdefault("ou", {}), sku, qty)
            if d.get("shop") == me and qty and lp and kind and _number(d.get("total")):
                cs = hb.setdefault("c", {}).setdefault(kind, [0, 0])
                cs[0], cs[1] = round(cs[0] + d["total"] / lp, 4), cs[1] + qty
    if since is None:
        since = min(first) if first else now
    if h_since is None:
        h_since = min(first_h) if first_h else now
    return {"b": b, "h": h, "since": since, "h_since": max(h_since, now - DAY_S)}


def _hist_median(hists):
    """(median, n) over paid/list histograms {bin: n}; (None, 0) when empty."""
    c = Counter()
    for hs in hists:
        c.update({k: v for k, v in (hs or {}).items() if _number(v) and v > 0})
    n = sum(c.values())
    if not n:
        return None, 0
    xs = sorted((float(k), v) for k, v in c.items())

    def at(i):
        for x, v in xs:
            if i < v:
                return x
            i -= v

    return round((at((n - 1) // 2) + at(n // 2)) / 2, 3), n


def table_view(t, now):
    """The tables as numbers: {"covered_h", "h1", "h6" (hours the 1 h and 6 h counts span),
    "sku": {sku: {"s1", "s6", "o6", "u1", "ou1", "pl6" (mean paid/list or None), "pl_n"}},
    "p_clear": {kind: {"h6", "n6", "h24", "n24"}} (median paid/list of market settles), "c_kind": {kind: our mean
    supply cost/list over 24 h or None}, "day": {"cogs", "rev", "exp", "o": {sku: our settles}, "h" (hours the
    24 h sums span)}}."""
    t = t if isinstance(t, dict) else {}
    b, h = t.get("b") or {}, t.get("h") or {}
    since = t.get("since") if _number(t.get("since")) else now
    covered = max(0.0, now - since)
    span = lambda w: round(max(BUCKET_S, min(covered, w)) / 3600, 3)
    rows = {}
    for k, v in b.items():
        recent = int(k) + BUCKET_S > now - HOUR_S
        for f, tf in (("s", "s6"), ("o", "o6"), ("u", None), ("ou", None)):
            for sku, n in (v.get(f) or {}).items():
                r = rows.setdefault(sku, {"s1": 0, "s6": 0, "o6": 0, "u1": 0, "ou1": 0, "pl": [0, 0]})
                if tf:
                    r[tf] += n
                if recent and f in ("s", "u", "ou"):
                    r[{"s": "s1", "u": "u1", "ou": "ou1"}[f]] += n
        for sku, (x, n) in (v.get("pl") or {}).items():
            r = rows.setdefault(sku, {"s1": 0, "s6": 0, "o6": 0, "u1": 0, "ou1": 0, "pl": [0, 0]})
            r["pl"][0] += x
            r["pl"][1] += n
    for r in rows.values():
        x, n = r.pop("pl")
        r["pl6"], r["pl_n"] = (round(x / n, 3) if n else None), n
    p_clear = {}
    for kind in C_FALLBACK:
        m6, n6 = _hist_median([(v.get("k") or {}).get(kind) for v in b.values()])
        m24, n24 = _hist_median([(v.get("k") or {}).get(kind) for v in h.values()])
        p_clear[kind] = {"h6": m6, "n6": n6, "h24": m24, "n24": n24}
    c_kind = {}
    for kind in C_FALLBACK:
        x = sum(((v.get("c") or {}).get(kind) or [0, 0])[0] for v in h.values())
        n = sum(((v.get("c") or {}).get(kind) or [0, 0])[1] for v in h.values())
        c_kind[kind] = round(x / n, 3) if n else None
    o24 = Counter()
    for v in h.values():
        o24.update({k: n for k, n in (v.get("o") or {}).items() if _number(n)})
    h_since = t.get("h_since") if _number(t.get("h_since")) else since
    day = {f: round(sum(v.get(f) or 0 for v in h.values())) for f in ("cogs", "rev", "exp")} | {
        "o": dict(o24), "h": round(min(DAY_S, max(0.0, now - h_since)) / 3600, 2)}
    return {"covered_h": round(covered / 3600, 2), "h1": span(HOUR_S), "h6": span(TBL_KEEP_S),
            "sku": rows, "p_clear": p_clear, "c_kind": c_kind, "day": day}
