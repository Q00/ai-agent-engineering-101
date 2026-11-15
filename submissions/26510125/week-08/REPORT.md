# REPORT.md — week-08 (중간 평가): Round 3 시장의 내 에이전트

Harness: Claude Code headless `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence`
(claude-haiku-4-5), 6·7주차와 같다. 가게 이름 `yeonsong2`, 7주차와 같은 키로 Round 3에 들어간다(키 파일은 커밋하지 않음).
키트 v4는 이 폴더의 `market_kit.py`에 복사해 **한 군데**만 고쳤다: 결제 단계에서 `agent.json`에 `pay` 훅이 있으면 키트의 고정
checkout 대신 내 `acp_buyer.py`를 부른다(선택과제 "acp.json으로 만드는 buyer"). 내 코드는 `seller.py`(starter_round3 기반),
`market_buyer.py`(7주차 확장), 새 훅 `restock.py`·`review.py`·`acp_buyer.py`, 공용 `market_price.py`, `agent.json`,
운영용 `run_kit.sh`·`watchdog.sh`다. `test_hooks.py`가 가짜 `.market/`과 mock 가게로 훅 전부를 오프라인 검사한다.

## 1. 부품별 설계

1주차 정의대로 **모델 · 컨텍스트 · 툴**로 나눈다.

### 1.1 모델 — 어디에 썼고 어디에 안 썼나

| 호출 지점 | 모델이 받는 것 | 모델이 답하는 것 | 코드가 강제하는 것 |
|---|---|---|---|
| `seller.py` 호가 | 정가, **원가(하한)**, 등급, 식품이면 남은 유통시간, 협상 기록 | `{"action": counter/accept/reject, "price"}` | 원가 미만이면 거절, 수치는 정수로 |
| `market_buyer.py` 가게 고르기 | 후보 가게별 한 줄(후기 수·평균·최신 후기 2개·**내 이력 요약**) | `{"name","why"}` | 목록 밖 이름이면 코드가 평균별점으로 고름(fallback 카운트) |
| `buyer.py` 협상 | 품목, 예산, 호가 기록 | `{"action","price"}` | 예산 초과 제안은 코드가 막음 |
| `review.py` 후기 | 실제 등급 vs 가게 주장(`claim_honest`), 남은 유통시간 vs `use_within_hours`, 보증, 가격 vs 정가 | `{"stars","review"}` | 거짓 주장·유통시간 부족이면 별 2개 이하로 캡 |
| `market_buyer.py` 메모리 요약 | 한 가게의 최근 거래 10건(합의가·영수증가·별점·거짓 주장 여부) | `{"note": 한 문장}` | 5건마다 1회, 200자 캡 |
| `acp_buyer.py` 계획 | 가게 acp.json에서 **생성한 툴 목록**(이름·메서드·경로·입력) | `{"plan": [툴 이름…]}` | 첫 툴이 create, 마지막이 complete가 아니면 코드가 `[create, complete]`로 대체 |
| `restock.py` | — (모델 없음) | — | ledger `settle` 집계로 결정 |

모델은 **판단이 필요한 여섯 지점**에만 부른다. 재고 보충은 숫자 문제라 모델을 쓰지 않는다(호출당 수 초, 60초마다 도는 훅에
LLM을 넣으면 비용만 든다). 모든 호출은 `harness.ask_json()`을 거쳐 첫 `{...}`만 파싱하고, 실패하면 코드 fallback으로 간다.

### 1.2 컨텍스트 — 지난 거래와 평판을 다음 결정에 넣는 방법

세 층의 메모리를 쓴다. 전부 파일이고 매 훅 실행마다 다시 읽는다(훅은 프로세스로 매번 새로 뜬다).

1. **내 거래 기억** `buyer_memory.json`(7주차): 가게 **이름**을 키로 거래·합의가·영수증가·불일치·별점을 쌓는다.
   불일치가 한 번이라도 있으면 `avoid`가 켜지고 그 가게엔 요청을 보내지 않는다.
2. **키트가 써 주는 Round 3 사실** `.market/`:
   - `payments.jsonl` — 키트가 **왜 결제를 안 했는지**(인증서 없음, 등급 C, 유통시간 부족…). 최근 50건에서 같은 가게가
     2번 이상 거부되면 그 가게를 후보에서 뺀다(`refused_shops()`). 협상에 성공해도 돈이 안 나가는 가게는 시간 낭비다.
   - `broken.jsonl` — 내가 산 물건 중 부서진 것의 이름. 같은 이름의 SKU는 다음 선택에서 뒤로 미룬다(`pick_sku()`).
   - `deal.json` — 결제 **후** 드러난 진짜 등급·`claim_honest`·`value`. `review.py`가 이것으로 별점을 매기니
     협상 시점의 인상이 아니라 **정산 결과**가 평판이 된다.
3. **시장 전체의 평판·가격·수요** — `market_price.py`: 공개 ledger의 `settle` 항목(`/api/ledger?after=-500`, 60초 캐시)에서
   SKU별 **체결 건수와 중앙값**을 뽑는다. Round 3에선 예산이 ledger에서 빠졌지만 무엇이 얼마에 팔렸는지는 남아 있다. 이 하나의
   신호가 세 곳에 들어간다: `restock.py`(무엇을 살지), `seller.py`(첫 호가를 정가가 아니라 시장 중앙값 근처로), `market_buyer.py`
   (첫 제안의 앵커 + 게시판 가격 리포트). figure의 빨간 폐루프다. 가게 후기(`shops.json`)는 7주차처럼 코드가 먼저 거른다.
4. **메모리 요약(meta 단계)**: 가게별 거래가 5건 쌓일 때마다 하네스가 원 로그를 한 문장 `note`("첫 호가에서 12 % 내려옴, 영수증
   정직, 85 %부터 제안")로 압축해 저장하고, 이후 프롬프트는 **로그가 아니라 note**를 본다(`consolidate()`). `review.py`는 정산 후
   `claim_honest=false`면 그 가게의 마지막 거래에 `dishonest`를 찍어 note와 게시판 경고의 재료로 넘긴다.

가게 쪽 컨텍스트는 `MARKET_DIR/inventory.json`이다. 호가 프롬프트에 **단위(unit)별 원가·등급·남은 유통시간**을 넣고,
유통시간 12시간 미만이거나 C등급이면 "빨리 파는 게 낫다"는 한 줄을 붙인다. 7주차처럼 협상 기록은 task 단위로 메모리에 들고 있다.

### 1.3 툴 — 무엇을 툴로 만들었고 어떻게

키트의 훅 계약이 곧 툴 인터페이스다. 각 훅은 **파일을 읽고 마지막 줄에 JSON 하나**를 찍는 독립 프로세스다.

| 툴 | 입력 | 출력 | 핵심 규칙 |
|---|---|---|---|
| `restock.py --state .market` | inventory, catalog, ledger settle 500건 | `{"buy":[{sku,qty}], "bankrupt"}` | 팔린 SKU 순으로 상위 10개, 재고 40개 상한, SKU당 잔고 5 %, 유통 48h 미만 식품은 가중치 0.3, 재고 0에 하한선 아래면 파산 신청; 10분에 한 번 게시판에 재고 공지 |
| `market_buyer.py … --order` | order.json(acceptable·kind·use_within_hours), shops.json(offers), catalog, payments, broken | `{"shop","receipt","stars","review","accept"}` | 재고 있는 가게만 → 가게 선택 → 예산 내 **정가 최대** SKU → `"I want to buy <name> (<sku>)"` → `accept={cert:true, grades:[A,B], min_hours_left}` |
| `review.py --deal deal.json` | 정산 영수증 | `{"stars","review"}` | 거짓 주장/유통시간 부족 ≤ 2★ |
| `seller.py`(A2A) | 요청문, 재고, SKU 시장 중앙값 | A2A task + 서명 영수증(`unit, claim, cert, warranty_hours`) | 비식품 보증 A 6h / B 3h / C 0, 원가 하한은 코드가 강제 |
| **`acp_buyer.py --pay pay.json`** | 주문(+open mandate), 가게 URL·공개키, 합의 영수증, accept | `{"mandate_receipt"}` 또는 `{"reason"}` | 아래 |
| `market_price.py` | ledger | `median_price(sku) -> (중앙값, n)` | 60초 캐시, 실패 시 빈 목록 |
| 게시판 | forum.json, 내 메모리 | `.market/outbox.jsonl` 한 줄 | buyer는 10분에 1회 **가격 리포트**(9주차 규칙: 글 수가 buyer ALL의 0.1)(SKU·지불가·시장 중앙값·거짓 주장 가게 경고), 가게는 10분마다 재고 공지. **투표**는 증거 기준: "부르는 값을 내고 별 다섯 개"류 조작 글에 −1, 내 메모리도 거짓으로 잡은 가게에 대한 buyer 경고에 +1, 글당 1회(메모리 `voted`), 실행당 3표 |

**acp.json으로 만드는 buyer(선택과제)**: `acp_buyer.py`는 checkout 흐름을 하드코딩하지 않는다. 가게의 `/.well-known/acp.json`을
받아 `tools` 배열의 항목마다 `{method, path 템플릿, input}`으로 **호출 가능한 함수를 생성**하고(`build_tools()`: `{id}` 같은 경로
변수는 인자에서, 나머지는 JSON 본문), 하네스에게 툴 설명만 보여 주고 호출 순서를 받는다. 실행은 코드가 키트와 같은 검사를 한다 —
checkout JWT가 가게 키로 서명됐고 item/price/task가 합의 영수증과 같은지, 금액이 payment mandate의 `amount_range.max` 이하인지,
단위가 `accept`를 통과하는지(아니면 cancel 툴 호출) — 그리고 `buyer_key.pem`으로 closed checkout/payment mandate를 서명해
complete 툴을 부른다. 키트 쪽 패치는 `market_kit.py`의 결제 분기 10줄: `pay.json`을 쓰고 훅의 마지막 JSON 줄을 `mandate_receipt`로 쓴다.
mock 가게 테스트(`test_hooks.py`)로 정상 결제·등급 C 거부→cancel·상한 초과 거부·엉뚱한 계획→fallback 네 경로를 확인했다.

**운영도 툴이다**: 순위는 2주간 켜져 있는 시간에 비례하므로 `watchdog.sh`가 5분마다 키트 프로세스와 로그 갱신 시각을 보고,
죽었거나 20분 조용하면 `run_kit.sh --background`로 다시 띄운다(터널 DNS 대기 3분 포함).

`accept`에 `warranty_hours`를 넣지 않은 건 의도다: starter 가게는 보증을 안 주므로 요구하면 대부분의 거래가 결제 전에 막힌다.
대신 내 가게는 보증을 **준다**(차별점이자 후기 재료).

## 2. 아키텍처 figure와 키트 기본형과의 차이

`architecture.pdf`(`architecture.tex`, TikZ) 한 페이지. 회색이 키트 v4(패치는 `pay` 훅 하나), 파란 상자가 내 코드, 점선이 파일,
**빨간 점선이 시장가 피드백 루프**다: 시장 ledger → `market_price.py` → (재고 선택, 가게 호가, buyer 앵커·게시판) → 다시 시장.

키트 기본형과 다른 점:

| 키트 기본형 | 내 구조 |
|---|---|
| 재고 20개 미만이면 **무작위** 카테고리·SKU를 산다 | ledger `settle`에서 **실제로 팔린 SKU**를 산다. 재고 상한·SKU당 지출 상한·유통시간 가중치 |
| buyer는 재고 있는 가게 중 **무작위**, 품목명만 말한다 | 후기·내 기억·`payments.jsonl`로 거른 뒤 모델이 고르고, **SKU를 지목**해 산다(정가 최대 = surplus 최대) |
| `accept` 없음 → 인증서 없는 C등급도 결제 | `accept`로 인증서·A/B등급·유통시간을 결제 전에 강제 |
| 후기는 협상 직후 인상으로 | 정산 **후** 진짜 등급으로 `review.py`가 다시 매김(거짓 주장은 ≤2★) |
| 가게는 등급을 모르고 정가 근처에서 시작 | 등급·유통시간·원가를 보고 호가, 비식품에 보증을 붙여 영수증에 씀 |
| 게시판 안 씀 | 가게 재고 공지(10분), buyer **가격 리포트**(SKU 중앙값·거짓 주장 가게) — 다른 buyer에게 쓸모 있어야 표가 붙는다 |
| 결제는 키트의 고정 `acp_pay()` | `pay` 훅 → `acp_buyer.py`가 **acp.json에서 툴을 생성**해 checkout을 끝낸다(검사는 동일) |
| 가격 정보 없음(예산은 Round 3에서 숨김) | ledger `settle` 중앙값이 가게·buyer·재고 세 결정의 공통 앵커 |
| 메모리 없음 | 거래 로그 + 5건마다 한 문장 note(meta 단계), 정산 후 `dishonest` 표시 |
| 죽으면 끝 | `watchdog.sh`가 되살림 |

하네스는 세 부품 어디에도 "루프"로 들어 있지 않다. **판단 한 번 = 호출 한 번**이고, 루프와 상태는 키트와 파일이 든다.

## 2.4 meta-harness — `tuner.py`: 에이전트가 자기 로그로 자기 파라미터를 고친다 (11/15 추가)

10일 동안 내가 손으로 한 일은 전부 같은 모양이었다: 로그를 grep해서 지표(방문→합의 전환, 마진, 결렬 시 제안/하한 비율,
결제 거부 사유)를 세고, 상수 하나를 바꾸고, 재시작하고, 한 시간 뒤 다시 센다. 그 루프를 에이전트에 넣었다.

- **측정(코드)**: `tuner.measure()`가 `logs/market-kit.log`의 최근 1시간에서 12개 지표를 센다(방문·합의·결렬·마진·결렬 시 제안/하한,
  주문·합의·재고없음·거부·결제·등급/유통기한 거부). 테스트로 파서를 고정.
- **결정(모델)**: 지표 표 + 현재 손잡이 3개 + 점수 산식을 하네스에 주고 `{"ask_factor","first_offer","price_cap","why"}`를 받는다.
  손잡이는 가게 첫 호가(시장 중앙값 대비 배율), buyer 첫 제안(중앙값 대비), buyer 지불 상한(정가 대비).
- **가드(코드)**: 범위 `ask 0.85~1.05, offer 0.75~0.95, cap 0.65~0.80` 안에서 **한 시간에 한 손잡이, 최대 0.05**만. 답이 없거나
  이상하면 유지. `params.json`에 쓰고 `logs/tuner.log`에 지표·전후 값·이유를 남긴다. seller/buyer는 매 요청마다 `params()`를 읽어
  재시작 없이 반영된다. `restock.py`가 1시간마다 백그라운드로 띄운다.
- 첫 결정(11/15 00:29): "결렬 22 ≈ 결제 23, 상한을 0.75→0.80으로 올려 막힌 거래를 푼다" — 내가 전날 손으로 내린 판단과 반대였고,
  그래서 범위 상한을 0.80으로 묶었다(그 위는 Saved 산식상 평균 이하 거래가 늘어 감점). 효과는 `tuner.log`로 추적한다.

## 2.5 10주차 LAB — 내 에이전트의 첫 수 (주문 없이 먼저 움직이는 행동)

| 항목 | 내용 |
|---|---|
| 행동 | 가게가 1시간마다 게시판에 **호가표**를 올린다: 내가 재고로 가진 A등급 비식품 중 시장 정산 매출 상위 3개 SKU의 내 호가(시장 중앙값의 95 %)와 중앙값·건수, 보증·인증서 조건. `restock.py::notice()` |
| 상대의 목표 | "[시세]" 글을 읽고 기준선 아래 가게부터 견적을 받는 buyer 에이전트들(10주차 STORY: THE PRICE REPORTS) |
| 맞힐 확률 | 중간. 좋아요 상위 글이 전부 수치 기반 글이었고, buyer 선택 사유의 15 %가 남의 글을 인용한다(7주차 데이터) |
| 틀렸을 때 비용 | 가게 글 슬롯 1개/시간. 가게 글은 가게 ALL에 안 들어가므로 점수 손실 없음 |
| 효과 지표 | seller 방문/시간, 합의/시간. **기준값(11/12 12:00~15:00): ~300 방문/h, ~75 합의/h**. 11주차에 다시 잰다 |
| 확인 | 첫 글 `post #96425`, `outbox.sent.jsonl`에 200 응답 (11/12 15:26) |

부수 발견: 이 글이 안 올라가서 `outbox.taking`을 봤더니 **buyer 글 1,557개가 밀려 있었고**, 키트가 10초마다 그 전부를 다시 POST해
429를 받고 있었다(초기 5분 간격 게시 시절의 잔재). 백로그를 최신 3개로 잘라 내고, 키트 `outbox()`에 "20줄 넘는 백로그는 버림"을
패치했다(커밋 `74adfa2`). 시장 서버 부하를 내가 만들고 있었던 셈이라 바로 고쳤다.

## 3. Round 3 결과

(Round 3은 11월 3일 시작. 아래는 Round 3 진행 중 채운다.)

- 제출 시점 정산 수: 가게 — / buyer —
- Players 순위: —
- 거부 사유(`logs/market-kit.log` 인용): —

## 로컬 검사

- `python test_hooks.py` — 훅 자체 검사(가짜 `.market/`, mock 가게): restock 수요순·예산 상한·식품 가중치, SKU 선택·`accept`·거부 가게 필터,
  review 캡, acp_buyer 정상 결제/등급 거부→cancel/상한 초과/계획 fallback, 가격 리포트 문구.
- 로컬 가게 + Round 3 buyer end-to-end(가짜 `order.json`, `shops.json(offers)`): `I want to buy lamp (LAMP-01)` → 3회 호가 → 110에 체결,
  서명 영수증 + `"accept": {"cert": true, "grades": ["A","B"]}` 출력 확인.
- `scripts/market_test_week07.py .` — `logs/market_test_local.txt`.
