"""claude -p 호출 계층. week-02 tools_shared.py에서 호출부만 떼어 왔다.

계약망에는 도구가 없다. 입찰 한 건은 system prompt 하나에 user message 하나를 붙인
단발 호출이고, 그 응답 텍스트가 입찰서다. 그래서 week-02에서 가져온 것은
_BASE_FLAGS / Meter / call_claude 셋뿐이고 도구 구현과 Action 파서는 가져오지 않았다.

이 경로의 성질 중 이번 주 설계에 영향을 주는 것:

1. temperature를 지정할 수 없다. 강의노트 지시대로 값을 추측하지 말고
   "직접 설정 불가, 내부 값 미확인"으로 기록한다.
2. tool_use 블록이 없으므로 입찰서를 텍스트로 받아 직접 파싱한다.
   파싱 실패는 고유한 실패 모드이므로 세어서 보고한다.
3. 호출마다 새 세션이라 contractor는 이전 입찰을 기억하지 못한다.
   계약망은 한 사이클이 독립이므로 프로토콜과 충돌하지 않는다.
"""
import json
import os
import subprocess

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5")

# 고정 플래그. week-02와 동일하고 CLI 2.1.251에서 전부 유효함을 확인했다.
#   --allowed-tools ""  Claude Code 자체 도구를 막는다. 이번 주는 하네스가 주는 도구가
#                       없지만, 이게 없으면 모델이 Read로 tasks.json을 열어 gold를
#                       직접 볼 수 있다. 통제 변수 보호용으로 유지한다.
_BASE_FLAGS = [
    "--output-format", "json",
    "--model", MODEL,
    "--allowed-tools", "",
    "--exclude-dynamic-system-prompt-sections",
    "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}',
]


class Meter:
    """호출 수와 토큰을 센다.

    results.csv가 요구하는 것은 메시지 수지 토큰이 아니다. 토큰을 세는 것은
    REPORT 비교표의 "협상 비용" 행에 쓸 근거를 남기기 위해서다. 호출당 약 36,480
    토큰은 Claude Code의 고정 오버헤드이므로 인용할 때 보정하고 보정했다고 적는다.
    """

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, usage: dict):
        total_in = (usage.get("input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0))
        self.tokens += total_in + usage.get("output_tokens", 0)
        self.calls += 1


class ModelError(RuntimeError):
    """claude -p 자체가 실패했다. 입찰 파싱 실패와는 구분한다.
    전자는 런의 크래시(results.csv note 행), 후자는 측정 대상인 무입찰이다."""


def call_claude(prompt: str, system: str, meter: Meter) -> str:
    """claude -p를 한 번 부르고 응답 텍스트를 돌려준다. 입찰 1건 = 호출 1회."""
    cmd = ["claude", "-p", prompt, "--system-prompt", system] + _BASE_FLAGS
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise ModelError(f"claude -p exited {proc.returncode}: {proc.stderr[:300]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ModelError(f"claude -p gave non-JSON: {proc.stdout[:300]!r}")
    if payload.get("is_error"):
        raise ModelError(f"claude -p error: {str(payload.get('result'))[:300]}")
    meter.add(payload.get("usage", {}))
    return payload.get("result") or ""
