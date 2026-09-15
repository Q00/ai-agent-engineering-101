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
import time
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


MAX_RETRIES = 6
BASE_DELAY = 8.0

# Proactive pacing beats reactive retry against a requests-per-minute cap: a
# retry that fires while the window is still full just burns another wait (the
# provider answered "retry in 53.8s" twice in a row before this was added).
# MIN_INTERVAL keeps the call rate under the cap instead of discovering it.
# 0 disables pacing, for a provider that does not need it.
MIN_INTERVAL = float(os.environ.get("AGENT_MIN_INTERVAL", "0"))
_last_call = [0.0]


def _pace():
    if MIN_INTERVAL <= 0:
        return
    wait = MIN_INTERVAL - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def _is_rate_limit(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 429 or "429" in str(e)


def _retry_delay(e: Exception, attempt: int) -> float:
    """Honour the provider's own retry hint when it gives one."""
    m = re.search(r"retry in (\d+(?:\.\d+)?)s", str(e))
    if m:
        return float(m.group(1)) + 1.0
    return BASE_DELAY * (attempt + 1)


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
        """One model call, with transport-level retry on 429.

        Free tiers cap requests per minute (Gemini free: 5/min/model), and
        Plan-then-Execute issues ~11 calls per run, so without this the harness
        with more iterations cannot finish at all -- which would make the A/B a
        comparison of who hit the quota first rather than of the two designs.

        This sits below both harnesses in the shared module, so it applies to
        each identically, and meter.add() still runs only on a call that
        succeeded: retries cost wall-clock time and change no measured metric.
        A daily-quota 429 is not retried away -- it just exhausts the attempts.
        """
        for attempt in range(MAX_RETRIES):
            _pace()
            try:
                if PROVIDER == "anthropic":
                    return self._send_anthropic()
                return self._send_openai()
            except Exception as e:
                if not _is_rate_limit(e) or attempt == MAX_RETRIES - 1:
                    raise
                delay = _retry_delay(e, attempt)
                print(f"  [429] rate limited, retrying in {delay:.1f}s "
                      f"(attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(delay)
        raise RuntimeError("unreachable")

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
