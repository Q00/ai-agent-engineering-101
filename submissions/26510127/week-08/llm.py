"""A small harness: one chat-completions call, prompt on stdin, answer on stdout (~600 tokens a call).

    $env:HARNESS_CMD = "python llm.py"

Any OpenAI-compatible endpoint. The key is read from the environment only, never written to a file:
    LLM_API_KEY    the API key
    LLM_BASE_URL   e.g. https://openrouter.ai/api/v1  |  https://api.openai.com/v1
                        https://generativelanguage.googleapis.com/v1beta/openai   (Gemini)
    LLM_MODEL      the provider's model name
Why: an agent CLI (Codex) sends its own system prompt and tool list with every call, ~16k tokens even for
"say ok"; the market prompts are ~500 tokens and need no tools.
"""
import os
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")


def main():
    key, base, model = (os.environ.get(k) for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"))
    if not (key and base and model):
        sys.exit("set LLM_API_KEY, LLM_BASE_URL and LLM_MODEL")
    prompt = sys.stdin.read()
    r = httpx.post(base.rstrip("/") + "/chat/completions", timeout=60,
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": model, "max_tokens": 300, "temperature": 0.2,
                         "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        sys.exit(f"{r.status_code} {r.text[:200]}")
    print(r.json()["choices"][0]["message"]["content"].strip())


if __name__ == "__main__":
    main()
