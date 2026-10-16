# STEP 4 — 같은 task에서 흥정하고 거래 종료하기

## 완료한 동작

- 하네스가 역제안 `ask`, 수락 `accept`, 거절 `reject`를 선택한다.
- 같은 taskId의 품목·최근 제시가·이력을 다음 판단에 전달한다.
- 역제안은 `TASK_STATE_INPUT_REQUIRED`, 수락은 영수증 artifact와
  `TASK_STATE_COMPLETED`, 거절은 `TASK_STATE_REJECTED`로 변환한다.
- 취소 요청은 모델 호출 없이 `TASK_STATE_CANCELED`로 처리한다.
- 종료 시 열려 있는 거래 목록에서 제거한다. 종료된 task의 추가 요청은
  SDK가 오류로 응답하는 것도 로컬 테스트로 확인했다.

Python은 seller의 가격·수락·거절을 대신 결정하지 않는다. 하한가와
수락 금액의 일치 여부를 검증하고, 잘못된 판단에 최대 한 번 정정을 요청한다.
수락 가격은 하한가 이상이며 현재 buyer 제안과 같아야 한다.
이를 만족하지 못하면 `failed`이며 영수증을 만들지 않는다.

Claude Code + Haiku와 Codex + Luna에서 실제로 아래 흐름을 검증했다.

```text
lamp 요청 → input-required
80 제안  → input-required (하네스 역제안)
제시가 수락 → completed + receipt

bookshelf 요청 → 낮은 가격 반복 → rejected
chair 요청 → CancelTask → canceled
```

영수증의 price는 실제 수락 금액이다. 숫자가 JSON에서 `120.0`처럼 보일 수
있는데 SDK의 data Part 숫자 직렬화 결과이며 문자열 가격이 아니다.
이 검증은 seller의 상태 전이를 확인한다. 예산 120을 지키는 buyer나 공식
테스트의 반복 통과를 검증한 것은 아니다.

## 직접 실행 — 서버 다시 시작

기존 STEP 3 서버가 켜져 있다면 터미널 1에서 Ctrl+C로 종료해야 새 코드를
사용할 수 있다. 두 터미널 모두 다음 폴더로 이동한다.

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-06
```

터미널 1:

```powershell
.\a2a-lab\.venv\Scripts\python.exe seller.py --mode claude --port 10099
```

Codex로 실습하려면 앞 서버를 종료한 뒤 `--mode codex`로 시작한다.
기존처럼 `mixed`와 `--seller-harness`로도 선택할 수 있다.

## 직접 실행 — 역제안과 영수증

터미널 2:

```powershell
. .\a2a-lab\use-a2a.ps1
a2a --timeout 120s send -a http://127.0.0.1:10099 "I want to buy a lamp"
$taskId = Read-Host "방금 출력된 Task ID를 붙여넣으세요"
a2a --timeout 120s send -a http://127.0.0.1:10099 --task-id $taskId "80"
```

`input-required`와 새로운 제시 가격을 확인한다. 그 가격을 제안해 거래를
마치려면 이어서 다음을 실행한다. 숫자는 직접 방금 응답을 보고 입력한다.

```powershell
$offer = Read-Host "마지막 asking 가격을 숫자로 입력하세요"
a2a --timeout 120s send -a http://127.0.0.1:10099 --task-id $taskId $offer
a2a task get -a http://127.0.0.1:10099 $taskId
```

직접 확인할 것:

1. 첫 메시지와 후속 메시지의 Task ID가 같은가?
2. 역제안은 lamp 하한가인 100 이상인가?
3. 수락 후 `completed`와 `receipt` artifact가 보이는가?
4. receipt의 item은 lamp이며 price는 방금 제안한 금액과 같은가?
5. `logs/seller/`의 taskId별 기록에 opening, followup과 이전 이력이 있는가?

가격과 거절 시점은 모델의 결정이므로 매 실행의 출력이 같지는 않다.
거래가 `rejected`로 끝나면 그 task로 더 보내지 않고 새 품목 요청으로 시작한다.

## 직접 실행 — 거절과 취소

새 bookshelf 거래를 만든 뒤 매우 낮은 가격을 반복한다.

```powershell
a2a --timeout 120s send -a http://127.0.0.1:10099 "I want to buy a bookshelf"
$bookTaskId = Read-Host "새 bookshelf Task ID를 붙여넣으세요"
a2a --timeout 120s send -a http://127.0.0.1:10099 --task-id $bookTaskId "1"
```

`input-required`이면 같은 제안 명령을 반복한다. `rejected`가 나오면 중단한다.
프롬프트는 반복되는 낮은 제안에 거절하도록 요청하지만, 정확한 횟수는
코드에서 강제하지 않는다. 이번 실제 검증에서는 2회 또는 3회에 거절했다.

취소는 별도의 새 거래로 확인한다.

```powershell
a2a --timeout 120s send -a http://127.0.0.1:10099 "I want to buy a chair"
$chairTaskId = Read-Host "새 chair Task ID를 붙여넣으세요"
a2a task cancel -a http://127.0.0.1:10099 $chairTaskId
```

취소 시 `canceled`가 나오며 영수증은 없어야 한다. 끝나면 터미널 1에서
Ctrl+C로 서버를 종료한다. 모델 판단 요청마다 구독 사용량이 소모된다.

## 실패와 수정 과정

1. 처음에는 숫자가 아닌 가격에 `InvalidParamsError` 예외를 던졌다.
   SDK가 task를 종료해, 다음 정상 입력도 아래 오류로 거절됐다.

   ```text
   test_invalid_offer_does_not_call_harness_or_lose_task ... FAIL
   Task ... is already completed.
   ```

   수정 후에는 예외 대신 기존 제시가와 입력 안내를 `input-required`로
   반환한다. 모델은 호출하지 않으며 새 가격을 임의로 만들지도 않는다.
   정상적인 숫자를 다시 보내면 같은 task로 계속 흥정할 수 있다.
   첫 실패 원본: `logs/step4-tests-20261016-132001.stderr.txt`.
   수정 후 10개 로컬 검사 통과: `logs/step4-tests-20261016-132032.stderr.txt`.
   `Dispatcher task is not running` 진단은 일부 로컬 테스트 종료 시에도
   출력됐지만 검사 결과는 OK였다. 이 SDK 진단의 원인은 따로 조사하지 않았다.

2. 실제 Codex 검증에서 낮은 가격을 두 번째에 거절했다. 첫 검증 스크립트가
   세 번째 전까지 반드시 `input-required`라고 가정해 실패했다.

   ```text
   AssertionError: ... 'state': 'TASK_STATE_REJECTED' ...
   ```

   과제는 거절의 고정 횟수를 요구하지 않는다. 허용된 조기 거절 시 즉시
   반복을 멈추고, 모든 역제안이 하한가 이상인지 확인하도록 검증을 수정했다.
   Codex를 다시 실행해 거절과 취소까지 통과했다. 실패 로그는 편집하지 않았다.

## 검증 자료

- Claude 성공: `logs/step4/20261016T042511024471Z-claude/`
- Codex 첫 시도 (검증 스크립트 실패): `logs/step4/20261016T042512026085Z-codex/`
- Codex 성공: `logs/step4/20261016T042653538668Z-codex/`
- 모델 원본 입출력: `logs/harness/`
- taskId별 판단·검증: `logs/seller/`
- 합성 응답을 사용한 로컬 검사 기록: `logs/step4-tests/`와 `logs/step3-tests/`

각 실제 검증 폴더에는 원본 HTTP 요청·응답, 카드, 서버 출력, 요약 결과가 있다.
검증용 서버만 종료했으며 기존 사용자의 서버는 건드리지 않았다.

```powershell
# 실제 모델 호출 없이 로컬 회귀 검사
.\a2a-lab\.venv\Scripts\python.exe -m unittest test_seller_opening test_seller_negotiation -v
# 실제 모델을 여러 번 호출하는 상태 전이 실습
.\a2a-lab\.venv\Scripts\python.exe verify_step4.py --mode claude
.\a2a-lab\.venv\Scripts\python.exe verify_step4.py --mode codex
```

buyer, agent.json과 공식 trade test는 이후 단계다.
다음 STEP 5에서는 seller의 제한 조건을 더 확인한다.
