"""Week 01 — the lab agent with a third tool: list_files.

Three tools: calculator, read_file, list_files.
OpenAI-compatible API version, pointed at OpenRouter.

Requires: pip install openai, and in the environment:
  OPENAI_API_KEY   your OpenRouter key
  OPENAI_BASE_URL  https://openrouter.ai/api/v1
  AGENT_MODEL      optional; defaults to minimax/minimax-m3:free

Sampling is pinned in code: temperature=0.0. See TOOLS.md.

Run:
    python first_agent.py
    python first_agent.py "your own goal here"
"""
import os
import sys
import ast
import json
import operator

from openai import OpenAI

MODEL = os.environ.get("AGENT_MODEL", "minimax/minimax-m3:free")
MAX_STEPS = 8
# Pinned, not left to the client default: run-01 and run-02 were the same code
# and the same goal but took different tool paths. A sampling parameter left
# implicit cannot be reproduced by someone reading this file.
TEMPERATURE = 0.0

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


# ---- shared sandbox check, used by read_file and list_files ----
def _safe_path(path: str) -> str | None:
    """Resolve path under the working directory; None if it escapes.

    The starter used abspath().startswith(getcwd()), which follows no symlinks
    and lets a sibling through on a prefix collision (cwd=C:\\work accepts
    C:\\workspace). realpath + os.sep fixes both.
    """
    root = os.path.realpath(os.getcwd())
    full = os.path.realpath(os.path.join(root, path))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full


# ---- tool 2: read_file (blocked outside the working directory) ----
def read_file(path: str) -> str:
    """Return the contents of a text file."""
    full = _safe_path(path)
    if full is None:
        return "denied: path outside the working directory"
    if not os.path.isfile(full):
        return f"error: no such file ({path})"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# ---- tool 3 (new): list_files ----
def list_files(path: str = ".") -> str:
    """List files and directories in the working directory."""
    full = _safe_path(path)
    if full is None:
        return "denied: path outside the working directory"
    if not os.path.isdir(full):
        return f"error: not a directory ({path})"

    lines = []
    for name in sorted(os.listdir(full)):
        if name.startswith(".") or name == "__pycache__":
            continue
        child = os.path.join(full, name)
        if os.path.isdir(child):
            lines.append(f"{name}/  (directory)")
        else:
            lines.append(f"{name}  ({os.path.getsize(child)} bytes)")
    if not lines:
        return f"{path}: (empty directory)"
    return "\n".join(lines[:200])


TOOLS_IMPL = {
    "calculator": calculator,
    "read_file": read_file,
    "list_files": list_files,
}

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
    # v1: described in the same terse one-liner style as the two starter tools.
    # Whether that is enough is an open question -- see logs/run-01.txt.
    {"type": "function",
     "function": {
         "name": "list_files",
         "description": "List files in the working directory.",
         "parameters": {"type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": []}}},
]


def run(goal: str, max_steps: int = MAX_STEPS):
    client = OpenAI()  # uses OPENAI_API_KEY and OPENAI_BASE_URL
    messages = [{"role": "user", "content": goal}]
    print(f"model={MODEL}  temperature={TEMPERATURE}  max_steps={max_steps}")
    print(f"goal: {goal}\n")

    for step in range(max_steps):   # <- this loop is what makes it an agent
        print(f"[step {step + 1}/{max_steps}]")
        resp = client.chat.completions.create(
            model=MODEL, tools=TOOLS, messages=messages,
            temperature=TEMPERATURE)
        msg = resp.choices[0].message
        messages.append(msg)

        if msg.content and msg.content.strip():
            print(f"  [text] {msg.content.strip()}")

        if not msg.tool_calls:               # final answer -> stop
            return msg.content or ""

        for call in msg.tool_calls:          # execute tool calls -> observe
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                out = f"tool error: arguments were not valid JSON: {exc}"
                print(f"  [tool] {name}(<unparseable>) -> {out}")
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": out})
                continue

            if name not in TOOLS_IMPL:       # free models do invent tool names
                out = (f"tool error: no such tool '{name}'. "
                       f"available: {', '.join(TOOLS_IMPL)}")
            else:
                try:
                    out = TOOLS_IMPL[name](**args)
                except Exception as exc:     # a tool failure is an observation, not a crash
                    out = f"tool error: {type(exc).__name__}: {exc}"
            print(f"  [tool] {name}({args}) -> {out}")
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": str(out)})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    # The default goal deliberately does NOT name the file, so the agent has to
    # discover it with list_files before it can read it.
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Find the memo file in this folder and sum the numbers written in it."
    print(run(goal))
