"""Week 01 - first agent with three tools: calculator, read_file, clock.

Based on the course starter `first_agent_openai.py` (OpenAI-compatible API,
so it also speaks to OpenRouter).

REPRODUCE
---------
    pip install openai tzdata

Windows cmd (no quotes, no spaces around '='):
    set OPENROUTER_API_KEY=<your openrouter key>    # never committed
    python first_agent.py > logs\run-01.txt 2>&1

bash:
    export OPENROUTER_API_KEY=<your openrouter key>
    python first_agent.py 2>&1 | tee logs/run-01.txt

Defaults if the environment variables are unset:
    AGENT_MODEL      minimax/minimax-m3:free
    OPENAI_BASE_URL  https://openrouter.ai/api/v1
    goal (argv[1])   see DEFAULT_GOAL below
The key is read from OPENROUTER_API_KEY, falling back to OPENAI_API_KEY.
The loop runs at most `max_steps` model turns (default 8).
"""
import ast
import json
import operator
import os
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from openai import OpenAI, RateLimitError

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
         "description": (
             "Look up a real calendar date. With no arguments it returns the "
             "current date and time from the system clock. With date_str it "
             "returns the same fields for that past or future date instead of "
             "reading the clock. "
             "Returns JSON with datetime, date, timezone, weekday and "
             "day_number. day_number is an absolute integer day count: to get "
             "the number of days between two dates, call clock twice - once "
             "per date - and subtract the two day_numbers with the calculator "
             "tool. Never subtract the day-of-month numbers instead; that is "
             "only correct when both dates fall in the same month. "
             "This tool does no arithmetic itself."),
         "parameters": {"type": "object",
                        "properties": {
                            "timezone": {
                                "type": "string",
                                "description": ("IANA timezone name, e.g. "
                                                "'Asia/Seoul' (the default) "
                                                "or 'UTC'.")},
                            "date_str": {
                                "type": "string",
                                "description": ("An ISO date, 'YYYY-MM-DD'. "
                                                "Omit it to read the current "
                                                "clock.")}},
                        "required": []}}},
]

# ---- provider configuration (everything except the key itself) ----
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.environ.get("AGENT_MODEL", "minimax/minimax-m3:free")
API_KEY = (os.environ.get("OPENROUTER_API_KEY")
           or os.environ.get("OPENAI_API_KEY"))

DEFAULT_GOAL = ("Read notes.txt, work out how much is still unpaid, and tell "
                "me how many days have passed between the memo date and today.")


# Free-tier endpoints share an upstream pool, so 429 is routine rather than
# exceptional. Waits grow so a busy pool is not hammered.
RETRY_WAITS = (5, 15, 45)


def _chat(client, messages):
    """One model turn, retrying the free tier's shared-pool 429s."""
    for attempt, wait in enumerate((*RETRY_WAITS, None), start=1):
        try:
            return client.chat.completions.create(
                model=MODEL, tools=TOOLS, messages=messages)
        except RateLimitError:
            if wait is None:
                raise
            print(f"  [429] upstream free pool is saturated; waiting {wait}s "
                  f"(retry {attempt}/{len(RETRY_WAITS)})", flush=True)
            time.sleep(wait)


def run(goal: str, max_steps: int = 8):
    if not API_KEY:
        sys.exit("set OPENROUTER_API_KEY (or OPENAI_API_KEY) first; see the "
                 "REPRODUCE block at the top of this file")
    # max_retries=0: this file does its own visible backoff instead, so the
    # log records every 429 rather than hiding retries inside the SDK.
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL, max_retries=0)
    messages = [{"role": "user", "content": goal}]

    for step in range(max_steps):   # <- this loop is what makes it an agent
        resp = _chat(client, messages)
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
    # The model writes real Unicode (e.g. U+2212 MINUS SIGN). A Windows
    # console defaults to cp949 here and raised UnicodeEncodeError on the
    # final answer, losing it from the log, so force utf-8 on both streams.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    goal = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GOAL
    print(f"model: {MODEL}")
    print(f"base:  {BASE_URL}")
    print(f"goal:  {goal}\n")
    print(run(goal))
