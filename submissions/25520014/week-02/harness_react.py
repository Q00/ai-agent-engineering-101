"""Week 02 — ReAct형 하네스.

매 스텝 Thought → Action → Observation을 돌고, 다음에 무엇을 할지 매번 다시 판단한다.
다섯 요소가 코드 어느 줄에 들어가는지 [요소N]으로 표시했다.

이 하네스가 정한 값:
  [요소1] 컨텍스트 관리 - 매 호출마다 지금까지의 전문을 다시 보낸다. claude -p는
          호출 사이에 기억이 없으므로, 무엇을 기억시킬지가 전부 이쪽 결정이다.
  [요소2] 도구 granularity - tools_shared에 고정. 두 하네스가 공유한다.
  [요소3] 종료 조건 - 모델이 finish(answer)를 명시적으로 불러야 끝난다.
          "도구를 안 부르면 끝"이 아니다. 여기에 max_steps 상한을 겹친다.
  [요소4] 에러 복구 - 도구 에러와 Action 파싱 실패를 모두 Observation으로 되돌려
          모델이 다음 스텝에서 고치게 한다. 하네스는 멈추지 않는다.
  [요소5] 인간 개입 지점 - IRREVERSIBLE에 든 도구는 실행 전에 승인을 받는다.
          지금 도구는 모두 읽기 전용이라 이 집합은 비어 있고 interventions는 0이다.
"""
import json
import sys

from tools_shared import TOOL_DOC, Meter, call_claude, parse_step, run_tool

SYSTEM = f"""너는 도구를 써서 태스크를 푸는 에이전트다.

도구를 직접 실행할 수는 없다. 네가 Action을 텍스트로 쓰면 하네스가 실행해서
Observation을 돌려준다. 파일을 스스로 읽으려 하지 말고 반드시 Action을 써라.

응답은 항상 정확히 두 줄이다. 다른 말은 쓰지 마라.

Thought: 지금 아는 것과 다음에 할 일을 한 줄로
Action: {{"tool": "<이름>", "args": {{...}}}}

쓸 수 있는 도구:
{TOOL_DOC}

Observation은 네가 쓰지 않는다. 하네스가 준다.
답을 알았으면 finish를 불러라. 예: Action: {{"tool": "finish", "args": {{"answer": "14:00"}}}}"""

# [요소5] 실행 전에 사람의 승인이 필요한 도구.
# 지금 도구는 읽기 전용이라 비어 있다. 파일을 쓰거나 지우는 도구를 넣으면
# 그 이름을 여기에 올린다.
IRREVERSIBLE = set()


def ask_human(action) -> bool:
    answer = input(f"approve {action.tool}({action.args})? [y/N] ").strip().lower()
    return answer == "y"


def run_react(task: str, max_steps: int = 8, log=print):
    meter = Meter()

    # [요소1] 컨텍스트: 이 리스트가 모델의 기억 전부다. 매 호출마다 통째로 보낸다.
    transcript = [f"Task: {task}"]

    for step in range(max_steps):                # [요소3] 종료 조건: 반복 상한
        prompt = "\n".join(transcript) + "\n\n다음 Thought와 Action을 쓰라."
        reply = call_claude(prompt, SYSTEM, meter)
        parsed = parse_step(reply)

        if parsed.thought:
            log(f"[step {step + 1}] Thought: {parsed.thought}")

        # [요소4] 파싱 실패도 멈출 이유가 아니다. 그대로 Observation으로 돌려준다.
        if parsed.action is None:
            log(f"  [parse] {parsed.error} | raw={reply.strip()[:160]!r}")
            transcript += [f"Thought: {parsed.thought}",
                           f"(형식 오류) {reply.strip()[:200]}",
                           f"Observation: {parsed.error}"]
            continue

        action = parsed.action

        # [요소3] 모델이 끝났다고 선언하는 유일한 방법
        if action.tool == "finish":
            answer = str(action.args.get("answer", "")).strip()
            log(f"  [finish] {answer}")
            return answer, meter

        # [요소5] 돌이킬 수 없는 도구 앞에서 멈추고 승인을 구한다
        if action.tool in IRREVERSIBLE and not ask_human(action):
            meter.interventions += 1
            obs = "denied: 사람이 승인하지 않음"
        else:
            obs = run_tool(action)               # [요소2] granularity는 tools_shared에

        log(f"  [tool] {action.tool}({action.args}) -> "
            f"{obs[:200].replace(chr(10), ' | ')}")
        transcript += [f"Thought: {parsed.thought}",
                       f"Action: {json.dumps({'tool': action.tool, 'args': action.args}, ensure_ascii=False)}",
                       f"Observation: {obs}"]

    return "MAX_STEPS reached: incomplete", meter   # 상한에서 강제 종료


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m = run_react(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions}")
