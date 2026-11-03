"""Injectable LLM client interface, a hard wall-clock timeout wrapper, and mock clients (tests only).

LLMClient: any object with complete(prompt: str, *, max_tokens: int, timeout_s: float) -> str.
call_with_timeout(): runs complete() in a daemon worker thread and returns None after timeout_s (the worker is
abandoned, never joined on the caller's path), and None on any exception. The caller then takes the rule path.

Token accounting has no tokenizer: est_tokens(text) = ceil(utf8 bytes / 2). ASCII runs about 4 chars/token and
Korean about 1-2 tokens per 3-byte syllable, so bytes/2 over-counts both (a cap expressed in these units is safe)."""
import json
import math
import threading
import time


def est_tokens(text):
    return math.ceil(len((text or "").encode("utf-8")) / 2)


class LLMClient:
    """Interface. A real implementation must be approved separately (paid / subscription-limited API)."""

    def complete(self, prompt, *, max_tokens, timeout_s):      # pragma: no cover - interface
        raise NotImplementedError


def call_with_timeout(client, prompt, max_tokens, timeout_s):
    """-> (text or None, status) status in ok | timeout | error:<Type> | bad_type."""
    box = {}

    def run():
        try:
            box["out"] = client.complete(prompt, max_tokens=max_tokens, timeout_s=timeout_s)
        except BaseException as e:            # noqa: BLE001 - any failure falls back to rules
            box["err"] = type(e).__name__

    t = threading.Thread(target=run, daemon=True, name="llm-call")
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        return None, "timeout"
    if "err" in box:
        return None, f"error:{box['err']}"
    out = box.get("out")
    if not isinstance(out, str):
        return None, "bad_type"
    return out, "ok"


# ---------------------------------------------------------------- mock clients (tests only; no network)
class MockClient(LLMClient):
    """Returns `reply` (str, or callable(prompt) -> str). Records every prompt it was given."""

    def __init__(self, reply="", delay_s=0.0, exc=None):
        self.reply, self.delay_s, self.exc = reply, delay_s, exc
        self.prompts = []

    def complete(self, prompt, *, max_tokens, timeout_s):
        self.prompts.append(prompt)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.exc is not None:
            raise self.exc
        return self.reply(prompt) if callable(self.reply) else self.reply


def json_reply(obj):
    return json.dumps(obj, ensure_ascii=False)
