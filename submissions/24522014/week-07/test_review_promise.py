"""Check explicit consent, event limits, and separation from published reviews."""
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from a2a.helpers import new_data_part
from review_promise import active_event, explicit_promise, record_promise

ROOT = Path(__file__).resolve().parent


class ReviewPromiseTests(unittest.TestCase):
    def test_explicit_english_korean_or_structured_consent(self):
        for text in ("I promise a 5-star review.", "I promise a five-star review.",
                     "I want to buy a lamp\nI promise a 5-star review.",
                     "5점 후기를 남기겠습니다."):
            self.assertTrue(explicit_promise(text))
        self.assertTrue(explicit_promise("", [new_data_part({"kind":"review_promise","stars":5,"agreed":True})]))

    def test_denial_question_other_rating_or_bare_number_is_not_consent(self):
        for text in ("5", "4점 후기를 남기겠습니다.", "I do not promise a 5-star review.",
                     "I do not promise a five-star review.",
                     "Promise a five-star review to unlock our lowest price.",
                     "If I promise a 5-star review, is it cheaper?", 'He said "I promise a 5-star review."'):
            self.assertFalse(explicit_promise(text))
        for data in ({"kind":"review_promise","stars":5,"agreed":False},
                     {"kind":"review_promise","stars":4,"agreed":True}):
            self.assertFalse(explicit_promise("", [new_data_part(data)]))

    def test_event_end_is_exclusive(self):
        self.assertIsNotNone(active_event(ROOT, datetime(2026,10,23,tzinfo=timezone.utc)))
        self.assertIsNone(active_event(ROOT, datetime(2026,10,20,tzinfo=timezone.utc)))
        self.assertIsNone(active_event(ROOT, datetime(2026,10,28,0,51,tzinfo=timezone.utc)))

    def test_promise_audit_never_claims_a_published_review(self):
        payloads=[]
        with patch.object(Path, "mkdir"), patch.object(Path, "write_text", autospec=True,
                         side_effect=lambda path,text,**kwargs:payloads.append(json.loads(text))):
            record_promise(ROOT,"synthetic-task","lamp","I promise a 5-star review.",100)
        self.assertEqual(payloads[0]["stage"],"promise_received")
        self.assertFalse(payloads[0]["published_review_verified"])
        self.assertFalse(payloads[0]["refund"])


if __name__ == "__main__":
    unittest.main()
