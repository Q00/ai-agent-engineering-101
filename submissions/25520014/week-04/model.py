"""claude -p 호출 계층. week-03 model.py에서 가져와 다중 턴용으로 고쳤다.

week-03의 계약망은 입찰 한 건이 단발 호출이라 history가 필요 없었다. 이번 주 협상은
에이전트가 자기 직전 발화를 기억해야 하므로 대화 기록이 필요한데, claude -p는 호출마다
새 세션이다. 두 가지 길이 있었다.

  (A) 지금까지의 transcript를 프롬프트 문자열로 펼쳐서 매 턴 통째로 넘긴다.
  (B) --resume <session_id>로 에이전트마다 세션 하나를 유지한다.

(A)를 골랐다. 재현성이 채점의 절반인데, (A)는 모델이 그 턴에 무엇을 봤는지가 프롬프트
문자열 그대로 로그에 남는다. (B)는 기록이 ~/.claude 세션 파일 안에 있어 로그만으로는
재현을 확인할 수 없고, 중단된 실행을 이어 돌릴 때 세션 상태가 꼬일 여지가 있다.

(A)의 대가는 적어 둔다. 자기 발화가 assistant 턴이 아니라 user 프롬프트 안의 인용문으로
들어간다. 모델이 "내가 한 말"과 "남이 한 말"을 턴 구조가 아니라 라벨로 구분하게 되므로,
lab 설명("자기 메시지가 assistant 턴")과 이 구현은 이 지점에서 다르다.

week-03에서 그대로 유지한 성질:

1. temperature를 지정할 수 없다. 값을 추측하지 말고 "직접 설정 불가, 내부 값 미확인"으로
   기록한다.
2. --allowed-tools "" 로 Claude Code 자체 도구를 막는다. 이게 없으면 에이전트가 Read로
   scenarios.json을 열어 상대의 비공개 한도를 볼 수 있다. 통제 변수 보호용이다.
3. 호출당 약 36,480 토큰은 Claude Code의 고정 오버헤드다. 인용할 때 보정하고
   보정했다고 적는다.
"""
import json
import os
import subprocess
import time

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")

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

    results.csv가 요구하는 reader_calls는 '메시지를 읽는 데 쓴 모델 호출 수'이므로
    에이전트 호출과 reader 호출을 따로 세야 한다. Meter 인스턴스를 둘로 나눠 쓴다.
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
    """claude -p 자체가 실패했다. 메시지 파싱 실패와는 구분한다.
    전자는 에피소드의 크래시(results.csv note 행), 후자는 측정 대상인 format_errors다."""


def render_history(history: list) -> str:
    """history를 프롬프트 문자열 하나로 펼친다. (A) 방식의 핵심.

    history는 lab의 규약대로 {"role": "assistant"|"user", "content": str}의 리스트다.
    assistant가 이 에이전트 자신의 발화, user가 상대의 발화다.
    """
    if not history:
        return ("The negotiation has not started yet. You speak first.\n\n"
                "Write your message now.")
    lines = []
    for m in history:
        who = "You" if m["role"] == "assistant" else "The other party"
        lines.append(f"{who}: {m['content']}")
    return ("This is the negotiation so far.\n\n"
            + "\n\n".join(lines)
            + "\n\nWrite your next message now.")


def call_model(system: str, history: list, meter: Meter, retries: int = 3) -> str:
    """claude -p를 한 번 부르고 응답 텍스트를 돌려준다. 메시지 1건 = 호출 1회.

    claude -p도 과부하나 네트워크로 간헐적으로 죽는다. lab이 429에 대해 요구하는 것과
    같은 이유로, 실패한 호출은 대기를 늘려 가며 다시 시도하고 그래도 안 되면 에피소드를
    크래시로 남긴다.
    """
    cmd = ["claude", "-p", render_history(history), "--system-prompt", system] + _BASE_FLAGS
    last = None
    for attempt in range(retries):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            last = ModelError("claude -p timed out after 300s")
            continue
        if proc.returncode != 0:
            last = ModelError(f"claude -p exited {proc.returncode}: {proc.stderr[:300]}")
            continue
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last = ModelError(f"claude -p gave non-JSON: {proc.stdout[:300]!r}")
            continue
        if payload.get("is_error"):
            last = ModelError(f"claude -p error: {str(payload.get('result'))[:300]}")
            continue
        meter.add(payload.get("usage", {}))
        return (payload.get("result") or "").strip()
    raise last
