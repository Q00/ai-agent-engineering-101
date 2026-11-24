# Contract Net — 구현 전 확정한 설계 v2.1

2026-09-16. 사용자와 검토한 3주차 아키텍처를 구현한다. 가상 행정업무 6개이며 실제 업무 자료·개인정보는 입력하지 않는다.

## 실험 계약

- tasks.json을 모델 실험 전에 커밋한다. T01/T02=A(계산), T03/T04=B(글쓰기), T05/T06=C(코드). 실행 결과에 맞춰 gold를 고치지 않는다.
- Manager는 고정 제어기다. Contractor만 LLM을 호출하며 gold 없는 현재 공고와 자기 system prompt만 전달한다.
- baseline: 분야별 역할. homogeneous: 모두 일반 문제 해결 역할. overconfident: baseline에서 C에게만 항상 입찰·confidence 95 이상 지시를 추가한다. 출력 점수를 코드로 보정하지 않는다.
- Contractor 호출은 A→B→C 순차. 최고 confidence 낙찰, 동점은 먼저 접수한 유효 입찰. 후보가 없으면 미배정.
- 태스크 순서 T01→T03→T05→T02→T04→T06. 조건은 baseline→homogeneous→overconfident를 반복해 각각 3회 실행한다. 동일 모델·온도·토큰 한도·실패 정책을 유지한다.
- official messages = 대상별 공고 3 + 유효 bid=true 수 + 낙찰 0/1. 불입찰·API 호출·오류·DB 이벤트는 포함하지 않는다.
- QUEUED→BIDDING→SEALED→AWARDED/UNASSIGNED→CLOSED. SQLite 안에 상태·protocol_messages·system_events를 저장하고 낙찰과 기록을 원자적으로 확정한다.
- 마감·역할·계약·중복·형식을 검사한다. gold로 분야 밖 입찰을 차단하지 않는다.
- JSON/필드 실패·API 오류·timeout과 정상 bid=false를 구분한다. 원응답을 보존하고 재질문·자동 재시도는 0회다. 실행기/저장소 실패는 counts가 빈 crashed row로 남긴다.
- 무료 API 한도/인증 오류는 현재 run을 실패 기록하고 전체 배치를 멈춘다. 재실행 시 기존 결과·로그를 덮어쓰지 않는다. 성공한 반복 수를 확인하며 같은 설정으로 이어간다.

## 구현과 검증 순서

1. 데이터·설계 사전 커밋.
2. 입찰 파싱·최고점/동점·gold 격리 테스트와 상태/이벤트 저장 구현.
3. OpenAI 호환 SDK 어댑터, CLI, 사후 평가, 오류/재개 테스트.
4. 설정 고정·코드 커밋 후 실제 모델 실험. 실패도 보존.
5. CSV와 로그에 근거해 REPORT.md의 네 부분 작성, 공식 구조 검사.

공식 최소 과제는 SQLite를 요구하지 않는다. 여기서는 모든 조건에 동일한 실행 기반으로 사용한다. 배정 품질은 사전 gold와의 일치이며 실제 작업 수행 정답률이 아니다.
