"""Week 01's three tools exposed through MCP over stdio or Streamable HTTP."""

import argparse
import ast
import operator
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

ROOT = Path(__file__).resolve().parent
mcp = MCPServer("week01-tools")
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}


def _ev(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        left, right = _ev(node.left), _ev(node.right)
        if type(node.op) is ast.Pow and (abs(left) > 1_000_000 or abs(right) > 8):
            raise ToolError("Power expression too large")
        return OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_ev(node.operand))
    raise ToolError("Expression not allowed")


def _inside(path: str) -> Path:
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT) or target == ROOT:
        raise ToolError("Path outside the server working directory")
    return target


@mcp.tool()
def calculator(expression: str) -> str:
    """Evaluate arithmetic with numbers and + - * / ** only."""
    try:
        return str(_ev(ast.parse(expression, mode="eval").body))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ToolError(f"Invalid arithmetic expression: {exc}") from exc


@mcp.tool()
def read_file(path: str) -> str:
    """Read up to 4000 characters from a text file inside the server directory."""
    target = _inside(path)
    try:
        return target.read_text(encoding="utf-8")[:4000]
    except OSError as exc:
        raise ToolError(f"Cannot read {path}: {exc.strerror}") from exc


@mcp.tool()
def write_note(path: str, content: str) -> str:
    """Write a text note inside the server directory."""
    target = _inside(path)
    if not target.parent.exists():
        raise ToolError("Parent directory does not exist")
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"Cannot write {path}: {exc.strerror}") from exc
    return f"wrote {len(content)} chars to {path}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--port", type=int, default=18050)
    args = parser.parse_args()
    if args.http:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port,
                json_response=True, stateless_http=True)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
