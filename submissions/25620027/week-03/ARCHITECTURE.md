# 도면에서 코드로 읽는 Contract Net

[아키텍처 SVG](architecture.svg) · [편집 가능한 원본](architecture.excalidraw) · [실험 전 설계](DESIGN.md)

## 먼저 이해할 실행 흐름

1. `run.py`가 config와 6개 태스크를 읽고 실험 설정·소스 해시를 고정한다.
2. `Batch.execute()`가 gold를 제외한 `RuntimeTask(id, desc)`만 `Engine`에 전달한다.
3. `Engine.execute()`가 태스크마다 같은 공고를 만든다. A→B→C의 독립적인 system/user 메시지 쌍으로 같은 모델을 한 번씩 호출한다.
4. `Protocol.submit_bid()`가 응답을 검사하고 `seal()`로 후보를 확정한다. `award()`는 최고 confidence를 선택하며 동점이면 먼저 접수한 유효 입찰을 선택한다.
5. 계약이 CLOSED가 된 뒤 `evaluate()`가 gold와 비교한다. `archive.py`가 결과 CSV를 만들고 다음 실행에서 기존 로그를 그대로 보존한다.

Manager와 Prompt Factory는 Python 코드다. 별도 LLM이 아니다. 독립 컨텍스트는 서로의 입찰이나 이전 대화를 전달하지 않는다는 뜻이며, 현재 호출은 순차적이다.

## 상자별 구현 위치

| 도면 구성 | 코드 | 책임 |
|---|---|---|
| tasks.json | `tasks.json`, `settings.load_inputs` | 태스크·gold 원본, 실행 순서·중복 ID 검사 |
| Run Config | `config.json`, `Settings` | 모델·온도·토큰·마감·호출 간격 고정 |
| Prompt Factory | `prompts.system_prompt` | 공통 지시 + 세 조건의 역할 지시 |
| Manager Controller | `Engine`, `Protocol` | 공고·수집·봉인·낙찰·종료 |
| Independent Sessions | `ChatAdapter.bid` | 현재 system/user 두 메시지만 모델 호출 |
| Announcement | `records.Announcement` | 작업·수신자·자격 설명·응답 규격·마감 |
| Bid / Response Inbox | `Contract.responses`, `Response` | 유효 입찰·불입찰·파싱/API/timeout 구분 |
| Tool Router / Phase Guard | `Protocol`의 메서드 | 역할·계약·단계·마감·중복 검사 |
| Contract Board | `Store`, SQLite contracts | 현재 상태, 버전, 응답, 승자 |
| Event Log | protocol_messages / system_events | 공식 메시지와 진단 이벤트를 분리 |
| Allocation Result | `runs/run-NNN/allocations.json` | CLOSED 계약별 배정과 원응답 |
| Offline Evaluator | `evaluate.evaluate` | 사전 gold 비교·지표 집계 |
| Run archive | `archive.py`, `batch.py` | 원본 로그·실패 행·중단 복구·설정 혼합 방지 |

도구는 외부 MCP 서버나 모델 function calling이 아니라 하네스 내부 함수다. SQLite도 로컬 파일이다. 추가 분산 서버 없이 상태·권한 경계를 명확히 한다.

## 상태와 거절

`QUEUED → BIDDING → SEALED → AWARDED / UNASSIGNED → CLOSED`

- `announce`: Manager만 QUEUED에서 실행한다. 공고 3건을 기록한다.
- `submit_bid`: Contractor만 BIDDING에서 실행한다. 마감 전 최초 종결 응답만 반영한다.
- `seal`: 응답 3건이 종결됐거나 마감일 때만 후보를 고정한다.
- `award`: Manager만 SEALED에서 실행한다. 낙찰 상태와 메시지·이벤트를 한 트랜잭션으로 저장한다. 같은 award 요청 ID의 재전달은 결과를 재사용하며 메시지를 늘리지 않는다.
- `close`: 배정 기록을 종료한다. 실제 업무 답안이 완성됐다는 뜻은 아니다.

API 오류는 불입찰이 아니다. malformed JSON도 정상 응답으로 고치지 않는다. 신원·계약·요청 ID는 실행기가 부착하므로 모델이 바꿀 수 없다. gold를 이용해 분야 밖 입찰을 거절하지 않는다.

## T01 손으로 따라가기 — 가정한 예

A가 91로 입찰하고 B가 불입찰, C가 84로 입찰하면 A 낙찰이다. 공식 메시지는 공고 3 + 유효 입찰 2 + 낙찰 1 = **6**이다. B의 응답·파싱·도구·DB 이벤트는 여기에 더하지 않는다. C가 98로 입찰하면 C 낙찰이고, 평가기가 gold=A와 비교해 오배정으로 센다. 점수 91·84·98은 설명용이며 실험 결과가 아니다.

## 검증 범위

- 단위: 엄격한 JSON/필드 파싱, 최고점·동점, 정상 불입찰, gold 투영.
- 실제 SQLite: 마감·권한·중복 검사, 메시지 분리, 낙찰 이벤트 쓰기 실패 시 전체 롤백.
- SDK/HTTP 경계: 요청 수·도구/정답 누출 여부, 401/402/429/503 처리, 자동 재시도 없음.
- 실제 CLI + 로컬 테스트 API: CSV·원본 로그 생성, 재실행 시 중복 방지, 429의 빈 지표 행, 중단 기록 보존, 설정 변경 거절.

테스트용 응답은 제출 실험 결과에 사용하지 않는다. 실제 API 실험 상태는 REPORT.md와 results.csv를 기준으로 한다.
