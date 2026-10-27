"""Market price scan for the shop: what counted deals are closing at right now, and who is surging.

When to scan (decided by market_watch.py, which polls every 10 minutes):
  - on a schedule: every --scan-hours (default 1 h), and
  - at once when a competitor's share of recent counted deals jumps: +SURGE_POINTS (5 points) or
    doubling to at least SURGE_MIN_SHARE (8%), with at least SURGE_MIN_DEALS deals in the window.
Data: /api/trades (the latest ~400 results, a few minutes of market) filtered to counted deals.
Output: .market/market-prices.json, read by pricing.py; every scan is appended to
logs/challenge-1/market-scans.jsonl with the reason it ran.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import median
from typing import Any

SURGE_POINTS = 0.05
SURGE_MIN_SHARE = 0.08
SURGE_MIN_DEALS = 5
PRICES = Path(".market/market-prices.json")
SCAN_STATE = Path(".market/market-scan-state.json")
SCAN_LOG = Path("logs/challenge-1/market-scans.jsonl")


def _q(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))]


def counted(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in trades if t.get("counted") and t.get("item") and isinstance(t.get("price"), (int, float))]


def shares(trades: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Share of counted deals per shop in this window."""
    deals = counted(trades)
    total = len(deals) or 1
    out: dict[str, dict[str, float]] = {}
    for t in deals:
        rec = out.setdefault(str(t["shop"]), {"deals": 0, "share": 0.0})
        rec["deals"] += 1
    for rec in out.values():
        rec["share"] = round(rec["deals"] / total, 4)
    return out


def detect_surge(prev: dict[str, dict[str, float]], cur: dict[str, dict[str, float]],
                 self_name: str) -> list[dict[str, Any]]:
    """Competitors whose share of recent counted deals jumped since the previous poll."""
    surges = []
    for shop, now in cur.items():
        if shop == self_name or now["deals"] < SURGE_MIN_DEALS:
            continue
        before = prev.get(shop, {"share": 0.0})["share"]
        jump = now["share"] - before
        if jump >= SURGE_POINTS or (now["share"] >= SURGE_MIN_SHARE and now["share"] >= 2 * max(before, 1e-9)):
            surges.append({"shop": shop, "share_before": before, "share_now": now["share"],
                           "deals": int(now["deals"])})
    return sorted(surges, key=lambda s: -(s["share_now"] - s["share_before"]))


def summarize(trades: list[dict[str, Any]], leaders: list[str] | None = None) -> dict[str, Any]:
    """Per item: n, min, p25, median, p75, mean of counted deals; per leader shop: its median per item."""
    deals = counted(trades)
    items: dict[str, Any] = {}
    for item in sorted({t["item"] for t in deals}):
        prices = [float(t["price"]) for t in deals if t["item"] == item]
        items[item] = {"n": len(prices), "min": min(prices), "p25": _q(prices, 0.25),
                       "median": median(prices), "p75": _q(prices, 0.75), "mean": round(sum(prices) / len(prices), 1)}
    leader_prices: dict[str, dict[str, float]] = {}
    for shop in leaders or []:
        mine = [t for t in deals if t["shop"] == shop]
        leader_prices[shop] = {item: median([float(t["price"]) for t in mine if t["item"] == item])
                               for item in {t["item"] for t in mine}}
    return {"items": items, "leaders": leader_prices, "window_deals": len(deals)}


SHOP_PRICES = Path(".market/shop-prices.json")


def shop_prices(trades: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float]]]:
    """Per shop, per item: how many counted deals and at what median/min price (for the buyer)."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for t in counted(trades):
        out.setdefault(str(t["shop"]), {}).setdefault(t["item"], []).append(float(t["price"]))  # type: ignore[arg-type]
    return {shop: {item: {"n": len(p), "median": median(p), "min": min(p)} for item, p in items.items()}
            for shop, items in out.items()}


def write_shop_prices(trades: list[dict[str, Any]], path: Path = SHOP_PRICES, now: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": round(time.time() if now is None else now), "shops": shop_prices(trades)},
                               ensure_ascii=False), encoding="utf-8")


def should_scan(state: dict[str, Any], now: float, scan_hours: float,
                surges: list[dict[str, Any]]) -> str | None:
    if surges:
        return "share surge: " + ", ".join(
            f"{s['shop']} {s['share_before']:.0%}->{s['share_now']:.0%}" for s in surges)
    if now - float(state.get("last_scan", 0)) >= scan_hours * 3600:
        return f"scheduled ({scan_hours:g} h)"
    return None


def step(trades: list[dict[str, Any]], self_name: str, scan_hours: float, now: float | None = None,
         state_path: Path = SCAN_STATE, prices_path: Path = PRICES, log_path: Path = SCAN_LOG) -> dict | None:
    """One poll: update shares, scan if due or if someone surged. Returns the scan written, or None."""
    now = time.time() if now is None else now
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    write_shop_prices(trades, prices_path.parent / SHOP_PRICES.name, now)
    cur = shares(trades)
    surges = detect_surge(state.get("shares", {}), cur, self_name) if state.get("shares") else []
    reason = should_scan(state, now, scan_hours, surges)
    state["shares"] = cur
    scan = None
    if reason:
        scan = {"ts": round(now), "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "reason": reason, "surges": surges,
                **summarize(trades, [s["shop"] for s in surges])}
        for path in (prices_path, log_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        prices_path.write_text(json.dumps(scan, ensure_ascii=False, indent=1), encoding="utf-8")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(scan, ensure_ascii=False) + "\n")
        state["last_scan"] = now
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return scan
