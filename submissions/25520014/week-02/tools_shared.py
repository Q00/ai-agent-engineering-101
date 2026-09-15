"""Week 02 — 두 하네스가 공유하는 도구, 모델 호출, 계측기.

도구가 다르면 하네스 비교가 성립하지 않는다. 그래서 read_file과 count_pattern은
두 하네스가 여기서 똑같이 import한다. 다섯 요소 중 "도구 granularity"는 이 파일에
고정되어 있고, 하네스가 바꾸는 것은 나머지 네 요소다.

모델은 구독 중인 Claude를 `claude -p`로 부른다. 이 경로에는 raw Messages API와
다른 성질이 두 개 있고, 둘 다 하네스 설계를 바꾼다.

1. tool_use 블록을 받을 수 없다. 응답은 텍스트뿐이므로 하네스가 텍스트를 파싱해
   도구를 직접 실행한다. ReAct 원전(Yao et al. 2022)도 tool_use API가 없던 시절의
   텍스트 파싱 방식이었고, 강의 노트도 "Thought가 텍스트로 남는다는 성질을 쓴다"고 한다.
2. 호출마다 새 세션이다. 기억이 없으므로 이전 스텝을 어떻게 전달할지가
   "컨텍스트 관리" 요소의 실제 설계 결정이 된다. 이 실험은 매 호출마다 지금까지의
   Thought/Action/Observation 전문을 다시 보낸다.
"""
import json
import os
import re
import subprocess
from dataclasses import dataclass, field

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5")

# claude -p 고정 플래그.
#   --allowed-tools ""   Claude Code 자체 도구(Read/Bash 등)를 막는다. 이게 없으면
#                        모델이 우리 도구를 거치지 않고 app.log를 직접 읽어서
#                        "같은 도구"라는 통제 변수가 깨진다.
#   --system-prompt      Claude Code 기본 시스템 프롬프트를 하네스 것으로 대체한다.
#   --exclude-dynamic-system-prompt-sections, --strict-mcp-config, --mcp-config {}
#                        환경에 따라 변하는 부분(MCP 서버 목록 등)을 빼서 재현성을 지킨다.
_BASE_FLAGS = [
    "--output-format", "json",
    "--model", MODEL,
    "--allowed-tools", "",
    "--exclude-dynamic-system-prompt-sections",
    "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}',
]

# ---------------------------------------------------------------- 도구


def read_file(path: str) -> str:
    """작업 디렉토리의 텍스트 파일을 읽는다."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]          # 컨텍스트 보호용 상한, week 01과 동일


def count_pattern(path: str, pattern: str) -> str:
    """정규식에 걸리는 줄 수를 센다."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    rx = re.compile(pattern)
    with open(full, encoding="utf-8") as f:
        return str(sum(1 for line in f if rx.search(line)))


TOOLS_IMPL = {"read_file": read_file, "count_pattern": count_pattern}

# 모델에게 도구를 알려주는 문장. tool_use 스키마가 아니라 프롬프트에 들어가는 텍스트다.
# finish는 TOOLS_IMPL에 없다. 하네스가 가로채서 종료 신호로 쓴다.
TOOL_DOC = """  read_file(path)                -> 파일 내용을 돌려준다(앞 4000자).
  count_pattern(path, pattern)   -> 정규식에 걸리는 줄 수를 돌려준다.
  finish(answer)                 -> 태스크를 끝낸다. 답을 알았을 때 이것을 부른다."""

# ---------------------------------------------------------------- 계측기


class Meter:
    """네 지표를 한곳에서 센다.

    tokens는 claude -p가 보고하는 입력·출력 합계 전부다. 여기에는 Claude Code가
    호출마다 붙이는 고정 시스템 오버헤드가 포함된다(측정값은 REPORT.md에 적었다).
    보정하지 않은 실측치를 results.csv에 넣고, 보정은 리포트에서 한다.
    """

    def __init__(self):
        self.tokens = 0
        self.iters = 0            # 1 iteration = claude -p 호출 1회
        self.interventions = 0    # 사람이 승인 또는 거부한 횟수
        self.calls = []           # 호출별 usage 원본. 오버헤드 보정 근거로 남긴다.

    def add(self, usage: dict):
        total_in = (usage.get("input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0))
        out = usage.get("output_tokens", 0)
        self.tokens += total_in + out
        self.iters += 1
        self.calls.append({"in": total_in, "out": out})


# ---------------------------------------------------------------- 모델 호출


class ModelError(RuntimeError):
    """claude -p가 실패했다. 런 하나의 실패로 기록되고 런을 잃지는 않는다."""


def call_claude(prompt: str, system: str, meter: Meter) -> str:
    """claude -p를 한 번 부르고 응답 텍스트를 돌려준다. 1 iteration."""
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


# ---------------------------------------------------------------- Action 파싱


@dataclass
class Action:
    tool: str
    args: dict


@dataclass
class Step:
    """모델이 낸 한 스텝. action이 None이면 파싱에 실패했다는 뜻이다."""
    thought: str
    action: "Action | None" = None
    error: str = ""
    raw: str = field(default="", repr=False)


def parse_step(text: str) -> Step:
    """Thought 한 줄과 Action 한 줄을 뽑는다.

    Action은 JSON 한 줄로 받는다. 파싱 실패는 숨기지 않고 Step.error에 담아
    하네스가 Observation으로 되돌릴 수 있게 한다(에러 복구 요소).
    """
    thought = ""
    m = re.search(r"^\s*Thought:\s*(.+)$", text, flags=re.M)
    if m:
        thought = m.group(1).strip()

    m = re.search(r"^\s*Action:\s*(.+)$", text, flags=re.M)
    if not m:
        return Step(thought, None, "Action 줄이 없다. 'Action: {\"tool\": ..., "
                                   "\"args\": {...}}' 형식으로 한 줄 쓰라.", text)

    blob = m.group(1).strip()
    blob = re.sub(r"^```(?:json)?|```$", "", blob).strip()
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError as e:
        return Step(thought, None, f"Action의 JSON을 읽을 수 없다({e}). "
                                   f"한 줄 JSON으로 다시 쓰라.", text)
    if not isinstance(obj, dict) or "tool" not in obj:
        return Step(thought, None, "Action JSON에 \"tool\" 키가 없다.", text)
    args = obj.get("args", {})
    if not isinstance(args, dict):
        return Step(thought, None, "Action의 \"args\"는 객체여야 한다.", text)
    return Step(thought, Action(str(obj["tool"]), args), "", text)


def run_tool(action: Action) -> str:
    """도구를 실행한다. 예외는 문자열로 돌려 Observation이 되게 한다."""
    fn = TOOLS_IMPL.get(action.tool)
    if fn is None:
        return f"error: unknown tool {action.tool}"
    try:
        return str(fn(**action.args))
    except Exception as e:                # 에러 복구: 에러도 Observation이다
        return f"error: {e}"
