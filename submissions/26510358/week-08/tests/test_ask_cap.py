"""First ask stays within ASK_MAX_OVER_MARKET of the SKU's market ratio (2026-11-06 close-rate cliff)."""
from r3lib import config, pricing


def test_cap_lowers_ask_above_market_plus_room():
    ask = pricing.cap_ask(320_000, 400_000, 0.70, floor=200_000)
    assert ask == int(400_000 * (0.70 + config.ASK_MAX_OVER_MARKET))


def test_cap_keeps_ask_already_inside():
    assert pricing.cap_ask(290_000, 400_000, 0.70, floor=200_000) == 290_000


def test_cap_never_goes_below_floor():
    assert pricing.cap_ask(320_000, 400_000, 0.50, floor=300_000) == 300_000


def test_no_market_evidence_leaves_ask():
    assert pricing.cap_ask(320_000, 400_000, None, floor=200_000) == 320_000
