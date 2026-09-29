"""Week 05 — MCP server: the three week-01 tools, moved out of the agent.

Tools: calculator, read_file, clock. Same behavior as week-01/first_agent.py;
the only change is where they live. The description and JSON schema are no
longer hand-written dicts: FastMCP builds them from the decorator, the
docstring and the type hints.

Transport: stdio. The host starts this file as a child process.
Settings (env): CLOCK_DESC = "full" (default) | "terse", same switch as week-01.
"""
import os
import ast
import operator
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import Field
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("week01-tools")

# ---- tool 1: calculator (safe, no eval) ----
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


@mcp.tool(description="Evaluate an arithmetic expression.")
def calculator(expression: str) -> str:
    return str(_ev(ast.parse(expression, mode="eval").body))


# ---- tool 2: read_file (blocked outside the working directory) ----
@mcp.tool(description="Read a text file in the working directory.")
def read_file(path: str) -> str:
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# ---- tool 3: clock ----
CLOCK_DESCRIPTIONS = {
    "terse": "Get the current time.",
    "full": (
        "Return the current date and time (YYYY-MM-DD HH:MM:SS, timezone, "
        "weekday). Call this whenever the task depends on today's date, the "
        "current time, or how much time remains until/since some date. "
        "Do not guess the date from memory; you do not know today's date "
        "without this tool. Default timezone is Asia/Seoul."
    ),
}


@mcp.tool(description=CLOCK_DESCRIPTIONS[os.environ.get("CLOCK_DESC", "full")])
def clock(timezone: Annotated[str, Field(
        description="IANA timezone, e.g. Asia/Seoul, UTC. "
                    "Optional; defaults to Asia/Seoul.")] = "Asia/Seoul") -> str:
    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:
        return (f"error: unknown timezone '{timezone}'. "
                "Use an IANA name such as Asia/Seoul or UTC.")
    return now.strftime("%Y-%m-%d %H:%M:%S %Z (%A)")


if __name__ == "__main__":
    mcp.run(transport="stdio")
