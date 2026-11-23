"""Week 02 — Plan-then-Execute형 하네스.

먼저 전체 계획을 한 번에 세우고(Plan), 그 단계를 순서대로 실행한다(Execute).
다섯 요소가 코드 어느 줄에 들어가는지 [요소N]으로 표시했다.

이 하네스가 정한 값. ReAct와 다른 곳은 요소 1, 3, 4다.

  [요소1] 컨텍스트 관리 - 역할을 둘로 쪼갠다.
          계획자는 태스크 설명과 도구 목록만 본다. 파일 내용을 한 번도 보지 못한다.
          실행자는 단계마다 새 호출이고, 계획 전문 + 현재 단계 + 이전 단계들의
          결과만 받는다. 이전 단계의 Thought는 넘겨주지 않는다.
          ReAct가 전문을 통째로 이어붙이는 것과 정반대 지점이다.
  [요소2] 도구 granularity - tools_shared에 고정. ReAct와 똑같다. 통제 변수.
  [요소3] 종료 조건 - 계획을 다 소진하면 끝난다. 중간 단계에서 답이 나와도
          멈추지 않는다. 이것이 Plan-then-Execute의 원래 동작이고, ReAct의
          finish 호출과 대조된다. 여기에 단계당 도구 호출 상한을 겹친다.
  [요소4] 에러 복구 - 두 층이다. 단계 안에서는 ReAct와 같이 에러가 Observation이
          된다. 단계 자체를 진행할 수 없으면 OFF_PLAN을 내고 계획자에게
          올라가 남은 계획을 다시 짠다. 이 재계획은 max_replan회까지다.
  [요소5] 인간 개입 지점 - ReAct와 같은 훅. 읽기 전용 도구뿐이라 지금은 0이다.
"""
import json
import re
import sys

from tools_shared import TOOL_DOC, Meter, call_claude, parse_step, run_tool

SYSTEM_PLAN = """너는 계획을 짜는 사람이다. 도구를 직접 쓸 수 없고, 파일 내용도 볼 수 없다.

주어진 태스크를 푸는 단계를 JSON 리스트 한 줄로만 답하라.
각 단계는 짧은 한국어 또는 영어 문장이다. 설명, 코드 블록, 다른 말은 쓰지 마라.

예: ["app.log를 읽는다", "시간대별 ERROR 수를 센다", "최다 시간대를 고른다"]"""

SYSTEM_EXEC = f"""너는 계획의 한 단계를 실행하는 사람이다. 계획을 짜지는 않는다.

도구를 직접 실행할 수는 없다. 네가 Action을 텍스트로 쓰면 하네스가 실행해서
Observation을 돌려준다. 파일을 스스로 읽으려 하지 마라.

응답은 항상 정확히 두 줄이다.

Thought: 이 단계에서 무엇을 할지 한 줄로
Action: {{"tool": "<이름>", "args": {{...}}}}

쓸 수 있는 도구:
{TOOL_DOC}

이 단계를 계획대로 진행할 수 없으면 Action 대신 다음 한 줄을 쓰라.
OFF_PLAN: 왜 안 되는지

이 단계에서 할 일을 마쳤으면 다음 한 줄을 쓰라.
DONE: 이 단계의 결과를 한 줄로"""

# [요소5] 실행 전에 사람의 승인이 필요한 도구. ReAct와 같은 집합을 쓴다.
IRREVERSIBLE = set()


def ask_human(action) -> bool:
    answer = input(f"approve {action.tool}({action.args})? [y/N] ").strip().lower()
    return answer == "y"


def parse_plan(text: str):
    """JSON 리스트 한 줄을 단계 리스트로 바꾼다. 실패하면 None.

    계획 파싱 실패는 숨기지 않는다. Plan-then-Execute만 가진 실패 모드이고,
    ReAct에는 대응물이 없다.
    """
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\[.*\]", text, flags=re.S)      # 앞뒤에 군말이 붙은 경우
    if m:
        text = m.group(0)
    try:
        plan = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(plan, list) and plan and all(isinstance(s, str) for s in plan):
        return plan
    return None


def make_plan(task: str, meter: Meter, extra: str = "") -> "list[str] | None":
    """[요소1] 계획자 호출. 프롬프트에 파일 내용이 절대 들어가지 않는다."""
    prompt = (f"Task: {task}\n"
              f"쓸 수 있는 도구:\n{TOOL_DOC}\n")
    if extra:
        prompt += f"\n{extra}\n"
    prompt += "\n단계를 JSON 리스트 한 줄로만 답하라."
    return parse_plan(call_claude(prompt, SYSTEM_PLAN, meter))


def run_step(plan, i, results, meter, log, max_tool_rounds):
    """[요소1] 실행자 호출. 단계마다 새 컨텍스트다.

    실행자가 보는 것: 계획 전문, 지금 단계 번호, 이전 단계들의 결과.
    보지 못하는 것: 이전 단계의 Thought, 계획자의 판단 근거.
    돌려주는 값은 (결과 문자열, off_plan 여부).
    """
    done_so_far = "\n".join(f"  {n + 1}. {plan[n]} -> {r}"
                            for n, r in enumerate(results)) or "  (없음)"
    obs_log = []

    for _ in range(max_tool_rounds):            # [요소3] 단계당 도구 호출 상한
        prompt = (f"계획 전체:\n"
                  + "\n".join(f"  {n + 1}. {s}" for n, s in enumerate(plan))
                  + f"\n\n이전 단계들의 결과:\n{done_so_far}\n"
                  + f"\n지금 실행할 단계 {i + 1}: {plan[i]}\n")
        if obs_log:
            prompt += "\n이 단계에서 지금까지 얻은 것:\n" + "\n".join(obs_log) + "\n"
        prompt += "\nThought와 Action을 쓰라. 단계를 마쳤으면 DONE을, 못 하겠으면 OFF_PLAN을 쓰라."

        reply = call_claude(prompt, SYSTEM_EXEC, meter)

        m = re.search(r"^\s*DONE:\s*(.+)$", reply, flags=re.M)
        if m:
            log(f"  [done] {m.group(1).strip()[:160]}")
            return m.group(1).strip(), False
        m = re.search(r"^\s*OFF_PLAN:\s*(.+)$", reply, flags=re.M)
        if m:
            log(f"  [off_plan] {m.group(1).strip()[:160]}")
            return m.group(1).strip(), True

        parsed = parse_step(reply)
        if parsed.thought:
            log(f"  Thought: {parsed.thought}")

        # [요소4] 1층: 파싱 실패도 Observation으로 되돌린다
        if parsed.action is None:
            log(f"  [parse] {parsed.error}")
            obs_log.append(f"Observation: {parsed.error}")
            continue

        action = parsed.action
        if action.tool == "finish":       # 이 하네스에 finish는 없다
            obs_log.append("Observation: error: 이 하네스에는 finish가 없다. "
                           "단계를 마쳤으면 DONE을 쓰라.")
            continue

        if action.tool in IRREVERSIBLE and not ask_human(action):
            meter.interventions += 1      # [요소5] 개입 지점
            obs = "denied: 사람이 승인하지 않음"
        else:
            obs = run_tool(action)        # [요소2] granularity는 tools_shared에

        log(f"  [tool] {action.tool}({action.args}) -> "
            f"{obs[:200].replace(chr(10), ' | ')}")
        obs_log.append(
            f"Action: {json.dumps({'tool': action.tool, 'args': action.args}, ensure_ascii=False)}"
            f"\nObservation: {obs}")

    # 상한을 넘겼다. 단계를 끝내지 못한 것이므로 OFF_PLAN으로 올린다.
    return "단계가 도구 호출 상한을 넘겼다", True


def run_plan_execute(task: str, max_replan: int = 1,
                     max_tool_rounds: int = 3, log=print):
    meter = Meter()

    # 1) PLAN: 전체 계획을 한 번에. 도구도 파일도 보지 못한 상태에서 짠다.
    plan = make_plan(task, meter)
    if plan is None:                            # 계획 파싱 실패도 하나의 실패 모드
        log("[plan] JSON 리스트로 읽을 수 없었다")
        return "plan parse failed", meter, 0
    log(f"[plan] {plan}")

    # 2) EXECUTE: 단계를 순서대로. [요소3] 계획을 다 소진하면 끝난다.
    results = []
    replans = 0
    i = 0
    while i < len(plan):
        log(f"[step {i + 1}/{len(plan)}] {plan[i]}")
        result, off_plan = run_step(plan, i, results, meter, log, max_tool_rounds)

        # [요소4] 2층: 단계가 막히면 계획자에게 올라가 남은 계획을 다시 짠다
        if off_plan and replans < max_replan:
            replans += 1                        # 유연성 상한
            done = "\n".join(f"  {n + 1}. {plan[n]} -> {r}"
                             for n, r in enumerate(results)) or "  (없음)"
            new_steps = make_plan(
                task, meter,
                f"이미 끝낸 단계:\n{done}\n"
                f"단계 {i + 1}({plan[i]})이 막혔다: {result}\n"
                f"남은 단계만 다시 짜라.")
            if new_steps is None:
                log("[replan] JSON 리스트로 읽을 수 없었다")
                break
            plan = plan[:i] + new_steps
            log(f"[replan] {plan}")
            continue

        results.append(result)
        i += 1

    # 계획을 소진했다. 마지막에 답을 물어본다.
    answer = ask_final(task, plan, results, meter)
    log(f"[answer] {answer}")
    return answer, meter, replans


def ask_final(task: str, plan, results, meter: Meter) -> str:
    """[요소3] 계획 소진 후 최종 답을 한 번 묻는다. ReAct의 finish와 달리
    모델이 이 시점을 고르지 않는다. 하네스가 정한다."""
    done = "\n".join(f"  {n + 1}. {plan[n]} -> {r}"
                     for n, r in enumerate(results)) or "  (없음)"
    prompt = (f"Task: {task}\n\n계획 실행 결과:\n{done}\n\n"
              f"위 결과만 근거로 최종 답을 한 줄로 쓰라. 'Answer: '로 시작하라.")
    reply = call_claude(prompt, "너는 결과를 종합해 답을 내는 사람이다. "
                                "한 줄로만 답하라.", meter)
    m = re.search(r"^\s*Answer:\s*(.+)$", reply, flags=re.M)
    return m.group(1).strip() if m else reply.strip()


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else \
        "In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form."
    answer, m, replans = run_plan_execute(task)
    print(answer)
    print(f"tokens={m.tokens} iters={m.iters} interventions={m.interventions} replans={replans}")
