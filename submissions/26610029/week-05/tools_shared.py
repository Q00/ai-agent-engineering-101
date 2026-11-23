"""Week 05 — model call shared by the buyer/seller hosts.

Anthropic only (this submission's provider choice throughout weeks 3-5).
Unlike week 04, this week's agents call real tools (the market's MCP tools),
so Chat takes a per-instance tool list (provider-neutral specs, converted
by the caller from whatever the MCP server's list_tools() returned) instead
of being toolless.

Requires: pip install anthropic, and in the environment (.env in this folder):
  ANTHROPIC_API_KEY   your key
  AGENT_MODEL         optional; defaults to claude-sonnet-4-5
"""
import os
import time
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


_load_dotenv()

TEMPERATURE = 1.0  # Anthropic default; top_p not used (API rejects setting both)
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-5")

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def create_message(**kwargs):
    """messages.create() with a growing wait on HTTP 429."""
    import anthropic
    wait = 2
    for attempt in range(5):
        try:
            return _get_client().messages.create(**kwargs)
        except anthropic.RateLimitError:
            if attempt == 4:
                raise
            time.sleep(wait)
            wait *= 2


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Reply:
    text: str
    tool_calls: list = field(default_factory=list)


class Chat:
    """One conversation with the model. `tools` is a list of provider-neutral
    specs: [{"name", "description", "parameters"}, ...] -- the caller converts
    them from whatever list_tools() returned."""

    def __init__(self, system: str, tools: list | None = None):
        self.system = system
        self.tools = tools or []
        self.messages = []

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def add_tool_result(self, call: ToolCall, output: str):
        block = {"type": "tool_result", "tool_use_id": call.id, "content": output}
        last = self.messages[-1] if self.messages else None
        if last and last["role"] == "user" and isinstance(last["content"], list):
            last["content"].append(block)
        else:
            self.messages.append({"role": "user", "content": [block]})

    def send(self) -> Reply:
        kwargs = dict(model=MODEL, max_tokens=1024, system=self.system,
                      messages=self.messages, extra_body={"temperature": TEMPERATURE})
        if self.tools:
            kwargs["tools"] = [{"name": t["name"], "description": t["description"],
                                "input_schema": t["parameters"]} for t in self.tools]
        resp = create_message(**kwargs)
        self.messages.append({"role": "assistant", "content": resp.content})
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input))
                 for b in resp.content if b.type == "tool_use"]
        return Reply(text, calls)
