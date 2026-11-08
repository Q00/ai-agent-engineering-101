# 실행 가능한 복합 작업 5개

기존 출시 검토 1개와 새 복합 작업 4개다. 기본 과제의 `tasks.json`과 ID·공개 설명이 일치한다.
아래는 실행 전에 고정한 요구사항과 평가 기준이며 에이전트가 작성한 결과물이 아니다.
이 기준으로 출력 상한 제거 후 5개 작업 × 3개 조건 × 3회 실험과 별도 429 복구를 완료했다.
[최신 결과](../conditions/20260922T012131-no-token-limit-9703d2/FINAL_REPORT.md)와
[실제 산출물·품질 점검](../conditions/20260922T012131-no-token-limit-9703d2/QUALITY_REVIEW.md)을 별도로 확인한다.
최신 manifest의 이 파일 hash는 실행 소스 커밋 `d3014d2`의 버전이다. 이번 결과 링크와 출력 상한 설명은 모든 실험 종료 후 수정했으며 아래 요구사항과 평가 기준은 유지했다.
이전 `69d801c` 버전으로 실행한 [2200토큰 실험](../conditions/20260921T113158-suite-ab4e67/FINAL_REPORT.md)도 보존했다.

|ID|핵심 산출물|협업 시 연결해야 할 부분|
|---|---|---|
|release-review|수익성·기술 위험·출시 권고|두 검토 결과를 받은 후 권고 작성|
|game-design-architecture|게임 기획서, TypeScript 인터페이스, 모듈 트리, 테스트·일정|게임 규칙 ↔ 도메인/저장/렌더링 구조 ↔ 검증·예산|
|payment-redesign|원인 가설, 결제 상태 전이·코드 구조, 마이그레이션·검증 계획|PG 불확실성 ↔ 중복 제어·데이터 전환 ↔ 운영 인계|
|growth-roadmap|대안별 손익, 기능 범위, 코드·데이터 변경 개요, 로드맵|수요·수익 ↔ 기능 시간 ↔ 출시·운영 준비|
|launch-operations|운영 일정, 용량·인력안, 고객 안내·FAQ, 비상 계획|정책·제품 정보 ↔ 광고/교육/랜딩 인계 ↔ 상담 용량|

## 게임 기획과 코드 구조를 한 번에 다루는 범위

브라우저 오프라인 1인용, 6×6 보드, 최대 12턴의 로그라이크를 설계한다.
승패·전투·보상·튜토리얼 규칙을 구체화하고 `GameState`, `Command`, `Event`, `Rng`, `SaveCodec`과
디렉터리 구조·턴 처리 의사코드·저장 버전·테스트의 대응 관계를 만든다.
같은 seed와 입력의 재현, 저장 후 복원, UI와 도메인 경계가 연결돼야 한다.
개발 160시간·QA 40시간 예산과 온라인 협동을 추가할 때의 범위 충돌도 검토한다.
산출물은 설계 문서와 짧은 코드 조각이다. 현재 실행기가 실제 게임을 빌드하거나 코드를 실행하지 않는다.

## 실행

저장소 루트에서 다음을 실행한다. `plan`과 `list`는 API를 호출하지 않는다.

```bash
# 목록과 ID
submissions/26622007/.venv/bin/python submissions/26622007/week-03/extensions/peer_dag/cli.py list

# 게임 사례의 입력·제한·검사할 필드 확인
submissions/26622007/.venv/bin/python submissions/26622007/week-03/extensions/peer_dag/cli.py plan --case game-design-architecture

# 커밋된 게임 사례를 실제 에이전트로 수행
submissions/26622007/.venv/bin/python submissions/26622007/week-03/extensions/peer_dag/cli.py live --case game-design-architecture --condition baseline
```

`--case`를 생략하면 기존 `release-review`다. 다른 작업은 표의 ID로 선택한다.
새 replay 로그에는 사례 ID가 기록돼 자동 선택되며 다른 사례를 명시하면 거절한다.
기존 로그는 release-review로 해석하고 정확한 재생에는 당시 프롬프트·전송 설정·소스를 사용한다.
`demo`는 기존 출시 검토 전용 모의 데이터이므로 새 큰 작업에 사용할 수 없다.

live 전에는 선택한 case/expected, catalog, config가 커밋돼 있는지 검사한다.
`expected.json`의 정답은 실행·계획 모델에게 주지 않고 사후 facts 검사에만 사용한다.
원문은 `logs/<run-id>.jsonl`, 산출물은 `runs/<run-id>/artifacts/<case-id>.json` 및 하위 작업 경로에 저장한다.
각 하위 작업의 문서도 보존하므로 최종 요약만으로 전체 품질을 판단하지 않는다.

## 자동 검사와 정성 검토

- 자동 검사는 제공 자료에서 계산 가능한 수치와 명시된 제약의 facts만 확인한다. boolean 요구사항을 맞게 적었다고 실제 구현이 검증된 것은 아니다.
- 기본 과제의 gold 배정 검사는 책임 분야 일치이며 결과 문서의 품질 점수와 다르다.
- 큰 작업의 품질은 아래 기준을 각각 미충족/부분 충족/충족으로 검토하고 근거 산출물의 문장을 남긴다.

|대상|정성 검토 기준|
|---|---|
|공통|요구 산출물 누락, 사실·추정·미검증 구분, 수치 설명의 일치, 계획의 실제 의존성, 근거 없는 완료 주장 여부|
|게임|게임 규칙이 인터페이스에 표현되는지, 모듈 의존 순환 여부, seed/저장 재현 경로, 규칙별 테스트, 예산·일정 일치|
|결제|타임아웃·중복·순서 역전·부분 실패를 다루는지, 고유 제약과 트랜잭션 경계, 구/신버전 공존·롤백, 검증 누락|
|확장안|대안·시나리오 계산, 반증할 수요 가설, 예산 초과 처리, 기능/코드/로드맵의 일관성, 고객 집중 위험|
|운영|정책 약속의 일관성, 담당/마감/선행조건, 미승인 충원 대안, 문의 용량과 FAQ, 기술 승인 없는 출시 방지|

현재 max_depth=5, max_tasks=12, max_steps=4, max_calls=64, max_parallel=3을 유지한다.
새 4개 과제는 `require_delegate=false`여서 계획을 스스로 정한다. 더 큰 과제라고 깊이 2 이상이나 재귀 협업이 실제로 발생했다고 미리 단정하지 않는다.
현재 API 요청에는 `max_tokens`와 `max_completion_tokens`를 넣지 않는다. 기존 2200토큰 상한은 사용자가 요청하지 않은 설정이라 제거했고, [별도 규약](../conditions/SUITE_NO_TOKEN_LIMIT_PROTOCOL.md)과 실행 ID로 재실험했다. 제공업체 자체 기본 한도는 남는다.
기존 `condition_study.py`는 release-review 한 사례의 9회 반복 전용이다. 새 `suite_study.py`가 5개 전체의 45회를 순회한다.
