"""Week 02 starter — tools, model call, and meter shared by both harnesses.

Both harnesses import from here. Same tools and same model for both is what
makes the A/B a harness comparison and not a tool comparison.

This submission fixes the provider to OpenRouter's OpenAI-compatible API.
Set OPENROUTER_API_KEY (or OPENAI_API_KEY) in the environment. Both harnesses
use the same explicit :free model; there is no model fallback.
"""
import json
import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- tools


def read_file(path: str) -> str:
    """Return the public starter app.log used by this experiment."""
    full = os.path.abspath(path)
    if os.path.normcase(os.path.realpath(full)) != os.path.normcase(os.path.realpath("app.log")):
        return "denied: this experiment only permits app.log"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]          # context guard, same as week 01


def count_pattern(path: str, pattern: str) -> str:
    """Count lines in the public starter app.log matching a regular expression."""
    full = os.path.abspath(path)
    if os.path.normcase(os.path.realpath(full)) != os.path.normcase(os.path.realpath("app.log")):
        return "denied: this experiment only permits app.log"
    rx = re.compile(pattern)
    with open(full, encoding="utf-8") as f:
        return str(sum(1 for line in f if rx.search(line)))


TOOLS_IMPL = {"read_file": read_file, "count_pattern": count_pattern}

# provider-neutral schemas; Chat converts them per provider
TOOL_SPECS = [
    {"name": "read_file",
     "description": "Read the public starter app.log (first 4000 characters). No other files are accessible.",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string", "enum": ["app.log"]}},
                    "required": ["path"]}},
    {"name": "count_pattern",
     "description": "Count lines in the public starter app.log that match a regular expression.",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string", "enum": ["app.log"]},
                                   "pattern": {"type": "string"}},
                    "required": ["path", "pattern"]}},
]

# ---------------------------------------------------------------- meter


class StepLimitReached(RuntimeError):
    """The shared budget includes planning, execution and summarization."""


class Meter:
    """The four metrics of the lab, counted in one place."""

    def __init__(self, max_steps=16, log=print):
        self.tokens = 0
        self.iters = 0            # one iteration = one attempted model call
        self.interventions = 0    # times a human approved or denied a call
        self.max_steps = max_steps
        self.log = log

    def begin_call(self):
        if self.iters >= self.max_steps:
            raise StepLimitReached("MAX_STEPS reached: incomplete")
        self.iters += 1

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)


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


PROVIDER = "openai"  # message format; the actual provider is OpenRouter
BASE_URL = "https://openrouter.ai/api/v1"
MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free")
MAX_TOKENS = 2048
TEMPERATURE = 0.2
TIMEOUT = 45.0
# OpenRouter's unified reasoning option. Keeping this shared makes the new A/B
# condition fair: both harnesses use the same model with the same reasoning mode.
REASONING_EFFORT = "none"

_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()
        else:
            from openai import OpenAI
            key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("Set OPENROUTER_API_KEY in the environment before running")
            if not MODEL.endswith(":free"):
                raise ValueError("AGENT_MODEL must be a fixed model ID ending in :free")
            _client = OpenAI(api_key=key, base_url=BASE_URL,
                             timeout=TIMEOUT, max_retries=0)
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
        self.meter.begin_call()
        self.meter.log("[request] " + json.dumps({
            "call": self.meter.iters, "model": MODEL,
            "system": self.system, "messages": self.messages,
            "tools": TOOL_SPECS if self.tools else [],
            "max_tokens": MAX_TOKENS, "temperature": TEMPERATURE,
            "reasoning": {"effort": REASONING_EFFORT},
        }, ensure_ascii=False, default=lambda value: value.model_dump(exclude_none=True)))
        if PROVIDER == "anthropic":
            reply = self._send_anthropic()
        else:
            reply = self._send_openai()
        self.meter.log("[response]\n" + reply.text)
        self.meter.log(f"[meter] tokens={self.meter.tokens} iters={self.meter.iters}")
        return reply

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
        kwargs = dict(model=MODEL, messages=self.messages,
                      max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                      extra_body={"reasoning": {"effort": REASONING_EFFORT}})
        if self.tools:
            kwargs["tools"] = [{"type": "function",
                                "function": {"name": t["name"],
                                             "description": t["description"],
                                             "parameters": t["parameters"]}}
                               for t in TOOL_SPECS]
        resp = _get_client().chat.completions.create(**kwargs)
        self.meter.log(f"[provider-response] id={resp.id} model={resp.model}")
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
            log("[Action] " + json.dumps({"id": call.id, "name": call.name,
                                         "args": call.args}, ensure_ascii=False))
            log("[Observation]\n" + out)
            self.add_tool_result(call, out)
