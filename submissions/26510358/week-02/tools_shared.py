"""Week 02 starter — tools, model call, and meter shared by both harnesses.

Both harnesses import from here. Same tools and same model for both is what
makes the A/B a harness comparison and not a tool comparison.

Settings are loaded from this module's .env file without overriding the
process environment. Provider is picked from the environment:
  AGENT_PROVIDER                optional explicit choice: openai | anthropic
  ANTHROPIC_API_KEY set          -> Anthropic SDK (pip install anthropic)
  otherwise                      -> OpenAI-compatible (pip install openai)
                                    OPENAI_API_KEY, optional OPENAI_BASE_URL
                                    (https://openrouter.ai/api/v1 for OpenRouter)
  AGENT_MODEL                    optional model override for either provider
  AGENT_REASONING_EFFORT          optional OpenAI reasoning_effort override
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

# ---------------------------------------------------------------- tools


def _input_path(path: str) -> Path | None:
    """Keep file tools in the working directory and exclude local credentials."""
    full = Path(path).resolve()
    if not full.is_relative_to(Path.cwd().resolve()) or full.name == ".env":
        return None
    return full


def read_file(path: str) -> str:
    """Return the contents of a text file in the working directory."""
    full = _input_path(path)
    if full is None:
        return "denied: protected file or path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]          # context guard, same as week 01


def count_pattern(path: str, pattern: str) -> str:
    """Count lines in a text file that match a regular expression."""
    full = _input_path(path)
    if full is None:
        return "denied: protected file or path outside the working directory"
    rx = re.compile(pattern)
    with open(full, encoding="utf-8") as f:
        return str(sum(1 for line in f if rx.search(line)))


TOOLS_IMPL = {"read_file": read_file, "count_pattern": count_pattern}

# provider-neutral schemas; Chat converts them per provider
TOOL_SPECS = [
    {"name": "read_file",
     "description": "Read a text file in the working directory (first 4000 characters).",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]}},
    {"name": "count_pattern",
     "description": "Count the lines of a text file that match a regular expression.",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "pattern": {"type": "string"}},
                    "required": ["path", "pattern"]}},
]

# ---------------------------------------------------------------- meter


class Meter:
    """The four metrics of the lab, counted in one place."""

    def __init__(self):
        self.tokens = 0
        self.iters = 0            # one iteration = one model call
        self.interventions = 0    # times a human approved or denied a call

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.iters += 1


# ---------------------------------------------------------------- model


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Reply:
    text: str
    tool_calls: list = field(default_factory=list)


PROVIDER = os.environ.get("AGENT_PROVIDER", "").strip().lower() or (
    "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai")
if PROVIDER not in ("openai", "anthropic"):
    raise SystemExit("AGENT_PROVIDER must be 'openai' or 'anthropic'.")
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
REASONING_EFFORT = os.environ.get("AGENT_REASONING_EFFORT", "").strip() or None

_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()
        else:
            from openai import OpenAI
            _client = OpenAI()
    return _client


class Chat:
    """One conversation with the model. Owns the provider-specific message
    format so the harnesses only see Reply and ToolCall."""

    def __init__(self, system: str, meter: Meter, tools: bool = True):
        self.system = system
        self.meter = meter
        self.tools = tools
        self.messages = []
        if PROVIDER == "openai":
            self.messages.append({"role": "system", "content": system})

    # ---- building the next turn
    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def add_tool_result(self, call: ToolCall, output: str):
        if PROVIDER == "anthropic":
            block = {"type": "tool_result", "tool_use_id": call.id, "content": output}
            last = self.messages[-1]
            if last["role"] == "user" and isinstance(last["content"], list):
                last["content"].append(block)
            else:
                self.messages.append({"role": "user", "content": [block]})
        else:
            self.messages.append({"role": "tool", "tool_call_id": call.id,
                                  "content": output})

    # ---- one model call
    def send(self) -> Reply:
        if PROVIDER == "anthropic":
            return self._send_anthropic()
        return self._send_openai()

    def _send_anthropic(self) -> Reply:
        kwargs = dict(model=MODEL, max_tokens=1024, system=self.system,
                      messages=self.messages)
        if self.tools:
            kwargs["tools"] = [{"name": t["name"], "description": t["description"],
                                "input_schema": t["parameters"]} for t in TOOL_SPECS]
        resp = _get_client().messages.create(**kwargs)
        self.meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        self.messages.append({"role": "assistant", "content": resp.content})
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input))
                 for b in resp.content if b.type == "tool_use"]
        return Reply(text, calls)

    def _send_openai(self) -> Reply:
        kwargs = dict(model=MODEL, messages=self.messages)
        if REASONING_EFFORT is not None:
            kwargs["reasoning_effort"] = REASONING_EFFORT
        if self.tools:
            kwargs["tools"] = [{"type": "function",
                                "function": {"name": t["name"],
                                             "description": t["description"],
                                             "parameters": t["parameters"]}}
                               for t in TOOL_SPECS]
        resp = _get_client().chat.completions.create(**kwargs)
        usage = resp.usage
        self.meter.add(getattr(usage, "prompt_tokens", 0),
                       getattr(usage, "completion_tokens", 0))
        msg = resp.choices[0].message
        self.messages.append(msg)
        calls = []
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": c.function.arguments}
            calls.append(ToolCall(c.id, c.function.name, args))
        return Reply(msg.content or "", calls)

    # ---- run the tools a reply asked for, feed results back
    def run_tools(self, reply: Reply, log=print) -> None:
        for call in reply.tool_calls:
            fn = TOOLS_IMPL.get(call.name)
            if fn is None:
                out = f"error: unknown tool {call.name}"
            else:
                try:
                    out = str(fn(**call.args))
                except Exception as e:           # error recovery: the error is an Observation
                    out = f"error: {e}"
            log(f"  [tool] {call.name}({call.args}) -> {out[:200].replace(chr(10), ' | ')}")
            self.add_tool_result(call, out)
