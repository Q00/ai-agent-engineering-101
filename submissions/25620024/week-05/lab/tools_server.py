"""Week 05 lab, step 2: the week-01 tools moved into an MCP server.

calculator, read_file, and (step 7) write_note are copied from
week-01/first_agent.py unchanged.
The hand-written TOOLS schema list is gone: @mcp.tool() builds each tool's
description from the docstring and its inputSchema from the type hints.

Run:
  python tools_server.py          # stdio: waits for a host to launch it
  python tools_server.py --http   # Streamable HTTP on http://127.0.0.1:8000/mcp
"""
import os
import sys
import ast
import operator

from mcp.server import MCPServer   # v2 name; v1 was mcp.server.fastmcp.FastMCP

mcp = MCPServer("week01-tools")

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


@mcp.tool()
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression string, e.g. '3 * (4 + 5)'."""
    return str(_ev(ast.parse(expression, mode="eval").body))


# ---- tool 2: read_file (blocked outside the working directory) ----
@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file in the working directory and return its contents."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    if not os.path.isfile(full):
        # Hand the failure back as an observation. Raising here would kill the
        # loop before the model ever gets to see that the file is missing.
        return f"denied: no such file - {path}"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# ---- tool 3: write_note (step 7; same sandbox as read_file; append only) ----
@mcp.tool()
def write_note(path: str, text: str) -> str:
    """Append one line of text to a note file in the working directory.
    Appends only - it never overwrites or deletes existing content, and it
    creates the file if it does not exist. Use it when the user asks for a
    result to be recorded or saved; do not use it to think out loud.
    Returns the file's new line count."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, "a", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")
    with open(full, encoding="utf-8") as f:
        lines = f.read().splitlines()
    return f"appended to {os.path.relpath(full)}; the file now has {len(lines)} line(s)"


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run("streamable-http", host="127.0.0.1", port=8000)
    else:
        mcp.run()   # stdio
