"""The one door to a model: OpenAI's Chat Completions API.

ask() returns "" when there is no key, or the call fails or is slow, so every
caller needs a rule to fall back on. The trading modules use the cheap model,
only where the code cannot read a buyer's sentence.

    OPENAI_API_KEY      the key (on the server from ~/market/openai.env, never in the repo)
    OPENAI_MODEL        the cheap model, gpt-6-luna unless set
    HARNESS_FAKE_REPLY  tests only: every ask() returns this text and calls nothing
"""
import json
import os
import re

import httpx

MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
URL = "https://api.openai.com/v1/chat/completions"


def ask(prompt: str, model: str | None = None, timeout: float = 20.0) -> str:
    if os.environ.get("HARNESS_FAKE_REPLY") is not None:
        return os.environ["HARNESS_FAKE_REPLY"]
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return ""
    try:
        r = httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, timeout=timeout,
                       json={"model": model or MODEL, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:                                       # noqa: BLE001
        print(f"[harness] {type(e).__name__}; falling back to rules", flush=True)
        return ""


def ask_json(prompt: str, model: str | None = None) -> dict | None:
    """The first {...} object in the answer, or None."""
    m = re.search(r"\{.*\}", ask(prompt, model) or "", re.S)
    try:
        return json.loads(m.group()) if m else None
    except ValueError:
        return None
