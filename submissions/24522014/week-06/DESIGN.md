# 내 하네스로 만드는 buyer와 가게 — STEP 1 설계

상태: 설계 완료, 과제 구현 및 거래 검증은 아직 시작하지 않음.
요구 사항의 기준: `weeks/week-06/README.md`.
기존 `a2a-lab/`은 강의 실습 기록으로 보존한다.

## 1. 사용할 하네스

Claude Code + Haiku와 Codex + Luna를 선택 가능한 하네스로 둔다.
Claude Code + Haiku는 STEP 6에서 실제 응답을 확인했다.
Codex + Luna는 설계에 추가했으며 실제 모델 호출은 STEP 2에서 검증한다.
각 비대화형 명령에 Python이 표준 입력으로 판단 요청을 전달한다.

Claude Code + Haiku:

```text
claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence
```

Codex + Luna (Windows):

```text
codex.cmd exec --skip-git-repo-check --model gpt-6-luna --sandbox read-only --ephemeral -
```

Luna의 모델 ID는 `gpt-6-luna`로 명시한다. Windows에서는 설치된 npm의
`codex.cmd`를 사용해 PowerShell의 `codex.ps1` 실행 정책 오류를 피한다.
macOS/Linux에서는 실행 파일 이름을 `codex`로 사용한다.
`--ephemeral`은 세션 파일 보존을 끄고, `--sandbox read-only`는 모델이 실행하는
셸 명령의 쓰기를 제한한다. 도구 자체를 비활성화하는 옵션은 아니다.
Codex CLI의 설치된 도움말에서 위 옵션을 확인했다. 현재 계정의 Luna 접근과
실제 JSON 응답 성공 여부는 아직 검증하지 않았다.

STEP 2에서는 하네스 종류와 모델을 설정으로 선택하고, 공통 JSON 응답을
반환하는 호출 인터페이스를 만든다. seller와 buyer에 각각 선택할 수 있게
설계하되, 처음에는 같은 하네스로 검증하고 이후 서로 다른 조합을 시도한다.
한쪽 호출이 실패해도 다른 하네스로 자동 대체하지 않고 오류를 기록한다.
로그와 `agent.json`의 `harness`에는 양쪽의 실제 하네스·모델을 명시한다.
조합을 바꾼 뒤에는 공식 trade test를 다시 실행해 결과를 보존한다.

공식 참고 문서:

- 모델 ID: https://developers.openai.com/api/docs/models
- 비대화형 실행: https://learn.chatgpt.com/docs/non-interactive-mode

각 명령의 Python 호출 검증은 STEP 2에서 한다. STEP 6의 서버는
메시지마다 작업을 끝내므로 여기서는 Python이 거래 이력을 관리하고
판단할 때마다 이력을 하네스에 전달한다. 하네스는 매번 새 빈 임시 폴더를
작업 디렉터리로 사용하며 임시 폴더도 자신의 제출 디렉터리 안에 만든다.
로그인된 구독을 이용하고 키나 인증 파일은 제출하지 않는다.

## 2. 파일 구성 계획

다음 파일은 이후 단계에서 하나씩 만든다. 아래 목록은 구현 계획이다.

| 경로 | 역할 | 만드는 단계 |
|---|---|---|
| `harness.py` | subprocess 호출, JSON 응답 해석·검증, 원본 로그 저장 | STEP 2 |
| `seller.py` | A2A 서버, 거래 상태 관리, seller 판단 요청 | STEP 3–5 |
| `buyer.py` | 인자 처리, 카드 조회·A2A 요청, buyer 판단 요청 | STEP 6–7 |
| `prompts/seller.txt` | 가격·수락·거절 판단 지침 | STEP 3–4 |
| `prompts/buyer.txt` | 제안·철수 판단 지침 | STEP 7 |
| `agent.json` | 하네스·모델 이름과 테스트용 실행 명령 | STEP 8 |
| `requirements.txt` | 과제 실행 의존성 | STEP 2부터 기록 |
| `logs/` | 하네스 입출력, 오류, 공식 테스트 출력 | 각 실행 시 |
| `test-report.json` | 공식 trade test가 생성한 결과 | STEP 9 |
| `REPORT.md` | 실제 구현 선택, 실패·수정 근거, A2A의 역할 | 진행 중 기록, STEP 10 마무리 |

과제의 실행 기준 디렉터리는 `submissions/24522014/week-06/`이다.
기존 `.venv`를 사용해 시작할 수 있으나 실행 경로는 STEP 8에서 명확히
등록한다. 독립 실행에 필요한 의존성과 Windows 설정도 문서화한다.

## 3. 하네스와 Python의 역할

| 하네스가 결정 | Python이 담당 |
|---|---|
| seller의 첫 가격과 역제안 가격 | Agent Card와 JSON-RPC 1.0 요청·응답 |
| seller의 수락·거절 | taskId와 거래 이력, 상태 전이 |
| buyer의 모든 가격 제안과 철수 | 헤더·URL·인자 처리, 로그·제한 시간 |
| 주어진 이력을 보고 다음 행동 선택 | JSON 형식과 하한가·예산 제약 검증 |

가격을 정하는 고정 공식이나 규칙 기반 대체 판단은 넣지 않는다.
하한가와 예산은 프롬프트에 제공하고 Python에서도 검사한다.
잘못된 판단은 임의로 가격을 보정하거나 자동 수락하지 않는다.
검증 오류를 하네스에 전달해 최대 1회 재요청하고, 다시 실패하면
원본 응답과 오류를 남겨 실행 실패로 처리한다. 이를 정상적인 거절이나
구매 철수로 위장하지 않는다. 세부 오류 처리는 구현 단계에서 확인한다.

## 4. 하네스 응답 형식

응답은 설명이나 Markdown 없이 JSON 객체 하나로 받는다.
`reason`은 로그용 설명이며 A2A 가격 메시지에 그대로 섞지 않는다.
아래 예시는 형식 설명용이며 실제 실행 결과나 가격 전략이 아니다.

Seller:

```json
{"action": "ask", "price": 130, "reason": "Opening offer"}
```

- `ask`: 최초 제안 또는 역제안. `price`는 하한가 이상의 양의 정수.
- `accept`: 현재 구매자 제안을 수락. `price`는 현재 제안과 같고 하한가 이상.
- `reject`: 거래 거절. `price`는 `null`.
- 최초의 정상 품목 요청에는 `ask`를 요구한다. 구매자 제안이 없는 상태의
  `accept`는 유효하지 않다.

Buyer:

```json
{"action": "offer", "price": 110, "reason": "Offer within budget"}
```

- `offer`: 예산 이하의 양의 정수를 제안.
- `walk_away`: 거래 중단. `price`는 `null`.
- 판매가를 받아들이는 경우에도 하네스가 그 금액을 `offer`로 결정한다.
  A2A로는 숫자 금액을 보내며 별도의 `accept` 문자열을 보내지 않는다.

입력에는 역할, 품목, 자기 쪽 제한, 현재 제안, 이전 메시지와 남은 실행
시간을 넣는다. buyer에게 seller의 비공개 하한가를 주지 않는다.
정확한 프롬프트는 해당 구현 단계에서 작성하고 실제 응답을 보며 개선한다.

## 5. A2A 거래 규칙

Seller의 Agent Card는 `/.well-known/agent-card.json`에 제공한다.
첫 인터페이스는 `JSONRPC`, 프로토콜 버전은 `1.0`이며 실행 포트의 URL을 쓴다.
판매 품목과 하한가는 lamp 100, chair 180, bookshelf 260이다.

```text
품목 요청 → 하네스 첫 가격 → INPUT_REQUIRED
    같은 taskId로 구매자 제안
        → 하네스 역제안 → INPUT_REQUIRED
        → 하네스 수락   → 영수증 artifact + COMPLETED
        → 하네스 거절   → REJECTED
    구매자 취소 → CANCELED
```

이는 상태 이름을 줄여 쓴 것이다. API에서는 `TASK_STATE_INPUT_REQUIRED`
등을 사용한다. 완료·거절·취소된 task에 다시 메시지를 보내면 오류로 응답한다.
현재 상태, 품목, 이전 제안과 대화 이력은 taskId별로 따로 관리한다.

- 협상 중 seller 메시지는 `lamp: asking 130`처럼 **마지막 숫자가 제시 가격**이
  되도록 Python이 구성한다. 하네스의 자유로운 설명 속 숫자는 붙이지 않는다.
- 수락 시 receipt의 data Part에 `seller`, `item`, 숫자 `price`를 넣는다.
- buyer는 카드를 읽은 뒤 카드의 JSON-RPC 주소를 사용한다.
- buyer의 요청에는 `A2A-Version: 1.0`을 넣고 후속 메시지에는 같은 taskId를 쓴다.
- 초기 품목 요청 이후의 모든 금액 제안·철수는 하네스가 결정한다.
- 예산을 넘는 구매를 보내지 않으며 종료 상태에서는 추가 구매 요청을 보내지 않는다.
- buyer는 전체 300초 안에 끝나도록 누적 시간과 하네스 호출 시간을 제한한다.
  제한 시간 초과는 정상 거래 성공으로 기록하지 않는다.

## 6. 이후 검증할 시나리오

| 검증 | 기대 결과 |
|---|---|
| lamp, 구매자 예산 120 | 100 이상 120 이하에서 거래 완료 |
| bookshelf, 구매자 예산 230 | 하한가 260 미만의 제시·판매가 없음 |
| buyer에 lamp 예산 120 | 공식 테스트 가게에서 예산 안에 정확히 한 번 구매 |
| buyer에 bookshelf 예산 230 | 예산 초과 없이 구매하지 않고 종료 |
| 종료된 seller task에 메시지 | 오류 |
| buyer 프로토콜 | 버전 헤더, 동일 taskId로 이어 보내기 |

공식 trade test의 판단 테스트는 각각 3회 실행되며 전부 통과해야 한다.
결과 파일은 직접 꾸미지 않고 테스트가 생성한 것을 사용한다.
실패 로그는 편집하지 않으며 각 구현·수정 단위마다 커밋한다.

## 7. STEP 1에서 직접 이해할 내용

1. 하네스의 `ask`와 `accept`는 각각 어떤 A2A 상태로 연결되는가?
2. 가격이 예산 또는 하한가를 어겼을 때 왜 Python이 임의로 바꾸지 않는가?
3. taskId가 달라지면 왜 이전 거래를 이어갈 수 없는가?
4. 가격 판단은 하네스가 해도 Python이 대화 이력을 저장해야 하는 이유는 무엇인가?

STEP 1은 문서 설계만 수행했다. 모델 호출·서버 실행·공식 테스트는 하지 않았다.
다음은 STEP 2의 Python 하네스 호출과 JSON 응답 확인이다.
