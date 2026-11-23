# Week 06 — 내 harness를 A2A 뒤에 두 번: seller와 buyer

로컬 Qwen을 부르는 내 harness(`harness.py`)를 A2A v1.0 뒤에 두 번 붙였다. 한 번은 협상하는 seller, 한 번은 쇼핑하는 buyer다. 에이전트 코드는 A2A만 말하고, 상품 선택·가격·수락·포기 같은 결정은 전부 harness가 한다.

## 요약

| 항목 | 결과 |
|---|---|
| trade test (`scripts/trade_test_week06.py`) | **21/21 PASS** (1분 33초, harness 호출 52회) |
| 도전 1: 상품 3,000개 카탈로그 seller | 설명만으로 요청 12건 → **12/12**. 엉뚱한 상품 0, reserve 아래 ask 0, reserve 아래 판매 0 |
| 도전 2: 상점 20곳 + 쇼핑 목록 5개 + 총예산 | **통과**. 5개 모두 한 번씩 구매, 총 880 ≤ 예산 1,038, 중복 0 |
| 실패에서 고친 것 | vLLM 미기동(7/20), 검색 순위 오류와 harness의 "none" 응답(도전 1, 8/12), 예산 과소 배분(도전 2, 3/5) |

---

## 1. Harness, 모델, 실행 명령

| 항목 | 값 |
|---|---|
| Harness | 직접 만든 최소 harness `harness.py`. 프롬프트 하나를 보내고 JSON 결정 하나를 받는다(JSON이 아니면 1회 재요청) |
| 모델 | `Qwen/Qwen3.8-27B` (bf16, revision `1d4bf0f2`, week-03~05와 동일) |
| 모델 서버 | 로컬 vLLM 0.17.1, RTX PRO 6000 96GB × 1 (`serve_model.sh`) |
| 호출 설정 | OpenAI 호환 chat API, temperature 0.2, `max_tokens=300`, `enable_thinking=false`, seed 미지정 |
| A2A | `a2a-sdk` 1.2.3 (seller 서버), buyer는 httpx로 JSON-RPC를 직접 보냄. `httpx` 0.28.1, `uvicorn` 0.54.0, `openai` 3.35.1 |

`agent.json`:

```json
{
  "harness": "own harness (harness.py) driving Qwen/Qwen3.8-27B on a local vLLM server (serve_model.sh), temperature 0.2, thinking off",
  "seller": {"cmd": "python -u seller.py --port {port}"},
  "buyer": {"cmd": "python -u buyer.py --url {url} --item {item} --budget {budget}"}
}
```

**실행 방법**

```bash
pip install "a2a-sdk[http-server]" uvicorn httpx openai     # python 3.12 venv
bash serve_model.sh                                          # harness 모델, :8011
python catalog.py                                            # catalog.json (이미 커밋됨, 시드 고정)

python ../../../scripts/trade_test_week06.py .               # trade test
python challenge1.py                                         # logs/challenge-1/
python challenge2.py                                         # logs/challenge-2/
python ../../../scripts/check_week06.py .
```

claude 구독 로그인이 필요 없는 대신, 이 harness는 `http://127.0.0.1:8011/v1`의 OpenAI 호환 서버가 필요하다. 다른 서버를 쓰려면 `HARNESS_BASE_URL`, `HARNESS_MODEL`을 바꾼다.

| 파일 | 역할 |
|---|---|
| `harness.py` | 모델 호출. 두 에이전트의 모든 결정이 여기서 나온다 |
| `seller.py` | A2A seller (실습 `seller_agent.py`의 규칙을 harness 호출로 교체). `--catalog`로 도전 1·2 |
| `buyer.py` | A2A buyer (trade test용). Card 읽기, `A2A-Version: 1.0`, taskId로 이어가기, CancelTask |
| `catalog.py` | 카탈로그 생성기(3,000개)와 검색 |
| `shopper.py` | 도전 2 buyer: 디렉터리, 쇼핑 목록, 총예산 |
| `challenge1.py`, `challenge2.py` | 도전 1·2 실행기와 기계적 검증 |

---

## 2. 각 에이전트가 결정하는 방식

### 2.1 seller

| 시점 | harness에 주는 것 | harness 답 형식 | 코드가 강제하는 것 |
|---|---|---|---|
| 첫 메시지 | buyer 문장 + 후보 상품(기본 3개, 카탈로그면 검색 상위 12개)의 id, 이름, 설명, **reserve** | `{"item_id", "ask", "message"}` | 후보에 없는 id는 거절(REJECTED). `ask = max(ask, reserve)` |
| 이후 메시지 | 상품, reserve, 지금까지의 ask와 offer, 이번 offer, 라운드 번호 | `{"action": accept/counter/reject, "price", "message"}` | 아래 guard |

- **reserve는 프롬프트와 코드 양쪽에 있다.** 프롬프트로 알려 주고, 코드가 다시 막는다.
- **guard 1:** `accept`인데 offer < reserve이면 counter로 바꾼다.
- **guard 2:** counter 가격은 reserve 이상, 직전 ask 이하로 자른다.
- **guard 3:** counter가 offer 이하이면 offer를 수락한다.
- **guard 4:** 8라운드를 넘으면 reject한다.
- **메시지 형식:** harness 문장에서 숫자를 지우고 끝에 `Asking N.` / `Counteroffer: N.`을 붙인다. 그래서 마지막 숫자가 항상 ask다.
- **영수증:** 거래는 영수증 artifact(data Part `{"seller", "item", "item_id", "price"}`)와 `TASK_STATE_COMPLETED`로 끝난다.

seller 프롬프트(요약): `You are the sales agent of {shop} ... Each item has a reserve: the lowest price you may accept. Never ask or accept below it.` 이어지는 턴에는 `Buyers usually walk away after about five rounds, and a deal at or above your reserve is better than no deal.`가 들어간다.

### 2.2 buyer

| 시점 | harness에 주는 것 | 답 형식 | 코드가 강제하는 것 |
|---|---|---|---|
| 매 라운드 | 물건, **budget**, 지금까지의 ask와 offer, seller 문장, 현재 ask(마지막 숫자) | `{"action": offer/accept/walk, "price"}` | offer > ask이면 ask로, offer > budget이면 budget으로 자른다. budget을 이미 한 번 냈는데 또 budget이면 CancelTask. 8라운드면 CancelTask |

- budget도 프롬프트와 코드 양쪽에 있다.
- 모든 요청(Card GET 포함)에 `A2A-Version: 1.0`을 붙이고, 두 번째 메시지부터 `taskId`를 싣는다.
- `walk`이면 `CancelTask`를 보낸다.

---

## 3. 통과하기까지 실패한 것과 고친 것

| # | 실패 | 로그 | 원인 | 고친 것 |
|---|---|---|---|---|
| 1 | trade test 7/20 | `<- error -32603 Connection error.` / `FAIL seller_opens #1: first message -> error, ask None` (커밋 `4fb1011`) | vLLM이 아직 로딩 중이라 harness 호출이 실패했다. seller는 `-32603`, buyer는 `APIConnectionError`로 죽었다 | 서버 준비 확인 후 재실행 → 21/21 |
| 2 | (통과했지만 guard가 막음) | `[67a777b2 harness] { "action": "accept", "price": 230, "message": "I appreciate your patience, but I cannot go any lower than my final price." }` → `[guard] accept at 230 is below the reserve 260: counter at 260 instead` (커밋 `c797e18`) | 모델이 reserve 260인 bookshelf를 230에 accept했다. 문장은 거절인데 action은 수락이다 | 코드 guard가 없었다면 `seller_holds_reserve` 실패. reserve를 프롬프트에만 두지 않은 이유 |
| 3 | 도전 1 8/12 | `I want to buy a low glass unit for a 80 inch television` → `item c00129 (Dark Brown glass bookshelf, 66 books, 74 cm)` | 검색: "about", "to"가 책장 설명에만 있어 점수가 높았다 | 불용어 제거, 카테고리 단어 가중치 2배, 동의어(couch→sofa, closet→wardrobe, television→tv) |
| 4 | 도전 1 (같은 실행) | `{ "item_id": "none", ... "We have several metal sofas that seat two, but could you please specify your preferred color or material?" }` | 맞는 후보가 여럿이자 모델이 되묻고 none을 골랐다 | 프롬프트: `If several fit, pick the closest one; do not ask for more details.` → 12/12 |
| 5 | 도전 2 3/5 구매 | `[allocate round 1] {... "2": {"shop": "S04", "cap": 50}, "3": {"shop": "S08", "cap": 430} ...}` → `item 2 ... asks [62, 58, 55, 53, 52] ... walked` (커밋 `a2dc890`) | harness가 상한 합계를 812로 잡았다(예산 1,038). 램프는 52, 식탁은 448에서 멈췄다 | 프롬프트에 "남은 돈은 낭비"를 추가하고, 코드가 남은 예산을 ask 한도 안에서 비례 배분 |
| 6 | 도전 2 (같은 실행) | `[quote] item 2 @ S03 (Nordic Room): TASK_STATE_REJECTED | Sorry, we do not have that item.` | 재시도 때 desk 상점에 desk lamp를 물었다 | 카드의 판매 품목 중 설명과 가장 긴 구절이 맞는 상점부터 시도 → 통과 |

최종 trade test 실행에서는 guard가 한 번도 작동하지 않았다. seller는 lamp를 3번 모두 115에, buyer는 lamp를 3번 모두 100에 샀다. bookshelf는 seller가 260까지 내려온 뒤 REJECTED로 끝났고, buyer 쪽은 kit seller가 다섯 번째 낮은 제안 뒤 REJECTED로 끝냈다.

---

## 4. A2A가 정해 준 것과 밖에서 합의해야 했던 것

A2A가 정해 준 것은 대화의 틀이다. Agent Card로 상대가 누구이고 어디로 보내는지를 알았고, 협상 한 번이 Task 하나라 `taskId`만 실어 보내면 같은 협상이 이어졌다. 상태도 `INPUT_REQUIRED`(내 차례), `COMPLETED`(거래), `REJECTED`(결렬), `CANCELED`(내가 떠남)로 정해져 있어서 buyer는 상태만 보고 루프를 끝냈다. 끝난 Task에 메시지를 보내면 SDK가 알아서 `error -32004 Task ... is in terminal state: TASK_STATE_COMPLETED`를 돌려줘서 그 검사는 코드 한 줄도 쓰지 않았다. 그러나 협상의 내용은 A2A 밖의 약속이었다. 가격이 어디 있는지(seller는 "메시지의 마지막 숫자", buyer는 "숫자만 보내기"), 수락이 어떻게 생겼는지(buyer는 상대 ask와 같은 숫자를 보내고, seller는 그 숫자로 Task를 닫는다. 따로 "accept" 메시지가 없다), 영수증의 어느 필드가 가격인지(data Part의 `price`), 첫 메시지가 상품을 어떻게 가리키는지(`I want to buy a lamp`), 몇 라운드면 포기하는지는 모두 kit과 내 코드가 같은 관례를 따라서 맞았을 뿐이다. 그래서 seller는 harness 문장에서 숫자를 지우고 ask를 맨 끝에 붙여야 했고, buyer는 harness가 무엇을 말하든 마지막 숫자만 ask로 읽었다. FIPA-ACL의 performative(`propose`, `accept-proposal`)에 해당하는 것이 A2A에는 없고, Task 상태가 그 일부만 대신한다.

---

## 5. 도전 1과 2: 큰 컨텍스트를 어떻게 다뤘나

### 5.1 도전 1: harness가 카탈로그를 보는 방법

| 항목 | 값 |
|---|---|
| 카탈로그 | `catalog.py`가 시드 6으로 생성. 15종 × 재질 10종 × 20개 = **3,000개**, `catalog.json` 981,961바이트 |
| 전부 넣으면 | 후보 줄 형식으로 약 407,000자. 모델 컨텍스트(8,192 토큰)를 크게 넘는다 |
| 실제로 넣는 것 | 코드 검색 상위 **12개**만, 약 2,000자 |
| 검색 (`catalog.Index`) | IDF 가중 단어 겹침 + 카테고리 단어 2배 + 동의어 확장 + 숫자 근접도(문장의 숫자와 높이·폭·용량) |
| 결정 분담 | 코드가 3,000개 → 12개로 줄이고, harness가 12개 중 하나와 첫 ask를 고른다 |

| 결과 (`logs/challenge-1/results.json`) | 값 |
|---|---|
| 요청 | 12건, 상품 이름 없이 설명만. 예: `something wooden to keep about 50 books, not taller than 81 cm` |
| 맞는 상품을 팜 | 12/12 (카테고리, 재질군, 용량 허용 범위, 높이 상한을 모두 만족) |
| reserve 아래 ask / 판매 | 0 / 0 |
| 거래 성사 | 11/12 (1건은 seller가 reserve 105까지 내려왔지만 buyer 예산 87보다 높아 REJECTED) |
| harness 호출 | 49회 |

`logs/challenge-1/challenge1.log`에서 한 줄을 인용한다.

```
## request 11: "something wooden to keep about 50 books, not taller than 81 cm" (hidden target c00047 Green pine bookshelf, 50 books, 76 cm, reserve 140, buyer budget 161)
  PASS  picked c00047 (Green pine bookshelf, 50 books, 76 cm), reserve 140, fits=True, TASK_STATE_COMPLETED, price 160.0, asks [182, 160]
```

### 5.2 도전 2: buyer가 협상 상태를 들고 다니는 방법

| 단계 | 누가 | harness가 보는 것 |
|---|---|---|
| Card 20장 읽기 | 코드 | – |
| 물건마다 상점 2곳 고르기 | harness | 상점당 한 줄 `S01 \| Oak & Iron \| bookshelf, dining chair, nightstand` × 20 + 쇼핑 목록 |
| 견적: 10개 Task를 동시에 엶 | 코드 | – |
| 물건마다 상점 1곳 + 상한 정하기 | harness | 견적당 한 줄 `item \| shop \| 상점이 고른 상품 \| ask` |
| 예산 검사 | 코드 | 상한 합계 > 남은 예산이면 축소, < 이면 ask 한도 안에서 남은 예산 배분. 따르지 않는 견적은 CancelTask |
| 5개 물건 동시 협상 | harness | **그 협상 한 줄만**: 물건, 상한, ask 목록, offer 목록, 현재 문장 |
| 실패한 물건 재시도(최대 3라운드) | harness + 코드 | 남은 예산과 새 견적 |

대화 전체는 어디에도 쌓지 않는다. 협상 하나의 상태는 `summary()`가 만드는 한 줄이 전부이고, 상점이 20곳이어도 harness 프롬프트는 커지지 않는다. 같은 물건을 두 번 사지 않는 것은 코드가 보장한다. 물건마다 견적 뒤에는 Task 하나만 남기고 나머지는 CancelTask하며, 재시도는 거래가 없는 물건만 한다.

| 결과 (`logs/challenge-2/check.json`, 상점 로그에서 직접 셈) | 값 |
|---|---|
| 상점 | 20곳(`seller.py` 사본), 각 3개 카테고리, reserve 배율 0.9~1.3 |
| 쇼핑 목록 | 5개, 설명만 (`something glass to keep about 136 books`, `a wooden desk lamp around 1000 lumens`, ...) |
| 총예산 | 1,038 (목록 상품 reserve 합 × 1.25) |
| 구매 | 5/5, 각 1번, 총 **880** |
| harness 호출 | shopper 16회, 상점들 60회. 28초 |

`logs/challenge-2/shopper.log`의 상태 줄을 인용한다.

```
[guard] caps added up to 877 < 1038: unused budget spread up to the asks -> 1036
[state] item 1 | S01 | Natural glass bookshelf, 136 books, 141 cm | asks [287, 245, 230] | offers [200, 220, 225] | deal at 225.0
[state] item 4 | S07 | White oak shoe rack, 9 pairs, 65 cm | asks [74, 66, 64, 63] | offers [58, 62, 60, 61] | deal at 61.0
```

item 4에서 buyer harness는 62를 낸 다음 60으로 내려갔다(`{"action": "offer", "price": 60}`). 요약 한 줄에 이전 offer가 있는데도 단조 증가를 지키지 않았다. 손해는 없었지만, 상태를 한 줄로 줄이면 모델이 그 줄을 정확히 따르는지는 따로 확인해야 한다는 사례다.

### 5.3 하지 않은 것

도전 3(숫자가 많은 긴 메시지, 적대적 문장)과 배포는 하지 않았다.
