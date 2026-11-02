"""The one door to a model: OpenAI's Chat Completions API.

The basic pipeline decides everything by rules and does not call it yet; the
door is here so the enhanced version can. ask() returns "" when there is no
key, or the call fails or is slow, so every caller needs a rule to fall back on.

    OPENAI_API_KEY   the key (on the server from ~/market/openai.env, never in the repo)
    OPENAI_MODEL     the model, gpt-6-luna unless set
"""
import os

import httpx

MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
URL = "https://api.openai.com/v1/chat/completions"


def ask(prompt: str, model: str | None = None, timeout: float = 20.0) -> str:
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
