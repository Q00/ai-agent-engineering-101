"""Overnight freeze (person's decision 11/09 ~23:55): until night_freeze.json "until" (11/10 09:00) no automatic rule
makes a new policy change: the CEO's live rules (depts/ceo_policy.tick) and the QC midnight buying split (depts/qc.py)
record what they would have changed and keep the values. Brakes, safeguards (the CEO guard, saved guard, shop order
guard, cash pause) and scheduled checks run as before; analyst proposals still go out and expire unanswered.
"""
import json
import os
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "night_freeze.json")


def until():
    try:
        with open(PATH, encoding="utf-8") as f:
            return float(json.load(f).get("until") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def frozen(now=None):
    return (now or time.time()) < until()


def until_text():
    u = until()
    return f"{datetime.fromtimestamp(u):%m-%d %H:%M}" if u else "-"
