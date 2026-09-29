"""Week 05 lab — the week-01 tools as an MCP server.

  python tools_server.py          # stdio (a host spawns this as a child process)
  python tools_server.py --http   # Streamable HTTP on http://127.0.0.1:8000/mcp

The docstring of each tool becomes its description, the type hints its inputSchema.
Bodies and descriptions are my week-01 ones unchanged (first_agent.py on the week-01
branch): the docstring here is the `description` the week-01 TOOLS list gave the model.
"""
import ast
import operator
import os
import sys

from mcp.server.mcpserver import MCPServer

# read_file resolves paths against this directory, not the caller's cwd,
# so stdio and HTTP runs see the same files.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

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
    """Evaluate an arithmetic expression."""
    return str(_ev(ast.parse(expression, mode="eval").body))


@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file in the working directory."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


if __name__ == "__main__":
    mcp.run("streamable-http") if "--http" in sys.argv else mcp.run()
