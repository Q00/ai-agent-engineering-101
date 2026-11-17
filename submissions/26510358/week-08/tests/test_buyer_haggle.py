"""Haggle canary (2026-11-15): a first ask within max_pay but under BUYER_HAGGLE_MS margin share is countered once;
the shop's next price within max_pay is then taken. 1,127 deals in 24 h took the first ask as-is."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import buyer_r3
from r3lib import config, pricing


def test_control_arm_takes_an_ask_within_max_pay():
    assert pricing.buyer_next_offer(100_000, 70_000, 66_000, None, 0, open_floor=0.6) == ("accept", 66_000)


def test_haggle_counters_a_first_ask_above_counter_below_then_takes_the_next():
    act, price = pricing.buyer_next_offer(100_000, 70_000, 66_000, None, 0, open_floor=0.6, counter_below=64_000,
                                          accept_after=1)
    assert act == "offer" and price == 60_000                    # opens at the estimated cost
    assert pricing.buyer_next_offer(100_000, 70_000, 63_000, 60_000, 1, counter_below=64_000,
                                    accept_after=1) == ("accept", 63_000)
    assert pricing.buyer_next_offer(100_000, 70_000, 66_000, 60_000, 1, counter_below=64_000,
                                    accept_after=1) == ("accept", 66_000)   # the shop did not move: still a deal


def test_haggle_takes_a_good_or_final_first_ask_at_once():
    assert pricing.buyer_next_offer(100_000, 70_000, 63_000, None, 0, counter_below=64_000,
                                    accept_after=1) == ("accept", 63_000)
    assert pricing.buyer_next_offer(100_000, 70_000, 66_000, None, 0, final=True, counter_below=64_000,
                                    accept_after=1) == ("accept", 66_000)


def test_haggle_arm_is_a_stable_share():
    picks = [buyer_r3.haggle_arm(f"o-{i:06x}") for i in range(4000)]
    assert abs(sum(picks) / len(picks) - config.BUYER_HAGGLE_SHARE) < 0.03
