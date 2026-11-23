# REPORT.md

## 1. Harness, model, command

- **Harness**: Claude Code headless. 두 에이전트 모두 결정이 필요할 때마다 `harness.py`가 다음 명령을 자식 프로세스로 띄우고, 프롬프트를 stdin으로 넣고 stdout의 첫 JSON 객체를 읽는다.
  ```
  claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence
  ```
  모델은 CLI 별칭 `haiku`(= claude-haiku-4-5), Claude Code 2.1.273. README대로 **빈 임시 디렉터리**에서 실행해 프로젝트 지시문을 읽지 않는다. 바이너리는 `$CLAUDE_BIN` → PATH의 `claude` → VS Code 확장에 번들된 바이너리 순으로 찾는다(이 서버에는 PATH에 없고 번들만 있다). 호출당 4–7초.
- **A2A 쪽**: 판매자는 a2a-sdk 1.2.2 `AgentExecutor`/`TaskUpdater`(랩의 `seller_agent.py` 뼈대), 구매자는 httpx로 직접 보내는 JSON-RPC(`SendMessage`, `CancelTask`, 카드 GET). SDK 1.2.2의 클라이언트는 protobuf 기반이라 헤더·taskId를 투명하게 다루려고 trade test와 같은 raw 방식을 택했다.
- **실행** (`agent.json` 그대로; 작업 디렉터리는 제출 폴더):
  ```
  python seller.py --port {port}
  python buyer.py --url {url} --item {item} --budget {budget}
  python run_challenges.py 1   /   python run_challenges.py 2
  ```
  `python`은 a2a-sdk·httpx·uvicorn이 깔린 3.10이어야 한다(이 서버는 PATH 첫 번째 python3가 3.11이라 `PATH=/opt/conda/bin:$PATH`로 돌렸다). trade test: `python scripts/trade_test_week06.py submissions/26510125/week-06` → 21/21 PASS(`test-report.json`, `logs/trade-test.log`).

## 2. How each agent decides

**판매자 (`seller.py`)** — 하네스가 정하는 것 세 가지, 코드가 지키는 것 두 가지.

| 결정 | 프롬프트 요지 | 답 형식 |
|---|---|---|
| 어떤 물건인가 | 구매자 문장 + 카탈로그 검색으로 좁힌 후보(id·이름·설명 최대 8개) | `{"id": "..."}` 또는 `{"id": null}`. 이름을 정확히 말하면(`lamp`) 후보가 1개라 하네스를 부르지 않음 |
| 첫 호가 | 물건·설명·하한가(reserve), "하한가 아래로 부르거나 팔지 말 것, 분명히 더 받을 것" | `{"ask": N}` |
| 제안에 대한 답 | 하한가, 지금까지의 (호가, 제안) 이력, 현재 호가, 새 제안, 라운드 n/6 | `{"action":"accept"}` / `{"action":"counter","price":N}` / `{"action":"reject"}` |

메시지 **텍스트는 코드가 조립**한다 — `"lamp: asking 150."`, `"80 is too low for the lamp. Counteroffer: 120."` — trade test가 "입력요구 메시지의 마지막 숫자"를 호가로 읽기 때문에, 숫자가 하나만, 맨 끝에 오게 했다. 성사 시 영수증은 data Part `{"seller","item","item_id","price"}`로 artifact에 붙이고(`price`는 구매자의 제안), 끝난 task에 오는 메시지는 SDK의 `DefaultRequestHandler`가 오류로 돌려준다(`seller_terminal` PASS).

**하한가는 프롬프트에도 있고 코드에서도 강제**한다: (a) 첫 호가 `max(ask, reserve)`, (b) 하네스가 하한가 아래 제안을 accept하면 하한가로 counter로 바꿈, (c) counter 가격이 하한가 아래면 하한가로 올림. 하네스가 JSON을 안 주면 랩의 규칙(1.3배 호가, 중간값 역제안)으로 떨어지고 `fallbacks`에 센다. trade test에서는 가드도 폴백도 **0회**였다(`logs/seller-under-test.log`에 `[guard]` 없음).

**구매자 (`buyer.py`)** — 카드를 읽고 JSON-RPC 주소를 고른 뒤 `I want to buy a {item}`으로 열고, 모든 요청에 `A2A-Version: 1.0`, 이후 메시지에 `taskId`. 호가(마지막 숫자)를 읽을 때마다 하네스에 묻는다: 예산, (호가, 내 제안) 이력, 현재 호가, 라운드 n/7 → `{"action":"accept"}` / `{"action":"offer","price":N}` / `{"action":"walk"}`. accept는 **호가를 그대로 숫자로 보내는 것**이고(kit 판매자는 reserve 이상이면 닫는다), walk는 `CancelTask` 뒤 종료. 최대 8메시지. **예산은 코드에서도 강제**: 예산 초과 호가를 accept하면 예산을 제안으로 바꾸고, 제안이 예산을 넘으면 깎는다. 역시 폴백 규칙이 있고 trade test에서 0회.

## 3. What failed on the way, and what changed

trade test 자체는 첫 실행에 21/21 통과했다. 실패는 그 전과 옆에서 났다.

1. **카탈로그 검색이 틀린 품목을 올렸다** (챌린지 1). 첫 구현은 이름·설명 토큰 겹침만 셌는데, 점검 스크립트에서
   ```
   'something wooden to keep about 40 books, not taller than my desk' -> 5 candidate(s)
      I0592 'compact oak desk' reserve=275
      I0593 'compact walnut desk' reserve=425
   ```
   처럼 "desk"가 "books"보다 세게 매칭돼 책상만 후보에 올랐다 — 이대로면 하네스가 책장을 고를 수조차 없다. 고친 것: 동의어(`books→bookshelf`, `light→lamp`, `wooden→oak/pine/…`), 품목어 가중치(+4), `my/than/like` 뒤의 품목어는 방(환경) 설명으로 보고 감점, 그리고 후보 8개를 **품목별로 최대 절반씩** 섞어 하네스가 종류를 고를 수 있게 했다. 수정 후 같은 질의는 `kinds {'bookshelf': 4, 'desk': 4}; top: compact oak bookshelf …`가 됐다.
2. **하네스가 JSON 뒤에 설명을 붙인다.** 예: `'```json\n{"action": "offer", "price": 105}\n```\n\nThe seller is asking 130, which exceeds my 120 budget…'`(`logs/buyer-under-test-buyer_deal-2.log`). "JSON만"이라고 해도 haiku는 자주 근거를 덧붙인다. 파서를 "첫 번째 `{…}`"로 두어 영향은 없었지만, 답 형식은 프롬프트가 아니라 파서가 지켰다.
3. **환경**: PATH의 `python3`가 3.11(새 conda env)이라 a2a-sdk·httpx가 전부 없다고 나왔다. 패키지는 3.10 user-site에 있었고, `/opt/conda/bin/python`(3.10)에 `a2a-sdk[http-server]`를 설치해 그걸로 돌렸다. `agent.json`의 `python`은 이 전제를 깔고 있다(1절).
4. **a2a-sdk 1.2.2 클라이언트**는 `send_message(request: a2a_pb2.SendMessageRequest)`처럼 protobuf 타입을 받아 헤더·taskId 제어가 불투명했다. 구매자를 raw JSON-RPC로 바꿨다(실패라기보다 설계 변경).
5. **챌린지 2의 판정기가 성공한 런을 FAIL로 찍었다.** 구매자 로그의 마지막 줄은
   ```
   [summary] bought {'stool': 73.0, 'lamp': 95.0, 'mirror': 121.0, 'chair': 271.0, 'bookshelf': 340.0} spent 900.0 of 900; missing []
   ```
   인데, `run_challenges.py`의 정규식이 `spent (\d+)`로 정수만 기대해 `900.0`에 매치되지 않았고, 그 결과 `bought {}, spent 0, missing [전부]`로 FAIL이 기록됐다(`logs/challenge-2/summary.txt` 첫 줄). 가격이 A2A data Part의 JSON 숫자로 와서 float인 것을 잊은 탓이다. 정규식을 `(\d+(?:\.\d+)?)`로 고치고, 상점 20개를 다시 띄우는 대신 **같은 로그를 다시 판정**해 두 번째 줄에 PASS를 덧붙였다(두 줄 다 남겨 둠).

## 4. What A2A settled, and what we still had to agree on outside it

A2A가 정해 준 것: 상대를 **찾는 법**(`/.well-known/agent-card.json`과 `supportedInterfaces`의 JSON-RPC 주소), **일의 단위와 이어가기**(task와 `taskId`), **어디까지 왔는지**(`INPUT_REQUIRED` → `COMPLETED`/`REJECTED`/`CANCELED`, terminal task에 메시지를 보내면 오류), **결과를 돌려주는 자리**(artifact의 data Part), 그리고 **버전 협상**(`A2A-Version: 1.0`). 구매자 코드는 판매자 안에 무엇이 있는지 — 모델, 프롬프트, 카탈로그 검색, 하한가 가드 — 전혀 몰라도 거래를 끝냈다. 반면 4주차 FIPA의 `performative` 같은 **행위 이름은 없다**. "105"가 수락인지 역제안인지는 받는 쪽이 글을 읽고 정하고, 그래서 trade test와 우리 두 에이전트는 프로토콜 밖에서 몇 가지를 따로 맞춰야 했다: 첫 메시지가 품목을 **이름 또는 설명**으로 담는다는 것, **가격은 메시지의 마지막 숫자**라는 것(판매자 메시지를 코드가 그 형식으로 조립한 이유), **수락은 호가를 그대로 숫자로 보내는 것**이고 판매자는 하한가 이상이면 닫는다는 것, 영수증의 data Part에 `price`가 있다는 것, 라운드 수. 그리고 강의가 짚은 대로 A2A에는 **위임의 조건을 적을 칸이 없다** — 예산도 하한가도 task 어디에도 없고, 각자의 프롬프트와 코드 가드 안에만 있다. 거래가 잘못됐을 때 밖에서 볼 수 있는 증거도 task 이력과 artifact뿐이다.

## 5. Challenges 1 and 2

**챌린지 1 — 하네스가 카탈로그를 보는 방법** (`logs/challenge-1/`). 카탈로그는 `make_catalog.py`가 만든 2,355개(이름·설명·하한가; 12종류 × 형용사 × 재질). 턴마다 전부를 프롬프트에 넣는 대신 **코드가 후보를 최대 8개로 좁히고**, 하네스는 그 후보의 id·이름·설명을 보고 하나를 고른다(`{"id": ...}`). 좁히는 규칙: 이름을 정확히 말하면 그 1개(하네스 호출 없음 — trade test의 `lamp`가 이 경로다); 아니면 토큰 겹침 + 동의어(`books→bookshelf`, `light→lamp`, `wooden→oak/pine/…`) + 품목어 가중치, `my/than` 뒤의 품목어("than my desk")는 감점, 그리고 **품목별 최대 절반**으로 섞어 종류 선택을 하네스에 남긴다. 네 요청 모두 맞는 종류를 골랐고 호가·성사가 그 품목 하한가 이상이었다(`summary.txt` 4/4 PASS):
```
[d852674e] <- 'something wooden to keep about 40 books, not taller than my desk'
    [harness] -> '{"id": "I0399"}  … compact bamboo bookshelf (I0399) is the best fit: it's wooden, holds about 150 books …, and at 75 cm tall is comfortably below typical desk height'
[d852674e] item='compact bamboo bookshelf' (harness pick, reserve 180) opening ask 270
[293f01c6] <- 'a high seat for the kitchen counter, metal if possible'
    [harness] -> '{"id": "I1409"} … I1409 is the only option that specifically lists the kitchen counter as its intended use AND offers metal'
[293f01c6] item='low aluminum stool' (harness pick, reserve 45) opening ask 65
```
요청당 하네스 호출은 품목 선택 1 + 첫 호가 1 + 제안마다 1이었다. 네 번째 줄은 눈여겨볼 만하다: 이름은 "low … stool"인데 설명(110 cm, kitchen counter, aluminum)을 읽고 골랐다 — 하네스는 이름보다 설명을 믿었고, 그래서 검색이 후보에 넣어 주기만 하면 됐다. 반대로 3절 1번처럼 **후보에 없으면 하네스도 못 고른다**: 좁히기 단계가 곧 상한선이다.

**챌린지 2 — 협상마다 상태를 들고 다니는 방법** (`logs/challenge-2/`). `run_challenges.py 2`는 상점 20개(`shops/shop_NN.json`: 서로 다른 품목 2–4종, 가격 배수 0.8–1.4)를 띄우고 `directory.json`(카드 주소 20개)을 쓴다. 구매자는 카드 20장을 읽고 하네스에 **skill·tag만 보여 주고** 품목별 상점과 예산 분배를 받는다(DF를 쓰는 장면):
```
[directory] 20 cards read for list ['lamp', 'chair', 'bookshelf', 'stool', 'mirror'], total budget 900
[plan] lamp <- http://127.0.0.1:9101 with budget 180   … (5품목 모두 180씩, 태그가 맞는 상점)
```
협상 5개는 스레드로 **동시에** 진행하고, 각 협상의 상태는 transcript가 아니라 작은 dict 하나 — `{item, shop, budget, ask, history[(ask, my offer)], rounds, status, task_id, price}` — 로 들고 다닌다. 하네스가 라운드마다 보는 것은 이 dict에서 뽑은 다섯 줄("history: ask 310 / my offer 250; ask 280 / my offer 265 …, the seller now asks 275")뿐이라 상점이 20개여도 프롬프트 길이는 일정했다. 끝난 협상은 상태 한 줄로 요약돼 집계된다:
```
[state] {"item": "stool", "shop": "http://127.0.0.1:9104", "status": "bought", "price": 73.0, "rounds": 3, "ask": 73}
[state] {"item": "bookshelf", "shop": "http://127.0.0.1:9103", "status": "rejected", "price": null, "rounds": 6, "ask": 260}
[state] {"item": "chair", "shop": "http://127.0.0.1:9102", "status": "rejected", "price": null, "rounds": 5, "ask": 215}
[retry] chair at another shop http://127.0.0.1:9104 with the remaining 611.0
[retry] bookshelf at another shop http://127.0.0.1:9102 with the remaining 340.0
[summary] bought {'stool': 73.0, 'lamp': 95.0, 'mirror': 121.0, 'chair': 271.0, 'bookshelf': 340.0} spent 900.0 of 900; missing []; harness calls 33 fallbacks 0
```
통과 조건 셋을 다 지켰다: 5품목 구입, 지출 900 ≤ 900, 상점 로그의 영수증 5개로 중복 없음(`summary.txt` 재판정 줄). 두 가지가 눈에 띈다. (a) **카드에는 가격이 없다.** 하네스는 태그로 상점을 골랐고 예산을 180씩 균등 분배했는데, 그 상점의 의자·책장 하한가는 214·309라 둘 다 거절당했다 — 얼마에 파는지는 협상을 해 봐야만 드러났다. (b) 2차 패스는 **태그가 맞는 첫 번째 다른 상점**을 골랐지 가장 싼 상점을 고른 게 아니다(의자는 shop_09가 153부터, 책장은 shop_08이 218부터 팔 수 있었다). 그래서 남은 예산 611로 의자 271, 남은 340으로 책장을 **정확히 340**에 사며 예산을 끝까지 썼다. 예산 초과는 한 번도 없었고 코드 가드도 울리지 않았지만, A2A의 발견(discovery)이 "무엇을 파는가"까지만 알려 주는 한, 가격을 아끼는 일은 디렉터리가 아니라 협상 순서와 재시도 전략의 몫이라는 것이 이 실행의 결론이다.
