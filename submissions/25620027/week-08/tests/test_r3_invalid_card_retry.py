"""Only malformed discovery cards may use the existing safe shop fallback."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

import httpx2
import pytest

from r3 import buyer
from tests.r3_stock import KIMCHI, LAMP
from tests.test_r3_top5_buyer_deadline import DeadlinePeers, run_order
from tests.test_r3_top5_buyer_safety import CertifiedPeer

if TYPE_CHECKING:
    from pathlib import Path


def malformed_peers(
    monkeypatch: pytest.MonkeyPatch, modes: dict[str, str], sku: str = LAMP
) -> DeadlinePeers:
    """Return real HTTP fixtures distinguishing discovery from message parsing."""
    peers = DeadlinePeers(modes, sku)
    normal_handle = peers.handle

    async def handle(request: httpx2.Request) -> httpx2.Response:
        host = request.url.host
        mode = modes[host]
        if request.method == "GET" and mode == "bad_card":
            peers.visits.append(host)
            return httpx2.Response(
                200,
                json={
                    "name": "malformed",
                    "skills": [],
                    "supportedInterfaces": [
                        {"url": f"http://{host}/rpc", "transport": "JSONRPC"},
                    ],
                },
            )
        accepting = b'"taskId"' in request.content
        if request.method != "GET" and (
            (mode == "bad_first_reply" and not accepting)
            or (mode == "bad_accept" and accepting)
        ):
            if accepting:
                peers.offers.append(host)
            return httpx2.Response(200, json={"result": {"task": {"id": "t-1"}}})
        if mode in {"bad_quality", "bad_price"}:
            if request.method == "GET":
                peers.visits.append(host)
            elif accepting:
                peers.offers.append(host)
            peer = CertifiedPeer(
                quote_grade="C" if mode == "bad_quality" else "A",
                ask=31000 if mode == "bad_price" else 17000,
            )
            return peer.handle(request)
        return await normal_handle(request)

    monkeypatch.setattr(peers, "handle", handle)
    return peers


@pytest.mark.parametrize(("kind", "sku"), [("goods", LAMP), ("food", KIMCHI)])
def test_invalid_card_retries_before_any_purchase_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["food", "goods"],
    sku: str,
) -> None:
    # Given: discovery is malformed, but another stocked shop has a valid card.
    peers = malformed_peers(
        monkeypatch, {"first.test": "bad_card", "second.test": "deal"}, sku
    )
    # When: the order encounters the same missing interface fields seen live.
    outcome = run_order(tmp_path, monkeypatch, peers, kind=kind)
    # Then: only the healthy shop receives an offer and a payable receipt returns.
    assert peers.visits == ["first.test", "second.test"]
    assert peers.offers == ["second.test"]
    assert outcome.receipt is not None
    assert outcome.shop == "http://second.test"


def test_invalid_cards_still_consume_the_shop_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: every discovery card is malformed and one shop past the limit exists.
    limit = buyer.BIG_ORDER_SHOPS
    peers = malformed_peers(
        monkeypatch, {f"shop{i:02d}.test": "bad_card" for i in range(limit + 1)}
    )
    # When: malformed cards consume the ordinary bounded fallback.
    outcome = run_order(tmp_path, monkeypatch, peers, kind="goods")
    # Then: no purchase request is sent and the shop past the limit is never visited.
    shops = {f"shop{i:02d}.test" for i in range(limit + 1)}
    assert len(peers.visits) == len(set(peers.visits)) == limit
    assert set(peers.visits) < shops
    assert peers.offers == []
    assert outcome.receipt is None


@pytest.mark.parametrize("seconds_left", [1.0, 1.8])
def test_invalid_card_does_not_extend_expiry_or_retry_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seconds_left: float
) -> None:
    # Given: the order is expired for negotiation, or has too little retry time.
    peers = malformed_peers(
        monkeypatch, {"first.test": "bad_card", "second.test": "deal"}
    )
    # When: the buyer preserves its original deadline and settlement reserve.
    outcome = run_order(
        tmp_path,
        monkeypatch,
        peers,
        kind="goods",
        expires=time.time() + seconds_left,
    )
    # Then: the healthy next shop is never contacted after the available budget.
    assert peers.visits == ([] if seconds_left == 1.0 else ["first.test"])
    assert peers.offers == []
    assert outcome.receipt is None


@pytest.mark.parametrize("mode", ["bad_first_reply", "bad_accept"])
def test_message_validation_errors_never_become_card_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    # Given: discovery succeeds, but a first response or acceptance is malformed.
    peers = malformed_peers(monkeypatch, {"first.test": mode, "second.test": "deal"})
    # When: parsing fails after a request was sent, possibly after commitment.
    outcome = run_order(tmp_path, monkeypatch, peers, kind="goods")
    # Then: the ambiguous negotiation remains terminal with no second payment.
    assert peers.visits == ["first.test"]
    assert peers.offers == (["first.test"] if mode == "bad_accept" else [])
    assert outcome.receipt is None
    assert outcome.reason.startswith("ValidationError:")


@pytest.mark.parametrize("mode", ["bad_quality", "bad_price"])
def test_card_fallback_does_not_relax_quality_or_price(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    # Given: the next shop has a valid card but excluded quality or an excess ask.
    peers = malformed_peers(
        monkeypatch, {"first.test": "bad_card", "second.test": mode}
    )
    # When: discovery fallback reaches ordinary negotiation and receipt guards.
    outcome = run_order(tmp_path, monkeypatch, peers, kind="goods")
    # Then: visiting the second shop cannot authorize an unsafe purchase.
    assert peers.visits == ["first.test", "second.test"]
    assert outcome.receipt is None
    if mode == "bad_quality":
        assert peers.offers == []
