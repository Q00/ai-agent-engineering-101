"""host: 한 턴 = claude -p 한 번 실행. Claude Code가 market server에 MCP client로 붙는다.

lab 6단계(second_client.sh)와 같은 방식에 Authorization 헤더 하나를 더했다. 모델은 tool
이름과 인자만 고르고, 토큰은 Claude Code가 모든 요청 헤더에 붙인다. 모델은 토큰을 보지 못한다.

  --tools ""                내장 Read/Bash 등을 없앤다(lab에서 --allowed-tools ""로는 안 막혔다)
  --strict-mcp-config       사용자 설정의 다른 MCP server를 무시하고 --mcp-config만 쓴다
  --allowed-tools mcp__market__*   market tool을 승인 프롬프트 없이 쓴다
  --max-turns               한 턴 안에서 모델 호출 상한. get_negotiation + 수 4번 시도로 충분하다
  stream-json               tool_use, tool_result, 모델의 말을 하나씩 로그에 남긴다

init 이벤트에 market tool 다섯 개 말고 다른 tool이 보이면 그 턴을 실패로 처리한다.
"""
import json
import os
import subprocess

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
MAX_TURNS = 12
MARKET_TOOLS = {f"mcp__market__{t}" for t in
                ("get_negotiation", "propose", "accept_proposal", "reject_proposal", "refuse")}
FLAGS = ["--model", MODEL, "--tools", "", "--strict-mcp-config",
         "--allowed-tools", "mcp__market__*", "--max-turns", str(MAX_TURNS),
         "--exclude-dynamic-system-prompt-sections",
         "--output-format", "stream-json", "--verbose"]


class HostError(RuntimeError):
    pass


def take_turn(url: str, token: str, system: str, prompt: str, log) -> dict:
    """한 당사자의 한 턴. 로그에 tool 호출과 결과를 쓰고 요약을 돌려준다."""
    config = {"mcpServers": {"market": {"type": "http", "url": url,
                                        "headers": {"Authorization": f"Bearer {token}"}}}}
    cmd = ["claude", "-p", prompt, "--system-prompt", system,
           "--mcp-config", json.dumps(config)] + FLAGS
    env = dict(os.environ, ENABLE_CLAUDEAI_MCP_SERVERS="false")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env,
                          stdin=subprocess.DEVNULL)
    summary = {"tool_uses": 0, "num_turns": None, "is_error": None}
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            tools = set(ev.get("tools") or [])
            servers = [(s["name"], s["status"]) for s in ev.get("mcp_servers") or []]
            if tools != MARKET_TOOLS or servers != [("market", "connected")]:
                raise HostError(f"unexpected tools {sorted(tools)} / servers {servers}")
        for block in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block["text"].strip():
                log(f"      [say] {block['text'].strip()}")
            elif block.get("type") == "tool_use":
                summary["tool_uses"] += 1
                name = block["name"].removeprefix("mcp__market__")
                log(f"      [call] {name}({json.dumps(block['input'])})")
            elif block.get("type") == "tool_result":
                c = block.get("content")
                text = c if isinstance(c, str) else "".join(x.get("text", "") for x in c or [])
                tag = "[refused]" if block.get("is_error") else "[result]"
                log(f"      {tag} {' '.join(text.split())}")
        if ev.get("type") == "result":
            summary["num_turns"], summary["is_error"] = ev.get("num_turns"), ev.get("is_error")
            summary["subtype"] = ev.get("subtype")
    if proc.returncode != 0 and summary["num_turns"] is None:
        raise HostError(f"claude -p exited {proc.returncode}: {proc.stderr[:300]}")
    return summary
