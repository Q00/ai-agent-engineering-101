from __future__ import annotations
import json
import os


import random
import time
import urllib.error
import urllib.request

from typing import Callable
from dotenv import load_dotenv

load_dotenv()

MAX_TOKENS  = 2048

class RateLimitError(RuntimeError):
    pass

def _sleep_backoff(attempt: int) -> None:
    time.sleep(min(60.0, (2**attempt) + random.random()))

def _http_json(url: str, payload: dict, headers: dict, timeout: int = 100) -> dict:

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data = data, headers = headers, method = "POST")

    try:
        with urllib.request.urlopen(req, timeout = timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
        
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors = "replace")
        if e.code == 429:
            raise RateLimitError(body) from e
        
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}") from e
    
def complete_retry(fn: Callable[[], str], attempts: int = 8) -> str:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    
    last : Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        
        except (RateLimitError, TimeoutError, urllib.error.URLError) as e:
            if  i == attempts - 1:
                raise RuntimeError(f"retries exhausted: {e}") from e
            
            _sleep_backoff(i)
        # TODO : compare codes
        """
        except RateLimitError as e:
            last = e
            _sleep_backoff(i)
        except (TimeoutError, urllib.error.URLError) as e:
            last = e
            _sleep_backoff(i)
        """

    raise AssertionError(f"unreachable")

def _flatten_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
        return "".join(parts).strip()
    return str(content).strip()

def _chat_text(out: object) -> str:
    if not isinstance(out, dict):
        raise RuntimeError(f"non-object model response: {type(out).__name__}")
    err = out.get("error")
    if err:
        raise RuntimeError(str(err)[:400])
    choices = out.get("choices")
    # debug only
    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        print(f"text: finish_reason={choice.get('finish_reason')}")


    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        msg = choices[0].get("message")
        if isinstance(msg, dict):
            text = _flatten_content(msg.get("content"))
            if text:
                return text
        text = _flatten_content(choices[0].get("text"))
        if text:
            return text
        
    text = _flatten_content(out.get("content"))
    if text:
        return text
    output = out.get("output")
    if isinstance(output, list):
        text = _flatten_content(output)
        if text:
            return text
    raise RuntimeError(f"no text in model response keys={list(out)[:12]}")

class LLM:

    def __init__(self, provider: str, model: str, temperature: float | None) -> None:
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.input_tokens = 0
        self.output_tokens = 0

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        provider = self.provider

        if provider == "openai":
            return complete_retry(lambda: self._openai(system, messages))
        if provider == "anthropic":
            return complete_retry(lambda: self._anthropic(system, messages))
        if provider in {"claude_cli", "claude-cli"}:
            return complete_retry(lambda: self._claude_cli(system, messages))
        if provider == "mock":
            return self._mock(system, messages)
        raise ValueError(f"unknown provider: {provider}")

    def reader(self, system: str, user: str) -> str:
        return self.complete(system, [{"role": "user", "content":user}])
    
    def _anthropic(self, system: str, messages: list[dict[str, str]]) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY")

        if not key:
            raise RuntimeError("ANTHROPIC API KEY is not set")
        
        payload: dict = {
            "model": self.model,
            "max_tokens": 256,
            "system": system,
            "messages": messages,
        }
    
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        
        out = _http_json(
            "https://api.anthropic.com/v1/messages",
            payload,
            {
                "content-type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            }
        )
        
        return _chat_text(out)
    
    def _openai(self, system: str, messages: list[dict[str, str]]) -> str:
        key = os.environ.get("API_KEY")

        if not key:
            raise RuntimeError("API KEY is not set")
        
        base = os.environ.get("BASE_URL", "https://api.openai.com/v1").rstrip("/")
        payload: dict = {
            "model": self.model,
            "max_completion_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": system}, *messages],
        }

        if self.temperature is not None:
            payload["temperature"] = self.temperature
        
        if self.model == "gpt-6-luna":
            payload["reasoning_effort"] = "none"
        
        out = _http_json(
            f"{base}/chat/completions",
            payload,
            {
                "content-type": "application/json",
                "authorization": f"Bearer {key}",
            }
        )

        usage = out.get("usage") or {}
        self.input_tokens += usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0)

        return _chat_text(out)

    def _claude_cli(self, system: str, messages: list[dict[str, str]]) -> str:
        import shutil
        import subprocess

        if shutil.which("claude") is None:
            raise RuntimeError("claude CLI not found on PATH")
        lines = [system, "", "Conversation:"]
        for m in messages:
            lines.append(f"{m['role'].upper()}: {m['content']}")
        lines.append("ASSISTANT:")
        prompt = "\n".join(lines)
        proc = subprocess.run(
            [
                "claude", "-p",
                "--model", self.model,
                "--system-prompt", system,
                prompt,],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            if "429" in err:
                raise RateLimitError(err)
            raise RuntimeError(err[:500] or f"claude exit {proc.returncode}")
        return (proc.stdout or "").strip()
    
    def _mock(self, system: str, messages: list[dict[str, str]]) -> str:
        text = system + "\n" + "\n".join(m["content"] for m in messages)

        if "Label the LAST message" in text or "Extract the speaker" in text:
            return _mock_reader(messages[-1]["content"])
        
        role = "buyer" if "You are the buyer." in system else "seller"
        condition = "free"

        if "[propose]" in system:
            condition = "tagged"
        elif "JSON object" in system:
            condition = "structured"
        
        limit = 200
        
        import re

        m = re.search(r"\b(?:budget|reserve\)?\s*:\s*(\d+)\b", system, re.IGNORECASE)

        if m is None:
            raise ValueError("mock could not find budget/reserve in system prompt")
        
        limit = int(m.group(1))
        
        last_price = None

        for msg in reversed(messages):
            nums = re.findall(r"\d+", msg["content"])
            if nums:
                last_price = int(nums[-1])
                break
        
        if role == "buyer":
            offer = min(limit, last_price if last_price else max(1, limit - 15))
            if last_price is not None and last_price <= limit:
                act, price = "accept-proposal", last_price
            elif last_price is not None and last_price >  limit + 20:
                act, price = "refuse", last_price
            else:
                act, price = "propose", offer
        else:
            offer = max(limit, last_price if last_price else limit + 15)
            if last_price is not None and last_price >= limit:
                act, price = "accept-proposal", last_price
            elif last_price is not None and last_price <  limit - 20:
                act, price = "refuse", last_price
            else:
                act, price = "propose", offer
        
        return render_mock(condition, role, act, price)

def render_mock(condition: str, role: str, act: str, price: int) -> str:
    if condition == "structured":
        return json.dumps({"performative": act, "content": {"price": price}})
    
    sentence = {
        "propose": f"I can do {price} for this",
        "accept-proposal": f"We have a deal at {price}",
        "reject-proposal": f"{price} does not work for me",
        "refuse": f"I have to walk away from {price}",
    }[act]

    if condition == "tagged":
        return f"[{act}] {sentence}"
    if role == "buyer" and act == "propose":
        # ambiguity test
        return f"Would you start at {price}? I saw another listing at {price + 20}"
    return sentence

def _mock_reader(user_text: str) -> str:
    import re

    lines = [ln.strip() for ln in user_text.strip().splitlines() if ln.strip()]

    last = ""

    for ln in reversed(lines):
        if ln.startswith("Label ") or ln.startswith("Extract ") or ln.startswith("Transcript"):
            continue
        last = re.sub(r"^\[(?:buyer|seller)\]\s*", "", ln)
        break
    if not last:
        last = lines[-1] if lines else ""
    
    tag = re.search(r"\[(propose|accept-proposal|reject-proposal|refuse)\]", last, re.I)
    if tag:
        nums = re.findall(r"\d+", last)
        price = int(nums[-1]) if nums else None
        return json.dumps({"performative": tag.group(1).lower(), "price": price})
    if "walk away" in last.lower() or "leave" in last.lower():
        return json.dumps({"performative":"refuse", "price": None})
    if "deal" in last.lower() or "accept" in last.lower():
        nums = re.findall(r"\d+", last)
        return json.dumps({"performative":"accept-proposal", "price": int(nums[-1]) if nums else None})
    nums = re.findall(r"\d+", last)

    if nums:
        return json.dumps({"performative": "propose", "price": int(nums[0])})
    
    return json.dumps({"performative": None, "price": None})

