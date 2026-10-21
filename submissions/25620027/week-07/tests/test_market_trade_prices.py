from __future__ import annotations

import datetime as dt
from pathlib import Path

import anyio
import httpx2
import pytest
from a2a.server.tasks import TaskUpdater
from pydantic import JsonValue, TypeAdapter

from market_a2a.catalog import Catalog
from market_a2a.comparison import (
    ComparedShop,
    ComparisonFeed,
    ComparisonSnapshot,
    ObservedQuote,
    TradePrice,
    compare_price,
)
from market_a2a.price_monitor import BOARD_URL, STATS_URL, PriceMonitor
from market_a2a.seller_messages import QuoteNotice, quote_message
from tests.test_opening_ask import EventCollector
from tests.test_price_monitor import MarketTransport

ROOT = Path(__file__).resolve().parents[1]


def snapshot(item: str, reserve: int, mean: float, n: int = 100) -> ComparisonSnapshot:
    now = dt.datetime.now(dt.UTC)
    return ComparisonSnapshot(
        observed_at=now, expires_at=now + dt.timedelta(minutes=15),
        market_round=2,
        trade_prices={item: TradePrice(n=n, min=reserve, max=reserve+100, mean=mean)},
        shops=tuple(ComparedShop(
            rank=index + 1, name=f"shop{index}", url=f"https://shop{index}.example",
            deals=100, quotes=(ObservedQuote.model_validate({
                "item": item, "ask": reserve + index, "observed_at": now,
            }),),
        ) for index in range(5)),
    )


@pytest.mark.parametrize(("item", "reserve", "mean", "expected"), [
    ("lamp", 100, 102.8, 102), ("chair", 180, 184.0, 183),
    ("bookshelf", 260, 266.7, 265),
])
def test_current_trade_mean_allows_bounded_margin(
    item: str, reserve: int, mean: float, expected: int,
) -> None:
    product = Catalog(ROOT / "catalog.json").get(item)
    feed = snapshot(item, reserve, mean)
    restored = ComparisonSnapshot.model_validate_json(feed.model_dump_json())
    quote = compare_price(product, restored)
    assert quote.ceiling == expected
    assert quote.ceiling <= mean
    assert quote.pricing_policy == "market_trade_mean"
    assert quote.market_trade_count == 100
    assert quote.market_round == 2


@pytest.mark.parametrize("mode", ["small", "stale", "no_round", "bad_range"])
def test_unusable_trade_statistics_do_not_change_prior_quote_rule(mode: str) -> None:
    feed = snapshot("lamp", 100, 102.8, n=99 if mode == "small" else 100)
    if mode == "no_round":
        feed = feed.model_copy(update={"market_round": None})
    if mode == "bad_range":
        feed = feed.model_copy(update={
            "trade_prices": {"lamp": TradePrice(n=100, min=105, max=110, mean=102.8)},
        })
    now = feed.observed_at + dt.timedelta(minutes=16) if mode == "stale" else None
    quote = compare_price(Catalog(ROOT / "catalog.json").get("lamp"), feed, now)
    assert quote.market_trade_mean is None
    assert quote.pricing_policy != "market_trade_mean"
    assert quote.ceiling == (110 if mode == "stale" else 100)


def test_quote_states_trade_mean_scope_without_changing_last_ask() -> None:
    product = Catalog(ROOT / "catalog.json").get("lamp")
    message = quote_message(
        TaskUpdater(EventCollector(), "task", "context"),
        QuoteNotice(
            product=product, price=102, previous_ask=None, coupon_applied=False,
            snapshot=snapshot("lamp", 100, 102.8),
        ),
    )
    assert "Market R2 completed-trade mean 102.8 (N=100)" in message.parts[0].text
    assert message.parts[0].text.endswith("Asking price: 102")


@pytest.mark.parametrize("mode", ["valid", "different_round", "unavailable"])
def test_monitor_binds_public_numeric_stats_to_current_round(
    mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    peer = MarketTransport()

    async def respond(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == STATS_URL:
            assert "authorization" not in request.headers
            if mode == "unavailable":
                return httpx2.Response(503)
            return httpx2.Response(200, json={
                "round": 1 if mode == "different_round" else 2,
                "rounds": {"2": {"items": {"lamp": {
                    "n": 100, "min": 100, "max": 140, "mean": 102.8,
                }}}},
            })
        reply = await peer.handle(request)
        if str(request.url) == BOARD_URL:
            body = TypeAdapter(dict[str, JsonValue]).validate_json(reply.content)
            body["round"] = 2
            return httpx2.Response(200, json=body)
        return reply

    def client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond))

    monkeypatch.setattr("market_a2a.price_monitor.create_monitor_client", client)

    async def scenario() -> None:
        monitor = PriceMonitor(ComparisonFeed(), tmp_path / "cache.json")
        observed = await monitor.refresh_once()
        assert observed.market_round == 2
        assert bool(observed.trade_prices) == (mode == "valid")
        assert len(observed.shops) == 5
        assert all(shop.quotes for shop in observed.shops)

    anyio.run(scenario)
