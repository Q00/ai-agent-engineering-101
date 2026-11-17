"""review.py forum posts: the 5-minute clock starts at the accepted post; refused or lost posts retry at once."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(os.environ.get("AX_TEST_SOURCE", str(Path(__file__).resolve().parent.parent)))
sys.path.insert(0, str(SOURCE))
import review
import shop_strategy

assert shop_strategy.DEFAULTS["buyer"]["forum_lead"] == 60


def deal(n):
    return {"ts": time.time(), "ledger": n, "sku": "G1", "name": "item", "kind": "goods", "price": 1000,
            "list_price": 2000, "grade": "A", "claim": "A", "claim_honest": True, "warranty_hours": 6,
            "value": 1, "shop": "s", "shop_url": "u"}


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    box = root / "buyer-box"
    box.mkdir()
    (root / "logs").mkdir()
    with patch.object(review, "MD", root), patch.object(review, "BOX", box), \
            patch.object(review, "STATE", root / "state.json"), patch.object(review, "FORUM_LOG", root / "logs/f.jsonl"):
        out, sent = box / "outbox.jsonl", box / "outbox.sent.jsonl"
        state = lambda: json.loads((root / "state.json").read_text())

        def kit_sends(status):                       # what market_kit.outbox does with the line
            line = json.loads(out.read_text().splitlines()[0])
            out.unlink()
            with open(sent, "a") as f:
                f.write(json.dumps({"sent": line, "status": status, "answer": "x"}) + "\n")

        memory = [deal(1)]
        review.maybe_post(memory)                    # nothing posted before: post now
        assert out.exists() and state()["pending"]["fresh"] == [1]
        kit_sends(200)
        memory.append(deal(2))
        review.maybe_post(memory)                    # just accepted: wait for the slot
        assert not out.exists() and state()["pending"] is None
        s = state()
        s["last_ok"] = time.time() - review.POST_GAP + 30     # inside the lead window: queue now
        (root / "state.json").write_text(json.dumps(s))
        review.maybe_post(memory)
        assert out.exists() and state()["pending"]["fresh"] == [2]
        kit_sends(400)                               # refused: the same deal goes out again on the next run
        review.maybe_post(memory)
        assert out.exists() and 2 in json.loads(out.read_text())["refs"]
        out.unlink()                                 # lost without an answer (kit restart)
        review.maybe_post(memory)
        assert out.exists() and 2 in json.loads(out.read_text())["refs"]
        text = json.loads(out.read_text())["post"]
        assert "#2" in text and "결제·정산된 거래 1건" in text
        events = [json.loads(l)["event"] for l in (root / "logs/f.jsonl").read_text().splitlines()]
        assert {"accepted", "refused", "lost", "queued"} <= set(events), events
print("PASS forum posts: clock from acceptance, lead window, immediate retry after refusal or loss, gaps logged")
