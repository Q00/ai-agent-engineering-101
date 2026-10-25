from __future__ import annotations
import json



import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()



@dataclass
class Meter:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: int = 0

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens
    
    def note(self) -> str:
        return f"parse_pending: tokens = {self.tokens}, calls = {self.calls}, errors = {self.errors}"
    
    def record_success(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
    
    def record_err(self) -> None:
        self.errors += 1


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def _message_text(rsps) -> str:
    choice = rsps.choices[0]
    msg = choice.message
    text = getattr(msg, "content", None)
    





    print(
        f"  [api] finish={choice.finish_reason} "
        f"content={msg.content!r} "
        f"reasoning={getattr(msg, 'reasoning_content', None)!r} "
        f"comp_tokens={getattr(rsps.usage, 'completion_tokens', None)}"
    )
    
    if isinstance(text, list):  # some providers return parts
        text = "".join(
            p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "")
            for p in text
        )
    if text:
        return text
    # reasoning / extra fields some models use instead of content
    for key in ("reasoning", "reasoning_content", "refusal"):
        extra = getattr(msg, key, None)
        if extra:
            return str(extra)
    return ""


@dataclass
class Chat:

    def __init__(
        self,
        meter: Meter,
        *,
        model: str,
        provider: str,
        temperature: float = 0.0,
        max_tokens: int = 256,
        mock: bool = False,
        mock_fn: Callable[[str, str], str] | None = None,
    ) -> None:

        self.meter = meter
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.mock = mock
        self.mock_fn = mock_fn

        if mock:
            self.client = None
            return
        
        
        api_key = os.environ.get("MODEL_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MODEL API KEY missing"
            )

        self.client = AsyncOpenAI(
            api_key = api_key,
            base_url = os.environ.get("META_API_BASE_URL", "https://api.meta.ai/v1")
        )

    def header_line(self) -> str:

        if self.model is not None:

            return (
                f"provider = {self.provider}, model = {self.model}"
                f"temperature = {self.temperature}, max_tokens = {self.max_tokens}"
                f"tools"
            )

        else:
            raise RuntimeError(f"model is not provided")
    
    
    async def complete(self, system: str, user: str) -> str:
        self.meter.calls += 1

        try:
            if self.mock:
                return self._complete_mock(system, user)
            
            return await self._complete_openai(system, user)
        
        
        except Exception as e:
            self.meter.record_err()
            raise RuntimeError(f"chat failed: {e}")

    async def _complete_openai(self, system:str, user: str) -> str:

        assert self.client is not None

        rsps = await self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort="low",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        

        usage = rsps.usage

        self.meter.record_success(
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens = getattr(usage, "completion_tokens") or 0
        )

        text = _message_text(rsps)

        if not text:
            return ""

        return text
        
    async def _complete_mock(self, system: str, user: str) -> str:
        if self.mock_fn is None:
            raise RuntimeError(f"mock = True, provider fn")
        
        text = self.mock_fn(system, user)
    
        self.meter.record_success(
            prompt_tokens = estimate_tokens(system) + estimate_tokens(user),
            completion_tokens = estimate_tokens(text)
        )

        return text