"""11-11: reviews tool: our reviews joined to our sales by order, per segment, with the effect on the per-account mean."""
import json
import os

from kernel import tools

NOW = 1_794_400_000.0


def _root(tmp_path):
    os.makedirs(tmp_path / ".r3"); os.makedirs(tmp_path / ".market")
    (tmp_path / ".market/catalog.json").write_text(json.dumps({"items": []}))
    ev = []
    def sale(order, buyer, grade, stars, wh=None, amount=100):
        ev.append({"ts": NOW - 7200, "type": "settle", "src": "market", "data": {
            "order": order, "buyer": buyer, "shop": "me", "grade": grade, "sku": "G1", "amount": amount,
            "warranty_hours": wh, "claim_honest": True}})
        ev.append({"ts": NOW - 3600, "type": "review", "role": "shop", "data": {"order": order, "stars": stars}})
    sale("o1", "b1", "A", 5); sale("o2", "b1", "A", 5); sale("o3", "b2", "C", 1); sale("o4", "b3", "C", 2, wh=6)
    ev.append({"ts": NOW - 3600, "type": "review", "role": "shop", "data": {"order": "zz", "stars": 3}})  # no sale
    ev.append({"ts": NOW - 4000, "type": "fail", "role": "shop", "data": {"order": "zz", "stage": "result"}})
    ev.append({"ts": NOW - 90 * 3600, "type": "review", "role": "shop", "data": {"order": "o1", "stars": 1}})  # old
    (tmp_path / ".r3/events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev))
    return str(tmp_path)


def test_reviews_by_grade(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.c, "settle_side", lambda d, login: "shop")
    out = tools.reviews(_root(tmp_path), hours=24, by="grade", now=NOW)
    lines = out.splitlines()
    assert "5건" in lines[0] and "판매와 이어진 4건" in lines[0]
    assert "판매와 이어지지 않은 리뷰 1건: 그 주문에 fail 이벤트가 있는 것 1건 (평균 3.00, 3점 이하 1건, 단계 result 1), 없는 것 0건" in lines[1]
    assert lines[2].startswith("  fail 시각(시)별 건수: ")
    lines = [lines[0]] + lines[3:]
    assert "계정별 평균의 평균 2.667 (계정 3곳)" in lines[1]
    assert lines[2].startswith("  견줄 값: 판매와 이어진 리뷰만의 평균 3.25, 이어지지 않은 리뷰까지 넣은 평균 3.20.")
    lines = lines[:2] + lines[3:]
    assert lines[3].startswith("  C: 리뷰 2, 평균 1.50, 3점 이하 2, 1~2점 2, 계정 2곳, 빼면 +2.333")
    assert lines[4].startswith("  A: 리뷰 2, 평균 5.00, 3점 이하 0")


def test_reviews_args(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.c, "settle_side", lambda d, login: "shop")
    root = _root(tmp_path)
    assert tools.reviews(root, by="nope", now=NOW).startswith("인자 오류")
    assert "보증 있음: 리뷰 1" in tools.reviews(root, by="warranty", now=NOW)
    assert "reviews" in tools.RECOMMENDED["shop"] and "reviews" in tools.TOOLS
