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
import math
import operator
from pathlib import Path

from openai import OpenAI

# ---- tool 1: calculator (safe, no eval) ----
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg}
_READABLE_FILES = {"notes.txt"}
_MAX_EXPRESSION_LENGTH = 100
_MAX_AST_NODES = 50
_MAX_ABS_VALUE = 10 ** 12
_MAX_EXPONENT = 10


def _checked_number(value):
    if (type(value) not in (int, float)
            or abs(value) > _MAX_ABS_VALUE
            or not math.isfinite(value)):
        raise ValueError("number outside allowed range")
    return value


def _ev(node):
    if isinstance(node, ast.Constant):
        return _checked_number(node.value)
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _OPS:
            raise ValueError("expression not allowed")
        left = _ev(node.left)
        right = _ev(node.right)
        if op_type is ast.Pow and abs(right) > _MAX_EXPONENT:
            raise ValueError("exponent outside allowed range")
        return _checked_number(_OPS[op_type](left, right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _OPS:
            raise ValueError("expression not allowed")
        return _checked_number(_OPS[op_type](_ev(node.operand)))
    raise ValueError("expression not allowed")


def calculator(expression: str) -> str:
    """Evaluate a bounded arithmetic expression, e.g. '3 * (4 + 5)'."""
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ValueError("expression too long")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        raise ValueError("expression too complex")
    return str(_ev(tree.body))


# ---- tool 2: read_file (blocked outside the working directory) ----
def read_file(path: str) -> str:
    """Return the contents of an approved task input file."""
    working_directory = Path.cwd().resolve()
    full = (working_directory / path).resolve()
    if (not full.is_relative_to(working_directory)
            or full.parent != working_directory
            or full.name not in _READABLE_FILES):
        return "denied: path outside the working directory"
    with full.open(encoding="utf-8") as f:
        return f.read()[:4000]


# ---- tool 3: text_stats (counts supplied text without file access) ----
def text_stats(text: str) -> str:
    """Return word, line, and character counts for supplied text."""
    return json.dumps({
        "words": len(text.split()),
        "lines": len(text.splitlines()),
        "characters": len(text),
    })


TOOLS_IMPL = {
    "calculator": calculator,
    "read_file": read_file,
    "text_stats": text_stats,
}

# ---- tool schemas handed to the model (the description IS the interface) ----
TOOLS = [
    {"type": "function",
     "function": {
         "name": "calculator",
         "description": "Evaluate a basic arithmetic expression within safe limits.",
         "parameters": {"type": "object",
                        "properties": {"expression": {"type": "string"}},
                        "required": ["expression"]}}},
    {"type": "function",
     "function": {
         "name": "read_file",
         "description": "Read notes.txt in the working directory.",
         "parameters": {"type": "object",
                        "properties": {"path": {"type": "string",
                                                "enum": ["notes.txt"]}},
                        "required": ["path"]}}},
    {"type": "function",
     "function": {
         "name": "text_stats",
         "description": "Count words, lines, and characters in supplied text. "
                        "Pass file contents, not a path; call read_file first.",
         "parameters": {"type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"]}}},
]

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")


def run(goal: str, max_steps: int = 8):
    client = OpenAI()  # uses OPENAI_API_KEY and OPENAI_BASE_URL
    messages = [{"role": "user", "content": goal}]

    for step in range(max_steps):   # <- this loop is what makes it an agent
        resp = client.chat.completions.create(
            model=MODEL, tools=TOOLS, messages=messages)
        if step == 0:
            print(f"  [model] {resp.model}")
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
        "Read notes.txt. Use text_stats on the full contents, then use " \
        "calculator for the requested expense arithmetic. Report both results."
    print(run(goal))
