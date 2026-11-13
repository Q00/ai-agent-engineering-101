"""Strategy 2 -- argue from an objective standard.

A bare counteroffer is a number the buyer can only push against. The market
publishes one it can check: for each item, the average price of the signed
deals in the current round (the dashboard's price/item). Citing it gives the
buyer a reason, and an LLM buyer reads the shop's words between offers --
unlike when it picks a shop, where only names and reviews reach it.

The harness is told the same number, so its asks and the shop's words agree.
The figure is fetched at most every ten minutes; if the market cannot be
reached the shop negotiates without it. Only true numbers are quoted.
"""
import time

STATS = "https://market.ourolabs.ai/api/stats"
TTL = 600
_cache = {"t": 0.0, "items": {}}


def items_from(stats: dict) -> dict:
    """The current round's per-item figures out of /api/stats."""
    return stats["rounds"][str(stats["round"])]["items"]


def mean_of(items: dict, item_id: str) -> int | None:
    m = items.get(item_id, {}).get("mean")
    return round(m) if isinstance(m, (int, float)) else None


def mean(item_id: str) -> int | None:
    """The market's average signed price for this item this round, or None.
    The PC's fetch; the Worker fetches with its own client and uses mean_of."""
    if time.time() - _cache["t"] > TTL:
        _cache["t"] = time.time()                       # one try per TTL, even when it fails
        try:
            import httpx                                # only the PC needs it
            _cache["items"] = items_from(httpx.get(STATS, timeout=3).json())
        except Exception:                               # noqa: BLE001  (offline -> no anchor)
            pass
    return mean_of(_cache["items"], item_id)


def sentence(name: str, m: int | None) -> str:
    """What the buyer is told. Said before the ask, never after it: buyers read
    the last number as the ask, so the anchor must not be the last number."""
    if m is None:
        return ""
    return (f"Signed deals for a {name} average {m} across the market this round, "
            f"and every receipt we issue is signed too. ")


def for_harness(m: int | None) -> str:
    """What the harness is told: the same figure, and that the buyer has it."""
    if m is None:
        return ""
    return f"Signed deals for this item average {m} across the market this round, and the buyer has been told so.\n"
