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
                os.environ.setdefault(key.strip(), value.strip())
            return


_load_dotenv()

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
# The installed anthropic SDK (1.5.0) no longer exposes a `temperature`
# parameter on Messages.create -- it was dropped from the Messages API.
# Not settable, same convention as the week-03 note for a CLI that does
# not expose it: recorded here rather than invented.
TEMPERATURE = "not settable (Messages API on this SDK/model has no temperature parameter)"

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
            )
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError):
            if attempt == 5:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")
