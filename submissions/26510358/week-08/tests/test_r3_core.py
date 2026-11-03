"""Round 3 memory and pricing on a synthetic ledger (no network)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import market, pricing
from r3lib.memory import Memory

CAT = {
    "F1": {"name": "생수 2L", "category": "식품>음료>주스/코코아/생수", "list_price": 10000, "kind": "food", "shelf_hours": 720},
    "G1": {"name": "의자", "category": "가구/침구>가구>소파/의자/스툴", "list_price": 100000, "kind": "goods"},
}


def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT)
    return Memory(tmp_path / "m.sqlite")


def settle(seq, sku, amount, ts, order=None, shop="https://s1", grade="A", honest=True):
    return {"seq": seq, "ts": ts, "kind": "settle", "round": 3,
            "data": {"order": order or f"o{seq}", "buyer": "b1", "shop": shop, "item": sku, "amount": amount,
                     "grade": grade, "claim": grade, "claim_honest": honest}}


def test_demand_prices_and_breaks(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    now = time.time()
    rows = [settle(i, "F1", 8000 + i * 10, now - i * 600) for i in range(1, 7)]
    rows += [settle(10, "G1", 70000, now - 100, order="og", grade="C"),
             {"seq": 11, "ts": now - 50, "kind": "broken", "round": 3, "data": {"order": "og", "buyer": "b1", "item": "G1"}},
             {"seq": 12, "ts": now, "kind": "order", "round": 3, "data": {}},
             settle(13, "F1", 1, now, order="old")]
    rows[-1]["round"] = 2                                   # earlier rounds are ignored
    m._ingest(rows)
    d = m.category_demand(now=now)
    assert d["식품>음료>주스/코코아/생수"]["n"] == 6 and d["식품>음료>주스/코코아/생수"]["rate"] > 0
    ratio, n = m.price_ratio(sku="F1")
    assert n == 6 and 0.8 < ratio < 0.9
    assert m.break_rate("C") > m.break_rate("A")         # the broken chair is grade C
    rep = m.shop_reputation("https://s1/")
    assert rep["sales"] == 7 and rep["break_rate"] > 0


def test_broken_matches_our_purchase(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    md = tmp_path / "md"
    md.mkdir()
    m.record_purchase_intent({"order": "o1", "budget": 90000}, "https://s1", "S1", {"item": "G1", "price": 60000})
    m.record_settled({"order": {"order": "o1"}, "receipt": {"price": 60000},
                      "settled": {"shop": "https://s1", "sku": "G1", "grade": "A", "warranty_hours": 6, "value": 100000}})
    (md / "broken.jsonl").write_text(json.dumps({"name": "의자", "broke_at": time.time()}) + "\n")
    hit = m.ingest_broken(md)
    assert hit and hit[0]["order_id"] == "o1" and hit[0]["covered"]
    assert m.ingest_broken(md) == []                      # offset kept: no double count


def test_pricing_rules():
    u = {"cost": 6000, "list_price": 10000, "kind": "food", "grade": "A", "expires_at": time.time() + 100 * 3600}
    floor = pricing.unit_floor(u, hours_left_round=200)
    assert floor == int(6000 * 1.08)
    assert pricing.unit_floor(u, hours_left_round=3) < 6000          # clearance near the end
    u2 = dict(u, expires_at=time.time() + 3 * 3600)
    assert pricing.unit_floor(u2, hours_left_round=200) == int(6000 * 0.3)
    assert pricing.unit_ask(u) >= floor
    assert pricing.seller_reply(9000, 6480, 9000, 1) == ("accept", 9000)
    act, p = pricing.seller_reply(9000, 6480, 5000, 0)
    assert act == "counter" and 6480 <= p < 9000
    # buyer never above budget or 95% of list
    assert pricing.buyer_limits(10000, 99999, "food") == 9500
    assert pricing.buyer_limits(10000, 7000, "food") == 7000
    assert pricing.buyer_limits(100000, 10**6, "goods", p_break=0.2) < 70000
    act, p = pricing.buyer_next_offer(10000, 9000, 9500, None, 0)
    assert act == "offer" and p == 5500
    assert pricing.buyer_next_offer(10000, 9000, 7000, 5500, 1) == ("accept", 7000)
