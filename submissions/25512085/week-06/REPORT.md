# Week 06 과제 리포트: A2A 프로토콜 기반 하네스(Codex) 구매자 및 판매자 에이전트 구현

## 1. 하네스(Harness), 모델 및 실행 명령어 (Harness & Execution Environment)

- **하네스 도구**: OpenAI Codex CLI (`codex`) v0.147.0
- **사용 모델**: `gpt-5.6-sol` (ChatGPT 플러스/팀 계정 OAuth 인증 기반)
- **실행 명령어**:
  ```bash
  codex exec --skip-git-repo-check --ephemeral --cd "<temp_dir>" -m gpt-5.6-sol -o "<output_file>" -
  ```
- **선정 배경 및 환경 구성**:
  외부 API 키 발급 및 비용 지출 없이 학생이 기존에 사용 중인 구독 계정 환경을 활용하기 위해 과제 가이드의 Option B(Codex CLI 하네스)를 채택했습니다. 
  초기 검증 과정에서 `gpt-4o`나 `gpt-6.1-sol`을 인자로 넘겼을 때 ChatGPT 계정 연동 권한 제약으로 인해 호출이 거부되었으나, 로컬 캐시인 `~/.codex/models_cache.json`을 분석하여 현재 활성화된 모델 식별자인 `gpt-5.6-sol`을 확인하고 이를 하네스 엔진으로 지정했습니다.
  또한 표준 입력(`stdin`)으로 프롬프트를 전달하고 임시 파일(`-o`)로 출력을 캡처하는 비대화형 파이프라인을 구축하여 에이전트 루프에서 안정적으로 호출되도록 구현했습니다.

---

## 2. 각 에이전트의 의사결정 로직 및 가드레일 (Decision Logic & Guardrails)

### 2.1 판매자 (Seller) 의사결정 로직
- **프롬프트 구성**:
  - 판매 대상 품목(`item`)과 해당 품목의 최저 판매 한도선(`reserve`: Lamp 100, Chair 180, Bookshelf 260)을 명시합니다.
  - 구매자의 현재 제안가(`buyer_offer`), 판매자의 직전 호가(`current_ask`), 최근 협상 히스토리를 제공합니다.
  - LLM에게 판매자 입장에서 마진을 확보하면서도 협상을 진전시킬 카운터오퍼를 숫자로 제시하거나, 구매자의 제안이 reserve 이상으로 합리적이면 `DEAL <가격>`, 더 이상 타협 불가능한 저가 공세가 이어지면 `REJECT`를 출력하도록 유도합니다.
- **응답 파싱**:
  - 응답 텍스트에서 정규식을 통해 `DEAL`, `REJECT` 키워드 또는 마지막으로 등장하는 숫자(호가)를 추출합니다.
- **Reserve 강제 위치 (Prompt vs Code)**:
  - **이중 방어(Prompt + Code Guardrail)** 구조를 적용했습니다.
  - 프롬프트 상에 `"You MUST NEVER offer or sell below the reserve"`라는 강한 제약을 부여했습니다.
  - 동시에 파이썬 코드 레벨에서 LLM이 반환한 숫자가 reserve 미만인 경우 `max(reserve, suggested)`로 강제 클램핑하며, 구매자 제시가가 reserve 미만일 때는 절대로 `TASK_STATE_COMPLETED`로 전이되지 않고 카운터오퍼 제시 또는 `TASK_STATE_REJECTED`로 종료되도록 안전장치를 마련했습니다.

### 2.2 구매자 (Buyer) 의사결정 로직
- **프롬프트 구성**:
  - 구매 품목(`item`), 최대 가용 예산(`budget`), 판매자의 현재 요구 가격(`seller_ask`), 구매자의 직전 제안가(`last_offer`)를 컨텍스트로 전달합니다.
  - 판매자 요구 가격이 예산 이내이면 즉시 해당 가격을 수락하고, 예산 초과 시 점진적으로 상향하는 카운터오퍼를 제시하거나 예산선에 도달하여 협상 불가 시 `QUIT`을 출력하도록 지시합니다.
- **응답 파싱**:
  - `QUIT` 키워드 감지 시 즉시 협상을 중단(`CancelTask` 호출)하고 루프를 빠져나옵니다. 숫자가 제시된 경우 마지막 숫자를 카운터오퍼로 파싱합니다.
- **Budget 강제 위치 (Prompt vs Code)**:
  - **이중 방어(Prompt + Code Guardrail)** 구조를 적용했습니다.
  - 프롬프트에 예산 초과 절대 금지(`"Maximum budget: {budget} (You MUST NEVER exceed {budget})"`)를 명시했습니다.
  - 파이썬 코드에서도 제안 가격을 `min(budget, suggested)`로 클램핑하여 LLM의 환각이나 실수로 인한 예산 초과 지출을 원천 차단했습니다. 또한 판매자 요구 가격이 예산 이내로 들어오는 즉시 거래를 체결하여 불필요한 결렬을 방지했습니다.

---

## 3. 실패 사례 분석 및 디버깅 과정 (Failure Log Analysis & Resolution)

과제 구현 및 테스트 과정에서 두 가지 중대한 기술적 실패가 발생했으며, 이를 로그 분석을 통해 해결했습니다.

### (1) 모델 권한 오류 (HTTP 400 Bad Request)
- **실패 증상 및 로그**:
  `codex exec -m gpt-4o` 명령으로 비대화형 실행을 테스트할 때 다음과 같은 에러가 발생했습니다:
  ```text
  HTTP 400 Bad Request: model is not supported when using Codex with a ChatGPT account
  ```
- **원인 분석**:
  Codex CLI가 API Key가 아닌 개인 ChatGPT 구독 계정 세션 토큰(`~/.codex/auth.json`)으로 동작할 때, 일반 API용 모델명(`gpt-4o`, `gpt-6.1-sol`) 호출이 차단되어 있었습니다.
- **해결 조치**:
  로컬 캐시인 `~/.codex/models_cache.json`을 검사하여 ChatGPT 계정에서 승인된 실제 모델명인 `gpt-5.6-sol`을 확인하고, 하네스 인자를 `-m gpt-5.6-sol`로 수정하여 해결했습니다.

### (2) Git Context Scan으로 인한 ReadTimeout 병목 현상
- **실패 증상 및 로그**:
  초기 트레이드 테스트 실행 중, 판매자 테스트의 마지막 단계인 `seller_holds_reserve #3`에서 다음과 같은 타임아웃 예외가 발생하며 테스트가 중단되었습니다:
  ```text
  Traceback (most recent call last):
    File "C:\Users\clair\Desktop\Projects\ai-agent-engineering-101\scripts\trade_test_week06.py", line 98, in rpc
      r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
    ...
  httpx.ReadTimeout: The read operation timed out
  ```
- **원인 분석**:
  - `seller.py`와 `buyer.py` 내부에서 `codex exec`를 실행할 때 별도의 디렉터리(`--cd`)를 지정하지 않아 현재 작업 폴더인 `submissions/25512085/week-06`에서 실행되었습니다.
  - 이로 인해 Codex CLI가 실행될 때마다 상위 Git 저장소의 커밋 이력과 프로젝트 컨텍스트 메모리(`naitedu_project`, `ai-agent-engineering-101`)를 매번 전수 인덱싱/스캔하면서, 턴당 응답 시간이 40~60초 이상으로 폭증했습니다.
  - 5~6라운드가 누적되는 `seller_holds_reserve #3` 단계에서 결국 테스트 스위트의 200초 소켓 타임아웃을 초과하게 되었습니다.
  - 이는 과제 명세서(README.md)의 주의사항인 `"Run the harness in an empty directory so it does not pick up a project's instructions"`를 간과했던 결과였습니다.
- **해결 조치**:
  - `codex exec` 호출 인자에 `--cd "{tempfile.gettempdir()}" --ephemeral`을 추가하여 빈 임시 디렉터리 환경에서 불필요한 파일 스캔 없이 단독 실행되도록 수정했습니다.
  - 서브프로세스 타임아웃을 20초로 설정하고 예외 발생 시 프로세스를 강제 종료(`p.kill()`)하는 방어 루틴을 추가했습니다.
  - 수정 후 단일 턴 응답 속도가 2~4초대로 대폭 개선되었으며, 재실행한 결과 21개 전체 테스트가 모두 정상 통과했습니다 (`21/21 passed -> PASS`).

---

## 4. A2A 프로토콜의 표준화 영역과 비표준 영역에 대한 고찰 (Architectural Reflection)

A2A v1.0(Agent-to-Agent) 프로토콜은 서로 다른 언어와 환경(예: Codex 기반 에이전트와 교재 스크립트 기반 에이전트)에서 실행되는 AI 에이전트들이 통신할 때 강력한 상호 운용성을 제공했습니다.
A2A가 표준화하여 해결해 준 핵심 영역은 다음과 같습니다:
1. **에이전트 검색(Discovery)**: `/.well-known/agent-card.json` 표준 엔드포인트를 통해 에이전트의 역량(Skills), 통신 방식(JSON-RPC 2.0), 프로토콜 버전(`1.0`)을 사전에 선언하고 발견할 수 있었습니다.
2. **태스크 수명주기(Task Lifecycle)**: `TASK_STATE_INPUT_REQUIRED`를 통해 추가 협상이 필요한 상태를 명시하고, 협상 완료 시 `TASK_STATE_COMPLETED`, 결렬 시 `TASK_STATE_REJECTED`로 상태 전이를 규격화하여 분산된 두 에이전트가 단일 세션(`taskId`)의 흐름을 정확히 추적할 수 있었습니다.
3. **아티팩트 교환(Artifact Exchange)**: 거래 성사 시 영수증 데이터를 표준 아티팩트(`receipt artifact`) 구조체에 담아 신뢰성 있게 전달할 수 있었습니다.

하지만 A2A는 전송 및 상태 머신 수준의 통신 규약일 뿐, 실제 상거래 비즈니스 도메인의 세부적인 어휘(Ontology)와 협상 프로토콜까지 정의하지는 않습니다. 따라서 이번 과제에서 판매자와 구매자는 A2A 외부에서 다음과 같은 규칙들을 암묵적으로 합의해야 했습니다:
- **가격 표현 방식**: 메시지 본문 안에서 '가장 마지막에 등장하는 숫자'를 제안 가격으로 간주한다는 규칙.
- **수락(Acceptance)의 의미**: 상대방의 요구 가격과 동일한 숫자를 보내거나, `TASK_STATE_COMPLETED`와 함께 영수증 아티팩트를 첨부하는 것이 최종 계약 체결을 의미한다는 도메인 합의.
- **퇴장(Walk-away) 방식**: 예산 초과로 합의가 불가능할 때 `CancelTask`를 호출하거나 침묵으로 응답을 중단하는 비즈니스 종료 규칙.

요컨대, A2A는 에이전트 간 "어떻게 세션을 연결하고 상태를 주고받을 것인가"에 대한 뼈대를 완벽히 규격화해주었지만, 그 통신 파이프라인 안을 흐르는 "무엇을 어떻게 거래할 것인가"에 대한 도메인 시맨틱(Domain Semantics)은 여전히 애플리케이션 계층의 명시적 프로토콜 설계가 필수적임을 체감할 수 있었습니다.
