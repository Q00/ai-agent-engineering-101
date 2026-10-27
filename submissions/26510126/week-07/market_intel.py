"""What the whole market knows, from its public ledger: going prices and each shop's record.

Used by market_buyer.py (which shops are expensive or dishonest) and meta_shop.py (where our
own prices stand). Everything comes from https://market.ourolabs.ai/api/ledger, a hash-chained
public record of every order and result, cached in .market/ledger.json and fetched incrementally.
Any network failure returns what the cache has (possibly nothing): callers must work without it.

    python market_intel.py            # print the market summary
"""
import json
import os
import statistics
import sys
import time

import httpx

MARKET = "https://market.ourolabs.ai"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".market", "ledger.json")
PAGE = 500
# checks the market makes on every result; failing one of these is the shop's doing.
# (signatures are left out: in Rounds 0 and 1 almost nobody signs yet)
SHOP_CHECKS = ("item_matches", "price_within_budget", "task_not_seen")


def load_ledger(timeout=8.0, max_pages=40):
    """All ledger entries, oldest first, refreshed from the market when it answers."""
    try:
        with open(CACHE) as f:
            entries = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        entries = []
    after = entries[-1]["seq"] if entries else 0
    try:
        with httpx.Client(timeout=timeout) as client:
            for _ in range(max_pages):
                page = client.get(f"{MARKET}/api/ledger", params={"after": after} if after else None).json()
                page = [e for e in page if e["seq"] > after]
                if not page:
                    break
                entries += page
                after = page[-1]["seq"]
                if len(page) < PAGE:
                    break
    except (httpx.HTTPError, ValueError, KeyError) as e:
        print(f"[intel] ledger fetch failed ({e!r}), using {len(entries)} cached entries", file=sys.stderr)
    if entries and entries[0]["seq"] == 1 and any(e["kind"] == "round" for e in entries[:1]):
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + f".{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(entries, f)
        os.replace(tmp, CACHE)
    return entries


RECENT = os.path.join(HERE, ".market", "ledger-recent.json")
RECENT_HOURS = 6  # the buyer looks back at most three hours (going prices) and a shop's last ten sales


def load_recent(hours=RECENT_HOURS, timeout=8.0, max_pages=40):
    """Join and round entries plus the last `hours` of the ledger, from a small cache of its own.

    The full cache passed 400 MB in Round 2 and the buyer parsed it on every order (2 GB, one core, 10-30 s),
    which with the meta-harness and the watcher on the same 16 GB Mac pushed the kit into swap until it stalled.
    The first call builds the small cache from the full one; later calls read only the small file."""
    try:
        with open(RECENT) as f:
            entries = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        entries = load_ledger(timeout=timeout, max_pages=max_pages)
    after = max((e["seq"] for e in entries), default=0)
    try:
        with httpx.Client(timeout=timeout) as client:
            for _ in range(max_pages):
                page = client.get(f"{MARKET}/api/ledger", params={"after": after} if after else None).json()
                page = [e for e in page if e["seq"] > after]
                if not page:
                    break
                entries += page
                after = page[-1]["seq"]
                if len(page) < PAGE:
                    break
    except (httpx.HTTPError, ValueError, KeyError) as e:
        print(f"[intel] ledger fetch failed ({e!r}), using {len(entries)} recent entries", file=sys.stderr)
    cut = time.time() - hours * 3600
    entries = [e for e in entries if e["kind"] in ("join", "round") or e["ts"] >= cut]
    if entries:
        os.makedirs(os.path.dirname(RECENT), exist_ok=True)
        tmp = RECENT + f".{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(entries, f)
        os.replace(tmp, RECENT)
    return entries


def summarize(entries, since=None):
    """Going prices per item, and per shop name: deals, prices per item, failed checks.

    `since` (unix time) keeps only results after it, so a shop's old prices fade out."""
    names = {}                       # shop url -> name, from join entries (urls change on restart)
    for e in entries:
        if e["kind"] == "join" and e["data"].get("role") == "shop" and e["data"].get("url"):
            names[e["data"]["url"].rstrip("/")] = e["data"]["name"]
    items, shops = {}, {}
    for e in entries:
        if e["kind"] != "result" or (since and e["ts"] < since):
            continue
        d = e["data"]
        url = (d.get("shop") or "").rstrip("/")
        name = names.get(url) or url.rsplit("/shop/", 1)[-1] or url
        s = shops.setdefault(name, {"results": 0, "deals": 0, "signed": 0, "sig_history": [], "prices": {}, "failed": {}, "last": 0})
        s["results"] += 1
        s["last"] = max(s["last"], e["ts"])
        r = d.get("receipt")
        checks = d.get("checks") or {}
        for c in SHOP_CHECKS:
            if checks.get(c) is False:
                s["failed"][c] = s["failed"].get(c, 0) + 1
        if isinstance(r, dict) and r.get("item") and isinstance(r.get("price"), (int, float)):
            s["deals"] += 1
            s["signed"] += checks.get("sig_seller_ok") is True
            s["sig_history"].append(checks.get("sig_seller_ok") is True)  # ledger order: oldest first
            s["prices"].setdefault(r["item"], []).append(r["price"])
            if checks.get("item_matches") is not False and checks.get("price_within_budget") is not False:
                items.setdefault(r["item"], []).append(r["price"])
    going = {}
    for item, prices in items.items():
        q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [min(prices), statistics.median(prices), max(prices)]
        going[item] = {"n": len(prices), "median": statistics.median(prices), "p25": q[0], "p75": q[-1],
                       "min": min(prices), "max": max(prices)}
    return going, shops


def current_round(entries):
    rounds = [e["data"].get("round", e.get("round")) for e in entries if e["kind"] == "round"]
    return rounds[-1] if rounds else 0


SIG_LAST = 5  # a shop's signing is judged on its last five deals, so one that just began signing counts


def unsigned(name, shops, recent=None):
    """True when most of a shop's last SIG_LAST receipts fail the seller-signature check (they do not count in Round 2)."""
    s = shops.get(name)
    last = (s or {}).get("sig_history", [])[-SIG_LAST:]
    return len(last) >= 3 and sum(last) < len(last) / 2


def shop_note(name, item, going, shops):
    """One line on a shop from the market's record, for the buyer's pick prompt. (note, dishonest)"""
    s = shops.get(name)
    if not s:
        return "no market record", False
    parts = [f"{s['deals']} deals in the market, {s['signed']} with a verified shop signature"]
    prices = s["prices"].get(item)
    g = going.get(item)
    if prices and g:
        med = statistics.median(prices)
        parts.append(f"its {item} sold at median {med:g} vs market median {g['median']:g}"
                     + (" (EXPENSIVE)" if med > g["p75"] else " (cheap)" if med < g["p25"] else ""))
    failed = sum(s["failed"].values())
    dishonest = failed >= 2 and failed >= 0.2 * s["results"]
    if failed:
        parts.append(f"market checks failed {failed} times ({', '.join(f'{k} {v}' for k, v in s['failed'].items())})"
                     + (" DISHONEST" if dishonest else ""))
    return "; ".join(parts), dishonest


if __name__ == "__main__":
    started = time.time()
    entries = load_ledger()
    going, shops = summarize(entries)
    print(f"{len(entries)} ledger entries in {time.time() - started:.1f} s")
    for item, g in sorted(going.items()):
        print(f"  {item:10} n={g['n']:4} median {g['median']:g}  p25 {g['p25']:g}  p75 {g['p75']:g}  range {g['min']:g}-{g['max']:g}")
    for name, s in sorted(shops.items(), key=lambda kv: -kv[1]["deals"])[:40]:
        med = {i: statistics.median(p) for i, p in s["prices"].items()}
        print(f"  {name[:28]:28} deals {s['deals']:4}/{s['results']:4}  medians {med}  failed {s['failed'] or '-'}")
