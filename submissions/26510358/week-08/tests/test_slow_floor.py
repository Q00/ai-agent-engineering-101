"""2026-11-10: 67 A chairs (13.4M, cost 0.675-0.73 of list) had floor = first ask = 0.81 of list against a 0.68
market. /rules: unsold stock counts at cost when the round ends and pays 1% of cost a day, so a slow unit held
48 h may go down to cost minus the storage still to pay; above that, selling now beats holding."""
import time

from r3lib import config, pricing

H = 3600


def chair(cost, age_h):
    return {"kind": "goods", "grade": "A", "cost": cost, "list_price": 296_000, "supplied_at": time.time() - age_h * H}


def test_slow_aged_goods_go_down_to_cost_minus_storage_left():
    assert pricing.unit_floor(chair(200_000, 60), hours_left_round=168, slow=True) == int(200_000 * (1 - 0.01 * 7))
    assert pricing.unit_floor(chair(200_000, 60), hours_left_round=0, slow=True) == 200_000


def test_young_units_food_and_not_slow_keep_the_normal_floor():
    normal = int(200_000 * (1 + config.MIN_MARGIN))
    assert pricing.unit_floor(chair(200_000, 10), hours_left_round=168, slow=True) == normal
    assert pricing.unit_floor(chair(200_000, 60), hours_left_round=168) == normal
    food = {"kind": "food", "cost": 6000, "list_price": 10_000, "supplied_at": time.time() - 60 * H}
    assert pricing.unit_floor(food, hours_left_round=168, slow=True) == int(6000 * (1 + config.MIN_MARGIN))


def test_only_units_that_cannot_reach_their_target_price_get_the_low_floor():
    a = chair(int(296_000 * 0.70), 60)          # cost + 8% = 0.756 > market 0.68 - 0.03
    b = chair(int(296_000 * 0.505), 60)         # cost + 8% = 0.545 < market 0.62 - 0.03
    assert pricing.needs_slow_floor(a, True, 0.68)
    assert not pricing.needs_slow_floor(b, True, 0.62)
    assert not pricing.needs_slow_floor(a, False, 0.68)
    assert not pricing.needs_slow_floor(a, True, None)


def test_warranty_refund_is_added_on_top_of_the_slow_floor():
    u = chair(200_000, 60)
    low = pricing.unit_floor(u, hours_left_round=168, slow=True)
    assert pricing.warranty_floor(low, u, 200_000, 72, 0.03, slow=True) == low + 6000
    normal = pricing.unit_floor(u, hours_left_round=168)
    assert pricing.warranty_floor(normal, u, 200_000, 72, 0.03) == normal + 6000
