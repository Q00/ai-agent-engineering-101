"""Week-05 lab: the week-01 tools as an MCP server.

The tool code is week-01's first_agent.py unchanged. What moved is the
interface: the docstring is now the tool description and the type hints are
the inputSchema, both served through tools/list instead of being hardcoded in
the agent.

  uv run python tools_server.py          # stdio (a client starts it)
  uv run python tools_server.py --http   # Streamable HTTP, 127.0.0.1:8000/mcp
"""

import ast
import json
import operator
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

mcp = MCPServer("week01-tools")

# ---- tool 1: calculator (safe, no eval) ----
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


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


# ---- tool 2: read_file (blocked outside the working directory) ----
@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file inside the server's working directory. Returns at most 4000 characters."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# ---- tool 3: get_exchange_rate (fixed host, KRW only, errors as strings) ----
_FX_URL = "https://api.frankfurter.dev/v1/{date}?base={base}&symbols=KRW"


@mcp.tool()
def get_exchange_rate(
    base: Annotated[str, Field(description="3-letter ISO currency code, e.g. USD")],
    date: Annotated[
        str, Field(description="YYYY-MM-DD; omit for the latest published rate")
    ] = "latest",
) -> str:
    """Look up the official daily exchange rate from a currency to Korean won (KRW). Give a 3-letter ISO currency code such as USD, EUR or JPY, and a date (YYYY-MM-DD) to get that day's rate, or omit the date for the latest published rate. Call it once per currency."""
    base = base.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", base):
        return "error: base must be a 3-letter currency code like USD"
    if date != "latest" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return "error: date must be YYYY-MM-DD or 'latest'"
    try:
        req = urllib.request.Request(  # the default urllib agent gets a 403
            _FX_URL.format(date=date, base=base), headers={"User-Agent": "week01-agent"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        return (
            f"1 {base} = {data['rates']['KRW']} KRW "
            f"(ECB reference rate, {data['date']})"
        )
    except (urllib.error.URLError, KeyError, ValueError) as e:
        return f"error: rate lookup failed for {base} on {date} ({e})"


if __name__ == "__main__":
    mcp.run("streamable-http") if "--http" in sys.argv else mcp.run()
