"""Policy versions stay bounded; prices derive from cost, grade, age and policy."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from r3.market_files import Unit
from r3.policy import (
    Policy,
    SellerPolicy,
    apply_changes,
    load_history,
    load_policy,
    save_policy,
)
from r3.pricing import claimed_grade, counter, decide, quote
from r3.seller import card_warranty_line, warranty_words
from tests.r3_stock import CURTAIN, KIMCHI, LAMP, unit

if TYPE_CHECKING:
    from pathlib import Path


def test_missing_policy_is_defaults_and_saving_versions_keeps_two_previous(
    tmp_path: Path,
) -> None:
    # Given: no policy file yet.
    path = tmp_path / "r3-policy.json"
    assert load_policy(path) == Policy()
    # When: the manager publishes three changes.
    v2 = save_policy(path, apply_changes(Policy(), {"seller.markup_a": 0.9}), "r1")
    v3 = save_policy(path, apply_changes(v2, {"buyer.first_offer_ratio": 0.5}), "r2")
    v4 = save_policy(path, apply_changes(v3, {"restock.units_per_sku": 2}), "r3")
    # Then: the versions count up and only two previous versions are kept.
    assert (v2.version, v3.version, v4.version) == (2, 3, 4)
    history = load_history(path)
    assert history.current.restock.units_per_sku == 2
    assert [p.version for p in history.previous] == [3, 2]
    assert load_policy(path).seller.markup_a == 0.9


def test_changes_outside_bounds_or_unknown_fields_are_rejected() -> None:
    # Given/When/Then: bounds come from the policy model, not the proposer.
    with pytest.raises(ValidationError):
        _ = apply_changes(Policy(), {"seller.markup_a": 1.5})
    with pytest.raises(ValidationError):
        _ = apply_changes(Policy(), {"restock.reserve_cash": -1})
    with pytest.raises(ValueError, match="Unknown policy field"):
        _ = apply_changes(Policy(), {"wallet.budget": 1})
    with pytest.raises(ValueError, match="Unknown policy field"):
        _ = apply_changes(Policy(), {"seller": 1})


def test_quote_prices_from_cost_and_grade_and_never_below_accept_line() -> None:
    now = time.time()
    # The 40k lamp is a "cheap" good by default; this test prices it as a dear one.
    policy = Policy().seller.model_copy(update={"cheap_list_price": 0})
    a = Unit.model_validate(unit(LAMP, 1, grade="A", now=now))
    c = Unit.model_validate(unit(LAMP, 2, grade="C", now=now))
    qa, qc = quote(a, now, None, policy), quote(c, now, None, policy)
    # Then: A would ask 95% of list but the owner's star cap opens it at 79%; C asks
    # 70%; both accept at cost*(1+accept_margin).
    assert qa.ask == round(40000 * 0.79)
    assert qc.ask == round(40000 * 0.70)
    assert qa.accept_at == round(a.cost * 1.15)
    assert qc.accept_at == round(c.cost * 1.15)
    assert qa.ask >= qa.accept_at >= qa.floor
    # And: a counter moves toward the accept line and stops there.
    moved = counter(qa.ask, qa.accept_at, policy)
    assert qa.accept_at <= moved < qa.ask
    assert counter(qa.accept_at, qa.accept_at, policy) == qa.accept_at


def test_food_near_expiry_clears_below_cost_and_boundary_claims_are_omitted() -> None:
    now = time.time()
    policy = Policy().seller
    nearly_gone = Unit.model_validate(
        unit(KIMCHI, 1, grade="C", now=now, shelf_left_ratio=0.1)
    )
    cleared = quote(nearly_gone, now, 720, policy)
    assert cleared.clearance is True
    assert cleared.accept_at == cleared.floor < nearly_gone.cost
    assert cleared.grade_now == "C"
    # And: a food unit well inside A is claimed A; one about to drop carries no claim.
    inside = Unit.model_validate(
        unit(KIMCHI, 2, grade="A", now=now, shelf_left_ratio=0.805)
    )
    assert claimed_grade(inside, now, 720, policy) == "A"
    about_to_drop = Unit.model_validate(
        unit(KIMCHI, 3, grade="A", now=now, shelf_left_ratio=0.80003)
    )
    assert claimed_grade(about_to_drop, now, 720, policy) == ""


def test_decide_accepts_counters_then_rejects_after_max_rounds() -> None:
    policy = Policy().seller
    assert decide(1200, 1500, 1100, 0, policy) == ("accept", 1200)
    action, new_ask = decide(900, 1500, 1100, 1, policy)
    assert action == "counter"
    assert 1100 <= new_ask < 1500
    assert decide(900, 1500, 1100, policy.max_rounds, policy) == ("reject", 0)
    assert decide(None, 1500, 1100, 0, policy)[0] == "counter"


def test_star_cap_never_quotes_below_the_accept_line() -> None:
    now = time.time()
    policy = Policy().seller
    # Cheap A at cost 0.75 of list accepts at 0.81 of list in the rating pilot:
    # the ask stays above even the 0.79 cap rather than crossing the accept line.
    dear = Unit.model_validate(unit(LAMP, 3, grade="A", now=now, cost=30000))
    quoted = quote(dear, now, None, policy)
    assert quoted.accept_at == round(30000 * 1.08)
    assert quoted.ask == quoted.accept_at > round(40000 * 0.79)


def test_cheap_goods_open_at_the_lower_star_cap_and_dear_goods_at_079() -> None:
    now = time.time()
    policy = Policy().seller
    # A 40k lamp (under 50k) bought at 15k: opens at 0.70 of list, not 0.79.
    cheap = Unit.model_validate(unit(LAMP, 4, grade="A", now=now, cost=15000))
    assert quote(cheap, now, None, policy).ask == round(40000 * 0.70)
    # A 236,900 curtain is dear: it keeps the 0.79 cap.
    dear = Unit.model_validate(unit(CURTAIN, 1, grade="A", now=now, cost=100000))
    assert quote(dear, now, None, policy).ask == round(236900 * 0.79)


def test_grade_c_can_carry_its_own_accept_margin() -> None:
    now = time.time()
    base = Policy().seller.model_copy(update={"cheap_list_price": 0})
    c = Unit.model_validate(unit(LAMP, 5, grade="C", now=now))
    a = Unit.model_validate(unit(LAMP, 6, grade="A", now=now))
    assert quote(c, now, None, base).accept_at == round(c.cost * 1.15)
    trial = base.model_copy(update={"accept_margin_c": 0.35})
    assert quote(c, now, None, trial).accept_at == round(c.cost * 1.35)
    # And: other grades keep the shared margin.
    assert quote(a, now, None, trial).accept_at == round(a.cost * 1.15)


def test_a_week_long_warranty_is_allowed_and_named_without_digits() -> None:
    # Given: goods break only within 6 h of a sale, so a week costs nothing more.
    policy = SellerPolicy(warranty_a=72, warranty_b=72, warranty_c=72)
    # Then: the policy takes it, and quotes and the card name it digit-free.
    assert policy.warranty("C") == 72
    assert warranty_words(168) == "seven-day"
    assert warranty_words(72) == "three-day"
    assert warranty_words(24) == "twenty-four-hour"
    assert warranty_words(6) == "six-hour"
    assert warranty_words(8760) == "lifetime"
    assert SellerPolicy(warranty_a=72).warranty("A") == 72
    line = card_warranty_line(policy)
    assert "three-day warranty" in line
    assert not any(ch.isdigit() for ch in line)
    assert card_warranty_line(SellerPolicy(warranty_a=6, warranty_b=0)).startswith(
        "goods carry"
    )


def test_high_ticket_goods_accept_on_their_own_lower_margin() -> None:
    now = time.time()
    # Given: a curtain listed at 236,900 that arrived at 76 % of list.
    dear = Unit.model_validate(unit(CURTAIN, 7, grade="A", now=now, cost=180000))
    lamp = Unit.model_validate(unit(LAMP, 8, grade="A", now=now, cost=15000))
    c = Unit.model_validate(unit(CURTAIN, 9, grade="C", now=now, cost=80000))
    base = Policy().seller
    trial = base.model_copy(
        update={
            "high_list_price": 200000,
            "accept_margin_high": 0.08,
            "accept_margin_c": 0.35,
        }
    )
    # Then: the shared margin holds it above what the market pays...
    assert quote(dear, now, None, base).accept_at == round(180000 * 1.15)
    # ...and the high-ticket margin brings the accept line down to cost * 1.08.
    assert quote(dear, now, None, trial).accept_at == round(180000 * 1.08)
    # And: cheap A goods use the rating pilot margin, grade C keeps its own.
    assert quote(lamp, now, None, trial).accept_at == round(15000 * 1.08)
    assert quote(c, now, None, trial).accept_at == round(80000 * 1.35)


def test_an_old_version_that_no_longer_validates_does_not_reset_the_policy(
    tmp_path: Path,
) -> None:
    # Given: a live policy whose previous version breaks a tighter bound now.
    path = tmp_path / "r3-policy.json"
    live = apply_changes(Policy(), {"seller.max_ask_ratio": 0.76})
    _ = save_policy(path, live, "live")
    data = json.loads(path.read_text())
    old = dict(data["current"])
    old["seller"] = {**old["seller"], "warranty_a": 8760}
    data["previous"] = [old, *data["previous"]]
    path.write_text(json.dumps(data))
    # Then: the live values survive; only the bad old version is dropped.
    history = load_history(path)
    assert history.current.seller.max_ask_ratio == 0.76
    assert all(p.seller.warranty_a <= 72 for p in history.previous)
    assert load_policy(path).seller.max_ask_ratio == 0.76
