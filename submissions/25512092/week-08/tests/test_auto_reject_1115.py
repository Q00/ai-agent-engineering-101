"""Analyst proposals rejected in advance (person 11/15 ~14:20): logistics.aging_after_h until the end of 11/17 is
recorded as auto_rejected and never sent; another key is sent as before; the queued path skips it too; one Telegram
summary a day. Through analyst_brain.analyse / release_queued / auto_reject.summary_due with a temp db and rules.
    python tests/test_auto_reject_1115.py"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_path  # noqa: E402

policy()
from depts import analyst, analyst_brain as AB, auto_reject as AR  # noqa: E402

DB = tmp_path(f"analyst_ar_{time.time_ns()}.db")
AR.RULES, AR.LOG, AR.STATE = (tmp_path(f"ar_{time.time_ns()}.json"), tmp_path(f"ar_{time.time_ns()}.jsonl"),
                             tmp_path(f"ar_state_{time.time_ns()}.json"))
json.dump({"keys": {"logistics.aging_after_h": {"until": time.time() + 3600, "why": "x"}}, "summary_h": 9},
          open(AR.RULES, "w"))
SENT = []


def connect():
    return sqlite3.connect(DB)


def run_analyse():
    other = "logistics.aging_cut"                       # allowed 0.05..0.2, current 0.1
    llm = json.dumps({"proposals": [
        {"key": "logistics.aging_after_h", "value": 12, "reason": "aging", "evidence": "e"},
        {"key": other, "value": 0.15, "reason": "other", "evidence": "e"}]})
    with mock.patch.object(analyst, "connect", connect), mock.patch.object(AB, "call_llm", lambda *a: (llm, {})), \
            mock.patch.object(analyst, "send_proposal", lambda kind, k, v, r, ev="": SENT.append(k) or 1), \
            mock.patch.object(AB, "QUIET", (0, 0)), mock.patch("decisions.record", lambda *a, **k: None):
        out = AB.analyse(snap={"by_key": {}}, log_=lambda *a: None, send=True)
    return other, out


def test_analyse():
    other, out = run_analyse()
    assert ("logistics.aging_after_h", 12, "auto rejected") in out, out
    assert SENT == [other], SENT
    d = connect()
    st = d.execute("SELECT status FROM proposals WHERE key='logistics.aging_after_h'").fetchall()
    assert st == [("auto_rejected",)], st
    rows = [json.loads(x) for x in open(AR.LOG, encoding="utf-8")]
    assert rows[-1]["key"] == "logistics.aging_after_h" and rows[-1]["value"] == 12, rows
    # queued path: an aging_after_h queued before the rule is skipped, not sent
    SENT.clear()
    d.execute("INSERT INTO proposals (ts, kind, key, value, reason, evidence, status, expires) VALUES "
              "(?, 'policy', 'logistics.aging_after_h', '12', 'q', 'e', 'queued', ?)", (time.time(), time.time() + 99))
    d.commit()
    with mock.patch.object(analyst, "connect", connect), mock.patch.object(AB, "QUIET", (0, 0)), \
            mock.patch.object(analyst, "send_proposal", lambda kind, k, v, r, ev="": SENT.append(k) or 1):
        AB.release_queued(log_=lambda *a: None)
    assert SENT == [] and len(open(AR.LOG, encoding="utf-8").readlines()) == 2, SENT
    # expired rule: proposals go out again
    json.dump({"keys": {"logistics.aging_after_h": {"until": time.time() - 1}}}, open(AR.RULES, "w"))
    assert AR.rule("logistics.aging_after_h") is None
    json.dump({"keys": {"logistics.aging_after_h": {"until": time.time() + 3600}}, "summary_h": 9}, open(AR.RULES, "w"))


def test_summary():
    sent = []
    send = lambda lv, k, t: sent.append((lv, k, t))
    t9 = max(time.time() + 5, datetime.now().replace(hour=10, minute=0).timestamp())
    t8 = datetime.now().replace(hour=8, minute=0).timestamp()
    if os.path.exists(AR.STATE):
        os.remove(AR.STATE)
    assert AR.summary_due(t8, send, log=lambda *a: None) is False          # before 09:00
    assert AR.summary_due(t9, send, log=lambda *a: None) is True
    assert AR.summary_due(t9 + 600, send, log=lambda *a: None) is False    # once a day
    assert sent[0][0] == "daily" and "logistics.aging_after_h -> 12" in sent[0][2] and "자동 거절" in sent[0][2], sent


if __name__ == "__main__":
    test_analyse()
    test_summary()
    print("ok")
