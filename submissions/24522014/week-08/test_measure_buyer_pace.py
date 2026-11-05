from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

import measure_buyer_pace as pace

KST = timezone(timedelta(hours=9))


class Measure(unittest.TestCase):
    def test_counts_inside_the_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".market").mkdir()
            (root / "logs/market-buyer").mkdir(parents=True)
            (root / ".market/catalog.json").write_text(json.dumps({"G1": {"list_price": 100}}), encoding="utf-8")
            (root / ".market/payments.jsonl").write_text(
                json.dumps({"order": "a", "paid": True, "item": "G1", "price": 80}) + "\n" + json.dumps({"order": "b", "paid": False, "reason": "not paying: 3.2 h"}) + "\n", encoding="utf-8")
            for name, record in (("20261105T010000000000Z-a.json", {"order": "a", "decision_trail": [{"reason_code": "rule_offer"}]}),
                                 ("20261105T010005000000Z-c.json", {"order": "c", "selection_reason": "attempt_cooldown", "failure_reason": "selection_attempt_cooldown"}),
                                 ("20261105T010030000000Z-b.json", {"order": "b", "decision_trail": [{"reason_code": "model_quota_hour"}]}),
                                 ("20261105T030000000000Z-z.json", {"order": "z", "failure_reason": "late"})):
                (root / "logs/market-buyer" / name).write_text(json.dumps({"record": record}), encoding="utf-8")
            start = datetime(2026, 11, 5, 10, 0, tzinfo=KST)
            result = pace.measure(root, start, start + timedelta(hours=1))
        self.assertEqual((result["orders"], result["attempts"], result["paid"], result["surplus_before_breakage"], result["payment_refused"]), (3, 2, 1, 20, 1))
        self.assertEqual((result["gap_under_10s"], result["gap_under_20s"], result["gaps"]), (1, 1, 2))
        self.assertEqual(dict(result["stops"])["selection_attempt_cooldown"], 1)


if __name__ == "__main__":
    unittest.main()
