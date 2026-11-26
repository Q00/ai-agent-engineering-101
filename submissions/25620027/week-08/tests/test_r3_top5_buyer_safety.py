"""Safety gates apply independently of the shop ranking policy."""

from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING, final, override

import anyio
import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter

from market_a2a.remote import Part, Task
from r3 import buyer
from r3.market_files import Order
from r3.policy import Policy, apply_changes, save_policy
from tests.r3_stock import LAMP, MILK, write_market
from tests.test_r3_fast_buyer import SellerPeer

if TYPE_CHECKING:
    from pathlib import Path


@final
class CertifiedPeer(SellerPeer):
    """Give quote and receipt quality separately at the real HTTP boundary."""

    def __init__(
        self,
        *,
        quote_grade: str = "A",
        receipt_grade: str = "A",
        receipt_sku: str = LAMP,
        ask: int = 17000,
        warranty_hours: float = 0,
    ) -> None:
        """Set unit facts on an otherwise ordinary fixed-price seller."""
        super().__init__(ask, ask, LAMP)
        self.quote_grade = quote_grade
        self.receipt_grade = receipt_grade
        self.receipt_sku = receipt_sku
        self.warranty_hours = warranty_hours

    @override
    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Attach certified facts without changing the buyer transport."""
        response = super().handle(request)
        if request.method == "GET":
            return response
        outer = TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
        result = TypeAdapter(dict[str, JsonValue]).validate_python(outer["result"])
        task = Task.model_validate(result["task"])
        if task.status.state == "TASK_STATE_INPUT_REQUIRED":
            message = task.status.message.model_copy(
                update={
                    "parts": (
                        *task.status.message.parts,
                        Part(
                            data={
                                "cert": {"cert": {"grade": self.quote_grade}},
                                "warranty_hours": self.warranty_hours,
                            },
                        ),
                    )
                },
            )
            task = task.model_copy(
                update={"status": task.status.model_copy(update={"message": message})},
            )
        else:
            data = task.artifacts[0].parts[0].data
            assert data is not None
            receipt = dict(data)
            receipt["cert"] = {"cert": {"grade": self.receipt_grade}}
            receipt["item"] = self.receipt_sku
            receipt["warranty_hours"] = self.warranty_hours
            artifact = task.artifacts[0].model_copy(
                update={"parts": (Part(data=receipt),)},
            )
            task = task.model_copy(update={"artifacts": (artifact,)})
        return httpx2.Response(
            200,
            json={"result": {"task": task.model_dump(mode="json", by_alias=True)}},
        )


def run_peer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    peer: CertifiedPeer,
    *,
    surplus_score: bool,
) -> buyer.Outcome:
    """Run one real R3 purchase under the current owner's safety thresholds."""
    market = write_market(tmp_path / ".market", [])
    order = Order(
        order="top5-safety",
        item="lamp",
        budget=60000,
        acceptable=(LAMP, MILK),
        kind="goods",
    )
    _ = (market / "order.json").write_text(order.model_dump_json())
    _ = (market / "shops.json").write_text(
        json.dumps(
            [
                {
                    "name": "Peer",
                    "url": "http://peer.test",
                    "offers": [{"sku": LAMP, "qty": 1}],
                }
            ]
        )
    )
    policy = apply_changes(
        Policy(),
        {
            "buyer.surplus_score": surplus_score,
            "buyer.dual_quote": False,
            "buyer.explore_rate": 0.0,
            "buyer.min_expected_credit": 0.28,
            "buyer.max_price_ratio": 0.70,
            "buyer.quick_accept_ratio": 0.64,
            "buyer.first_offer_ratio": 0.48,
        },
    )
    _ = save_policy(tmp_path / "policy.json", policy, "top5 regression")
    monkeypatch.setattr(
        buyer,
        "create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle)),
    )
    return anyio.run(
        partial(
            buyer.buy,
            market / "shops.json",
            "lamp",
            60000,
            market / "order.json",
            lambda _: None,
            folder=market,
            policy_file=tmp_path / "policy.json",
            events_file=tmp_path / "events.sqlite",
        )
    )


@pytest.mark.parametrize("surplus_score", [False, True])
def test_uncovered_b_quote_never_receives_an_offer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, surplus_score: bool
) -> None:
    # Given: a cheap grade B quote without the required warranty.
    peer = CertifiedPeer(quote_grade="B")
    # When: either ranking method processes the same unit.
    outcome = run_peer(tmp_path, monkeypatch, peer, surplus_score=surplus_score)
    # Then: ranking cannot bypass the quality guard before acceptance.
    assert peer.offers == []
    assert outcome.receipt is None


@pytest.mark.parametrize("surplus_score", [False, True])
def test_actual_receipt_quality_overrides_a_safe_quote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, surplus_score: bool
) -> None:
    # Given: a safe A quote is followed by an uncovered B receipt.
    peer = CertifiedPeer(receipt_grade="B")
    # When: the selected shop completes the negotiation.
    outcome = run_peer(tmp_path, monkeypatch, peer, surplus_score=surplus_score)
    # Then: the buyer refuses the receipt instead of handing it to settlement.
    assert peer.offers == [17000]
    assert outcome.receipt is None
    assert buyer.RECEIPT_REFUSAL in outcome.reason


@pytest.mark.parametrize("surplus_score", [False, True])
def test_shipped_sku_price_ceiling_is_independent_of_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, surplus_score: bool
) -> None:
    # Given: 17,000 fits the requested 40,000 list, but not the shipped 23,000 list.
    peer = CertifiedPeer(receipt_sku=MILK)
    # When: the shop returns another acceptable SKU in its signed receipt.
    outcome = run_peer(tmp_path, monkeypatch, peer, surplus_score=surplus_score)
    # Then: the actual shipped price ratio is checked under either ranking flag.
    assert outcome.receipt is None
    assert "shipped list 23000" in outcome.reason


@pytest.mark.parametrize("surplus_score", [False, True])
def test_safe_receipt_still_completes_under_both_rankings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, surplus_score: bool
) -> None:
    # Given: a low-price grade A quote and matching receipt.
    peer = CertifiedPeer()
    # When: the ranking switch varies while owner thresholds stay fixed.
    outcome = run_peer(tmp_path, monkeypatch, peer, surplus_score=surplus_score)
    # Then: the additional guard leaves a safe trade available.
    assert outcome.receipt is not None
    assert outcome.agreed_price == 17000
