"""Week 02 - tools, model wrapper and meter shared by BOTH harnesses.

The A/B experiment varies the harness and nothing else, so everything that is
supposed to be a constant lives here and is imported by harness_react.py and
harness_plan_execute.py alike:

  * the tool set    read_file, count_pattern   (axis 2, held constant)
  * the model call  one provider, one model name, one max_tokens
  * the meter       tokens / iters / interventions

Provider: OpenRouter through the OpenAI-compatible API.

    export OPENAI_BASE_URL=https://openrouter.ai/api/v1
    export OPENAI_API_KEY=<openrouter key>
    export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free   # optional, the default

--dry-run swaps in a scripted fake model so the harness logic can be debugged
without spending a request. The free tier allows about 50 requests a day and
the six graded runs need roughly 40 of them.
"""
import json
import os
import re
import sys

MODEL = os.environ.get("AGENT_MODEL", "anthropic/claude-sonnet-4.5")

# Left unset, so no max_tokens is sent and the provider default applies.
# Runs 1-6 were made with max_tokens=1024, the value in the lecture skeleton,
# and every one of them was truncated mid-sentence: the plan arm never reached
# the JSON array it was asked for and one react run returned a cut-off
# reasoning dump as its final answer. The cap was a constant shared by both
# arms, but it did not cost them the same, which is a finding in REPORT.md
# rather than something to leave in place.
MAX_TOKENS = int(os.environ["AGENT_MAX_TOKENS"]) if os.environ.get("AGENT_MAX_TOKENS") else None

HERE = os.path.dirname(os.path.abspath(__file__))
READ_CAP = 4000


# --------------------------------------------------------------------------
# tools (axis 2: granularity). Identical for both harnesses.
# --------------------------------------------------------------------------

def _resolve(path):
    """Keep tools inside the submission directory."""
    full = os.path.abspath(os.path.join(HERE, path))
    if os.path.commonpath([full, HERE]) != HERE:
        raise ValueError("denied: " + path + " is outside the submission directory")
    if not os.path.isfile(full):
        raise FileNotFoundError("no such file: " + path)
    return full


def read_file(path):
    """Return the text of a file, truncated to READ_CAP characters."""
    with open(_resolve(path), encoding="utf-8") as f:
        return f.read()[:READ_CAP]


def count_pattern(path, pattern):
    """Count regex matches of `pattern` over the whole file at `path`.

    The lecture skeleton signs this as count_pattern(text, pattern). It takes a
    path here instead: app.log is 3082 bytes, so read_file returns all of it,
    and passing that text back as a tool argument on every call would add ~800
    tokens per call that have nothing to do with the task. The change applies
    to both harnesses, so axis 2 stays constant between the two arms.

    A bad regex raises re.error on purpose. How that exception is handled is
    axis 4, and axis 4 is one of the things this A/B measures.
    """
    with open(_resolve(path), encoding="utf-8") as f:
        text = f.read()
    return len(re.findall(pattern, text))


TOOLS = {"read_file": read_file, "count_pattern": count_pattern}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file and return its contents (first 4000 characters).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "for example app.log"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_pattern",
            "description": ("Count how many times a Python regular expression matches "
                            "inside the file at path. Returns an integer."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "for example app.log"},
                    "pattern": {"type": "string",
                                "description": "Python regex. Example: ERROR"},
                },
                "required": ["path", "pattern"],
            },
        },
    },
]


# --------------------------------------------------------------------------
# meter - three of the four metrics in results.csv (success is judged outside)
# --------------------------------------------------------------------------

class Meter:
    """interventions stays 0 for this experiment.

    The task only reads and counts, so there is no irreversible action to stop
    in front of. Axis 5 is fixed to fully autonomous on BOTH arms, which makes
    it a constant rather than an unmeasured variable. See TASK.md.
    """

    def __init__(self):
        self.tokens = 0
        self.iters = 0
        self.interventions = 0

    def add(self, n_in, n_out):
        self.tokens += (n_in or 0) + (n_out or 0)
        self.iters += 1

    def __str__(self):
        return "tokens=%d iters=%d interventions=%d" % (
            self.tokens, self.iters, self.interventions)


# --------------------------------------------------------------------------
# model reply - the shape the harness code reads (mirrors the lecture skeleton)
# --------------------------------------------------------------------------

class ToolCall:
    def __init__(self, id, name, args):
        self.id = id
        self.name = name
        self.args = args

    def __str__(self):
        return "%s(%s)" % (self.name, json.dumps(self.args, ensure_ascii=False))


class Reply:
    def __init__(self, text, tool_calls=None, finish_reason=None):
        self.text = text or ""
        self.tool_calls = tool_calls or []
        # "length" means the provider cut the reply off at the token ceiling.
        # Without this the harness cannot tell a finished answer from a
        # truncated one, and in runs 1-6 it could not.
        self.finish_reason = finish_reason

    @property
    def truncated(self):
        return self.finish_reason == "length"

    @property
    def tool_call(self):
        """The lecture skeleton branches on `reply.tool_call is None`."""
        return self.tool_calls[0] if self.tool_calls else None

    def as_message(self):
        """The assistant turn to append to history."""
        m = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            m["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name,
                              "arguments": json.dumps(c.args, ensure_ascii=False)}}
                for c in self.tool_calls
            ]
        return m


def observation(tool_call_id, content):
    """A tool-result turn."""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": str(content)}


# --------------------------------------------------------------------------
# the model call
# --------------------------------------------------------------------------

_client = None
_fake = None


def use_fake_model(script):
    """Install a scripted model. No network, no requests spent.

    `script` is a list of (text, [(tool_name, args_dict), ...]) pairs, consumed
    one per model turn.
    """
    global _fake
    _fake = list(script)


def fake_remaining():
    return None if _fake is None else len(_fake)


def _client_once():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(
            base_url=os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
            api_key=os.environ["OPENAI_API_KEY"],
        )
    return _client


def call_model(messages, meter, tools=None):
    """One model turn. Increments meter.iters and adds the token usage."""
    if _fake is not None:
        if not _fake:
            raise RuntimeError("dry-run script exhausted: the harness asked for one "
                               "more model turn than the script provides")
        text, calls = _fake.pop(0)
        meter.add(len(json.dumps(messages, ensure_ascii=False)) // 4, 40)
        return Reply(text, [ToolCall("fake_%d_%d" % (meter.iters, i), n, a)
                            for i, (n, a) in enumerate(calls)],
                     finish_reason="tool_calls" if calls else "stop")

    kwargs = dict(model=MODEL, messages=messages)
    if MAX_TOKENS:
        kwargs["max_tokens"] = MAX_TOKENS
    if tools:
        kwargs["tools"] = tools
    resp = _client_once().chat.completions.create(**kwargs)

    usage = getattr(resp, "usage", None)
    meter.add(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))

    msg = resp.choices[0].message
    calls = []
    for c in (getattr(msg, "tool_calls", None) or []):
        try:
            args = json.loads(c.function.arguments or "{}")
        except json.JSONDecodeError:
            # A malformed argument string is a real failure mode, not a crash.
            args = {"__unparsed__": c.function.arguments}
        calls.append(ToolCall(c.id, c.function.name, args))
    return Reply(msg.content, calls,
                 finish_reason=getattr(resp.choices[0], "finish_reason", None))


def utf8_console():
    """Windows consoles are cp949; an em dash in a model reply kills the print."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
