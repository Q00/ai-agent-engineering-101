"""Every order shares the same safe discovery and settlement deadline."""

from __future__ import annotations

import json
import time
from functools import partial
from typing import TYPE_CHECKING, Literal, final

import anyio
import httpx2
import pytest

from r3 import buyer
from r3.market_files import Order
from r3.policy import Policy, apply_changes, save_policy
from tests.r3_stock import KIMCHI, LAMP, write_market
from tests.test_r3_top5_buyer_safety import CertifiedPeer

if TYPE_CHECKING:
    from pathlib import Path


@final
class DeadlinePeers:
    """Simulate noncommitting delays and uncertain acceptance on real HTTP calls."""

    def __init__(self, modes: dict[str, str], sku: str) -> None:
        """Select a deterministic behavior for each local shop."""
        self.modes = modes
        self.visits: list[str] = []
        self.offers: list[str] = []
        self.sku = sku

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Serve normal protocol messages or lose one chosen response."""
        host = request.url.host
        mode = self.modes.get(host, "deal")
        if request.method == "GET":
            self.visits.append(host)
            if mode == "slow_card":
                await anyio.sleep(0.20)
        elif b'"taskId"' in request.content:
            self.offers.append(host)
            if mode == "lost_accept":
                message = "acceptance response lost"
                raise httpx2.ReadTimeout(message, request=request)
        elif mode == "slow_quote":
            await anyio.sleep(0.20)
        if mode == "empty" and request.method != "GET":
            return httpx2.Response(
                200,
                json={
                    "result": {
                        "task": {
                            "id": "empty",
                            "contextId": "empty-context",
                            "status": {
                                "state": "TASK_STATE_REJECTED",
                                "message": {
                                    "parts": [{"text": "out of stock"}],
                                },
                            },
                        }
                    }
                },
            )
        peer = CertifiedPeer(
            receipt_sku=self.sku,
            receipt_grade="B" if mode == "bad_receipt" else "A",
        )
        peer.sku = self.sku
        return peer.handle(request)


def run_order(  # noqa: PLR0913 - independent deadline regression inputs
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    peers: DeadlinePeers,
    *,
    kind: Literal["food", "goods"],
    expires: float | None = None,
    dual_quote: bool = False,
) -> buyer.Outcome:
    """Run an inexpensive order with the production safety thresholds."""
    market = write_market(tmp_path / ".market", [])
    order = Order(
        order="top5-deadline",
        item=peers.sku,
        budget=60000,
        acceptable=(peers.sku,),
        kind=kind,
        use_within_hours=24 if kind == "food" else None,
        expires=expires,
    )
    _ = (market / "order.json").write_text(order.model_dump_json())
    _ = (market / "shops.json").write_text(
        json.dumps(
            [
                {
                    "name": host,
                    "url": f"http://{host}",
                    "reviews": [{"stars": max(1, 5 - index)}],
                    "offers": [{"sku": peers.sku, "qty": 1}],
                }
                for index, host in enumerate(peers.modes)
            ]
        )
    )
    _ = save_policy(
        tmp_path / "policy.json",
        apply_changes(
            Policy(),
            {
                "buyer.surplus_score": False,
                "buyer.dual_quote": dual_quote,
                "buyer.explore_rate": 0.0,
                "buyer.min_expected_credit": 0.28,
                "buyer.max_price_ratio": 0.70,
                "buyer.quick_accept_ratio": 0.64,
                "buyer.first_offer_ratio": 0.48,
            },
        ),
        "top5 regression",
    )
    monkeypatch.setattr(
        buyer,
        "create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peers.handle)),
    )
    return anyio.run(
        partial(
            buyer.buy,
            market / "shops.json",
            peers.sku,
            60000,
            market / "order.json",
            lambda _: None,
            folder=market,
            policy_file=tmp_path / "policy.json",
            events_file=tmp_path / "events.sqlite",
        )
    )


@pytest.mark.parametrize(("kind", "sku"), [("goods", LAMP), ("food", KIMCHI)])
@pytest.mark.parametrize("mode", ["slow_card", "slow_quote"])
def test_small_and_food_orders_abandon_slow_initial_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["food", "goods"],
    sku: str,
    mode: str,
) -> None:
    # Given: the first shop exceeds the initial-response allowance before any offer.
    peers = DeadlinePeers({"first.test": mode, "second.test": "deal"}, sku)
    monkeypatch.setattr(buyer, "BIG_FIRST_REPLY_SECONDS", 0.03)
    # When: a small goods or food order starts.
    outcome = run_order(tmp_path, monkeypatch, peers, kind=kind)
    # Then: the healthy next shop receives the sole acceptance.
    assert peers.visits == ["first.test", "second.test"]
    assert peers.offers == ["second.test"]
    assert outcome.receipt is not None


@pytest.mark.parametrize(("kind", "sku"), [("goods", LAMP), ("food", KIMCHI)])
def test_all_order_sizes_stop_at_the_distinct_shop_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["food", "goods"],
    sku: str,
) -> None:
    # Given: one more fast but empty shop than the limit, with ample time left.
    limit = buyer.BIG_ORDER_SHOPS
    peers = DeadlinePeers({f"shop{i:02d}.test": "empty" for i in range(limit + 1)}, sku)
    # When: precommit refusals trigger fallback.
    outcome = run_order(tmp_path, monkeypatch, peers, kind=kind)
    # Then: the shop past the limit is never contacted.
    assert len(peers.visits) == limit
    assert len(set(peers.visits)) == limit
    assert outcome.receipt is None


@pytest.mark.parametrize(("kind", "sku"), [("goods", LAMP), ("food", KIMCHI)])
def test_settlement_reserve_applies_to_small_and_food_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["food", "goods"],
    sku: str,
) -> None:
    # Given: the kit expiry has less than the settlement handoff reserve left.
    peers = DeadlinePeers({"first.test": "deal"}, sku)
    # When: the buyer starts with only 1.4 seconds before expiry.
    outcome = run_order(
        tmp_path,
        monkeypatch,
        peers,
        kind=kind,
        expires=time.time() + 1.4,
    )
    # Then: it preserves settlement time instead of initiating a late negotiation.
    assert peers.visits == []
    assert outcome.receipt is None
    assert outcome.reason == "timed out"


@pytest.mark.parametrize(("kind", "sku"), [("goods", LAMP), ("food", KIMCHI)])
def test_ambiguous_acceptance_never_retries_another_shop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["food", "goods"],
    sku: str,
) -> None:
    # Given: the first shop loses its response after receiving an acceptance.
    peers = DeadlinePeers({"first.test": "lost_accept", "second.test": "deal"}, sku)
    # When: the failure is observed after commitment.
    outcome = run_order(tmp_path, monkeypatch, peers, kind=kind)
    # Then: the buyer does not risk another payment.
    assert peers.visits == ["first.test"]
    assert peers.offers == ["first.test"]
    assert outcome.receipt is None


def test_explicitly_rejected_completed_receipt_can_retry_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a signed receipt is locally rejected before submission to settlement.
    peers = DeadlinePeers({"first.test": "bad_receipt", "second.test": "deal"}, LAMP)
    # When: its known completed task has an uncovered B unit.
    outcome = run_order(tmp_path, monkeypatch, peers, kind="goods")
    # Then: a good second receipt is the only receipt returned for payment.
    assert peers.visits == ["first.test", "second.test"]
    assert outcome.receipt is not None
    assert outcome.shop == "http://second.test"


def test_dual_preflight_counts_toward_shop_limit_without_reopening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the optional dual preflight sees two empty shops, then more are tried.
    limit = buyer.BIG_ORDER_SHOPS
    shops = {f"shop{i:02d}.test": "empty" for i in range(limit + 1)}
    peers = DeadlinePeers(shops, LAMP)
    # When: fallback uses the shared order clock after simultaneous first quotes.
    outcome = run_order(tmp_path, monkeypatch, peers, kind="goods", dual_quote=True)
    # Then: each attempted shop opens once; the shop past the limit stays untouched.
    assert len(peers.visits) == len(set(peers.visits)) == limit
    assert set(peers.visits) < set(shops)
    assert outcome.receipt is None


def test_dual_preflight_cannot_retry_an_uncertain_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: both shops quote safely, then the selected shop loses its acceptance.
    peers = DeadlinePeers({"first.test": "lost_accept", "second.test": "deal"}, LAMP)
    # When: the optional dual path completes its initial comparison.
    outcome = run_order(tmp_path, monkeypatch, peers, kind="goods", dual_quote=True)
    # Then: only the selected shop receives an offer; the other quote stays unused.
    assert sorted(peers.visits) == ["first.test", "second.test"]
    assert peers.offers == ["first.test"]
    assert outcome.receipt is None
