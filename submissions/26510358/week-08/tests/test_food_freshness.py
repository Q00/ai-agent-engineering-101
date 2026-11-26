"""2026-11-10: 183 food units / 2.42M expired in 72 h, mostly 24-48 h-shelf SKUs. Orders need 0.25/0.5/0.75 of the
shelf life, so markdowns and restock counts follow the share of shelf left, not absolute hours."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from r3lib import config, pricing
import restock_r3


def unit(hours_left, shelf=24, cost=6000):
    return {"cost": cost, "list_price": 10000, "kind": "food", "grade": "B", "sku": "Gx",
            "expires_at": time.time() + hours_left * 3600, "shelf_hours": shelf}


def test_short_shelf_food_is_dumped_before_orders_stop_taking_it():
    # 24 h shelf, 7 h left = 0.29 of shelf: under the old rule (6 h) it kept a full-margin floor
    assert pricing.unit_floor(unit(7), hours_left_round=200) == int(6000 * 0.3)
    assert pricing.unit_floor(unit(11), hours_left_round=200) == int(6000 * 0.8)      # 0.46 of shelf
    assert pricing.unit_floor(unit(20), hours_left_round=200) > 6000                  # 0.83: normal


def test_long_shelf_food_keeps_the_absolute_rules():
    assert pricing.unit_floor(unit(5, shelf=720), hours_left_round=200) == int(6000 * 0.3)
    assert pricing.unit_floor(unit(300, shelf=720), hours_left_round=200) > 6000


def test_food_without_shelf_info_falls_back_to_hours():
    u = unit(20)
    del u["shelf_hours"]
    assert pricing.unit_floor(u, hours_left_round=200) > 6000


def test_restock_does_not_count_food_no_order_can_take():
    cat = {"Gx": {"shelf_hours": 24}}
    assert not restock_r3.sellable_food(unit(5), cat)          # 0.21 of shelf
    assert restock_r3.sellable_food(unit(7), cat)
    assert restock_r3.sellable_food({"kind": "goods"}, cat)


def test_hot_premium_skus_get_more_units():
    assert restock_r3.premium_per_sku(0) == config.PREMIUM_PER_SKU                    # floor
    assert restock_r3.premium_per_sku(12 * 25) == max(config.PREMIUM_PER_SKU, 4)       # market 25/h -> 0.15 h of it
    assert restock_r3.premium_per_sku(12 * 100) == config.PREMIUM_HOT_MAX_PER_SKU      # capped
