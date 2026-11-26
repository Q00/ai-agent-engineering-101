"""Patch 3.6 caps a deal's saved at 1 below cost, so opening under the estimated cost buys no score, only turns.
2026-11-14: our first offer was 0.55 of list for every kind and grade, under the market cost of A (0.618) and of
goods (0.594). A canary share of orders opens at the estimated cost instead."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import buyer_r3
from r3lib import config, pricing


def test_first_offer_opens_at_estimated_cost():
    act, p = pricing.buyer_next_offer(1_000_000, 900_000, None, None, 0, open_floor=0.618)
    assert (act, p) == ("offer", 618_000)


def test_open_floor_never_above_max_pay_or_ask():
    assert pricing.buyer_next_offer(1_000_000, 600_000, None, None, 0, open_floor=0.618)[1] == 600_000
    assert pricing.buyer_next_offer(1_000_000, 900_000, 580_000, None, 0, open_floor=0.618) == ("accept", 580_000)


def test_no_open_floor_keeps_old_opening():
    assert pricing.buyer_next_offer(1_000_000, 900_000, None, None, 0)[1] == 550_000


def test_ungraded_goods_use_goods_mix():
    assert buyer_r3.open_floor({"kind": "goods"}) == config.COST_RATIO["goods"]
    assert buyer_r3.open_floor({"kind": "goods", "hint": {"grade": "A"}}) == config.COST_RATIO["A"]
    assert buyer_r3.open_floor({"kind": "food"}) == config.COST_RATIO["food"]


def test_cost_open_arm_share_and_independent_of_fanout(monkeypatch):
    monkeypatch.setattr(config, "BUYER_FANOUT_SHARE", 0.5)
    ids = range(20_000)
    picks = [buyer_r3.cost_open_arm(i) for i in ids]
    assert abs(sum(picks) / len(picks) - config.BUYER_COST_OPEN_SHARE) < 0.03
    both = sum(a and buyer_r3.fanout_arm(i) for i, a in zip(ids, picks))
    assert abs(both / len(picks) - config.BUYER_COST_OPEN_SHARE * 0.5) < 0.03
