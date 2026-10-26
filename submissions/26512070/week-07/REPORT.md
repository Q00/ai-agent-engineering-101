# Week 07 — 에이전트 시장: 평판과 검증

## 요약

| 항목 | 내용 |
|---|---|
| 학번 / 시장 닉네임 | 26512070 / `김관우` (GitHub @gom31, claim code로 연동) |
| 하네스 / 모델 | Claude Code 2.1.283, `claude -p --model haiku` (내장 도구 끔), 6주차 ReAct 루프 그대로 |
| 가게 | 6주차 가게 + 영수증 `{shop, item, price, task}`에 Ed25519 `sig_seller` (LAB 2) |
| market buyer | haiku가 가게를 고르고, 코드는 기억(`memory.jsonl`)을 정리해 넘기고 속인 가게를 영구 차단 (LAB 1) |
| market test | **25/25 PASS** (`test-report.json`, `logs/market-test.log`) |
| 배포 | 집 PC(Windows 11)에서 `market_kit.py join --tunnel` 상시 실행 (cloudflared quick tunnel) |
| 시장 결과 (Round 2) | __R2_RESULT__ |

수업(실시간 Round 0, 1, 2)에는 참석하지 못했다. 시장에는 수업이 끝난 뒤 Round 2 규칙이 이미 적용된 상태에서 처음 들어갔다(2026-10-26 18:57). 그래서 (3)의 Round 0, 1은 내 에이전트의 경험이 아니라 시장 dashboard API(`/api/board?round=N`)의 수치로 적었다.

## (1) buyer가 가게를 고르는 방법과 기억의 형식

### 누가 무엇을 정하나

| 일 | 맡은 쪽 | 이유 |
|---|---|---|
| 가게 고르기 | **haiku** (`react.run` 한 턴, final action `pick(shop)`) | 후기 문장을 읽고 상황에 맞게 판단하는 일은 규칙보다 모델이 낫다 |
| 기억 정리 | **코드** | 모델이 기억을 다시 쓰게 하면 기록이 흐려진다. 사실(합의가, 영수증가)은 코드가 그대로 적는다 |
| 속은 가게 차단 | **코드** (haiku 후보 목록에서 제외) | "한 번 속으면 영구 차단"은 판단이 아니라 규칙이다 |
| 예산 지키기 | **코드** (6주차 `wallet.check_offer` + `guard`) | 프롬프트가 아니라 코드에서 막는다 (강의 요구) |
| 별점, 후기 | **코드** (`judge()`) | 다른 buyer가 읽을 후기는 의견보다 사실이 낫다: `Agreed 101, receipt says 117.` |

흐름 (`market_buyer.py`):

1. `{shops}`와 `memory.jsonl`을 읽는다.
2. `blocked(memory)`: 판정이 `cheated:`로 시작하는 기록이 한 번이라도 있는 가게 이름을 뺀다. 로그에 `[choose] blocked '<name>': cheated us before: ...`를 남긴다.
3. 남은 가게가 하나면 그 가게, 여럿이면 haiku가 고른다. 고른 이유를 `[choose] <url>: <name>: <이유>`로 출력한다(키트가 로그에 옮긴다).
4. 흥정은 6주차 buyer 그대로(`buyer.negotiate()`로 함수만 떼어 냄).
5. `judge()`가 결과를 판정하고 `memory.jsonl`에 한 줄 쓰고, 마지막 줄에 `{"shop", "receipt", "stars", "review"}`를 출력한다.

"속았다"의 정의(코드): 영수증 가격이 합의가(내 마지막 제안)보다 높다, 영수증 품목이 주문 품목과 다르다, 영수증 가격이 예산을 넘는다. 영수증이 없거나 `sig_seller`가 없는 것은 기록만 하고 차단하지 않는다(정직하지만 서명을 못 붙인 가게가 있다: (3)의 willow-goods).

### 기억의 형식: `memory.jsonl` (주문 하나에 한 줄)

```json
{"time": "2026-10-26T19:13:16", "shop": "elm-street", "item": "lamp", "budget": 125,
 "offers": [75, 118], "agreed": 118, "receipt_price": 118, "receipt_item": "lamp", "signed": true,
 "final_state": "TASK_STATE_COMPLETED", "verdict": "fair", "stars": 4,
 "review": "Lamp at 118, receipt matched the agreed price.", "why_picked": "elm-street has the highest rating ..."}
```

- 가게는 `name`으로 기억한다. URL은 가게가 키트를 다시 띄울 때마다 바뀐다(README). 내 로그에서는 jiseokkk가 22번 모두 같은 trycloudflare 주소였지만, 내 가게 주소는 키트를 다시 띄울 때마다 바뀌었다.
- `verdict`는 `fair` / `fair but unsigned` / `no deal (ask above budget)` / `no deal` / `no receipt on a completed deal` / `cheated: ...` 중 하나.
- 주의: market test도 같은 디렉터리에서 market buyer를 실행하므로, 테스트 가게(`Kit Shop`, `Kit Shop North/South`) 기록이 섞여 있다. 시장에 같은 이름의 가게가 없어서 선택에는 영향이 없다.

## (2) 후기를 하네스에 보여 준 방법

haiku에게는 가게마다 아래 세 덩어리를 준다. 시장 후기와 내 기록을 따로 보여 주고, 시스템 프롬프트에 "내 기록이 남의 후기보다 무겁다"고 적었다. (형식 예: `reviews_text()`와 `record_text()`가 만드는 모양, 값은 줄임)

```
* elm-street
  market reviews: 10, average 5.0 stars; newest first:
    - 5* 'Quick, fair deal on the lamp at 113; receipt matched.'
    - ...(최신 3개)
  your record: 2 order(s), 2 deal(s)
    - lamp, budget 140: agreed 118, receipt 118, 84% of budget, fair
    - lamp, budget 125: agreed 118, receipt 118, 94% of budget, fair
```

- **평균 별점 + 최신 후기 3개**: 처음에는 5개를 보여 줬다. 시장에 가게가 35곳이라 프롬프트가 길어져 haiku 호출이 30초 제한을 세 번 연속 넘었다((4) 참고). 3개로 줄이고 한 번에 120자로 자르고, 고르기 호출 제한을 60초로 늘린 뒤 16.7초에 끝났다.
- **내 기록에 "예산의 몇 %에 샀는지"**: "싸게 산 가게를 우선한다"를 숫자로 보이게 했다. 실제 이유 줄: `jiseokkk has my strongest track record: I've bought 4 bookshelves from them (10 deals total), all with receipts matching exactly ...`
- 차단된 가게는 아예 보여 주지 않는다. 모델이 "그래도 싸니까"로 다시 고를 여지를 없앴다.

market test `buyer_reads_reviews`는 3번 모두 좋은 후기 가게를 골랐다. 이유 줄: `Kit Shop North has multiple reviews showing receipt overcharging (dishonest); Kit Shop South has excellent reviews ...`

## (3) Round 0, 1, 2에서 일어난 일

수업에 없었으므로 Round 0, 1은 dashboard API의 라운드별 수치다. Round 2는 내 가게와 buyer가 직접 겪은 것이다.

### 시장 전체 (`/api/board?round=N`)

| 라운드 | 시장 거래 | 점유율 1위 | 눈에 띈 것 |
|---|---|---|---|
| R0 (기억, 후기 없음) | 1,252 | grand-furniture 9.1% | maple-home 5.4%로 4위. 키트가 무작위로 가게를 고르니 정직하지 않은 가게도 손님을 받는다 |
| R1 (아무나 후기) | 1,812 | corner-store 29.5% | maple-home 0.4%, 별점 3.0 (후기 12개). 후기가 생기자 바로 밀려났다 |
| R2 (서명한 영수증만) | 287,052 | ggari-shop 12.2% | maple-home 별점 1.0, 방문 278에 인정 거래 7. willow-goods(R0 2위, 6%)는 R2 인정 거래 0 |

WATCH 질문에 대한 답:

- **R1에 높았다가 R2에 내려온 가게**: corner-store 29.5% → 3.5%(R2 10위), outseop-26510122 15.5% → 2.3%. R0 1위 grand-furniture는 R2 39위(인정 거래 70).
- **정직하지만 서명을 붙이지 못한 가게**: willow-goods. 내 buyer가 Round 0 방식(6주차 buyer, LAB 1 전)으로 이 가게와 거래했을 때 영수증에 `sig_seller`가 없었다: `19:01:36 [market] ledger #843255: chair at 216, stars None, verified False (failed: sig_seller_ok, sig_buyer_ok), review not shown`. R2 점유율 0.
- **R2 상위 가게의 공통점**: 카드 설명이 "one message closes the deal", "Single reply", "answered in milliseconds by rule"이다. 규칙으로 즉시 답하고 시장 평균 이하로 부른다. 방문 대비 인정 거래가 0.7~0.8이다.

### 내 가게와 buyer (Round 2, 18:57부터)

| 시각 | 일 | 근거 (`logs/market-kit.log`) |
|---|---|---|
| 18:57 | 6주차 코드 그대로 입장 | `shop registered as 김관우, round 2` |
| 18:57~19:12 | buyer는 6주차 buyer라 후기를 안 남김. 가게는 서명 없는 영수증 | `lamp at 102, stars None, verified True, review not shown` (상대 가게는 서명함). 이 사이 내 가게의 성사 17건은 R2에 세지지 않았다 |
| 19:12 | LAB 1, LAB 2 적용 후 재입장 | `ledger #844648: lamp at 118, stars 4, verified True, review accepted` |
| 19:13~ | haiku가 후기와 내 기록으로 고름 | 위 (2)의 이유 줄들 |
| 20:11 | 키트가 통째로 꺼짐 (5분) | VS Code에서 Claude Code 세션이 다시 시작될 때 키트 프로세스가 같이 정리됨 |

__R2_NUMBERS__

## (4) 실패한 것과 고친 것

### 서명

서명 자체는 처음 실행에서 통과했다(market test `seller_signed_receipt` 3/3). 강의 자료의 두 함정을 미리 막았기 때문이다.

- **가격을 정수로 서명**: `int(price)`. A2A data Part는 숫자를 실수로 보내서 120이 120.0으로 도착한다. 실제로 다른 가게 영수증도 `"price": 102.0`으로 왔고 키트가 정수로 되돌려 확인했다.
- **키 경로를 파일 기준으로**: `shop_key.pem`을 `seller.py`가 있는 디렉터리 기준으로 연다. 키트와 market test는 seller를 이 디렉터리에서 실행하지만, 상대 경로는 실행 위치에 따라 깨진다.

서명이 없던 동안의 손실은 있었다: 18:57~19:12에 내 가게가 판 17건은 R2 점유율에 들어가지 않았다.

### 그 밖의 실패 (커밋에 그대로 남김)

| 실패 | 원인 | 고친 것 | 커밋 |
|---|---|---|---|
| 가게 고르기가 haiku 호출 3번 모두 시간 초과, 별점 최고 가게로 대체 | 시장 가게 35곳 x 후기 5개로 프롬프트가 길어져 30초 제한 초과 | 후기 3개, 120자로 자르기, 고르기 호출 60초 → 16.7초 | `3334a6a` |
| 키트가 5분간 꺼짐 | VS Code 세션 재시작 때 백그라운드 키트도 종료 | 재시작. 마감까지는 별도 프로세스로 띄워야 함 ((6) 참고) | – |
| 도전 1 첫 호가 변경(의자 234 → 210)을 되돌림 | haiku가 의자 협상 1건(구간 전체 9건)만 보고 결정 | 원래 값으로 되돌리고, 구간 거래 20건 미만 또는 품목 5건 미만이면 바꾸지 않는 규칙 추가 | `9cedc9a`, `33cdb3c` |
| 시스템 `py`에 a2a 패키지가 없음 | `agent.json`이 `py ...`를 쓰는데 Start-Process로 띄운 키트는 venv가 꺼져 있음 | `join.ps1`에서 `VIRTUAL_ENV`와 PATH를 venv로 설정 (`py` 런처는 `VIRTUAL_ENV`를 따른다) | – |

## (5) 도전 과제

### 도전 1: 가게 호가 바꾸기

방식: 후기에서 칭찬받은 점은 유지하고, 가격으로 고칠 수 있는 나쁜 점을 하나씩 고친다(`challenge1_tune.py`).

1. dashboard의 내 가게 수치, 내 가게 후기(`/api/shops`), 마지막 변경 이후 `trades/seller.jsonl`의 거래(결과, 품목별 평균가, 흥정 길이, 품절, 첫 호가에서 떠남)를 모은다. market test 실행 시간대의 거래는 뺀다.
2. haiku가 `keep`(칭찬받은 점)과 `problems`(가격으로 고칠 수 있는 문제)를 적고, **품목 하나의 list price 하나**를 바꾼다.
3. 코드가 막는 것: 하한가 고정, 하한가 이상, 한 번에 15% 이내, 구간 거래 20건 이상, 그 품목 5건 이상.
4. `inventory.json`에 쓰면 가게가 다음 거래부터 새 호가를 읽는다(재시작 없음, 재고는 판 만큼 그대로).

__CHALLENGE1__

### 도전 2: 정직하지 않은 참가자

**시장에서 만난 상대: maple-home.** 후기 8개가 모두 1점이고 내용이 같은 패턴이다(`logs/challenge-2/shops-snapshot.json`):

```
1* round 2: Agreed 300, receipt says 345.
1* round 2: Agreed 215, receipt says 245.
1* round 2: Agreed 120, receipt says 135.
1* round 1: [WARNING] chair: agreed 185, receipt says 210. Receipt does not match the deal. Avoid.
```

합의한 값보다 약 15% 높은 영수증을 낸다. 다른 33개 가게의 평균은 4.4점이다.

**내 buyer가 피한 방법 1: 후기 (시장).** `challenge2_evidence.py` 결과(`logs/challenge-2/market-evidence-*.txt`): market buyer가 처리한 주문 58건 모두 maple-home이 목록에 있었고, **한 번도 고르지 않았다.** 4건에서는 이유에 직접 적었다:

```
19:32:38 [choose] ... myeonggi: My budget is 90. I need to avoid maple-home (dishonest receipt practices). ...
19:24:57 [choose] ... jiseokkk: ... avoiding maple-home (clear dishonesty in receipts) and ggari-shop ...
```

**피한 방법 2: 내 기억 (로컬 재현).** 시장에서는 후기만으로 피했기 때문에 "한 번 속으면 영구 차단" 코드가 실제로 돈 적이 없다. 그래서 maple-home처럼 영수증을 15% 부풀리는 가게를 로컬에 띄워 확인했다(`challenge2_replay.py`, 기억 파일을 따로 써서 시장용 `memory.jsonl`은 건드리지 않음). 이 가게에는 **후기를 하나도 주지 않았다.**

```
### run 1: ['overcharge-shop (local replay)']
  [memory] overcharge-shop (local replay): cheated: agreed 101, receipt 117
### run 2: ['overcharge-shop (local replay)', 'honest-shop (local replay)']
  [choose] blocked 'overcharge-shop (local replay)': cheated us before: cheated: agreed 101, receipt 117 (lamp, ...)
  [memory] honest-shop (local replay): fair
```

첫 거래에서 합의 101, 영수증 117로 속은 것이 기록되고, 다음 주문에서는 후기가 없어도 기억만으로 차단해 정직한 가게로 갔다. 별점 1과 후기 `Agreed 101, receipt says 117.`도 남겨서, 시장이었다면 다른 buyer가 읽을 수 있었다.

## (6) 가게와 buyer의 배포

| 항목 | 내용 |
|---|---|
| 기계 | 집 PC, Windows 11, Python 3.11 venv (저장소 루트 `.venv`) |
| 명령 | `MARKET_OWNER=<claim code> python market_kit.py join --dir submissions/26512070/week-07 --market https://market.ourolabs.ai --name 김관우 --tunnel` |
| 가게 | 키트가 seller를 로컬 포트에 띄우고 cloudflared quick tunnel(`*.trycloudflare.com`)로 공개. 가게와 buyer가 같은 기계라 `--deployed` 대신 `--tunnel` (README 지시) |
| buyer | 같은 키트가 시장에서 주문을 받아 market buyer를 실행 |
| 키트 | 시장 서버가 배포하는 최신 키트(`market.ourolabs.ai/kit.tar.gz`). 저장소의 `scripts/market_kit.py`보다 새것이고, seller가 죽거나 터널이 끊기면 다시 띄우는 watchdog이 있다. market test는 두 버전이 같다 |
| 실행 방식 | 저장소 밖 `C:\Users\Admin\ax\agent-market\join.ps1`이 `Start-Process`로 숨김 실행. claim code는 이 파일에만 있고 커밋하지 않는다. `*.pem`은 키트가 `.gitignore`에 넣었다 |
| 로그 | `logs/market-kit.log` (이 기계에서 키트가 쓴 것) |

Workers나 VPS 대신 집 PC를 고른 이유: 가게와 buyer 모두 `claude -p`(내 로그인한 Claude Code)를 하네스로 쓰므로, 같은 하네스를 서버에 올리려면 서버에도 Claude Code 로그인이 필요하다. 마감까지 이 PC를 켜 둔다.

한계: 20:11에 Claude Code 세션이 다시 시작될 때 키트가 같이 꺼진 적이 있다. PC 재부팅이나 세션 재시작에도 살아 있으려면 Windows 작업 스케줄러에 `join.ps1`을 등록해야 한다.
