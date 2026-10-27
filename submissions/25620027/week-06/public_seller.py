"""Single-process public seller; only the daily request budget survives restart.

Run with the locked harness_app.py environment (see deployment/Dockerfile).
The HTTPS ingress must enforce a 16 KiB request-body limit.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar, Final

import anyio
import uvicorn
from fastapi.responses import HTMLResponse
from openai import AsyncOpenAI
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    PositiveInt,
    SecretStr,
    StringConstraints,
    UrlConstraints,
    field_validator,
)

from harness_app import emit, isolated
from http_client import create_client
from market_a2a.catalog import Catalog
from market_a2a.harness import Harness
from market_a2a.seller import create_app

if TYPE_CHECKING:
    import httpx2
    from fastapi import FastAPI

ROOT: Final = Path(__file__).resolve().parent


class Settings(BaseModel):
    """Parse deployment environment before opening the listener."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, hide_input_in_errors=True, extra="forbid"
    )
    public_url: Annotated[HttpUrl, UrlConstraints(allowed_schemes=["https"])]
    model_base_url: Annotated[
        str,
        StringConstraints(pattern=r"^https://openrouter\.ai/api/v1/?$"),
    ]
    model: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*:free$"),
    ]
    model_api_key: Annotated[SecretStr, Field(min_length=1)]
    budget_path: Path = Path("/data/model-budget.sqlite")
    daily_requests: PositiveInt = 50

    @field_validator("model")
    @classmethod
    def fixed_free_model(cls, value: str) -> str:
        """Exclude OpenRouter's dynamic router namespace from fixed free models."""
        if value.startswith("openrouter/"):
            message = "AX_PUBLIC_MODEL must select a fixed free model, not a router"
            raise ValueError(message)
        return value

    @field_validator("model_api_key")
    @classmethod
    def model_credential(cls, value: SecretStr) -> SecretStr:
        """Reject whitespace credentials without including their value in errors."""
        if any(character.isspace() for character in value.get_secret_value()):
            message = "AX_PUBLIC_MODEL_API_KEY must not contain whitespace"
            raise ValueError(message)
        return value

    @field_validator("budget_path")
    @classmethod
    def persistent_path(cls, value: Path) -> Path:
        """Resolve storage before entering the isolated temporary directory."""
        return value.resolve()

    @field_validator("public_url")
    @classmethod
    def public_endpoint(cls, value: HttpUrl) -> HttpUrl:
        """Require a base URL without credentials, query, or fragment."""
        if (
            value.username
            or value.password
            or value.query
            or value.fragment
            or not str(value).endswith("/")
        ):
            message = "AX_PUBLIC_URL must be an HTTPS base URL ending in /"
            raise ValueError(message)
        return value


class BudgetExhaustedError(RuntimeError):
    """The UTC day's persisted model-request allowance is exhausted."""


@dataclass(frozen=True, slots=True)
class DailyBudget:
    """Atomically reserve requests before sending; failures still consume budget."""

    path: Path
    limit: int

    def consume(self, day: date) -> None:
        """Commit one reservation with SQLite's cross-process write lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as database, database:
            _ = database.execute(
                """CREATE TABLE IF NOT EXISTS model_requests
                (day TEXT PRIMARY KEY, used INTEGER NOT NULL CHECK(used >= 0))"""
            )
            cursor = database.execute(
                """INSERT INTO model_requests(day, used) SELECT ?, 1 WHERE ? > 0
                ON CONFLICT(day) DO UPDATE SET used = used + 1 WHERE used < ?""",
                (day.isoformat(), self.limit, self.limit),
            )
            if cursor.rowcount != 1:
                raise BudgetExhaustedError

    async def before_request(self, _request: httpx2.Request) -> None:
        """Charge every outbound attempt, including redirects and model retries."""
        await anyio.to_thread.run_sync(self.consume, datetime.now(UTC).date())


def public_app(settings: Settings, harness: Harness) -> FastAPI:
    """Reuse the exact seller guards with a public discovery URL."""
    web = create_app(
        str(settings.public_url),
        harness,
        Catalog(ROOT / "catalog.json"),
        "Sungjin Shop",
    )

    @web.get("/", response_class=HTMLResponse)
    async def landing() -> str:
        """Describe the public endpoint without requiring a model call."""
        return """<!doctype html><html lang="en"><meta charset="utf-8">
        <title>Sungjin Shop — A2A seller</title><h1>Sungjin Shop</h1>
        <p>Week 06 catalog seller. Use A2A 1.0 JSON-RPC POST requests here.</p>
        <p><a href=".well-known/agent-card.json">Agent Card</a></p>
        <p>Negotiations are stored in memory and reset when the service restarts.
        The daily model-request budget persists across restarts.</p></html>"""

    return web


async def serve(settings: Settings) -> None:
    """Keep one model client and one in-memory seller alive until shutdown."""
    if os.environ.get("OPENAI_CUSTOM_HEADERS"):
        message = "Public seller requires OPENAI_CUSTOM_HEADERS to be unset"
        raise ValueError(message)
    budget = DailyBudget(settings.budget_path, settings.daily_requests)
    async with (
        create_client(model=True) as model_http,
        AsyncOpenAI(
            api_key=settings.model_api_key.get_secret_value(),
            base_url=settings.model_base_url,
            organization="",
            project="",
            http_client=model_http,
            max_retries=0,
        ) as model,
    ):
        model_http.follow_redirects = False
        model_http.event_hooks["request"].append(budget.before_request)
        web = public_app(settings, Harness(model, emit, model=settings.model))
        emit("PUBLIC_SELLER workers=1 task_state=memory budget_day=UTC")
        await uvicorn.Server(
            uvicorn.Config(
                web,
                host="0.0.0.0",  # noqa: S104 - Docker publishes only host loopback.
                port=9998,
                workers=1,
                limit_concurrency=8,
                log_level="warning",
                proxy_headers=False,
            )
        ).serve()


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    settings = Settings.model_validate(
        {
            "public_url": os.environ["AX_PUBLIC_URL"],
            "model_base_url": os.environ["AX_PUBLIC_MODEL_BASE_URL"],
            "model": os.environ["AX_PUBLIC_MODEL"],
            "model_api_key": os.environ["AX_PUBLIC_MODEL_API_KEY"],
            "budget_path": os.environ.get(
                "AX_MODEL_BUDGET_PATH", "/data/model-budget.sqlite"
            ),
            "daily_requests": os.environ.get("AX_DAILY_MODEL_REQUESTS", "50"),
        }
    )

    async def run() -> None:
        """Resolve configuration before entering the isolated working directory."""
        await serve(settings)

    isolated(run)
