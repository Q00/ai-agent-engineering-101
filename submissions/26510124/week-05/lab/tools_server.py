"""Week 01's two tools, served over MCP stdio or Streamable HTTP."""
import argparse
import ast
import operator
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

mcp = MCPServer("week01-tools")
ROOT = Path(__file__).resolve().parent
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg}


def _ev(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_ev(node.left), _ev(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_ev(node.operand))
    raise ValueError("expression not allowed")


@mcp.tool(annotations=READ_ONLY)
def calculator(expression: str) -> str:
    """Evaluate arithmetic such as '3 * (4 + 5)'. Numbers and + - * / ** only."""
    try:
        return str(_ev(ast.parse(expression, mode="eval").body))
    except (SyntaxError, ValueError, ArithmeticError) as exc:
        raise ToolError(f"invalid arithmetic expression: {exc}") from exc


@mcp.tool(annotations=READ_ONLY)
def read_file(path: str) -> str:
    """Read a UTF-8 text file inside the server's root directory (default: the lab folder). Return at most 4000 characters."""
    full = (ROOT / path).resolve()
    if not full.is_relative_to(ROOT):
        raise ToolError("denied: path outside the server root directory")
    try:
        with full.open(encoding="utf-8") as f:
            return f.read(4000)
    except (OSError, UnicodeError) as exc:
        raise ToolError("cannot read requested file as UTF-8 text") from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http", action="store_true", help="serve http://127.0.0.1:8000/mcp")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--root", type=Path, default=ROOT, help="directory readable by the tools")
    args = parser.parse_args()
    ROOT = args.root.resolve(strict=True)
    if not ROOT.is_dir():
        parser.error("--root must be a directory")
    if args.http:
        mcp.run("streamable-http", host="127.0.0.1", port=args.port)
    else:
        mcp.run()
