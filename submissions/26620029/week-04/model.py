"""Model call wrapper shared by the agents and the reader.

Loads ANTHROPIC_API_KEY from a .env file at the repository root (or any
parent directory of this file) if it is not already in the environment, so
running the scripts does not require exporting the key by hand.
"""
import os
import time
from pathlib import Path

import anthropic


def _load_dotenv():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        env_path = parent / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                os.environ.setdefault(key.strip(), value)
            return


_load_dotenv()

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
# The installed anthropic SDK (1.8.0, not 1.5.0 as an earlier note here said)
# dropped `temperature` from Messages.create()'s typed keyword arguments --
# confirmed by reading the SDK source, not just from a missing-argument
# error. That is an SDK-binding change, not an API change: a direct call
# with extra_body={"temperature": ...} against this same model succeeds, so
# the Messages API itself still accepts it. TEMPERATURE below is sent via
# extra_body on every call and is Anthropic's own documented default
# (1.0), chosen so pinning it does not change behavior versus the earlier
# runs that never set it -- only makes the value explicit and reproducible.
TEMPERATURE = 1.0

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def call_model(system: str, messages: list, max_tokens: int = 200) -> str:
    """One model call. messages: [{"role": "user"|"assistant", "content": str}, ...]."""
    delay = 2.0
    for attempt in range(6):
        try:
            resp = _get_client().messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                extra_body={"temperature": TEMPERATURE},
            )
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError):
            if attempt == 5:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")
