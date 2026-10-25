# 복구 실행에서 발견한 facts 키 의미 충돌

`recovery-r1-growth-roadmap-baseline`은 문서 생성에 성공했지만 필수 facts 14/16만 맞췄다.
원래 429 실패를 대체하지 않는 별도 복구 기록이며, 내용 오류를 고치기 위한 추가 호출은 하지 않았다.

|산출물|s_margin|e_margin|사용한 의미|
|---|---:|---:|---|
|biz_analysis|48000|240000|고객당 공헌이익, 원|
|ops_readiness|48000|240000|고객당 공헌이익, 원|
|tech_design|16|-16|개발 예산 대비 여유 시간, 시간|
|integrate|48000|240000|고객당 공헌이익, 원|
|최종 루트|16|-16|개발 여유 시간으로 바뀜|

루트는 올바른 공헌이익을 별도 `s_margin_per_customer`/`e_margin_per_customer`에 기록하면서,
필수 키 `s_margin`/`e_margin`에는 시간 차이를 넣었다. 본문 비교표에는 공헌이익이 맞게 적혀 있다.
최종 루트의 원본 `model_reply`부터 16/-16이 기록돼 있다.
[실행 코드](../../../core.py)는 하위 산출물을 각 작업 ID 아래 `children`으로 전달하고 모델 합성 응답을 검증한다.
[출력 스키마](../../../response_formats.py)는 facts 값의 기본 타입을 제한하지만 키별 의미·단위·출처를 고정하지 않는다.
따라서 strict JSON Schema 통과와 의미 일관성은 별도 문제다.

원본 증거:

- [사업 분석](../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline/artifacts/growth-roadmap/biz_analysis.json)
- [기술 설계](../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline/artifacts/growth-roadmap/tech_design.json)
- [하위 통합](../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline/artifacts/growth-roadmap/integrate.json)
- [최종 루트](../../../runs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline/artifacts/growth-roadmap.json)
- [요청·응답·작업 추적](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline.jsonl)

다음 구현 후보는 facts의 이름·의미·단위·출처 계약을 공통으로 제공하고, 같은 키의 단위나 값이 충돌하면
합성 전에 명시적으로 감지하는 것이다. 정답을 모델 입력에 주입하는 것과 구분해야 한다.
이 실험에는 해당 변경을 적용하지 않았고, 원본 값도 수정하지 않았다.
