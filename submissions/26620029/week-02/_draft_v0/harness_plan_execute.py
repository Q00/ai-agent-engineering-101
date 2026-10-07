# Plan-then-Execute형: 계획을 먼저 굳히고 순서대로 실행한다
import json
from tools_shared import TOOLS, Meter, call_model

def run_plan_execute(task, max_replan=1):
    meter = Meter()
    # 1) PLAN: 전체 계획을 한 번에 요청
    plan_raw = call_model(
        [{"role": "user", "content": f"다음 태스크의 단계별 계획을 JSON 리스트로만 답하라: {task}"}],
        meter)
    plan = json.loads(plan_raw)          # 파싱 실패도 하나의 실패 모드
    replans = 0
    # 2) EXECUTE: 계획의 각 단계를 순서대로 수행
    context = []
    for step in plan:
        result = execute_step(step, context, meter)     # 도구 호출 포함
        context.append(result)
        if result.get("off_plan") and replans < max_replan:
            plan = replan(task, context, meter); replans += 1   # 유연성 상한
    return summarize(context, meter), meter, replans