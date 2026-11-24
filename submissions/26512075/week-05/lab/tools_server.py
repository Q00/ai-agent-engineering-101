"""Week-1 tools moved onto an MCP server.

  python tools_server.py            # stdio (host spawns this as a child)
  python tools_server.py --http     # http://127.0.0.1:8000/mcp

Checkpoint: add a third @mcp.tool here only.
The next host run must print three tool names.
"""

from __future__ import annotations

import argparse
import ast
import operator
from pathlib import Path

try:
    from mcp.server import MCPServer as _Server
except ImportError:  # mcp<2
    from mcp.server.fastmcp import FastMCP as _Server

ROOT = Path(__file__).resolve().parent
mcp = _Server("week01-tools")

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    raise ValueError("only +, -, *, /, //, %, ** on numbers are allowed")


@mcp.tool()
def calculator(expression: str) -> str:
    """Evaluate a whole-number or decimal arithmetic expression and return the result."""
    tree = ast.parse(expression, mode="eval")
    value = _eval(tree)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


@mcp.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 text file from the lab working directory and return its contents."""
    target = (ROOT / path).resolve()
    if ROOT not in target.parents and target != ROOT:
        raise ValueError("path must stay inside the lab directory")
    return target.read_text(encoding="utf-8")


# Third tool lives only on the server. Host code must not name it.
@mcp.tool()
def count_words(text: str) -> str:
    """Count whitespace-separated words in the given text and return the integer count."""
    return str(len(text.split()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.http:
        # Streamable HTTP. Lab curl target: http://127.0.0.1:8000/mcp
        mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()