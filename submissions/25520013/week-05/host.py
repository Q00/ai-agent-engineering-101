"""Week-05: one turn for one party, with `claude -p` as the MCP host.

Adapted from lab/claude_host.py. One turn is one fresh `claude -p` run (no
--resume): the party's memory of the negotiation is whatever get_negotiation
returns. The market server entry carries this party's bearer token in an
Authorization header, written to a temp mcp.json in a fresh scratch directory.

Isolation flags as in the lab: no setting sources, no dynamic system-prompt
sections, no built-in tools, `--strict-mcp-config` so only the market is
attached. The init event's tool list is returned so every log shows it.

Claude Code shows a tool result as the server's structuredContent JSON (e.g.
{"result": "..."}) when there is one; the text is kept exactly as returned.
"""

import json
import os
import subprocess
import tempfile

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
SERVER = "market"
TIMEOUT_S = 240


def run_turn(mcp_url: str, token: str, system: str, message: str) -> dict:
    """Run one host turn; returns tool calls, text blocks, init tools, num_turns, error."""
    scratch = tempfile.mkdtemp(prefix="week05-turn-")
    config = os.path.join(scratch, "mcp.json")
    entry = {
        "type": "http",
        "url": mcp_url,
        "headers": {"Authorization": f"Bearer {token}"},
    }
    with open(config, "w") as f:
        json.dump({"mcpServers": {SERVER: entry}}, f)
    cmd = [
        "claude",
        "--print",
        "--model",
        MODEL,
        "--output-format",
        "stream-json",
        "--verbose",
        "--exclude-dynamic-system-prompt-sections",
        "--setting-sources",
        "",
        "--system-prompt",
        system,
        "--strict-mcp-config",
        "--mcp-config",
        config,
        "--tools",
        "",
        "--allowedTools",
        f"mcp__{SERVER}",
        "--",
        message,
    ]
    out = {
        "init_tools": None,
        "events": [],
        "tool_calls": [],
        "num_turns": None,
        "error": None,
    }
    try:
        proc = subprocess.run(
            cmd, cwd=scratch, capture_output=True, text=True, timeout=TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        out["error"] = f"host timeout after {TIMEOUT_S}s"
        return out
    calls = {}  # tool_use id -> call dict, filled in when its result arrives
    for line in proc.stdout.splitlines():
        ev = json.loads(line)
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            out["init_tools"] = ev.get("tools")
        elif ev.get("type") == "assistant":
            for block in ev["message"].get("content", []):
                if block.get("type") == "text" and block["text"].strip():
                    out["events"].append(("text", block["text"].strip()))
                elif block.get("type") == "tool_use":
                    call = {
                        "name": block["name"].removeprefix(f"mcp__{SERVER}__"),
                        "args": block["input"],
                        "result": None,
                        "is_error": None,
                    }
                    calls[block["id"]] = call
                    out["tool_calls"].append(call)
                    out["events"].append(("call", call))
        elif ev.get("type") == "user":
            for block in ev["message"].get("content", []):
                if block.get("type") == "tool_result":
                    res = block.get("content")
                    if isinstance(res, list):
                        res = "\n".join(c.get("text", "") for c in res)
                    call = calls.get(block["tool_use_id"])
                    if call is not None:
                        call["result"], call["is_error"] = (
                            str(res),
                            bool(block.get("is_error")),
                        )
        elif ev.get("type") == "result":
            out["num_turns"] = ev.get("num_turns")
    if proc.returncode:
        out["error"] = f"claude exit {proc.returncode}: {proc.stderr.strip()[-500:]}"
    return out
