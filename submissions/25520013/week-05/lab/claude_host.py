"""Week-05 lab: `claude -p` as the MCP host for tools_server.py.

The lab turns the week-01 loop into an MCP host. Here the host is Claude Code
in print mode instead: it already speaks MCP, so this file only builds the
server entry, runs one `claude -p`, and prints the tool calls from its
stream-json output in the lecture's `[tool] name(args) -> result` form.

Isolation, as in week-04's backend.py: no setting sources, no dynamic
system-prompt sections, a scratch working directory, no built-in tools, and
`--strict-mcp-config` so only the server named here is attached. The init
event's tool list is printed so that can be checked in every log.

  uv run python claude_host.py stdio "Read notes.txt and sum the numbers in it."
  uv run python claude_host.py http  "..."   # server: tools_server.py --http
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
HTTP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
SERVER = "week01-tools"
HERE = os.path.dirname(os.path.abspath(__file__))
SYSTEM = "You are a helpful assistant. Use the tools you are given to answer."


def server_entry(transport: str) -> dict:
    if transport == "http":
        return {"type": "http", "url": HTTP_URL}
    # stdio: the client starts the server itself, in the lab directory so
    # read_file sees notes.txt.
    return {
        "type": "stdio",
        "command": "uv",
        "args": ["run", "--directory", HERE, "python", "tools_server.py"],
    }


def _short(text: str, n: int = 300) -> str:
    text = text.replace("\n", "\n         ")
    return text if len(text) <= n else text[:n] + " ..."


def run(transport: str, goal: str) -> int:
    scratch = tempfile.mkdtemp(prefix="week05-lab-")
    config = os.path.join(scratch, "mcp.json")
    with open(config, "w") as f:
        json.dump({"mcpServers": {SERVER: server_entry(transport)}}, f)
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
        SYSTEM,
        "--strict-mcp-config",
        "--mcp-config",
        config,
        "--tools",
        "",
        "--allowedTools",
        f"mcp__{SERVER}",
        "--",
        goal,
    ]
    print(
        f"# {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
        f" | host: claude -p ({MODEL}) | transport: {transport}"
    )
    print(f"# server entry: {json.dumps(server_entry(transport))}")
    print(f"# goal: {goal}")
    proc = subprocess.run(cmd, cwd=scratch, capture_output=True, text=True)
    names = {}
    for line in proc.stdout.splitlines():
        ev = json.loads(line)
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            print(f"[host] {len(ev.get('tools', []))} tools: {ev.get('tools')}")
            print(f"[host] mcp servers: {ev.get('mcp_servers')}")
        elif ev.get("type") == "assistant":
            for block in ev["message"].get("content", []):
                if block.get("type") == "tool_use":
                    names[block["id"]] = f"{block['name']}({block['input']})"
        elif ev.get("type") == "user":
            for block in ev["message"].get("content", []):
                if block.get("type") == "tool_result":
                    out = block.get("content")
                    if isinstance(out, list):
                        out = "\n".join(c.get("text", "") for c in out)
                    tag = "[error]" if block.get("is_error") else "[tool]"
                    print(
                        f"  {tag} {names.get(block['tool_use_id'])} -> {_short(str(out))}"
                    )
        elif ev.get("type") == "result":
            print(f"[answer] {ev.get('result')}")
            print(
                f"[usage] turns={ev.get('num_turns')} cost_usd={ev.get('total_cost_usd')}"
                f" duration_ms={ev.get('duration_ms')}"
            )
    if proc.returncode:
        print(f"[exit {proc.returncode}] {proc.stderr.strip()[-500:]}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], sys.argv[2]))
