"""Mature goods losses correct payment-only optimism without changing safety."""
# Assigned single test module owns SQLite and selection coverage.

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, Literal

import pytest
from pydantic import ValidationError

from r3 import buyer
from r3.buyer_report_db import update_facts
from r3.events import EventLedger
from r3.ledger_db import LedgerDB
from r3.market_files import CatalogItem, Order, PaymentLine, ShopEntry, ShopOffer
from r3.policy import BuyerPolicy, Policy, load_history, load_policy, save_policy
from tests.test_r3_big_orders import Peers, prepare, run_buy

if TYPE_CHECKING:
    from pathlib import Path

NOW = 200000.0
SALE = NOW - 22000
BAD = "https://bad.test"
GOOD = "https://good.test"


def setup_sources(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Create real indexed ledger and facts files with explicit goods catalog."""
    ledger = tmp_path / "ledger.sqlite"
    _ = LedgerDB(ledger)
    catalog = tmp_path / "catalog.json"
    _ = catalog.write_text(
        json.dumps(
            {
                "sku": {"kind": "goods", "list_price": 100},
                "food": {"kind": "food", "list_price": 100},
                "unknown": {"list_price": 100},
                "zero": {"kind": "goods", "list_price": 0},
            }
        )
    )
    return (
        ledger,
        tmp_path / "facts.sqlite",
        catalog,
        tmp_path / buyer.SAVED_FEEDBACK_FILE,
    )


def append_sale(  # noqa: PLR0913 - distinct evidence dimensions under test
    ledger: Path,
    seq: int,
    *,
    at: float = SALE,
    sku: str = "sku",
    kind: str = "settle",
    owner: str = "me",
    unit: str | None = "unit",
    shop: str = BAD,
    paid: int = 70,
) -> None:
    """Store the actual market event shape, not a mocked publication result."""
    _ = LedgerDB(ledger).ingest(
        [
            {
                "seq": seq,
                "ts": at,
                "kind": kind,
                "round": 3,
                "data": {
                    "buyer": owner,
                    "shop": shop,
                    "item": sku,
                    "amount": paid,
                    "unit": unit,
                    "order": f"o-{seq}",
                },
            }
        ]
    )


def sync_facts(ledger: Path, facts: Path) -> None:
    """Bring the production report cache to a fresh market tick."""
    _ = LedgerDB(ledger).ingest(
        [
            {
                "seq": 10000,
                "ts": NOW,
                "kind": "tick",
                "round": 3,
                "data": {},
            }
        ]
    )
    with closing(sqlite3.connect(facts)) as db:
        _ = update_facts(ledger, db, "me")


def publish(paths: tuple[Path, Path, Path, Path]) -> None:
    """Invoke the real source reader and atomic publisher."""
    ledger, facts, catalog, output = paths
    buyer.publish_saved_feedback(
        ledger,
        facts,
        catalog,
        "me",
        output,
        observed_at=NOW,
        ledger_latest_at=NOW,
        ledger_fresh=True,
        owner_control_until=NOW + 90000,
    )


def test_mature_loss_overcomes_many_paid_successes() -> None:
    # Given: equal price and stock, but the old payment evidence prefers bad.
    shops = tuple(
        ShopEntry(name=n, url=url, offers=(ShopOffer(sku="sku", qty=1),))
        for n, url in [("bad", BAD), ("good", GOOD)]
    )
    order = Order(order="one", kind="goods", acceptable=("sku",))
    catalog = {"sku": CatalogItem(sku="sku", list_price=100)}
    counts = {BAD: {"paid": 100}, GOOD: {"paid": 1}}
    baseline = buyer.choose(order, shops, catalog, counts, {}, surplus_score=True)
    assert baseline is not None
    assert baseline.shop.url == BAD
    # When: measured mature losses distinguish usefulness from payment success.
    selected = buyer.choose(
        order,
        shops,
        catalog,
        counts,
        {},
        surplus_score=True,
        mature_losses={
            BAD: buyer.SavedShopLoss(known_deals=100, loss_sum=20.0),
            GOOD: buyer.SavedShopLoss(known_deals=100, loss_sum=0.0),
        },
    )
    # Then: the lower-loss seller wins without making the other shop ineligible.
    assert selected is not None
    assert selected.shop.url == GOOD


@pytest.mark.parametrize(
    ("known", "loss", "expected"),
    [
        (19, 10.0, 1.0),
        (20, 2.0, 0.8),
        (100, 60.0, 0.5),
        (20, 0.0, 1.0),
    ],
)
def test_minimum_prior_and_floor(known: int, loss: float, expected: float) -> None:
    # Given: known mature loss and an independent .25 useful surplus proxy.
    losses = {BAD: buyer.SavedShopLoss(known_deals=known, loss_sum=loss)}
    # When: apply the evidence factor to a normalized shop URL.
    actual = buyer.saved_loss_factor(BAD + "/", losses, {BAD: 0.75})
    # Then: small samples stay neutral and the 20-observation prior shrinks loss.
    assert actual == pytest.approx(expected)


def test_unknown_shop_and_price_use_neutral_or_usual_prior() -> None:
    # Given: another shop's expensive price must not become our unknown price.
    losses = {BAD: buyer.SavedShopLoss(known_deals=20, loss_sum=2.8)}
    # When: score known loss without a matching historical price.
    factor = buyer.saved_loss_factor(BAD, losses, {GOOD: 0.99})
    # Then: USUAL_RATIO=.72 leaves .28 and shrunk loss=.07, giving .75.
    assert factor == pytest.approx(0.75)
    assert buyer.saved_loss_factor(GOOD, losses, {}) == 1.0


@pytest.mark.parametrize(
    ("kind", "enabled"), [("food", True), ("goods", False), (None, True)]
)
def test_other_selection_paths_unchanged(
    kind: Literal["food", "goods"] | None, *, enabled: bool
) -> None:
    # Given: a single stocked shop and losses large enough to halve goods weight.
    order = Order(kind=kind, acceptable=("sku",))
    shops = (ShopEntry(name="bad", url=BAD, offers=(ShopOffer(sku="sku", qty=1),)),)
    catalog = {"sku": CatalogItem(sku="sku", list_price=100)}
    baseline = buyer.choose(order, shops, catalog, {}, {}, surplus_score=enabled)
    # When: feedback reaches an excluded mode or kind.
    actual = buyer.choose(
        order,
        shops,
        catalog,
        {},
        {},
        surplus_score=enabled,
        mature_losses={BAD: buyer.SavedShopLoss(known_deals=100, loss_sum=50.0)},
    )
    # Then: all Choice fields remain identical, not only the selected URL.
    assert actual == baseline


@pytest.mark.parametrize(
    ("refund", "paid", "expected_loss"),
    [
        (False, 70, 1.3),
        (True, 70, 0.3),
        (70, 70, 0.3),
        (0, 70, 1.3),
        (False, 0, 2.0),
        (True, 0, 1.0),
    ],
)
def test_actual_break_refund_loss(
    tmp_path: Path, *, refund: bool | int, paid: int, expected_loss: float
) -> None:
    # Given: a .70-price good that actually breaks; refund status is explicit.
    paths = setup_sources(tmp_path)
    ledger, facts, _, output = paths
    append_sale(ledger, 1, paid=paid)
    _ = LedgerDB(ledger).ingest(
        [
            {
                "seq": 2,
                "ts": SALE + 100,
                "kind": "broken",
                "round": 3,
                "data": {"buyer": "me", "unit": "unit", "refunded": refund},
            }
        ]
    )
    sync_facts(ledger, facts)
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (ledger, facts)]
    # When: derive loss from actual settled-unit evidence.
    publish(paths)
    result = buyer.load_saved_feedback(output, NOW)
    # Then: refund earns credit0; unrefunded broken goods earn credit-1.
    assert result is not None
    assert result.shops[BAD].known_deals == 1
    assert result.shops[BAD].loss_sum == pytest.approx(expected_loss)
    assert [
        hashlib.sha256(p.read_bytes()).hexdigest() for p in (ledger, facts)
    ] == before


def test_unknown_unpaid_food_immature_and_other_buyer_not_learned(
    tmp_path: Path,
) -> None:
    # Given: only one healthy own good qualifies; all misleading evidence differs.
    paths = setup_sources(tmp_path)
    ledger, facts, _, output = paths
    append_sale(ledger, 1, unit="healthy")
    append_sale(ledger, 2, unit="unpaid", kind="order")
    append_sale(ledger, 3, unit="food", sku="food")
    append_sale(ledger, 4, unit="young", at=NOW - 100)
    append_sale(ledger, 5, unit="other", owner="other")
    append_sale(ledger, 6, unit="")
    append_sale(ledger, 7, unit="unknown-kind", sku="unknown")
    append_sale(ledger, 8, unit="zero-list", sku="zero")
    append_sale(ledger, 9, unit="unknown-refund")
    append_sale(ledger, 10, unit="partial-refund")
    append_sale(ledger, 11, unit="too-old", at=NOW - 21780 - 86401)
    for seq, unit, refund in [(12, "unknown-refund", None), (13, "partial-refund", 30)]:
        _ = LedgerDB(ledger).ingest(
            [
                {
                    "seq": seq,
                    "ts": SALE + 100,
                    "kind": "broken",
                    "round": 3,
                    "data": {"buyer": "me", "unit": unit, "refunded": refund},
                }
            ]
        )
    sync_facts(ledger, facts)
    # When: use real SQLite cache and indexed settlement window.
    publish(paths)
    result = buyer.load_saved_feedback(output, NOW)
    # Then: unknown evidence cannot inflate known sample size or incur a loss.
    assert result is not None
    assert result.shops == {BAD: buyer.SavedShopLoss(known_deals=1, loss_sum=0.0)}


@pytest.mark.parametrize(
    "problem", ["stale-ledger", "lagging-facts", "missing-facts", "malformed-catalog"]
)
def test_bad_sources_preserve_previous_feedback(tmp_path: Path, problem: str) -> None:
    # Given: a previous exact artifact and an unusable new source.
    paths = setup_sources(tmp_path)
    ledger, facts, catalog, output = paths
    append_sale(ledger, 1)
    sync_facts(ledger, facts)
    _ = output.write_text("previous-good-file")
    if problem == "stale-ledger":
        with closing(sqlite3.connect(ledger)) as db, db:
            _ = db.execute("UPDATE rows SET ts=? WHERE seq=10000", (NOW - 61,))
    elif problem == "lagging-facts":
        with closing(sqlite3.connect(facts)) as db, db:
            _ = db.execute("UPDATE report_cursor SET seq=1")
    elif problem == "missing-facts":
        facts.unlink()
    else:
        _ = catalog.write_text("{")
    # When: publication cannot establish fresh complete source coverage.
    publish(paths)
    # Then: it does not refresh an old result or replace it with invented zeros.
    assert output.read_text() == "previous-good-file"


@pytest.mark.parametrize(
    ("generated", "latest", "now", "maturity"),
    [
        (NOW, NOW, NOW + 3601, 21780),
        (NOW + 1, NOW, NOW, 21780),
        (NOW, NOW - 61, NOW, 21780),
        (NOW, NOW + 1, NOW, 21780),
        (NOW, None, NOW, 21780),
        (NOW, NOW, NOW, 21600),
        (float("nan"), NOW, NOW, 21780),
        (NOW, NOW, float("nan"), 21780),
    ],
)
def test_loader_rejects_unknown_or_stale_clock(
    tmp_path: Path, generated: float, latest: float | None, now: float, maturity: int
) -> None:
    # Given: malformed freshness metadata despite apparently useful loss rows.
    path = tmp_path / buyer.SAVED_FEEDBACK_FILE
    _ = path.write_text(
        json.dumps(
            {
                "generated_at": generated,
                "ledger_latest_at": latest,
                "maturity_seconds": maturity,
                "shops": {},
            }
        )
    )
    # When: the trading path reads the file.
    result = buyer.load_saved_feedback(path, now)
    # Then: legacy selection is the explicit fallback.
    assert result is None


@pytest.mark.parametrize(
    "raw",
    [
        '{"known_deals":-1,"loss_sum":0}',
        '{"known_deals":20,"loss_sum":41}',
        '{"known_deals":20,"loss_sum":NaN}',
        '{"known_deals":true,"loss_sum":0}',
        '{"known_deals":20,"loss_sum":-1}',
    ],
)
def test_invalid_loss_cannot_cross_boundary(raw: str) -> None:
    # Given / When: parse nonfinite, negative, boolean or impossible evidence.
    with pytest.raises(ValidationError):
        _ = buyer.SavedShopLoss.model_validate_json(raw)
    # Then: construction failed; it cannot reach the ranking formula.


@pytest.mark.parametrize("raw", [None, "{", "null", "[]"])
def test_missing_or_malformed_feedback_is_optional(
    tmp_path: Path, raw: str | None
) -> None:
    # Given: no usable evidence file.
    path = tmp_path / buyer.SAVED_FEEDBACK_FILE
    if raw is not None:
        _ = path.write_text(raw)
    # When / Then: the optional loader returns no adjustment safely.
    assert buyer.load_saved_feedback(path, NOW) is None


@pytest.mark.parametrize(
    ("surplus_enabled", "feedback_present", "expected_shop"),
    [
        (True, True, "http://good.test"),
        (False, True, "http://bad.test"),
        (True, False, "http://bad.test"),
        (False, False, "http://bad.test"),
    ],
)
def test_buy_loads_feedback_and_keeps_one_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    surplus_enabled: bool,
    feedback_present: bool,
    expected_shop: str,
) -> None:
    # Given: valid A72 quotes and paid evidence that otherwise favors bad.test.
    folder = prepare(tmp_path, ("bad.test", "good.test"))
    policy_file = tmp_path / "policy.json"
    _ = save_policy(
        policy_file,
        Policy(buyer=BuyerPolicy(surplus_score=surplus_enabled)),
        "real-history feedback fixture",
        expected_version=load_history(policy_file).current.version,
    )
    assert load_policy(policy_file).buyer.surplus_score is surplus_enabled
    ledger = EventLedger(tmp_path / "events.sqlite")
    for number in range(10):
        order = f"historical-paid-{number}"
        ledger.record_attempt(
            order=order,
            shop="http://bad.test",
            shop_name="Peer",
            sku="G00000004",
            list_price=236900,
            price=150000,
            outcome="agreed",
            market_round=3,
        )
        _ = ledger.sync_payments(
            [PaymentLine(order=order, shop="http://bad.test", price=150000, paid=True)]
        )
    if feedback_present:
        _ = (tmp_path / buyer.SAVED_FEEDBACK_FILE).write_text(
            buyer.SavedFeedback(
                generated_at=NOW,
                ledger_latest_at=NOW,
                shops={
                    "http://bad.test": buyer.SavedShopLoss(
                        known_deals=100, loss_sum=50.0
                    )
                },
            ).model_dump_json()
        )
    monkeypatch.setattr(buyer, "WALL_CLOCK", lambda: NOW)
    peers = Peers({})
    # When: actual buy loads policy/history and optionally consumes mature feedback.
    result = run_buy(folder, peers, monkeypatch)
    # Then: only the enabled, present feedback reverses the prior paid preference.
    assert result.shop == expected_shop
    assert result.receipt is not None
    assert peers.visits == [expected_shop.removeprefix("http://")]
    assert peers.offers == [expected_shop.removeprefix("http://")]


@pytest.mark.parametrize(
    ("age", "expected"),
    [(21779.0, 0), (21780.0, 0), (21781.0, 1), (108180.0, 1), (108181.0, 0)],
)
def test_exact_half_open_maturity_window(
    tmp_path: Path, age: float, expected: int
) -> None:
    # Given: one actual settlement at a precisely chosen cohort boundary.
    paths = setup_sources(tmp_path)
    ledger, facts, _, output = paths
    append_sale(ledger, 1, at=NOW - age)
    sync_facts(ledger, facts)
    # When: publish the [now-21780-86400, now-21780) cohort.
    publish(paths)
    result = buyer.load_saved_feedback(output, NOW)
    # Then: equal-end remains excluded; the included lower bound stays mature.
    assert result is not None
    assert sum(s.known_deals for s in result.shops.values()) == expected


def test_duplicate_units_do_not_inflate_sample_size(tmp_path: Path) -> None:
    # Given: two settlement rows claim the same physical unit.
    paths = setup_sources(tmp_path)
    ledger, facts, _, output = paths
    append_sale(ledger, 1)
    append_sale(ledger, 2)
    sync_facts(ledger, facts)
    # When: read the raw indexed settlements, not deduplicated paid counts.
    publish(paths)
    result = buyer.load_saved_feedback(output, NOW)
    # Then: ambiguous duplicates are excluded rather than trusted as two sales.
    assert result is not None
    assert result.shops == {}


@pytest.mark.parametrize(
    ("fresh", "latest"), [(False, NOW), (True, None), (True, NOW - 61), (True, NOW + 1)]
)
def test_stale_input_never_creates_feedback(
    tmp_path: Path, *, fresh: bool, latest: float | None
) -> None:
    # Given: freshness is unproven even before source IO.
    paths = setup_sources(tmp_path)
    ledger, facts, catalog, output = paths
    # When: invoke the publisher without current usable evidence.
    buyer.publish_saved_feedback(
        ledger,
        facts,
        catalog,
        "me",
        output,
        observed_at=NOW,
        ledger_latest_at=latest,
        ledger_fresh=fresh,
        owner_control_until=0,
    )
    # Then: there is no manufactured zero-loss update.
    assert not output.exists()


def test_partial_restore_disables_only_loss_ranking_and_keeps_feedback(
    tmp_path: Path,
) -> None:
    # Given: fresh valid learned evidence and an owner review lease.
    path = tmp_path / buyer.SAVED_FEEDBACK_FILE
    original = buyer.SavedFeedback(
        generated_at=NOW,
        ledger_latest_at=NOW,
        owner_control_until=NOW + 3600,
        shops={BAD: buyer.SavedShopLoss(known_deals=100, loss_sum=50.0)},
    ).model_dump_json()
    _ = path.write_text(original)
    assert buyer.load_saved_feedback(path, NOW) is not None
    # When: an operator withdraws this economic factor without rewriting policy.
    marker = tmp_path / buyer.SAVED_FEEDBACK_DISABLED_FILE
    _ = marker.write_text("owner partial restore")
    # Then: ranking is neutral while facts/control survive for audit and recovery.
    assert buyer.load_saved_feedback(path, NOW) is None
    assert path.read_text() == original
    marker.unlink()
    assert buyer.load_saved_feedback(path, NOW) is not None
