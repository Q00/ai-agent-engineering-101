"""Week 05 LAB: expose the week-01 tools through an MCP server.

Run from the directory whose files the agent may read:
    python tools_server.py          # stdio
    python tools_server.py --http   # http://127.0.0.1:8000/mcp
"""

import ast
import ipaddress
import operator
import socket
import sys
from pathlib import Path
from urllib import request as urlrequest
from urllib.parse import urlparse

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


# Week-01 tool 3: preserve the URL checks and bounded response handling.
FETCH_TIMEOUT = 5.0
FETCH_MAX_BYTES = 200_000
FETCH_MAX_CHARS = 4000
FETCH_ALLOWED_PORTS = {80, 443, 8000, 8080}


def _check_url(url: str):
    """Raise ValueError unless url is an http(s) URL pointing at a public host."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError(f"scheme not allowed: {p.scheme or 'missing'}")
    if not p.hostname:
        raise ValueError("no host in URL")
    port = p.port or (443 if p.scheme == "https" else 80)
    if port not in FETCH_ALLOWED_PORTS:
        raise ValueError(f"port not allowed: {port}")
    try:
        infos = socket.getaddrinfo(p.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"DNS lookup failed: {e}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError(f"non-public address: {ip}")
    return p


class _GuardedRedirect(urlrequest.HTTPRedirectHandler):
    """Re-run the same checks on every redirect target."""

    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urlrequest.build_opener(_GuardedRedirect)


@mcp.tool()
def fetch(url: str) -> str:
    """Fetch a public web page or API response over http/https and return the first few thousand characters of its text. Local, private, and non-http URLs are rejected."""
    try:
        _check_url(url)
    except ValueError as e:
        return f"denied: {e}"

    req = urlrequest.Request(url, headers={
        "User-Agent": "week01-agent/1.0",
        "Accept": "text/*, application/json;q=0.9, */*;q=0.1",
    })
    try:
        with _OPENER.open(req, timeout=FETCH_TIMEOUT) as resp:
            ctype = resp.headers.get_content_type()
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read(FETCH_MAX_BYTES + 1)
            status = resp.status
    except ValueError as e:
        return f"denied: {e}"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"

    if not (ctype.startswith("text/") or ctype in
            ("application/json", "application/xml", "application/xhtml+xml")):
        return f"denied: content-type not allowed: {ctype}"

    body_truncated = len(raw) > FETCH_MAX_BYTES
    text = raw[:FETCH_MAX_BYTES].decode(charset, errors="replace")
    if len(text) > FETCH_MAX_CHARS:
        text, body_truncated = text[:FETCH_MAX_CHARS], True

    suffix = "\n...[truncated]" if body_truncated else ""
    return f"HTTP {status} {ctype}\n{text}{suffix}"


if __name__ == "__main__":
    if "--http" in sys.argv[1:]:
        mcp.run(transport="streamable-http", stateless_http=True, json_response=True)
    else:
        mcp.run()
