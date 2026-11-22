"""An accepted small-order offer must not open another shop's negotiation."""

from __future__ import annotations

import json
import sqlite3
from functools import partial
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter

from r3 import buyer
from r3.policy import Policy, apply_changes, save_policy
from tests.r3_stock import KIMCHI, LAMP, write_market
from tests.test_r3_fast_buyer import SellerPeer

if TYPE_CHECKING:
    from pathlib import Path


class SmallPeers:
    """Two usable shops, with the first changing behavior after an offer."""

    def __init__(self, mode: str, sku: str) -> None:
        """Fix the first response and track all actual HTTP contacts."""
        self.mode: str = mode
        self.visits: list[str] = []
        self.posts: list[str] = []
        self.offers: list[int] = []
        self.first: SellerPeer = SellerPeer(
            ask=60000 if mode == "price_wall" else 15000,
            accept_at=59000 if mode == "price_wall" else 15000,
            sku=sku,
        )
        self.second: SellerPeer = SellerPeer(ask=15000, accept_at=15000, sku=sku)

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Return real A2A tasks and simulate ambiguous postaccept responses."""
        host = request.url.host
        if request.method == "GET":
            self.visits.append(host)
        else:
            self.posts.append(host)
        if host == "second.test":
            return self.second.handle(request)
        if request.method == "GET" or b'"taskId"' not in request.content:
            return self.first.handle(request)
        payload = TypeAdapter(dict[str, JsonValue]).validate_json(request.content)
        params = payload["params"]
        assert isinstance(params, dict)
        message = params["message"]
        assert isinstance(message, dict)
        request_parts = message["parts"]
        assert isinstance(request_parts, list)
        request_part = request_parts[0]
        assert isinstance(request_part, dict)
        offer = request_part["text"]
        assert isinstance(offer, str)
        self.offers.append(int(offer))
        if self.mode == "timeout":
            raise TimeoutError
        if self.mode == "http_loss":
            error = "accept response lost"
            raise httpx2.ReadTimeout(error, request=request)
        if self.mode in {"deal", "price_wall"}:
            return self.first.handle(request)
        parts: list[dict[str, object]] = [{"text": "Counteroffer: 15000."}]
        if self.mode == "grade_c":
            parts.append({"data": {"grade": "C"}})
        if self.mode == "short_food":
            parts.append({"data": {"hours_left": 1}})
        return httpx2.Response(
            200,
            json={
                "result": {
                    "task": {
                        "id": "t-1",
                        "contextId": "c-1",
                        "status": {
                            "state": "TASK_STATE_REJECTED"
                            if self.mode == "rejected"
                            else "TASK_STATE_INPUT_REQUIRED",
                            "message": {
                                "parts": parts,
                                "metadata": {"ask": 15000},
                            },
                        },
                    }
                }
            },
        )


def run_small(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> tuple[buyer.Outcome, SmallPeers]:
    """Run buy, Remote, negotiation and the event ledger with in-process HTTP."""
    food = mode == "short_food"
    sku = KIMCHI if food else LAMP
    folder = write_market(tmp_path / ".market", [])
    _ = (folder / "order.json").write_text(
        json.dumps(
            {
                "order": "small-safety-3",
                "budget": 60000,
                "item": sku,
                "kind": "food" if food else "goods",
                "acceptable": [sku],
                "use_within_hours": 24 if food else None,
            }
        )
    )
    _ = (folder / "shops.json").write_text(
        json.dumps(
            [
                {
                    "name": name,
                    "url": f"http://{name}.test",
                    "offers": [{"sku": sku, "qty": 3}],
                }
                for name in ("first", "second")
            ]
        )
    )
    _ = save_policy(
        tmp_path / "policy.json",
        apply_changes(Policy(), {"buyer.dual_quote": False, "buyer.explore_rate": 0.0}),
        "deterministic single-quote regression",
    )
    peers = SmallPeers(mode, sku)
    monkeypatch.setattr(
        buyer,
        "create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peers.handle)),
    )
    outcome = anyio.run(
        partial(
            buyer.buy,
            folder / "shops.json",
            sku,
            60000,
            folder / "order.json",
            lambda _: None,
            folder=folder,
            policy_file=tmp_path / "policy.json",
            events_file=tmp_path / "events.sqlite",
        )
    )
    return outcome, peers


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("pending", "TASK_STATE_INPUT_REQUIRED"),
        ("rejected", "TASK_STATE_REJECTED"),
        ("grade_c", "UnacceptedGradeError"),
        ("short_food", "ShortShelfLifeError"),
        ("timeout", "timed out"),
        ("http_loss", "ReadTimeout"),
    ],
)
def test_small_accept_never_contacts_another_shop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, reason: str
) -> None:
    outcome, peers = run_small(tmp_path, monkeypatch, mode)
    assert peers.offers
    assert peers.offers[0] == 15000
    assert peers.visits == ["first.test"]
    assert set(peers.posts) == {"first.test"}
    assert outcome.receipt is None
    assert reason in outcome.reason
    with sqlite3.connect(tmp_path / "events.sqlite") as connection:
        assert connection.execute(
            "SELECT shop, outcome FROM buyer_attempts"
        ).fetchall() == [("http://first.test", "no_deal")]


def test_unaccepted_small_price_wall_still_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome, peers = run_small(tmp_path, monkeypatch, "price_wall")
    assert peers.offers
    assert max(peers.offers) < 59000
    assert peers.visits == ["first.test", "second.test"]
    assert outcome.shop == "http://second.test"
    assert outcome.receipt is not None
    assert outcome.agreed_price == 15000


def test_small_accept_success_keeps_single_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome, peers = run_small(tmp_path, monkeypatch, "deal")
    assert peers.visits == ["first.test"]
    assert peers.offers == [15000]
    assert outcome.shop == "http://first.test"
    assert outcome.receipt is not None
    assert outcome.agreed_price == 15000
    with sqlite3.connect(tmp_path / "events.sqlite") as connection:
        assert connection.execute(
            "SELECT shop, outcome FROM buyer_attempts"
        ).fetchall() == [("http://first.test", "agreed")]
