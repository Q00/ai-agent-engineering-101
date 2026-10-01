
import os
import ast
import operator
import json
from datetime import datetime
from openai import OpenAI


# =========================================================
# 도구 1: 계산기
# =========================================================

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg
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
        _ev(
            ast.parse(expression, mode="eval").body
        )
    )


# =========================================================
# 도구 2: 파일 읽기
# =========================================================

def read_file(path: str) -> str:
    """작업 폴더의 텍스트 파일 내용을 읽는다."""

    full = os.path.abspath(path)
    cwd = os.path.abspath(os.getcwd())

    if os.path.commonpath([full, cwd]) != cwd:
        return "거부: 작업 폴더 밖 경로"

    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# =========================================================
# 도구 3: 현재 시각
# =========================================================

def clock() -> str:
    """현재 날짜와 시각을 반환한다."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# =========================================================
# 실제 Python 함수 연결
# =========================================================

TOOLS_IMPL = {
    "calculator": calculator,
    "read_file": read_file,
    "clock": clock
}


# =========================================================
# 모델에게 보여줄 도구 설명
# =========================================================

TOOLS = [

    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "산술 수식을 계산한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string"
                    }
                },
                "required": ["expression"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "작업 폴더의 텍스트 파일을 읽는다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string"
                    }
                },
                "required": ["path"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "clock",
            "description": "현재 날짜와 시각이 필요할 때 사용한다.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]


# =========================================================
# Agent
# =========================================================

def run(goal: str, max_steps: int = 8):

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"]
    )

    messages = [
        {
            "role": "user",
            "content": goal
        }
    ]


    # 이 루프가 Agent를 만든다
    for step in range(max_steps):

        print(f"\n--- Step {step + 1} ---")

        response = client.chat.completions.create(
            model="openrouter/free",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto"
        )

        message = response.choices[0].message
        messages.append(message)


        # 도구 호출이 없으면 최종 답변
        if not message.tool_calls:
            return message.content


        # 모델이 선택한 도구 실행
        for tool_call in message.tool_calls:

            tool_name = tool_call.function.name

            arguments = json.loads(
                tool_call.function.arguments
            )

            print(
                f"[도구 요청] {tool_name}({arguments})"
            )

            result = TOOLS_IMPL[tool_name](**arguments)

            print(
                f"[도구 결과] {result}"
            )

            # 실행 결과를 다시 모델에게 전달
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result)
                }
            )


    return "중단: 최대 반복 횟수 초과"


# =========================================================
# 실행
# =========================================================

if __name__ == "__main__":

    answer = run(
        "notes.txt를 읽고 숫자들의 합을 계산한 뒤, 현재 날짜와 시각도 함께 알려줘."
    )

    print("\n최종 답:")
    print(answer)
