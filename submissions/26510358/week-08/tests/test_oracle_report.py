"""Price oracle and the forum report lane on a synthetic ledger (no network, no model)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import config, oracle
from test_r3_core import CAT, mem, settle


def _ledger(m, now):
    m._ingest([settle(i, "G1", 70000 + i * 100, now - i * 60, grade="AB"[i % 2]) for i in range(1, 9)])
    m._ingest([settle(100 + i, "F1", 7000, now - i * 60) for i in range(1, 13)])


def test_sku_and_category_stats(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    now = time.time()
    _ledger(m, now)
    s = oracle.sku_stats(m, "G1", hours=3, now=now)
    assert s["n"] == 8 and s["low"] == 70100 and s["high"] == 70800 and s["list_price"] == 100000
    assert set(s["by_grade"]) == {"A", "B"} and s["refs"][0] == 8               # highest ledger seq first
    c = oracle.category_stats(m, "주스/코코아/생수", hours=3, now=now)
    assert c["n"] == 12 and c["ratio_median"] == 0.7 and c["top_sku"] == "F1"
    assert oracle.hot_skus(m, now=now)[0] == ("F1", 12)


def test_reports_skip_subjects_already_posted(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    now = time.time()
    _ledger(m, now)
    subject, text, refs = oracle.sku_report(m, now=now)
    assert subject == "sku:F1" and "7,000" in text and refs
    assert oracle.sku_report(m, skip={"sku:F1"}, now=now)[0] == "sku:G1"
    assert oracle.category_report(m, now=now)[0].startswith("cat:")


def test_purchase_report_is_first_hand(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    now = time.time()
    with m.db() as c:
        c.execute("INSERT INTO purchases (order_id, ts, shop_url, shop_name, sku, name, list_price, price, grade, claim, "
                  "claim_honest, warranty_hours, ledger, paid) VALUES ('o1', ?, 'https://s1', 'Shop1', 'G1', '의자', "
                  "100000, 72000, 'A', 'A', 1, 6, 55, 1)", (now,))
    assert oracle.purchase_report(m, now=now) is None                           # settle 55 not synced yet
    m._ingest([settle(55, "G1", 72000, now, order="o1")])
    subject, text, refs = oracle.purchase_report(m, now=now)
    assert subject == "buy:o1" and refs == [55] and "28% 저렴" in text and "정직" in text and "보증 6h" in text
    assert oracle.purchase_report(m, skip={"buy:o1"}, now=now) is None


def test_report_lane_gap_selfcheck_and_rotation(tmp_path, monkeypatch):
    import forum_r3
    m = mem(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "MARKET_DIR", tmp_path)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    now = time.time()
    _ledger(m, now)
    rec = forum_r3.scheduled_report(m, now, p_injection=lambda p: 0.1)
    assert rec["decision"] == "post" and rec["source"] == "report:sku"           # no purchase yet: falls through
    line = json.loads((tmp_path / "outbox.jsonl").read_text().splitlines()[-1])
    assert line["as"] == "buyer" and line["refs"]
    assert forum_r3.scheduled_report(m, now + 60, p_injection=lambda p: 0.1)["decision"] == "wait"
    later = now + config.REPORT_GAP_MIN * 60 + 1
    rec = forum_r3.scheduled_report(m, later, p_injection=lambda p: 0.9)        # self-check drops every draft
    assert rec["decision"] == "silent" and "self-check" in rec["why"]


def test_report_lane_pauses_when_disliked(tmp_path, monkeypatch):
    import forum_r3
    m = mem(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "MARKET_DIR", tmp_path)
    now = time.time()
    m.upsert_forum([{"id": 9, "ts": now, "author": "myeonggi", "role": "buyer", "text": "x", "likes": 1, "dislikes": 3}])
    assert forum_r3.scheduled_report(m, now, p_injection=lambda p: 0.0)["decision"] == "pause"
