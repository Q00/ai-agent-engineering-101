"""Week 01 starter — OpenAI-compatible API version (works with OpenRouter).

Two tools: calculator, read_file. Your assignment: add a third.
Run from this directory with:
  uv run --with openai python first_agent.py

Configuration:
  OPENAI_API_KEY   required environment variable; use an OpenRouter key.
  OpenRouter base URL and minimax/minimax-m3:free are set in this file.
  Never commit an API key.
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

# ---- tool 3: write_note(blocked outside the working directory) ----
def write_note(path:str, content:str) -> str:
    """Write text content to a file in the working directory."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"

    with open(full, "w", encoding="utf-8") as f:
        f.write(content)

        return f"wrote note to {path}"

TOOLS_IMPL = {"calculator": calculator, "read_file": read_file, "write_note": write_note,}

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
             "Write text to a file within the working directory. "
             "Use this only when the user explicitly asks to save a result or note."
         ),
         "parameters":{
             "type":"object",
             "properties":{
                 "path": {
                     "type":"string",
                     "description":"Relative path of the text file to write."
                 },
                 "content":{
                     "type":"string",
                     "description":"Text content to write into the file."
                 }
             },
             "required":["path","content"],
             "additionalProperties":False
                    
            }
     }}
]

MODEL = "minimax/minimax-m3:free"


def run(goal: str, max_steps: int = 8):
    client = OpenAI(base_url="https://openrouter.ai/api/v1")  # uses OPENAI_API_KEY
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
