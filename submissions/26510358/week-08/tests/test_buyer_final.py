"""A shop that calls its price final means it (of 2,591 finals in the logs 2,539 held, 98%): take it at once when it is
within our limit, walk at once when it is not, instead of nudging up for up to 6 more turns."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import buyer_r3
from r3lib import pricing


def test_a_final_price_within_the_limit_is_taken_at_once():
    # 0.78 of list, first turn: without "final" the rule counters (it accepts only <= 0.72 of list or from turn 3)
    assert pricing.buyer_next_offer(100_000, 80_000, 78_000, None, 0)[0] == "offer"
    assert pricing.buyer_next_offer(100_000, 80_000, 78_000, None, 0, final=True) == ("accept", 78_000)


def test_a_final_price_above_the_limit_is_left_at_once():
    assert pricing.buyer_next_offer(1_611_000, 1_115_244, 1_126_399, 1_006_224, 2, final=True) == ("walk", None)


def test_final_without_a_price_changes_nothing():
    assert pricing.buyer_next_offer(100_000, 80_000, None, 60_000, 1, final=True) == \
        pricing.buyer_next_offer(100_000, 80_000, None, 60_000, 1)


def test_final_wording_seen_in_the_logs():
    for text in ("Final price: 1126399.",
                 "Thanks for the offer. This is already the lowest price we can sell it for. This is our final price. "
                 "My price: 409140.",
                 "I appreciate it, but we are already at our floor price for this item.",
                 "Thanks for the offer, but we cannot go lower than this right now. My price: 17808"):
        assert buyer_r3.is_final(text), text
    for text in ("[산해직송] 해남 감자, grade C, no warranty. Send your offer as a number. Asking 8643.",
                 "Counteroffer: 52000. Grade A, 72 h warranty.",
                 "Deal: 의자 at 8643."):
        assert not buyer_r3.is_final(text), text


class FakeShop:
    """Answers every offer with the same final price; counts the turns."""
    def __init__(self, final):
        self.final, self.turns, self.cancelled = final, 0, False

    def _task(self, text):
        return {"id": "t1", "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                       "message": {"parts": [{"text": text}]}}}

    def send(self, text, task_id=None):
        self.turns += 1
        if task_id is None:
            return self._task(f"의자 (G1), grade A. Asking {self.final}. Send your offer as a number.")
        return self._task(f"Final price: {self.final}.")

    def cancel(self, task_id):
        self.cancelled = True

    def close(self):
        pass


def test_negotiate_walks_from_a_final_price_over_the_limit_after_one_counter(monkeypatch):
    shop = FakeShop(1_126_399)
    monkeypatch.setattr(buyer_r3.a2a, "Client", lambda url, timeout=60: shop)
    c = {"shop": "http://s", "item_name": "의자", "sku": "G1", "kind": "goods", "list": 1_611_000,
         "max_pay": 1_115_244, "budget": 2_000_000}
    rec, talk = buyer_r3.negotiate(c, {"id": "o1"}, deadline=10 ** 12)
    assert rec is None and shop.cancelled
    assert shop.turns == 2                     # the request and one counter; it was 7 before (6 creeping offers)
