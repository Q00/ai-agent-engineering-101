"""Week 05 lab — the week-01 loop as an MCP host, with `claude -p` as the model.

  python mcp_agent.py                                       # spawns tools_server.py over stdio
  MCP_SERVER=http://127.0.0.1:8000/mcp python mcp_agent.py  # attaches over Streamable HTTP

What changed from week-01 first_agent.py:
  - no TOOLS / TOOLS_IMPL in this file. The tool list comes from tools/list and every
    call goes out as tools/call, so the host holds no tool name.
  - the model is `claude -p` instead of an OpenAI-compatible API. claude -p returns
    text only (no tool_calls), so the tool list goes into the system prompt and the
    model answers with one JSON object per step, which the loop parses. This parse
    step is the one thing the lecture's host does not have.
"""
import asyncio
import json
import os
import re
import subprocess
import sys

from mcp import Client, StdioServerParameters

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.environ.get("MCP_SERVER") or StdioServerParameters(
    command=sys.executable, args=[os.path.join(HERE, "tools_server.py")])
MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
GOAL = "Read notes.txt and sum the numbers in it."

# --tools "" removes Claude Code's own Read/Bash, so the only way to the file is
# through our server. --allowed-tools "" is not enough on CLI 2.1.281: with it the
# model still read notes.txt itself (num_turns 2, no tools/call), see
# logs/attempt-01-builtin-read-leak/. The empty MCP config keeps the user's servers out.
CLAUDE_FLAGS = [
    "--output-format", "json",
    "--model", MODEL,
    "--tools", "",
    "--exclude-dynamic-system-prompt-sections",
    "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}',
]

SYSTEM = """You are an agent that uses tools to finish a task.

Available tools (from the MCP server):
{tools}

Reply with exactly one JSON object and nothing else, one of:
  {{"tool": "<tool name>", "args": {{...}}}}   to call a tool
  {{"answer": "<final answer>"}}               when the task is done
You will see each tool result before your next reply."""


def describe(tool):
    """MCP tool -> one block of the system prompt (the text-protocol version of to_openai)."""
    return json.dumps({"name": tool.name, "description": tool.description,
                       "inputSchema": tool.input_schema})


def call_claude(prompt: str, system: str) -> str:
    cmd = ["claude", "-p", prompt, "--system-prompt", system] + CLAUDE_FLAGS
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr[:300]}")
    payload = json.loads(proc.stdout)
    if payload.get("is_error"):
        raise RuntimeError(f"claude -p error: {str(payload.get('result'))[:300]}")
    return payload.get("result") or ""


def parse(text: str):
    """The model's reply -> the FIRST JSON object in it, or None if there is none.

    Only the first object counts; anything after it is dropped. haiku sometimes writes
    a call, then a "Tool result" it made up, then an answer, all in one reply
    (logs/attempt-02-strict-parse/http-run-01.txt). Keeping only the first object
    means the call really goes to the server and the invented result never counts.
    """
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("tool" in obj or "answer" in obj):
            return obj
    return None


def parse_exact(text: str):
    """The whole reply as one JSON object, or None. Used only to log replies with extra text."""
    try:
        return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
    except json.JSONDecodeError:
        return None


async def run(goal, max_steps=8):
    async with Client(SERVER) as mcp:
        tools = (await mcp.list_tools()).tools                      # tools/list
        print(f"  [host] {len(tools)} tools: {[t.name for t in tools]}")
        system = SYSTEM.format(tools="\n".join(describe(t) for t in tools))
        transcript = [f"Task: {goal}"]   # claude -p is stateless: resend the whole history

        for step in range(max_steps):   # <- this loop is what makes it an agent
            reply = call_claude("\n\n".join(transcript), system)
            obj = parse(reply)
            if obj is not None and parse_exact(reply) != obj:
                print(f"  [model] {reply[:300]!r}")    # extra text around the object was dropped
            # the history keeps only what the loop acted on, not the dropped tail
            transcript.append(f"You: {json.dumps(obj) if obj is not None else reply}")

            if obj is None:
                print(f"  [parse] not one JSON object: {reply[:200]!r}")
                transcript.append("Error: reply with exactly one JSON object.")
                continue
            if "answer" in obj:                                        # final answer -> stop
                return str(obj["answer"])

            name, args = obj.get("tool"), obj.get("args", {})
            result = await mcp.call_tool(name, args)                   # tools/call
            out = "\n".join(c.text for c in result.content if c.type == "text")
            print(f"  [tool] {name}({args}) -> {out}")
            transcript.append(f"Tool result ({name}): {out}")

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    print(f"  [host] server={SERVER if isinstance(SERVER, str) else 'stdio'} model={MODEL}")
    print(f"  [goal] {GOAL}")
    print(f"  [answer] {asyncio.run(run(GOAL))}")
