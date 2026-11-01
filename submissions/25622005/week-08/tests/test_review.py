"""review hook in a subprocess: stars from settled facts, deal archived, nothing printed on error."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

W = Path(__file__).resolve().parents[1]

ORDER = {"order": "o-5", "item": "김치", "category": "식품>김치", "budget": 41234, "acceptable": ["F1"],
         "kind": "food", "use_within_hours": 10, "round": 3}
RECEIPT = {"shop": "fake", "item": "F1", "price": 22000, "task": "t1", "unit": "u7", "sig_seller": "x"}
SETTLED = {"shop": "http://a", "unit": "u7", "sku": "F1", "name": "김치 A", "grade": "A", "claim": "A",
           "claim_honest": True, "expires_at": 0, "warranty_hours": None, "value": 30000, "ledger": 812}


class ReviewHookTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="r3-rv-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / "strategy").mkdir()
        shutil.copy(W / "strategy" / "SKILL.md", self.home / "strategy" / "SKILL.md")
        (self.home / ".market").mkdir()
        self.deal = self.home / ".market" / "deal.json"

    def run_hook(self, settled=None, buyer_row=None, raw=None):
        if raw is not None:
            self.deal.write_text(raw, encoding="utf-8")
        else:
            self.deal.write_text(json.dumps({"order": ORDER, "receipt": RECEIPT, "settled": settled or SETTLED},
                                            ensure_ascii=False), encoding="utf-8")
        if buyer_row:
            d = self.home / "logs" / "decisions"
            d.mkdir(parents=True, exist_ok=True)
            (d / "buyer.jsonl").write_text(json.dumps(buyer_row, ensure_ascii=False) + "\n", encoding="utf-8")
        p = subprocess.run([sys.executable, "-B", str(W / "review.py"), "--deal", str(self.deal)], cwd=self.home,
                           env=dict(os.environ, R3_HOME=str(self.home)), capture_output=True, text=True, timeout=30)
        return p.stdout.splitlines()

    def test_rates_and_archives(self):
        row = {"id": "d-buyer-9", "kind": "order", "ref": {"order": "o-5"},
               "choice": {"sku": "F1", "unit": "u7", "V": 30000, "name": "김치 A"}}
        lines = self.run_hook(buyer_row=row)
        self.assertEqual(len(lines), 1)
        out = json.loads(lines[0])
        self.assertEqual(out["stars"], 5)
        self.assertIn("claim honest", out["review"])
        self.assertNotIn("41234", out["review"])
        archived = json.loads((self.home / "memory" / "deals" / "o-5.json").read_text(encoding="utf-8"))
        self.assertEqual(archived["settled"]["ledger"], 812)
        rv = json.loads((self.home / "logs" / "decisions" / "review.jsonl").read_text(encoding="utf-8"))
        self.assertEqual((rv["role"], rv["choice"], rv["memory_refs"]), ("review", {"stars": 5}, ["d-buyer-9"]))
        self.assertEqual(rv["ref"], {"order": "o-5", "ledger": 812})

    def test_shipped_other_sku_than_agreed(self):
        row = {"id": "d-buyer-9", "kind": "order", "ref": {"order": "o-5"}, "choice": {"sku": "F2", "V": 30000}}
        out = json.loads(self.run_hook(buyer_row=row)[0])
        self.assertEqual(out["stars"], 1)
        self.assertIn("agreed F2", out["review"])

    def test_dishonest_claim(self):
        out = json.loads(self.run_hook(dict(SETTLED, grade="B", claim_honest=False))[0])
        self.assertEqual(out["stars"], 2)

    def test_error_prints_nothing(self):
        self.assertEqual(self.run_hook(raw="{broken"), [])
        self.assertIn("JSONDecodeError", (self.home / "logs" / "hooks" / "review.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
