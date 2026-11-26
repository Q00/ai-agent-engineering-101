"""11/17 17:16 (owner): a repeated price wall ends at the first ask."""

from __future__ import annotations

from r3.buyer import REPEAT_WALL_SECONDS, remember_wall, repeat_wall


def test_repeat_wall_ends_only_on_a_first_ask_not_below_the_last_one() -> None:
    key = ("https://shop.test", "G1")
    assert not repeat_wall(key, 100, 1000.0)
    remember_wall(key, 100, 1000.0)
    assert repeat_wall(key, 100, 1500.0)
    assert repeat_wall(key, 120, 1500.0)
    assert not repeat_wall(key, 99, 1500.0)  # the seller came down: talk as usual
    assert not repeat_wall(key, 100, 1000.0 + REPEAT_WALL_SECONDS + 1)
    assert not repeat_wall(key, 100, 1600.0)  # expired entry was dropped
