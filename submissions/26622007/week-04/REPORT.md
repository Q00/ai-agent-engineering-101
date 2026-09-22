# Week 04 HTML 실습 실행 기록

이 문서는 실제 실행 결과와 검증 기록이다. FIPA 비교와 제출용 최종 해석은 별도 작성 대상이다.

실험 `html-deepseek-20260922`, 사전 소스 커밋 `071692aa434541459e26226673e6249cb8191893`.
DeepSeek V4.1 Flash / DeepInfra FP8, temperature=1.0, top_p=0.95, reasoning off, MAX_TURNS=8.
자전거·탁상등·교재·키보드 네 시나리오, 세 조건, 조건별 세 번 반복: 총 36개 에피소드.

## 조건별 결과

| 조건 | 정답/12 | deal | no_deal | open | 중단 | 위반 | 평균 턴 | 형식 오류 | reader 호출 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| free | 7 | 6 | 4 | 2 | 0 | 1 | 4.333 | 12 | 52 |
| tagged | 5 | 4 | 1 | 7 | 0 | 0 | 7.583 | 24 | 32 |
| structured | 10 | 8 | 2 | 2 | 0 | 0 | 6.75 | 0 | 0 |

## 재현과 검증

[실행 명령과 HTML 대응](lab/README.md), [공식 코드 조각](lab/reference/lecture-code.txt).
직렬화 요청 313개에서 모델·온도·top_p·reasoning·제공업체·response_format과 전체 대화 이력을 검사했다.
HTTP/전송 오류 기록: `{'429': 5}`. API usage.cost 합계 USD `0.0107783872`.
구조화 응답 165개 중 로컬 형식 검증 실패 0개. 형식 검증은 의미 판정의 정확성을 보장하지 않는다.
HTML은 완성된 starter가 아니며 일부 문구와 구현이 생략돼 있다. 공개 코드 문구와 참조 사례를 그대로 사용하고 초기화·파서·기록·재개를 구현했다.
사용자의 기존 규칙으로 reader 및 structured 요청에는 strict JSON Schema를 적용했다. 이 API 제약은 HTML의 Claude CLI 참조 실행에 명시되지 않아 형식 오류율을 직접 비교할 수 없다.
3개 독립 run을 동시에 진행하되 에피소드 내부의 발언 순서와 대화 이력은 분리했다. 이전 pilot 결과는 이 표에 포함하지 않는다.

## 에피소드 전체

| run | 조건 | 시나리오 | 가능 | 결과 | 가격 | 정답 | 위반 | 턴 | 형식 오류 | reader | note |
|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| html-deepseek-20260922-free-01 | free | 1 | 1 | no_deal |  | 0 | 0 | 7 | 0 | 7 |  |
| html-deepseek-20260922-free-01 | free | 2 | 1 | deal | 45 | 1 | 0 | 3 | 1 | 3 |  |
| html-deepseek-20260922-free-01 | free | 3 | 1 | deal | 40 | 1 | 0 | 3 | 0 | 3 |  |
| html-deepseek-20260922-free-01 | free | 4 | 0 | open |  | 0 | 0 | 8 | 8 | 8 |  |
| html-deepseek-20260922-free-02 | free | 1 | 1 | no_deal |  | 0 | 0 | 2 | 0 | 2 |  |
| html-deepseek-20260922-free-02 | free | 2 | 1 | deal | 35 | 1 | 0 | 5 | 0 | 5 |  |
| html-deepseek-20260922-free-02 | free | 3 | 1 | deal | 24 | 0 | 1 | 4 | 1 | 4 |  |
| html-deepseek-20260922-free-02 | free | 4 | 0 | no_deal |  | 1 | 0 | 2 | 0 | 2 |  |
| html-deepseek-20260922-free-03 | free | 1 | 1 | deal | 120 | 1 | 0 | 5 | 2 | 5 |  |
| html-deepseek-20260922-free-03 | free | 2 | 1 | open |  | 0 | 0 | 8 | 0 | 8 |  |
| html-deepseek-20260922-free-03 | free | 3 | 1 | deal | 40 | 1 | 0 | 3 | 0 | 3 |  |
| html-deepseek-20260922-free-03 | free | 4 | 0 | no_deal |  | 1 | 0 | 2 | 0 | 2 |  |
| html-deepseek-20260922-structured-01 | structured | 1 | 1 | deal | 135 | 1 | 0 | 6 | 0 | 0 |  |
| html-deepseek-20260922-structured-01 | structured | 2 | 1 | deal | 45 | 1 | 0 | 5 | 0 | 0 |  |
| html-deepseek-20260922-structured-01 | structured | 3 | 1 | deal | 40 | 1 | 0 | 8 | 0 | 0 |  |
| html-deepseek-20260922-structured-01 | structured | 4 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 0 |  |
| html-deepseek-20260922-structured-02 | structured | 1 | 1 | deal | 145 | 1 | 0 | 8 | 0 | 0 |  |
| html-deepseek-20260922-structured-02 | structured | 2 | 1 | deal | 40 | 1 | 0 | 7 | 0 | 0 |  |
| html-deepseek-20260922-structured-02 | structured | 3 | 1 | deal | 40 | 1 | 0 | 7 | 0 | 0 |  |
| html-deepseek-20260922-structured-02 | structured | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| html-deepseek-20260922-structured-03 | structured | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 0 |  |
| html-deepseek-20260922-structured-03 | structured | 2 | 1 | deal | 40 | 1 | 0 | 5 | 0 | 0 |  |
| html-deepseek-20260922-structured-03 | structured | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| html-deepseek-20260922-structured-03 | structured | 4 | 0 | no_deal |  | 1 | 0 | 8 | 0 | 0 |  |
| html-deepseek-20260922-tagged-01 | tagged | 1 | 1 | deal | 125 | 1 | 0 | 7 | 1 | 3 |  |
| html-deepseek-20260922-tagged-01 | tagged | 2 | 1 | deal | 45 | 1 | 0 | 7 | 1 | 3 |  |
| html-deepseek-20260922-tagged-01 | tagged | 3 | 1 | open |  | 0 | 0 | 8 | 8 | 0 |  |
| html-deepseek-20260922-tagged-01 | tagged | 4 | 0 | no_deal |  | 1 | 0 | 7 | 1 | 2 |  |
| html-deepseek-20260922-tagged-02 | tagged | 1 | 1 | open |  | 0 | 0 | 8 | 6 | 0 |  |
| html-deepseek-20260922-tagged-02 | tagged | 2 | 1 | deal | 40 | 1 | 0 | 8 | 1 | 4 |  |
| html-deepseek-20260922-tagged-02 | tagged | 3 | 1 | open |  | 0 | 0 | 8 | 1 | 3 |  |
| html-deepseek-20260922-tagged-02 | tagged | 4 | 0 | open |  | 0 | 0 | 8 | 1 | 4 |  |
| html-deepseek-20260922-tagged-03 | tagged | 1 | 1 | open |  | 0 | 0 | 8 | 1 | 3 |  |
| html-deepseek-20260922-tagged-03 | tagged | 2 | 1 | deal | 40 | 1 | 0 | 6 | 1 | 2 |  |
| html-deepseek-20260922-tagged-03 | tagged | 3 | 1 | open |  | 0 | 0 | 8 | 1 | 4 |  |
| html-deepseek-20260922-tagged-03 | tagged | 4 | 0 | open |  | 0 | 0 | 8 | 1 | 4 |  |

각 run의 원문과 모든 판독은 `logs/<run>.txt`, API 원본은 `logs/<run>.jsonl`에 있다.
HTML 참조 사례와 대조한 [실제 관찰](lab/runs/html-deepseek-20260922/OBSERVATIONS.md).
요청/이력 검증 및 상세 집계: [audit.json](lab/runs/html-deepseek-20260922/audit.json).
