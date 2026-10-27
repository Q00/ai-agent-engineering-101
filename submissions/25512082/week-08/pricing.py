"""Challenge 1: opening asks derived from the shop's own market evidence.

`update_prices.py` writes `pricing-policy.json`; the seller only reads it.
Reserves never change. Without a policy file the seller keeps the model-chosen opening.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from protocol import RESERVES

POLICY_PATH = Path(__file__).resolve().parent / "pricing-policy.json"


def load_policy(path: Path = POLICY_PATH) -> dict | None:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    asks = policy.get("asks") if isinstance(policy, dict) else None
    if not isinstance(asks, dict):
        return None
    return policy


def opening_ask(item: str, policy: dict | None) -> int | None:
    """The policy ask for `item`, or None when the model should choose; never below the reserve."""
    if policy is None:
        return None
    ask = policy["asks"].get(item)
    if not isinstance(ask, int) or isinstance(ask, bool):
        return None
    return max(ask, RESERVES[item])


def fallback_decision(item: str, ask: int, offer: int, rounds: int) -> dict:
    """Week 08: the rule the seller uses when the model call fails (rate limit, timeout, bad output),
    so the shop keeps trading instead of rejecting. Never below the reserve.

    Accept an offer at or above the current ask, within 5 of it, or at or above the reserve after
    3 buyer offers. Otherwise counter halfway between the ask and the offer, rounded up to 5,
    at least the reserve and at least 5 below the current ask.
    """
    reserve = RESERVES[item]
    if offer >= reserve and (offer >= ask - 5 or rounds >= 3):
        return {"action": "accept", "price": offer}
    halfway = (ask + max(offer, reserve) + 9) // 10 * 5
    counter = max(reserve, min(halfway, ask - 5))
    if counter <= offer:
        return {"action": "accept", "price": offer}
    return {"action": "counter", "price": counter}


def record_trade(row: dict) -> None:
    """Append one finished negotiation to SELLER_TRADE_LOG (unset in tests: nothing is written)."""
    target = os.environ.get("SELLER_TRADE_LOG")
    if not target:
        return
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **row}
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass
