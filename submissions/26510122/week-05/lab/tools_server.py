#!/usr/bin/env python3
"""Week 01 tools exposed through one reusable MCP server."""

from __future__ import annotations

import argparse
import ast
import operator
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


HERE = Path(__file__).resolve().parent
_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _evaluate(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        return _BINARY_OPS[type(node.op)](
            _evaluate(node.left), _evaluate(node.right)
        )
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_evaluate(node.operand))
    raise ValueError("expression not allowed")


def _workspace() -> Path:
    return Path(os.environ.get("MCP_WORKSPACE", HERE)).resolve()


def _inside_workspace(path: str) -> Path:
    candidate = (_workspace() / path).resolve()
    if not candidate.is_relative_to(_workspace()):
        raise ToolError("path outside the server working directory")
    return candidate


mcp = MCPServer("week01-tools")


@mcp.tool()
def calculator(expression: str) -> str:
    """Evaluate arithmetic such as '3 * (4 + 5)'. Numbers and + - * / ** only."""
    try:
        return str(_evaluate(ast.parse(expression, mode="eval").body))
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 text file inside the server working directory, up to 4000 characters."""
    target = _inside_workspace(path)
    if not target.is_file():
        raise ToolError(f"not a file: {path}")
    return target.read_text(encoding="utf-8")[:4000]


@mcp.tool()
def write_note(content: str) -> str:
    """Append a completed expense summary to expense_summary.txt without replacing prior notes."""
    target = _inside_workspace("expense_summary.txt")
    if target.is_symlink():
        raise ToolError("expense_summary.txt must not be a symbolic link")
    with target.open("a", encoding="utf-8") as stream:
        stream.write(content.rstrip() + "\n")
    return "Saved to expense_summary.txt"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.http:
        mcp.run(
            "streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
