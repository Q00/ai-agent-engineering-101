"""Challenge 1: the shop reads its own trades and moves its asking prices.

Reserves never move. Only the opening markup over the reserve changes:
  - a buyer takes the opening ask without haggling   -> demand is strong, markup +STEP_UP
  - a buyer leaves after seeing the ask, never offers -> the ask scared them off, markup -STEP_DOWN
  - a buyer leaves whose best offer was below reserve -> their budget was below our floor, no change
State lives in .market/seller-pricing.json; every change is appended to logs/challenge-1/pricing.jsonl
so the before/after share and average price can be shown from the logs.

Launch phase (market-share push, e.g. at the start of Round 2):
  `python pricing.py --start-launch 20 --ramp 30` writes .market/launch.json. From then on the
  first 20 deals (all items together) are priced at the reserve itself (zero margin, never below),
  and over the next 30 deals the cap rises linearly back to the learned markup above.
  The running shop picks the file up at its next opening; no restart needed.
  `python pricing.py --stop-launch` ends it.

Market cap (ask basis A): market_watch.py scans the prices counted deals close at (hourly, and at once
when a competitor's share surges) into .market/market-prices.json. The opening ask never sits above the
market's upper quartile (p75) for the item (the last measured one when the scan is stale; reserve x
FALLBACK_MARKUP if never measured), never above reserve x ASK_CAP, and never below the reserve. This is
the "within the market's range" promise on the Agent Card.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

START_MARKUP = 1.15
MIN_MARKUP = 1.05
MAX_MARKUP = 1.30
STEP_UP = 0.02
STEP_DOWN = 0.04
MARKET_MAX_AGE = 3 * 3600
FALLBACK_MARKUP = 1.08      # ask cap for an item whose market median was never measured
ASK_CAP = 1.10              # the opening ask never exceeds reserve x 1.10 (same as the Worker shop)


class AdaptivePricer:
    def __init__(
        self,
        reserves: dict[str, int],
        state_path: Path = Path(".market/seller-pricing.json"),
        event_path: Path = Path("logs/challenge-1/pricing.jsonl"),
        launch_path: Path = Path(".market/launch.json"),
        market_path: Path = Path(".market/market-prices.json"),
    ) -> None:
        self.reserves = dict(reserves)
        self.state_path = state_path
        self.event_path = event_path
        self.launch_path = launch_path
        self.market_path = market_path
        self._market: tuple[float, dict[str, Any]] = (-1.0, {})
        self.state: dict[str, dict[str, Any]] = self._load()

    # ------------------------------------------------------------------ market cap
    def market_cap(self, item: str) -> int:
        """Ceiling that keeps the card's promise "our opening ask stays within the market's range"
        (ask basis A, 10/22): the upper quartile (p75) of counted deal prices from the latest scan, else
        the last p75 measured for the item, else reserve x FALLBACK_MARKUP. Never below the reserve.
        A surging shop no longer pulls the cap down to its median; a surge only triggers a rescan."""
        reserve = self.reserves[item]
        try:
            mtime = self.market_path.stat().st_mtime
            if mtime != self._market[0]:
                self._market = (mtime, json.loads(self.market_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            self._market = (-1.0, {})
        stats = (self._market[1].get("items") or {}).get(item)
        if not stats:
            return max(reserve, math.ceil(reserve * FALLBACK_MARKUP - 1e-9))
        ref = float(stats.get("p75", stats["median"]))      # scans written before p75 existed
        return max(reserve, math.floor(ref))

    # ------------------------------------------------------------------ launch phase
    def _launch(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.launch_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) and value.get("active") else None
        except (OSError, ValueError):
            return None

    def launch_cap(self) -> float | None:
        """Markup cap during a launch: 1.0 (reserve) for `flat` deals, then a linear ramp."""
        launch = self._launch()
        if launch is None:
            return None
        done = int(launch.get("deals", 0))
        flat, ramp = int(launch.get("flat", 20)), max(1, int(launch.get("ramp", 30)))
        if done < flat:
            return 1.0
        if done < flat + ramp:
            return 1.0 + (MAX_MARKUP - 1.0) * (done - flat) / ramp
        return None

    def _count_launch_deal(self) -> None:
        launch = self._launch()
        if launch is None:
            return
        launch["deals"] = int(launch.get("deals", 0)) + 1
        if launch["deals"] >= int(launch.get("flat", 20)) + int(launch.get("ramp", 30)):
            launch["active"] = False
            launch["ended"] = round(time.time())
        try:
            self.launch_path.write_text(json.dumps(launch, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------ state
    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError):
            pass
        return {}

    def _item(self, item: str) -> dict[str, Any]:
        return self.state.setdefault(
            item,
            {"markup": START_MARKUP, "visits": 0, "deals": 0, "revenue": 0,
             "took_ask": 0, "left_at_ask": 0, "below_floor": 0},
        )

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _event(self, item: str, event: str, **values: Any) -> None:
        s = self._item(item)
        record = {"ts": round(time.time(), 1), "item": item, "event": event,
                  "markup": round(s["markup"], 3), "ask": self.ask(item),
                  "visits": s["visits"], "deals": s["deals"],
                  "avg_price": round(s["revenue"] / s["deals"], 1) if s["deals"] else None, **values}
        print(json.dumps({"event": "pricing", **record}, ensure_ascii=False), file=sys.stderr, flush=True)
        try:
            self.event_path.parent.mkdir(parents=True, exist_ok=True)
            with self.event_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------ API
    def ask(self, item: str) -> int:
        reserve = self.reserves.get(item)
        if reserve is None:
            raise KeyError(item)
        markup = min(MAX_MARKUP, max(MIN_MARKUP, float(self._item(item)["markup"])))
        cap = self.launch_cap()
        if cap is not None:
            markup = min(markup, cap)
        ask = math.ceil(reserve * min(markup, ASK_CAP) - 1e-9)
        if cap is None:                                    # launch already prices at the reserve
            ask = min(ask, self.market_cap(item))
        return max(reserve, ask)                           # never below reserve, whatever the cap

    def opened(self, item: str) -> None:
        self._item(item)["visits"] += 1
        self._save()

    def deal(self, item: str, price: int, ask: int, rounds: int) -> None:
        launch = self.launch_cap() is not None
        s = self._item(item)
        s["deals"] += 1
        s["revenue"] += int(price)
        old = s["markup"]
        if rounds == 0 and price >= ask and not launch:    # launch asks say nothing about demand
            s["took_ask"] += 1
            s["markup"] = min(MAX_MARKUP, old + STEP_UP)
        self._save()
        if launch:
            self._count_launch_deal()
        self._event(item, "deal", price=price, rounds=rounds, launch=launch,
                    markup_change=round(s["markup"] - old, 3))

    def lost(self, item: str, offers: list[int]) -> None:
        s = self._item(item)
        old = s["markup"]
        reserve = self.reserves[item]
        if not offers and self.launch_cap() is not None:
            why = "left at a launch ask (reserve); no change"
        elif not offers:
            s["left_at_ask"] += 1
            s["markup"] = max(MIN_MARKUP, old - STEP_DOWN)
            why = "left after the opening ask"
        elif max(offers) < reserve:
            s["below_floor"] += 1
            why = "best offer below reserve"
        else:
            why = "left mid-negotiation"
        self._save()
        self._event(item, "lost", offers=offers, why=why,
                    markup_change=round(s["markup"] - old, 3))


def main() -> None:
    ap = argparse.ArgumentParser(description="Start or stop the shop's launch (market-share) phase.")
    ap.add_argument("--start-launch", type=int, metavar="FLAT", help="deals sold at the reserve itself")
    ap.add_argument("--ramp", type=int, default=30, help="deals over which the margin returns")
    ap.add_argument("--stop-launch", action="store_true")
    ap.add_argument("--file", type=Path, default=Path(".market/launch.json"))
    a = ap.parse_args()
    if a.stop_launch:
        value = {"active": False, "ended": round(time.time())}
    elif a.start_launch is not None:
        value = {"active": True, "flat": a.start_launch, "ramp": a.ramp, "deals": 0, "started": round(time.time())}
    else:
        print(a.file.read_text(encoding="utf-8") if a.file.exists() else "no launch file")
        return
    a.file.parent.mkdir(parents=True, exist_ok=True)
    a.file.write_text(json.dumps(value, indent=1), encoding="utf-8")
    print(json.dumps(value))


if __name__ == "__main__":
    main()
