"""Week 02 assignment — shared tools, model wrapper, and meter.

Both harnesses import from here. The model and the tool set are held constant;
that is what makes this a *harness* comparison rather than a tool comparison.
What each harness is free to vary is **axis 1, context management**: the ReAct
harness resends the whole transcript every call, while the Plan-then-Execute
harness carries only a compact ledger. Because context lives in each harness's
own message list, this module exposes a plain `call_model(messages, meter)`
instead of a single shared Chat object.

Provider: OpenAI-compatible (OpenRouter). Reads OPENAI_API_KEY, OPENAI_BASE_URL,
and AGENT_MODEL from the environment. No key is stored in this file.

    export OPENAI_BASE_URL=https://openrouter.ai/api/v1
    export OPENAI_API_KEY=<your key>
    export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
"""
import json
import os
import re
from dataclasses import dataclass

# ------------------------------------------------------------------ tools
# Identical to the lab tools. Held constant across both harnesses.


def read_file(path: str) -> str:
    """Return the first 4000 characters of a text file in the working dir."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


def count_pattern(path: str, pattern: str) -> str:
    """Count the lines of a text file that match a regular expression."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"error: bad pattern: {e}"
    with open(full, encoding="utf-8") as f:
        return str(sum(1 for line in f if rx.search(line)))


TOOLS_IMPL = {"read_file": read_file, "count_pattern": count_pattern}

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file in the working directory (first 4000 characters).",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "count_pattern",
        "description": "Count the lines of a text file that match a regular expression.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"},
                                      "pattern": {"type": "string"}},
                       "required": ["path", "pattern"]}}},
]

# ------------------------------------------------------------------ meter


class Meter:
    """The four lab metrics, counted in one place."""

    def __init__(self):
        self.tokens = 0
        self.iters = 0            # one iteration = one model call
        self.interventions = 0    # human approvals/denials (read-only tools -> 0)

    def add(self, in_tok, out_tok):
        self.tokens += int(in_tok or 0) + int(out_tok or 0)
        self.iters += 1


# ------------------------------------------------------------------ model


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free")
_client = None


def _client_once():
    global _client
    if _client is None:
        from openai import OpenAI
        # Short per-request timeout + few retries so a slow free-tier call is
        # recorded as a failure instead of hanging. Applies to both harnesses,
        # so the A/B stays fair.
        _client = OpenAI(timeout=90.0, max_retries=1)
    return _client


def call_model(messages, meter, tools=True):
    """One model call. Returns (assistant_message, text, [ToolCall]).

    The caller owns `messages`, so *context management (axis 1) lives in the
    harness*, not here.
    """
    kwargs = dict(model=MODEL, messages=messages)
    if tools:
        kwargs["tools"] = TOOL_SPECS
    resp = _client_once().chat.completions.create(**kwargs)
    u = resp.usage
    meter.add(getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0))
    msg = resp.choices[0].message
    calls = []
    for c in (msg.tool_calls or []):
        try:
            args = json.loads(c.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append(ToolCall(c.id, c.function.name, args))
    return msg, (msg.content or ""), calls


def run_tool_calls(msg, calls, messages, log=print):
    """Append the assistant message and each tool result onto `messages`.

    An exception from a tool comes back as an Observation (axis 4, error
    recovery) rather than crashing the run.
    """
    messages.append(msg)
    for c in calls:
        fn = TOOLS_IMPL.get(c.name)
        try:
            out = str(fn(**c.args)) if fn else f"error: unknown tool {c.name}"
        except Exception as e:                       # error -> Observation
            out = f"error: {e}"
        log(f"  [tool] {c.name}({c.args}) -> {out[:120].replace(chr(10), ' | ')}")
        messages.append({"role": "tool", "tool_call_id": c.id, "content": out})
