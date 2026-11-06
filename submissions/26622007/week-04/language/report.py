"""Readable report for the predeclared language and turn-policy analysis."""
from decimal import Decimal
import json


def write_report(here, rows, tests, paired, summaries, audits):
    significant = [t for t in tests if t["significant_holm"]]
    lines = ["# 한국어 협상과 통계 검증", "", "## Material Passport", "",
        "- Origin Skill: academic-research-suite / experiment-agent", "- Origin Mode: validate",
        "- Origin Date: 2026-09-22", "- Verification Status: ANALYZED",
        "- Version Label: language_validation_v1", "- Overall Confidence: CAUTION", "",
        "분석 계산과 원문 재생은 검증했다. 새로운 독립 표본에서 같은 통계 결론이 재현됐다는 뜻은 아니다.", "",
        "## 실행과 비교 범위", "",
        "DeepSeek V4.1 Flash / DeepInfra FP8, temperature=1.0, top_p=0.95, reasoning off.",
        "영어 기존 72회와 한국어 추가 72회. 언어 × 턴 정책 × 조건마다 네 시나리오를 세 번씩 실행했다.",
        "한국어 역할·형식·reader 지시와 물품명만 번역하고 가격·파서·태그·JSON 스키마는 유지했다.",
        "structured 출력은 언어 중립적인 JSON이며 이 조건의 언어 차이는 입력 지시 언어다.",
        "무제한도 180초 관측 종료를 사용한다. 중단을 no_deal로 바꾸지 않았다.",
        "[고정한 분석 계획](ANALYSIS_PLAN.md), [한국어 프롬프트](korean.py), [실행 방법](README.md).", "",
        "## 정답 및 종료 결과", "",
        "정답은 가능한 거래의 한도 내 합의 또는 불가능한 거래의 포기다. 정답 수는 관측 범위 내 확인된 값이다.",
        "censored/crashed는 궁극적 성패 미상이며 주 검정에서는 '관측 내 정답 확인 안 됨'=0으로 센다.",
        "평균 턴에 관측 중단의 현재 턴도 포함된다. 턴 제한 두 묶음은 독립 생성 대화다.", "",
        "| 언어 | 정책 | 조건 | 정답/12 | deal | no_deal | open | 관측 중단 | 실패 | 위반 | 평균 턴 | 형식 오류 | reader |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summaries:
        lines.append("| " + " | ".join(str(s[k]) for k in ("language", "turn_policy", "condition", "correct", "deal", "no_deal", "open", "censored", "crashed", "violations", "mean_turns", "format_errors", "reader_calls")) + " |")
    lines += ["", "## 통계적으로 유의한가", "",
        f"시나리오별로 층화한 양측 정확 검정을 24개 비교에 적용하고 전체에 Holm 보정했다. 보정 후 p<0.05는 **{len(significant)}개**다.",
        "아래 Δ는 앞 그룹 - 뒤 그룹 정답률 차이(%p)다. 95% 구간은 시나리오별 이항 구간을 합친 보수적 효과 구간이다.",
        "조건당 12회이지만 시나리오는 네 개뿐이며 각 시나리오당 3회다. 비유의는 동등성이나 효과 부재를 증명하지 않는다.",
        "언어별 API 실행 시점이 달라 시간 변화와 공급자 변동을 언어 효과에서 분리할 수 없다.", "",
        "| 비교 | Δ %p | 보수적 95% 구간 | 원 p | Holm p (24개) | 유의 |", "|---|---:|---|---:|---:|---|"]
    for t in tests:
        lo, hi = t["difference_ci95_conservative_pp"]
        lines.append(f"| {t['comparison']} | {t['difference_pp']:.1f} | [{lo:.1f}, {hi:.1f}] | {t['p_raw']:.6f} | {t['p_holm']:.6f} | {'예' if t['significant_holm'] else '아니오'} |")
    lines += ["", "이 p값은 네 고정 시나리오 내 호출이 독립적이고 교환 가능하다는 가정에 의존한다.",
        "동일 run의 네 에피소드는 이력을 공유하지 않지만 공급자 상태에 따른 상관을 완전히 배제할 수 없다.",
        "표본은 무작위로 뽑은 일반 협상 시나리오가 아니므로 다른 물품·가격·모델에 대한 일반화는 별도 검증 대상이다.", "",
        "## 같은 대화의 첫 8턴과 최종 관측", "",
        "첫 8턴은 저장된 무제한 응답을 원래 파서로 재생했다. 아래 두 열은 별도 실행 대화가 아니다.",
        "특히 영어 free는 같은 대화에서 deal 4→4로 그대로다. 기존 8턴 실행의 deal 6과의 차이를 턴 제한 제거의 결과로 볼 수 없다.",
        "온도는 양쪽 모두 1.0이다. 새 표본 생성에 따른 변동은 온도 변경 효과가 아니다.", "",
        "| 언어 | 조건 | deal 8턴→최종 | 정답 8턴→최종 | 추가 정답 | 퇴행 | 정확 McNemar p | Holm p (6개) |",
        "|---|---|---|---|---:|---:|---:|---:|"]
    for p in paired:
        lines.append(f"| {p['language']} | {p['condition']} | {p['before_deal']}→{p['after_deal']} | {p['before_success']}→{p['after_success']} | {p['gain']} | {p['loss']} | {p['p_raw']:.6f} | {p['p_holm']:.6f} |")
    lines += ["", "성공하면 즉시 종료하는 구조상 정답 퇴행은 발생할 수 없다. 따라서 이 McNemar p는 중첩된 관측 시점의 보조 기술 통계이며 무작위 처리의 인과 검정으로 해석하지 않는다.", "",
        "## 미종료 민감도", "", "| 언어 | 정책 | 조건 | 확인 정답 | 궁극적 성패 미상 | 미상 전부 정답일 때 상한 |", "|---|---|---|---:|---:|---:|"]
    for s in summaries:
        if s["unknown_final"]:
            lines.append(f"| {s['language']} | {s['turn_policy']} | {s['condition']} | {s['correct']} | {s['unknown_final']} | {s['possible_correct_upper']} |")
    lines += ["", "이 상한은 미상 표본의 최종 성패 범위만 보여 준다. 주 지표인 관측 내 확인 정답을 바꾸거나 원본 CSV를 재라벨링하지 않는다.", "",
        "## 통계 오류 점검", "", "Coverage: 11/11 checked", "", "| 항목 | 판단 | 확인 내용 |", "|---|---|---|"]
    checks = [
        ("Simpson 역설", "층화 적용", "모든 비교에 동일한 네 시나리오와 층당 3회, 층별 성공 수 공개. 물품별 차이는 전체 우열로 숨기지 않음."),
        ("생태학적 오류", "일반화 제한", "분석 단위는 에피소드이며 일반 협상 전체나 사용자 개인의 능력으로 추론하지 않음."),
        ("Berkson 선택 편향", "주의", "네 강의 예제의 편의 표본이며 모집단 대표성 없음."),
        ("충돌변수 통제", "회피", "종료 여부나 오류 수를 공변량으로 통제하지 않음."),
        ("기저율 무시", "공개", "각 묶음에서 거래 가능 시나리오 3개, 불가능 1개로 같은 비율."),
        ("평균 회귀", "주의", "기존 영어 결과를 본 뒤 확장한 탐색 실험이며 재실행 차이를 개선이라고 단정하지 않음."),
        ("생존자 편향", "회피", "중단·실패를 전체 분모에 보존하고 확인 정답과 최종 성패 미상을 구분."),
        ("다중 탐색", "보정", "24개 비교 전체 p와 Holm p를 공개, 유리한 비교만 선택하지 않음."),
        ("분석 경로 선택", "기록", "영어는 이미 관찰됨. 한국어 수집 전 표본 수·지표·검정 고정, 유의해질 때까지 추가 실행 없음."),
        ("상관과 인과 혼동", "주의", "시간상 언어 무작위 배정이 아니며 같은 대화의 중첩 관측은 처치 비교가 아님."),
        ("역인과", "구조상 제한", "프롬프트 언어는 응답 생성 전에 결정됨. 시간·제공업체 혼입 가능성은 남음."),
    ]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in checks]
    calls = sum(sum(audits[k]["requests"].values()) for k in ("ko_8", "ko_none"))
    cost = sum(Decimal(v) for k in ("ko_8", "ko_none") for v in audits[k]["cost_usd"].values())
    lines += ["", "## 재현과 근거", "",
        f"한국어 API 요청 {calls}개, usage.cost 합계 USD {cost}. 설정·response_format·전체 이력·파싱·결과 재생을 검증했다.",
        "[실제 대화 사례](DIALOGUES.md), [72회 원본 결과](runs/korean-deepseek-20260922/results.csv), [집계](runs/korean-deepseek-20260922/summary.csv), [전체 검정](runs/korean-deepseek-20260922/tests.csv), [감사](runs/korean-deepseek-20260922/audit.json).",
        "통계량은 고정 자료에서 결정적으로 재계산할 수 있다. 실제 모델 대화의 동일 결과 재현을 보장하지 않는다.",
        "방법: [SciPy 정확 검정](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html), [Holm 보정](https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html), [정확 McNemar](https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html)."]
    (here / "REPORT.md").write_text("\n".join(lines) + "\n")
