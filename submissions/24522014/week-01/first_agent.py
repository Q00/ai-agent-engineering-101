"""Week 01 starter — OpenAI-compatible API version (works with OpenRouter).

Four tools: calculator, read_file, fetch, clock.
Requires: pip install openai, and in the environment:
  OPENAI_API_KEY   your key (an OpenRouter key works)
  OPENAI_BASE_URL  optional; set to https://openrouter.ai/api/v1 for OpenRouter
  AGENT_MODEL      optional; defaults to gpt-4o-mini. For OpenRouter free
                   models use e.g. AGENT_MODEL=meta-llama/llama-3.3-70b-instruct:free
"""
import os
import sys
import ast
import json
import socket
import operator
import ipaddress
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlparse
from urllib import request as urlrequest

from openai import OpenAI

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


def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression string, e.g. '3 * (4 + 5)'."""
    return str(_ev(ast.parse(expression, mode="eval").body))


# ---- tool 2: read_file (blocked outside the working directory) ----
def read_file(path: str) -> str:
    """Return the contents of a text file."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# ---- tool 3: fetch (http/https only, public hosts only, bounded) ----
FETCH_TIMEOUT = 5.0          # seconds, per socket operation
FETCH_MAX_BYTES = 200_000    # stop reading the body here
FETCH_MAX_CHARS = 4000       # what the model actually sees
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
        # is_global excludes loopback, private, link-local, reserved, unspecified
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


def fetch(url: str) -> str:
    """Fetch a public http(s) URL and return the beginning of its text body."""
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
    except ValueError as e:                 # raised by the redirect guard
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


# ---- tool 4: clock (current time, IANA timezone) ----
DEFAULT_TZ = os.environ.get("AGENT_TZ", "Asia/Seoul")


def clock(timezone: str = DEFAULT_TZ) -> str:
    """Return the current date and time in an IANA timezone."""
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return (f"error: unknown timezone: {timezone!r} "
                "(use an IANA name like 'Asia/Seoul'; on Windows this needs "
                "`pip install tzdata`)")
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"

    now = datetime.now(tz)
    return (f"{now.strftime('%Y-%m-%d %H:%M:%S')} "
            f"{now.tzname()} (UTC{now.strftime('%z')}), "
            f"{now.strftime('%A')}, tz={timezone}")


TOOLS_IMPL = {"calculator": calculator, "read_file": read_file,
              "fetch": fetch, "clock": clock}

# ---- tool schemas handed to the model (the description IS the interface) ----
TOOLS = [
    {"type": "function",
     "function": {
         "name": "calculator",
         "description": "Evaluate an arithmetic expression.",
         "parameters": {"type": "object",
                        "properties": {"expression": {"type": "string"}},
                        "required": ["expression"]}}},
    {"type": "function",
     "function": {
         "name": "read_file",
         "description": "Read a text file in the working directory.",
         "parameters": {"type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"]}}},
    {"type": "function",
     "function": {
         "name": "fetch",
         "description": ("Fetch a public web page or API response over http/https "
                         "and return the first few thousand characters of its text. "
                         "Local, private, and non-http URLs are rejected."),
         "parameters": {"type": "object",
                        "properties": {"url": {
                            "type": "string",
                            "description": "Absolute http:// or https:// URL."}},
                        "required": ["url"]}}},
    {"type": "function",
     "function": {
         "name": "clock",
         "description": ("Get the current date, time, weekday, and UTC offset. "
                         f"Defaults to {DEFAULT_TZ} if no timezone is given. "
                         "Call this instead of guessing today's date."),
         "parameters": {"type": "object",
                        "properties": {"timezone": {
                            "type": "string",
                            "description": ("IANA timezone name, e.g. 'Asia/Seoul', "
                                            "'UTC', 'America/New_York'.")}},
                        "required": []}}},
]

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")


def run(goal: str, max_steps: int = 8):
    client = OpenAI()  # uses OPENAI_API_KEY and OPENAI_BASE_URL
    messages = [{"role": "user", "content": goal}]

    for step in range(max_steps):   # <- this loop is what makes it an agent
        resp = client.chat.completions.create(
            model=MODEL, tools=TOOLS, messages=messages)
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:               # final answer -> stop
            return msg.content or ""

        for call in msg.tool_calls:          # execute tool calls -> observe
            args = json.loads(call.function.arguments)
            out = TOOLS_IMPL[call.function.name](**args)
            print(f"  [tool] {call.function.name}({args}) -> {str(out)[:200]}")
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": str(out)})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read notes.txt and sum the numbers in it."
    print(run(goal))
