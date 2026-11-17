"""A faulty wallet and concurrent clients still face independent ledger checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio

from tests.r3_ledger import JSON
from tests.r3_payment import closed_payment
from tests.r3_scenario import scenario

if TYPE_CHECKING:
    import pytest


def test_verifier_rejects_above_cap_even_when_a_faulty_wallet_signs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: unlike real acp_pay, this faulty wallet signs100 against a90 mandate.
    async def run() -> None:
        async with scenario(monkeypatch) as scene:
            payment = await closed_payment(scene, budget=90)
            # When: bypass the payer's own cap check with valid buyer signatures.
            response = await scene.http.post(
                f"/acp/checkout_sessions/{payment['session']}/complete",
                json={"payment_data": payment["payment_data"]},
            )
            # Then: the independent local market layer refuses; no wallet changes.
            assert response.status_code == 402
            body = JSON.validate_json(response.content)
            market = JSON.validate_python(body["market"])
            assert JSON.validate_python(market["checks"])["amount_in_range"] is False
            assert scene.ledger.balances == {"buyer": 1000, "shop": 0}

    anyio.run(run)


def test_two_concurrent_completions_charge_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two clients race to complete the same correctly signed authorization.
    async def run() -> None:
        async with scenario(monkeypatch) as scene:
            payment = await closed_payment(scene)
            codes: list[int] = []

            async def complete() -> None:
                reply = await scene.http.post(
                    f"/acp/checkout_sessions/{payment['session']}/complete",
                    json={"payment_data": payment["payment_data"]},
                )
                codes.append(reply.status_code)

            # When: start both requests concurrently, rather than sequential replay.
            async with anyio.create_task_group() as group:
                _ = group.start_soon(complete)
                _ = group.start_soon(complete)
            # Then: one succeeds; the other is refused by the session or verifier.
            assert codes.count(200) == 1
            assert all(code in (200, 402, 409) for code in codes)
            assert len(scene.ledger.used_orders) == 1
            assert scene.ledger.balances == {"buyer": 900, "shop": 100}

    anyio.run(run)
