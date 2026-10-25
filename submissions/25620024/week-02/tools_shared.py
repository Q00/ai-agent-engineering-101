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


def errors_by_hour(path: str, level: str = "ERROR") -> str:
    """Count log lines per hour for one level, without a regex.

    count_pattern takes a raw regex, and an hour written as "11:" also matches
    the minute field of 09:11:56, so per-hour counts came back inflated. This
    tool reads the hour from a fixed position instead: the log format is
    "DATE TIME LEVEL message", so the hour is the first two characters of
    field 2 and the level is field 3. No pattern to get wrong.
    """
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    counts = {}
    skipped = 0
    with open(full, encoding="utf-8") as f:
        for line in f:
            fields = line.split()
            if len(fields) < 3:
                skipped += 1 if line.strip() else 0
                continue
            if fields[2] != level:
                continue
            hour = fields[1][:2]
            if len(hour) != 2 or not hour.isdigit():
                skipped += 1
                continue
            counts[hour] = counts.get(hour, 0) + 1
    if not counts:
        return f"no {level} lines found in {path}"
    out = ", ".join(f"{h}:00={n}" for h, n in sorted(counts.items()))
    total = sum(counts.values())
    tail = f" (skipped {skipped} unparseable line(s))" if skipped else ""
    return f"{out}; total {level}={total}{tail}"


TOOLS_IMPL = {"read_file": read_file, "count_pattern": count_pattern,
              "errors_by_hour": errors_by_hour}

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
    {"name": "errors_by_hour",
     "description": (
         "Count a log file's lines per hour for one level, e.g. "
         "'09:00=1, 10:00=2, 14:00=6; total ERROR=19'. Reads the hour from a "
         "fixed field position, so it cannot miscount the way a regex hour "
         "such as '11:' does by also matching the minutes of 09:11:56. "
         "Prefer this over count_pattern for any per-hour question."),
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "level": {"type": "string",
                                             "description": "log level to count, default ERROR"}},
                    "required": ["path"]}},
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


PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"

# One AGENT_MODEL serves both providers, so a value left over from the other
# provider gets sent to the wrong API: an OpenRouter id such as
# "nvidia/nemotron-3.5-lightning:free" is a 404 on the Anthropic API, and the
# run dies as a crash row instead of a measurement. Ignore an id that cannot
# belong to the selected provider, and say so loudly.
_DEFAULTS = {"anthropic": "claude-haiku-4-5",   # cheapest, fastest current Claude
             "openai": "gpt-4o-mini"}
_requested = os.environ.get("AGENT_MODEL", "").strip()
if PROVIDER == "anthropic" and _requested and not _requested.startswith("claude-"):
    print(f"tools_shared: AGENT_MODEL={_requested!r} is not an Anthropic model "
          f"id; using {_DEFAULTS['anthropic']!r} instead.")
    _requested = ""
MODEL = _requested or _DEFAULTS[PROVIDER]

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
