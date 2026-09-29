"""Week 05 lab: expose the Week 01 tools through MCP."""

import ast
import json
import operator
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


def _ev(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
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
    """Read a text file inside the server working directory, up to 4000 characters."""
    root = Path.cwd().resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        return "denied: path outside the working directory"
    return target.read_text(encoding="utf-8")[:4000]


@mcp.tool()
def clock(timezone: str = "Asia/Seoul", date_str: str = "") -> str:
    """Return current time, or calendar fields and day number for an ISO date."""
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        return f"error: unknown IANA timezone {timezone!r}"

    if date_str:
        try:
            requested = date.fromisoformat(date_str.strip())
        except ValueError:
            return f"error: {date_str!r} is not an ISO date (YYYY-MM-DD)"
        return json.dumps(
            {
                "date": requested.isoformat(),
                "weekday": requested.strftime("%A"),
                "day_number": requested.toordinal(),
            },
            ensure_ascii=False,
        )

    now = datetime.now(tz)
    return json.dumps(
        {
            "datetime": now.isoformat(timespec="seconds"),
            "date": now.date().isoformat(),
            "timezone": timezone,
            "weekday": now.strftime("%A"),
            "day_number": now.date().toordinal(),
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    transport = "streamable-http" if "--http" in sys.argv else "stdio"
    mcp.run(transport)
