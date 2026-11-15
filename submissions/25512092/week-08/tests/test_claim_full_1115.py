"""Grade claims on every non-food receipt (person 11/15 ~16:00): receipt_rules.json claim_share 1.0 read by the live
seller on every negotiation (no restart) -> every goods receipt carries the unit's grade, food none; the guard (one
false claim in the ledger since full_since -> claim_share back to 0.5 + urgent Telegram) and the 6 h report.
Through seller_r3.Seller.execute (the A2A path) and depts/claim_full on a temp db; live files untouched.
    python tests/test_claim_full_1115.py"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_food_need as F  # noqa: E402  (its seller harness: temp inventory / db, code haggle)
from _isolate import tmp_json, tmp_path  # noqa: E402
from depts import claim_full as CF, receipt_exp  # noqa: E402

NOW = time.time()
REAL_ARM = receipt_exp.claim_arm                       # before F.env stubs it


def goods(uid, grade):
    return {"unit": uid, "sku": "G00d50001", "name": "테스트 이불", "category": "가구>침구>이불", "list_price": 30000,
            "kind": "goods", "cost": 12000, "supplied_at": NOW - 3600, "expires_at": None, "grade": grade, "cert": None}


def receipt_for(units, share, ask_text):
    with F.env(units, on=False), mock.patch.object(receipt_exp, "FLAG", __import__("pathlib").Path(
            tmp_json({"claim_share": share}, f"rr{time.time_ns()}"))), \
            mock.patch("depts.receipt_exp.claim_arm", REAL_ARM):   # the real arm (env stubs it)
        s = F.seller.Seller()
        t, ev = asyncio.run(F.send(s, ask_text))
        ask = s.open[t.id]["ask"]
        t, ev = asyncio.run(F.send(s, f"{ask}", t))
        arts = [e for e in ev if type(e).__name__ == "TaskArtifactUpdateEvent"]
        assert arts, F.texts(ev)
        return str(arts[0])


def test_seller_claims():
    for g in ("A", "B", "C"):                          # share 1.0: every goods receipt carries the grade, 5 tries
        for _ in range(5):
            r = receipt_for([goods("u1", g)], 1.0, "I want to buy 테스트 이불 (G00d50001). What is your price?")
            assert f'key: "claim" value {{ string_value: "{g}" }}' in " ".join(r.split()), r
    food = F.unit("f1", 30)                            # food: still no claim
    r = receipt_for([food], 1.0, F.ASK)
    assert 'key: "claim"' not in r, r
    seen = {"claim" in receipt_for([goods("u1", "B")], 0.0, "I want to buy 테스트 이불 (G00d50001). What is your price?")
            for _ in range(3)}
    assert seen == {False}, seen                       # share 0: no claim (the old 0.5 lies between)


def db(rows):
    p = tmp_path(f"cf{time.time_ns()}.db")
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE settles (seq INTEGER PRIMARY KEY, ts REAL, buyer TEXT, shop TEXT, sku TEXT, amount INTEGER, "
              "unit TEXT, grade TEXT, claim_honest INTEGER)")
    d.execute("CREATE TABLE my_stock (unit TEXT, sku TEXT, cost INTEGER, supplied_at REAL)")
    for r in rows:
        d.execute("INSERT INTO settles (ts, buyer, shop, sku, amount, unit, grade, claim_honest) VALUES (?,?,?,?,?,?,?,?)", r)
    d.commit()
    d.close()
    return p


def test_guard():
    rr = tmp_json({"claim_share": 1.0, "full_since": NOW - 3600, "full_prev": 0.5}, "rr_g")
    sent = []
    p = db([(NOW - 600, "b", CF.ME, "G1", 1000, "u", "B", 1)])
    assert CF.guard(send=lambda *a: sent.append(a), path=rr, db_path=p, log=lambda *a: None) == "ok" and not sent
    p = db([(NOW - 600, "b", CF.ME, "G1", 1000, "u", "B", 1), (NOW - 300, "b", CF.ME, "G2", 1000, "u2", "C", 0),
            (NOW - 7200, "b", CF.ME, "G3", 1000, "u3", "C", 0)])          # the one before full_since does not count
    assert CF.guard(send=lambda *a: sent.append(a), path=rr, db_path=p, log=lambda *a: None) == "tripped"
    r = json.load(open(rr, encoding="utf-8"))
    assert r["claim_share"] == 0.5 and r.get("full_off_ts") and "거짓 등급 주장 1건" in r["full_off_why"], r
    assert len(sent) == 1 and sent[0][0] == "urgent" and "자동 원복" in sent[0][2], sent
    assert CF.guard(send=lambda *a: sent.append(a), path=rr, db_path=p, log=lambda *a: None) == "off" and len(sent) == 1


def test_report():
    since = NOW - 6 * 3600 - 60
    rr = tmp_json({"claim_share": 1.0, "full_since": since, "full_prev": 0.5, "report_h": 6}, "rr_r")
    cat = {"G1": {"kind": "goods"}, "F1": {"kind": "food"}}
    p = db([(NOW - 3600, "inextro", CF.ME, "G1", 1000, "a", "A", 1), (NOW - 3000, "x", CF.ME, "G1", 1000, "b", "B", 1),
            (NOW - 2000, "x", CF.ME, "F1", 1000, "c", "B", None), (since - 3600, "x", CF.ME, "G1", 1000, "d", "A", None)])
    kit = tmp_path(f"kit{time.time_ns()}.log")
    with open(kit, "w", encoding="utf-8") as f:
        for ts in (NOW - 9 * 3600, NOW - 4000):                 # in time order, as the kit writes
            f.write(time.strftime("%H:%M:%S", time.localtime(ts)) + " [acp] checkout refused: unit u-1 is not in stock\n")
        f.write(time.strftime("%H:%M:%S", time.localtime(NOW - 30)) + " [seller] x\n")
    sent = []
    ok = CF.report_due(send=lambda *a: sent.append(a), path=rr, log=lambda *a: None, db_path=p, catalog=cat,
                       kit_paths=[kit])
    assert ok and sent[0][0] == "daily", sent
    t = sent[0][2]
    assert "비식품 판매 2건 (주장 있음 100%) vs 1건 (0%)" in t and "거짓 주장 기록 0건 vs 0건" in t, t
    assert "결제 거절(우리 가게 checkout refused) 1건 vs 1건" in t and "inextro 0.2 vs 0.0" in t, t
    assert not CF.report_due(send=lambda *a: sent.append(a), path=rr, log=lambda *a: None, db_path=p, catalog=cat,
                             kit_paths=[kit])


if __name__ == "__main__":
    test_seller_claims()
    test_guard()
    test_report()
    print("ok")
