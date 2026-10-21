import ast
import operator
import os
import sys

from mcp.server.mcpserver import MCPServer

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


# ---- tool 3: write_note (blocked outside the working directory) ----
@mcp.tool()
def write_note(path: str, content: str) -> str:
    """Write text content to a file in the working directory."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
        return f"wrote note to {path}"


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run("streamable-http")
    else:
        mcp.run()
