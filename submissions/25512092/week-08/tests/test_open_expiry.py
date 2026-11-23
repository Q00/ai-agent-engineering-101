"""Quiet negotiations expire after 10 min (11/09 ~11:30); B grade high-price warranty: TV / 냉방 on, 침대/매트리스
50% trial by unit id inside the window, others none; sofas and A / C grades untouched.
    python tests/test_open_expiry.py"""
import json
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from depts import b_warranty as bw, open_expiry as oe, sales, sofa  # noqa: E402
from _isolate import policy, tmp_json, tmp_path  # noqa: E402

policy()                                          # a temp policy.json (code defaults)
sofa.RULES = tmp_json({"enabled": False}, "sofa_rules")   # not the live sofa_rules.json (is_sofa is mocked anyway)

NOW = 1_800_000_000


def test_stale():
    # a temp open_expiry.json (the live one is 300 s since 11/09 18:07); 600 s here
    with mock.patch.object(oe, "RULES", tmp_json({"expire_s": 600}, "open_expiry")):
        op = {"a": {"unit": {"unit": "u1", "sku": "S1"}, "t": NOW - 601}, "b": {"unit": {"unit": "u2", "sku": "S2"}, "t": NOW - 599},
              "c": {"subst": {"X": (1, 10)}, "x": "S3"}}
        assert oe.stale(op, NOW) == ["a"]
        assert op["c"]["t"] == NOW                      # first seen: now
        assert oe.stale(op, NOW + 601) == ["a", "b", "c"]
        oe.touch(op["b"], NOW + 601)
        assert oe.stale(op, NOW + 601) == ["a", "c"]


def test_expire_s():
    """open_expiry.json "expire_s" is read on every call; no file or a bad one: EXPIRE_S (600)."""
    with mock.patch.object(oe, "RULES", tmp_json({"expire_s": 300}, "open_expiry")):
        assert oe.expire_s() == 300
        op = {"a": {"unit": {"unit": "u1"}, "t": NOW - 301}, "b": {"unit": {"unit": "u2"}, "t": NOW - 299}}
        assert oe.stale(op, NOW) == ["a"]
    with mock.patch.object(oe, "RULES", tmp_path("missing.json")):
        assert oe.expire_s() == oe.EXPIRE_S == 600
    bad = tmp_path("bad.json")
    open(bad, "w").write("not json")
    with mock.patch.object(oe, "RULES", bad):
        assert oe.expire_s() == 600


def test_state_and_log():
    d = tempfile.mkdtemp()
    with mock.patch.object(oe, "STATE", os.path.join(d, "s.json")), mock.patch.object(oe, "EXPIRED", os.path.join(d, "e.jsonl")):
        op = {"a": {"unit": {"unit": "u1", "sku": "S1"}, "t": NOW}, "c": {"subst": {}, "x": "S3", "t": NOW}}
        oe.write_state(op, NOW, force=True)
        s = json.load(open(oe.STATE))
        assert s["open"] == 2 and s["units"] == ["u1"]
        oe.log_expired("a", op["a"], NOW + 700)
        e = json.loads(open(oe.EXPIRED).read())
        assert e["unit"] == "u1" and e["quiet_s"] == 700 and e["subst"] is False


R = {"enabled": True, "on_cats": ["TV", "냉방/환기/제습"], "trial_cats": ["침대/매트리스"], "trial_share": 0.5,
     "trial_since": NOW - 10, "trial_until": NOW + 86400}


def u(cat, grade="B", cost=300_000, unit="u1", sku="S1"):
    return {"unit": unit, "sku": sku, "category": "가전>" + cat, "grade": grade, "cost": cost, "kind": "goods"}


def test_b_warranty():
    with mock.patch.object(bw, "rules", return_value=R), mock.patch.object(sofa, "is_sofa", return_value=False):
        assert bw.arm(u("TV"), now=NOW) == "on" and bw.hours(u("TV"), "normal", 48, NOW) == 48
        assert bw.arm(u("냉방/환기/제습"), now=NOW) == "on"
        assert bw.arm(u("냉장/냉동고"), now=NOW) is None and bw.arm(u("컴퓨터"), now=NOW) is None
        assert bw.arm(u("TV", grade="A"), now=NOW) is None and bw.arm(u("TV", grade="C"), now=NOW) is None
        assert bw.arm(u("TV", cost=90_000), now=NOW) is None                  # not high-price
        assert bw.arm(u("TV"), case="cash_low", now=NOW) is None
        arms = [bw.arm(u("침대/매트리스", unit=f"u-{i}"), now=NOW) for i in range(400)]
        assert set(arms) == {"trial_on", "trial_off"} and 160 < arms.count("trial_on") < 240
        assert all(bw.arm(u("침대/매트리스", unit=f"u-{i}"), now=NOW + 5) == arms[i] for i in range(50))   # stable
        assert bw.arm(u("침대/매트리스"), now=NOW + 86401) is None                  # trial over
        # sales.warranty_hours: B TV 48, B trial off 0, B fridge 0 (A rule unchanged)
        off = next(f"u-{i}" for i in range(400) if arms[i] == "trial_off")
        on = next(f"u-{i}" for i in range(400) if arms[i] == "trial_on")
        with mock.patch("time.time", return_value=NOW):
            assert sales.warranty_hours(u("TV"), "normal", {}) == 48
            assert sales.warranty_hours(u("침대/매트리스", unit=on), "normal", {}) == 48
            assert sales.warranty_hours(u("침대/매트리스", unit=off), "normal", {}) == 0
            assert sales.warranty_hours(u("냉장/냉동고"), "normal", {}) == 0
    with mock.patch.object(bw, "rules", return_value=R), mock.patch.object(sofa, "is_sofa", return_value=True):
        assert bw.arm(u("TV"), now=NOW) is None                                # sofas keep their own rule
    with mock.patch.object(bw, "rules", return_value=dict(R, enabled=False)):
        assert bw.arm(u("TV"), now=NOW) is None


if __name__ == "__main__":
    test_stale()
    test_expire_s()
    test_state_and_log()
    test_b_warranty()
    print("ok")
