import os
import ast
import json
import time
import operator
from datetime import datetime

from openai import OpenAI, RateLimitError


# ---- OpenRouter 설정 ----
MODEL = "minimax/minimax-m3:free"

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)


# ---- 도구 1: 계산기 ----
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _ev(node):
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.BinOp):
        return _OPS[type(node.op)](
            _ev(node.left),
            _ev(node.right)
        )

    if isinstance(node, ast.UnaryOp):
        return _OPS[type(node.op)](
            _ev(node.operand)
        )

    raise ValueError("허용되지 않은 식")


def calculator(expression: str) -> str:
    """수식 문자열을 계산한다."""
    return str(
        _ev(ast.parse(expression, mode="eval").body)
    )


# ---- 도구 2: 파일 읽기 ----
def read_file(path: str) -> str:
    """작업 폴더 안의 텍스트 파일을 읽는다."""

    full = os.path.abspath(path)
    cwd = os.path.abspath(os.getcwd())

    if not full.startswith(cwd):
        return "거부: 작업 폴더 밖 경로"

    try:
        with open(full, encoding="utf-8") as f:
            return f.read()[:4000]
    except FileNotFoundError:
        return f"파일을 찾을 수 없음: {path}"


# ---- 도구 3: 현재 시각 ----
def clock() -> str:
    """현재 로컬 날짜와 시각을 돌려준다."""

    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


# ---- 실제 도구 구현 연결 ----
TOOLS_IMPL = {
    "calculator": calculator,
    "read_file": read_file,
    "clock": clock,
}


# ---- 모델에게 전달할 도구 스키마 ----
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "산술 수식을 정확하게 계산한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "계산할 산술 수식"
                    }
                },
                "required": ["expression"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "현재 작업 폴더 안에 있는 텍스트 파일의 내용을 읽는다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "읽을 파일 경로"
                    }
                },
                "required": ["path"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "clock",
            "description": (
                "현재 실제 로컬 날짜와 시각을 알려준다. "
                "사용자의 요청이 현재 날짜나 현재 시각에 의존할 때 사용한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


# ---- API 호출 + rate limit 재시도 ----
def call_model(messages, max_retries: int = 5):
    for attempt in range(max_retries):

        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )

        except RateLimitError:
            if attempt == max_retries - 1:
                raise

            wait_seconds = 10

            print(
                f"[재시도] Rate limit 발생 "
                f"({attempt + 1}/{max_retries}). "
                f"{wait_seconds}초 후 다시 시도합니다."
            )

            time.sleep(wait_seconds)


# ---- 에이전트 루프 ----
def run(goal: str, max_steps: int = 8):

    messages = [
        {
            "role": "user",
            "content": goal
        }
    ]

    for step in range(max_steps):

        print(f"[step {step + 1}] 모델 호출")

        resp = call_model(messages)

        msg = resp.choices[0].message

        assistant_message = {
            "role": "assistant",
            "content": msg.content,
        }

        if msg.tool_calls:
            assistant_message["tool_calls"] = msg.tool_calls

        messages.append(assistant_message)

        # 도구 호출이 없으면 최종 답변
        if not msg.tool_calls:
            return msg.content or ""

        # 도구 호출 실행
        for tool_call in msg.tool_calls:

            name = tool_call.function.name

            args = json.loads(
                tool_call.function.arguments
            )

            if name not in TOOLS_IMPL:
                out = f"알 수 없는 도구: {name}"

            else:
                try:
                    out = TOOLS_IMPL[name](**args)
                except Exception as e:
                    out = f"도구 실행 오류: {e}"

            print(
                f"[도구] {name}({args}) -> {out}"
            )

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(out),
            })

    return "중단: 최대 반복 초과"


# ---- 실행 예시 ----
if __name__ == "__main__":

    goal = (
        "지금 현재 날짜와 시각이 몇 시인지 알려줘. "
        "추측하지 마."
    )

    result = run(goal)

    print("\n[최종 답변]")
    print(result)