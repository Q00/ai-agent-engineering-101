"""The redesign harness's only model: OpenRouter, with the key the live harness uses.

The key is read from OPENROUTER_API_KEY (process, then the Windows user environment) and kept in memory
only. No Claude Code, no Anthropic API. Every call appends one line to work/calls.jsonl
(time, model, ms, ok, prompt/answer chars, tokens, cost) - never the key, never the prompt text.
"""
import json
import os
import time
import urllib.error
import urllib.request

from redesign import guard

URL = "https://openrouter.ai/api/v1/chat/completions"
CONFIG = guard.OWN / "config.json"


class LLMError(RuntimeError):
    pass


def _key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key = winreg.QueryValueEx(k, "OPENROUTER_API_KEY")[0]
        except OSError:
            key = None
    if not key:
        raise LLMError("OPENROUTER_API_KEY is not set")
    return key


def config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def ask(prompt, system=None, model=None, timeout=None, tag=""):
    cfg = config()
    model = model or cfg["model"]
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = json.dumps({"model": model, "messages": msgs, "usage": {"include": True}}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
    t0 = time.time()
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": tag, "model": model, "prompt_chars": len(prompt)}
    try:
        with urllib.request.urlopen(req, timeout=timeout or cfg.get("timeout_s", 120)) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        rec.update(ok=False, ms=int((time.time() - t0) * 1000), error=f"http {e.code}")
        guard.append("calls.jsonl", json.dumps(rec))
        raise LLMError(f"openrouter {e.code}: {e.read()[:200]!r}")
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
        rec.update(ok=False, ms=int((time.time() - t0) * 1000), error=type(e).__name__)
        guard.append("calls.jsonl", json.dumps(rec))
        raise LLMError(f"openrouter: {type(e).__name__}")
    u = data.get("usage") or {}
    rec.update(ok=True, ms=int((time.time() - t0) * 1000), answer_chars=len(text),
               tokens_in=u.get("prompt_tokens"), tokens_out=u.get("completion_tokens"), cost=u.get("cost"))
    guard.append("calls.jsonl", json.dumps(rec))
    return text
