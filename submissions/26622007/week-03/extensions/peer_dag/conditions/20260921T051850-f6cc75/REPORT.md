# 재귀 Worker 구조의 세 조건 비교

## 설정과 실험 범위

[사전 고정한 실험 규약](../PROTOCOL.md)에 따라 동일 출시 검토 사례를 조건별 3회 실행했다.
모든 Worker가 계획·평가·실행·재위임·통합을 수행하며 max_depth=5, 장기 메모리 없음이다.
기본 Contract Net의 배정 정확도 실험을 대체하지 않는 확장이다.

실행 소스 커밋: `3f0ce0f0f3edfa3dfec3a438535e448281d92554`. 모델: `deepseek/deepseek-v4.1-flash`, temperature=0.
조건별 실행을 블록마다 동시에 시작했다. 제공업체 자동 라우팅을 사용하므로 시간은 공급자 지연과 공유 부하의 영향을 받는다.

## 결과

|조건|전체 검사 통과|필드 검사|평균 호출|평균 초|보고 비용 합계|실제 최대 깊이|C 배정 비율|
|---|---:|---:|---:|---:|---:|---|---:|
|baseline|3/3|36/36|20.0|201.3|$0.03451|[1, 1, 1]|33.3%|
|homogeneous|3/3|36/36|20.0|319.3|$0.04125|[1, 1, 1]|25.0%|
|overconfident|2/3|24/36|18.0|259.6|$0.03274|[1, 1, 1]|81.8%|

## 반복 일치와 조작 확인

|조건|산출물 있는 실행|서로 다른 핵심 결과|C의 95 이상 입찰/유효 제안|C 루트 선정|거절된 제안|
|---|---:|---:|---:|---:|---:|
|baseline|3/3|1|2/11|0/3|2|
|homogeneous|3/3|1|3/11|0/3|2|
|overconfident|2/3|1|11/11|3/3|3|

핵심 결과 일치는 12개 지정 facts 값으로 계산하고 문장 표현은 비교하지 않는다. 같은 오답도 일치하므로 정확도와 함께 해석한다.
산출물이 없는 실행은 일치 비교에서 제외하되 전체 검사 통과율의 분모 3에는 포함한다.
필드 검사는 산출물이 없는 실행을 0/12로 센다. C 배정 비율은 루트를 포함한 모든 배정 중 C의 비율이며 오배정률이 아니다.

## 실행 로그 해석 — 실행 후 작성

### 결과는 일치했지만 배정은 달라졌다

성공한 8개 실행의 12개 핵심 수치와 판단은 조건을 가로질러 모두 동일했다.
반면 C의 배정은 baseline 4/12, homogeneous 3/12, overconfident 9/11이었다.
과신 조건의 분모가 11인 이유는 세 번째 실행의 실패로 마지막 하위 작업이 배정되지 않았기 때문이다.
이는 결과가 생성된 경우의 수치 일치와, 어떤 Worker가 수행하는지의 일치가 서로 다른 지표임을 보여준다.
한 종류의 제공 자료 계산 사례에서 관찰한 결과이며 다른 문제로의 일반화나 구조 개선 효과를 입증하지 않는다.

### 확신도를 가린 평가 뒤에도 동점 선정에서 과신이 작동했다

과신 조건 세 실행의 루트 선정은 모두 같은 패턴이었다.
계획 평가 합계는 A/B/C 모두 6점이었고 확신도는 A=92, B=92, C=97이었다.
따라서 점수 다음에 confidence를 비교하는 코드가 C를 골랐다.
각 원본의 event seq=30에서 확인할 수 있다:
[1회차](../../logs/20260921T051850-f6cc75-1-overconfident.jsonl),
[2회차](../../logs/20260921T051850-f6cc75-2-overconfident.jsonl),
[3회차](../../logs/20260921T051850-f6cc75-3-overconfident.jsonl).

확신도를 평가 입력에서 제외하는 것만으로 과신의 영향을 제거하지 못했다.
다만 모든 C 배정을 이 동점 규칙의 결과로 설명할 수는 없다.
1회차 수익성 작업에서는 다른 후보 두 개가 형식 오류로 거절되어 C만 남았고,
같은 회차 출시 권고에서는 B의 평가 합계 6점이 C의 5점보다 높아 C의 더 높은 확신도에도 B가 선정됐다.

### 실패는 결과 스키마 위반에서 시작됐다

과신 3회차의 C는 기술 검토 결과에서 `unverified_tests`를 배열로 반환했다.
현재 facts 계약은 문자열·숫자·boolean만 허용하므로 런타임이 이를 거절했다.
[원본 로그](../../logs/20260921T051850-f6cc75-3-overconfident.jsonl)의 seq=96이 해당 모델 응답,
seq=99가 기술 검토 실패, seq=108이 루트 실패다.
성공한 수익성 결과는 보존됐지만 기술 결과를 요구하는 출시 권고는 blocked가 됐다.

실패 원인은 형식 위반으로 확인됐으며 과신 지시가 그 형식 오류를 유발했다는 인과관계는 이 세 번의 관찰만으로 확정할 수 없다.
오류를 조용히 고치거나 성공 실행으로 교체하지 않았다.
과신 조건의 평균 호출 18회가 다른 조건의 20회보다 적은 것도 이 실패로 후속 호출이 생략된 영향이며 효율 개선으로 해석하지 않는다.

### 검증 범위

전체 모델 호출과 HTTP 요청은 각각 174회였다. 기록된 174개 요청의 시스템·사용자 메시지가
각 조건의 메시지 생성 규칙과 일치하고 장기 메모리 입력이 없음을 검사했다.
실행별 의존성·Worker 동시 사용·자원 충돌·종료 상태 감사는 실패 실행을 포함해 9/9 통과했다.
감사 통과는 실패 전파까지 규칙대로 처리했다는 뜻이며 과제 결과 성공과 구분한다.
소스·입력·설정의 통제 조건 검사 6개가 모두 통과했다.

응답에 보고된 비용은 합계 약 $0.10851이고 반환된 응답의 비용 누락은 0건이었다.
개발 회귀 테스트는 55개 통과했다:
[재귀·조건·집계 22개](../../logs/20260921-conditions-dev-peer.log),
[웹 조사 회귀 17개](../../logs/20260921-conditions-dev-research.log),
[기본 하네스 16개](../../logs/20260921-conditions-dev-base.log).
강의 제출 검사는 [기록](../../logs/20260921-conditions-week03-check.log)처럼
기본 homogeneous/overconfident 반복 실행과 기본 로그 부족으로 여전히 미통과다.

## 실행별 증거

|조건/회차|상태|검사|실제 깊이|원본 로그|산출물|
|---|---|---:|---:|---|---|
|baseline/1|succeeded|12/12|1|[trace](../../logs/20260921T051850-f6cc75-1-baseline.jsonl)|[result](../../runs/20260921T051850-f6cc75-1-baseline/result.json)|
|homogeneous/1|succeeded|12/12|1|[trace](../../logs/20260921T051850-f6cc75-1-homogeneous.jsonl)|[result](../../runs/20260921T051850-f6cc75-1-homogeneous/result.json)|
|overconfident/1|succeeded|12/12|1|[trace](../../logs/20260921T051850-f6cc75-1-overconfident.jsonl)|[result](../../runs/20260921T051850-f6cc75-1-overconfident/result.json)|
|homogeneous/2|succeeded|12/12|1|[trace](../../logs/20260921T051850-f6cc75-2-homogeneous.jsonl)|[result](../../runs/20260921T051850-f6cc75-2-homogeneous/result.json)|
|overconfident/2|succeeded|12/12|1|[trace](../../logs/20260921T051850-f6cc75-2-overconfident.jsonl)|[result](../../runs/20260921T051850-f6cc75-2-overconfident/result.json)|
|baseline/2|succeeded|12/12|1|[trace](../../logs/20260921T051850-f6cc75-2-baseline.jsonl)|[result](../../runs/20260921T051850-f6cc75-2-baseline/result.json)|
|overconfident/3|failed|0/12|1|[trace](../../logs/20260921T051850-f6cc75-3-overconfident.jsonl)|[result](../../runs/20260921T051850-f6cc75-3-overconfident/result.json)|
|baseline/3|succeeded|12/12|1|[trace](../../logs/20260921T051850-f6cc75-3-baseline.jsonl)|[result](../../runs/20260921T051850-f6cc75-3-baseline/result.json)|
|homogeneous/3|succeeded|12/12|1|[trace](../../logs/20260921T051850-f6cc75-3-homogeneous.jsonl)|[result](../../runs/20260921T051850-f6cc75-3-homogeneous/result.json)|

## 검증과 한계

- all_nine_attempts_present: True
- three_per_condition: True
- same_controls: True
- same_source_snapshot: True
- recorded_conditions_match: True
- all_recorded_prompts_match: True

- 입력은 한 종류의 합성 출시 검토 사례이며 조건별 3회뿐이다. 일반적인 우수성이나 완전한 결정성을 주장하지 않는다.
- 하위 작업은 모델이 생성하므로 조건마다 작업 구성과 배정 기회가 달라질 수 있다. 하위 작업별 gold는 사후에 붙이지 않는다.
- 선택 점수는 LLM 평가이며 동점에서는 자기 확신도를 사용한다. 높은 확신도가 정확한 능력 확률이라는 보장은 없다.
- 초과 자신감 지시는 원래의 과장 금지 지시 뒤에 추가했다. 모델이 따랐는지는 실제 C의 유효 제안으로 측정한다.
- 계약 메시지는 propose 호출 시작 + propose 응답 수 + award 수다. 평가·실행·통합 호출은 별도 LLM 호출 수에 포함한다.
- 비용은 반환된 응답의 보고값 합계이며 응답이 없는 요청의 청구액은 알 수 없다. 시간 제한 종료도 실패로 보존한다.
- 이 확장은 강의 기본 results.csv와 필수 9개 로그에 합산하지 않는다.
