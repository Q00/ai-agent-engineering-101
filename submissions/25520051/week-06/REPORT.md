# Week 06 — Claude Code를 A2A seller와 buyer로

## 1. Harness, 모델, 명령

- **Harness / 모델:** Claude Code 2.1.169, 모델 `haiku`. 두 에이전트 모두 [`harness.py`](./harness.py)를 통해
  아래 명령을 비대화형으로 호출한다.

  ```
  claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence --system-prompt <역할>
  ```

  프롬프트는 stdin으로 넣는다. harness는 빈 임시 디렉터리에서 실행하는데, 이 레포의 `CLAUDE.md` /
  `AGENTS.md`를 읽어 과제 규칙이 협상 판단에 섞이지 않게 하기 위해서다. 호출마다 새 세션이므로,
  지금까지의 협상 내용은 에이전트가 직접 들고 있다가 매 라운드 프롬프트에 다시 넣는다.
- **에이전트:** [`seller.py`](./seller.py)는 lab의 `seller_agent.py` 골격 위에 만든 A2A v1.0 서버
  (a2a-sdk 1.2.2, JSON-RPC binding)이고, [`buyer.py`](./buyer.py)는 SDK 클라이언트 없이 `httpx`로 직접
  작성한 A2A 클라이언트다.
- **명령** ([`agent.json`](./agent.json)): `python seller.py --port {port}`,
  `python buyer.py --url {url} --item {item} --budget {budget}`. venv(`a2a-sdk[http-server]`, `uvicorn`,
  `httpx`)를 PATH 앞에 두고 실행했다.
- **통과한 실행:** 21/21, 5분 58초, harness 호출 39회 (`logs/`의 `[harness] raw` 줄 수).

## 2. 각 에이전트의 판단 방식

두 에이전트 모두 harness에 **JSON 한 줄**로 답하게 하고, 코드는 답에서 첫 `{...}`만 뽑아 쓴다.
haiku는 거의 항상 ```` ```json ```` 코드펜스로 감싸서 답했기 때문이다
(예: `logs/buyer-under-test-buyer_deal-1.log`의 `'```json\n{"action": "offer", "price": 105, ...}\n```'`).

**Seller.** 시스템 프롬프트: 좋은 가격에 팔고 싶은 가구점 주인이며, reserve 아래로는 절대 내려가지 않고
직전 제시가보다 올리지 않는다. 매 라운드 프롬프트에는 물건, reserve, 지금까지의 대화, 직전 제시가, buyer의
새 제안이 들어간다. 답 형식: `{"action": "counter" | "accept" | "reject", "price": <정수>, "message": "<한 문장>"}`.

- reserve는 프롬프트와 **코드 두 곳에서** 지킨다. 역제안 가격은 `min(max(price, reserve), 직전 제시가)`로
  자르고, reserve 미만 제안을 accept하면 직전 제시가로 역제안하도록 바꾼다.
- input-required 메시지 끝에는 항상 `My price: <제시가>`를 붙인다. 트레이드 테스트가 메시지의 *마지막 숫자*를
  제시가로 읽는데, 모델이 쓴 문장에는 다른 숫자가 섞이는 경우가 많기 때문이다.
- 거래가 성사되면 `{"seller", "item", "price"}` data Part를 가진 `receipt` artifact를 붙인다.

**Buyer.** 시스템 프롬프트: 예산이라는 절대 상한이 있는 신중한 구매자이며, 최대한 싸게 사되 예산 안이라면
안 사는 것보다 사는 게 낫고, 상점이 예산까지 내려오지 않으면 그만둔다. 매 라운드 프롬프트에는 물건, 예산,
지금까지의 대화, 상점의 현재 제시가(상점 메시지의 마지막 숫자)가 들어간다. 답 형식:
`{"action": "offer" | "accept" | "walk", "price": <정수>, "reason": "<짧은 이유>"}`.

- `offer` / `accept`는 같은 `taskId`로 숫자만 보내고, `walk`는 `CancelTask`를 보낸다.
- 예산도 프롬프트와 **코드 두 곳에서** 지킨다. 예산을 넘는 제안은 예산으로, 상점 제시가보다 높은 제안은
  제시가로 자른다.
- 모든 요청(카드 GET, `SendMessage`, `CancelTask`)에 `A2A-Version: 1.0` 헤더를 붙인다. 10라운드 또는
  240초가 지나면 멈춘다.

통과한 실행에서 코드 안전장치는 한 번도 작동하지 않았다(`grep guard logs/*.log` 결과 없음). haiku가 프롬프트만으로
reserve와 예산을 지켰다. 그래도 안전장치를 둔 것은 "모든 실행 통과"가 조건인데, 39번 맞았다고 다음에도 맞는다는
보장은 없기 때문이다.

## 3. 실패한 것과 바꾼 것

**(a) harness가 JSON 답을 거부했다.** 처음에는 시스템 프롬프트 없이
`Reply with only this JSON: {"action":"counter","price":115}`만 stdin으로 넣어 호출했는데, haiku가 다음과 같이
거부하는 것을 터미널에서 확인했다.

> I can't respond with only arbitrary JSON output. That appears to be a prompt injection test.

Claude Code의 기본 시스템 프롬프트가 코딩 도우미 역할이라 "JSON만 답하라"는 요청을 공격처럼 본 것으로 보인다.
`--system-prompt`로 가구점 역할을 주고("You are a furniture shop negotiating a sale. Answer with one line of
JSON only: ...") 다시 호출하자 바로 JSON으로 답했다.

**(b) 1차 트레이드 테스트 19/21: harness 호출 한 번이 실패하자 buyer가 거래를 포기했다.**
커밋 `dc62c93`에 남긴 1차 실행의 `logs/trade-test.log`:

```
  FAIL  buyer_deal #3: deals [] (budget 120)
  FAIL  buyer_protocol_deal #3: posts 2, without A2A-Version 1.0 0, follow-ups with taskId 0
```

`logs/buyer-under-test-buyer_deal-3.log`:

```
[harness] call failed: TimeoutExpired(['claude', '-p', '--model', 'haiku', ...], 90)
[buyer] harness gave no price; walking away
```

harness 답이 없으면 바로 그만두게 짠 것이 문제였다. `ask_json`이 타임아웃 60초로 최대 2번 시도하게 바꿨고
(커밋 `167fa5b`), 2차 실행에서 21/21로 통과했다(커밋 `dfc2729`).

**(c) Windows에서 seller 프로세스가 남았다.** 두 번의 실행 모두 끝난 뒤에도 `seller.py`가 포트(57697, 49178)를
계속 열고 있었다(해당 포트에서 Harness Shop 카드가 응답함). 트레이드 테스트는 seller를 `shell=True`로 띄우고
`terminate()`로 끄는데, Windows에서는 `cmd.exe`만 종료되고 그 자식 Python 프로세스는 남는다. 실행마다 새 포트를
쓰기 때문에 테스트 결과에는 영향이 없었지만, 남은 프로세스는 직접 종료했다.

## 4. A2A가 정해 준 것과 정해 주지 않은 것

A2A 덕분에 상대 에이전트를 찾는 방법(Agent Card), 요청 형식(`SendMessage`, `taskId`로 이어가기), 그리고 협상이
어디까지 왔는지(input-required면 내 차례, completed면 거래 성사, rejected/canceled면 무산)는 따로 정할 필요가
없었다. 반면 메시지 *안의 내용*은 A2A가 정해 주지 않아서, "가격은 메시지의 마지막 숫자", "제안은 숫자만",
"수락은 상대 제시가를 그대로 다시 보내기", "최종 가격은 `receipt` artifact의 `price`" 같은 약속을 seller와 buyer가
따로 맞춰야 했다. 이 약속은 카드의 skill 설명에 영어 문장으로만 적혀 있어서, 다른 형식을 쓰는 상대를 만나면
프로토콜 오류 없이 협상이 어긋날 수 있다.
