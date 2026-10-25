import asyncio
import json
import os
import sys

from openai import OpenAI
from mcp import Client, StdioServerParameters

SERVER = os.environ.get("MCP_SERVER") or StdioServerParameters(
    command=sys.executable,
    args=[os.path.join(os.path.dirname(__file__), "tools_server.py")]
)

def get_base_url() -> str:
    url = (
        os.environ.get("LMSTUDIO_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "http://127.0.0.1:1234/v1"
    ).rstrip("/")
    if url == "http://127.0.0.1:1234":
        url = f"{url}/v1"
    return url

BASE_URL = get_base_url()
MODEL = os.environ.get("AGENT_MODEL", "qwen/qwen3.8-27b")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))
API_KEY = (
    os.environ.get("LMSTUDIO_API_TOKEN")
    or os.environ.get("OPENAI_API_KEY")
    or "lm-studio"
)


def to_openai(tool):
    """Convert MCP Tool definition to OpenAI function format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


async def run(goal: str, max_steps: int = 8):
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    messages = [{"role": "user", "content": goal}]

    async with Client(SERVER) as mcp:
        tools_list = (await mcp.list_tools()).tools
        openai_tools = [to_openai(t) for t in tools_list]
        print(f"[host] {len(tools_list)} tools: {[t.name for t in tools_list]}")
        print(f"[host] Using model '{MODEL}' via {BASE_URL}")

        for step in range(max_steps):
            extra_body = {}
            if "openrouter.ai" in BASE_URL:
                extra_body = {"reasoning": {"enabled": False}}

            try:
                resp = client.chat.completions.create(
                    model=MODEL,
                    temperature=TEMPERATURE,
                    tools=openai_tools,
                    messages=messages,
                    extra_body=extra_body if extra_body else None,
                )
            except Exception as e:
                print(f"[host] LLM call failed: {e}", file=sys.stderr)
                if "127.0.0.1:1234" in BASE_URL:
                    print(
                        "[host] Tip: LM Studio가 실행 중이고 Developer(Local Server)에서 'Start Server'가 켜져 있는지 확인해주세요.",
                        file=sys.stderr,
                    )
                return f"error: {e}"

            msg = resp.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                return msg.content or ""

            for call in msg.tool_calls:
                args = json.loads(call.function.arguments) if call.function.arguments else {}
                result = await mcp.call_tool(call.function.name, args)
                out = "\n".join(c.text for c in result.content if c.type == "text")
                print(f"  [tool] {call.function.name}({args}) -> {out}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(out),
                })

        return "stopped: max steps exceeded"


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else "Read notes.txt and sum the numbers in it."
    result = asyncio.run(run(goal))
    print(result)
