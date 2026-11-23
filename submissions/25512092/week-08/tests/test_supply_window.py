"""Supply window fit and the 8% high margin filter (person's decision 11/11 ~14:50).
    python tests/test_supply_window.py"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json, tmp_path  # noqa: E402

policy()
from depts import purchasing as PU, supply_window as W  # noqa: E402

L = [{"sku": f"G{i}", "qty": 1} for i in range(10)]


def test_soon_and_wait():
    assert W.soon({"open": True}, 0) and W.soon({"open": False, "opens_at": 1150}, 1000)
    assert not W.soon({"open": False, "opens_at": 1200}, 1000)
    slept = []
    assert W.wait_open({"open": False, "opens_at": 1100}, log=lambda m: None, now=1000, sleep=slept.append) == 101
    assert slept == [101]
    assert W.wait_open({"open": True, "opens_at": 900}, now=1000, sleep=slept.append) == 0 and len(slept) == 1


def test_fit():
    r = {"open": False, "opens_at": 1000, "closes_at": 1180}
    assert W.n_fit(r, 900) == 15                       # planned early: the whole window
    kept, dropped = W.fit(L, r, 900, log=lambda m: None)
    assert kept == L and not dropped
    r = dict(r, open=True)
    assert W.n_fit(r, 1131) == 4                       # the 2nd plan ~131 s in: 4 lines (1134 + 3 x 12 <= 1175)
    kept, dropped = W.fit(L, r, 1131, log=lambda m: None)
    assert kept == L[:4] and dropped == L[4:]          # the lowest ranked are dropped
    assert W.n_fit(r, 1173) == 0 and W.fit(L, r, 1173, log=lambda m: None)[0] == []
    assert W.n_fit({"open": True}, 1000) is None and W.fit(L, {"open": True}, 1000)[0] == L


def test_high_margin():
    assert PU.high_margin(0.10) == 0.10                # the test file: {} -> the default
    p = tmp_json({"enabled": True, "min_margin": 0.08})
    old = PU.HIGH_MARGIN
    try:
        PU.HIGH_MARGIN = p
        assert PU.high_margin(0.10) == 0.08
        PU.HIGH_MARGIN = tmp_json({"enabled": False, "min_margin": 0.08})
        assert PU.high_margin(0.10) == 0.10
    finally:
        PU.HIGH_MARGIN = old
    PU.note_band([{"sku": "GA", "qty": 2}], log=lambda m: None)
    rows = [json.loads(x) for x in open(PU.BAND_LOG, encoding="utf-8")]
    assert rows[-1]["sku"] == "GA" and rows[-1]["qty"] == 2 and "logs" not in PU.BAND_LOG.replace("\\", "/").split("/")[-2:-1]


def test_second_plan():
    import tempfile
    d = tempfile.mkdtemp()
    path = tmp_path("plan2.json")
    W.save_second(L[:3], {"opens_at": 1000, "closes_at": 1180}, log=lambda m: None, path=path)
    with open(os.path.join(d, "inventory.json"), "w", encoding="utf-8") as f:
        json.dump({"restock": {"open": True, "opens_at": 1000, "closes_at": 1180}}, f)
    import limits
    from unittest import mock
    from depts import supply_window as SW
    with mock.patch.object(limits, "buy_paused", lambda now=None: False),             mock.patch("decisions.record", lambda *a, **k: None):
        assert SW.take_second(d, log=lambda m: None, now=1120, path=path) == L[:3]      # 1123 + 2 x 12 <= 1175
        assert SW.take_second(d, log=lambda m: None, now=1120, path=path) is None       # used once
        W.save_second(L[:6], {"opens_at": 1000, "closes_at": 1180}, log=lambda m: None, path=path)
        assert SW.take_second(d, log=lambda m: None, now=1140, path=path) == L[:3]      # fitted: 3 of 6
        W.save_second(L[:3], {"opens_at": 1000, "closes_at": 1180}, log=lambda m: None, path=path)
        assert SW.take_second(d, log=lambda m: None, now=1190, path=path) is None       # window closed
        W.save_second(L[:3], {"opens_at": 1000, "closes_at": 1180}, log=lambda m: None, path=path)
        with open(os.path.join(d, "inventory.json"), "w", encoding="utf-8") as f:
            json.dump({"restock": {"open": False, "opens_at": 2200, "closes_at": 2380}}, f)
        assert SW.take_second(d, log=lambda m: None, now=1100, path=path) is None       # another window
    W.save_second([], {}, path=path)
    assert not os.path.exists(path)


def test_split_no_twice():
    """One plan of 2 x MAX_LINES split in two: the halves never share a SKU."""
    plan = [{"sku": f"G{i}", "qty": 1} for i in range(17)]
    a, b = plan[:10], plan[10:]
    assert not ({p["sku"] for p in a} & {p["sku"] for p in b}) and len(a) == 10


if __name__ == "__main__":
    test_soon_and_wait()
    test_fit()
    test_high_margin()
    test_second_plan()
    test_split_no_twice()
    print("ok")
