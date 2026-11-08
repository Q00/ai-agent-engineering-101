# 복합 작업 5개 최종 실험 사전 규약

실행 전 코드·입력·정답·이 문서를 커밋한다. 이전 실험의 성공/실패를 대체하지 않는 독립 batch다.

- cases/catalog.json의 5개 작업 × baseline/homogeneous/overconfident × 3회 = 45회. 486개 facts 검사.
- 회차별 작업 순서를 한 칸씩 회전하고 조건 순서도 BHO/HOB/OBH로 회전한다. 난수로 순서를 바꾸지 않는다.
- 외부 실험은 하나씩 실행하고 사이에 15초 대기한다. 각 실험 내부 max_parallel=3은 유지한다.
- requester=A, max_depth=5, max_tasks=12, max_steps=4, max_calls=64. 장기 메모리와 외부 웹 도구는 사용하지 않는다.
- 기존 config.json의 model/provider/temperature/max_tokens 및 모든 프롬프트를 고정한다. 각 단계의 strict response_format과 로컬 검증을 유지한다.
- HTTP 429만 기존 6회 시도 및 Retry-After/누적 대기 300초 정책을 적용한다. 기타 재시도는 기존 2회 정책이다.
- 실행당 600초 상한. 실행 실패나 오답을 성공으로 바꿀 때까지 재실행하지 않는다. 이후 슬롯도 동일하게 실행한다.
- 원본 JSONL·콘솔·attempt·result·하위 산출물을 보존한다. 중간 종료는 없는 결과를 0점으로 계산하고 trace 불완전함을 명시한다.
- 실행 전마다 소스/입력 hash를 검사한다. 실행 중 코드·정답을 변경하면 batch를 멈추고 별도 실험으로 분리한다.

## 평가

자동 통과는 해당 사례의 필수 scalar facts 전부가 맞고 루트가 succeeded인 경우다. 결과가 없는 실행도 분모에 남긴다.
조건별 전체 결과와 사례별 3회 결과를 모두 보여준다. 동일 사실 벡터와 정확도를 함께 보고, 같은 오답의 반복을 성공으로 해석하지 않는다.
전체 artifact, 루트 plan, award map의 hash도 별도로 남긴다. 문자열·작업 ID가 달라져도 hash는 달라지므로 의미상 차이와 동일시하지 않는다.

병렬성은 execute/synthesize call_start~call_end 구간이 실제로 겹쳤는지 측정한다. propose의 동시 호출만으로 병렬 작업이라고 부르지 않는다.
동일 Worker 중첩, 실제 깊이, 과신 C의 95 이상 유효 입찰·루트/전체 배정 비율을 별도로 측정한다.
max_depth=5는 상한이며 깊은 호출을 강제로 만들지 않는다. release-review만 기존 위임 강제 조건이고 새 4개 작업은 자율 결정이다.
API 요청에 기록된 messages/팀 명단/response_format/provider와 실제 HTTP 응답의 JSON Schema를 검사한다.
HTTP 이벤트는 호출 종료 시 버퍼에서 기록되므로 HTTP 로그 시각으로 요청 중첩을 추정하지 않는다.
비용은 결과에 반환된 usage 비용 합계로, 응답 없는 요청 비용은 알 수 없다. 강제 종료의 누락된 HTTP 이벤트/비용은 추정하지 않는다.

정성 검토는 cases/README.md의 고정된 기준을 사용한다. 모든 성공한 루트 산출물을 검토하고 필요 시 해당 하위 산출물을 대조한다.
미충족/부분 충족/충족과 구체적인 산출물 문장을 남긴다. 자동 facts 통과를 문서 전체 품질이나 게임/결제 코드 실행 성공으로 부르지 않는다.
외부 사실 검증·실제 빌드·테스트를 수행한 실험이 아니다. 결함이 발견돼도 원본을 수정하지 않고 후속 제안으로 남긴다.

## 범위

이 실험은 peer DAG의 작업 수행·산출물 품질 실험이다. 고정 manager가 있는 기본 강의 과제의 results.csv와 배정 정확도 실험에는 합산하지 않는다.
3회 반복은 작은 표본이며 완전한 결정성이나 보편적인 조건 우열을 입증하지 못한다.

실행: 저장소 루트에서 `submissions/26622007/.venv/bin/python -u submissions/26622007/week-03/extensions/peer_dag/suite_study.py run`.
