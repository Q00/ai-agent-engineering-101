"""tools/subst_watch.py: metrics and stop rules on synthetic experiment logs and ledger results."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, HERE)
import subst_watch as W  # noqa: E402


class L:
    def __init__(self, res):
        self.res = res


def run(n_a, paid_a, stars_a, stars_n, mism=0):
    lines, res = [], {}
    for i in range(n_a):
        t = f"a{i}"
        lines += [{"ts": 10, "task": t, "arm": "A", "event": e} for e in ("offer", "number_reply", "receipt")]
        res[t] = {"ts": 10, "stars": stars_a, "review": "receipt does not match" if i < mism else "ok",
                  "settled": i < paid_a, "price": 1000}
    for i in range(30):
        res[f"n{i}"] = {"ts": 10, "stars": stars_n, "review": "", "settled": True, "price": 500}
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    f.write("\n".join(json.dumps(x) for x in lines))
    f.close()
    W.EXP = f.name
    m = W.metrics(L(res), 0)
    return m, W.stop_reason(m)


def test_ok():
    m, why = run(25, 20, 4.6, 4.6)
    assert why is None and m["A"]["paid"] == 20 and m["A"]["revenue"] == 20000 and m["A"]["number_replies"] == 25


def test_low_paid():
    assert "결제율" in run(25, 5, 4.6, 4.6)[1]


def test_stars():
    assert "별점" in run(25, 20, 4.2, 4.6)[1]


def test_stars_paid_only():
    # 11:58 case: unpaid A deals with low stars do not count; 14 paid A results < 20 -> no star judgement yet
    m, why = run(21, 14, 4.2, 4.6)
    assert why is None and m["A"]["stars_paid_n"] == 14
    lines, res = [], {}
    import tempfile as _t
    for i in range(30):                       # 30 A receipts: 22 paid at 4.7 stars, 8 unpaid at 1 star
        t = f"a{i}"
        lines += [{"ts": 10, "task": t, "arm": "A", "event": e} for e in ("offer", "receipt")]
        res[t] = {"ts": 10, "stars": 4.7 if i < 22 else 1, "review": "", "settled": i < 22, "price": 1000}
    for i in range(30):
        res[f"n{i}"] = {"ts": 10, "stars": 4.5, "review": "", "settled": True, "price": 500}
    f = _t.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    f.write("\n".join(json.dumps(x) for x in lines))
    f.close()
    W.EXP = f.name
    m = W.metrics(L(res), 0)
    assert m["A"]["stars_all"] < 4.0 and W.stop_reason(m) is None    # all-stars would stop, paid-only does not


def test_mismatch():
    assert "불일치" in run(25, 20, 4.6, 4.6, mism=5)[1]


def test_too_few():
    assert run(10, 0, 1, 5)[1] is None


def test_report_text():
    m, _ = run(25, 20, 4.6, 4.6)
    t = W.text(m, 0, 3600)
    assert "숫자 응답 25" in t and "결제 20" in t
