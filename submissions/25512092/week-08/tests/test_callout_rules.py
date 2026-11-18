"""Stricter callout rules (person 11/12 ~14:40), through callout.tick (what analyst_main runs every 5 min) and
callout.approve (the Telegram 승인 handler):
  1. only market-judged evidence: an item-mismatch draft (our buyer's judgement) is refused in send
  2. >= 3 claimed settles and >= 50% untrue in 24 h; food only with >= 3 food settles >= 80% untrue
  3. approve re-reads every listed ledger number; a mismatch is not queued; one callout per shop per 72 h
  4. a correction excludes the shop for 7 days (tick and approve skip it)
Live files untouched: temp market.db, rules, forum_once.json; Telegram and decisions faked.
    python tests/test_callout_rules.py"""
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from depts import analyst, callout as C  # noqa: E402
import decisions  # noqa: E402
import market_db  # noqa: E402

NOW = time.time()
CAT = {"G1": {"kind": "goods"}, "G2": {"kind": "goods"}, "F1": {"kind": "food"}}


def setup():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "m.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE settles (seq INTEGER PRIMARY KEY, ts REAL, buyer TEXT, shop_url TEXT, shop TEXT, sku TEXT, "
              "name TEXT, grade TEXT, amount INTEGER, claim_honest INTEGER)")
    c.execute("CREATE TABLE broken (ts REAL, buyer TEXT, shop_url TEXT, shop TEXT, sku TEXT)")
    rows = []
    seq = 100

    def add(shop, sku, honest, n=1, grade="B"):
        nonlocal seq
        for _ in range(n):
            seq += 1
            rows.append((seq, NOW - 3600, f"b{seq}", f"http://{shop}", shop, sku, sku, grade, 1000, honest))
    add("two-of-two", "G1", 0, 2)                         # 2 / 2: fewer than 3 settles -> no
    add("half", "G1", 0, 2); add("half", "G2", 1, 2)      # 2 / 4 = 50% -> yes
    add("low", "G1", 0, 2); add("low", "G2", 1, 3)        # 2 / 5 = 40% -> no
    add("food", "F1", 0, 3); add("food", "F1", 1, 1)      # food 3 / 4 = 75% < 80%: food out -> nothing
    add("food2", "F1", 0, 4); add("food2", "F1", 1, 1)    # food 4 / 5 = 80% -> yes
    add("fixed", "G1", 0, 3)                              # excluded after a correction
    c.executemany("INSERT INTO settles VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    rules = os.path.join(d, "callout_rules.json")
    with open(rules, "w", encoding="utf-8") as f:
        json.dump({"excluded": {"fixed": NOW + 3 * 86400}}, f)
    return d, p, rules


@contextlib.contextmanager
def env():
    d, p, rules = setup()
    conn = lambda *a, **k: sqlite3.connect(p)
    sent = []
    with mock.patch.object(market_db, "connect", conn), mock.patch.object(analyst, "connect", conn), \
            mock.patch.object(analyst, "_call", lambda m, **k: sent.append(k) or {"message_id": len(sent)}), \
            mock.patch.object(analyst, "chat_id", lambda: 1), mock.patch.object(analyst, "log", lambda m: None), \
            mock.patch.object(decisions, "record", lambda *a, **k: None), mock.patch.object(C, "_catalog", lambda: CAT), \
            mock.patch.object(C, "RULES", rules), mock.patch.object(C, "HERE", d):
        yield d, p, sent


def proposals(p):
    c = sqlite3.connect(p)
    try:
        return c.execute("SELECT id, key, value FROM proposals ORDER BY id").fetchall()
    finally:
        c.close()


def test_tick_and_approve():
    with env() as (d, p, sent):
        C.tick(NOW)
        keys = sorted(k for _, k, _ in proposals(p))
        assert keys == ["callout.food2", "callout.half"], keys
        C.tick(NOW + 600)                                     # 72 h per shop: nothing new
        assert len(proposals(p)) == 2
        pid, key, value = [x for x in proposals(p) if x[1] == "callout.half"][0]
        assert C.approve({"id": pid, "key": key, "value": value}).startswith("buyer 게시 대기열")
        q = json.load(open(os.path.join(d, "forum_once.json"), encoding="utf-8"))
        assert len(q) == 1 and q[0]["key"] == f"callout:half:{pid}"
        bad = json.loads(value)                               # 3: the ledger says otherwise -> not queued
        bad["post"] = bad["post"].replace("실제 등급 B", "실제 등급 C", 1)
        r = C.approve({"id": pid + 10, "key": key, "value": json.dumps(bad)})
        assert r.startswith("게시 안 함: 원장 재확인 실패"), r
        c = sqlite3.connect(p)                                # a breakage reported after -> not queued
        c.execute("INSERT INTO broken VALUES (?,?,?,?,?)", (NOW, "b103", "http://half", "half", "G1"))
        c.commit()
        c.close()
        r = C.approve({"id": pid + 11, "key": key, "value": value})
        assert "고장 True" in r, r
        assert len(json.load(open(os.path.join(d, "forum_once.json"), encoding="utf-8"))) == 1
        # 1: an item-mismatch draft is refused
        try:
            C.send("half", {"post": "x", "refs": [1]}, 1, kind="item")
            raise AssertionError("item draft sent")
        except ValueError:
            pass
        # 4: a correction excludes the shop for 7 days; approve then refuses a callout on it
        C.send("food2", C.correction_draft("food2", 1, dict(ts=NOW, order="o-1", receipt_sku="F1", receipt_name="f",
                                                             price=1000, item="x", result_seq=105)), 1, kind="correction")
        assert C.excluded("food2", NOW + 6 * 86400) and not C.excluded("food2", NOW + 8 * 86400)
        pid2, key2, value2 = [x for x in proposals(p) if x[1] == "callout.food2"][0]
        assert C.approve({"id": pid2, "key": key2, "value": value2}).startswith("게시 안 함: food2는 정정 후 7일")
        corr = [x for x in proposals(p) if "[정산 기록 정정]" in x[2]][0]   # the correction itself is queued
        assert C.approve({"id": corr[0], "key": corr[1], "value": corr[2]}).startswith("buyer 게시 대기열")
    with env() as (d, p, sent):                               # candidates: the counts left out
        out, skipped = C.candidates(NOW)
        assert sorted(out) == ["food2", "half"] and skipped["excluded"] == 3 and skipped["food"] == 3, skipped


if __name__ == "__main__":
    test_tick_and_approve()
    print("ok")
