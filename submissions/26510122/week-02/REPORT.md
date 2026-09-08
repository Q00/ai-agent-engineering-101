# Harness A/B — 로그 오류 시간대 분석

## 1. 변형 정의

같은 `app.log`(원본 유지), TASK.md, `read_file`·`count_pattern`, OpenRouter `nvidia/nemotron-3.5-lightning:free`로 비교했다. 정답은 **14:00(ERROR 6건)**이다. 주 비교는 **5~10행**: 양쪽 모두 응답 최대 2048토큰·실행당 180초, 각 3회다. 1~4행은 제한 없는 파일럿으로 별도 보존했다. TASK.md는 실행 전 9e00637에 커밋했다. 실행 환경과 명령은 README.md 및 environment.json에 있다.

| 축 | ReAct | Plan-then-Execute |
|---|---|---|
| 문맥 관리 | 하나의 대화에 전체 실행 기록 누적 | 계획·실행 대화 분리, 실행 대화에는 계획과 결과 누적 |
| 도구 단위 | 공통 도구를 다음 판단에 따라 호출 | 같은 도구를 계획 단계 안에서 호출 |
| 종료 조건 | 도구 호출 없는 답변 또는 모델 호출 8회 | 계획 실행 후 최종 답변; 초기 계획 파싱 실패 시 즉시 종료 |
| 오류 복구 | 도구 오류를 다음 관찰로 전달 | 도구 오류 전달 + OFF_PLAN이면 최대 1회 재계획; 초기 파싱 실패 재시도 없음 |
| 사람 개입 | 승인 대상 집합이 비어 개입 0 | 승인 절차 없음; 본 실험 개입 0 |

## 2. 측정

아래는 results.csv의 전체 실행이다(note는 가독성을 위해 요약). iters는 모델 응답 횟수이며 도구 호출 수가 아니다. 4행의 개입 1은 도구 승인이 아니라 외부 수동 중단이다.

| run | harness | success | tokens | iters | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | O | 3589 | 2 | 0 | pilot |
| 2 | react | O | 3912 | 2 | 0 | pilot |
| 3 | react | O | 3975 | 2 | 0 | pilot |
| 4 | plan_exec | X | 미상 | 미상 | 1 | pilot 수동 중단·부분 기록 |
| 5 | react | O | 3927 | 2 | 0 | bounded |
| 6 | react | O | 3922 | 2 | 0 | bounded |
| 7 | react | O | 3707 | 2 | 0 | bounded |
| 8 | plan_exec | X | 2138 | 1 | 0 | bounded; plan parse failed |
| 9 | plan_exec | X | 2138 | 1 | 0 | bounded; plan parse failed |
| 10 | plan_exec | X | 2138 | 1 | 0 | bounded; plan parse failed |

동일 조건 5~10행 평균: **ReAct 성공 3/3, 3852토큰, 2회, 개입 0 / Plan-then-Execute 성공 0/3, 2138토큰, 1회, 개입 0**.

## 3. 해석

<!-- 학생이 직접 작성할 부분. 아직 작성하지 않았음. -->
