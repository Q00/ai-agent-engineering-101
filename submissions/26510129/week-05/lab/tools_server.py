"""Week 05 lab: the week-01 tools (calculator, read_file) as an MCP server.

Run:  python tools_server.py          -> stdio
      python tools_server.py --http   -> Streamable HTTP at http://127.0.0.1:8000/mcp
                                         (PORT=8010 python tools_server.py --http to change the port)
"""
import ast
import operator
import os
import sys

from mcp.server.mcpserver import MCPServer   # v1: from mcp.server.fastmcp import FastMCP

mcp = MCPServer("week01-tools")

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


@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file inside the server's working directory. Returns at most 4000 characters."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


@mcp.tool()
def write_note(path: str, text: str) -> str:
    """Write a note to a text file in the working directory."""   # description as in my week-01 TOOLS.md
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return "written"


if __name__ == "__main__":
    if "--http" in sys.argv:   # PORT overrides 8000 when something else already holds it
        mcp.run("streamable-http", port=int(os.environ.get("PORT", "8000")))
    else:
        mcp.run()              # default: stdio
