"""Week 01 submission — three-tool agent over OpenRouter (OpenAI-compatible API).

Tools: calculator, read_file, write_note.
Requires: pip install openai, and OPENROUTER_API_KEY in the environment.
Model: z-ai/glm-5.2:free (OpenRouter free tier).
"""
import os
import sys
import ast
import json
import time
import random
import operator

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


# ---- tool 3: write_note (write/overwrite a text file in the working dir) ----
def write_note(path: str, content: str) -> str:
    """Write content to a text file in the working directory."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return f"wrote {len(content)} chars to {path}"


TOOLS_IMPL = {"calculator": calculator,
              "read_file": read_file,
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
         "description": "Write text content to a file in the working directory. Creates or overwrites the file.",
         "parameters": {"type": "object",
                        "properties": {"path": {"type": "string"},
                                       "content": {"type": "string"}},
                        "required": ["path", "content"]}}},
]

MODEL = os.environ.get("AGENT_MODEL", "z-ai/glm-5.2:free")
BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")


def _create_with_retry(client, **kwargs):
    """Call the OpenAI API, retrying on 429 rate-limit with backoff.

    Free-tier OpenRouter models are frequently rate-limited on the shared
    upstream pool; this lets the agent survive transient 429s.
    """
    for attempt in range(5):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            retry_after = getattr(e, "retry_after", None)
            wait = float(retry_after) if retry_after else 2 ** attempt
            print(f"  [retry] 429 rate-limit, waiting {wait:.0f}s "
                  f"(attempt {attempt + 1}/5)")
            time.sleep(wait)
    return client.chat.completions.create(**kwargs)


def run(goal: str, max_steps: int = 8):
    client = OpenAI(
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        base_url=BASE_URL,
    )
    messages = [{"role": "user", "content": goal}]

    for step in range(max_steps):   # <- this loop is what makes it an agent
        resp = _create_with_retry(
            client, model=MODEL, tools=TOOLS, messages=messages)
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
    # Auto-generate notes.txt with random numbers for the agent to sum.
    numbers = [random.randint(1, 100) for _ in range(5)]
    with open("notes.txt", "w", encoding="utf-8") as f:
        f.write("Meeting memo, auto-generated\n\n")
        f.write("Numbers to sum:\n")
        for n in numbers:
            f.write(f"{n}\n")
    print(f"Generated notes.txt with numbers: {numbers}")
    print(f"Expected sum: {sum(numbers)}")

    goal = ("Read notes.txt, sum the numbers listed in it, "
            "and write the result to result.txt.")
    print(f"Goal: {goal}")
    result = run(goal)
    print(f"\nAgent final answer: {result}")
