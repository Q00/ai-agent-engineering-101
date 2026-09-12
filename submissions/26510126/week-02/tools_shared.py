"""Week 02 starter — tools, model call, and meter shared by both harnesses.

Both harnesses import from here. Same tools and same model for both is what
makes the A/B a harness comparison and not a tool comparison.

Provider is picked from the environment:
  ANTHROPIC_API_KEY set          -> Anthropic SDK (pip install anthropic)
  otherwise                      -> OpenAI-compatible (pip install openai)
                                    OPENAI_API_KEY, optional OPENAI_BASE_URL
                                    (https://openrouter.ai/api/v1 for OpenRouter)
  AGENT_MODEL                    optional model override for either provider
"""
import json
import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- tools


def read_file(path: str) -> str:
    """Return the contents of a text file in the working directory."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]          # context guard, same as week 01


def count_pattern(path: str, pattern: str) -> str:
    """Count lines in a text file that match a regular expression."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    rx = re.compile(pattern)
    with open(full, encoding="utf-8") as f:
        return str(sum(1 for line in f if rx.search(line)))


def count_by_hour(path: str, pattern: str) -> str:
    """Tally matching lines per clock hour, one 'HH:00 n' line per hour.

    The coarse counterpart of count_pattern: the same tally that costs one
    count_pattern call per hour costs one call here. Only in the coarse tool
    set (AGENT_TOOLSET=coarse).
    """
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    rx = re.compile(pattern)
    hours = {}
    with open(full, encoding="utf-8") as f:
        for line in f:
            if not rx.search(line):
                continue
            m = re.search(r"\b(\d{2}):\d{2}(?::\d{2})?\b", line)
            if m:
                hours[m.group(1)] = hours.get(m.group(1), 0) + 1
    if not hours:
        return "no matching lines with a recognisable HH:MM timestamp"
    return "\n".join(f"{h}:00 {n}" for h, n in sorted(hours.items()))


_READ_FILE_SPEC = {
    "name": "read_file",
    "description": "Read a text file in the working directory (first 4000 characters).",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}
_COUNT_PATTERN_SPEC = {
    "name": "count_pattern",
    "description": "Count the lines of a text file that match a regular expression.",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"},
                                  "pattern": {"type": "string"}},
                   "required": ["path", "pattern"]}}
_COUNT_BY_HOUR_SPEC = {
    "name": "count_by_hour",
    "description": ("Count the lines of a text file that match a regular expression, "
                    "grouped by the clock hour of each line's HH:MM timestamp. "
                    "Returns one 'HH:00 count' line per hour that has a match."),
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"},
                                  "pattern": {"type": "string"}},
                   "required": ["path", "pattern"]}}

# Tool granularity is element 2 of the five. It is a constant within any one
# A/B — both harnesses import whatever this module exposes — but it can be
# switched between experiments. "fine" is the starter set and the default, so
# the runs recorded against it stay reproducible from this file unchanged.
TOOLSET = os.environ.get("AGENT_TOOLSET", "fine")

if TOOLSET == "coarse":
    TOOLS_IMPL = {"read_file": read_file, "count_by_hour": count_by_hour}
    TOOL_SPECS = [_READ_FILE_SPEC, _COUNT_BY_HOUR_SPEC]
elif TOOLSET == "fine":
    TOOLS_IMPL = {"read_file": read_file, "count_pattern": count_pattern}
    TOOL_SPECS = [_READ_FILE_SPEC, _COUNT_PATTERN_SPEC]
else:
    raise SystemExit(f"AGENT_TOOLSET must be 'fine' or 'coarse', got {TOOLSET!r}")

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


PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")

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
