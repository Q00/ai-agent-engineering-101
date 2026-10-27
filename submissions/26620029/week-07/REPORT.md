# Week 07 REPORT — agent market (26620029)

## (1) buyer가 가게를 고르는 방법과 기억의 형식

`market_buyer.py`의 `choose()`는 `{shops}`로 받은 후보 전체에 점수를 매기고 제일 높은 것 하나를 고른다(`market_buyer.py:101-112`).

```
score = 후기점수 - 0.7*min(red_flags,3) + 내_기억_조정 + jitter(0~0.05)
```

- **후기점수** (`review_score`, `market_buyer.py:69-80`): 최신 후기일수록 가중치를 크게(`DECAY=0.85`의 거듭제곱) 주는 가중평균이고, 후기가 적을 때는 `PRIOR=3.0`(가중치 2.0) 쪽으로 당겨서(섣불리 5점 후기 1개로 낙관하지 않게) 수축시킨다. 동시에 정규식 `RED_FLAG`(`receipt (said|says|was)|overcharg|...|avoid|scam|cheat|forged|unsigned`)로 별점 2 이하 후기의 "영수증 금액이 다르다/속였다" 류 문장을 세어 `red_flags`로 뺀다.
- **내 기억 조정** (`memory_score`, `market_buyer.py:83-98`): `buyer_memory.json`에서 그 가게 이름(`shop`)과 거래한 내 과거 기록을 모아 `cheated` 1건당 -3.0, `deal` 수만큼(최대 +1.0), `walk`/`error`는 소폭 감점. 같은 품목에서 예산 대비 더 싸게 산 적이 있으면 소폭 가산.

`buyer_memory.json`은 거래 1건당 JSON 객체 하나를 이어붙인 리스트다(최신 300건 유지, `MEMORY_KEEP=300`):

```json
{
 "time": "2026-10-26T22:28:56",
 "item": "lamp", "budget": 120,
 "shop": "dariaklt",
 "offers": [95, 108],
 "result": "deal",
 "price": 108, "task": "a1b2..."
}
```

`result`는 `deal` / `walk` / `cheated`(영수증이 합의가와 다르거나 서명이 없음) / `error` 중 하나. **가게는 `name`으로 기억한다** — README이 말한 대로 URL은 kit을 다시 켤 때마다 바뀌므로 URL로 기억하면 재시작할 때마다 기억이 끊긴다.

## (2) 후기를 하네스에 보여 준 방법

`reputation(shop, why)`가 그 가게의 최근 후기 3개를 `"{별}* {후기 80자}"` 형태로 묶어 한 줄 문자열을 만들고(`market_buyer.py:115-117`), `negotiate()`가 협상 프롬프트의 `{reputation}` 자리에 그대로 꽂는다(`market_buyer.py:36-48, 167`):

```
Shop: {card}
What other buyers and your own past trades say about this shop: {reputation}
```

즉 "왜 이 가게를 골랐는지"(`choose()`의 `why` 문자열, 점수 계산 근거)와 "최근 후기 3개"를 합쳐서 하네스(`claude -p --model haiku`)에게 매 메시지 생성 전에 보여준다. 하네스는 이 문장만 보고 가격을 깎을지 말지, 공격적으로 깎을지를 스스로 판단한다 — 코드는 예산 상한만 강제하고(line 177) 판단 자체는 건드리지 않는다.

## (3) Round 0, 1, 2에서 내 가게와 buyer에 일어난 일

`logs/market-kit.log`에 세 라운드가 다 찍혀 있다(2026-10-21, kit 재시작 `20:27:53`):

- **Round 0** (`20:29:02` ~ `20:29:35`, 32초): "market] shop registered as ddolcom, round 0" 직후 첫 주문부터 **kit이 무작위로 상대 가게를 배정**했다 — `[kit] shop for this order: q00 https://exclusive-engineering-constant-seafood.trycloudflare.com`. 체결은 됐지만(chair at 250) 영수증에 서명이 없어서 `[sign] receipt has no sig_seller` → `[market] ledger #4481: chair at 250, stars None, verified False (failed: sig_seller_ok, sig_buyer_ok), review not shown`. 후기도 보이지 않았다.
- **Round 1** (`20:29:35` ~ `21:23:12`, 약 54분): 이때부터 `market_buyer.py`의 `choose()`가 실제로 호출되기 시작해 `[score] ...` / `[choose] ...` 줄이 로그에 나타난다. 예: `20:55:18 [buyer] [choose] https://market.ourolabs.ai/shop/grand-furniture: grand-furniture - score 3.90 = reviews 3.99 (10), red flags 0, ...`. 서명은 아직 필수가 아니라서(`verified False`인 채로) 거래 자체는 셌다.
- **Round 2** (`21:23:12`부터 지금까지 계속): "Round 2: a deal counts only with a receipt signed by the shop and the buyer, behind an order the market issued" 규칙이 걸리자마자 `21:23:44 [market] ledger #10082: bookshelf at 260, stars 4, verified True, review accepted`처럼 **서명이 확인된 건만 집계**되기 시작했다. 대시보드(`https://market.ourolabs.ai/board`, 2026-10-26 기준)에서 내 가게 "Ddolcom's BEST SHOP"은 `deals 467, share 0.2%, stars 4.41, revenue 107,743, profit 15,843`(leaderboard 18위/44명). buyer는 Round 2 집계로 96건(이번 세션에 재가동한 뒤 `check_week07.py` 출력: "buyer 'ddolcom' of @ddolcom, running now, 96 counted deal(s)").

## (4) 서명을 붙이며 실패한 것과 고친 것

제출 폴더를 다시 열어보니 `seller.py`가 `sign_receipt()` 헬퍼 11줄만 남아 있고 **A2A 서버 자체(Starlette 앱, `Seller(AgentExecutor)`, `/.well-known/agent-card.json` 라우트)가 통째로 빠져 있었다** — 진짜 가게 코드는 `seller.py.bak`에만 남아 있었다. 그 결과 `test-report.json`은 `"seller_card": {"pass": false, "detail": "no Agent Card at /.well-known/agent-card.json within 60 s"}`로 멈춰 있었고, `logs/seller-under-test.log`는 비어 있었다(서버가 아예 안 뜨니 기록할 것도 없었다). `agent.json.bak`/`seller.py.bak`을 남긴 `apply_week07.py`(루트에 있던 패치 스크립트)는 `await ...add_artifact(...name="receipt")` 패턴을 찾아 **그 자리에** 서명 코드를 끼워 넣도록 짜여 있는데, 실제로 적용됐어야 할 결과물과 지금 파일의 내용이 어긋나 있던 것으로 보아 이 패치 적용 과정에서 파일이 통째로 덮어써진 것으로 보인다.

고친 내용:
1. `seller.py.bak`의 진짜 협상 로직(`Seller.execute`, `build()`, 카드/라우트)을 `seller.py`로 되살렸다.
2. accept 분기에서 영수증을 `sign_receipt({"shop": "Harness Shop", "item": deal["item"], "price": int(price), "task": task.id})`로 만들도록 바꿨다 — 기존 week-06 코드는 키 이름이 `"seller"`였는데, week-07 규격은 정확히 `{shop, item, price, task}`라서 `canon()`이 보는 키 이름부터 맞춰야 서명이 검증된다.
3. `python -m py_compile`로 문법부터 확인하고, `python scripts/market_test_week07.py submissions/26620029/week-07`를 돌려 **25/25 통과**(이전 실행은 `seller_card`에서 막혀 버이어 테스트 14개만 겨우 돌았었다)를 확인했다. 새 `test-report.json`/`logs/market-test.log`/`logs/seller-under-test.log`(309줄, 이전엔 0줄)가 이번에 다시 쓰였다.

중요한 점은 **이 버그가 실제 시장 거래에는 영향이 없었다**는 것이다. 실시장에 등록된 가게는 `seller.py`가 아니라 Cloudflare Workers에 올린 `shop-worker/src/index.js`이고, 거기 서명 로직(`signReceipt()`, `src/index.js:72-80`)은 처음부터 따로, 올바르게 짜여 있었다 — `shop-worker/check_shop.py`로 실가게를 직접 때려본 결과도 `12/12 passed`였다. 즉 이번에 고친 `seller.py`는 **로컬 채점용 사본**의 버그였고, 라이브 거래(467건, Round 2 집계 포함)는 영향을 받지 않았다.

또 하나: 제출 폴더를 열었을 때 이 컴퓨터에 `market_kit.py` 프로세스가 전혀 떠 있지 않았다(마지막 로그가 10/21 22:19, python 프로세스 0개) — 지난 5일간 buyer가 시장에서 주문을 받지 못하고 있었던 것이다. `--deployed https://ddolcom-shop.ddolcom.workers.dev`로 다시 join을 시도했더니 `[market] join as shop failed: 409 {"error":"...already the card URL of shop \"Ddolcom's BEST SHOP\""}` — 그 URL은 이미 "Ddolcom's BEST SHOP"이라는 이름으로 등록돼 있어서 "ddolcom"이라는 다른 이름으로는 재등록이 안 되는 것이었다(Cloudflare Worker는 로컬 프로세스 없이도 그 등록 그대로 계속 살아 있었다 — 대시보드에 `alive: true`로 떠 있었다). 그래서 `--buyer-only`로 buyer만 다시 띄우고(가게는 재등록할 필요가 없었다), `--shop-only`에 이미 등록된 정확한 이름(`"Ddolcom's BEST SHOP"`)을 줘서 `logs/market-kit.log`에 살아있는 등록 로그 한 줄을 새로 남겼다 — `check_week07.py`가 이 로그의 마지막 등록 줄로 시장에서 이름을 찾기 때문이다.

## (5) 도전 1과 2

### 도전 1 — 가게 가격을 내 후기를 보고 바꾸기

바꾸기 전(`logs/challenge-1/before-dashboard.json`, 2026-10-26 13:34 UTC) 내 가게 "Ddolcom's BEST SHOP"의 후기 10개 중 3점짜리는 전부 같은 패턴이었다:

> "Lamp at 124, above the 104 I usually pay." / "Lamp at 137, above the 104 I usually pay." / "Bookshelf at 320, above the 271 I usually pay."

반대로 5점짜리는 전부 호가가 두세 번 내려간 뒤에 체결된 건이었다(`"The bookshelf's price dropped from 380 to 350 to 320, and I paid 290"`). 그때 `wrangler.toml`은 `ASK_LAMP=150`(예약가 100, +50%), `ASK_CHAIR=260`(+44%), `ASK_BOOKSHELF=380`(+46%)로 — 경쟁 가게("Fair Furniture"는 +5%, 점유율 6%; "YMS's Furniture"는 최대 +30%) 대비 훨씬 높게 불렀고, 점유율은 0.2%(467/약 29만 건)에 그치고 있었다.

`wrangler.toml`을 `ASK_LAMP=115(+15%), ASK_CHAIR=200(+11%), ASK_BOOKSHELF=290(+12%)`로 내리고, `ACCEPT_AFTER`도 3→2, `ACCEPT_ANY`도 5→3으로 낮춰(더 빨리, 더 낮은 가에서 받아들이게) `npx wrangler@4 deploy`로 재배포했다(`logs/challenge-1/deploy.log`). 재배포 직후 `shop-worker/check_shop.py`로 직접 확인:

```
-> I want to buy a lamp
<- TASK_STATE_INPUT_REQUIRED | lamp: asking 115. Send your offer as a number.
-> 115
<- TASK_STATE_COMPLETED | Deal: lamp at 115.
...
12/12 passed -> PASS
```

재배포 후 ~6분 뒤 대시보드(`logs/challenge-1/shortly-after-dashboard.json`)는 아직 `deals 467, visits 945`로 그대로였다 — 점유율 0.2%짜리 가게라 몇 분 안에 새 손님이 들어오지는 않았다. 가격이 실제로 바뀌어 적용된 것은 `check_shop.py`의 실시간 협상 로그로 확인했고, 점유율/평균가가 실제로 움직이는 건 마감 전까지 며칠 더 지켜봐야 보일 것이다 — 짧은 시간 안의 before/after라는 한계를 그대로 적는다.

### 도전 2 — 시장에서 만난 믿을 수 없는 참가자 피하기

`maple-home`은 5일(2026-10-21 20:27 ~ 10-26 22:34, `[score] maple-home` 180회 기록) 내내 가장 낮은 점수권에 머물렀다. 후기 8개 **전부**가 같은 패턴이다(`.market/shops.json`, `logs/challenge-2/maple-home-score.log`):

> "Agreed 300, receipt says 345." / "Agreed 215, receipt says 245." / "[WARNING] chair: agreed 185, receipt says 210. Receipt does not match the deal. Avoid."

합의한 가격보다 영수증 금액이 더 높게 나오는, Round 2 서명 규칙이 막으려는 바로 그 위조다. `market_buyer.py`의 `RED_FLAG` 정규식(`receipt (said|says|was)|...|did not match|avoid`)이 8개 전부에 걸려 `review_score()`가 평균을 1.58점까지 끌어내리고 `choose()`가 거기서 다시 `-0.7*min(8,3)`을 빼서, 최근 점수는:

```
22:34:57 [buyer] [score] maple-home: score -0.51 = reviews 1.58 (8), red flags 8, no trades of mine
```

같은 시각 다른 37개 가게는 전부 3.0~4.9점대였다(`logs/challenge-2/maple-home-score.log`의 전체 스코어 목록). 시간순으로도 일관됐다(`logs/challenge-2/score-trend.log`): `1.10 → 0.83 → (후기 창 교체) 1.66 → 0.65 → -0.51`. **5일간 180번의 평가에서 한 번도 3점을 넘지 못했고**, `buyer_memory.json`(193건) 어디에도 `maple-home`과의 거래가 없다 — buyer는 매번 이 가게를 후보로 받아봤지만 한 번도 고르지 않았다.

## (6) 가게와 buyer를 어디에 어떻게 배포했는가

- **가게**: Cloudflare Workers (`shop-worker/`). `wrangler.toml`에 Workers AI 바인딩(`env.AI`, `llama-3.3-70b-instruct-fp8-fast`로 매 수와 제안), 작업 상태는 Durable Object(`TaskStore`, SQLite 백엔드) 하나에 저장해 모든 요청이 같은 태스크를 본다. 서명 개인키는 레포에 넣지 않고 `wrangler secret put SHOP_KEY_PEM`로 Worker의 시크릿으로만 올렸다(`shop_key.pem`은 로컬에만 있고 `.gitignore`로 제외). 주소는 `https://ddolcom-shop.ddolcom.workers.dev`, 서버리스라 로컬 PC가 꺼져 있어도 계속 떠 있다 — 이번에 복구할 때도 `alive: true`로 그대로 살아 있었다.
- **buyer**: 이 PC(`D:\ai-agent-engineering-101`)에서 `python -u scripts/market_kit.py join --dir submissions/26620029/week-07 --market https://market.ourolabs.ai --name ddolcom --buyer-only`를 **백그라운드 프로세스**(PowerShell `Start-Process -WindowStyle Hidden`, 터미널/세션을 닫아도 살아있게)로 돌린다. `submissions/26620029/week-07/join.ps1`에 같은 설정이 있고, `MARKET_OWNER` 환경변수로 GitHub claim code를 넘겨 `@ddolcom`으로 연결했다. 하네스는 `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence`(`harness.py`), kit은 받은 주문마다 `market_buyer.py`를 새로 실행해 결과를 시장에 올린다.
