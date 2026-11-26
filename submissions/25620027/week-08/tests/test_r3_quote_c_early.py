"""Reject stated C units before making an offer, then reuse the unpaid retry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2
import pytest

from tests.test_r3_small_accept_guard import SmallPeers, run_small

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("description", ["grade C", "refurbished", "C급", "GRADE c"])
def test_stated_c_quote_retries_before_any_offer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, description: str
) -> None:
    original = SmallPeers.handle

    async def handle(self: SmallPeers, request: httpx2.Request) -> httpx2.Response:
        response = await original(self, request)
        if (
            request.url.host == "first.test"
            and request.method == "POST"
            and b'"taskId"' not in request.content
        ):
            payload = response.json()
            payload["result"]["task"]["status"]["message"]["parts"] = [
                {"text": f"This unit is {description}: asking 15000."}
            ]
            return httpx2.Response(200, json=payload)
        return response

    monkeypatch.setattr(SmallPeers, "handle", handle)
    outcome, peers = run_small(tmp_path, monkeypatch, "pending")
    assert peers.visits == ["first.test", "second.test"]
    assert peers.offers == []  # No acceptance or counteroffer went to the C shop.
    assert outcome.shop == "http://second.test"
    assert outcome.receipt is not None
    assert outcome.agreed_price == 15000
