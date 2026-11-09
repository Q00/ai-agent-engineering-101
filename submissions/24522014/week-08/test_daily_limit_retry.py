"""The daily attempt limit counts first attempts only. A retry is the single named second attempt for an order already tried,
so it never uses up a first attempt and is not refused by the daily limit. On 11-08 UTC the 1,070 retries counted toward the
4,320 cap, which was reached at 07:17:59 KST and left 287 orders untried until 09:00 KST."""

from datetime import datetime, timezone
import unittest

import buyer_support as support

DAY = datetime(2026, 11, 10, tzinfo=timezone.utc).timestamp()
SETTINGS = {**support.config(), "enabled": True, "mode": "balanced", "max_attempts_per_utc_day": 4320, "min_attempt_interval_seconds": 10}


def state(first, retries, start=DAY):
    """`first` first attempts 10 s apart, then `retries` retries of the earliest of them, each naming its first shop."""
    attempts = [{"at": start + 10 * i, "order": f"o{i}", "shop": f"s{i % 7}", "url": "u", "sku": "G1", "cap": 1, "rival_at_selection": False}
                for i in range(first)]
    attempts += [{"at": start + 10 * i + 1, "order": f"o{i}", "shop": "r", "url": "u", "sku": "G1", "cap": 1, "rival_at_selection": False,
                  "retry_of": f"s{i % 7}"} for i in range(retries)]
    return {"schema": 1, "round": 3, "attempts": attempts}


class DailyLimitCountsFirstAttempts(unittest.TestCase):
    def test_a_full_day_of_first_attempts_refuses_a_new_first_attempt(self):
        for retries in (0, 1, 1070):
            with self.subTest(retries=retries):
                s = state(4320, retries)
                now = DAY + 10 * 4320 + 60
                self.assertEqual(support.availability(s, "new", SETTINGS, now), "daily_attempt_limit")

    def test_retries_never_use_up_a_first_attempt(self):
        s = state(4319, 1000)
        now = DAY + 10 * 4319 + 60
        self.assertEqual(support.availability(s, "new", SETTINGS, now), "available")
        s["attempts"].append({"at": now, "order": "new", "shop": "a", "url": "u", "sku": "G1", "cap": 1, "rival_at_selection": False})
        self.assertEqual(support.availability(s, "newer", SETTINGS, now + 60), "daily_attempt_limit")

    def test_the_retry_of_an_order_already_tried_is_outside_the_limit(self):
        s = state(4320, 0)
        now = DAY + 10 * 4319 + 30
        self.assertEqual(support.availability(s, "o4319", SETTINGS, now, retry_of="s" + str(4319 % 7)), "available")

    def test_a_retry_is_still_only_the_single_named_second_attempt(self):
        s = state(10, 1)
        now = DAY + 400
        self.assertEqual(support.availability(s, "unknown", SETTINGS, now, retry_of="s0"), "duplicate_order")    # no first attempt
        self.assertEqual(support.availability(s, "o0", SETTINGS, now, retry_of="s0"), "duplicate_order")         # already retried
        self.assertEqual(support.availability(s, "o1", SETTINGS, now, retry_of="elsewhere"), "duplicate_order")  # names another shop
        self.assertEqual(support.availability(s, "o1", SETTINGS, now, retry_of="s1"), "available")
        self.assertEqual(support.availability(s, "o1", SETTINGS, now), "duplicate_order")                         # a second first attempt

    def test_yesterdays_first_attempts_do_not_count(self):
        s = state(4320, 500, start=DAY - 86400)
        self.assertEqual(support.availability(s, "new", SETTINGS, DAY + 5), "available")

    def test_the_bound_and_the_interval_are_unchanged(self):
        self.assertEqual(support.config()["max_attempts_per_utc_day"], 4320)
        s = state(3, 0)
        self.assertEqual(support.availability(s, "new", SETTINGS, DAY + 25), "attempt_cooldown")


if __name__ == "__main__":
    unittest.main()
