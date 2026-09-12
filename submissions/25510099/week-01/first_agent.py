"""Week 01 — first agent with three tools: calculator, read_file, clock.

Runs on an OpenAI-compatible API (used here with OpenRouter).
Requires: pip install openai, and in the environment:
  OPENAI_API_KEY   your key (an OpenRouter key works)
  OPENAI_BASE_URL  optional; set to https://openrouter.ai/api/v1 for OpenRouter
  AGENT_MODEL      optional; defaults to nvidia/nemotron-3-super-120b-a12b:free
                   (an OpenRouter free model with tool-calling support)
"""
import os
import sys
import ast
import json
import operator
from datetime import datetime
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


# ---- tool 3: clock (no arguments; the model must not guess the time) ----
def clock() -> str:
    """Return the current date and time in Asia/Seoul, ISO 8601."""
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


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
         "description": "Get the current date and time (Asia/Seoul, ISO 8601). "
                        "Always call this when the current date or time is needed; "
                        "never guess it. Takes no arguments.",
         "parameters": {"type": "object",
                        "properties": {},
                        "required": []}}},
]

MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")


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
        "notes.txt를 읽고, 오늘 날짜 기준으로 (1) 지난 회의가 며칠 전이었는지 "
        "(2) 베타 출시까지 며칠 남았는지 (3) 외주 비용 차감 후 남는 예산이 "
        "얼마인지 알려줘")
    print(run(goal))
