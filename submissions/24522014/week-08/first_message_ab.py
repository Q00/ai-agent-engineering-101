"""Food-order first message A/B test: half of the food orders (by order hash) state the order's accepted grades,
needed shelf life and certificate in the first message. Off unless buyer_support.json says otherwise.

The control arm sends today's message unchanged. The conditions arm states facts about our order only; it asks
nothing of the seller beyond the market certificate we already ask for on goods. Every check after the first
message, including the kit's before-payment check, stays as it is.
"""

import hashlib
import json
import math
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "buyer_support.json"
DISABLED = {"enabled": False, "share": None, "salt": None}
GRADES = ("A", "B", "C")


def _valid(section):
    return (isinstance(section, dict) and section.get("enabled") is True
            and type(section.get("share")) is float and math.isfinite(section["share"]) and 0 < section["share"] < 1
            and type(section.get("salt")) is str and 0 < len(section["salt"]) <= 64)


FOOD_SECTION = "first_message_conditions"
GOODS_SECTION = "goods_first_message_grades"


def _load(path, name):
    try:
        section = json.loads(Path(path).read_text(encoding="utf-8")).get(name)
    except (OSError, ValueError, AttributeError):
        return dict(DISABLED)
    return {key: section[key] for key in DISABLED} if _valid(section) else dict(DISABLED)


def load(path=CONFIG):
    """Food test: {"enabled", "share", "salt"}; anything missing, unreadable or malformed means off."""
    return _load(path, FOOD_SECTION)


def load_goods(path=CONFIG):
    """Goods test (grade sentence), its own section and salt; anything missing, unreadable or malformed means off."""
    return _load(path, GOODS_SECTION)


def arm(order_id, settings):
    """"conditions" or "control" for an order when the test is on, else None. The same order always gets the same arm,
    so a retry at another shop stays in its arm."""
    if not (isinstance(settings, dict) and settings.get("enabled") is True) or type(order_id) is not str or not order_id:
        return None
    bucket = int(hashlib.sha256(f"{settings['salt']}:{order_id}".encode("utf-8")).hexdigest(), 16) % 10000
    return "conditions" if bucket < round(settings["share"] * 10000) else "control"


def _hours(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}".rstrip("0").rstrip(".")


def _grades(value):
    grades = [g for g in GRADES if isinstance(value, list) and g in value]
    if not grades:
        return None
    return grades[0] if len(grades) == 1 else ", ".join(grades[:-1]) + " or " + grades[-1]


def text(item, accept):
    """The conditions arm's first message. A part whose value the order lacks is left out."""
    accept = accept if isinstance(accept, dict) else {}
    grades, hours = _grades(accept.get("grades")), _hours(accept.get("min_hours_left"))
    parts = [f"I want to buy a {item}."]
    if grades and hours:
        parts.append(f"This order accepts grade {grades} and needs at least {hours} hours of shelf life left.")
    elif grades:
        parts.append(f"This order accepts grade {grades}.")
    elif hours:
        parts.append(f"This order needs at least {hours} hours of shelf life left.")
    if accept.get("cert") is True:
        parts.append("Please include a market certificate.")
    return " ".join(parts)


def goods_grade_sentence(accept):
    """The goods conditions arm's one added sentence, placed after "I want to buy {item}. "; None without grades."""
    grades = _grades(accept.get("grades")) if isinstance(accept, dict) else None
    return f"This order accepts grade {grades}. " if grades else None
