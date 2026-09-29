# Week 05 보고서 — 미실행 초안

단계 3까지 인증·협상 생성·조회·행동 도구와 토큰 한도 검사를 구현하고 모델 없이 32개 검사를 통과했다. host/runner와 주입은 아직 없다. 아래 결과표에는 에이전트 실험 결과가 없다.

## 1. 설정과 재현 방법

| 항목 | 설정 |
|---|---|
| host / 모델 / temperature | Codex CLI 0.156.1, codex exec / gpt-6-luna, reasoning low / 미지정 (CLI 옵션 미노출) |
| Python / MCP SDK / 의존성 버전 | Python 3.12.4 / mcp 2.2.0 / requirements-lock.txt |
| 로그인 / 모델 접근 | 현재 CLI Not logged in; 실제 모델 접근 미검증 |
| 토큰 발급 방식 및 내용 | 보호된 POST /admin/negotiations에서 opaque random token 발급; grant는 역할·협상 ID와 server 조건의 자신의 한도. MCP resource/scope도 SDK에서 검증 |
| 서버·host·runner 실행 명령 | 서버 실행은 STAGE2.md, 전체 HTTP 검사는 STAGE3.md. host/runner는 미구현 |
| 턴 제한 / 반복 수 | 8회 host 실행, 통과한 행동을 turns로 별도 집계 / 필수 조건별 시나리오당 3회 |
| 중단 후 재개 방식 | 구현 후 기록 |
| 시나리오 커밋 | 7e8eb8a; 실험 전에 4개 시나리오 고정 |

API 키, bearer token, 관리자 토큰 값은 기록하지 않는다. 역할별 system prompt는 prompts.json, 실험 설정은 experiment.json, 측정 기준은 MEASUREMENT.md에 있다. Codex CLI에 역할 지시를 적용하는 방식과 MCP 연결은 단계 4에서 구현·검증한다.

## 2. 결과

| condition | episodes | correct | violations | attempted violations | refused calls | mean turns |
|---|---|---|---|---|---|---|
| prompt_inject | 미실행 | — | — | — | — | — |
| server_inject | 미실행 | — | — | — | — | — |

전체 에피소드 표는 results.csv의 실제 행을 대조하여 여기에 작성한다. 오류 에피소드도 남기고 오류 원인을 설명한다. 선택 조건을 실행하면 요약표에 추가한다.

## 3. FIPA-ACL과 market 비교

| 비교 항목 | 4주차 FIPA-ACL | 이번 market의 구현 및 관측 근거 |
|---|---|---|
| 호출자는 누구이며 누가 정하는가 | 확인 후 작성 | 구현 후 작성 |
| 행위는 어디에 표현되는가 | 확인 후 작성 | 구현 후 작성 |
| content는 무엇인가 | 확인 후 작성 | 구현 후 작성 |
| 한도는 누가 강제하는가 | 확인 후 작성 | 구현 후 작성 |
| 외부에서 무엇을 검증할 수 있는가 | 확인 후 작성 | 검사 결과로 작성 |
| 실제로 어떤 실패가 발생했는가 | 자신의 실행 근거 확인 | 원본 로그로 작성 |

현재 본인 디렉터리에 4주차 제출물이 없다. 강의의 개념 비교와 자신의 실제 실행 결과를 구분하고, 실행하지 않은 실패 사례를 자신의 관측으로 쓰지 않는다.

## 4. 해석

실험 후 한 문단으로 작성한다. 주입에 대한 모델의 반응, 한도 밖 시도, 실제 위반, 서버 거부를 구분하고 어떤 계층이 한도를 지켰는지 로그 파일과 줄 번호로 뒷받침한다. 거부 뒤 같은 턴에 유효한 행동이 이어진 횟수도 기록한다. server 조건의 violation=0이 강제 검사로 보장되는 값이라는 점을 설명한다.
