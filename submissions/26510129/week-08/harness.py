"""The one place that talks to the OpenAI Responses API.

    decide(system, situation) -> dict    parsed from the one-line JSON the model answers

The model only proposes a small JSON decision.  Deterministic code still owns the
budget, inventory, receipt checks, and signing authority.  OPENAI_API_KEY or
OPENROUTER_API_KEY is read from the environment and never written to disk or logs.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

OPENAI_API_URL = "https://api.openai.com/v1/responses"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/responses"
MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
MAX_OUTPUT_TOKENS = 48
TIMEOUT = 30          # the kit stops a buyer after 300 s; fail closed instead of blocking it
RETRIES = 1           # one malformed-output retry; keep token use bounded


def _payload(system: str, situation: str) -> dict:
    return {
        "model": MODEL,
        "reasoning": {"effort": "none"},
        "instructions": system,
        "input": situation,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }


def _route() -> tuple[str, str, str, str] | None:
    """Return provider, endpoint, model id, and key without logging the key."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openrouter_key:
        model = MODEL if "/" in MODEL else f"openai/{MODEL}"
        return "openrouter", OPENROUTER_API_URL, model, openrouter_key
    if openai_key and openai_key.startswith("sk-or-v1"):
        model = MODEL if "/" in MODEL else f"openai/{MODEL}"
        return "openrouter", OPENROUTER_API_URL, model, openai_key
    if openai_key:
        return "openai", OPENAI_API_URL, MODEL.removeprefix("openai/"), openai_key
    return None


def _output_text(response: dict) -> str:
    """Collect every output_text part; Responses may include non-message items."""
    chunks = []
    for item in response.get("output") or []:
        for part in item.get("content") or []:
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "\n".join(chunks).strip()


def call(system: str, situation: str) -> str:
    t0, size = time.time(), len(system) + len(situation)
    route = _route()
    if route is None:
        print("[harness] no OpenAI/OpenRouter API key is set; treated as no answer", file=sys.stderr, flush=True)
        return ""
    provider, api_url, model, api_key = route
    payload = _payload(system, situation)
    payload["model"] = model
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as raw:
            response = json.loads(raw.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", errors="replace")
        print(f"[harness] {provider} HTTP {exc.code} in={size}ch: {detail}", file=sys.stderr, flush=True)
        return ""
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"[harness] {provider} request failed in={size}ch: {type(exc).__name__}: {exc}",
              file=sys.stderr, flush=True)
        return ""
    out = _output_text(response)
    usage = response.get("usage") or {}
    print(f"[harness] {provider}/{model} {time.time() - t0:.1f}s in={usage.get('input_tokens', '?')}tok "
          f"out={usage.get('output_tokens', '?')}tok chars={size} -> {out[:200]!r}",
          file=sys.stderr, flush=True)
    return out


def parse(text: str, need: str = "action"):
    """The first JSON object in the answer that has the key `need`. Nested objects are fine; code fences are ignored."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            d, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and need in d:
            return d
    return None


def decide(system: str, situation: str, need: str = "action") -> dict | None:
    for _ in range(1 + RETRIES):
        d = parse(call(system, situation), need)
        if d is not None:
            if "price" in d:
                try:
                    d["price"] = int(round(float(d["price"])))
                except (TypeError, ValueError):
                    d.pop("price")
            return d
    return None
