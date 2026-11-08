# 재귀 Worker 구조의 세 조건 비교

## 설정과 실험 범위

[사전 고정한 실험 규약](../FINAL_PROTOCOL.md)에 따라 동일 출시 검토 사례를 조건별 3회 실행했다.
모든 Worker가 계획·평가·실행·재위임·통합을 수행하며 max_depth=5, 장기 메모리 없음이다.
기본 Contract Net의 배정 정확도 실험을 대체하지 않는 확장이다.

실행 소스 커밋: `5aa1a55b5afd08464e51e81cf409136b5b562fff`. 모델: `deepseek/deepseek-v4.1-flash`, temperature=0.
실험 간 동시 실행 수: 1. 실험 사이 대기: 15초.
제공업체 설정: `{"allow_fallbacks": true, "max_price": {"completion": 1.2, "prompt": 0.3}, "only": ["fireworks"], "require_parameters": true}`.
각 실험 내부 max_parallel=3은 유지한다. 시간은 공급자 지연과 부하의 영향을 받는다.

## 결과

|조건|전체 검사 통과|필드 검사|평균 호출|평균 초|보고 비용 합계|실제 최대 깊이|C 배정 비율|
|---|---:|---:|---:|---:|---:|---|---:|
|baseline|3/3|36/36|20.0|60.9|$0.04162|[1, 1, 1]|33.3%|
|homogeneous|2/3|24/36|18.0|49.5|$0.03570|[1, 1, 1]|27.3%|
|overconfident|3/3|36/36|20.0|74.9|$0.04306|[1, 1, 1]|83.3%|

## 반복 일치와 조작 확인

|조건|산출물 있는 실행|서로 다른 핵심 결과|C의 95 이상 입찰/유효 제안|C 루트 선정|거절된 제안|
|---|---:|---:|---:|---:|---:|
|baseline|3/3|1|3/12|1/3|2|
|homogeneous|2/3|1|2/10|0/3|2|
|overconfident|3/3|1|12/12|3/3|0|

핵심 결과 일치는 12개 지정 facts 값으로 계산하고 문장 표현은 비교하지 않는다. 같은 오답도 일치하므로 정확도와 함께 해석한다.
산출물이 없는 실행은 일치 비교에서 제외하되 전체 검사 통과율의 분모 3에는 포함한다.
필드 검사는 산출물이 없는 실행을 0/12로 센다. C 배정 비율은 루트를 포함한 모든 배정 중 C의 비율이며 오배정률이 아니다.

## 실행별 증거

|조건/회차|상태|검사|실제 깊이|원본 로그|산출물|
|---|---|---:|---:|---|---|
|baseline/1|succeeded|12/12|1|[trace](../../logs/20260921T093041-84990d-1-baseline.jsonl)|[result](../../runs/20260921T093041-84990d-1-baseline/result.json)|
|homogeneous/1|succeeded|12/12|1|[trace](../../logs/20260921T093041-84990d-1-homogeneous.jsonl)|[result](../../runs/20260921T093041-84990d-1-homogeneous/result.json)|
|overconfident/1|succeeded|12/12|1|[trace](../../logs/20260921T093041-84990d-1-overconfident.jsonl)|[result](../../runs/20260921T093041-84990d-1-overconfident/result.json)|
|homogeneous/2|succeeded|12/12|1|[trace](../../logs/20260921T093041-84990d-2-homogeneous.jsonl)|[result](../../runs/20260921T093041-84990d-2-homogeneous/result.json)|
|overconfident/2|succeeded|12/12|1|[trace](../../logs/20260921T093041-84990d-2-overconfident.jsonl)|[result](../../runs/20260921T093041-84990d-2-overconfident/result.json)|
|baseline/2|succeeded|12/12|1|[trace](../../logs/20260921T093041-84990d-2-baseline.jsonl)|[result](../../runs/20260921T093041-84990d-2-baseline/result.json)|
|overconfident/3|succeeded|12/12|1|[trace](../../logs/20260921T093041-84990d-3-overconfident.jsonl)|[result](../../runs/20260921T093041-84990d-3-overconfident/result.json)|
|baseline/3|succeeded|12/12|1|[trace](../../logs/20260921T093041-84990d-3-baseline.jsonl)|[result](../../runs/20260921T093041-84990d-3-baseline/result.json)|
|homogeneous/3|failed|0/12|1|[trace](../../logs/20260921T093041-84990d-3-homogeneous.jsonl)|[result](../../runs/20260921T093041-84990d-3-homogeneous/result.json)|

## 검증과 한계

- all_nine_attempts_present: True
- three_per_condition: True
- same_controls: True
- same_source_snapshot: True
- recorded_conditions_match: True
- all_recorded_prompts_match: True
- all_recorded_response_formats_match: True

- 입력은 한 종류의 합성 출시 검토 사례이며 조건별 3회뿐이다. 일반적인 우수성이나 완전한 결정성을 주장하지 않는다.
- 하위 작업은 모델이 생성하므로 조건마다 작업 구성과 배정 기회가 달라질 수 있다. 하위 작업별 gold는 사후에 붙이지 않는다.
- 선택 점수는 LLM 평가이며 동점에서는 자기 확신도를 사용한다. 높은 확신도가 정확한 능력 확률이라는 보장은 없다.
- 초과 자신감 지시는 원래의 과장 금지 지시 뒤에 추가했다. 모델이 따랐는지는 실제 C의 유효 제안으로 측정한다.
- 계약 메시지는 propose 호출 시작 + propose 응답 수 + award 수다. 평가·실행·통합 호출은 별도 LLM 호출 수에 포함한다.
- 비용은 반환된 응답의 보고값 합계이며 응답이 없는 요청의 청구액은 알 수 없다. 시간 제한 종료도 실패로 보존한다.
- 이 확장은 강의 기본 results.csv와 필수 9개 로그에 합산하지 않는다.
