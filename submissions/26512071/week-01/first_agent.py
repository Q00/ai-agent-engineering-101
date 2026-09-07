"""Week 01 - first agent with three tools: calculator, read_file, clock.

Based on the course starter `first_agent_openai.py` (OpenAI-compatible API,
so it also speaks to OpenRouter).

REPRODUCE
---------
    pip install openai tzdata
    export OPENAI_BASE_URL=https://openrouter.ai/api/v1
    export OPENAI_API_KEY=<your openrouter key>     # not committed
    export AGENT_MODEL=meta-llama/llama-3.3-70b-instruct:free
    python first_agent.py 2>&1 | tee logs/run-01.txt

Defaults if the environment variables are unset:
    AGENT_MODEL      gpt-4o-mini
    OPENAI_BASE_URL  the OpenAI default (https://api.openai.com/v1)
    goal (argv[1])   see DEFAULT_GOAL below
The loop runs at most `max_steps` model turns (default 8).
"""
import ast
import json
import operator
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from openai import OpenAI

# ---- tool 1: calculator (safe, no eval) ----
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg}


def _ev(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        return _OPS[type(node.op)](_ev(node.left), _ev(node.right))
    if isinstance(node, ast.UnaryOp):
        return _OPS[type(node.op)](_ev(node.operand))
    raise ValueError("expression not allowed")


def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression string, e.g. '3 * (4 + 5)'."""
    return str(_ev(ast.parse(expression, mode="eval").body))


# ---- tool 2: read_file (blocked outside the working directory) ----
def read_file(path: str) -> str:
    """Return the contents of a text file."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# ---- tool 3: clock (the tool I added) ----
DEFAULT_TZ = "Asia/Seoul"


def clock(timezone: str = DEFAULT_TZ, date_str: str = "") -> str:
    """Current wall-clock time, or the day_number of a given ISO date.

    Returns JSON so the fields are unambiguous to the model. `day_number` is
    the proleptic Gregorian ordinal: subtracting two day_numbers with the
    `calculator` tool yields the number of days between the two dates.
    """
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        return (f"error: no timezone data for {timezone!r}. Use an IANA name "
                "such as 'Asia/Seoul' or 'UTC'. (On Windows this also happens "
                "when the tzdata package is missing: pip install tzdata)")

    if date_str:
        try:
            d = date.fromisoformat(date_str.strip())
        except ValueError:
            return (f"error: {date_str!r} is not an ISO date. "
                    "Use YYYY-MM-DD, e.g. '2026-09-01'.")
        return json.dumps({"date": d.isoformat(),
                           "weekday": d.strftime("%A"),
                           "day_number": d.toordinal()}, ensure_ascii=False)

    now = datetime.now(tz)
    return json.dumps({"datetime": now.isoformat(timespec="seconds"),
                       "date": now.date().isoformat(),
                       "timezone": timezone,
                       "weekday": now.strftime("%A"),
                       "day_number": now.date().toordinal()}, ensure_ascii=False)


TOOLS_IMPL = {"calculator": calculator, "read_file": read_file, "clock": clock}

# ---- tool schemas handed to the model (the description IS the interface) ----
TOOLS = [
    {"type": "function",
     "function": {
         "name": "calculator",
         "description": "Evaluate an arithmetic expression.",
         "parameters": {"type": "object",
                        "properties": {"expression": {"type": "string"}},
                        "required": ["expression"]}}},
    {"type": "function",
     "function": {
         "name": "read_file",
         "description": "Read a text file in the working directory.",
         "parameters": {"type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"]}}},
    {"type": "function",
     "function": {
         "name": "clock",
         "description": "Return the current date and time.",
         "parameters": {"type": "object",
                        "properties": {
                            "timezone": {"type": "string"},
                            "date_str": {"type": "string"}},
                        "required": []}}},
]

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")

DEFAULT_GOAL = ("Read notes.txt, work out how much is still unpaid, and tell "
                "me how many days have passed between the memo date and today.")


def run(goal: str, max_steps: int = 8):
    client = OpenAI()  # uses OPENAI_API_KEY and OPENAI_BASE_URL
    messages = [{"role": "user", "content": goal}]

    for step in range(max_steps):   # <- this loop is what makes it an agent
        resp = client.chat.completions.create(
            model=MODEL, tools=TOOLS, messages=messages)
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:               # final answer -> stop
            print(f"[step {step}] final answer")
            return msg.content or ""

        for call in msg.tool_calls:          # execute tool calls -> observe
            name = call.function.name
            raw = call.function.arguments or "{}"
            try:
                args = json.loads(raw)
                if name not in TOOLS_IMPL:
                    out = f"error: no tool named {name!r}"
                else:
                    out = TOOLS_IMPL[name](**args)
            except Exception as e:
                # Hand the failure back to the model instead of crashing:
                # a bad tool call is a recoverable observation, not a bug.
                out = f"error: {type(e).__name__}: {e}"
            print(f"[step {step}] [tool] {name}({raw}) -> {out}")
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": str(out)})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GOAL
    print(f"model: {MODEL}")
    print(f"goal:  {goal}\n")
    print(run(goal))
