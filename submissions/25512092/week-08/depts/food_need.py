"""Food shelf life the buyer needs (person's decision 11/12 ~16:40, against "Only 7.6 h of shelf life left; I needed
18.0 h" 2-star reviews, 17 a day). Switched by food_need.json (read on every call; on with the 18:00 rollout):
  read   the hours the buyer needs from its message: "at least N hours of shelf life", "needs at least N hours of
         shelf life left", "I need it to last N hours" (the largest number found)
  choose seller_r3.find_unit keeps only units with at least need + MARGIN_H (0.5 h, for the negotiation) of shelf
         life left (the one that expires first among them, as before); none -> "none in stock" (no sale)
  check  right before the receipt the chosen unit is checked again; short -> another unit of the same SKU and grade
         that fits, else no receipt (the negotiation ends without a deal)
Orders that state no hours, and goods (no expiry), are unchanged.
"""
import json
import os
import re
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "food_need.json")
MARGIN_H = 0.5
PATTERNS = (re.compile(r"at least (\d+(?:\.\d+)?) ?h(?:ours?)? of shelf", re.I),
            re.compile(r"to last (\d+(?:\.\d+)?) ?h(?:ours?)?", re.I))


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def on(r=None):
    return bool((rules() if r is None else r).get("enabled"))


def need_hours(text):
    """The largest hours the message asks for, or None."""
    vals = [float(m) for p in PATTERNS for m in p.findall(text or "")]
    return max(vals) if vals else None


def left_h(u, now=None):
    return None if not u.get("expires_at") else (u["expires_at"] - (now or time.time())) / 3600


def fits(u, need, now=None, r=None):
    """True when the unit may be sold to a buyer needing `need` hours (rule off, no need, or no expiry: True)."""
    r = rules() if r is None else r
    if not on(r) or not need:
        return True
    left = left_h(u, now)
    return left is None or left >= need + float(r.get("margin_h", MARGIN_H))


def keep(units, text, now=None):
    """The units of `units` that fit what `text` asks for."""
    r = rules()
    need = need_hours(text) if on(r) else None
    return units if not need else [u for u in units if fits(u, need, now, r)]
