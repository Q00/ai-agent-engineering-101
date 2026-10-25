# Week 06 Report: my harness as a seller and a buyer over A2A

## 1. Harness, model, command

- harness: Claude Code 2.1.296, non-interactive
- command (prompt on stdin, run in a fresh empty temp directory so no project instructions are read, 90 s timeout):
  `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence`
- model: the `haiku` alias resolved to **claude-haiku-5-5** (read from `--output-format json` on one call). One decision call is about 3.3k input tokens (mostly Claude Code's own system prompt, partly cached) and ~10 output tokens, about USD 0.0006 at list price; it runs on the subscription, no API key.
- A2A side: a2a-sdk 1.2.2 (JSON-RPC binding), uvicorn 0.54.0, httpx, Python 3.13.
- `agent.json`: seller `python seller.py --port {port}`, buyer `python buyer.py {url} {item} {budget}`.
- run: `python scripts/trade_test_week06.py submissions/26510119/week-06`. On this machine the shell's `python` belonged to another project's venv without a2a-sdk, so the test was run as `PATH=/root/miniconda3/bin:$PATH python scripts/trade_test_week06.py ...`.
- harness calls per trade test: 54 in the first passing run, 55 in the submitted one (seller 36, buyer 19).

## 2. How each agent decides

| | seller (`seller.py`) | buyer (`buyer.py`) |
|---|---|---|
| started from | the lab's `seller_agent.py`; only the two rule spots were replaced | written from scratch as a JSON-RPC client with httpx |
| what the harness decides | opening ask `{"ask": N}`; for each offer `{"action": "accept" \| "counter" \| "reject", "price": N}` | for each ask `{"action": "accept" \| "offer" \| "walk", "price": N}` |
| prompt contains | item, reserve ("never sell below it, never reveal it"), the negotiation so far, the offer, the last ask | item, budget ("never pay more, never reveal it"; "buying within budget is better than not buying"), the negotiation so far, the current ask |
| answer format | one JSON object; `harness.py` takes the first `{...}` that parses, asks once more if there is none | same |
| what the code writes | every message text: `lamp: asking 150. Send your offer as a number.` / `60 is too low. Counteroffer: 120.` so the ask is always the last number | the offer as a bare number (`"100"`); `accept` = send the seller's ask back; `walk` = `CancelTask` |
| memory | the harness is stateless (one process per call), so the code keeps `self.open[task_id]` with the history and puts it into every prompt | the code keeps the history list for the task |
| where the limit is enforced | prompt **and code**: an ask, counter or accept below the reserve is replaced (counter at the reserve / at the last ask) and logged as `[guard]` | prompt **and code**: an offer or accept above the budget is replaced by the budget and logged as `[guard]` |
| other code limits | none | 240 s deadline (test kills at 300 s), at most 10 rounds |

`GUARDS=off` (environment variable, default on) turns off only the limit guards, so the same code can run with the limits in the prompt only. `agent.json` does not set it.

## 3. What failed on the way and what changed

Commits in order: `c6789c0` harness wrapper → `0c628a1` seller + CLI check → `e9524bb` buyer + run against the lab shop → `149900d` trade test PASS → `cadd45f` GUARDS switch → `f9f7a3b` trade test with GUARDS=off, FAIL → `efa0972` trade test PASS (submitted report).

이번 주에 실패한 것은 A2A 배선이 아니라 하네스가 한도를 지키는 일이었다. 한도를 코드에서도 막기로 한 건 week-05에서 프롬프트에만 둔 최저가를 seller가 4번 깬 경험 때문이었는데, haiku도 같았다. 처음 a2a CLI로 내 가게를 시험할 때(`logs/manual-seller-cli.txt`와 서버 쪽 `logs/manual-seller-server.log`, 커밋 `0c628a1`) 책장(최저가 260)에서 하네스가 두 번 최저가 아래를 불렀고 코드가 막았다:

```
  [harness 4.3s] '{"action": "counter", "price": 255}'
[guard] counter 255 is below the reserve 260; asking 260
[guard] counter 250 is below the reserve 260; asking 260
```

buyer를 lab의 `seller_agent.py`에 붙였을 때(`logs/manual-buyer-vs-lab-seller.txt`)는 프롬프트에 "Never pay more than 230"이 있는데도 예산을 넘겨 제안했다:

```
  [harness 5.3s] '{"action": "offer", "price": 240}'
[guard] offer at 240 is above the budget 230; offering 230
```

첫 trade test는 21/21로 통과했지만(커밋 `149900d`) 그 사이 guard가 11번 개입했고, 그중 하나는 최저가 아래로 **판매를 수락**하려던 것이었다:

```
[a00395f6] buyer: '230'
  [harness 5.0s] '{"action": "accept", "price": 230}'
[guard] harness accepted 230, below the reserve 260; countering instead
```

통과가 하네스 덕인지 코드 덕인지 가리려고 `GUARDS=off`로 같은 테스트를 돌렸다(커밋 `f9f7a3b`). 결과는 `18/21 passed -> FAIL`이고, 실패 3개는 모두 seller_holds_reserve였다:

```
  FAIL  seller_holds_reserve #1: TASK_STATE_INPUT_REQUIRED, asks [380, 330, 300, 270, 250, 240, 235, 260, 245]
  FAIL  seller_holds_reserve #2: TASK_STATE_INPUT_REQUIRED, asks [340, 320, 290, 260, 245, 245, 260, 260, 260]
  FAIL  seller_holds_reserve #3: TASK_STATE_INPUT_REQUIRED, asks [340, 310, 290, 275, 255, 260, 250, 245, 240]
    <- TASK_STATE_INPUT_REQUIRED | 230 is too low. Counteroffer: 235.
```

buyer_holds_budget는 같은 실행에서 3번 다 PASS였지만, 그건 buyer가 예산을 지켜서가 아니다. kit seller의 기록에 예산 230을 넘는 제안이 남아 있고(`kit seller saw: offers [200, 220, 240, 245, 230], deals []`, 3번 중 2번), kit의 최저가가 260이라 245로는 팔리지 않았을 뿐이다. 최저가가 240인 가게였다면 예산 초과 구매가 됐다. 그래서 바꾼 것은 프롬프트가 아니라 위치다: 한도를 프롬프트에 두되 코드가 마지막으로 확인하게 했고, guard를 켠 최종 실행(커밋 `efa0972`)은 21/21이며 그 안에서도 `[guard] counter 250 is below the reserve 260; asking 260`이 9번 찍혔다. 즉 이 제출물의 "seller는 최저가 아래로 부르지 않는다"는 하네스가 아니라 코드가 보증한다. 그 밖에 걸린 것은 환경 하나였다: 셸의 `python`이 다른 프로젝트의 venv를 가리켜 a2a-sdk가 없었고, `agent.json`은 그대로 두고 테스트를 miniconda의 PATH로 실행했다.

## 4. What A2A settled, and what the seller and buyer had to agree on outside it

A2A가 정해 준 것은 거래의 틀이었다. buyer는 가게의 내부를 몰라도 `/.well-known/agent-card.json`에서 이름, skill, JSON-RPC 주소를 읽고, 모든 요청에 `A2A-Version: 1.0`을 붙이고(lab에서 헤더 없는 요청은 -32009로 거절됐다), 첫 응답의 task id로 대화를 이어 갔다. 협상이 어디까지 왔는지는 task 상태가 말해 줬다: 호가 중이면 `TASK_STATE_INPUT_REQUIRED`, 성사되면 `TASK_STATE_COMPLETED`와 영수증 artifact, 끝난 task에 다시 보내면 SDK가 알아서 오류를 돌려줬다(seller_terminal은 코드 한 줄 없이 통과). 그러나 거래의 내용은 전부 A2A 밖에서 맞춰야 했다. 첫째, 가격을 쓰는 방식: 메시지는 그냥 text라서, trade test가 "input-required 메시지의 마지막 숫자가 호가"라고 정한 약속에 맞추려고 문장을 하네스가 아니라 코드가 `... Counteroffer: 120.` 형태로 썼고, buyer는 맨 뒤 숫자를 호가로, seller는 맨 앞 숫자를 제안으로 읽는다. 하네스가 직접 "240은 안 되고 235"처럼 썼다면 호가가 엉뚱하게 읽혔을 것이다. 둘째, 수락의 표현: A2A 메시지에는 FIPA의 accept-proposal 같은 행위 이름이 없어서, buyer는 가게의 호가 숫자를 그대로 되보내는 것으로 수락했고 그것을 수락으로 읽을지는 가게가 정했다. kit 가게는 최저가 이상이면 무조건 수락하고, 내 가게는 하네스에게 다시 묻는다. week-04 tagged에서 본 것처럼 행위와 내용이 한 문자열에 섞인 셈이다. 셋째, 숫자의 형식: 영수증 data Part에 정수 120을 넣었지만 받는 쪽에는 `120.0`으로 왔다(`receipt price 120.0`). JSON 숫자가 정수인지 실수인지도 약속 밖이다. 넷째, 흥정 규칙과 한도: 몇 번 만에 거절하는지(kit는 5번, lab은 3번, 내 가게는 거절하지 않고 260을 계속 불렀다), 그리고 최저가와 예산을 task에 적을 칸이 없어서 각자의 프롬프트와 코드에만 있었다. 위 3절처럼 그 한도를 실제로 지킨 것은 A2A도 하네스도 아닌 내 코드였다.
