"""What the market actually paid: Round 3 `settle` entries of the public ledger, cached 60 s in .market/.

    from market_price import settled, median_price
    median_price("SKU-1")  -> (median, n) over the newest 500 ledger entries, or (None, 0)

Shared by seller.py (open near the market price, not the list price), restock.py (what sells) and
market_buyer.py (anchor the first offer, report prices on the forum).
"""
import json, os, statistics, time, urllib.request
from pathlib import Path

MARKET = "https://market.ourolabs.ai"
CACHE = Path(os.environ.get("MARKET_DIR", ".market")) / "settled_cache.json"


def settled(ttl=60):
    """Settle entries' data dicts (item = SKU, price, shop, buyer...), newest 500, cached for ttl seconds."""
    try:
        if time.time() - CACHE.stat().st_mtime < ttl:
            return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        led = json.load(urllib.request.urlopen(f"{MARKET}/api/ledger?after=-500", timeout=15))
    except Exception:
        return []
    out = [e["data"] for e in led if e.get("kind") == "settle" and isinstance(e.get("data"), dict)]
    try:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(out), encoding="utf-8")
    except OSError:
        pass
    return out


def price_of(d):
    p = d.get("price", d.get("amount"))
    return p if isinstance(p, (int, float)) else None


def median_price(sku):
    ps = [price_of(d) for d in settled() if d.get("item") == sku and price_of(d) is not None]
    return (int(statistics.median(ps)), len(ps)) if ps else (None, 0)
