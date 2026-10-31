"""Regressions for the five boundary defects found in the 2026-10-31 independent review.

Each test reproduces the reviewer's probe and pins the corrected behaviour: honest
food claims, no receipt the buyer never agreed to, cash reserve checked at the
official worst-case cost, a manager that survives a malformed tool payload, and a
quality cohort old enough to have matured.
"""

from __future__ import annotations

import json
import time
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Self

import anyio
import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts import market_kit as kit

from market_a2a.receipts import ReceiptSigner
from r3 import buyer, manager, restock
from r3.ledger_db import LedgerDB
from r3.market_files import DemandRow, Inventory, Unit, read_catalog
from r3.policy import Policy, apply_changes, load_policy
from r3.pricing import claimed_grade
from tests.r3_stock import KIMCHI, LAMP, unit, write_market

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_food_claim_is_the_grade_that_holds_at_checkout_and_settlement() -> None:
    # Given: 720 h kimchi at 80.5% shelf life: grade A now and for 3.6 more hours.
    now = time.time()
    stocked = Unit.model_validate(unit(KIMCHI, 1, now=now, shelf_left_ratio=0.805))
    policy = Policy().seller
    # Then: the claim is A, not a "safe" B that would match neither moment.
    assert claimed_grade(stocked, now, 720, policy) == "A"
    # And: a unit whose grade flips within the horizon carries no claim at all
    # (0.80003 of 720 h is 78 s above the A cut; the horizon is 180 s).
    edge = Unit.model_validate(unit(KIMCHI, 2, now=now, shelf_left_ratio=0.80003))
    assert edge.current_food_grade(now, 720) == "A"
    assert edge.current_food_grade(now + policy.claim_horizon_seconds, 720) == "B"
    assert claimed_grade(edge, now, 720, policy) == ""
    # And: goods keep their fixed grade.
    assert (
        claimed_grade(Unit.model_validate(unit(LAMP, 1, grade="B")), now, None, policy)
        == "B"
    )


def test_buyer_refuses_a_completed_receipt_it_never_offered_or_above_its_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a shop that answers the first message with a signed 59,000 receipt
    # (valid for the kit, under the 60,000 mandate) although we never named a price.
    folder = write_market(tmp_path / ".market", [])
    _ = (folder / "order.json").write_text(
        json.dumps(
            {
                "order": "o-probe",
                "item": "kimchi",
                "budget": 60000,
                "acceptable": [KIMCHI],
                "kind": "food",
                "use_within_hours": 24,
            }
        ),
        encoding="utf-8",
    )
    _ = (folder / "shops.json").write_text(
        json.dumps(
            [
                {
                    "name": "Peer",
                    "url": "http://peer.test",
                    "reviews": [],
                    "offers": [{"sku": KIMCHI, "name": "kimchi", "qty": 1}],
                }
            ]
        ),
        encoding="utf-8",
    )
    key = Ed25519PrivateKey.generate()
    signed = ReceiptSigner(key).sign(
        shop="Peer", item=KIMCHI, price=59000, task="auto-1"
    )
    receipt = {
        "shop": "Peer",
        "item": KIMCHI,
        "price": 59000,
        "task": "auto-1",
        "sig_seller": signed.sig_seller,
    }
    assert kit.verify_seller(kit.pub_b64(key), receipt)
    sent: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(
                200,
                json={
                    "name": "Peer",
                    "skills": [],
                    "supportedInterfaces": [
                        {
                            "url": "http://peer.test/rpc",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                },
            )
        sent.append(
            json.loads(request.content)["params"]["message"]["parts"][0]["text"]
        )
        return httpx2.Response(
            200,
            json={
                "result": {
                    "task": {
                        "id": "auto-1",
                        "contextId": "c-1",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [{"parts": [{"data": receipt}]}],
                    }
                }
            },
        )

    monkeypatch.setattr(
        buyer,
        "create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
    )
    outcome = anyio.run(
        partial(
            buyer.buy,
            folder / "shops.json",
            "kimchi",
            60000,
            folder / "order.json",
            lambda _m: None,
            folder=folder,
            policy_file=tmp_path / "policy.json",
            events_file=tmp_path / "events.sqlite",
        )
    )
    # Then: only the product request went out and the receipt is not forwarded.
    assert len(sent) == 1
    assert not sent[0].isdigit()
    assert outcome.receipt is None
    assert outcome.agreed_price is None
    assert "without an offer" in outcome.reason
    # And: even with an agreed price, a receipt above the buyer's own cap is refused.
    cap = buyer.price_cap(
        buyer.Choice(
            buyer.ShopEntry(name="P", url="http://p"), KIMCHI, "k", 52000, "r", 1.0
        ),
        60000,
        Policy().buyer,
    )
    assert cap == int(52000 * 0.95)
    order = buyer.Order(order="o", item="kimchi", budget=60000, acceptable=(KIMCHI,))
    choice = buyer.Choice(
        buyer.ShopEntry(name="P", url="http://p"), KIMCHI, "k", 52000, "r", 1.0
    )
    assert "cap" in str(buyer.valid_receipt(receipt, order, choice, 59000, cap))
    assert (
        buyer.valid_receipt({**receipt, "price": 49000}, order, choice, 49000, cap)
        is None
    )


def test_restock_quantity_is_cut_to_what_the_reserve_allows_at_worst_case_cost(
    tmp_path: Path,
) -> None:
    # Given: a valid policy asking for 20 lamps with 500,000 above the cash reserve.
    folder = write_market(tmp_path / ".market", [])
    catalog = read_catalog(folder)
    policy = apply_changes(Policy(), {"restock.units_per_sku": 20}).restock
    inventory = Inventory(round=3, balance=2_000_000)
    demand = (DemandRow(category=catalog[LAMP].category, item="lamp", kind="goods"),)
    lines, state = restock.plan(
        inventory, catalog, demand, policy, restock.RestockState(), time.time()
    )
    # Then: 16 units at the official worst case (40,000 * 0.75 = 30,000) fit; 20 do not.
    assert lines == [{"sku": LAMP, "qty": 16}]
    assert state.planned_cost == 16 * 30_000 <= inventory.balance - policy.reserve_cash
    # And: the model has no field that could lower the enforcement cost share.
    assert "cost_estimate_ratio" not in Policy().restock.model_dump()


def test_manager_survives_a_non_object_tool_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a fake SDK whose set_policy arguments are valid JSON but not an object.
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            type="function",
                            function=SimpleNamespace(name="set_policy", arguments="[]"),
                        )
                    ]
                )
            )
        ]
    )

    class FakeClient:
        def __init__(self, **_: object) -> None:
            self.chat = SimpleNamespace(completions=self)

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def create(self, **_: object) -> SimpleNamespace:
            return completion

    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-placeholder")
    monkeypatch.setenv("AX_R3_MANAGER_MODEL", "1")
    monkeypatch.delenv("AX_PAID_QUOTA_PATH", raising=False)
    monkeypatch.setattr(manager, "AsyncOpenAI", FakeClient)
    instance = manager.Manager(
        tmp_path,
        "http://127.0.0.1:9",
        shop_url="http://ours.test",
        buyer_name="25620027",
        shop_name="Ours",
    )
    try:
        # Then: no exception, no policy change, policy file still the defaults.
        assert anyio.run(partial(instance.ask_model, {})) is None
        assert load_policy(instance.policy_file) == Policy()
        # And: an oversized or unknown change set is refused the same way.
        completion.choices[0].message.tool_calls[0].function.arguments = json.dumps(
            {"changes": {"wallet.budget": 1}, "reason": "no"}
        )
        assert anyio.run(partial(instance.ask_model, {})) is None
    finally:
        instance.http.close()


def test_six_hour_summary_reports_break_rates_from_a_matured_cohort(
    tmp_path: Path,
) -> None:
    # Given: a sale 7 h ago whose good broke 6.5 h ago.
    now = time.time()
    db = LedgerDB(tmp_path / "ledger.sqlite")
    _ = db.ingest(
        [
            {
                "seq": 1,
                "ts": now - 7 * 3600,
                "kind": "settle",
                "round": 3,
                "data": {
                    "buyer": "b",
                    "shop": "s",
                    "item": LAMP,
                    "amount": 30000,
                    "grade": "A",
                },
            },
            {
                "seq": 2,
                "ts": now - 6.5 * 3600,
                "kind": "broken",
                "round": 3,
                "data": {"buyer": "b", "item": LAMP},
            },
        ]
    )
    # Then: the manager's 6 h trend summary still carries the matured quality cohort.
    summary = db.summary({}, hours=6)
    assert summary["settles"] == 0
    assert summary["break_rates"] == {"A": {"broken": 1, "observed": 1}}
