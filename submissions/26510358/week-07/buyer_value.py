"""Bounded, confirmed personal price history for model context only.

No prices are parsed from reviews, advertisements or Card descriptions. This
module does not choose an offer, fair price, margin, ceiling or acceptance rule.
Past payment and contract evidence alone cannot establish comparable quality.
"""
from collections.abc import Mapping
from datetime import datetime, timezone
import math


def _whole_number(value, *, positive=False):
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value) or int(value) != value or value < (1 if positive else 0):
            return None
    except (ValueError, OverflowError):
        return None
    return int(value)


def _epoch(value):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("A timezone-aware datetime is required")
        number = value.timestamp()
    elif type(value) in (int, float):
        number = value
    else:
        raise ValueError("now must be an epoch number or timezone-aware datetime")
    try:
        if not math.isfinite(number):
            raise ValueError("now must be finite")
    except OverflowError as exc:
        raise ValueError("now must be finite") from exc
    return number


def _observed_at(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.timestamp(), parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def value_context(shop, item, *, now=None, max_age=86400):
    """Return at most five recent confirmed observations of the same item.

    ``shop.current_round`` is trusted market context supplied by the caller. In
    Round 3 or later, legacy Round 2 confirmations cannot enter this context:
    each row also needs round >= 3 and settled is True. Unknown current round
    stays explicit and is not presented as proof of Round 3 settlement.

    ``recent.time`` is the local history observation time, not settlement latency
    or the time of a current quote. ``now`` accepts UTC epoch seconds or an aware
    datetime. Missing dates, future dates, booleans, nonwhole/nonpositive prices
    and unconfirmed records are excluded. No budgets or peer-provided prose leave
    personal memory through this summary.
    """
    if type(max_age) not in (int, float):
        raise ValueError("max_age must be a finite nonnegative number")
    try:
        valid_age = math.isfinite(max_age) and max_age >= 0
    except OverflowError:
        valid_age = False
    if not valid_age:
        raise ValueError("max_age must be a finite nonnegative number")
    current_time = _epoch(datetime.now(timezone.utc) if now is None else now)
    shop = shop if isinstance(shop, Mapping) else {}
    current_round = _whole_number(shop.get("current_round"))
    memory = shop.get("personal_memory")
    memory = memory if isinstance(memory, Mapping) else {}
    recent = memory.get("recent")
    recent = recent if isinstance(recent, list) else []
    observations = []
    for row in recent:
        if not isinstance(row, Mapping) or row.get("item") != item:
            continue
        if row.get("verified") is not True or row.get("status") != "completed":
            continue
        price, observed = _whole_number(row.get("price"), positive=True), _observed_at(row.get("time"))
        if price is None or observed is None or not 0 <= current_time - observed[0] <= max_age:
            continue
        round_number = _whole_number(row.get("round"))
        settled = row.get("settled") is True and round_number is not None and round_number >= 3
        if current_round is not None and current_round >= 3 and not settled:
            continue
        task = row.get("task")
        if not isinstance(task, str) or not task.strip() or len(task) > 256:
            task = None
        observations.append((observed[0], {
            "price": price, "observed_at": observed[1], "task": task,
            "round": round_number,
            "confirmation": "round3_settlement" if settled else "market_verified_result",
        }))
    observations.sort(key=lambda observation: observation[0], reverse=True)
    selected = [observation[1] for observation in observations[:5]]
    return {
        "source": "personal_confirmed_history",
        "source_field": "personal_memory.recent",
        "item": item,
        "current_round": current_round,
        "historical_not_current": True,
        "quality_comparison_verified": False,
        "quality_comparability": "unknown",
        "observations": selected,
        "observation_count": len(selected),
        "evidence_limits": [
            "Historical observations are not current offers.",
            "Matching quality, availability and other purchase conditions have not been verified.",
            "These observations do not define a fair price, offer, acceptance threshold or budget ceiling.",
        ],
    }
