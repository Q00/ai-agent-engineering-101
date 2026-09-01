"""Week 01 submission — first agent with THREE tools (OpenAI-compatible API).

Tools: calculator, read_file, clock. The third tool (clock) is the assignment.

Reproducibility (everything but the API key):
  SDK    : pip install openai   (tested with openai 1.109.1, Python 3.13)
  Model  : nvidia/nemotron-3.5-lightning:free  via OpenRouter (set via AGENT_MODEL).
           Any tool-capable model works — just swap the slug. Model choice does
           not change the agent's code. Honest note on why this slug and not the
           one I first picked: the originally-chosen z-ai/glm-5.2:free, plus
           google/gemma-4-31b-it:free, google/gemma-4-26b-a4b-it:free and
           poolside/laguna-xs-2.1:free, were all returning upstream HTTP 429
           (free shared-pool rate limit) at submission time (see logs/run-01,
           run-02); openai/gpt-oss-20b:free had been delisted from the free tier
           (404). nemotron-3.5-lightning was the free tool-capable model that
           actually ran (logs/run-03).
  Env    :
    export OPENAI_API_KEY="$OPENROUTER_API_KEY"          # your OpenRouter key
    export OPENAI_BASE_URL=https://openrouter.ai/api/v1
    export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
  Run    :
    python first_agent.py "Read notes.txt, sum the numbers in it, and tell me the total along with the current time."
    # capture a log with:  python first_agent.py 2>&1 | tee logs/run-03.txt
"""
import os
import sys
import ast
import json
import operator
from datetime import datetime, timezone

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


# ---- tool 3: clock (current time; takes no arguments) ----
def clock() -> str:
    """Return the current local date and time as an ISO-8601 string."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
    # tool 3 — the description below is YOUR interface to defend in TOOLS.md.
    # Tweak the wording and watch how the model's decision to call clock shifts.
    {"type": "function",
     "function": {
         "name": "clock",
         "description": "Return the current date and time. Use this only when the "
                        "task needs to know what time or date it is right now. It "
                        "takes no arguments and does no arithmetic or file access.",
         "parameters": {"type": "object", "properties": {}, "required": []}}},
]

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")


def run(goal: str, max_steps: int = 8):
    client = OpenAI()  # uses OPENAI_API_KEY and OPENAI_BASE_URL
    messages = [{"role": "user", "content": goal}]

    for step in range(max_steps):   # <- this loop is what makes it an agent
        resp = client.chat.completions.create(
            model=MODEL, tools=TOOLS, messages=messages)
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:               # final answer -> stop
            return msg.content or ""

        for call in msg.tool_calls:          # execute tool calls -> observe
            # clock takes no args; some models send "" instead of "{}" -> guard it
            args = json.loads(call.function.arguments or "{}")
            out = TOOLS_IMPL[call.function.name](**args)
            print(f"  [tool] {call.function.name}({args}) -> {out}")
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": str(out)})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read notes.txt and sum the numbers in it."
    print(run(goal))
