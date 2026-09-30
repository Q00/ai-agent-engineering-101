"""One OpenAI-compatible API request per contractor; zero automatic retries."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from cnp.records import Announcement, ModelReply

if TYPE_CHECKING:
    from cnp.settings import Settings


@dataclass(frozen=True, slots=True)
class ChatAdapter:
    """Use the course-compatible SDK with caller-owned client lifetime."""

    client: OpenAI
    settings: Settings

    def bid(self, prompt: str, announcement: Announcement) -> ModelReply:
        """Preserve raw response and transport failure as distinct outcomes."""
        start = monotonic()
        try:
            result = self.client.chat.completions.create(
                model=self.settings.model,
                temperature=self.settings.temperature,
                max_tokens=self.settings.max_tokens,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": announcement.model_dump_json()},
                ],
            )
        except APITimeoutError:
            return ModelReply(
                error="timeout", error_detail="request_timeout", elapsed_s=monotonic() - start
            )
        except APIConnectionError:
            return ModelReply(
                error="api_error", error_detail="connection_error", elapsed_s=monotonic() - start
            )
        except APIStatusError as exc:
            return ModelReply(
                error="api_error",
                error_detail=f"HTTP {exc.status_code}",
                stop_batch=exc.status_code in (401, 402, 429),
                elapsed_s=monotonic() - start,
            )
        if not result.choices:
            return ModelReply(
                raw=result.model_dump_json(),
                error="api_error",
                error_detail="missing_choices",
                elapsed_s=monotonic() - start,
            )
        return ModelReply(
            text=result.choices[0].message.content or "",
            raw=result.model_dump_json(),
            tokens=result.usage.total_tokens if result.usage is not None else 0,
            elapsed_s=monotonic() - start,
        )
