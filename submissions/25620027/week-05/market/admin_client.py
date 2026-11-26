"""Typed client for the runner-only market administration routes."""

import httpx2
from pydantic import TypeAdapter

from market.api_models import OpenedNegotiation, OpenNegotiation
from market.models import EpisodeSummary, EventRecord, NegotiationId


class AdminClient:
    """Open episodes, inspect metrics, and pass an unproductive host turn."""

    def __init__(self, client: httpx2.AsyncClient, secret: str) -> None:
        self._client: httpx2.AsyncClient = client
        self._headers: dict[str, str] = {"X-Admin-Secret": secret}

    async def open(self, request: OpenNegotiation) -> OpenedNegotiation:
        """Create one isolated negotiation and mint its two bearer grants."""
        response = await self._client.post(
            "/admin/negotiations",
            headers=self._headers,
            content=request.model_dump_json(),
        )
        response.raise_for_status()
        return OpenedNegotiation.model_validate_json(response.content)

    async def summary(self, negotiation_id: NegotiationId) -> EpisodeSummary:
        """Read grader-facing metrics without incrementing tool calls."""
        response = await self._client.get(
            f"/admin/negotiations/{negotiation_id}/summary",
            headers=self._headers,
        )
        response.raise_for_status()
        return EpisodeSummary.model_validate_json(response.content)

    async def events(self, negotiation_id: NegotiationId) -> tuple[EventRecord, ...]:
        """Read the append-only extension ledger."""
        response = await self._client.get(
            f"/admin/negotiations/{negotiation_id}/events",
            headers=self._headers,
        )
        response.raise_for_status()
        return TypeAdapter(tuple[EventRecord, ...]).validate_json(response.content)

    async def pass_turn(self, negotiation_id: NegotiationId) -> None:
        """Advance after a host returns without committing a valid move."""
        response = await self._client.post(
            f"/admin/negotiations/{negotiation_id}/pass",
            headers=self._headers,
        )
        response.raise_for_status()
