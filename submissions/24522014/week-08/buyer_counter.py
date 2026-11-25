"""One bounded rule counteroffer at the cap for a clear over-budget ask. Off unless config says otherwise.

It never pays above the cap, never calls a model and uses our market catalog for list price, not the seller's figure.
Every other check, including the kit's before-payment check, still applies to whatever the seller answers.
"""

from fractions import Fraction
import json
import math
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "buyer_support.json"
DISABLED = {"enabled": False, "max_ask_per_cap": None, "min_surplus_per_list": None}
REASON = "Clear ask slightly over the coded cap; one rule counteroffer at the cap, no model used."


def _plain_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _valid(config):
    return (isinstance(config, dict) and config.get("enabled") is True
            and _plain_number(config.get("max_ask_per_cap")) and 1 < config["max_ask_per_cap"] <= 2
            and _plain_number(config.get("min_surplus_per_list")) and 0 <= config["min_surplus_per_list"] < 1)


def load_settings(path=CONFIG):
    """Return the validated settings; anything missing, unreadable or malformed means disabled."""
    try:
        section = json.loads(Path(path).read_text(encoding="utf-8")).get("over_budget_counter")
    except (OSError, ValueError, AttributeError):
        return dict(DISABLED)
    if _valid(section):
        return {key: section[key] for key in ("enabled", "max_ask_per_cap", "min_surplus_per_list")}
    return dict(DISABLED)


REPEAT_DISABLED = {"enabled": False, "max_ask_per_cap": None, "max_rule_counters": None, "min_surplus_per_list": None}
REPEAT_REASON = "Ask over the coded cap; the same rule counteroffer at the cap again, no model used."


def _valid_repeat(config):
    count = config.get("max_rule_counters") if isinstance(config, dict) else None
    return _valid(config) and type(count) is int and 1 <= count <= 3


def load_repeat_settings(path=CONFIG):
    """rule_counter_repeat: the same counter at the cap for asks up to max_ask_per_cap, at most max_rule_counters per conversation.
    Its own section, so over_budget_counter (the 1.18 first counter) stays as it is. Anything missing or malformed means off."""
    try:
        section = json.loads(Path(path).read_text(encoding="utf-8")).get("rule_counter_repeat")
    except (OSError, ValueError, AttributeError):
        return dict(REPEAT_DISABLED)
    if _valid_repeat(section):
        return {key: section[key] for key in REPEAT_DISABLED}
    return dict(REPEAT_DISABLED)


def evaluate_repeat(ask, cap, list_price, *, counters_so_far, config):
    """(decision or None, check, exhausted). The same checks as the first counter except 'already used' and 'repeated price':
    repeating the cap is the point. exhausted is True when the ask would qualify but max_rule_counters is reached."""
    if not _valid_repeat(config):
        return None, None, False
    decision, check = evaluate(ask, cap, list_price, config=config)
    check["rule_counters_before"] = counters_so_far
    if decision is None:
        return None, check, False
    if counters_so_far >= config["max_rule_counters"]:
        check.update(reason_code="counter_repeat_exhausted", offer=None)
        return None, check, True
    return {"action": "offer", "price": cap, "reason": REPEAT_REASON}, check, False


def _exact(value):
    # str() keeps the configured 1.18 instead of its binary approximation, so boundaries are exact.
    return Fraction(str(value))


def evaluate(ask, cap, list_price, *, used=False, last_offer=None, config=None):
    """Return (decision or None, check). check is safe to log: numbers and a reason code only."""
    usable_list = list_price if _plain_number(list_price) and list_price > 0 else None
    check = {"reason_code": None, "ask": ask, "cap": cap, "list_price": usable_list, "offer": None,
             "ask_per_cap": None, "surplus_per_list": None}
    if (type(ask) is not int or type(cap) is not int or cap <= 0 or ask <= cap):
        check["reason_code"] = "counter_not_over_budget"
        return None, check
    check["ask_per_cap"] = round(ask / cap, 4)
    if usable_list is not None:
        check["surplus_per_list"] = round((usable_list - cap) / usable_list, 4)
    if not _valid(config):
        check["reason_code"] = "counter_disabled"
    elif used:
        check["reason_code"] = "counter_already_used"
    elif last_offer == cap:
        check["reason_code"] = "counter_repeated_price"
    elif Fraction(ask, cap) > _exact(config["max_ask_per_cap"]):
        check["reason_code"] = "counter_ratio_exceeded"
    elif usable_list is None:
        check["reason_code"] = "counter_no_list_price"
    elif (Fraction(usable_list) - cap) / Fraction(usable_list) < _exact(config["min_surplus_per_list"]):
        check["reason_code"] = "counter_surplus_too_low"
    else:
        check.update(reason_code="rule_counter_offer", offer=cap)
        return {"action": "offer", "price": cap, "reason": REASON}, check
    return None, check
