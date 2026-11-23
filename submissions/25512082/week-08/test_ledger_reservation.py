"""Regression for 2026-11-10: a rejected or never-settled row locked a unit forever,
so every later agreement on the three held units ended "Duplicate or unrecordable trade"."""
import json
import tempfile
import unittest
from pathlib import Path

from ledger import LedgerError, TradeLedger
from seller import PENDING_RESERVATION_SECONDS, SafeSeller

PENDING = "a2a_agreed_pending_market_settlement"


def agreement(task: str, unit: str, at: float) -> dict:
    return {"transaction_id": task, "order_id": task, "unit_id": unit, "status": PENDING,
            "recorded_at": at, "rejection_reason": ""}


class LedgerReservationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "ledger.json"

    def ledger(self, rows: list[dict]) -> TradeLedger:
        self.path.write_text(json.dumps(rows), encoding="utf-8")
        return TradeLedger(self.path)

    def test_stale_pending_no_longer_blocks(self) -> None:
        led = self.ledger([agreement("t1", "u-1", 1_000.0)])
        led.append(agreement("t2", "u-1", 1_000.0 + PENDING_RESERVATION_SECONDS + 1),
                   unit_window=PENDING_RESERVATION_SECONDS, now=1_000.0 + PENDING_RESERVATION_SECONDS + 1)
        self.assertEqual(len(TradeLedger(self.path).rows), 2)

    def test_fresh_pending_still_blocks(self) -> None:
        led = self.ledger([agreement("t1", "u-1", 1_000.0)])
        with self.assertRaises(LedgerError):
            led.append(agreement("t2", "u-1", 1_010.0), unit_window=PENDING_RESERVATION_SECONDS, now=1_010.0)

    def test_old_rejected_row_with_real_unit_does_not_block(self) -> None:
        led = self.ledger([{"transaction_id": "t1", "order_id": "t1", "unit_id": "u-1",
                            "status": "rejected", "rejection_reason": "no_price_agreement"}])
        led.append(agreement("t2", "u-1", 2_000.0), unit_window=PENDING_RESERVATION_SECONDS, now=2_000.0)

    def test_transaction_id_stays_unique(self) -> None:
        led = self.ledger([agreement("t1", "u-1", 1_000.0)])
        with self.assertRaises(LedgerError):
            led.append(agreement("t1", "u-2", 9_999.0), unit_window=PENDING_RESERVATION_SECONDS, now=9_999.0)

    def test_default_behaviour_unchanged(self) -> None:
        led = self.ledger([agreement("t1", "u-1", 1_000.0)])
        with self.assertRaises(LedgerError):
            led.append(agreement("t2", "u-1", 9_999.0))

    def test_seller_failure_never_writes_real_unit_id(self) -> None:
        seller = SafeSeller(self.tmp, self.tmp / "key.pem", self.path)
        seller._failure("t9", "no_price_agreement", "u-7")
        row = TradeLedger(self.path).rows[-1]
        self.assertEqual(row["unit_id"], "none:t9")
        self.assertEqual(row["unit"], "u-7")
        events = [json.loads(x) for x in (self.tmp / "seller-events.jsonl").read_text().splitlines()]
        self.assertEqual(events[-1]["reason"], "no_price_agreement")


if __name__ == "__main__":
    unittest.main()
