"""Week 05 LAB: expose the week-01 tools through an MCP server.

Run from the directory whose files the agent may read:
    python tools_server.py          # stdio
    python tools_server.py --http   # http://127.0.0.1:8000/mcp
"""

import ast
import operator
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer


mcp = MCPServer("week01-tools")

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _ev(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_ev(node.left), _ev(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_ev(node.operand))
    raise ValueError("expression not allowed")


@mcp.tool()
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression. Numbers and + - * / ** only."""
    return str(_ev(ast.parse(expression, mode="eval").body))


@mcp.tool()
def read_file(path: str) -> str:
    """Read up to 4000 characters of a UTF-8 file in the server's working directory."""
    root = Path.cwd().resolve()
    full = Path(path).resolve()
    if not full.is_relative_to(root):
        return "denied: path outside the working directory"
    return full.read_text(encoding="utf-8")[:4000]


if __name__ == "__main__":
    if "--http" in sys.argv[1:]:
        mcp.run(transport="streamable-http", stateless_http=True, json_response=True)
    else:
        mcp.run()
