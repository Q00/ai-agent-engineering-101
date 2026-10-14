"""Week 05 LAB: the week-01 tools (calculator, read_file) as an MCP server.

Run:
  python tools_server.py          stdio (the client starts this process itself)
  python tools_server.py --http   Streamable HTTP at http://127.0.0.1:8000/mcp
Requires: pip install "mcp>=2"
"""
import ast
import operator
import os
import sys
from datetime import datetime, timezone

from mcp.server.mcpserver import MCPServer   # v1: from mcp.server.fastmcp import FastMCP

mcp = MCPServer("week01-tools")

# ---- tool 1: calculator (safe, no eval) -- same _ev as week 01 ----
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


# ---- tool 2: read_file (blocked outside the working directory) -- same as week 01 ----
@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file inside the server's working directory. Returns at most 4000 characters."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# ---- tool 3: clock (no arguments) -- the week-01 assignment tool, moved here ----
@mcp.tool()
def clock() -> str:
    """Get the current date and time in UTC, as an ISO 8601 string. Takes no arguments. Use this whenever the user's request depends on 'now' (e.g. today's date, how long until/since something) rather than guessing or relying on training data."""
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    mcp.run("streamable-http") if "--http" in sys.argv else mcp.run()   # default: stdio
