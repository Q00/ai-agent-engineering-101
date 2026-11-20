"""Retain official ALL scores; diagnostic weights never replace official scores."""

from pathlib import Path
import math
from market_state import read, write


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def entry(snapshot):
    c = snapshot.get("competition", {})
    own = c.get("own", {})
    return {"market_at": snapshot.get("observed_at", 0), "player_total": (c.get("official_player") or {}).get("total"),
            "shop_all": (own.get("shops") or {}).get("all"), "buyer_all": (own.get("buyers") or {}).get("all")}


def history(md, snapshot, now):
    path = Path(md) / "official-score-history.json"
    cache = read(path)
    if cache is None:
        cache = []
        for source in sorted((Path(md).parent / "logs/market-intel").glob("*.json"))[-8:]:
            data = read(source)
            if data.get("round") == 3:
                cache.append(entry(data))
    cache.append(entry(snapshot))
    result = {r["market_at"]: r for r in cache if finite(r.get("player_total")) and 0 <= now-r["market_at"] <= 86400}
    rows = sorted(result.values(), key=lambda r: r["market_at"])[-60:]
    if rows != read(path, []):
        write(path, rows)
    return rows
