"""Week 05 — the week-01 agent as an MCP host.

The loop is the week-01 loop. What changed: the tools are no longer defined
here. The host connects to server.py, asks it `tools/list`, hands those
schemas to the model, and forwards each tool_use to the server via `tools/call`.

Requires: Python >= 3.10, pip install -r requirements.txt (mcp v2), ANTHROPIC_API_KEY.

Settings (env; none are secrets except the key):
  ANTHROPIC_API_KEY  required. Never written to this repo.
  AGENT_MODEL        default claude-sonnet-4-5 (same as week-01)
  CLOCK_DESC         "full" (default) | "terse"; forwarded to the server
  DROP_CLOCK         if set, clock is not offered to the model (2-tool baseline)
  MCP_SERVER         unset: start server.py as a stdio child process.
                     http://127.0.0.1:8000/mcp: connect over HTTP
                     (start it first with `python server.py --http`)

Run (from this directory; read_file is limited to the working directory):
  python host.py
  python host.py "Read notes.txt and sum the numbers in it."
  python host.py 2>&1 | tee logs/run-$(date +%m%d-%H%M).txt
"""
import os
import sys
import asyncio

import anthropic
from mcp import Client, StdioServerParameters

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-5")

SERVER = os.environ.get("MCP_SERVER") or StdioServerParameters(
    command=sys.executable,
    args=[os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")],
    env={**os.environ},  # the child gets CLOCK_DESC; the key is not needed there
)

DEFAULT_GOAL = (
    "Read notes.txt. It is an expense memo with a reimbursement deadline. "
    "Report: (1) the total amount spent, (2) how much is still unreimbursed, "
    "and (3) how many days are left until the deadline as of today."
)


async def run(goal: str, max_steps: int = 8, complete=None):
    """`complete(tools, messages)` is the model call; tests replace it."""
    if complete is None:
        client = anthropic.Anthropic()

        def complete(tools, messages):
            return client.messages.create(model=MODEL, max_tokens=1024,
                                          tools=tools, messages=messages)

    async with Client(SERVER) as session:
        # tools/list -> the schemas the model sees. Nothing is hand-written here.
        listed = (await session.list_tools()).tools
        if os.environ.get("DROP_CLOCK"):
            listed = [t for t in listed if t.name != "clock"]
        tools = [{"name": t.name, "description": t.description,
                  "input_schema": t.input_schema} for t in listed]

        via = "HTTP" if isinstance(SERVER, str) else "stdio"
        print(f"[config] model={MODEL} "
              f"clock_desc={os.environ.get('CLOCK_DESC', 'full')} "
              f"tools={[t['name'] for t in tools]} (via MCP {via})")
        print(f"[goal] {goal}")
        messages = [{"role": "user", "content": goal}]

        for step in range(max_steps):
            resp = complete(tools, messages)
            messages.append({"role": "assistant", "content": resp.content})
            print(f"[step {step + 1}] stop_reason={resp.stop_reason} "
                  f"in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    print(f"  [text] {b.text.strip()}")

            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if b.type == "text")

            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    # tools/call -> the server runs the tool, not this process
                    res = await session.call_tool(block.name, block.input)
                    out = "".join(c.text for c in res.content if c.type == "text")
                    print(f"  [tool] {block.name}({block.input}) -> {out}")
                    results.append({"type": "tool_result",
                                    "is_error": bool(res.is_error),
                                    "tool_use_id": block.id, "content": out})
            messages.append({"role": "user", "content": results})

        return "stopped: max steps exceeded"


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GOAL
    print("[answer]", asyncio.run(run(goal)))
