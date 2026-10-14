"""Week 05 lab — the week-01 tools as an MCP server.

Run:
  python tools_server.py          # stdio (a host spawns it as a child process)
  python tools_server.py --http   # Streamable HTTP at http://127.0.0.1:8000/mcp

The docstring of each tool becomes its description, and the type hints
become its inputSchema. Nothing else about the tools is written by hand.
"""
import os
import sys
import ast
import operator
from typing import Annotated

from pydantic import Field
from mcp.server.mcpserver import MCPServer   # v1: from mcp.server.fastmcp import FastMCP

mcp = MCPServer("week01-tools")

# ---- calculator (safe, no eval), unchanged from week 01 ----
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
    """Evaluate an arithmetic expression such as '3 * (4 + 5)'. Numbers and + - * / ** only."""
    return str(_ev(ast.parse(expression, mode="eval").body))


# ---- read_file (blocked outside the working directory), unchanged from week 01 ----
@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file inside the server's working directory. Returns at most 4000 characters."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# ---- write_note (appends; guarded like read_file), moved from week 01 ----
# The week-01 description and per-parameter descriptions are kept word for
# word: TOOLS.md in week-01 records why each sentence is there.
@mcp.tool()
def write_note(
    path: Annotated[str, Field(description="Path to the file to add a line to, "
                                           "inside the working directory.")],
    text: Annotated[str, Field(description="The one new line to add. Must not "
                                           "include content the file already "
                                           "contains.")],
) -> str:
    """Appends a single line to the end of a text file in the working directory. Everything already in the file is preserved: the file is never truncated and existing lines are never replaced. Pass only the new line in 'text' — do not resend content that is already in the file."""
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


if __name__ == "__main__":
    mcp.run("streamable-http") if "--http" in sys.argv else mcp.run()   # default: stdio
