# 실행 전 고정한 실험 설계

사용자 요청에 따라 Codex가 구현과 실험을 수행함
아래 예상은 실행 전 설계 가설이며 학습자가 직접 진술한 예상으로 간주하지 않음

- 계산 2개, 글쓰기 2개, 코드 2개로 구성하고 gold를 A, B, C에 2개씩 배정함
- tasks.json을 구현 및 첫 API 실행보다 먼저 커밋함
- baseline → homogeneous → overconfident 순서로 조건당 3회 실행함
- 모든 실행에서 태스크 순서 1~6, contractor 응답 순서 A→B→C를 고정함
- 수업의 BID_SYSTEM, OVERCONFIDENT, ANNOUNCEMENT를 그대로 사용함
- baseline 능력은 A=`arithmetic and numeric computation`, B=`writing and editing prose`, C=`writing and debugging code`로 고정함
- homogeneous는 능력만 모두 `general problem solving`으로 바꿈
- overconfident는 baseline의 C 시스템 프롬프트 끝에 수업의 과신 지시만 추가함
- manager는 일반 Python 코드이며, contractor는 공고마다 이력 없는 LLM 호출을 1회 수행함
- gold는 낙찰 후 평가에만 사용하고 모델 입력에는 전달하지 않음
- 유효한 bid=true 중 최고 confidence를 낙찰자로 정하고 동점은 먼저 응답한 쪽에 배정함
- messages는 공고 3개 + 유효한 bid=true 개수 + 낙찰이 있으면 1개로 계산함
- 거절과 파싱 실패는 로그에 남기되 수업 규칙에 따라 입찰 메시지 수에 포함하지 않음
- JSON 형식이나 필드 검증에 실패하면 재시도·보정 없이 입찰 없음으로 처리하고 parse_fails를 셈
- 실제 태스크 수행이나 답 검증은 하지 않으므로 correct는 수행 성공률이 아닌 사전 gold와의 배정 일치 수임
- 원본 응답, 실행 설정, 사용량을 로그에 남기고 중단된 실행도 삭제하지 않음

## 실행 전 예상과 해석 범위

baseline은 능력 구분이 입찰을 거르는 신호가 될 것으로 예상함
homogeneous는 입찰 증가와 동점 발생으로 메시지 수가 늘고 A의 선순위 이점이 드러날 수 있음
overconfident는 C의 분야 밖 입찰이 늘 수 있지만, A/B가 더 높은 확신도를 내거나 동점이면 낙찰이 유지될 수 있음
3회 반복은 관찰용 소규모 비교이며 통계적 유의성이나 전체 모델로의 일반화를 주장하지 않음
동일한 모델의 역할 프롬프트는 실제로 서로 다른 능력을 가진 센서·프로그램과 구분함
결과에 맞춰 태스크, gold, 프롬프트를 변경하지 않음

## 자료

- [week-03 강의](https://wpti.dev/ai-agent-engineering-101/week-03.html)와 [제출 명세](../../../..//weeks/week-03/README.md)를 기준으로 삼음
- 참고 PR [156](https://github.com/Q00/ai-agent-engineering-101/pull/156), [155](https://github.com/Q00/ai-agent-engineering-101/pull/155), [154](https://github.com/Q00/ai-agent-engineering-101/pull/154)의 로그 근거, 동점 효과, 과신 지시 준수 여부를 검토함
- 참고 PR의 결과를 이번 실행 결과로 사용하지 않음
