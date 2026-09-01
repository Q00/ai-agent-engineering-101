"""Week 01 — OpenAI-compatible API version, run against OpenRouter.

Three tools: calculator, read_file, write_note.
Requires: pip install openai, and in the environment:
  OPENAI_API_KEY   your key (an OpenRouter key works)
  OPENAI_BASE_URL  set to https://openrouter.ai/api/v1 for OpenRouter
  AGENT_MODEL      e.g. AGENT_MODEL=minimax/minimax-m2.7:free

Note: the model example in the original starter
(meta-llama/llama-3.3-70b-instruct:free) is no longer listed on OpenRouter.
Verified working free models with tool calling, 2026-09-01:
  minimax/minimax-m2.7:free
  cohere/north-mini-code:free
"""
import os
import sys
import ast
import json
import operator

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


# ---- tool 3: write_note (appends; guarded like read_file) ----
def write_note(path: str, text: str) -> str:
    """Append one line of text to a file. Existing content is kept."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, "a", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")
    with open(full, encoding="utf-8") as f:
        total = sum(1 for _ in f)
    # the return value is part of the interface too: it is the only channel
    # that can tell the model what actually happened to the file.
    return (f"appended 1 line; {os.path.basename(full)} now has {total} "
            f"lines and nothing was removed")


TOOLS_IMPL = {"calculator": calculator, "read_file": read_file,
              "write_note": write_note}

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
         "name": "write_note",
         "description": (
             "Appends a single line to the end of a text file in the working "
             "directory. Everything already in the file is preserved: the file "
             "is never truncated and existing lines are never replaced. Pass "
             "only the new line in 'text' \u2014 do not resend content that is "
             "already in the file."),
         "parameters": {
             "type": "object",
             "properties": {
                 "path": {"type": "string",
                          "description": "Path to the file to add a line to, "
                                         "inside the working directory."},
                 "text": {"type": "string",
                          "description": "The one new line to add. Must not "
                                         "include content the file already "
                                         "contains."}},
             "required": ["path", "text"]}}},
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
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read notes.txt and sum the numbers in it."
    print(run(goal))
