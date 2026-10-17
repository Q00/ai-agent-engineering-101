# A2A 실습 실행 기록

## 시도 1: echo 카드 검사에서 중단

- 로그: `logs/lab/20261017T034425.053443Z/`
- CLI: 공식 a2a 0.3.0. Python 3.12.14, a2a-sdk 1.2.2, uvicorn 0.54.0.
- 실제 카드: `name=Echo Agent; interface={'url': 'http://127.0.0.1:8080', 'protocolBinding': 'HTTP+JSON', 'protocolVersion': '1.0'}`
- 실패: `AssertionError: echo_card: ... 'protocolBinding': 'HTTP+JSON' ...`
- 원인: 실습 실행 도구가 모든 서버의 binding을 JSONRPC라고 가정했다. 공식 CLI echo 서버의 기본값은 HTTP+JSON(REST)이고, 과제의 판매자는 JSONRPC를 요구한다. 서로 다른 검사 조건이 필요하다.
- 다음 수정: echo는 HTTP+JSON, Python hello·판매자는 JSONRPC인지 각각 검사한다. 서버 예제 자체는 변경하지 않는다.

## 시도 2: echo의 빈 skill 목록

- 로그: `logs/lab/20261017T034450.408524Z/`
- binding 조건을 고친 뒤에도 카드 검사 실패. 실제 응답에는 `"skills": []`가 있다.
- 원인: 판매자 과제의 필수 skill 목록 조건까지 echo에 적용했다. CLI가 기본 생성한 echo 카드의 skill 목록은 비어 있다.
- 다음 수정: echo는 이름·지원 binding·버전을 확인하고, hello와 판매자는 skill 목록도 확인한다. 판매자 과제의 조건을 완화하지 않는다.

## 시도 3: 프로토콜 실습 통과

- 로그: `logs/lab/20261017T034522.256182Z/`
- `lab-summary.json`의 `passed`는 `true`.
- echo: HTTP+JSON 1.0 카드 조회, completed와 `hello A2A` artifact 확인.
- hello: JSONRPC 1.0 카드 조회, completed와 `Hello! You said: hi there` 확인.
- 판매자: JSONRPC 1.0 카드와 skill 확인.
- 버전 헤더 없는 요청: `-32009`, `VERSION_NOT_SUPPORTED` 확인.
- lamp: 동일 taskId로 `asking 130` → `80 is too low. Counteroffer: 105.` → completed. 영수증: `{'item': 'lamp', 'price': 105, 'seller': 'Lamp Shop'}`.
- task 조회: history 5개. 종료 후 추가 메시지: `UNSUPPORTED_OPERATION`, CLI exit code 1.
- sofa: rejected. chair에 100을 3회 제안: input-required, input-required, rejected.
- bookshelf: CancelTask 후 canceled.
- 실행 도구가 시작한 서버는 각 시나리오가 끝나면 종료한다.

## 시도 4: Claude 하네스 실행 권한 오류

- A2A 로그: `logs/lab/20261017T034736.022356Z/`.
- 실제 하네스 명령의 직접 실행 로그: `logs/lab/claude-direct-20261017-034759-567.log`.
- A2A 카드 조회는 성공했지만 task는 `TASK_STATE_FAILED`, `exit status 1`로 끝났다.
- 직접 실행한 실제 오류: `Your organization has disabled Claude subscription access for Claude Code · Use an Anthropic API key instead, or ask your admin to enable access`.
- API 키를 새로 만들거나 요청하지 않는다. 기존 ChatGPT 로그인을 쓰는 Codex CLI로 동일한 단일 응답 실습을 시도한다.
- 참고: 제한된 셸에서는 Codex 상태가 `Not logged in`으로 보였으나, 권한을 허용한 실행에서 `Logged in using ChatGPT`와 exit code 0을 확인했다. 인증 파일을 읽거나 복사하지 않았다.
- 공식 설명: https://learn.chatgpt.com/docs/non-interactive-mode (`codex exec`는 최종 응답을 stdout에 출력하고, `--ephemeral`은 세션 파일 저장을 생략한다).

## 시도 5: Codex CLI 기본 모델의 계정 접근 오류

- 로그: `logs/lab/20261017T035000.123587Z/`.
- Codex CLI 0.156.1, 기존 ChatGPT 로그인, 설정된 모델 `gpt-6.1-sol`로 실행.
- A2A 카드는 조회됐지만 task는 `TASK_STATE_FAILED`로 종료됐다.
- 실제 오류: `The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.`
- 요청받은 기본 모델을 임의로 다른 CLI 모델로 바꾸지 않았다.
- 사용자는 보유한 OpenAI API 키를 로컬에 설정해 사용하는 방식을 선택했다.
- 기존 과제 코드의 OpenAI API 기본 모델은 `gpt-4o-mini`이다. CLI의 기본 모델 설정과 별개다.
- 현재 프로세스·Windows 사용자·시스템 환경변수에서 키가 없음을 값 출력 없이 확인했다. Git에서 제외되는 `.env`에 사용자가 직접 입력할 빈 설정을 준비했다. API 호출은 아직 하지 않았다.

## 배운 점

- A2A 카드의 binding을 읽어야 한다. echo의 REST와 판매자의 JSON-RPC가 모두 A2A 1.0으로 동작한다.
- 다중 턴 협상은 같은 taskId를 유지해야 한다. contextId는 관련 메시지와 task를 묶는다.
- 응답 메시지는 협상 진행을, artifact의 data Part는 최종 영수증을 전달한다.
- A2A는 상태·메시지·결과물의 틀을 정하지만, `80`이 제안이고 `105`가 수락이라는 의미나 최저가·예산 정책까지 정하지 않는다.
- 이 판매자는 강의의 규칙 기반 예제다. 최종 과제에서는 이 판단을 하네스가 수행해야 한다.

이 기록은 실습용이며 최종 과제의 `REPORT.md` 또는 공식 `test-report.json`을 대신하지 않는다.
