"""Summary item 26 (person 11/15): the high_share 0.7 / high_max 200 pin warning only until the pin's end, 11/13 12:00.
Through morning_summary.needs_items (what night_lines' 26 prints), with the live pins / policy read only.
    python tests/test_morning_needs_1115.py"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import morning_summary as M  # noqa: E402


def test_pin_warning_gone():
    after = datetime(2026, 11, 15, 9).timestamp()
    assert not [x for x in M.needs_items(after - 86400, after) if "고정 11/13 12:00" in x]
    before = datetime(2026, 11, 13, 10).timestamp()
    out = M.needs_items(before - 86400, before)
    assert all("고정 11/13 12:00" not in x or x.startswith(("high_share", "high_max")) for x in out), out


if __name__ == "__main__":
    test_pin_warning_gone()
    print("ok")
