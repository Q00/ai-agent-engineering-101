"""Food grade claims (person 11/15 ~16:40): A / B food units get their grade in the receipt's claim, computed when the
receipt is built from expires_at and the catalog's shelf_hours (A >= 80 %, B >= 45 % left; inventory.json may be
10 s old), unless the next drop is within food_n_h (3) hours; C never (food and non-food); non-food A / B claimed.
Through seller_r3.Seller.execute (the A2A path); then the guard (a false food claim -> food claims off, non-food kept)
and the food 6 h report on a temp db. Live files untouched.
    python tests/test_claim_food_1115.py"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_food_need as F  # noqa: E402
import test_claim_full_1115 as CFT  # noqa: E402
from _isolate import tmp_json, tmp_path  # noqa: E402
from depts import claim_full as CF, receipt_exp  # noqa: E402

REAL_ARM = CFT.REAL_ARM
S = 100.0                                          # shelf_hours of the test food SKU
RULES = {"claim_share": 1.0, "claim_grades": ["A", "B"], "food_claim": True, "food_n_h": 3}
ASK_FOOD = f"I want to buy 테스트 김치 10kg ({F.SKU}). What is your price?"
ASK_GOODS = "I want to buy 테스트 이불 (G00d50001). What is your price?"


def claim_of(units, ask, rules=RULES):
    log = tmp_path(f"cn{time.time_ns()}.jsonl")
    with F.env(units, on=False), mock.patch.object(receipt_exp, "FLAG", Path(tmp_json(rules, f"rr{time.time_ns()}"))), \
            mock.patch("depts.receipt_exp.claim_arm", REAL_ARM), mock.patch.object(receipt_exp, "CLAIM_LOG", Path(log)), \
            mock.patch.object(receipt_exp, "_shelf_hours", lambda sku, catalog=None: S if sku == F.SKU else None):
        s = F.seller.Seller()
        t, ev = asyncio.run(F.send(s, ask))
        ask_p = s.open[t.id]["ask"]
        t, ev = asyncio.run(F.send(s, f"{ask_p}", t))
        arts = [e for e in ev if type(e).__name__ == "TaskArtifactUpdateEvent"]
        assert arts, F.texts(ev)
        r = " ".join(str(arts[0]).split())
    i = r.find('key: "claim" value { string_value: "')
    rows = [json.loads(x) for x in open(log, encoding="utf-8")] if os.path.exists(log) else []
    return (r[i + 36] if i >= 0 else None), rows


def test_food():
    # A, 90 h left of 100 (next drop A->B in 10 h): claimed A
    g, rows = claim_of([F.unit("a", 90, "A")], ASK_FOOD)
    assert g == "A" and rows[-1]["claim"] == "A" and rows[-1]["next_drop_h"] > 9.9, (g, rows)
    # A, 82 h left (drop in 2 h <= 3): no claim, logged
    g, rows = claim_of([F.unit("a2", 82, "A")], ASK_FOOD)
    assert g is None and rows[-1]["claim"] is None and rows[-1]["grade"] == "A", (g, rows)
    # B, 60 h left (B->C in 15 h): claimed B; B, 46 h left (drop in 1 h): none
    assert claim_of([F.unit("b", 60, "B")], ASK_FOOD)[0] == "B"
    assert claim_of([F.unit("b2", 46, "B")], ASK_FOOD)[0] is None
    # C, 30 h left: never
    assert claim_of([F.unit("c", 30, "C")], ASK_FOOD)[0] is None
    # the inventory says A but the unit is already under 80 % (stale file): the grade computed now, B, is claimed
    g, rows = claim_of([F.unit("st", 70, "A")], ASK_FOOD)
    assert g == "B" and rows[-1]["inv_grade"] == "A", (g, rows)
    # food_claim off: no food claim
    assert claim_of([F.unit("a3", 90, "A")], ASK_FOOD, dict(RULES, food_claim=False))[0] is None


def test_goods():
    for gr, want in (("A", "A"), ("B", "B"), ("C", None)):
        for _ in range(3):
            assert claim_of([CFT.goods("u1", gr)], ASK_GOODS)[0] == want, gr


def test_guard_food():
    now = time.time()
    rr = tmp_json(dict(RULES, full_since=now - 7200, full_prev=0.5, food_since=now - 3600), "rr_fg")
    cat = {"F1": {"kind": "food"}, "G1": {"kind": "goods"}}
    sent = []
    p = CFT.db([(now - 600, "b", CF.ME, "F1", 1000, "u", "B", 0), (now - 500, "b", CF.ME, "G1", 1000, "v", "A", 1)])
    assert CF.guard(send=lambda *a: sent.append(a), path=rr, db_path=p, log=lambda *a: None, catalog=cat) == "tripped"
    r = json.load(open(rr, encoding="utf-8"))
    assert r["food_claim"] is False and r["claim_share"] == 1.0 and not r.get("full_off_ts") and r.get("food_off_ts"), r
    assert len(sent) == 1 and sent[0][0] == "urgent" and "식품 등급 주장 자동 끔" in sent[0][2], sent
    # a false non-food claim: claim_share back to 0.5 (the 1안 rule), food untouched
    rr = tmp_json(dict(RULES, full_since=now - 7200, full_prev=0.5, food_since=now - 3600), "rr_fg2")
    sent = []
    p = CFT.db([(now - 600, "b", CF.ME, "G1", 1000, "u", "C", 0)])
    assert CF.guard(send=lambda *a: sent.append(a), path=rr, db_path=p, log=lambda *a: None, catalog=cat) == "tripped"
    r = json.load(open(rr, encoding="utf-8"))
    assert r["claim_share"] == 0.5 and r["food_claim"] is True and len(sent) == 1, (r, sent)


def test_food_report():
    now = time.time()
    since = now - 6 * 3600 - 60
    rr = tmp_json(dict(RULES, food_since=since, report_h=6), "rr_fr")
    cat = {"F1": {"kind": "food"}, "G1": {"kind": "goods"}}
    p = CFT.db([(now - 3600, "olstjr", CF.ME, "F1", 1000, "a", "A", 1), (now - 3000, "x", CF.ME, "F1", 1000, "b", "C", None),
                (since - 3600, "x", CF.ME, "F1", 1000, "d", "A", None)])
    sent = []
    assert CF.food_report_due(send=lambda *a: sent.append(a), path=rr, log=lambda *a: None, db_path=p, catalog=cat,
                              kit_paths=[tmp_path("nokit.log")])
    t = sent[0][2]
    assert "식품 판매 중 주장 있음 1/2 (50%) vs 0/1 (0%)" in t and "거짓 주장 기록 0건 vs 0건" in t, t
    assert "olstjr 0.2 vs 0.0" in t and "다음 하락까지 3시간 이내 제외" in t, t


if __name__ == "__main__":
    test_food()
    test_goods()
    test_guard_food()
    test_food_report()
    print("ok")
