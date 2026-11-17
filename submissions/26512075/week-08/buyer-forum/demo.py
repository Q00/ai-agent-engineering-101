"""Run an entirely offline demonstration in a temporary directory."""

import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from . import analyst, publisher
    from .storage import read_jsonl, write_json, write_jsonl
else:
    import analyst
    import publisher
    from storage import read_jsonl, write_json, write_jsonl


class MockMarket:
    base = "https://mock.market.example"
    token = {"buyer": "example-token"}
    joined = {"buyer": ("mock-buyer", None, None)}

    def call(self, method, path, role, **kwargs):
        assert (method, path, role) == ("POST", "/api/forum", "buyer")
        assert kwargs["json"]["refs"] == [103]
        return SimpleNamespace(status_code=201, headers={})


def main():
    with tempfile.TemporaryDirectory(prefix="forum-demo-") as folder:
        state = Path(folder)
        now = time.time()
        catalog = {"G11111111": {"category": "가구"}, "G22222222": {"category": "식품"}}
        forum = [{"id": 100, "round": 3, "ts": now, "text": "참고용 모의 게시글", "refs": []}]
        def settlement(seq, sku, buyer):
            return {"seq": seq, "ts": now, "round": 3, "kind": "settle", "data": {
                "sku": sku, "amount": 700, "grade": "B", "warranty_hours": 6,
                "buyer": buyer, "shop": "https://mock.shop.example", "order": f"order-{seq}"}}
        ledger = [settlement(101, "G11111111", "other"), settlement(102, "G11111111", "other"),
                  settlement(103, "G22222222", "mock-buyer")]
        write_json(state / "catalog.json", catalog)
        write_json(state / "forum.json", forum)
        write_json(state / "ledger.json", ledger)
        write_jsonl(state / "payments.jsonl", [{"paid": True, "ledger": 103, "price": 700,
                                                "shop": "https://mock.shop.example", "order": "order-103"}])
        report = analyst.run_once(state, MockMarket.base, ledger_file=state / "ledger.json")
        print("1. 내부 후보", [row["sku"] for row in report["candidates"]])
        draft = publisher.preview(state, MockMarket.base, "mock-buyer", 103)
        print("2. 모의 미리보기", json.dumps(draft, ensure_ascii=False))
        publisher.enqueue(state, MockMarket.base, "mock-buyer", 103, draft["id"])
        print("3. 모의 승인 대기열", len(read_jsonl(state / publisher.QUEUE)), "건")
        print("4. 모의 발송 결과", publisher.dispatch(state, MockMarket()))
        print("5. 모의 기록", read_jsonl(state / publisher.JOURNAL)[-1]["state"])
        print("모든 데이터와 전송은 이 프로그램 안의 모의 객체를 사용했습니다.")


if __name__ == "__main__":
    main()
