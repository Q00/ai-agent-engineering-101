"""Emergency purchase utility, retry and hard owner price locks."""

from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter

from market_a2a.remote import Task
from r3 import buyer
from r3.emergency_buyer import (
    UNKNOWN_GRADE,
    PurchaseValue,
    discount_factor,
    receipt_value,
    says_final,
    warranty_share,
)
from r3.market_files import Order
from r3.policy import Band, Policy, apply_changes, load_history, save_policy
from tests.r3_stock import LAMP, write_market
from tests.test_r3_fast_buyer import SellerPeer
from tests.test_r3_manager_buyer_lock import managed

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("grade", "warranty", "ratio", "expected", "allowed"),
    [
        ("A", 0, 0.60, 0.3384, False),
        ("A", 0, 0.59, 0.34796, True),
        ("A", 72, 0.64, 0.34416, True),
        ("A", 72, 0.65, 0.3346, False),
        ("B", 0, 0.42, 0.37302, False),
        ("B", 72, 0.60, 0.3476, True),
        ("B", 72, 0.62, 0.33022, False),
        (None, 72, 0.40, None, False),
    ],
)
def test_expected_contribution(
    grade: str | None,
    warranty: int,
    ratio: float,
    expected: float | None,
    *,
    allowed: bool,
) -> None:
    value = PurchaseValue("goods", grade, warranty, 100000, int(ratio * 100000))
    assert (
        value.expected_credit() == pytest.approx(expected)
        if expected is not None
        else value.expected_credit() is None
    )
    assert value.acceptable() is allowed


def test_unknown_reference_and_receipt_warranty_downgrade() -> None:
    assert not PurchaseValue("goods", "A", 72, 0, 1).acceptable()
    prior = PurchaseValue("goods", "A", 72, 100000, 64000)
    actual = receipt_value(
        {"cert": {"cert": {"grade": "A"}}, "warranty_hours": 0}, prior
    )
    assert not actual.acceptable()
    assert PurchaseValue("food", None, 0, 100000, 66000).acceptable()


def test_shop_unknown_uses_observed_median_not_list_currency() -> None:
    assert discount_factor("new", {"a": 0.55, "b": 0.65}) == pytest.approx(0.40)
    assert discount_factor("a", {"a": 0.55, "b": 0.65}) == pytest.approx(0.45)


class QualityPeer(SellerPeer):
    """Expose the selected unit's grade before an offer and in its receipt."""

    def __init__(self, grade: str) -> None:
        """Set a grade on an otherwise identical seller."""
        super().__init__(17000, 17000, LAMP)
        self.grade = grade

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Return selected-unit certificate facts with the original reply."""
        response = super().handle(request)
        if request.method == "GET":
            return response
        outer = TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
        result = TypeAdapter(dict[str, JsonValue]).validate_python(outer["result"])
        task = Task.model_validate(result["task"])
        body = task.model_dump(mode="json", by_alias=True)
        # A protocol-shaped reply, not a production policy or marketplace write.
        cert = {"cert": {"unit": "u-1", "sku": LAMP, "grade": self.grade}}
        if task.status.state == "TASK_STATE_INPUT_REQUIRED":
            body["status"]["message"]["parts"].append({"data": {"cert": cert}})
        else:
            body["artifacts"][0]["parts"][0]["data"]["cert"] = cert
        return httpx2.Response(200, json={"result": {"task": body}})


def test_rejected_quality_retries_next_shop_without_committing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market = write_market(tmp_path / ".market", [])
    order = Order(
        order="emergency", item="lamp", budget=100000, acceptable=(LAMP,), kind="goods"
    )
    (market / "order.json").write_text(order.model_dump_json())
    (market / "shops.json").write_text(
        json.dumps(
            [
                {
                    "name": "bad",
                    "url": "http://bad.test",
                    "reviews": [{"stars": 5}],
                    "offers": [{"sku": LAMP, "qty": 1}],
                },
                {
                    "name": "good",
                    "url": "http://good.test",
                    "reviews": [{"stars": 4}],
                    "offers": [{"sku": LAMP, "qty": 1}],
                },
            ]
        )
    )
    policy = apply_changes(
        Policy(),
        {
            "buyer.surplus_score": False,
            "buyer.max_price_ratio": 0.66,
            "buyer.quick_accept_ratio": 0.58,
            "buyer.first_offer_ratio": 0.42,
            "buyer.dual_quote": False,
            "buyer.explore_rate": 0.0,
        },
    )
    save_policy(tmp_path / "policy.json", policy, "emergency")
    bad, good = QualityPeer("B"), QualityPeer("A")
    monkeypatch.setattr(
        buyer,
        "create_client",
        lambda: httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda r: (bad if r.url.host == "bad.test" else good).handle(r)
            )
        ),
    )
    logs: list[str] = []
    outcome = anyio.run(
        partial(
            buyer.buy,
            market / "shops.json",
            "lamp",
            100000,
            market / "order.json",
            logs.append,
            folder=market,
            policy_file=tmp_path / "policy.json",
        )
    )
    assert not bad.offers
    assert good.offers
    assert outcome.receipt is not None
    assert any("retry shop=good" in x for x in logs)


def test_owner_prices_and_bands_locked_without_feedback_lease(tmp_path: Path) -> None:
    with managed(tmp_path) as manager:
        before = load_history(manager.policy_file)
        fields = {
            "buyer.max_price_ratio": 0.68,
            "buyer.quick_accept_ratio": 0.58,
            "buyer.first_offer_ratio": 0.42,
            "buyer.late_accept_share": 0.9,
            "buyer.food_margin_hours": 4,
            "buyer.surplus_score": True,
        }
        assert manager.filter_tactical(fields, "code", "trigger cash_idle") == {}
        assert (
            manager.filter_model_bands(
                {"buyer.quick_accept_ratio": Band(low=0.55, high=0.62)}, "sonnet", "try"
            )
            == {}
        )
        assert load_history(manager.policy_file) == before


def test_line_price_counters_and_final_price_rules() -> None:
    # Given: the highest prices that still pass .34 for each quality case.
    a_cover = PurchaseValue("goods", "A", 72, 100000, 1).max_price()
    a_naked = PurchaseValue("goods", "A", 0, 100000, 1).max_price()
    b_naked = PurchaseValue("goods", "B", 0, 100000, 1).max_price()
    half = PurchaseValue("goods", "B", 72, 100000, 1, 0.5).max_price()
    unknown = PurchaseValue("goods", UNKNOWN_GRADE, 72, 100000, 1).max_price()
    # Then: each line passes, a credit more does not, and B without warranty never.
    assert a_cover is not None
    assert a_naked is not None
    assert PurchaseValue("goods", "A", 72, 100000, a_cover).acceptable()
    assert not PurchaseValue("goods", "A", 72, 100000, a_cover + 100).acceptable()
    assert a_naked <= 60000 < a_cover
    assert b_naked is None
    # A shop that refunds half its covered breaks protects half: a lower line.
    full = PurchaseValue("goods", "B", 72, 100000, 1).max_price()
    assert half is not None
    assert full is not None
    assert half < full
    # A quote without a grade prices at the A/B supply mix and is still bounded.
    assert unknown is not None
    assert a_cover > unknown > full
    # And: sellers' "final" wording is recognised, a plain counteroffer is not.
    assert says_final("Our final price is 4200.")
    assert says_final("No deal below 3000.")
    assert not says_final("Counteroffer: 4500.")


def test_quote_quality_does_not_make_an_unprotected_b_receipt_acceptable() -> None:
    # Given: a low-price quote with full grade-A warranty protection passes.
    quoted = PurchaseValue("goods", "A", 72, 100000, 40000)
    assert quoted.acceptable()
    # When: the actual receipt instead promises grade B and no warranty.
    actual = receipt_value(
        {"cert": {"cert": {"grade": "B"}}, "warranty_hours": 0}, quoted
    )
    # Then: even positive expected savings cannot waive the uncovered-B rule.
    assert actual.expected_credit() is not None
    assert not actual.acceptable()


def test_owner_credit_line_moves_the_price_line() -> None:
    # Given: the same covered A unit at the 0.34 and the 0.30 owner lines.
    strict = PurchaseValue("goods", "A", 72, 100000, 1).max_price()
    looser = PurchaseValue("goods", "A", 72, 100000, 1, 1.0, 0.30).max_price()
    food = PurchaseValue("food", None, 0, 100000, 1, 1.0, 0.30).max_price()
    # Then: a lower credit line lets the buyer pay more, and food tops at 0.70.
    assert strict is not None
    assert looser is not None
    assert looser > strict
    assert food is not None
    assert 69000 <= food <= 70000
    assert PurchaseValue("goods", "A", 72, 100000, looser, 1.0, 0.30).acceptable()
    assert not PurchaseValue("goods", "A", 72, 100000, looser, 1.0, 0.34).acceptable()


def test_warranty_share_reads_our_goods_receipts(tmp_path: Path) -> None:
    import sqlite3  # noqa: PLC0415 - local fixture database
    import time  # noqa: PLC0415

    db = tmp_path / "ledger.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "create table rows (ts real, kind text, buyer text, shop text, data text)"
    )
    now = time.time()
    rows = [
        ("https://a", {"warranty_hours": 72}),
        ("https://a", {"warranty_hours": 6}),
        ("https://a", {"warranty_hours": 0}),
        ("https://b", {}),
        ("https://b", {"warranty_hours": 3}),
        ("https://c", {"warranty_hours": 0, "expires_at": now + 99}),  # food
    ]
    for shop, data in rows:
        con.execute(
            "insert into rows values (?, 'settle', 'me', ?, ?)",
            (now - 60, shop, json.dumps(data)),
        )
    con.commit()
    con.close()
    shares, overall = warranty_share(db, "me", now)
    # Then: a = (2+1)/(3+2), b = (0+1)/(2+2); food rows do not count.
    assert shares["https://a"] == pytest.approx(0.6)
    assert shares["https://b"] == pytest.approx(0.25)
    assert "https://c" not in shares
    assert overall == pytest.approx((2 + 1) / (5 + 2))


def test_seller_receipt_claim_is_the_grade_when_no_certificate_rides_along() -> None:
    # Given: real sellers' receipts name the grade as "claim"; the kit adds the
    # certificate only at checkout (11/8: every goods receipt read as unknown).
    quote = PurchaseValue("goods", None, 0, 100000, 60000)
    a_receipt = {"claim": "A", "warranty_hours": 72.0, "price": 60000}
    c_receipt = {"claim": "C", "warranty_hours": 72.0, "price": 60000}
    # Then: an A unit with a 72 h warranty at 0.60 of list passes...
    assert receipt_value(a_receipt, quote).grade == "A"
    assert receipt_value(a_receipt, quote).acceptable()
    # ...and a C claim is still refused (grade C stays excluded).
    assert not receipt_value(c_receipt, quote).acceptable()
    # And: a certificate, when present, wins over the claim.
    certified = {"claim": "A", "cert": {"cert": {"grade": "B"}}, "warranty_hours": 72}
    assert receipt_value(certified, quote).grade == "B"


def test_our_price_walkaways_do_not_cool_a_shop(tmp_path: Path) -> None:
    from r3.events import EventLedger  # noqa: PLC0415 - local fixture

    ledger = EventLedger(tmp_path / "events.sqlite")
    for n in range(3):
        ledger.record_attempt(
            order=f"o-{n}",
            shop="http://cheap.test",
            shop_name="Cheap",
            sku="G1",
            list_price=100000,
            price=None,
            outcome="no_deal",
            reason="UnfitUnitError: seller stays above the emergency line 60000",
            market_round=3,
        )
    ledger.record_attempt(
        order="o-9",
        shop="http://down.test",
        shop_name="Down",
        sku="G1",
        list_price=100000,
        price=None,
        outcome="no_deal",
        reason="timed out",
        market_round=3,
    )
    streaks = ledger.failure_streaks(1.0)
    # Then: leaving on price is ours, a timeout is the shop's.
    assert "http://cheap.test" not in streaks
    assert streaks["http://down.test"][0] == 1
    assert ledger.shop_outcomes(1.0)["http://cheap.test"] == {"walked": 3}


class ClaimPeer(SellerPeer):
    """A seller that hides the grade in its quote and names it only in the receipt."""

    def __init__(self, claim: str, warranty: int) -> None:
        """Set the receipt's claimed grade and warranty hours."""
        super().__init__(17000, 17000, LAMP)
        self.claim, self.warranty = claim, warranty

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Rewrite only the receipt: claim and warranty as real sellers send them."""
        response = super().handle(request)
        if request.method == "GET":
            return response
        outer = TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
        result = TypeAdapter(dict[str, JsonValue]).validate_python(outer["result"])
        task = Task.model_validate(result["task"])
        body = task.model_dump(mode="json", by_alias=True)
        if task.status.state == "TASK_STATE_COMPLETED":
            data = body["artifacts"][0]["parts"][0]["data"]
            data["claim"] = self.claim
            data["warranty_hours"] = self.warranty
        return httpx2.Response(200, json={"result": {"task": body}})


def test_a_refused_receipt_moves_the_order_to_the_next_shop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market = write_market(tmp_path / ".market", [])
    order = Order(
        order="claim-c", item="lamp", budget=100000, acceptable=(LAMP,), kind="goods"
    )
    (market / "order.json").write_text(order.model_dump_json())
    (market / "shops.json").write_text(
        json.dumps(
            [
                {
                    "name": "hides-c",
                    "url": "http://hides.test",
                    "reviews": [{"stars": 5}],
                    "offers": [{"sku": LAMP, "qty": 1}],
                },
                {
                    "name": "honest-a",
                    "url": "http://honest.test",
                    "reviews": [{"stars": 4}],
                    "offers": [{"sku": LAMP, "qty": 1}],
                },
            ]
        )
    )
    policy = apply_changes(
        Policy(),
        {
            "buyer.surplus_score": False,
            "buyer.max_price_ratio": 0.70,
            "buyer.quick_accept_ratio": 0.64,
            "buyer.first_offer_ratio": 0.42,
            "buyer.dual_quote": False,
            "buyer.explore_rate": 0.0,
        },
    )
    save_policy(tmp_path / "policy.json", policy, "emergency")
    hides, honest = ClaimPeer("C", 0), ClaimPeer("A", 72)
    monkeypatch.setattr(
        buyer,
        "create_client",
        lambda: httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda r: (hides if r.url.host == "hides.test" else honest).handle(r)
            )
        ),
    )
    logs: list[str] = []
    outcome = anyio.run(
        partial(
            buyer.buy,
            market / "shops.json",
            "lamp",
            100000,
            market / "order.json",
            logs.append,
            folder=market,
            policy_file=tmp_path / "policy.json",
        )
    )
    # Then: the C receipt is refused, unpaid, and the order closes at the next shop.
    assert hides.offers
    assert outcome.shop == "http://honest.test"
    assert outcome.receipt is not None
    assert any("claim C" in line and "retry shop=honest-a" in line for line in logs)


def test_category_receipt_is_judged_on_the_cheapest_sku_the_shop_listed() -> None:
    from r3.market_files import CatalogItem, ShopEntry, ShopOffer  # noqa: PLC0415

    # Given: the 11/8 15:38 case: a juice order asked for G78487d68 (list 25,100),
    # agreed 14,809, and the receipt named only the category; the shop also
    # listed G9476605f (list 19,700) and shipped it.
    catalog = {
        "G78487d68": CatalogItem(sku="G78487d68", list_price=25100, kind="food"),
        "G9476605f": CatalogItem(sku="G9476605f", list_price=19700, kind="food"),
    }
    shop = ShopEntry(
        name="juice",
        url="http://juice.test",
        offers=(ShopOffer(sku="G78487d68", qty=1), ShopOffer(sku="G9476605f", qty=1)),
    )
    order = Order(
        order="o-juice",
        item="주스/코코아/생수",
        budget=30000,
        acceptable=("G78487d68", "G9476605f"),
        kind="food",
    )
    choice = buyer.Choice(shop, "G78487d68", "juice", 25100, "I want to buy", 1.0)
    policy = Policy().buyer.model_copy(
        update={"max_price_ratio": 0.70, "min_expected_credit": 0.28}
    )
    category = {"item": "주스/코코아/생수", "price": 14809}
    # Then: worst case 14,809 / 19,700 = 0.752 is above the 0.70 cap: refused.
    _, problem = buyer.check_receipt_value(
        category, order, choice, catalog, 14809, policy, {}
    )
    assert problem is not None
    assert "above 0.70 of the shipped list 19700" in problem
    # And: naming the cheaper SKU outright is judged the same way...
    named = {"item": "G9476605f", "price": 14809}
    _, problem = buyer.check_receipt_value(
        named, order, choice, catalog, 14809, policy, {}
    )
    assert problem is not None
    # ...the asked SKU at the same price passes (0.59 of 25,100)...
    asked = {"item": "G78487d68", "price": 14809}
    assert (
        buyer.check_receipt_value(asked, order, choice, catalog, 14809, policy, {})[1]
        is None
    )
    # ...and a category receipt from a shop that listed nothing is refused.
    bare = buyer.Choice(
        ShopEntry(name="x", url="http://x.test"), "G78487d68", "j", 25100, "r", 1.0
    )
    assert (
        buyer.check_receipt_value(category, order, bare, catalog, 14809, policy, {})[1]
        is not None
    )


def test_goods_receipt_without_claim_or_certificate_prices_at_the_ab_mix() -> None:
    quote = PurchaseValue("goods", None, 0, 100000, 60000)
    bare = {"warranty_hours": 72.0, "price": 60000}
    # Then: the kit pays only certified A or B at checkout, so "?" applies.
    assert receipt_value(bare, quote).grade == UNKNOWN_GRADE
    assert receipt_value(bare, quote).acceptable()


def test_a_shop_that_walled_us_on_a_sku_ranks_low_for_that_sku(tmp_path: Path) -> None:
    from r3.events import EventLedger  # noqa: PLC0415 - local fixture
    from r3.market_files import CatalogItem, ShopEntry, ShopOffer  # noqa: PLC0415

    ledger = EventLedger(tmp_path / "events.sqlite")
    for n in range(2):
        ledger.record_attempt(
            order=f"o-{n}",
            shop="http://wall.test",
            shop_name="Wall",
            sku=LAMP,
            list_price=100000,
            price=None,
            outcome="no_deal",
            reason="UnfitUnitError: seller stays above the emergency line 60000",
            market_round=3,
        )
    walls = ledger.wall_offers(2.0)
    assert walls == {("http://wall.test", LAMP): 2}
    weight = buyer.wall_weighter(walls)
    assert weight("http://wall.test/", LAMP) == buyer.WALL_WEIGHT
    assert weight("http://wall.test", "G-other") == 1.0
    # Given: two shops listing the lamp, the wall shop otherwise preferred (5 stars).
    catalog = {LAMP: CatalogItem(sku=LAMP, list_price=100000, kind="goods")}
    shops = (
        ShopEntry(
            name="wall",
            url="http://wall.test",
            reviews=({"stars": 5},),
            offers=(ShopOffer(sku=LAMP, qty=1),),
        ),
        ShopEntry(
            name="open",
            url="http://open.test",
            reviews=({"stars": 3},),
            offers=(ShopOffer(sku=LAMP, qty=1),),
        ),
    )
    order = Order(
        order="o", item="lamp", budget=100000, acceptable=(LAMP,), kind="goods"
    )
    plain = buyer.choose(order, shops, catalog, {}, {})
    ranked = buyer.choose(order, shops, catalog, {}, {}, offer_weight=weight)
    assert plain is not None
    assert plain.shop.name == "wall"
    assert ranked is not None
    assert ranked.shop.name == "open"
    # And: a walled shop is still taken when it is the only one listing the SKU.
    alone = buyer.choose(order, shops[:1], catalog, {}, {}, offer_weight=weight)
    assert alone is not None
    assert alone.shop.name == "wall"
