# Week 06 REPORT — 하네스로 만든 가게와 buyer (26620029)

## (1) 하네스, 모델, 실행 명령

- 하네스: Claude Code CLI 2.1.302, 모델 `haiku`
- 호출 명령 (`harness.py`의 `HARNESS_CMD`), 프롬프트는 표준 입력으로 넣는다:

  ```bash
  claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence
  ```

- 하네스는 `tempfile.mkdtemp()`로 만든 **빈 폴더**를 cwd로 실행한다. 이 저장소의 `CLAUDE.md`/`AGENTS.md`가 프롬프트에 섞이지 않게 하려는 것이다. 호출 하나의 타임아웃은 90초이고, 답에서 JSON을 못 찾거나 시간이 넘으면 한 번 더 부른다.
- `agent.json`의 명령: `python seller.py --port {port}`, `python buyer.py --url {url} --item {item} --budget {budget}`
- 환경: Python 3.13, a2a-sdk 1.2.2, uvicorn 0.53.0, httpx. API 키는 쓰지 않았다(Claude Code 로그인 세션).
- trade test 한 번: 1회차 4분 59초, 2회차 하네스 호출 60회(가게 42 + buyer 18). 교수님 참조 실행(4분 41초, 60회)과 비슷하다.

## (2) 가게와 buyer가 판단하는 방법

| | 가게 (`seller.py`) | buyer (`buyer.py`) |
|---|---|---|
| A2A 부분 | 실습 `seller_agent.py` 뼈대 그대로 (카드, `TaskUpdater`, receipt artifact, `self.open`) | httpx로 JSON-RPC 직접 호출. 카드 → `supportedInterfaces[0].url` → `SendMessage` → 같은 `taskId`로 이어 보냄. 모든 요청(카드 GET 포함)에 `A2A-Version: 1.0` |
| 하네스가 정하는 것 | 첫 호가, 매 제안마다 counter / accept / reject와 역제안 값 | 매 라운드 accept(호가 수락) / offer(내 값) / walk(그만두기)와 값 |
| 프롬프트에 넣는 것 | 품목, 하한가, 지금까지의 호가·제안 이력, 현재 호가, 이번 제안 | 품목, 예산, 이력, 현재 호가, 몇 번째 제안인지, "가게가 낮은 제안 몇 번에 끝낼 수 있다" |
| 답 형식 | JSON 한 줄 `{"action": "accept"\|"counter"\|"reject", "price": N, "say": "..."}`. 첫 호가는 `{"price": N, "say": "..."}` | JSON 한 줄 `{"action": "accept"\|"offer"\|"walk", "price": N}` |
| 한도를 지키는 곳 | **프롬프트 + 코드.** `guard_ask()`가 하한가 아래 호가를 하한가로 올리고, 하한가 아래 accept는 현재 호가로 하는 counter로 바꾼다 | **프롬프트 + 코드.** `guard_offer()`가 예산을 넘는 제안을 예산으로 깎는다. 예산을 넘는 호가를 accept해도 실제로 보내는 값은 예산이다 |
| 하네스가 실패하면 | 현재 호가를 그대로 유지(counter) | 거래하지 않고 walk(CancelTask). 예산 쪽으로 안전한 선택 |

코드 가드는 결정을 새로 만들지 않고, 규칙을 어기는 결정만 막는다. 가격 메시지는 코드가 `"<모델 문장> Counteroffer: <호가>."` 형식으로 만든다. trade test가 "마지막 숫자"를 호가로 읽기 때문에, 호가 숫자는 항상 코드가 맨 끝에 붙인다.

## (3) 통과하기까지 실패한 것과 고친 것

trade test는 1회차부터 21/21이었다. 하지만 PASS 줄만으로는 안 보이는 문제가 로그에 세 가지 있었다.

**① 하한가를 프롬프트로만 막았다면 `seller_holds_reserve`가 실패했다.** 프롬프트에 "Never ask below it"이라고 적었는데도, haiku는 책장(하한가 260)에서 230 제안을 받자 245를 불렀다. 1회차 `logs/seller-under-test.log`:

```
[seller c31176d5] call 29 (6.2s) -> '{"action": "counter", "price": 245, "say": "I can\'t come down to 230, but I\'m willing to meet you partway at 245 if that works for you."}'
[guard] ask 245 below reserve 260 -> 260
```

같은 실행에서 한 번 더 일어났고(`call 35`), 2회차에도 한 번 있었다. 세 번 모두 코드 가드가 260으로 올려서 테스트는 통과했다. 프롬프트만 있었다면 kit buyer가 245를 마지막 숫자로 읽어 `no ask below 260` 조건에서 실패했을 것이다. 이 결과를 보고 **한도는 코드에도 둔다**는 설계를 유지했다.

**② 가드는 숫자만 고치고 문장은 그대로 둬서, 메시지가 앞뒤가 맞지 않았다.** 1회차 `logs/trade-test.log`:

```
<- TASK_STATE_INPUT_REQUIRED | I can't come down to 230, but I'm willing to meet you partway at 245 if that works for you. Counteroffer: 260.
<- TASK_STATE_INPUT_REQUIRED | We're close, but I can't go below 245 on this bookshelf; can you meet me there? Counteroffer: 260.
```

kit buyer는 마지막 숫자만 읽어서 문제가 없었다. 하지만 문장 전체를 읽는 상대(사람이나 다른 하네스 buyer)는 245에 팔겠다는 말로 받아들일 수 있다. 수정(`ae0b677`): 모델 문장에 숫자가 있는데 실제 호가와 다르면 문장을 버리고 `Counteroffer: N.`만 보낸다. 2회차에서는 가드가 작동한 줄이 `<- TASK_STATE_INPUT_REQUIRED | Counteroffer: 260.`으로 나갔다.

**③ buyer가 CancelTask를 두 번 보냈다.** 하네스가 walk를 고르면 CancelTask를 보내고 루프를 빠져나오는데, 내 코드가 그 응답으로 `t`를 갱신하지 않았다. 그래서 루프 밖의 "라운드 소진 → 취소" 분기가 한 번 더 실행됐다. 1회차 `logs/buyer-under-test-buyer_holds_budget-1.log`:

```
harness says walk; walking away (CancelTask)
out of rounds; walking away (CancelTask)
<- error -32002 Task cannot be canceled
done: TASK_STATE_INPUT_REQUIRED, harness calls 5, 22s
```

테스트는 거래가 없고 헤더가 맞으면 통과라서 PASS였다. 하지만 이미 취소된 task에 요청을 또 보낸 것이고, 마지막 상태도 잘못 기록됐다. 수정(`545bc16`): CancelTask가 돌려준 task로 `t`를 바꾼다. 2회차에서는 `done: TASK_STATE_CANCELED`로 끝나고 오류가 없다.

그 밖에 본 것: buyer는 lamp에서 거의 매번 첫 제안에 100이나 105를 불러 바로 샀다(하네스 1회 호출). 하한가가 100이라는 정보는 buyer 프롬프트에 없다. 그런데도 kit 가게의 첫 호가 130을 보고 바로 100 근처를 부른 것은 모델이 그렇게 판단했기 때문이다. 책장에서는 180→215→225→230처럼 예산 쪽으로 올리다가, 가게가 거절하거나(REJECTED) 하네스가 walk를 골랐다(CANCELED). 예산 가드가 작동한 적은 없다. 그래도 최저가 쪽에서 모델이 프롬프트를 어긴 것을 봤기 때문에, 예산 가드도 그대로 둔다.


## (4) 결론 / Lessoned & Learn

① AI가 "최저가 아래로 부르지 마"를 어겼다

프롬프트에 넣은 지시 (seller.py의 OFFER_PROMPT):

Your lowest acceptable price (reserve) is {reserve}. Never ask below it and never sell below it.

1회차 logs/seller-under-test.log: 손님이 230을 내자 모델이 245를 불렀고, 가드가 260으로 올렸습니다.

135-[task c31176d5] <- '230'
136-[seller c31176d5] call 29 (6.2s) -> '{"action": "counter", "price": 245, "say": "I can\'t come down to 230, but I\'m willing to meet you partway at 245 if that works for you."}'
137:[guard] ask 245 below reserve 260 -> 260
--
154-[task a8bc1cdb] <- '230'
155-[seller a8bc1cdb] call 35 (5.8s) -> '{"action": "counter", "price": 245, "say": "We\'re close, but I can\'t go below 245 on this bookshelf; can you meet me there?"}'
156:[guard] ask 245 below reserve 260 -> 260

2회차에도 같은 위반이 한 번 더 나왔습니다. 우연한 한 번이 아니라 반복되는 경향입니다.

68-[seller b7aedc53] call 23 (5.7s) -> '{"action": "counter", "price": 245, "say": "...I can\'t go below 245 on this bookshelf, and I\'m offering that as my final price."}'
69:[guard] ask 245 below reserve 260 -> 260

→ 두 번의 실행에서 모두 3번 위반했고, 3번 모두 가드가 막았습니다.

② 숫자만 고쳐서 앞뒤가 안 맞는 메시지가 나갔다

1회차 logs/trade-test.log: 상대에게 실제로 전달된 메시지입니다.

86:  <- TASK_STATE_INPUT_REQUIRED | I can't come down to 230, but I'm willing to meet you partway at 245 if that works for you. Counteroffer: 260.
101: <- TASK_STATE_INPUT_REQUIRED | We're close, but I can't go below 245 on this bookshelf; can you meet me there? Counteroffer: 260.

2회차 (커밋 ae0b677로 수정한 뒤): 다른 숫자가 든 문장은 빠지고 가격만 나갑니다.

70-  -> SendMessage "230" taskId=b7aedc53
71:  <- TASK_STATE_INPUT_REQUIRED | Counteroffer: 260.
③ 구매자가 "취소"를 두 번 보냈다

1회차 logs/buyer-under-test-buyer_holds_budget-1.log:

-> SendMessage '230' taskId=7733842c
<- TASK_STATE_INPUT_REQUIRED | 230 is too low. Counteroffer: 260.
harness says walk; walking away (CancelTask)        ← 1번째 취소
out of rounds; walking away (CancelTask)            ← 2번째 취소 (버그)
<- error -32002 Task cannot be canceled
done: TASK_STATE_INPUT_REQUIRED, harness calls 5, 22s   ← 취소됐는데 상태도 잘못 기록됨

2회차 (커밋 545bc16로 수정한 뒤) logs/buyer-under-test-buyer_holds_budget-2.log:

harness says walk; walking away (CancelTask)
done: TASK_STATE_CANCELED, harness calls 5, 25s

그래도 테스트는 PASS였다는 근거: 1회차 결과도 21/21 passed -> PASS였습니다. 시험은 "거래가 없었는지"만 봤기 때문에 이 버그를 잡지 못했습니다.


