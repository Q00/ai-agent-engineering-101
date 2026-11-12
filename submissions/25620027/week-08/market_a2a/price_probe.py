"""Restricted public A2A openings with explicit product identity and cleanup."""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Final, Literal
from uuid import uuid4

import anyio
import httpx2
from pydantic import ValidationError

from market_a2a.comparison import ComparedShop, ObservedQuote
from market_a2a.models import HarnessError
from market_a2a.remote import Remote, RpcError, Task, Wire

LOGGER: Final = logging.getLogger(__name__)
ITEMS: Final[tuple[Literal["lamp", "chair", "bookshelf"], ...]] = (
    "lamp",
    "chair",
    "bookshelf",
)
TIMEOUT_SECONDS: Final = 20


class CancelReply(Wire):
    """The SDK returns a task directly for a successful CancelTask result."""

    result: Task | None = None
    error: RpcError | None = None


def supported_host(url: str) -> str:
    """Allow only HTTPS public infrastructure; never arbitrary host destinations."""
    parsed = httpx2.URL(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        detail = "unsupported_url"
        raise HarnessError(detail)
    host = parsed.host
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) is None:
        detail = "unsupported_host"
        raise HarnessError(detail)
    if host != "market.ourolabs.ai" and not host.endswith(
        (".trycloudflare.com", ".workers.dev")
    ):
        detail = "unsupported_host"
        raise HarnessError(detail)
    return host


def create_monitor_client() -> httpx2.AsyncClient:
    """Use verified TLS with no redirects, environment proxy, or transport retries."""
    return httpx2.AsyncClient(
        transport=httpx2.AsyncHTTPTransport(http2=True, retries=0, trust_env=False),
        timeout=httpx2.Timeout(TIMEOUT_SECONDS),
        follow_redirects=False,
        trust_env=False,
    )


def unavailable(
    item: Literal["lamp", "chair", "bookshelf"], code: str
) -> ObservedQuote:
    """Keep failed items visible without copying remote prose into the snapshot."""
    return ObservedQuote(item=item, observed_at=dt.datetime.now(dt.UTC), error=code)


async def cancel_opening(client: httpx2.AsyncClient, endpoint: str, task: Task) -> None:
    """Best-effort SDK CancelTask cleanup, never a price or checkout request."""
    try:
        with anyio.fail_after(TIMEOUT_SECONDS):
            response = await client.post(
                endpoint,
                headers={"A2A-Version": "1.0"},
                json={
                    "jsonrpc": "2.0",
                    "id": str(uuid4()),
                    "method": "CancelTask",
                    "params": {"id": task.id},
                },
            )
            _ = response.raise_for_status()
            envelope = CancelReply.model_validate_json(response.content)
            if (
                envelope.error is not None
                or envelope.result is None
                or envelope.result.status.state != "TASK_STATE_CANCELED"
            ):
                LOGGER.info("price_monitor cancel_error=unsupported_or_rejected")
    except (httpx2.HTTPError, TimeoutError, ValidationError):
        LOGGER.info("price_monitor cancel_error=transport_or_payload")


def supplemental_ask(
    text: str, item: Literal["lamp", "chair", "bookshelf"]
) -> int | None:
    """Parse only two observed full quote formats; never return advertising prose."""
    for pattern in (
        rf"{item}: asking price ([0-9]{{1,9}})",
        (
            rf"{item}: [^\r\n]{{1,500}} Asking ([0-9]{{1,9}})\. "
            r"Send your offer as a number\."
        ),
    ):
        matched = re.fullmatch(pattern, text.strip())
        if matched is not None and int(matched[1]) > 0:
            return int(matched[1])
    return None


async def opening_quote(
    client: httpx2.AsyncClient,
    endpoint: str,
    item: Literal["lamp", "chair", "bookshelf"],
) -> ObservedQuote:
    """Open one product task, retain only a valid ask, and cancel the task."""
    task: Task | None = None
    try:
        with anyio.fail_after(TIMEOUT_SECONDS):
            task = await Remote(client, lambda _line: None).send(
                endpoint, f"I want to buy {item}"
            )
        for identity in (task.metadata, task.status.message.metadata):
            if (identity.item and identity.item != item) or (
                identity.sku and identity.sku != item
            ):
                return unavailable(item, "product_mismatch")
        ask = task.asking_price()
        if ask is None:
            ask = supplemental_ask(task.observation(), item)
        if task.status.state != "TASK_STATE_INPUT_REQUIRED":
            return unavailable(item, "unexpected_task_state")
        if ask is None:
            return unavailable(item, "ask_unavailable")
        return ObservedQuote(item=item, ask=ask, observed_at=dt.datetime.now(dt.UTC))
    except (httpx2.HTTPError, HarnessError, ValidationError, TimeoutError):
        return unavailable(item, "opening_failed")
    finally:
        if task is not None:
            await cancel_opening(client, endpoint, task)


async def sample_shop(client: httpx2.AsyncClient, shop: ComparedShop) -> ComparedShop:
    """Read one card and sample three items without following alternate hosts."""
    try:
        host = supported_host(shop.url)
        with anyio.fail_after(TIMEOUT_SECONDS):
            card = await Remote(client, lambda _line: None).card(shop.url)
        endpoint = card.endpoint()
        if supported_host(endpoint) != host:
            return shop.model_copy(
                update={
                    "quotes": tuple(
                        unavailable(i, "endpoint_host_mismatch") for i in ITEMS
                    )
                }
            )
    except HarnessError as exc:
        code = (
            exc.detail
            if exc.detail
            in {"unsupported_url", "unsupported_host", "endpoint_host_mismatch"}
            else "card_unavailable"
        )
        return shop.model_copy(
            update={"quotes": tuple(unavailable(i, code) for i in ITEMS)}
        )
    except (httpx2.HTTPError, httpx2.InvalidURL, ValidationError, TimeoutError):
        return shop.model_copy(
            update={"quotes": tuple(unavailable(i, "card_unavailable") for i in ITEMS)}
        )
    quotes = tuple([await opening_quote(client, endpoint, item) for item in ITEMS])
    return shop.model_copy(update={"quotes": quotes})
