"""Persistently reserve authorized OpenAI attempts before sending paid requests."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from market_a2a.model_provider import DEFAULT_MODEL
from market_a2a.models import HarnessError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from openai import AsyncOpenAI
    from openai.types.chat import (
        ChatCompletion,
        ChatCompletionMessageParam,
        ChatCompletionToolParam,
    )

PAID_DAILY_LIMIT: Final = 5000
KST: Final = timezone(timedelta(hours=9))
COUNTS: Final = TypeAdapter(tuple[tuple[int], ...])


@dataclass(frozen=True, slots=True)
class DailyQuota:
    """Count attempted calls per KST day atomically, across process restarts."""

    path: Path
    limit: int = PAID_DAILY_LIMIT

    def __post_init__(self) -> None:
        """Reject lost ledgers rather than silently granting a fresh paid allowance."""
        if not 0 < self.limit <= PAID_DAILY_LIMIT:
            message = "OpenAI daily limit must be between1 and5000"
            raise HarnessError(message)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        marker = self.path.with_suffix(".initialized")
        exists = self.path.exists()
        if marker.exists() and not exists:
            message = "Paid quota ledger is missing; refusing to reset usage"
            raise HarnessError(message)
        with closing(sqlite3.connect(self.path)) as database, database:
            if not exists:
                _ = database.execute(
                    """CREATE TABLE daily_usage (day TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL CHECK(attempts >= 0))""",
                )
            _ = database.execute("SELECT day, attempts FROM daily_usage LIMIT 0")
        self.path.chmod(0o600)
        if not marker.exists():
            with marker.open("x", encoding="utf-8") as stream:
                _ = stream.write("paid-quota-v1\n")
            marker.chmod(0o600)

    def reserve(self, now: datetime | None = None) -> int:
        """Commit one slot before network I/O; failed attempts still consume it."""
        if not self.path.exists():
            message = "Paid quota ledger disappeared; refusing paid request"
            raise HarnessError(message)
        current = now if now is not None else datetime.now(UTC)
        if current.tzinfo is None:
            message = "Quota time must include a timezone"
            raise HarnessError(message)
        day = current.astimezone(KST).date().isoformat()
        with closing(sqlite3.connect(self.path, timeout=10)) as database, database:
            rows = COUNTS.validate_python(database.execute(
                """INSERT INTO daily_usage(day, attempts) VALUES (?, 1)
                ON CONFLICT(day) DO UPDATE SET attempts=attempts+1
                WHERE attempts < ? RETURNING attempts""", (day, self.limit),
            ).fetchall())
            if not rows:
                message = f"OpenAI daily attempt limit reached for {day} KST"
                raise HarnessError(message)
        return rows[0][0]


@dataclass(frozen=True, slots=True)
class OpenAIFallback:
    """One explicitly authorized paid request; SDK retries must remain disabled."""

    client: AsyncOpenAI = field(repr=False)
    quota: DailyQuota
    model: str = DEFAULT_MODEL
    max_input_bytes: int | None = None

    async def complete(
        self, messages: list[ChatCompletionMessageParam],
        tools: list[ChatCompletionToolParam], emit: Callable[[str], None],
    ) -> ChatCompletion:
        """Reserve quota even if OpenAI later fails or the process is interrupted."""
        if (self.client.max_retries != 0
                or self.client.base_url.host != "api.openai.com"):
            message = "Paid fallback requires OpenAI directly with SDK retries off"
            raise HarnessError(message)
        if self.max_input_bytes is not None:
            size = len(json.dumps([messages, tools], ensure_ascii=False).encode())
            if size > self.max_input_bytes:
                message = "Validation request exceeds its authorized input bound"
                raise HarnessError(message)
        used = self.quota.reserve()
        emit(f"OPENAI_ATTEMPT day_kst={datetime.now(KST).date()} used={used}/5000")
        return await self.client.chat.completions.create(
            model=self.model, temperature=0, messages=messages, tools=tools,
            tool_choice="required", parallel_tool_calls=False,
            max_completion_tokens=450,
        )
