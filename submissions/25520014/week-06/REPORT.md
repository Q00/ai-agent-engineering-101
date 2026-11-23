# Week 06 — Harness as a buyer and a seller

## 1. Harness, model, command

| | |
|---|---|
| harness | Claude Code CLI 2.1.302, 비대화형 `claude -p` |
| model | `--model haiku` → `claude-haiku-5-5` (`--output-format json`의 `modelUsage`로 확인) |
| temperature | 직접 설정 불가 |

```bash
claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence --output-format json
```

seller와 buyer가 같은 래퍼(`harness.py`)를 쓴다. 프롬프트는 stdin으로 넘기고, 응답 JSON의
`result` 필드를 읽는다. 이 레포의 CLAUDE.md/AGENTS.md를 읽지 않도록 빈 임시 디렉터리에서
실행한다. 호출마다 새 세션이므로 지금까지의 협상 기록을 매번 프롬프트에 펼쳐 넣는다.

`agent.json`의 명령이 `python`이라 trade test는 레포 `.venv`를 PATH 앞에 두고 돌렸다.

```bash
PATH="$PWD/.venv/bin:$PATH" python scripts/trade_test_week06.py submissions/25520014/week-06
```

## 2. How each agent decides

**답 형식.** 두 에이전트 모두 JSON 객체 하나로 답하게 했다. `harness.ask_json`이 응답에서 첫
`{...}`를 뽑아 파싱하고, 실패하면 한 번 다시 부른다. 두 번 다 실패하면 seller는 task를
`REJECTED`로 끝내고 buyer는 종료한다.

| agent | 언제 | 허용한 답 |
|---|---|---|
| seller | 첫 메시지 | `{"price": N}` |
| seller | buyer의 offer를 받았을 때 | `{"action": "accept"}` / `{"action": "counter", "price": N}` / `{"action": "reject"}` |
| buyer | seller의 호가를 받았을 때 | `{"action": "accept"}` / `{"action": "offer", "price": N}` / `{"action": "walk"}` |

**프롬프트.** seller(`seller.py`의 `OPEN_PROMPT`, `RESPOND_PROMPT`)에는 품목, reserve, reserve를
밝히지 말라는 지시, 지금까지의 기록, buyer의 최신 offer를 넣었다. accept는 "offer가 reserve
이상일 때만", counter는 "reserve 이상"으로 조건을 적었다. buyer(`buyer.py`의 `DECIDE_PROMPT`)에는
품목, budget, 기록, 현재 호가를 넣었다. accept는 "호가가 budget 이하일 때만", offer는 "budget
이하"로 조건을 적고, 너무 낮게만 부르면 seller가 협상을 끝낼 수 있다고 덧붙였다.

**한도를 지키는 곳: 프롬프트와 코드 둘 다.** 코드 가드는 모델의 답이 한도를 넘을 때만 숫자를
한도로 되돌리고, 그때마다 로그에 `GUARD` 줄을 남긴다. 처음에는 프롬프트로만 지켰다. 바꾼 이유는
3부에 있다.

| | 모델의 답 | 코드 가드 |
|---|---|---|
| seller reserve | 호가나 counter가 reserve 미만 | reserve로 올린다 |
| seller reserve | reserve 미만 offer에 accept | 판매하지 않고 reserve로 counter |
| buyer budget | offer나 accept할 가격이 budget 초과 | budget으로 offer |

## 3. What failed and what changed
첫번째 실행에서는 `seller`와 `buyer`의 `reserve`와 `budget`을 프롬프트로만 지키게 하였다. 그 결과, `seller_holds_reserve`에서 3번 중 2번 실패하였다.

```
# logs/trade-test.log (첫 번째 실행, 커밋 51b09d3)
FAIL  seller_holds_reserve #1: TASK_STATE_INPUT_REQUIRED, asks [340, 300, 260, 270, 250, 260, 260, 260, 260]
FAIL  seller_holds_reserve #2: TASK_STATE_COMPLETED, asks [380, 300, 280, 270, 265, 260, 260], sold at 230.0

# logs/seller-under-test.log (첫 번째 실행, 커밋 51b09d3)
[harness respond 6692f0f0] 2.6s reply: {"action": "counter", "price": 250}
[harness respond 23d44339] 2.5s reply: {"action": "accept"}
[task 23d44339] -> COMPLETED at 230
```

모델의 가격 제안 실수를 해결하기 위하여, 프롬프트를 수정하는 대신에 코드 수준에서 가드를 추가하였다. 두번째 실행에서는 21개 테스트 모두 문제 없이 통과하였다. 하지만 `seller`는 첫 번째 실행과 같은 실수를 반복하였고, `buyer`도 budget을 넘긴 240을 제안하여 가드가 총 2번 작동하였다.

```
# logs/seller-under-test.log
[harness respond c4ae5db6] 3.1s reply: {"action": "accept"}
[task c4ae5db6] GUARD accept at 230 < reserve 260 -> counter 260

# logs/buyer-under-test-buyer_holds_budget-2.log
[harness round 3] 2.9s reply: {"action": "offer", "price": 240}
GUARD offer at 240 > budget 230 -> offer 230
```


## 4. What A2A fixed, and what it did not
A2A는 누가, 어느 대화에서, 어떤 상태로 말하는지와 같은 규칙만을 정해준다. 예를 들어, 대화 상대는 Agent Card로 찾고, 협상 한 건은 같은 `taskId`로 이어가며, 현재 거래가 진행 중인지에 대한 판단은 `INPUT_REQUIRED`, `COMPLETED`, `REJECTED`와 같은 상태로 확인한다.

다만 A2A가 보장하는 것은 텍스트가 오가는 방법뿐이고, 그 텍스트 안의 내용이 무슨 뜻인지는 정해주지 않는다. 예를 들어, 호가는 텍스트에 포함된 마지막 숫자이고, 가격 제안은 첫 번째 숫자로 파악한다. 또한 A2A에는 수락 동작이 별도로 존재하지 않아, `buyer`가 호가를 그대로 재전송하는 것을 수락으로 정의하였다.
