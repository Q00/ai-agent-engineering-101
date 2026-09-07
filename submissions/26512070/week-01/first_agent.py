"""Week 01 starter — OpenAI-compatible API version (works with OpenRouter).

Two tools: calculator, read_file. Your assignment: add a third.
Requires: pip install openai, and in the environment:
  OPENAI_API_KEY   your key (an OpenRouter key works)
  OPENAI_BASE_URL  optional; set to https://openrouter.ai/api/v1 for OpenRouter
  AGENT_MODEL      optional; defaults to gpt-4o-mini. For OpenRouter free
                   models use e.g. AGENT_MODEL=meta-llama/llama-3.3-70b-instruct:free
"""
import os
import sys
import ast
import json
import operator
from datetime import datetime

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


# ---- tool 3: seconds_recorder (write the current second and the one 5s later) ----
def _inside_cwd(full: str) -> bool:
    try:
        return os.path.commonpath([full, os.getcwd()]) == os.getcwd()
    except ValueError:          # different drive on Windows
        return False


def seconds_recorder(path: str) -> str:
    """Write the current second and the second five seconds later to a text file."""
    full = os.path.abspath(path)
    if not _inside_cwd(full):
        return "denied: path outside the working directory"
    now = datetime.now().second     # already a whole number in 0..59
    after = (now + 5) % 60          # wraps past 59: 57 -> 2
    try:
        with open(full, "w", encoding="utf-8") as f:
            f.write(f"time: {now}\ntime_after_five: {after}\n")
    except OSError as e:
        return f"failed to write: {e}"
    return f"wrote 2 lines to {os.path.basename(full)}"


TOOLS_IMPL = {"calculator": calculator, "read_file": read_file,
              "seconds_recorder": seconds_recorder}

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
         "name": "seconds_recorder",
         "description": "Write the current clock second and the clock second five "
                        "seconds later into a text file, as the two lines "
                        "'time: <n>' and 'time_after_five: <n>'. Both are final "
                        "values: whole numbers from 0 to 59, already adjusted to "
                        "stay in that range. Use them as they are; no further "
                        "arithmetic is needed to interpret them. Returns a "
                        "confirmation only, not the recorded numbers.",
         "parameters": {"type": "object",
                        "properties": {"path": {
                            "type": "string",
                            "description": "Destination file inside the working "
                                           "directory, e.g. 'seconds.txt'."}},
                        "required": ["path"]}}},
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
            args = json.loads(call.function.arguments)
            out = TOOLS_IMPL[call.function.name](**args)
            print(f"  [tool] {call.function.name}({args}) -> {out}")
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": str(out)})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else (
        "Use seconds_recorder to record the current second and the second five "
        "seconds later into seconds.txt. Then use read_file to read the two "
        "values back. Then use calculator to add the two values, and report "
        "the sum.")
    print(run(goal))
