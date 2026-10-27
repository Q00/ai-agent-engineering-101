"""Append timestamped Agent Market leaderboard snapshots without secrets."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
MIN_REVIEWS = 10
MIN_DEALS = 10


def _number(value: Any, default: float = 0) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _paid(value: dict[str, Any]) -> float:
    paid = value.get("paid")
    return float(paid) if isinstance(paid, (int, float)) else 9.0


SHOP_KEYS: dict[str, Callable[[dict[str, Any]], tuple[Any, ...]]] = {
    "profit": lambda x: (-_number(x.get("profit")), -_number(x.get("deals"))),
    "deals": lambda x: (-_number(x.get("deals")), -_number(x.get("stars"))),
    "revenue": lambda x: (-_number(x.get("revenue")), -_number(x.get("deals"))),
    "stars": lambda x: (
        -int(_number(x.get("reviews")) >= MIN_REVIEWS),
        -_number(x.get("stars")),
        -_number(x.get("reviews")),
    ),
}

BUYER_KEYS: dict[str, Callable[[dict[str, Any]], tuple[Any, ...]]] = {
    "saved": lambda x: (-_number(x.get("saved")), -_number(x.get("deals"))),
    "deals": lambda x: (-_number(x.get("deals")), _paid(x)),
    "paid": lambda x: (
        -int(_number(x.get("deals")) >= MIN_DEALS),
        _paid(x),
        -_number(x.get("deals")),
    ),
}


def _fetch(market: str) -> dict[str, Any]:
    request = urllib.request.Request(
        market.rstrip("/") + "/api/board",
        headers={"User-Agent": "week07-market-monitor/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("market board did not return a JSON object")
    return value


def _matches(agent: dict[str, Any], name: str, owner: str) -> bool:
    owner_value = agent.get("owner")
    login = owner_value.get("login", "") if isinstance(owner_value, dict) else ""
    return str(agent.get("name", "")).casefold() == name.casefold() or str(login).casefold() == owner.casefold()


def _rankings(
    agents: list[dict[str, Any]],
    selected: dict[str, Any],
    keys: dict[str, Callable[[dict[str, Any]], tuple[Any, ...]]],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for label, key in keys.items():
        ordered = sorted(agents, key=key)
        result[label] = next(index for index, value in enumerate(ordered, 1) if value is selected)
    return result


def snapshot(market: str, name: str, owner: str) -> dict[str, Any]:
    board = _fetch(market)
    shops = [x for x in board.get("shops", []) if isinstance(x, dict)]
    buyers = [x for x in board.get("buyers", []) if isinstance(x, dict)]
    shop = next((x for x in shops if _matches(x, name, owner)), None)
    buyer = next((x for x in buyers if _matches(x, name, owner)), None)
    return {
        "measured_at_kst": datetime.now(KST).isoformat(timespec="seconds"),
        "round": board.get("round"),
        "totals": board.get("totals", {}),
        "shop_count": len(shops),
        "buyer_count": len(buyers),
        "shop": shop,
        "shop_ranks": _rankings(shops, shop, SHOP_KEYS) if shop else None,
        "buyer": buyer,
        "buyer_ranks": _rankings(buyers, buyer, BUYER_KEYS) if buyer else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="https://market.ourolabs.ai")
    parser.add_argument("--name", default="sohyunkim11")
    parser.add_argument("--owner", default="sohyunkim11")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--interval", type=int, default=0, help="seconds; zero records once")
    args = parser.parse_args()
    if args.interval < 0:
        parser.error("--interval must be zero or positive")

    while True:
        value = snapshot(args.market, args.name, args.owner)
        line = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        print(line, flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        if args.interval == 0:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
