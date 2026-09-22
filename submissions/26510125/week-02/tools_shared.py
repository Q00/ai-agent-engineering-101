"""Week 02 assignment — tools, model call, and Meter shared by both harnesses.

Reinterpreted from the starter: single provider (OpenAI-compatible, so this
talks to OpenRouter with two env vars), and every control signal a harness
needs from the model (stop, plan, off-plan) is asked for as a tool call
instead of a magic string in free text. The hypothesis behind that choice is
in REPORT.md; the point of keeping it in one shared module is unchanged from
the starter: same tools, same model, same Meter for both harnesses, so the
A/B isolates the harness.

Requires: pip install openai
  OPENAI_API_KEY    your key (an OpenRouter key works)
  OPENAI_BASE_URL   optional; https://openrouter.ai/api/v1 for OpenRouter
  AGENT_MODEL       optional; defaults to gpt-4o-mini
"""
import os
from dataclasses import dataclass, field

from openai import OpenAI

# ---------------------------------------------------------------- tools


def read_file(path: str) -> str:
    """Return the contents of a text file in the working directory."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


def count_pattern(path: str, pattern: str) -> str:
    """Count lines in a text file that match a regular expression."""
    import re
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    rx = re.compile(pattern)
    with open(full, encoding="utf-8") as f:
        return str(sum(1 for line in f if rx.search(line)))


TASK_TOOLS = {"read_file": read_file, "count_pattern": count_pattern}

TASK_TOOL_SPECS = [
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

# ---------------------------------------------------------------- meter


class Meter:
    """The three metrics results.csv wants, counted in one place."""

    def __init__(self):
        self.tokens = 0
        self.iters = 0            # one iteration = one model call
        self.interventions = 0    # times a human approved or denied a call

    def add(self, usage) -> None:
        if usage is not None:
            self.tokens += (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
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


MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


class Chat:
    """One conversation with the model, with a chooseable tool set per call
    so a harness can hand the model a control-flow tool (submit_plan,
    report_off_plan) instead of asking it to write a magic string."""

    def __init__(self, system: str, meter: Meter):
        self.meter = meter
        self.messages = [{"role": "system", "content": system}]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_result(self, call: ToolCall, output: str) -> None:
        self.messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

    def send(self, tool_specs=None, force_tool: str | None = None) -> Reply:
        kwargs = dict(model=MODEL, messages=self.messages)
        if tool_specs:
            kwargs["tools"] = tool_specs
        if force_tool:
            kwargs["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
        resp = _get_client().chat.completions.create(**kwargs)
        self.meter.add(resp.usage)
        msg = resp.choices[0].message
        self.messages.append(msg)
        calls = []
        for c in msg.tool_calls or []:
            import json
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": c.function.arguments}
            calls.append(ToolCall(c.id, c.function.name, args))
        return Reply(msg.content or "", calls)

    def run_task_tools(self, reply: Reply, log=print) -> None:
        """Execute only TASK_TOOLS calls (control-flow tool calls are handled
        by the harness itself, since only the harness knows what they mean)."""
        for call in reply.tool_calls:
            if call.name not in TASK_TOOLS:
                continue
            try:
                out = str(TASK_TOOLS[call.name](**call.args))
            except Exception as e:          # error recovery: the error is an Observation
                out = f"error: {e}"
            log(f"  [tool] {call.name}({call.args}) -> {out[:200].replace(chr(10), ' | ')}")
            self.add_tool_result(call, out)
