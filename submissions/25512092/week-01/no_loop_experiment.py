"""Week 01 starter — the agent from the lab, Anthropic API version.

Two tools: calculator, read_file. Your assignment: add a third.
Requires: pip install anthropic, and ANTHROPIC_API_KEY in the environment.
"""
import os
import sys
import ast
import operator

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


TOOLS_IMPL = {"calculator": calculator, "read_file": read_file}

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
]


def run(goal: str, max_steps: int = 8):
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": goal}]

    # 루프 없음 - 모델을 딱 한 번만 부른다
    resp = client.messages.create(
        model="claude-sonnet-4-5", max_tokens=1024,
        tools=TOOLS, messages=messages)

    print(f"  [stop_reason] {resp.stop_reason}")
    for block in resp.content:
        print(f"  [block] {block.type}: {block}")

    return "".join(b.text for b in resp.content if b.type == "text")


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read notes.txt and sum the numbers in it."
    print(run(goal))
