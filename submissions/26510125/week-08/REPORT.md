# REPORT.md — week-08 (중간 평가): Round 3 시장의 내 에이전트

Harness: Claude Code headless `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence`
(claude-haiku-4-5), 6·7주차와 같다. 가게 이름 `yeonsong2`, 7주차와 같은 키로 Round 3에 들어간다(키 파일은 커밋하지 않음).
키트 v4(`scripts/market_kit.py`)는 고치지 않았다. 내 코드는 `seller.py`(starter_round3 기반), `market_buyer.py`(7주차 확장),
새 훅 `restock.py`·`review.py`, 그리고 `agent.json`이다. 로컬 자체 검사 `test_hooks.py`가 세 훅을 가짜 `.market/`으로 돌린다.

## 1. 부품별 설계

1주차 정의대로 **모델 · 컨텍스트 · 툴**로 나눈다.

### 1.1 모델 — 어디에 썼고 어디에 안 썼나

| 호출 지점 | 모델이 받는 것 | 모델이 답하는 것 | 코드가 강제하는 것 |
|---|---|---|---|
| `seller.py` 호가 | 정가, **원가(하한)**, 등급, 식품이면 남은 유통시간, 협상 기록 | `{"action": counter/accept/reject, "price"}` | 원가 미만이면 거절, 수치는 정수로 |
| `market_buyer.py` 가게 고르기 | 후보 가게별 한 줄(후기 수·평균·최신 후기 2개·**내 이력 요약**) | `{"name","why"}` | 목록 밖 이름이면 코드가 평균별점으로 고름(fallback 카운트) |
| `buyer.py` 협상 | 품목, 예산, 호가 기록 | `{"action","price"}` | 예산 초과 제안은 코드가 막음 |
| `review.py` 후기 | 실제 등급 vs 가게 주장(`claim_honest`), 남은 유통시간 vs `use_within_hours`, 보증, 가격 vs 정가 | `{"stars","review"}` | 거짓 주장·유통시간 부족이면 별 2개 이하로 캡 |
| `restock.py` | — (모델 없음) | — | ledger `settle` 집계로 결정 |

모델은 **판단이 필요한 네 지점**에만 부른다. 재고 보충은 숫자 문제라 모델을 쓰지 않는다(호출당 수 초, 60초마다 도는 훅에
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
3. **시장 전체의 평판·수요**: 가게 후기(`shops.json`)는 7주차처럼 코드가 먼저 거르고(평균 2.5 미만, 영수증 불일치 후기 2개↑),
   공개 ledger의 `settle` 항목(`/api/ledger?after=-500`)은 `restock.py`의 수요 신호다 — Round 3에선 예산이 ledger에서
   빠졌지만 **무엇이 팔렸는지**는 남아 있다.

가게 쪽 컨텍스트는 `MARKET_DIR/inventory.json`이다. 호가 프롬프트에 **단위(unit)별 원가·등급·남은 유통시간**을 넣고,
유통시간 12시간 미만이거나 C등급이면 "빨리 파는 게 낫다"는 한 줄을 붙인다. 7주차처럼 협상 기록은 task 단위로 메모리에 들고 있다.

### 1.3 툴 — 무엇을 툴로 만들었고 어떻게

키트의 훅 계약이 곧 툴 인터페이스다. 각 훅은 **파일을 읽고 마지막 줄에 JSON 하나**를 찍는 독립 프로세스다.

| 툴 | 입력 | 출력 | 핵심 규칙 |
|---|---|---|---|
| `restock.py --state .market` | inventory, catalog, ledger settle 500건 | `{"buy":[{sku,qty}], "bankrupt"}` | 팔린 SKU 순으로 상위 10개, 재고 40개 상한, SKU당 잔고 5 %, 유통 48h 미만 식품은 가중치 0.3, 재고 0에 하한선 아래면 파산 신청; 10분에 한 번 게시판에 재고 공지 |
| `market_buyer.py … --order` | order.json(acceptable·kind·use_within_hours), shops.json(offers), catalog, payments, broken | `{"shop","receipt","stars","review","accept"}` | 재고 있는 가게만 → 가게 선택 → 예산 내 **정가 최대** SKU → `"I want to buy <name> (<sku>)"` → `accept={cert:true, grades:[A,B], min_hours_left}` |
| `review.py --deal deal.json` | 정산 영수증 | `{"stars","review"}` | 거짓 주장/유통시간 부족 ≤ 2★ |
| `seller.py`(A2A) | 요청문, 재고 | A2A task + 서명 영수증(`unit, claim, cert, warranty_hours`) | 비식품 보증 A 6h / B 3h / C 0, 원가 하한 |
| 게시판 | — | `.market/outbox.jsonl` 한 줄 | buyer는 정산 1건마다(5분 제한), 가게는 10분마다 |

`accept`에 `warranty_hours`를 넣지 않은 건 의도다: starter 가게는 보증을 안 주므로 요구하면 대부분의 거래가 결제 전에 막힌다.
대신 내 가게는 보증을 **준다**(차별점이자 후기 재료).

## 2. 아키텍처 figure와 키트 기본형과의 차이

`architecture.pdf`(`architecture.tex`, TikZ) 한 페이지. 회색이 키트 v4(그대로), 파란 상자가 내 에이전트, 점선이 파일 경계다.

키트 기본형과 다른 점:

| 키트 기본형 | 내 구조 |
|---|---|
| 재고 20개 미만이면 **무작위** 카테고리·SKU를 산다 | ledger `settle`에서 **실제로 팔린 SKU**를 산다. 재고 상한·SKU당 지출 상한·유통시간 가중치 |
| buyer는 재고 있는 가게 중 **무작위**, 품목명만 말한다 | 후기·내 기억·`payments.jsonl`로 거른 뒤 모델이 고르고, **SKU를 지목**해 산다(정가 최대 = surplus 최대) |
| `accept` 없음 → 인증서 없는 C등급도 결제 | `accept`로 인증서·A/B등급·유통시간을 결제 전에 강제 |
| 후기는 협상 직후 인상으로 | 정산 **후** 진짜 등급으로 `review.py`가 다시 매김(거짓 주장은 ≤2★) |
| 가게는 등급을 모르고 정가 근처에서 시작 | 등급·유통시간·원가를 보고 호가, 비식품에 보증을 붙여 영수증에 씀 |
| 게시판 안 씀 | 가게 재고 공지(10분), buyer 거래 보고(5분) — 둘 다 코드가 쓴다 |

하네스는 세 부품 어디에도 "루프"로 들어 있지 않다. **판단 한 번 = 호출 한 번**이고, 루프와 상태는 키트와 파일이 든다.

## 3. Round 3 결과

(Round 3은 11월 3일 시작. 아래는 Round 3 진행 중 채운다.)

- 제출 시점 정산 수: 가게 — / buyer —
- Players 순위: —
- 거부 사유(`logs/market-kit.log` 인용): —

## 로컬 검사

- `python test_hooks.py` — 세 훅의 자체 검사(가짜 `.market/`): restock 수요순·예산 상한·식품 가중치, SKU 선택·`accept`·거부 가게 필터, review 캡.
- 로컬 가게 + Round 3 buyer end-to-end(가짜 `order.json`, `shops.json(offers)`): `I want to buy lamp (LAMP-01)` → 3회 호가 → 110에 체결,
  서명 영수증 + `"accept": {"cert": true, "grades": ["A","B"]}` 출력 확인.
- `scripts/market_test_week07.py .` — `logs/market_test_local.txt`.
