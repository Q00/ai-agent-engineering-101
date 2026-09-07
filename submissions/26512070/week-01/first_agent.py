"""Week 01 starter — the agent from the lab, Anthropic API version.

Two tools: calculator, read_file. Your assignment: add a third.
Requires: pip install anthropic, and ANTHROPIC_API_KEY in the environment.
"""
import os
import sys
import ast
import operator
from datetime import datetime

import anthropic

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
    {"name": "calculator",
     "description": "Evaluate an arithmetic expression.",
     "input_schema": {"type": "object",
                      "properties": {"expression": {"type": "string"}},
                      "required": ["expression"]}},
    {"name": "read_file",
     "description": "Read a text file in the working directory.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
    {"name": "seconds_recorder",
     "description": "Write the current clock second and the clock second five seconds "
                    "later into a text file, as the two lines 'time: <n>' and "
                    "'time_after_five: <n>'. Both are whole numbers in 0-59; the second "
                    "value wraps past 59 (57 becomes 2). Returns a confirmation only, "
                    "not the recorded numbers.",
     "input_schema": {"type": "object",
                      "properties": {"path": {
                          "type": "string",
                          "description": "Destination file inside the working "
                                         "directory, e.g. 'seconds.txt'."}},
                      "required": ["path"]}},
]


def run(goal: str, max_steps: int = 8):
    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY
    messages = [{"role": "user", "content": goal}]

    for step in range(max_steps):   # <- this loop is what makes it an agent
        resp = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=1024,
            tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":   # final answer -> stop
            return "".join(b.text for b in resp.content if b.type == "text")

        results = []
        for block in resp.content:           # execute tool calls -> observe
            if block.type == "tool_use":
                out = TOOLS_IMPL[block.name](**block.input)
                print(f"  [tool] {block.name}({block.input}) -> {out}")
                results.append({"type": "tool_result",
                                "tool_use_id": block.id, "content": str(out)})
        messages.append({"role": "user", "content": results})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read notes.txt and sum the numbers in it."
    print(run(goal))
