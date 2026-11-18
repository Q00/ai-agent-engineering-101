"""depts/analyst_brain.py: logistics.near_expiry_h is proposed only when a day's perishable waste at cost > 200,000."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from depts import analyst_brain as B  # noqa: E402
from _isolate import policy  # noqa: E402

# a temp policy.json; high_share / high_max set so allowed_keys() does not fall back to the live market.db
policy({"purchasing": {"high_share": 0.4, "high_max": 30}})


def rep(waste, age=0):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"ts": time.time() - age, "metrics": {"perishable_24h": {"waste_cost": waste}}}, f)
    f.close()
    return f.name


def test_gate():
    assert B.waste_24h(rep(43_839)) == 43_839
    assert not B.gate_open("logistics.near_expiry_h", 43_839)
    assert not B.gate_open("logistics.near_expiry_h", 200_000)
    assert B.gate_open("logistics.near_expiry_h", 200_001)
    assert not B.gate_open("logistics.near_expiry_h", None)
    assert B.gate_open("purchasing.spend_share", None)


def test_stale_or_missing():
    assert B.waste_24h(rep(500_000, age=4 * 3600)) is None
    assert B.waste_24h(os.path.join(HERE, "no_such.json")) is None


def test_validate_drops_gated():
    orig = B.waste_24h
    try:
        B.waste_24h = lambda *a, **k: 43_839
        assert "logistics.near_expiry_h" not in B.allowed_keys()
        good, dropped = B.validate('{"proposals":[{"key":"logistics.near_expiry_h","value":24}]}')
        assert not good and "not allowed" in dropped[0]
        B.waste_24h = lambda *a, **k: 250_000
        assert "logistics.near_expiry_h" in B.allowed_keys()
    finally:
        B.waste_24h = orig


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
