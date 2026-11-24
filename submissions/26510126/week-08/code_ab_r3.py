"""A code change tried on half of the buyer's orders, so its verdict compares the same hours (the outer loop's A/B).

The outer loop judged a code change by the hour after it against the hour before. On 11/4 that measured the clock:
2f23dbf's rule never fired once (another rule caught the same talks first), yet its metric read -10%, the evening's
traffic, not the change. Now the coder puts the new behaviour behind

    if code_ab_r3.on("<key>", order["order"]):

and while the change is on trial (code_ab_r3.json names its key), on() is True for the orders whose hash with the key
is even and False for the rest. The verdict (meta_code_r3.check) splits the hour's orders the same way, by recomputing
the hash, so nothing extra is logged. Once judged, the trial ends: a kept change runs on every order (on() is True for
a key that is not on trial), a reverted one leaves the code.
"""
import hashlib
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRIAL = Path(os.environ.get("CODE_AB") or HERE / "code_ab_r3.json")


def arm(key, order_id):
    """'B' (the new code) or 'A' (as before) for this order, the same every time."""
    return "B" if int(hashlib.sha1(f"{key}|{order_id}".encode()).hexdigest(), 16) % 2 == 0 else "A"


def trial():
    try:
        t = json.loads(TRIAL.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return t if isinstance(t, dict) and t.get("key") else None


def on(key, order_id):
    """Run the new code for this order? Half the orders while `key` is on trial, every order otherwise."""
    t = trial()
    if not t or t["key"] != key or not order_id:
        return True
    return arm(key, order_id) == "B"


def start(key):
    tmp = TRIAL.with_suffix(".tmp")
    tmp.write_text(json.dumps({"key": key}), encoding="utf-8")
    os.replace(tmp, TRIAL)


def stop():
    tmp = TRIAL.with_suffix(".tmp")
    tmp.write_text("{}", encoding="utf-8")
    os.replace(tmp, TRIAL)
