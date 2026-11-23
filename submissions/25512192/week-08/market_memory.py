"""The shared context layer: what the market has shown us, folded into small stats both agents read.

    python market_memory.py --sync         # pull new ledger entries into state/market_stats.json
    python market_memory.py --summary      # print the summary the strategist and the buyer read

The public ledger is the only place that says what sells (the market publishes no demand weights). Every
settle entry is folded into counters per category, per SKU and per shop; nothing raw is kept, so the file
stays small however long Round 3 runs. Only Round 3 entries count.

Files (all under state/, committed as evidence; atomic writes so readers never see half a file):
  market_stats.json   cursor + per-category / per-SKU / per-shop counters from the ledger
  sales.jsonl         our shop's closed deals (written by seller.py)
"""
import argparse
import json
import os
import statistics
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
STATS = STATE / "market_stats.json"
SALES = STATE / "sales.jsonl"
MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
ROUND = 3
BACKFILL = 5000             # first sync starts this many entries before the newest
MAX_PAGES = 40              # 40 x 500 entries per sync at most, so a hook never runs long
RATIOS_KEPT = 30            # newest paid/list ratios kept per category and per shop
HOUR = 3600


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def empty():
    return {"cursor": None, "synced_at": 0, "settles": 0, "categories": {}, "skus": {}, "shops": {}, "hourly": {}, "refs": {}}


def catalog(state_dir):
    return read_json(Path(state_dir) / "catalog.json", {})


def fold(stats, e, cat):
    """One settle entry into the counters."""
    d = e.get("data") or {}
    sku, amount = d.get("item"), d.get("amount")
    if not sku or not isinstance(amount, (int, float)):
        return
    info = cat.get(sku) or {}
    category = info.get("category") or "?"
    lp = info.get("list_price") or 0
    ratio = round(amount / lp, 3) if lp else None
    hour = str(int(e.get("ts", time.time()) // HOUR))
    stats["settles"] += 1
    refs = stats.setdefault("refs", {})                    # newest ledger seqs per category, for forum citations
    refs[category] = (refs.get(category, []) + [e.get("seq")])[-5:]
    stats["hourly"][hour] = stats["hourly"].get(hour, 0) + 1

    c = stats["categories"].setdefault(category, {"n": 0, "ratios": [], "last": 0, "recent": {}})
    c["n"] += 1
    c["last"] = e.get("ts", 0)
    c["recent"][hour] = c["recent"].get(hour, 0) + 1
    if ratio:
        c["ratios"] = (c["ratios"] + [ratio])[-RATIOS_KEPT:]

    s = stats["skus"].setdefault(sku, {"n": 0, "sum": 0, "category": category})
    s["n"] += 1
    s["sum"] += amount

    shop = d.get("shop") or d.get("seller")
    if shop:
        h = stats["shops"].setdefault(str(shop), {"n": 0, "claims": 0, "honest": 0, "ratios": []})
        h["n"] += 1
        if d.get("claim") in ("A", "B", "C"):
            h["claims"] += 1
            h["honest"] += bool(d.get("claim_honest"))
        if ratio:
            h["ratios"] = (h["ratios"] + [ratio])[-RATIOS_KEPT:]


def prune(stats, now):
    """Keep only the last 24 hourly buckets, so 'recent' means recent."""
    keep = {str(int(now // HOUR) - i) for i in range(24)}
    stats["hourly"] = {k: v for k, v in stats["hourly"].items() if k in keep}
    for c in stats["categories"].values():
        c["recent"] = {k: v for k, v in c["recent"].items() if k in keep}


def sync(state_dir=HERE / ".market", log=print):
    """Pull ledger entries after the cursor. Returns the stats. Never raises: a market outage keeps the old stats."""
    stats = read_json(STATS, None) or empty()
    cat = catalog(state_dir)
    after = stats["cursor"] if stats["cursor"] is not None else -BACKFILL
    pages, new = 0, 0
    try:
        with httpx.Client(timeout=20) as c:
            while pages < MAX_PAGES:
                r = c.get(f"{MARKET}/api/ledger", params={"after": after})
                r.raise_for_status()
                entries = r.json()
                pages += 1
                for e in entries:
                    after = e["seq"]
                    if e.get("kind") == "settle" and e.get("round", (e.get("data") or {}).get("round")) == ROUND:
                        fold(stats, e, cat)
                        new += 1
                if len(entries) < 500:
                    break
    except (httpx.HTTPError, ValueError, KeyError) as ex:
        log(f"[memory] ledger sync stopped: {type(ex).__name__}: {ex}")
    stats["cursor"] = after
    stats["synced_at"] = time.time()
    prune(stats, time.time())
    write_json(STATS, stats)
    log(f"[memory] ledger synced to #{after}: {new} new settles, {stats['settles']} in total")
    return stats


def category_rank(stats, hours=6):
    """Categories by settled deals in the last `hours`, then by all-time count."""
    now = int(time.time() // HOUR)
    keep = {str(now - i) for i in range(hours)}
    def recent(c):
        return sum(v for k, v in c["recent"].items() if k in keep)
    return sorted(stats["categories"].items(), key=lambda kv: (recent(kv[1]), kv[1]["n"]), reverse=True)


def median_ratio(ratios, default=None):
    return statistics.median(ratios) if ratios else default


def honesty(stats, shop):
    """Share of a shop's grade claims that the ledger proved true, or None with no claims seen."""
    h = stats["shops"].get(shop) or {}
    return h["honest"] / h["claims"] if h.get("claims") else None


def our_sales(since=0):
    out = []
    try:
        for line in SALES.read_text(encoding="utf-8").splitlines():
            s = json.loads(line)
            if s.get("ts", 0) >= since:
                out.append(s)
    except (OSError, ValueError):
        pass
    return out


def record_sale(sale):
    STATE.mkdir(exist_ok=True)
    with open(SALES, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(sale, ts=time.time()), ensure_ascii=False) + "\n")


def summary(stats, top=12):
    """A short text the strategist and the buyer's harness read: what sells and at what share of list price."""
    lines = [f"Round 3 ledger: {stats['settles']} settled deals seen; last 24 h by hour: "
             + ", ".join(str(v) for _, v in sorted(stats["hourly"].items())[-24:])]
    for name, c in category_rank(stats)[:top]:
        r = median_ratio(c["ratios"])
        lines.append(f"- {name}: {c['n']} deals, {sum(c['recent'].values())} in 24 h, "
                     f"median paid/list {r:.2f}" if r else f"- {name}: {c['n']} deals")
    shops = sorted(stats["shops"].items(), key=lambda kv: kv[1]["n"], reverse=True)[:8]
    if shops:
        lines.append("Top shops: " + "; ".join(
            f"{k} {v['n']} deals, paid/list {median_ratio(v['ratios'], 0):.2f}, honest claims "
            f"{v['honest']}/{v['claims']}" for k, v in shops))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")    # Korean names on a cp949 console
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--state", default=str(HERE / ".market"))
    a = ap.parse_args()
    st = sync(a.state) if a.sync else (read_json(STATS, None) or empty())
    if a.summary or not a.sync:
        print(summary(st))
