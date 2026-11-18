"""Trial C on again by itself (person 11/14 ~16:10): after the guard (12 h+ unsold high cost > 60M) turned it off, the
12 h+ unsold high cost under 45M for 6 h in a row -> off_ts cleared (C on), high_max back to 120, one Telegram.
Through purchasing.make_plan (what restock.main calls every minute), temp rules / state, live files untouched.
    python tests/test_wide_reon_1114.py"""
import json
import os
import sys
import time
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_deep_free_1113 as T  # noqa: E402  (its make_plan harness; isolates policy)
from _isolate import tmp_json, tmp_path  # noqa: E402
from depts import wide_trial as W  # noqa: E402

NOW = time.time()
OFF = round(NOW - 20 * 3600)
RULES = {"enabled": True, "since": 1, "applied": ["C"], "max_cost": 60_000_000, "high_max_back": 100,
         "re_on_cost": 45_000_000, "re_on_h": 6, "re_on_high_max": 120, "off_ts": OFF, "off_why": "x"}


def aged(n):
    return [{"unit": f"a{i}", "sku": f"A{i}", "cost": 1_000_000, "grade": "A", "supplied_at": NOW - 13 * 3600}
            for i in range(n)]


class Shim:
    """plan_with patches alerts.send with a no-op; this hands it the recorder instead."""
    def __init__(self, send):
        def patch(target, *a, **k):
            return mock.patch(target, send) if target == "alerts.send" else mock.patch(target, *a, **k)
        patch.object = mock.patch.object
        self.patch = patch


def run(n_aged, state):
    rp, sp = tmp_json(dict(RULES), "wt"), tmp_path(f"reon-{time.time_ns()}.json")
    if state is not None:
        json.dump(dict(state, off_ts=OFF), open(sp, "w"))
    sent, back = [], []
    orig_rules = W.rules
    with mock.patch.object(W, "REON", sp), mock.patch.object(W, "high_max_back", lambda n, why: back.append(n)), \
            mock.patch.object(W, "rules", lambda path=None: orig_rules(path or rp)), \
            mock.patch.object(W, "save", (lambda s: lambda r, path=None: s(r, path or rp))(W.save)), \
            mock.patch.object(T, "mock", Shim(lambda lv, k, text, **kw: sent.append((lv, text)))):
        T.W.RULES = rp                       # plan_with resets W.RULES; rules() above reads rp anyway
        _, logs = T.plan_with(True, aged(n_aged))
        r = orig_rules(rp)
    return r, json.load(open(sp)), sent, back, logs


def test_reon():
    # under 45M (40 x 1M) for 6 h+, checks without a gap -> on, high_max 120, one Telegram, the off kept in "offs"
    r, st, sent, back, logs = run(40, {"below_since": NOW - 6 * 3600 - 60, "last": NOW - 60})
    assert not r.get("off_ts") and W.on("C", r), r
    assert r["offs"][-1]["off_ts"] == OFF and back == [120], (r, back)
    assert len(sent) == 1 and "다시 켬" in sent[0][1] and "high_max 120" in sent[0][1], sent
    assert any("[wide trial] on again" in m for m in logs), logs
    # under but only 5 h -> still off, the clock kept
    r, st, sent, back, _ = run(40, {"below_since": round(NOW - 5 * 3600), "last": NOW - 60})
    assert r.get("off_ts") == OFF and not sent and not back and st["below_since"] == round(NOW - 5 * 3600), (r, st)
    # first check under the line -> the clock starts now
    r, st, sent, _, _ = run(40, None)
    assert r.get("off_ts") == OFF and abs(st["below_since"] - NOW) < 120 and not sent, st
    # at or over 45M (50 x 1M) -> clock reset, off
    r, st, sent, _, _ = run(50, {"below_since": NOW - 7 * 3600, "last": NOW - 60})
    assert r.get("off_ts") == OFF and st["below_since"] is None and not sent, st
    # checks stopped for over 30 min -> the clock restarts
    r, st, sent, _, _ = run(40, {"below_since": NOW - 7 * 3600, "last": NOW - 3600})
    assert r.get("off_ts") == OFF and abs(st["below_since"] - NOW) < 120 and not sent, st


def test_not_off():
    # trial on (no off_ts): nothing to do; without re_on_cost: never on again
    rp = tmp_json({k: v for k, v in RULES.items() if k not in ("off_ts", "off_why")}, "wt2")
    assert W.re_on(aged(10), path=rp, state_path=tmp_path("s2.json")) == "n/a"
    rp = tmp_json({k: v for k, v in RULES.items() if k != "re_on_cost"}, "wt3")
    assert W.re_on(aged(10), path=rp, state_path=tmp_path("s3.json")) == "n/a"


if __name__ == "__main__":
    test_reon()
    test_not_off()
    print("ok")
