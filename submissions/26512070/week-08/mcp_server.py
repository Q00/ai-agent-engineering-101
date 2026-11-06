"""The memory MCP server (HARNESS.md 2.3, build step 8): our read-only tools over MCP stdio.

    claude mcp add harness-memory -- py C:\\...\\week-08\\mcp_server.py      # then ask Claude Code about the market

It exposes the me.*, market.*, memory.*, eval.*, calc.* and check.* tools of tools.registry (the same
definitions every agent uses), with the dots written as underscores because MCP tool names allow only
letters, digits, _ and -. Nothing that acts or writes (act.*, rec.*) is exposed. Every call goes through
tools.registry.call, so it is checked against the schema and logged like any other tool call.

Protocol: JSON-RPC 2.0, one message per line on stdin/stdout (MCP stdio transport).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tools import calc, check, evaluate, harness_tools, query, record, search  # noqa: E402,F401  (registers the tools)
from tools.registry import TOOLS, Ctx, call  # noqa: E402

READ_ONLY = ("me.", "market.", "memory.", "eval.", "calc.", "check.")
PROTOCOL = "2025-06-18"


def exposed():
    return {n.replace(".", "_"): n for n in sorted(TOOLS) if n.startswith(READ_ONLY)}


def handle(msg, ctx):
    """The reply to one JSON-RPC message, or None for a notification."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "harness-memory", "version": "1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        names = exposed()
        result = {"tools": [{"name": k, "description": TOOLS[v].description, "inputSchema": TOOLS[v].input_schema}
                            for k, v in names.items()]}
    elif method == "tools/call":
        p = msg.get("params") or {}
        name = exposed().get(p.get("name"))
        if name is None:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {p.get('name')!r}"}}
        out = call(ctx, name, p.get("arguments") or {})
        result = {"content": [{"type": "text", "text": json.dumps(out.get("result") if out["ok"] else out,
                                                                   ensure_ascii=False, default=str)}],
                  "isError": not out["ok"]}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method!r}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    ctx = Ctx(caller="mcp")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg, ctx)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
