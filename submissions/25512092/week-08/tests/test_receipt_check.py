"""depts/receipt_check.py on the real mismatched receipts (11/07-11/08) and on plain messages."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from depts.receipt_check import agreed, buyer_offer  # noqa: E402
from depts import receipt_check as _rc  # noqa: E402
_fn = _rc.first_number


def _after(t):
    return _fn(t, now=_rc.UNITS_FROM + 1)



def test_shop_name_number():
    t = "That is more than I want to pay. 25620027 quoted me 9313 for this. I can do 8257."
    assert agreed(t, 25620027, 10082)[0] == 8257


def test_competitor_quote():
    t = "That is more than I want to pay. inextro quoted me 76289 for this. I can do 64117."
    assert agreed(t, 76289, 76800) == (64117, agreed(t, 76289, 76800)[1]) and agreed(t, 76289, 76800)[1]


def test_typo_ten_times():
    assert agreed("420673", 420673, 42835)[0] == 42835


def test_plain_offer_unchanged():
    for t, p, a in [("8257", 8257, 9000), ("I can do 8257.", 8257, 9000), ("ok, 9950 then", 9950, 9930),
                    ("I offer 19,500 for G0dfa250d", 19500, 19016), ("8257 is my price, I need 360 hours", 8257, 9000)]:
        assert agreed(t, p, a) == (p, None), t


def test_sku_ignored():
    assert buyer_offer("G1f2acd72 at 28483?") == 28483


def test_hours_not_a_price():
    t = "Can you quote a qualifying unit with the market certificate? Need at least 540 hours of shelf life."
    assert buyer_offer(t) is None


def test_number_before_words():
    assert buyer_offer("8257 how about it?") == 8257
    assert buyer_offer("9000 hmm") == 9000


def test_korean():
    assert buyer_offer("540시간 필요, 8000원") == 8000
    assert buyer_offer("3개 9000") == 9000


def test_first_number_thousands():
    first_number = _after
    assert first_number("I can do 27,000.") == 27000
    assert first_number("27000") == 27000
    assert first_number("10,400 then") == 10400
    assert first_number("1,234,567") == 1234567
    assert first_number("27,0000") is None         # not a thousands group and not a plain number: no offer
    assert first_number("3 units, 9000") == 9000   # 11/08: a number with a unit is not the offer
    assert first_number("no number") is None


def test_first_number_units():
    first_number = _after
    assert first_number("Need at least 540 hours of shelf life. I can pay 12000") == 12000
    assert first_number("I need it to last 360.5 hours. 8000?") == 8000
    assert first_number("1 unit please, 15000") == 15000
    assert first_number("20% off? 9000") == 9000
    assert first_number("2개 3000") == 3000
    assert first_number("Need 180.0 hours") is None
    assert first_number("9000 hmm") == 9000 and first_number("8257 how about") == 8257


def test_korean_price_sentences():
    first_number = _after
    assert first_number("제가 가능한 금액은 6732입니다.") == 6732
    assert first_number("제가 드릴 수 있는 금액은 63942입니다.") == 63942
    assert first_number("Need at least 360.25 h remaining") is None


def test_units_gate_before_2105():
    assert _fn("Need at least 540 hours", now=_rc.UNITS_FROM - 1) == 540      # before: as the 14:06 seller reads it
    assert _fn("27,000", now=_rc.UNITS_FROM - 1) == 27000
    assert _fn("Need at least 540 hours", now=_rc.UNITS_FROM + 1) is None
