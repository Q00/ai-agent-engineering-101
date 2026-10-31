"""Kit v4 inputs pass our boundaries and the launcher wires every v4 hook."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from deploy.vps.bundle import build_context
from deploy.vps.launch import prepare
from deploy.vps.settings import LaunchSettings
from market_a2a.market_decision import MarketShop
from r3.models import CreateCheckout


def test_checkout_envelope_accepts_the_v4_acceptable_list_and_still_forbids_junk() -> (
    None
):
    receipt = {
        "shop": "S",
        "item": "G1",
        "price": 100,
        "task": "t",
        "sig_seller": "x",
        "unit": "u-1",
        "claim": "A",
        "warranty_hours": 6,
    }
    body = {
        "line_items": [{"id": "G1", "quantity": 1}],
        "receipt": receipt,
        "acceptable": ["G1", "G2"],
    }
    parsed = CreateCheckout.model_validate(body)
    assert parsed.acceptable == ("G1", "G2")
    assert (
        CreateCheckout.model_validate(
            {k: v for k, v in body.items() if k != "acceptable"}
        ).acceptable
        is None
    )
    with pytest.raises(ValidationError):
        _ = CreateCheckout.model_validate({**body, "coupon": "free"})
    with pytest.raises(ValidationError):
        _ = CreateCheckout.model_validate({**body, "acceptable": [1, 2]})


def test_shop_registry_entries_carry_v4_offers() -> None:
    shops = TypeAdapter(tuple[MarketShop, ...]).validate_json(
        json.dumps(
            [
                {
                    "name": "A",
                    "url": "http://a.test",
                    "reviews": [],
                    "offers": [{"sku": "G1", "name": "kimchi", "qty": 2}],
                },
                {"name": "B", "url": "http://b.test"},
            ]
        )
    )
    assert shops[0].offers[0].sku == "G1"
    assert shops[1].offers == ()


def test_launcher_writes_every_v4_hook_with_the_order_path(tmp_path: Path) -> None:
    # Given: a build context with the pinned kit and fake private identity files.
    app = tmp_path / "build"
    _ = build_context(Path(__file__).resolve().parents[1], app)
    private = tmp_path / "private"
    private.mkdir()
    for name in ("shop_key.pem", "buyer_key.pem"):
        _ = (private / name).write_text("fake", encoding="utf-8")
    settings = LaunchSettings(
        market_url="https://market.ourolabs.ai",
        public_url="https://shop.example.test",
        remaining_orders=0,
        stop_at=datetime.now(UTC) + timedelta(days=15),
        run_directory=tmp_path / "run",
        private_directory=private,
    )
    # When: the launcher prepares the run directory.
    prepare(settings, app)
    agent = json.loads(
        (settings.run_directory / "agent.json").read_text(encoding="utf-8")
    )
    # Then: the kit sees the order, restock and review hooks and unlimited orders.
    assert "--order '{order}'" in agent["market_buyer"]["cmd"]
    assert agent["restock"]["cmd"].endswith("restock --state '{state}'")
    assert agent["review"]["cmd"].endswith("review --deal '{deal}'")
    assert "--orders 0" in " ".join(settings.command(app))
    assert settings.seconds_remaining(datetime.now(UTC)) > 14 * 86400
