"""Week 02 starter — tools, model call, and meter shared by both harnesses.

Both harnesses import from here. Same tools and same model for both is what
makes the A/B a harness comparison and not a tool comparison.

AGENT_PROVIDER defaults to codex (authenticated CLI, no Python SDK needed).
Explicit anthropic/openai providers retain the starter SDK implementations.
AGENT_MODEL overrides the model. Both harnesses use the same configuration.
For this benchmark only app.log is readable, excluding the reference answer.
"""
import json
import os
import re
from pathlib import Path
from dataclasses import dataclass, field

# ---------------------------------------------------------------- tools

WORKSPACE = Path(__file__).resolve().parent


def input_path(path):
    full = (WORKSPACE / path).resolve()
    if full != WORKSPACE / "app.log":
        raise ValueError("only the benchmark input app.log is readable")
    return full


def read_file(path: str) -> str:
    """Return the contents of a text file in the working directory."""
    full = input_path(path)
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]          # context guard, same as week 01


def count_pattern(path: str, pattern: str) -> str:
    """Count lines in a text file that match a regular expression."""
    full = input_path(path)
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
        self.tokens_complete = True

    def record_usage(self, input_tokens, output_tokens):
        self.tokens += input_tokens + output_tokens

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


PROVIDER = os.environ.get("AGENT_PROVIDER", "codex")
if PROVIDER not in ("codex", "anthropic", "openai"):
    raise ValueError("AGENT_PROVIDER must be codex, anthropic, or openai")
MODEL = os.environ.get(
    "AGENT_MODEL",
    {"codex": "gpt-6-astra", "anthropic": "claude-sonnet-4-5",
     "openai": "gpt-4o-mini"}[PROVIDER])

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
        if PROVIDER == "codex":
            self.messages.append({"role": "tool", "tool_call_id": call.id,
                                  "name": call.name, "content": output})
            return
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
        if PROVIDER == "codex":
            return self._send_codex()
        if PROVIDER == "anthropic":
            return self._send_anthropic()
        return self._send_openai()

    def _send_codex(self) -> Reply:
        from codex_backend import complete
        self.meter.iters += 1
        try:
            text, calls = complete(self.system, self.messages, TOOL_SPECS,
                                   self.tools, MODEL, self.meter.record_usage)
        except Exception:
            self.meter.tokens_complete = False
            raise
        tool_calls = [ToolCall(f"call-{self.meter.iters}-{i}", c["name"], c["args"])
                      for i, c in enumerate(calls)]
        self.messages.append({"role": "assistant", "text": text,
                              "tool_calls": [vars(c) for c in tool_calls]})
        return Reply(text, tool_calls)

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
