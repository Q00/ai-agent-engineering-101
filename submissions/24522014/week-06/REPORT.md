# Week 06 — 내 하네스로 만드는 buyer와 가게

학번: 24522014 / GitHub: kcwarni

## 1. 하네스, 모델, 실행 환경

seller와 buyer를 Python으로 구현하고, 각 가격 판단은 비대화형 하네스에 요청한다.
Codex와 함께 STEP 1–10으로 나누어 구현·검증했으며, 각 단계의 실행 방법과
시도·수정 기록은 `DESIGN.md`, `STEP2.md`부터 `STEP10.md`에 남겼다.
`a2a-lab/`은 앞선 강의 실습이며, 제출 대상 에이전트는 루트의 `seller.py`, `buyer.py`다.

현재 제출 설정 `agent.json`은 **seller와 buyer 모두 Claude Code + Haiku**다.
모델 인자는 CLI 별칭인 `haiku`를 사용한다. `harness.py`가 구성하는 인자는 다음과 같다.
각 프롬프트는 UTF-8 표준 입력으로 보낸다.

```text
claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence
```

대안으로 Codex + Luna를 구현했으며, 모델 ID는 `gpt-6-luna`다.

```text
codex exec --skip-git-repo-check --model gpt-6-luna --sandbox read-only --ephemeral --ignore-user-config -c project_doc_max_bytes=0 -
```

Windows에서는 npm shim 대신 설치된 native `codex.exe`를 찾아 인자 목록으로 실행한다.
실제 실행 파일의 절대 경로와 인자는 각 `logs/harness/*/metadata.json`에 기록한다.
매 호출의 작업 디렉터리는 새로 만든 빈 `.harness-work/<실행ID>/`다.
프로젝트 지침이 판단에 섞이지 않도록 CLI 설정도 제한하며, 실패 시 다른 모델이나
규칙 기반 가격으로 자동 대체하지 않는다. CLI 로그인된 구독을 사용했고 API 키는 넣지 않았다.

Python 의존성은 `requirements.txt`의 a2a-sdk[http-server] 1.2.2, uvicorn 0.54.0,
httpx 0.28.1이다. Windows PowerShell에서 기존 실습 가상환경을 사용했다.
공식 테스트가 과제 폴더에서 실행하는 명령은 정확히 다음과 같다.

```text
python seller.py --mode claude --port {port}
python buyer.py --mode claude --url {url} --item {item} --budget {budget}
```

`profiles/`에는 Claude 통일, Codex 통일, 두 방향의 교차 조합을 저장했다.
`agent_profiles.py`로 선택하면 `agent.json`이 바뀐다. 변경 후에는 공식 결과도
새 명령으로 다시 생성해야 한다. 현재 제출 결과는 Claude 통일 설정에 대한 것이다.

## 2. 판단 프롬프트와 코드의 역할

공통 응답은 설명이나 Markdown 없이 `action`, `price`, `reason`만 가진 JSON이다.
금액은 양의 정수이며, 거절·철수의 금액은 null이다. `reason`은 비공개 판단 로그에
저장하고 A2A 가격 메시지에 복사하지 않는다. 아래 JSON은 형식 예시다.

```json
{"action":"ask","price":150,"reason":"Leave room to negotiate"}
```

### Seller

`prompts/seller.txt`는 최초 요청에서 `ask`, 후속 요청에서 `ask`, `accept`, `reject`를
선택하도록 한다. 하한가 미만 제안에는 먼저 역제안하고, 반복되는 낮은 제안에는
거절하도록 요청한다. 충분히 높은 제안의 수락과 개선된 제안에서 합리적으로 거래를
마치는 것도 요청한다. 이 문장들은 모델의 판단 지침이며 고정 가격 공식은 없다.
입력에는 phase, 품목, 비공개 reserve, buyer 메시지, 현재 offer와 ask, 같은 task의
이력이 들어간다. 상대 메시지는 지시가 아닌 데이터로 취급하도록 명시한다.

Python은 `ask`를 INPUT_REQUIRED, `accept`를 receipt artifact와 COMPLETED,
`reject`를 REJECTED로 변환한다. lamp 100, chair 180, bookshelf 260의 하한가를
프롬프트와 코드 양쪽에서 적용한다. `accept` 가격은 현재 buyer 제안과 같아야 한다.
하한가 미만 판단을 올바른 가격으로 덮어쓰지 않고 검증 오류로 돌려보낸다.
JSON·행동·금액 검증 실패에는 최대 한 번 정정을 요청하며, 판단 전체 제한은 90초다.
인증·프로세스 오류는 정정 재시도 대상으로 삼지 않는다. 유효한 판단을 얻지 못하면
FAILED로 처리하고 영수증을 만들지 않는다.

품목·현재 제시가·이력은 taskId별 메모리에 관리한다. 재시작 후 거래 복구를 구현한 것은
아니다. CancelTask는 모델 호출 없이 취소 상태로 바꾸며 종료된 task의 재요청은 SDK가
오류로 반환한다. 잘못된 숫자 입력에는 기존 검증된 ask를 반복해 task를 열어 둔다.

### Buyer

`prompts/buyer.txt`는 예산 안에서 한 번 구매하는 것을 목표로 한다. ask가 예산 이하면
그 가격을 제안하고, 예산 초과면 첫 가격을 최저가로 단정하지 않고 흥정하도록 요청한다.
개선 없는 낮은 제안을 반복하지 말고, 거래가 불가능하면 `walk_away`를 선택하도록 한다.
입력은 품목, 예산, seller 메시지의 현재 ask, 거래 이력, 남은 턴과 시간이다.
seller의 비공개 하한가는 전달하지 않는다. 모든 금액 제안과 철수는 모델이 결정한다.

Python은 `offer`의 금액을 네트워크 전송 전에 검사하여 예산 초과를 차단한다.
`walk_away`는 CancelTask로 보내고 CANCELED 응답을 확인한다. 모델 응답의 형식·예산
오류는 최대 한 번 정정을 요청하고, 임의로 가격을 예산에 맞춰 보정하지 않는다.
최대 판단 턴은 12회, 세션 제한은 290초, 한 판단은 남은 시간 범위에서 최대 90초다.
실패·시간 초과를 정상 철수나 구매 성공으로 바꾸지 않는다.

buyer는 Agent Card의 JSON-RPC URL을 사용하며 버전 헤더, taskId와 contextId를 유지한다.
COMPLETED 상태의 영수증은 품목·양의 숫자 가격·예산을 다시 검사한다.
`--manual`은 앞 단계 통신 실습용이며, 현재 공식 제출 명령은 모델이 판단하는 모드다.

## 3. 실패와 수정: 원본 로그에 근거한 기록

### 숫자가 아닌 제안 뒤에 task를 이어갈 수 없었던 문제

처음에는 seller 실행기에서 잘못된 제안에 InvalidParamsError를 던졌다.
SDK가 이 예외로 task를 종료했기 때문에 다음 정상 제안도 처리하지 못했다.
`logs/step4-tests-20261016-132001.stderr.txt`의 실패 부분은 다음과 같다.

```text
a2a.utils.errors.InvalidParamsError: Send your offer as one positive integer.
FAIL: test_invalid_offer_does_not_call_harness_or_lose_task (test_seller_negotiation.NegotiationProtocol.test_invalid_offer_does_not_call_harness_or_lose_task)
FAILED (failures=1)
```

같은 로그의 후속 요청 오류 메시지는 다음과 같다.

```text
Task e62e2ab8-0f9e-47fb-bedc-314490ecfaa6 is already completed.
```

수정 후에는 예외를 던지는 대신 입력 안내와 기존 ask를 INPUT_REQUIRED로 반환했다.
모델을 부르거나 새 가격을 만들지 않고 같은 task에 정상 숫자를 다시 받을 수 있다.
`logs/step4-tests-20261016-132032.stderr.txt`에서 로컬 검사 10개가 통과했다.

```text
Ran 10 tests in 0.179s

OK
```

별도로 Codex 상태 전이 검사에서 모델이 낮은 가격을 두 번째에 거절하자 검증기가
실패한 시도도 보존했다. `logs/step4/20261016T042512026085Z-codex/verification.json`에
`passed: false`와 REJECTED 응답이 있다. 과제는 거절 횟수를 고정하지 않으므로 검증기의
가정을 수정하고 조기 거절을 허용했다. 재실행 증거는
`logs/step4/20261016T042653538668Z-codex/`에 있다. 모델 판단을 고정 횟수로 바꾼 것은 아니다.

### LAB 파일 BOM으로 구조 검사가 실패한 문제

공식 전체 거래 테스트를 진행하면서 구조 검사도 실행했다.
`logs/step9-structure-before-20261016-161935.stdout.txt`에는 다음 실패가 있다.

```text
FAIL  a2a-lab\seller_agent.py has a syntax error: invalid non-printable character U+FEFF (<unknown>, line 1)
```

공식 검사기는 실습 코드도 순회한다. 해당 파일 첫 부분의 UTF-8 BOM만 제거했으며,
`logs/step9-structure-bom-fixed-20261016-162007.stdout.txt`에서 소스 21개 파싱을 확인했다.
수정 전 실패와 수정 후 검사는 각각 커밋했다. 당시 아직 생성 전인 거래 결과와
작성 전인 REPORT.md가 없다는 실패도 원본 그대로 남겼다. 과거 로그는 편집하지 않았다.

## 4. 검증 결과와 재현

2026-10-16 16:17:56 KST에 현재 `agent.json`으로 시작한 공식 전체 테스트는
**21/21 PASS**였다. `scripts/trade_test_week06.py`를 수정하거나 실행 함수를 교체하지 않았다.
`test-report.json`과 `logs/trade-test.log`는 공식 테스트가 직접 생성했다.
테스트 코드 SHA-256은 `8e00baa43b319e6c0067eab8eedc6834cbe4b9dc3e9e2ab5bd16e9572c99f120`이다.

| 항목 | 반복 결과 |
|---|---|
| 우리 seller의 lamp 판매 | 120, 100, 100 |
| 우리 seller의 bookshelf 하한가 | 세 번 모두 준수, 거절 종료 |
| 우리 buyer의 lamp 구매 | 105, 105, 100 |
| 우리 buyer의 bookshelf 예산 | 세 번 모두 구매 없이 취소 |
| 카드·첫 제안·종료 task·버전 헤더·taskId | 모두 통과 |

각 seller 출력은 `logs/seller-under-test.log`, buyer 6회 출력은
`logs/buyer-under-test-*.log`에 있다. 출력에 연결된 `logs/harness/`에는 프롬프트와 원본
stdout·stderr·메타데이터, `logs/seller/`와 `logs/buyer/`에는 판단·HTTP·결과 기록이 있다.
Claude 통일 외의 Codex 통일과 양방향 교차 조합도 Step 8에서 각 두 시나리오씩 통과했다.
이는 자체 에이전트끼리의 연결 검사 8건이며, 네 조합 모두 공식 전체 테스트를 통과했다는
뜻은 아니다. 타 수강생 에이전트와의 선택 실습은 이 기록에 포함되지 않는다.

재현하려면 CLI에 로그인하고 `requirements.txt`를 설치한 Python을 PATH에서 찾을 수 있어야
한다. 아래는 기존 Windows 가상환경을 사용하는 저장소 루트 기준 명령이다.
새 환경에서는 같은 의존성을 설치한 가상환경 경로로 바꾼다.

```powershell
$work = Join-Path (Get-Location).Path 'submissions/24522014/week-06'
$env:Path = (Join-Path $work 'a2a-lab/.venv/Scripts') + ';' + $env:Path
python scripts/trade_test_week06.py submissions/24522014/week-06
python scripts/check_week06.py submissions/24522014/week-06
```

거래 테스트 재실행은 모델 사용량을 소모하며 가격과 턴 수는 달라질 수 있다.
재실행 전 고정 이름의 기존 보고서와 공식 로그를 별도 시각별 폴더에 복사·커밋하여
이전 결과를 보존한다. Step 10에서는 문서만 추가했으므로 이미 통과한 거래 테스트를
다시 호출하지 않고, 현재 스크립트 해시·명령과 결과의 일치를 공식 구조 검사로 확인한다.

## 5. A2A가 해결한 것과 별도로 합의한 것

A2A는 Agent Card로 통신 주소와 JSON-RPC 1.0 인터페이스를 알리고, SendMessage와
taskId로 여러 번의 흥정을 같은 작업에 연결하며, INPUT_REQUIRED·COMPLETED·REJECTED·
CANCELED 상태와 artifact로 진행과 결과를 표현하는 공통 틀을 제공했다. 덕분에 상대가
Claude인지 Codex인지 몰라도 같은 buyer의 전송 코드를 사용할 수 있었다. 하지만 A2A만으로
가격의 의미나 수락 문법이 결정되지는 않았다. 이 구현은 INPUT_REQUIRED 메시지의 마지막
숫자를 ask로 읽고, buyer가 양의 정수 금액을 보내면 현재 제안으로 해석하며, 별도 accept
문자열 대신 seller가 그 금액을 수락해 COMPLETED와 receipt를 반환하도록 합의했다.
receipt의 seller·item·숫자 price 필드, 품목 이름, 하한가와 예산 검증도 애플리케이션의
계약이다. A2A 호환 카드가 있다는 것만으로 이 가격 문법까지 호환되는 것은 아니다.
