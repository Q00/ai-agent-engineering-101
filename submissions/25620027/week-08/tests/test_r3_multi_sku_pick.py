"""Every ask gets the lowest-cost unit; food near expiry still goes first."""

from __future__ import annotations

from r3.market_files import Unit
from r3.seller import Request, pick_unit, required_shelf_hours

NOW = 1_794_560_000.0


def unit(name: str, sku: str, cost: int, hours_ago: float, grade: str = "A") -> Unit:
    return Unit(
        unit=name,
        sku=sku,
        category="가구/침구>가구>침대/매트리스",
        kind="goods",
        list_price=1_000_000,
        cost=cost,
        grade=grade,
        supplied_at=NOW - hours_ago * 3600,
    )


OLD_DEAR = unit("old", "G0000000a", 720_000, 60)
NEW_CHEAP = unit("new", "G0000000b", 600_000, 2)
C_CHEAPEST = unit("c", "G0000000b", 400_000, 1, grade="C")


def test_listing_buyer_gets_the_lowest_cost_share_unit() -> None:
    request = Request(
        ("G0000000a", "G0000000b"), None, "I want to buy G0000000a, G0000000b"
    )
    picked = pick_unit((OLD_DEAR, NEW_CHEAP, C_CHEAPEST), request, NOW, set())
    assert picked is NEW_CHEAP


def test_single_sku_buyer_gets_the_lowest_cost_unit_of_that_sku() -> None:
    older = unit("older", "G0000000a", 720_000, 60)
    newer = unit("newer", "G0000000a", 600_000, 2)
    request = Request(("G0000000a",), None, "I want to buy G0000000a")
    assert pick_unit((older, newer), request, NOW, set()) is newer


def test_food_close_to_expiry_still_goes_first() -> None:
    fresh = unit("fresh", "G0000000c", 300_000, 1).model_copy(
        update={"kind": "food", "expires_at": NOW + 400 * 3600}
    )
    soon = unit("soon", "G0000000c", 500_000, 100).model_copy(
        update={"kind": "food", "expires_at": NOW + 20 * 3600}
    )
    request = Request(("G0000000c",), None, "I want to buy G0000000c")
    assert pick_unit((fresh, soon), request, NOW, set()) is soon


def test_stated_shelf_life_picks_a_unit_that_lasts() -> None:
    short = unit("short", "G0000000d", 300_000, 5).model_copy(
        update={"kind": "food", "expires_at": NOW + 200 * 3600}
    )
    long = unit("long", "G0000000d", 500_000, 5).model_copy(
        update={"kind": "food", "expires_at": NOW + 500 * 3600}
    )
    asks = (
        "I want to buy x (G0000000d), it must last 361.0 hours",
        "I want to buy x (G0000000d) | need: shelf life 361.0 h or more",
        (
            "I want to buy x (G0000000d). I need it to last 360.0 hours: please "
            "ship a unit with at least 414.0 hours of shelf life left"
        ),
    )
    for text in asks:
        request = Request(("G0000000d",), None, text)
        assert pick_unit((short, long), request, NOW, set()) is long
    plain = Request(("G0000000d",), None, "I want to buy x (G0000000d)")
    assert pick_unit((short, long), plain, NOW, set()) is short


def test_no_unit_lasting_the_stated_shelf_life_means_no_offer() -> None:
    short = unit("short", "G0000000d", 300_000, 5).model_copy(
        update={"kind": "food", "expires_at": NOW + 264 * 3600}
    )
    text = "I want to buy x (G0000000d). Must last at least 540.0 hours."
    request = Request(("G0000000d",), None, text)
    assert pick_unit((short,), request, NOW, set()) is None
    # Goods carry no expiry and still fit any stated need.
    assert (
        pick_unit(
            (NEW_CHEAP,),
            Request(
                ("G0000000b",), None, "I want to buy G0000000b, must last 540 hours"
            ),
            NOW,
            set(),
        )
        is NEW_CHEAP
    )


def test_required_shelf_hours_reads_the_largest_need() -> None:
    assert (
        required_shelf_hours("It must last at least 74 hours (use within 72 h).") == 74
    )
    assert required_shelf_hours("I want to buy sofa (G1) with a 6 h warranty") is None
    policy_text = (
        'I want to buy x (G1). Required checkout policy: {"cert": true, '
        '"grades": ["A", "B", "C"], "min_hours_left": 126.05}'
    )
    assert required_shelf_hours(policy_text) == 126.05
    assert (
        required_shelf_hours(
            "Please quote your best price with market certificate (cert=true) and "
            "at least 180.25h shelf life."
        )
        == 180.25
    )
    assert (
        required_shelf_hours(
            "I need it to last 126 hours: please offer only a unit with at least "
            "126.5 hours of shelf life left"
        )
        == 126.5
    )


def test_plain_food_ask_prefers_a_fresh_unit_within_the_cost_band() -> None:
    near = unit("near", "G0000000e", 300_000, 5).model_copy(
        update={"kind": "food", "expires_at": NOW + 400 * 3600}
    )
    fresh = unit("fresh", "G0000000e", 320_000, 5).model_copy(
        update={"kind": "food", "expires_at": NOW + 600 * 3600}
    )
    dear_fresh = unit("dear", "G0000000e", 450_000, 5).model_copy(
        update={"kind": "food", "expires_at": NOW + 700 * 3600}
    )
    plain = Request(("G0000000e",), None, "I want to buy x (G0000000e)")
    assert pick_unit((near, fresh), plain, NOW, set()) is fresh
    # A much dearer unit does not jump ahead for freshness alone.
    assert pick_unit((near, dear_fresh), plain, NOW, set()) is near


def test_cost_floor_category_ask_gets_the_cheapest_unit_in_absolute_cost() -> None:
    dear = unit("dear", "G0000000x", 43_180, 5).model_copy(
        update={"list_price": 60_900, "category": "식품>육류>가공/조리 육류"}
    )
    cheap = unit("cheap", "G0000000y", 11_181, 5).model_copy(
        update={"list_price": 17_900, "category": "식품>육류>가공/조리 육류"}
    )
    text = "I want to buy a 가공/조리 육류 (buyer: q00). I'm a regular here."
    request = Request((), None, text)
    assert pick_unit((dear, cheap), request, NOW, set(), cheapest=True) is cheap
    # Without the flag the lowest cost share still wins (0.709 vs 0.625 here).
    assert pick_unit((dear, cheap), request, NOW, set()) is cheap
    dear_low_share = dear.model_copy(update={"cost": 30_000})
    assert pick_unit((dear_low_share, cheap), request, NOW, set()) is dear_low_share
    assert (
        pick_unit((dear_low_share, cheap), request, NOW, set(), cheapest=True) is cheap
    )


def test_q00_category_ask_gets_the_cheapest_unit_of_any_grade() -> None:
    # 11/15: q00 buys food C (434 of 809 food in 24 h); absolute cost decides.
    category = {"category": "식품>김치/반찬>김치"}
    a_dear = unit("a", "G0000000p", 20_000, 5).model_copy(update=category)
    c_cheap = unit("c", "G0000000q", 9_000, 5, grade="C").model_copy(update=category)
    text = "I want to buy a 김치 (buyer: q00). I'm a regular here."
    request = Request((), None, text)
    assert pick_unit((a_dear, c_cheap), request, NOW, set(), cheapest=True) is c_cheap
