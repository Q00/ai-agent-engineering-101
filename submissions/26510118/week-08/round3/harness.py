"""The one door to a model: OpenAI's Chat Completions API.

ask() returns "" when there is no key, or the call fails or is slow, so every
caller needs a rule to fall back on. Three models, placed by how often and how much
a decision matters (prices per million tokens, input/output, OpenAI's table of 11/2):

    MODEL         gpt-6-luna     $0.10/$0.50  trading modules: a buyer's or shop's sentence
                                              the code cannot read (many calls, small)
    MODEL_MID     gpt-6.1-terra  $1/$5        the seller and buyer analyses, every 3 hours
    MODEL_STRONG  gpt-6.1-sol    $2/$10       the integrated analysis that changes the rules,
                                              every 12 hours

    OPENAI_API_KEY      the key (on the server from ~/market/openai.env, never in the repo)
    OPENAI_MODEL, OPENAI_MODEL_MID, OPENAI_MODEL_STRONG   override the three names
    HARNESS_FAKE_REPLY  tests only: every ask() returns this text and calls nothing
"""
import json
import os
import re

import httpx

MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
MODEL_MID = os.environ.get("OPENAI_MODEL_MID", "gpt-6.1-terra")
MODEL_STRONG = os.environ.get("OPENAI_MODEL_STRONG", "gpt-6.1-sol")
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
    return first_json(ask(prompt, model))


def first_json(text: str | None) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group()) if m else None
    except ValueError:
        return None


def chat(messages: list, model: str, tools: list | None = None, timeout: float = 90.0) -> dict | None:
    """One Chat Completions turn with function tools; the assistant message, or None."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    body = {"model": model, "messages": messages}
    if tools:
        body.update(tools=tools, parallel_tool_calls=False)
    try:
        r = httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]
    except Exception as e:                                       # noqa: BLE001
        print(f"[harness] {model}: {type(e).__name__}: {str(e)[:200]}", flush=True)
        return None


def run_tools(messages: list, model: str, tools: list, run, max_calls: int, chat_fn=None) -> str:
    """Let the model call tools at most max_calls times, then take its final text. The code counts
    the calls and runs the tools; `run(name, args) -> text` is the only way a tool is executed."""
    chat_fn = chat_fn or chat
    calls = 0
    while True:
        msg = chat_fn(messages, model, tools if calls < max_calls else None)
        if not msg:
            return ""
        wanted = msg.get("tool_calls") or []
        if not wanted or calls >= max_calls:
            return msg.get("content") or ""
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": wanted})
        for call in wanted:
            calls += 1
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": run(call["function"]["name"], args) if calls <= max_calls
                             else json.dumps({"error": "tool budget spent"})})
