"""Turn `claude -p --output-format stream-json --verbose` into the lab's log lines."""
import json
import sys

for line in sys.stdin:
    ev = json.loads(line)
    if ev.get("type") == "system" and ev.get("subtype") == "init":
        print(f"  [host] claude -p, model={ev.get('model')}, tools={ev.get('tools')}")
        print(f"  [host] mcp_servers={ev.get('mcp_servers')}")
    for block in (ev.get("message") or {}).get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            print(f"  [tool] {block['name']}({block['input']})")
        elif block.get("type") == "tool_result":
            c = block.get("content")
            text = c if isinstance(c, str) else "".join(x.get("text", "") for x in c or [])
            print(f"         -> {text}")
    if ev.get("type") == "result":
        print(f"  [answer] {ev.get('result')}")
        print(f"  [result] num_turns={ev.get('num_turns')} is_error={ev.get('is_error')}")
