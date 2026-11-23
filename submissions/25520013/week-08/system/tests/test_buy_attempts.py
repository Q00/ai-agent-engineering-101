"""11-12: buy_attempts tool: how our buy attempts ended, joined to buyer settles by order."""

import json
import os

from kernel import tools

NOW = 1_794_400_000.0


def _root(tmp_path):
    os.makedirs(tmp_path / ".r3")
    os.makedirs(tmp_path / ".market")
    (tmp_path / ".market/catalog.json").write_text(
        json.dumps(
            [
                {"sku": "F1", "category": "식품/과일", "shelf_hours": 48},
                {"sku": "G1", "category": "가구/의자"},
            ]
        )
    )
    ev = []

    def buy(order, tried, ts=NOW - 3600):
        ev.append(
            {
                "ts": ts,
                "type": "decision",
                "role": "buyer",
                "key": "buy:" + order,
                "data": {
                    "hook": "market_buyer",
                    "what": "offer_selection",
                    "settled": 0,
                    "tried": tried,
                },
            }
        )

    def paid(order, amount, value):
        ev.append(
            {
                "ts": NOW - 3500,
                "type": "settle",
                "src": "deal",
                "role": "buyer",
                "data": {"order": order, "amount": amount, "value": value},
            }
        )

    done = {"sku": "G1", "state": "TASK_STATE_COMPLETED", "rounds": 3}
    cap = {
        "sku": "G1",
        "state": "TASK_STATE_INPUT_REQUIRED",
        "cap_stop": True,
        "rounds": 4,
    }
    buy("o1", [dict(cap, shop="s1"), dict(done, shop="s2")])
    paid("o1", 80, 100)
    buy("o2", [dict(cap, shop="s1")])
    buy(
        "o3",
        [
            {
                "sku": "F1",
                "state": "walked_away",
                "why": "claimed shelf life too short",
            },
            {"sku": "F1", "state": "TASK_STATE_INPUT_REQUIRED", "deadline_cut": True},
        ],
    )
    buy("o4", [{"sku": "F1", "state": "TASK_STATE_COMPLETED", "rounds": 1}])
    paid("o4", 9, 10)
    buy("old", [dict(done)], ts=NOW - 30 * 3600)
    (tmp_path / ".r3/events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in ev)
    )
    return str(tmp_path)


def test_buy_attempts(tmp_path):
    out = tools.buy_attempts(_root(tmp_path), hours=6, now=NOW)
    lines = out.splitlines()
    assert lines[0].startswith(
        "구매 시도 최근 6시간: 주문 4건, 정산으로 이어진 주문 2건 (50%), 가게별 시도 6건"
    )
    assert (
        "  식품 시도 3건: " in out
        and "마감에 잘림 1건 (33%)" in out
        and "우리가 물러남 1건 (33%)" in out
    )
    assert (
        "  일반상품 시도 3건: 가격 상한에서 멈춤 2건 (67%), 흥정 완료 1건 (33%)" in out
    )
    assert "  우리가 물러난 사유: 말한 유통기한이 짧음 1" in out
    assert "  가격 상한에서 멈춘 시도가 있던 주문 2건 중 정산 1건 (50%)" in out
    assert "  마감에 잘린 시도가 있던 주문 1건 중 정산 0건 (0%)" in out
    assert "  정산되지 않은 주문 2건의 마지막 시도: " in out
    assert "  일반상품 정산 1건의 지불액/가치: 평균 0.800, 중앙 0.800" in out
    assert "  식품 정산 1건의 지불액/가치: 평균 0.900" in out
    assert "  흥정 완료 시도의 회차: 중앙 2, 최대 3" in out
    assert "오류 내용" not in out


def test_buy_attempts_errors_and_first_cap(tmp_path):
    root = _root(tmp_path)
    with open(os.path.join(root, ".r3/events.jsonl"), "a") as fh:
        fh.write(json.dumps({"ts": NOW - 100, "type": "decision", "role": "buyer", "key": "buy:o9", "data": {
            "hook": "market_buyer", "tried": [{"sku": "G1", "error": "HTTP 502 from https://x.example/a"}]}}) + "\n")
    out = tools.buy_attempts(root, hours=6, now=NOW)
    assert "  오류 내용(숫자와 주소는 가림): HTTP N from [URL] 1" in out
    assert "x.example" not in out
    assert "  cap_stop 표시는 이 구간에서 " in out


def test_buy_attempts_empty_and_registry(tmp_path):
    os.makedirs(tmp_path / ".r3")
    assert (
        tools.buy_attempts(str(tmp_path), hours="x", now=NOW)
        == "최근 6시간 구매 결정 기록 없음"
    )
    assert (
        "buy_attempts" in tools.RECOMMENDED["buyer"]
        and tools.TOOLS["buy_attempts"][0] is tools.buy_attempts
    )
