"""claude -p가 호출마다 붙이는 고정 토큰 오버헤드를 측정한다.

results.csv의 tokens에는 Claude Code 자체의 시스템 프롬프트와 내장 도구 정의가
호출마다 포함된다. 이 값을 알아야 REPORT.md에서 "대화에 실제로 쓴 토큰"을
tokens - iters * overhead 로 보정할 수 있다.

측정 방법: 각 하네스의 실제 시스템 프롬프트로, 대화 내용이 거의 없는 최소 프롬프트를
한 번 보낸다. 그때의 입력 토큰이 그 하네스의 호출당 하한이다.

사용법: python probe_overhead.py
"""
from tools_shared import Meter, call_claude

PROBES = [("react", "harness_react"), ("plan_exec_plan", "harness_plan_execute")]


def main():
    print(f"{'harness':22s} {'in':>9s} {'out':>6s}   note")
    for label, module in PROBES:
        try:
            mod = __import__(module)
        except ImportError as e:
            print(f"{label:22s} {'-':>9s} {'-':>6s}   import 실패: {e}")
            continue
        for attr in ("SYSTEM", "SYSTEM_PLAN", "SYSTEM_EXEC"):
            system = getattr(mod, attr, None)
            if system is None:
                continue
            meter = Meter()
            call_claude("ping", system, meter)
            c = meter.calls[0]
            print(f"{label + '.' + attr:22s} {c['in']:9d} {c['out']:6d}   "
                  f"system={len(system)}자")


if __name__ == "__main__":
    main()
