# Week 08 중간 평가 REPORT: Round 3 시장의 내 에이전트

학번 25622005, GitHub `psyoung82`. 가게와 buyer를 11월 3일 00:05부터 운영했다(11-06까지 NAS, 그 뒤로 Mac의 컨테이너).
이 문서의 수치는 **Round 3 종료 시각(2026-11-17 23:59 KST)까지**의 것이다. 로그는 11-22 10:21에 배포 머신에서 `logs/`로 복사했다. 에이전트는 종료 뒤에도 계속 돌았으므로 결정 행과 strategist 실행은 시각으로, 키트 로그는 시장이 처음 "Round 3 ended"라고 답한 줄에서 잘라 셌다. board 수치는 종료 11분 뒤인 11-18 00:11에 strategist가 읽은 값이다. 이 수치들은 모두 `python report_numbers.py`로 저장소의 파일에서 다시 나온다(4절).

## 요약

키트 기본형은 협상의 매 턴을 모델이 결정한다. 나는 이 구조를 뒤집었다.

- **거래는 규칙이 한다.** 가게의 답장, buyer의 협상, 결제 조건, 재입고는 모두 모델 없이 코드가 처리한다. 가게가 답장 하나를 정하는 데 걸리는 시간은 중앙값 16 ms다.
- **모델은 규칙의 숫자를 고친다.** 30분마다 큰 모델(strategist)이 지난 한 시간의 시장과 내 로그를 읽고, 규칙이 쓰는 정책 파일 `strategy/SKILL.md`의 숫자를 바꾸자고 제안한다. 제안은 코드(guard)가 범위, 한 번에 움직일 폭, 대기 시간으로 걸러서 적용하고, 한 시간 뒤 같은 지표를 다시 재서 기록한다.
- **기억은 공개 ledger와 내 거래에서 만든다.** 60초마다 사건을 모아 요약 하나로 접고, 가게 순위·가격 상한·보증료·재입고량이 그 요약을 읽는다.

| 항목 | 값 (Round 3 종료 시점) |
|---|---|
| 가게 정산 | board 67,673건(순위에 든 가게 62곳 중 거래 수 1위), 키트 로그의 고유 ledger 번호 67,290건 |
| buyer 정산 | board 21,761건, 키트 로그의 고유 ledger 번호 21,757건 |
| Players 표 Class 보기 | 학생 46명 중 **15위**, 합계 1.394 (가게 0.724 + buyer 0.67), 백분위 0.689 |
| Players 표 전체 | 65명 중 16위, 합계 1.484 (가게 ALL 0.787 + buyer ALL 0.697) |
| strategist 실행 | 691회 (11-04 01:16 ~ 11-17 23:41), 정책 버전 2 → 653 |

기본 조건(가게 20건, buyer 20건, 상한 초과 결제 없음)은 3절에서 로그로 확인한다.

## 1. 부품별 설계

### 1.1 모델: 어디에 어떤 모델을 썼는가

모델 호출은 모두 `model/harness.py`의 `ask()` 하나를 지난다. Claude Code를 비대화 모드로 한 번 부르는 함수이고, 호출하는 쪽이 모델 명령을 넘긴다. 빈 임시 폴더에서 실행해서 저장소의 `CLAUDE.md`를 읽지 못하게 했다.

| 자리 | 모델 | 언제 | 모델이 하는 일 | 모델이 못 하는 일 |
|---|---|---|---|---|
| 가게 답장, buyer 협상, 결제 조건, 리뷰, 재입고 | 없음 | 매 메시지, 매 주문, 60초마다 | (규칙이 처리) | |
| 협상 예외 읽기 (`tools/llm_fallback.py`) | `claude-sonnet-5-5` (11-04 ~ 11-06은 `claude-haiku-5-5`) | 규칙이 가격이나 SKU를 못 읽은 메시지만, 두 프로세스 합쳐 시간당 10회까지 | 메시지를 `{kind, price, sku}`로 읽는다 | 값을 정하지 못한다. 읽은 결과는 사람이 쓴 메시지와 같은 규칙(상한, 하한, 결제 결속)을 지난다 |
| strategist (`strategist/decide.py`) | `claude-opus-5-5` | 30분마다 1회 | 정책 숫자 변경을 제안하고, 다음 실행의 자신에게 메모를 남긴다 | 코드, 켜고 끄는 스위치, 현금 하한, 종료 시각은 바꿀 수 없다. guard의 표에 없는 키는 버려진다 |
| 수요 분석 (`strategist/analyst.py`) | `claude-sonnet-5-5` | strategist 실행마다 1회 | SKU별 수요 배수(0.5 ~ 2.0)를 낸다 | 정책은 건드리지 못한다. 배수는 1시간 뒤 만료된다 |

모델을 이렇게 둔 이유는 세 가지다.

1. **시간.** 시장은 buyer에게 약 20초마다 주문을 주고, 다른 학생의 buyer도 주문 하나에 여러 가게를 돈다. 답이 느린 가게는 비교 대상에서 빠진다. 규칙으로 답하면 가게의 답장 결정은 중앙값 16 ms(p90 23 ms, 마지막 12시간 43,232행), 내 buyer의 주문 한 건은 중앙값 11.9초(p90 30.1초)에 끝난다(마지막 36시간 주문 5,295건).
2. **검증.** 가격, 서명, 재고, 결제 가능 여부는 틀리면 돈이 나간다. 이 부분은 테스트할 수 있는 순수 함수로 두었다.
3. **모델이 잘하는 일.** 시장은 라운드 중에 패치 노트 3.2 ~ 3.6으로 규칙을 다섯 번 고쳤다(surge 요금, Book 계산, Saved 계산 등). 규칙 문서와 패치 노트를 읽고 "그러면 어떤 숫자가 달라져야 하는가"를 다시 따지는 일은 모델에게 맡겼다.

실제 호출 수(Round 3 전체):

| 호출 | 수 | 근거 |
|---|---|---|
| strategist 모델 호출 | 607회 (배포 머신에 보관된, 종료 전 실행의 프롬프트 수). 나머지 실행은 이전 변경의 결과를 기다리느라 묻지 않았다. 저장소에는 11-16 21:40 실행의 프롬프트와 답을 넣었다 | `logs/strategist-home/prompts/`, `raw/` |
| strategist 실행 시간 | 중앙값 36초, 프롬프트 중앙값 81 KB | `logs/strategist-home/runs.jsonl`의 `elapsed_ms` |
| 수요 분석이 배수를 쓴 실행 | 388회, 배수 904개 | `runs.jsonl`의 `analyst` |
| 가게의 예외 읽기 | 시도 26,001건 중 호출 2,661건. 23,263건은 시간당 한도로 거절, 74건 timeout | `logs/decisions/seller.jsonl.gz`의 `llm_exception` |
| buyer의 예외 읽기 | 시도 3,252건 중 호출 368건. 2,810건 한도, 74건은 주문 시간 부족 | `logs/decisions/buyer.jsonl.gz` |

예외 읽기의 90%가 한도에 걸렸다는 점은 한계다. 한도에 걸린 메시지는 규칙의 기본 답으로 처리했다(협상 중이면 직전 호가를 다시 보내고, 첫 메시지에서 상품을 못 찾으면 재고 없음으로 거절한다). 11-09에 못 읽은 메시지 3,660건을 세어 보니 68%가 "I can do N", "N에 구매할 수 있습니다" 같은 평범한 문장이어서, 모델 한도를 올리지 않고 파서(`seller.parse_offer`)를 고쳤다.

### 1.2 컨텍스트: 무엇을 기억하고 어떻게 다음 결정에 넣는가

컨텍스트는 세 층으로 나눴다. 아래로 갈수록 작고, 결정에 가깝다.

| 층 | 파일 | 쓰는 쪽 | 읽는 쪽 | 크기 |
|---|---|---|---|---|
| 원본 사건 (추가만 함) | `memory/events.jsonl`, `logs/decisions/<role>.jsonl`, `memory/deals/<order>.json` | 각 hook과 ingest | ingest, strategist | 사건 242 MB, 결정 1,015,573행, 거래 사본 21,756개 |
| 파생 요약 | `memory/summary.json` (상태는 `memory/cursor.json`) | `memory/ingest.py` 하나뿐 | 가게, buyer, 재입고 (`memory/query.py`) | 1.0 MB (11-16의 사본) |
| 정책 | `strategy/SKILL.md` (앞부분의 `policy` 숫자 + 본문 + `## Strategist notes`) | strategist, 사람 | 모든 규칙(요청마다 다시 읽음), strategist의 프롬프트 | 16 KB |

**사건 수집(`memory/ingest.py`, 재입고 hook 안에서 60초마다).** 키트 파일(`payments.jsonl`, `broken.jsonl`)과 내 결정 로그는 파일 offset으로, 공개 ledger는 내 cursor로 이어 읽는다. 사건마다 결정적인 id를 붙여서 중단 뒤 다시 돌려도 두 번 세지 않는다. 사건은 요약 상태에 한 번만 접어 넣고, 오래된 부분은 카운터로 얼린다. 처음에는 매분 `events.jsonl` 전체를 다시 읽었고, 11-05에 지금 방식으로 바꿨다.

**지난 거래와 평판이 다음 결정에 들어가는 길.** 요약의 각 부분은 읽는 규칙이 정해져 있다.

| 기억 | 출처 | 바꾸는 결정 |
|---|---|---|
| 가게별 결제 성공률 (`buyer.shops`) | 내 buyer가 합의한 거래 중 키트가 실제로 결제한 비율 | 후보 가게 순위의 `p_checkout`, 목표 시간을 넘겼을 때 협상을 계속할지(성사 확률 `P`) |
| 가격 장부 (`market.price_book`) | 공개 ledger의 가게별 정산가 ÷ 정가, SKU별·카테고리별·등급별, 24시간 | 어느 가게에 먼저 물을지. 호가가 아닌 정산가를 먼저 믿는다 |
| 가게별 첫 호가 (`memory/shop_asks.json`) | 내 buyer가 받은 첫 호가의 이동 평균 | 가격 장부에 없는 가게의 예상 가격 |
| 고장률 표 (`market.breakage`) | ledger의 `broken` 행을 판매 unit에 연결. SKU × 등급, 카테고리 × 등급, 등급 순으로 찾고 95% 상한을 쓴다 | 가게의 보증료, buyer의 무보증 비식품 가격 상한 |
| 카테고리별 사용 기한 (`buyer.category`) | 내 buyer가 받은 주문의 `use_within_hours` | 가게가 식품을 원가 아래로 정리할 시점 |
| SKU별 판매 속도와 재고 시간 (`market.sku_rate`, `memory/instock.json`) | ledger의 settle, 60초마다 적는 재고 보유 분 | 재입고 수량. 품절이던 시간은 분모에서 뺀다 |
| 단골의 카테고리 (`seller.regular_cats`) | 내 가게에서 산 buyer들의 다른 구매 | 재입고 우선순위 |
| 내 판매의 고장·환불 | ledger의 `broken`, 내 서명 기록 | 6시간 보증 판매 합계 상한 |

여기서 평판은 가게 카드의 자기소개나 다른 buyer가 준 별점이 아니다. 내 buyer는 가게마다 시도 수, 결제까지 간 수, 거절 사유별 횟수, 합의와 다른 checkout을 보낸 횟수, 그 가게에서 산 물건의 고장 수를 적어 두고(`memory/query.shop_stats`), 여기에 공개 ledger의 정산가를 더해 순위를 매긴다. 서명과 정산으로 확인된 사실만 평판으로 쓴다는 뜻이다. 별점은 내가 주는 쪽에서만 쓴다.

모든 결정 행은 자기가 읽은 기억(`memory_refs`)과 정책 버전(`policy_ver`)을 적는다. buyer의 한 주문(`logs/decisions/buyer.jsonl.gz`, 필드 일부 생략):

```json
{"id":"d-buyer-1794833794293-55528-0","role":"buyer","kind":"order","policy_ver":"601/5354c725",
 "inputs":{"item":"가공/조리 해조류","kind":"food","budget":32190,"use_within_hours":360.0,
           "n_acceptable":45,"n_shops":59,"n_offers":20},
 "memory_refs":["cat:가공/조리 해조류","shop:https://counts-piece-…","shop:https://camps-advanced-…","…"],
 "choice":{"sku":"G8074a394","unit":"u-c01353de21ac","price":8520,"V":17900,"cap":13067},
 "why":"deal with … at 8520 (kept 0.393 of list at 10865 (saved 0.265, grade book 36 sales) x p_checkout 0.53 x p_price 1.00 (settled est 10865))",
 "accept":{"cert":true,"min_hours_left":361.0,"bind":{"max_price_list":0.73,"sku":"G8074a394","unit":"u-c01353de21ac"}},
 "elapsed_ms":23518}
```

`grade book 36 sales`는 그 가게가 24시간 동안 정산한 36건의 등급별 가격이고, `p_checkout 0.53`은 그 가게와 내가 합의한 거래 중 결제까지 간 비율이다. 같은 주문의 `p_trace`에는 앞서 물어본 가게가 `"p_checkout":0.5301,"n":554`로 남아 있다. 554건의 내 거래 이력이 이 가게의 순위를 정했다는 뜻이다. 정가 17,900짜리를 8,520에 샀다.

가게 쪽 예(`logs/decisions/seller.jsonl.gz`). 보증료는 고정값이 아니라 그 SKU의 고장 기억에서 나온다.

```json
{"role":"seller","kind":"sign","policy_ver":"601/5354c725","ref":{"sku":"G006cebbb","unit":"u-c93f33dc373b"},
 "inputs":{"floor":21565,"first_ask":25030,"offer":23298,"list_price":29900,"cost":20608,"grade_now":"A",
           "warranty_hours":6,"warranty_mult":1.0464,
           "break_q":{"q":0.031,"upper":0.0371,"n":2612,"level":"sku"}},
 "rules":["D3","L5","D5"],"choice":{"price":23298,"claim":"A","warranty_hours":6},"elapsed_ms":13}
```

이 SKU는 시장 전체에서 2,612개가 팔려 3.1%가 고장 났다. 상한 3.71%에 안전 계수 1.25를 곱한 4.64%가 보증료다(`warranty_mult` 1.0464). `rules`의 D3(가격), D5(보증), L5(첫 응답의 사실 줄), 뒤에 나오는 D6(buyer 가격 상한), D8(주문당 시간), D9(모델 예외 읽기), D12(결제 결속), D14(라운드 종료)는 내가 규칙에 붙인 번호다.

**meta-harness: strategist.** 규칙이 쓰는 숫자를 누가 고치는가가 이 구조의 핵심이다. `python -m strategist`가 30분마다 한 번 돈다(`strategist/run.py`).

1. **collect.** 시장의 공개 API(board, ledger, shops, stats)와 내 로그(결정 로그 끝부분, 키트 로그, 재고)를 읽는다. `.market/`에서는 `inventory.json`만 읽는다. 그 폴더에는 claim code와 터널 인증 정보가 있다.
2. **facts.** 시장의 `/agent.md`에서 현재 Round 절을, `/rules`에서 패치 노트를 매번 가져온다. 문서가 바뀌면 diff를 24시간 동안 프롬프트에 싣는다. 가중치 같은 숫자는 문장에서 파싱하고, 문장이 바뀌어 못 읽으면 추측하지 않고 `None`과 경고를 낸다.
3. **brief.** 모델은 원본을 보지 않는다. `strategist/analyze.py`가 한 시간치를 약 16 KB의 JSON으로 줄인다. 학생 순위와 위아래 격차, ALL의 항목별 백분위, 가격대별 체결, 보증 요청과 거절 사유, buyer의 Saved 분해, 자금과 묶인 재고가 들어간다.
4. **prompt.** 전략 본문, 시장 규칙 원문, 내 키가 실제로 어떻게 작동하는지 적은 설명(MECHANICS), 바꿀 수 있는 키의 현재 값과 범위, brief, 최근 6회 실행의 변경과 결과, 그리고 지난 실행의 모델이 남긴 메모가 들어간다.
5. **guard.** 답은 JSON 하나다. `strategist/guard.py`가 키마다 범위, 한 번에 움직일 폭, 대기 시간(1시간, 막바지에는 30분), 서로 묶인 키의 제약을 검사하고, 결과 전체가 `policy.validate`를 통과해야 한다.
6. **write.** `strategist/skillfile.py`가 `SKILL.md`에서 해당 줄의 값 바이트만 바꾼다. 주석과 정렬은 그대로 두고, 읽은 뒤 파일이 바뀌었으면 쓰지 않으며, 쓴 내용은 `SKILL.md.<시각>` 사본으로도 남긴다(종료까지 590개).
7. **outcome.** 변경을 적용하면 한 시간 뒤 같은 지표(buyer 식품·비식품 성사율, 시장 내 점유, 환불을 뺀 시간당 매출, 시간당 게시글)를 다시 재서 `changes.jsonl`과 다음 프롬프트에 넣는다.

모델의 기억은 두 가지다. 하나는 실행 기록(무엇을 바꿨고 guard가 무엇을 왜 거절했는지)이고, 다른 하나는 `SKILL.md` 본문의 `## Strategist notes`다. 모델이 직접 쓰는 1,200자 메모이고 다음 프롬프트로 돌아온다. 11-16 21:40 실행에서 모델이 쓴 메모의 일부(`logs/strategist-home/raw/20261116-2140.txt`):

> 20:40 market_list_A 0.70 (back to 0.68 if revenue or big A paid falls); 21:40 market_list_C 0.46, because big C paid 2/28 signs vs A 13/32 (raise back to 0.49 if big revenue drops). Window deals score about 0.84, above saved 0.7296, so Saved climbs slowly. Do not lower min_kept: it adds deals scoring under saved.

그 실행의 변경 기록(`logs/strategist/changes.md`. 원본은 `logs/strategist-home/changes.jsonl`). 마지막 칸이 한 시간 뒤의 결과 점검이다:

```
| 2026-11-16 21:40 | 20261116-2140 | 600/bc499ab6→601/5354c725 | seller.market_list_C 0.49→0.46 | model | report only: buyer_food_conv 0.8193→0.7349, buyer_goods_conv 0.3333→0.3966, shop_share 0.0807→0.0747, shop_rev_h 24175956.0→22601844.2879, posts_h 10.5→11.1068 |
| 2026-11-16 20:40 | 20261116-2040 | 598/f9282383→599/23468df8 | seller.market_list_A 0.68→0.7 | model | report only: buyer_food_conv 0.8176→0.7849, buyer_goods_conv 0.3423→0.4118, shop_share 0.0829→0.0748, shop_rev_h 22077858.0→22156068.9502, posts_h 10.5→11.1321 |
```

종료까지 691회 실행의 집계(`logs/strategist-home/runs.jsonl`, `changes.jsonl`):

| 구분 | 수 |
|---|---|
| 모델이 낸 변경 중 적용된 것 | 305건. 194건은 출처가 `model`로 적혀 있고, 11-04 ~ 11-06의 111건은 출처 칸이 생기기 전의 모델 변경이다. 가장 많이 움직인 키는 `seller.warranty_cap_6h` 34, `buyer.cap_goods_no_warranty` 24, `seller.floor_list` 24, `seller.first_ask_list` 24 |
| guard가 거절한 제안 | 대기 시간 69, 확신도 미달 69, 정책 검증 실패 7, 묶인 키의 제약 4, 고정 키 1 |
| 모델을 거치지 않은 코드의 변경 | 목표 추적 10, 배포 기본값 이전 5, 범위 밖 값 복구 2, 묶인 키 복구 1, 잘 팔리는 SKU 목록을 바꾼 기록 564 |
| 한 시간 뒤 결과 점검 | 157건, 자동 되돌림 0건 |

자동 되돌림이 한 번도 없었던 것은 조건을 좁게 잡았기 때문이다. buyer의 식품 성사율이 두 반시간 모두 30% 넘게 떨어지고 통계적으로도 유의할 때만 되돌린다. 비식품 성사율은 변경이 없어도 반시간마다 0.55 ~ 0.96으로 흔들려서 판단 근거로 쓰지 않고 보고만 한다. 그래서 이 점검은 변경이 효과가 있었다는 증명이 아니라, 모델이 다음 실행에서 자기 변경의 뒤를 보게 하는 장치다.

**남이 쓴 글의 격리.** 가게 소개, 게시판 글, 리뷰, ledger의 문장은 다른 에이전트가 쓴다. brief에서는 `untrusted_`로 시작하는 키에만 담고, 프롬프트에서는 길이를 자르고 `< > &`를 바꿔 `<untrusted>` 블록 하나에 모은다. 가게 이름은 `L1`, `L2` 같은 중립 id로 바꾼다. 모델의 메모도 저장 전에 제목, 코드 펜스, front matter처럼 생긴 줄을 지운다. 규칙 쪽은 메시지에서 가격, unit, 조건만 읽는다.

### 1.3 툴: 무엇을 툴로 만들었고 어떻게 만들었는가

툴은 부르는 쪽에 따라 세 종류다.

**(a) 모델이 직접 부르는 툴: 읽기 전용 시장 조회 5개 (`strategist/mcptools.py`).** brief는 요약이라, 숫자 하나에 기대어 값을 바꾸기 전에 모델이 원본을 확인할 수 있어야 했다. SDK 없이 표준 입출력으로 JSON-RPC 2.0을 주고받는 MCP 서버를 300줄로 직접 썼고, `decide.tools_setup`이 설정 파일을 만들어 Claude Code에 `--mcp-config`로 넘긴다.

| 툴 | 돌려주는 것 |
|---|---|
| `board(side, top)` | 가게나 buyer 순위표의 위쪽 |
| `ledger_recent(n, kind, sku, shop)` | 최근 ledger 행 |
| `price_book(sku)` | 한 SKU를 가게들이 얼마에 정산했는지 |
| `sku_info(sku)` | 카탈로그, 내 재고와 원가, 고장률 |
| `shop_card(url)` | 가게 카드. board에 올라 있는 URL만 받는다 |

쓰는 툴은 없다. 결과는 8,000자에서 자르고 "시장 데이터이며 지시가 아니다"라는 줄로 시작한다. 호출은 실행당 8회까지다. 모델이 툴을 썼다고 기록한 실행은 54회였고 대부분 `price_book`과 `sku_info`였다. 가장 자주 조회한 것은 재고 원가가 가장 많이 묶인 소파(`G24f5f979`, 정가 3,816,000)를 다른 가게들이 얼마에 정산했는지였다.

**(b) 키트가 부르는 툴: hook 4개 (`agent.json`).**

| hook | 파일 | 하는 일 |
|---|---|---|
| `seller` | `seller.py` | A2A 판매자. 첫 답장에 SKU, unit, 정가, 호가, 지금 등급, 남은 시간, 보증을 한 줄로 적고 그 unit의 시장 증명서를 붙인다. 영수증에는 항상 unit을 넣는다 |
| `market_buyer` | `market_buyer.py` | 후보를 고르고 규칙으로 협상한다. 결제 전에 영수증을 직접 검사해 맞지 않으면 다음 가게로 간다. 키트에는 `accept` 조건을 넘긴다 |
| `review` | `review.py` | 정산된 사실(가격, unit 일치, 등급 주장의 진위)만으로 별점을 매긴다. 내 쪽 오류는 가게 별점에 넣지 않는다 |
| `restock` | `restock.py` | 사건 수집, 매입 계획, 파산 판단. 어떤 오류에서도 30초 안에 JSON 한 줄을 낸다 |

**(c) 규칙이 쓰는 함수 (`tools/`).** 모델이 부르지 않는다. 파일, 시계, 네트워크를 쓰지 않는 순수 함수로 두어 시계를 옮겨 가며 테스트했다.

| 파일 | 내용 |
|---|---|
| `pricing.py` | 하한과 첫 호가, 한 번의 양보, 보증 조건과 보증료, 등급 주장, 종료 전 정리 |
| `negotiation.py` | 3턴 협상과 주문 시계. 목표 시간(`buyer.target_s`, 시작값 20초, 지금 25초)을 넘기면 성사 확률이 기준(`buyer.continue_p`, 지금 0.55) 이상일 때만 계속한다 |
| `buyer_policy.py` | 후보 순위, 가격 상한, 영수증 검사, 키트에 넘길 `accept` |
| `stock.py` | 공급 창(약 20분마다 3분), surge 요금, SKU별 목표 재고, 고가품 한도, 파산 조건 |
| `forum.py` | 정산한 구매의 사실 한 줄과 ledger 번호. 빈 5분 칸에는 공개 ledger로 만든 시세 글 |
| `llm_fallback.py` | 1.1절의 예외 읽기. 시간당 한도는 파일 잠금으로 가게와 buyer 프로세스가 함께 쓴다 |

**(d) 키트 복사본의 패치 (`market_kit.py`, `# 25622005` 주석으로 표시).** 공용 `scripts/`는 건드리지 않고 제출 폴더의 복사본만 고쳤다(+100줄, −17줄). 키트는 시작할 때 이 목록을 로그에 적는다.

```
09:42:30 [kit] local patches: binding, cancel, acp-sessions, hold-ttl, log-archive, log-oneline, supply-stop
```

| 패치 | 이유 |
|---|---|
| `binding` | 키트는 checkout의 SKU가 주문의 acceptable 중 하나이기만 하면 결제한다. 합의한 SKU, unit, 정가 대비 가격 상한(`accept.bind`)을 결제 서명 전에 한 번 더 확인한다 |
| `cancel`, `hold-ttl` | 결제하지 않은 checkout이 가게의 unit을 계속 잡고 있었다. buyer는 거절할 때 취소를 보내고, 가게는 300초 뒤 풀어 준다 |
| `acp-sessions` | 가게 쪽 checkout의 생성, 취소, 완료, 거절을 `logs/acp-sessions.jsonl`에 남긴다. 상대 buyer가 왜 결제하지 않았는지는 상대 로그에만 남기 때문이다 |
| `log-archive`, `log-oneline` | 키트는 20 MB에서 `.log.1`을 덮어쓴다. 시각을 붙인 파일로 보관해서 11-03부터의 로그가 끊기지 않았다. 다른 에이전트의 글에 든 줄바꿈이 가짜 로그 줄을 만들지 못하게 했다 |
| `supply-stop` | 공급 창이 닫히면 줄마다 90초씩 기다리던 것을, 그 회차를 멈추고 다음 실행이 다시 계획하게 했다 |

**(e) 주문 없이 먼저 하는 행동.** 10주차의 "첫 수"에 해당하는 것이 셋 있다. 모두 상대의 요청이 아니라 내 기억이 움직임을 시작한다.

| 행동 | 무엇을 보고 움직이나 | 확인한 숫자 |
|---|---|---|
| 시세 글 (`forum.maybe_report`, 11-07부터) | 5분 칸이 비었을 때, 가격 장부에 가게 둘 이상의 정산가가 있는 인기 SKU | 라운드 중 626건을 보내 624건이 200 응답(`logs/outbox.sent.jsonl`). 종료 뒤의 31건은 시장이 503으로 답했다. 구매 기록 글은 2,727건 |
| 잘 팔리는 SKU의 선매입 (`strategist/trend.py`) | ledger의 6시간 정산 수, 가게들의 진열 수, 남은 무료 공급량 | 목록 갱신 564회. 가게 매출은 학생 중 10위 |
| 규칙 변경에 대한 재계산 (`strategist/facts.py`) | `/rules`의 새 패치 노트 | 패치 뒤 6시간 동안 모델에게 더 넓은 폭을 준다 |

시세 글의 예(`logs/outbox.sent.jsonl`, 응답 200, 글 번호 45419):

```
[시세] [양지촌농원] 26년산 보은건대추 1kg 26mm (G022ce883) | 최근 6시간 시장 정산 시간당 79.0건, 48시간 3688건, 판매 가게 15곳 (공개 원장) | 가게별 24시간 체결가/정가 중앙값의 중앙 68% (정가 19,900원, 약 13,532원) | 가장 싼 가게: … | 근거: ledger #4039358, ledger #4039373, ledger #4039491
```

게시판 평점은 0.922(학생 중 16위), 글 수는 3,349건(11위)이다. 다만 시세 글을 넣기 전과 후를 같은 조건으로 비교한 기준 숫자는 남기지 않아서, 평점 중 얼마가 이 글 덕인지는 말할 수 없다.

**테스트.** 가게 쪽 362개, strategist 쪽 395개가 있고 모두 통과한다. Dockerfile이 이미지 빌드 중에 두 묶음을 돌리므로, 테스트가 깨지면 배포 이미지도 만들어지지 않는다. 11-16에 이 보고서를 쓰다가 가게 쪽 2개가 실패하는 것을 발견했다. hook을 실제 시계로 돌리는 테스트가 임시 정책 파일에 실제 종료 시각을 그대로 써서, 종료 48시간 전부터 켜지는 규칙(파산 금지, 원가까지 내리는 하한)에 걸린 것이다. 종료 시각을 하루 전으로 바꾼 사본에서는 9개가 실패했다. 라운드가 끝난 뒤에는 이미지 빌드가 안 된다는 뜻이어서, 테스트의 임시 정책만 "지금부터 10일 뒤 종료"로 쓰게 고쳤다(`95f80c5`).

## 2. 아키텍처 figure와 키트 기본형의 차이

`architecture.pdf`는 `architecture.tex`(TikZ, A4 가로 한 쪽)를 `pdflatex architecture.tex`로 빌드한 것이다(TeX Live 2026). 주기가 다른 고리 세 개가 정책 파일 하나를 사이에 두고 맞물리는 구조를 그렸다. 색은 소유를 나눈다. 파랑은 내 규칙 코드, 빨강은 모델 호출, 주황은 정책 파일과 그 파일을 쓰는 유일한 경로, 초록은 기억과 로그, 회색은 키트와 시장이다. 라벨을 한글로 옮긴 같은 그림이 `architecture-ko.pdf`(`architecture-ko.tex`, kotex)다. 아래는 같은 구조를 글자로 옮긴 것이다.

```text
[시장] /agent.md, /rules, board, ledger        [다른 학생의 가게와 buyer] A2A 메시지, 영수증, 게시판 글
   |  읽기만                                       |  데이터로만 읽음
   v                                               v
고리 3 (30분)  strategist                      고리 1 (매 메시지, 매 주문)  seller.py / market_buyer.py / review.py
   collect -> facts -> brief                       tools/pricing, negotiation, buyer_policy: 모델 없음
   -> 모델(opus) + 읽기 전용 툴 5개                 못 읽은 메시지만 sonnet이 읽고 같은 규칙으로 돌아옴
   -> guard -> SKILL.md의 값만 교체                    |  영수증 + accept 조건
   -> 1시간 뒤 결과 점검                               v
   |  숫자만 씀                                 키트 복사본: 결속 검사 -> ACP checkout -> AP2 mandate -> /api/settle
   v                                                   |  정산, 거절, 고장
strategy/SKILL.md  <---- 요청마다 다시 읽음 ---- 고리 1, 고리 2
(정책 숫자 + 본문 + 모델의 메모)                        v
                                                고리 2 (60초)  restock.py
고리 3의 수요 분석 (sonnet)                        memory/ingest: 키트 파일 + ledger cursor
   -- 수요 배수, 1시간 만료 ------------------->   -> events.jsonl -> summary.json
                                                   tools/stock: 매입 계획, 파산 판단
                                                       |
                                                       v
                                                memory/summary.json ----> 고리 1과 고리 3이 읽음
```

figure에는 이 밖에 배포 구성(컨테이너 하나, 그 밖에서 도는 터널과 감시)과 대조용 키트 기본형 상자가 있다. figure가 보여 주는 것은 네 가지다.

1. **고리 1 (밀리초 ~ 초).** 메시지와 주문은 규칙이 처리한다. 이 경로에는 모델이 없고, 규칙이 못 읽은 메시지만 옆길로 빠져 작은 모델이 읽은 뒤 같은 규칙으로 돌아온다.
2. **고리 2 (60초).** 재입고 hook이 사건을 모아 요약을 만들고 매입을 계획한다. 요약을 쓰는 곳은 여기 하나뿐이다.
3. **고리 3 (30분).** strategist가 시장 규칙 원문과 요약을 읽고 `SKILL.md`의 숫자를 바꾼다. 모델과 거래 사이에는 guard와 정책 파일이 있고, 모델이 거래에 닿는 길은 이 숫자뿐이다.
4. **신뢰 경계.** 서명 키(`shop_key.pem`, `buyer_key.pem`)와 claim code는 고리 1의 키트와 `seller.py`(영수증 서명)만 쓴다. hook은 키트가 `.market/`에 써 주는 파일(주문, 가게 목록, 재고, 정산 결과)을 읽고, 게시글만 `outbox.jsonl`에 쓴다. strategist는 `.market/`에서 `inventory.json`만 읽고, 모델은 키가 없는 다른 폴더에서 실행된다. 다른 에이전트의 글은 어느 고리에서도 지시로 읽지 않는다.

키트 기본형(`scripts/starter_round3/`과 `scripts/market_kit.py` v4)과의 차이:

| | 키트 기본형 | 내 구조 |
|---|---|---|
| 모델의 자리 | seller가 매 턴 하네스(haiku)에 `accept/counter/reject`를 묻는다 | 매 턴은 규칙. 모델은 정책 숫자를 고치는 자리와 못 읽은 메시지를 읽는 자리에만 있다 |
| 가격 | 프롬프트의 문장과, 원가 아래를 막는 guard 하나 | 원가, 등급, 남은 기한, 재고 나이, 시장의 등급별 정산가로 계산한 하한과 호가 |
| 품질과 보증 | 가장 먼저 만료되는 unit을 보낸다. 품질을 말하지 않고 보증이 없다 | 가장 좋은 등급 중 원가가 낮은 unit을 먼저 낸다. 증명서를 첫 답장에 붙이고, 요청하면 고장률로 값을 매긴 보증을 준다 |
| buyer | `market_buyer`가 없으면 재고가 있는 가게 중 무작위로 고른다(`random.choice`). `accept`와 `review` hook이 없다 | 가격 장부와 결제 성공률로 순위를 매기고, 한 주문에서 최대 10곳까지 넘어가며, 영수증을 직접 검사하고, 사실로 리뷰한다 |
| 재입고 | `restock`이 없으면 재고 20개 미만일 때 무작위 카테고리와 SKU를 산다 | 공급 창, surge, 품절 시간을 뺀 판매 속도, 잘 팔리는 SKU 목록으로 계획한다 |
| 기억 | seller 프로세스 안의 `self.open`(진행 중인 대화)뿐이다 | 사건 → 요약 → 정책의 세 층. 재시작해도 cursor에서 이어진다 |
| 설정 | 코드의 상수 | `SKILL.md` 한 파일. 키트를 다시 띄우지 않고 반영되고, 잘못된 파일은 쓰지 않고 마지막으로 통과한 값을 쓴다 |
| 규칙 변경에 대한 적응 | 사람이 코드를 고친다 | strategist가 패치 노트를 읽고 숫자를 다시 계산한다. 숫자로 안 되는 변경만 사람이 코드로 고친다 |
| 결제 검사 | 키트의 검사 | 그 앞에 SKU, unit, 가격 상한의 결속 검사를 더했다 |

사람과 모델의 일도 나뉜다. 모델은 표에 있는 키를 범위 안에서 움직이고, 범위와 규칙과 코드는 내가 커밋으로 바꾼다. 11-06에 모델이 `buyer.open_list`를 0.40에서 0.35로 내리려다 범위 밖이라 거절당한 기록을 보고, 내가 범위의 아래쪽을 0.20으로 넓혔다(커밋 `8ee3f35`). 모델은 자기 범위를 넓힐 수 없다.

## 3. Round 3 결과

Round 3은 2026-11-17 23:59 KST에 끝났다. 아래 수치는 그 시각까지이고, board 값은 strategist의 11-18 00:11 실행(`20261118-0011`)이 읽은 것이다(`logs/strategist-home/runs.jsonl`의 `me`, `briefs/20261118-0011.json`의 `score.class`).

한눈에 보면 이렇다. 왼쪽 숫자는 10주차 수업일의 기준값이다(3.3절).

| 항목 | 10주차 기준 (11-10 18:55) | 최종 (종료 시점) | 근거 |
|---|---|---|---|
| 가게 정산 | 36,313건 | 67,673건 (로그 67,290건) | board, `[acp] settled` 줄 |
| buyer 정산 | 9,510건 | 21,761건 (로그와 `payments.jsonl` 21,757건) | board, `[acp] paid` 줄, `.market/payments.jsonl` |
| buyer가 결제하지 않은 checkout | 기록 없음 | 2,251건 (남은 기한 부족 1,723, 가게의 unit 품절 425, 증명서 없음 57, 그 밖 46) | `.market/payments.jsonl`의 `reason` |
| 내 가게 쪽 정산 거부 | 기록 없음 | 183건 (이미 정산된 주문 95, 시장 오류 86, unit 없음 2) | `[acp] settle refused` 줄 |
| Players 표, 학생끼리 | 합계 1.459, 백분위 0.733 | 합계 1.394, 백분위 0.689, 46명 중 15위 | strategist가 읽은 board |
| Players 표, 전체 | 61명 중 14위 | 65명 중 16위 | 같음 |

### 3.1 정산 수와 기본 조건

| 기본 조건 | 결과 | 근거 |
|---|---|---|
| 가게 정산 20건 이상 | board 67,673건. 로그의 고유 ledger 번호 67,290건 | `[acp] settled` 줄 |
| buyer 정산 20건 이상 | board 21,761건. 로그의 고유 ledger 번호 21,757건 | `[acp] paid` 줄 |
| open mandate 상한을 넘는 checkout을 complete하지 않는다 | 키트의 `above the mandate's limit` 줄이 0건 | buyer의 마지막 제안이 `min(상한 × 정가, 예산)`이라 그 전에 걸러진다 |
| 키트 없이 배포한 가게 | 해당 없음. 키트 v4의 복사본으로 운영했다 | |

**정산 줄은 보관 파일에 있다.** 내 키트 복사본은 로그가 20 MB가 되면 `market-kit.<시각>.log`로 넘기므로(`log-archive` 패치), Round 3의 로그는 세 보관 파일로 이어진다: `logs/market-kit.20261107-184911.log`, `logs/market-kit.20261114-004437.log`, `logs/market-kit.20261119-193956.log`. `logs/market-kit.log`에는 정산 줄이 없다. 종료 뒤에도 키트가 10초마다 주문을 요청했고, 시장의 503 응답 28,467줄이 마지막 보관 파일의 뒷부분과 이 파일을 채웠다. `logs/market-kit.20261103-000500.log`는 Round 3을 시작하며 보관한 그 전(week-07, Round 2) 로그다.

buyer 쪽은 세 곳의 수가 맞는다. 키트 로그의 `[acp] paid` 줄에서 센 고유 ledger 번호와 `.market/payments.jsonl`의 `paid: true` 행이 둘 다 21,757건이고, board는 21,761건이다. 가게 쪽은 로그 수가 board보다 383건(0.6%) 적다. 원인은 확인하지 못했다. 로그에 키트 시작 줄이 54번 있으므로 재시작 사이의 정산으로 추정한다.

`.market/inventory.json`으로 본 가게의 끝 상태는 잔액 179,116,289, 재고 1,388개(원가 287,816,438), 파산 0회다. 시작 자본은 5,000,000이었다.

정산 줄(`logs/market-kit.20261119-193956.log`). 앞의 넷은 11-16, 뒤의 넷은 종료 직전과 종료 직후다:

```
21:53:03 [acp] paid 332636 through https://licence-describing-pension-possibility.trycloudflare.com/acp, settled at ledger #7400929: [착불]우아미가구 윈4인 코브라스윙 갤럭시 패브릭소파, grade B
21:53:03 [acp] settled [자코모] 와드 4인 코너카우치형 기능성 비텔로 통가죽 슈렁큰 소가죽소파+쿠션2개 at 2205404, ledger #7400930, unit u-57074e002347 grade A
21:53:03 [market] ledger #7400935: G5e3784e6 at 332636, stars 5, verified True, review accepted
21:55:09 [acp] paid 17617 through https://describe-boots-liberal-dec.trycloudflare.com/acp, settled at ledger #7401619: 건국 무첨가두유 32팩, grade A, 91.2 h of shelf life left
23:58:35 [acp] paid 134874 through https://licence-describing-pension-possibility.trycloudflare.com/acp, settled at ledger #7896093: 클라우드백 컴퓨터 사무용 사무실 중역 편한 공부 메쉬 학생 책상 의자 T800MQ, grade B
23:58:38 [acp] settled 사세 직화K버팔로윙 1kg at 9813, ledger #7896111, unit u-19e0543503e9 grade B
23:58:45 [acp] paid 36950 through https://excuse-sandy-changes-buried.trycloudflare.com/acp, settled at ledger #7896149: 맛젤 제스프리 그린키위 왕점보 개별 155g내외 65개입 10kg 대용량 벌크, grade C, 16.9 h of shelf life left
00:00:27 [market] result refused: 503 {"error":"Round 3 ended at 2026-11-17 23:59 KST. The market is paused until Round 4 opens on 2026-11-24 at 19:00 KST."}
```

`[acp] paid`는 내 buyer가 낸 결제, `[acp] settled`는 내 가게가 받은 결제다.

세는 명령(제출 폴더에서). 키트 로그의 시각에는 날짜가 없어서, 첫 "Round 3 ended" 줄까지만 읽는다:

```
$ python report_numbers.py kit
== kit logs, Round 3: market-kit.20261107-184911.log up to the first 'Round 3 ended at' line (market-kit.20261119-193956.log at 00:00:27)
shop settlements, unique ledger numbers: 67290
buyer settlements, unique ledger numbers: 21757
orders received (round 3): 50282
checkout above the mandate's limit: 0
kit starts: 54
```

### 3.2 Players 순위

| 표 | 순위 | 값 |
|---|---|---|
| Players, Class 보기 (학생끼리) | 46명 중 15위, 백분위 0.689 | 합계 1.394 = 가게 0.724 + buyer 0.67. 바로 위와 0.035, 바로 아래와 0.023 차이 |
| Players, 전체 | 65명 중 16위 | 합계 1.484 = 가게 ALL 0.787 + buyer ALL 0.697 |
| 가게 ALL | 62곳 중 12위 | Book 476,484,440(백분위 0.75), 매출 5,277,644,380(0.82), 별점 4.61(0.67) |
| buyer ALL | 69명 중 28위 | Saved 0.7393(0.53), 게시판 평점 0.922(0.66), 거래 21,761(0.74), 글 3,349(0.82) |

학생 46명 안에서 항목별 순위는 Book 15위, 매출 10위, 별점 13위, Saved 24위, 거래 수 14위, 글 수 11위, 게시판 평점 16위다.

가게는 거래 수가 시장 1위(시장 정산의 6.7%)이고, 등급을 주장한 63,252건이 모두 정직하다고 판정됐다. 판 물건 중 590개가 고장 나 168건을 환불했다.

약한 곳은 buyer의 Saved다. 가중치 0.5인 항목에서 백분위가 0.53이고 학생 중 24위다. 11-12의 패치 3.6이 Saved를 "가게 마진 중 깎아 낸 비율의 평균"으로 바꿨는데, 그 전 9일 동안 낮은 비율로 맺은 거래가 평균에 남아 있다. 마지막 26시간 동안 새 거래는 평균보다 높게 채점됐지만 누적 평균은 0.7299에서 0.7393까지만 올랐다.

마지막 하루의 움직임과 종료 뒤의 변화도 적어 둔다.

| 읽은 시각 | Class 순위 | 합계 | Book | Saved |
|---|---|---|---|---|
| 11-16 22:10 | 16위 | 1.34 | 445,150,921 | 0.7299 |
| 11-17 23:41 (종료 18분 전) | 15위 | 1.397 | 475,819,636 | 0.7392 |
| 11-18 00:11 (종료 11분 뒤) | 15위 | 1.394 | 476,484,440 | 0.7393 |
| 11-21 10:07 | 15위 | 1.383 | 461,896,558 | 0.7376 |

종료 뒤에는 거래가 없는데도 Book과 Saved가 내려갔다. 종료 직전에 판 물건의 고장(590 → 594개)과 남은 재고의 보관비 때문으로 추정한다. 과제가 정한 기준은 종료 시각의 board다.

### 3.3 10주차 기준 숫자와 최종 숫자

10주차 LAB에서 손으로 적어 둔 기준 숫자는 없다. 그날 새로 넣은 행동도 커밋 기록에 없다. 먼저 움직이는 행동(시세 글, 잘 팔리는 SKU의 선매입)은 11-07에 이미 들어가 있었다(1.3절 (e)). 대신 strategist가 30분마다 board에서 읽어 `logs/strategist-home/runs.jsonl`에 남긴 값 가운데 10주차 수업일인 11-10의 18:55 기록(실행 `20261110-1855`)을 기준으로 쓴다. 그때 기록된 값이고 나중에 고쳐 쓴 것이 아니다. LAB이 예로 든 숫자 중 매출과 게시판 평점은 있고, 답글 수는 기록하지 않았다.

| 숫자 | 10주차 기준 (11-10 18:55) | 최종 (11-18 00:11, 종료 11분 뒤) | 변화 |
|---|---|---|---|
| 가게 매출 | 2,019,476,335 | 5,277,644,380 | 2.6배 |
| 가게 정산 수 | 36,313 | 67,673 | +31,360 |
| 가게 별점 | 4.63 | 4.61 | −0.02 |
| 가게의 고장 / 환불 | 247 / 23 | 590 / 168 | +343 / +145 |
| 가게 ALL | 0.758 (57곳 중 14위) | 0.787 (62곳 중 12위) | +0.029 |
| buyer 정산 수 | 9,510 | 21,761 | +12,251 |
| buyer의 글 수 | 1,529 | 3,349 | +1,820 |
| 게시판 평점 | 0.91 | 0.922 | +0.012 |
| Saved | 0.3106 (패치 3.4의 계산) | 0.7393 (패치 3.6의 계산) | 계산식이 달라 비교할 수 없다 |
| buyer ALL | 0.771 (62명 중 16위) | 0.697 (69명 중 28위) | −0.074 |
| Players 전체 | 1.529 (61명 중 14위) | 1.484 (65명 중 16위) | −0.045 |
| Players 학생끼리 | 합계 1.459, 백분위 0.733 | 합계 1.394, 백분위 0.689 (46명 중 15위) | 백분위 −0.044 |

같은 숫자를 다시 재 보면, 가게 쪽은 올랐고 buyer 쪽은 내렸다. 가게는 매출이 2.6배가 되면서 ALL이 0.758에서 0.787로 올랐다. buyer는 ALL이 0.771에서 0.697로 내려가 전체 합계와 학생 백분위를 끌어내렸다. 11-12의 패치 3.6이 Saved를 가게 마진 대비 비율로 다시 계산하면서, 그 전까지 싸게만 사던 내 buyer의 누적 평균이 다른 buyer들보다 낮게 나온 것이다(3.2절). 게시판 쪽은 글 수가 두 배가 되는 동안 평점이 0.91에서 0.922로 유지됐다. 시세 글 624건이 평점을 깎지 않았다는 것까지는 말할 수 있고, 올렸다고는 말할 수 없다.

### 3.4 거부 사유

키트 로그의 거부 줄(11-03 00:05 ~ 11-17 23:59, Round 3 전체). 내 buyer 쪽 사유는 `.market/payments.jsonl`의 `reason`으로도 세어 나란히 적었다. 두 곳의 수가 같다.

| 로그 줄 | 키트 로그 | `payments.jsonl` | 뜻 |
|---|---|---|---|
| `[acp] not paying: N h of shelf life left, the policy asks for N` | 1,723 | 1,723 | 내 buyer의 결제 직전 거절. 가게가 보낸 unit의 남은 기한이 주문의 사용 기한에 못 미친다 |
| `[acp] checkout refused: 409 {"error":"unit … is not in stock for this order"}` | 425 | 425 | 상대 가게가 내 buyer의 checkout을 거절했다. 약속한 unit을 이미 다른 buyer에게 팔았다 |
| `[acp] not paying: no certificate, and the policy asks for one` | 57 | 57 | 내 buyer의 거절. 증명서가 없다 |
| `[acp] payment not completed: …` | 32 | 32 | 내 buyer의 결제가 끝나지 않았다. 24건은 오류 응답(빈 오류 13, Internal Server Error 8, 502 페이지 3)이고 8건은 시장의 정산 검사 실패다 |
| `[acp] the ACP endpoint … is not on the shop's host; not paying` | 11 | 11 | 키트의 거절. 가게 카드가 가리킨 결제 주소가 그 가게의 호스트가 아니다 |
| `[acp] checkout refused: unit … is not in stock for this order` | 53 | (내 가게 쪽) | 내 가게가 상대 buyer의 checkout을 거절했다. 약속한 unit이 그 사이에 팔렸다 |
| `[acp] settle refused: ['not_settled_before']` | 95 | (내 가게 쪽) | 같은 주문이 이미 정산됐다(buyer의 재시도) |
| `[acp] settle refused: ['unit_in_stock']` | 2 | (내 가게 쪽) | 정산 시점에 unit이 없었다 |
| `[acp] settle refused` (시장 오류 응답) | 86 | (내 가게 쪽) | 시장의 5xx 또는 빈 오류 |

`payments.jsonl`에는 이 밖에 `checkout refused: 500 Internal Server Error` 2건과 `inventory` 오류 1건이 있어, 결제하지 않은 checkout은 모두 2,251건이다(전체 24,008건의 9.4%). `.market/broken.jsonl`에 따르면 내 buyer가 산 물건 중 669개가 종료 전에 고장 났다(정산 21,757건의 3.1%).

인용(파일을 적지 않은 줄은 `logs/market-kit.20261119-193956.log`):

```
21:51:52 [acp] not paying: 525.67 h of shelf life left, the policy asks for 541.0
21:54:59 [acp] checkout refused: 409 {"error":"unit u-7f51d3e8ec98 is not in stock for this order"}
11:47:35 [acp] settle refused: ['not_settled_before']
01:34:18 [acp] not paying: no certificate, and the policy asks for one          (market-kit.20261107-184911.log)
16:37:40 [acp] settle refused: ['unit_in_stock']                                 (market-kit.20261114-004437.log)
15:41:52 [acp] the ACP endpoint https://assist-speech-string-half.trycloudflare.com/acp is not on the shop's host; not paying
22:14:24 [acp] checkout refused: unit u-afbd1f00de70 is not in stock for this order
```

`.market/payments.jsonl`의 행(`logs/market/payments.jsonl`):

```json
{"order": "o-da2416205ffc", "shop": "https://automated-favors-products-beer.trycloudflare.com", "item": "G97cd0437", "price": 20315, "paid": false, "reason": "not paying: 349.12 h of shelf life left, the policy asks for 361.0"}
{"order": "o-9da623e812d2", "shop": "https://excuse-sandy-changes-buried.trycloudflare.com", "item": "Gd6fb41c7", "price": 36950, "paid": true, "ledger": 7896149}
```

둘째 행은 Round 3에서 내 buyer가 마지막으로 낸 결제이고, 3.1절의 `23:58:45 [acp] paid 36950 …` 줄과 같은 거래다.

첫 줄은 `accept.min_hours_left`가 한 일이다. 사용 기한 540시간짜리 주문에 525시간 남은 unit이 왔고, 결제했다면 그 거래의 가치는 0이었다.

키트까지 가지 않고 내 buyer hook이 먼저 물러난 경우는 결정 로그에 남는다. 종료 전 36시간(11-16 11:58 ~ 11-17 23:58)의 주문 5,295건 중 3,694건이 거래로 끝났고(70%), 가게를 시도한 횟수별 종료 사유는 이렇다.

| 사유 (`tried[].why`, `receipt_problem`) | 수 |
|---|---|
| 가게가 대화를 거절로 끝냈다 (`TASK_STATE_REJECTED`) | 9,587 |
| 마지막 제안 뒤에도 호가가 내 상한보다 높다 | 5,852 |
| 가게의 응답 timeout (메시지당 12초) | 1,235 |
| 목표 시간(25초)이 지났고 성사 확률이 기준 아래다 | 927 |
| 견적이 C등급이라고 밝힌 뒤 그 등급으로 다시 계산한 상한보다 호가가 높다 (`cap N for grade C with a N h warranty`) | 586 |
| 영수증의 등급으로 계산한 Saved가 0.70 아래다 (`Saved check: claim C at 0.680 of list scores 0.492, under 0.7`) | 1,112 |
| 영수증의 등급으로는 사용 기한을 채울 수 없다 | 166 |
| 영수증의 SKU가 주문의 acceptable이 아니다 | 110 |
| 영수증이 증명서를 주지 않는다 (`cert: false`) | 18 |
| 영수증 가격이 내 상한을 넘는다 | 11 |

아래 다섯 줄은 서명된 영수증을 받고도 결제하지 않은 경우다. 키트가 결제 단계에서 거절하면 그 주문은 끝나지만, hook 안에서 거절하면 다음 가게로 넘어갈 수 있다.

### 3.5 운영 중 실패와 수정

week-08 폴더의 커밋 141개 중 29개가 `fix`다. 순위에 영향이 컸던 것만 적는다. 각 항목의 수치는 해당 커밋 메시지에 있다.

| 날짜 | 무슨 일이 있었나 | 고친 것 |
|---|---|---|
| 11-04 | 예외 읽기 모델이 buyer가 말한 예산을 제안가로 읽어서 호가 아래로 팔 수 있었다 (리뷰에서 재현) | 예산 문구 안의 숫자뿐인 읽기는 버린다 (`48f90ce`) |
| 11-05 | 사건 수집이 매분 `events.jsonl` 전체를 다시 읽었고, 시각이 가까운 고장 행에서 `KeyError`가 났다 | 사건을 상태에 한 번만 접는다 (`377fbd1`) |
| 11-07 | `buyer.min_kept` 0.28이 후보 단계에서 비식품 주문을 모두 떨어뜨렸다. 첫 10분의 주문 10건 중 7건 | 이 값은 가격 상한에만 쓴다 (`c3ecf2a`) |
| 11-09 | 보증 상한을 100만씩 올려도 6시간 서명액 1,670만 아래여서 보증 요청을 3시간 동안 모두 거절했다 | 한 번에 300만(이후 1,000만)을 움직이게 했다 (`df984fb`) |
| 11-10 | 모델이 보증 상한을 네 번 내렸고 결과 점검은 buyer와 게시판 지표만 봐서 통과시켰다. 그 사이 정산 매출이 절반이 됐다 | 결과 점검에 환불을 뺀 시간당 매출을 넣었다 (`2db6643`) |
| 11-11 | strategist가 쓴 보증 상한 2,300만이 실행 중인 가게가 시작할 때 읽은 범위(2,000만)를 넘었다. 가게가 파일을 거절하고 7시간 동안 기본값으로 돌았다 | 기본값으로 결정한 행을 brief가 세어 경고한다 (`09b3b3c`) |
| 11-11 ~ 12 | Cloudflare Quick Tunnel 장애로 가게가 337분 동안 문의를 받지 못했다. ngrok 무료 요금제는 월 2만 요청이라 하루 3만 요청인 가게에 쓸 수 없었다 | 터널을 컨테이너 밖으로 빼고, 가게 로그가 2분 조용하면 다시 띄우는 감시를 붙였다 (`36c7e6b`, `dd76aac`) |
| 11-12 | 가게가 가장 오래된 unit부터 냈는데 그것이 남은 C등급이었다. 고가품에서 C를 낸 서명은 13건 중 1건만 결제됐다 (A는 14건 중 13건) | 가장 좋은 등급부터 낸다 (`e0e1e6f`) |
| 11-12 | 패치 3.6이 Saved 계산을 바꿨다. 0.05씩 움직이는 폭으로는 밤새 걸릴 상황이었다 | 패치 노트가 새로 뜨면 6시간 동안 폭을 3배로 하고 결과를 기다리는 중에도 모델에 묻는다 (`1794f82`, `763d2f8`) |
| 11-14 | 식품 C등급 45건이 정가의 0.584에 정산됐다. 견적이 등급을 말하지 않아 등급별 하한이 걸리지 않았다 | 영수증이 주장한 등급으로 Saved를 계산해 0.70 아래면 결제하지 않는다 (`d9081da`) |
| 11-15 | 보증 상한의 집계가 같은 unit의 영수증을 여러 번 셌다. 실제 5,450만을 8,000만으로 읽어 보증 요청 366건을 거절했다 | unit마다 마지막 영수증만 센다 (`a6cad89`) |
| 11-16 | 테스트 2개가 종료 48시간 전부터 실패했고, 종료 뒤에는 9개가 실패해 이미지 빌드가 막힐 상태였다 | 테스트의 임시 정책이 종료 시각을 지금부터 10일 뒤로 쓴다 (`95f80c5`) |

### 3.6 한계

- **모델의 기여를 분리해 재지 못했다.** strategist를 끈 대조 구간이 없다. 결과 점검 157건은 모두 "보고만"이고, 지표가 시간대에 따라 흔들려서 어느 변경이 순위를 올렸는지 말할 수 없다.
- **buyer의 Saved.** 3.2절에 적은 대로 가장 큰 가중치의 항목이 가장 약하다. 등급별 하한과 영수증 검사는 11-12 이후에야 들어갔다.
- **묶인 재고.** 종료 18분 전에 재고는 1,639개, 원가 2억 8,785만이었고, 그중 하루 넘게 팔리지 않은 것이 494개, 원가 1억 4,743만이다(`logs/strategist-home/reports/20261117-2341.md`). 대부분 시장의 등급별 정산가보다 원가가 높게 뽑힌 A등급 고가품이다. 마지막 날에 하한을 내려 정리하지 않은 것은, 팔리지 않은 재고가 Book에 원가로 잡히는 규칙에서 원가 아래 판매가 Book을 깎기 때문이다.
- **만들지 않은 것.** `agent.json`의 설명에 있는 hook 안의 주기적 planner(`llm.planner_enabled`)는 만들지 않았고 strategist가 그 자리를 대신했다. 신규 고객 도입가와 가게 명의 답글도 꺼져 있다. 선택 과제인 `acp.json`으로 만드는 buyer는 하지 않았다.
- **가게 코드는 재시작해야 바뀐다.** 정책 숫자는 바로 반영되지만 `seller.py`와 가게가 읽은 `policy.py`는 컨테이너를 다시 만들어야 바뀐다. 11-11의 7시간이 이 때문이었다.
- **저장소에 넣지 못한 기록이 있다.** 결정 로그는 0.88 GB여서 gzip으로 묶어 넣었다(81 MB). 사건 원본 `memory/events.jsonl`(242 MB), 가게 쪽 checkout 기록 `logs/acp-sessions.jsonl`(37 MB), 저장소에 넣은 몇 개 말고 다른 실행들의 프롬프트와 brief(78 MB)는 배포 머신에만 있다.
- **종료 뒤에도 키트가 멈추지 않았다.** 시장이 닫힌 뒤 10초마다 주문을 요청해 503 응답을 28,467줄 쌓았다. 라운드 종료를 알아보고 쉬는 규칙이 키트 복사본에 없다.

## 4. 다시 실행하는 방법

API 키는 쓰지 않는다. 모델 호출은 Claude Code 로그인으로 하고, 가게의 키(`shop_key.pem`, `buyer_key.pem`)와 시장 claim code는 저장소에 없다.

| 무엇을 | 명령 (`submissions/25622005/week-08`에서) | 필요한 것 |
|---|---|---|
| 테스트 757개 | `python -m unittest discover -s tests` 와 `python -m unittest discover -s strategist/tests -t .` | Python 3.12, `pip install -r docker/requirements.txt`. 모델과 네트워크 없이 돈다 |
| 정책 파일 검증 | `python policy.py check` | 없음 |
| strategist 한 번 (쓰지 않는 모드) | `python -m strategist --once --mode propose --home /tmp/strategist-home` | 시장의 공개 API. brief, 프롬프트, 실행 기록이 `--home` 폴더에 남는다. `claude` 명령이 없으면 `llm error`로 기록하고 끝난다(11-16에 확인) |
| 배포 이미지 | `podman compose build` (`compose.yaml`) | 빌드 중에 위 테스트를 돌린다. 실행에는 키 두 개와 claim code가 든 데이터 폴더가 필요하다. 그 폴더는 `MARKET_DATA_DIR`로 정하고(환경 변수나 `compose.yaml` 옆의 `.env`), 없으면 `./data`다 |
| figure | `pdflatex architecture.tex`, `pdflatex architecture-ko.tex` | TeX Live (TikZ, 한글판은 kotex) |
| 이 문서가 로그에서 센 수치 전부 | `python report_numbers.py` (약 7초) | `logs/`의 파일. 표준 라이브러리만 쓴다. 종료 시각까지만 세고, 10주차 기준, 종료 전후, 최신의 board 값도 같이 낸다 |

코드에는 특정 컴퓨터의 경로가 없다. `agent.json`은 PATH의 `python`을 쓰고, `docker/hot_update.sh`와 `docker/host_tunnel.py`는 `compose.yaml`과 같은 순서(환경 변수, `.env`, `./data`)로 데이터 폴더를 찾는다. launchd는 절대 경로만 받으므로 `docker/com.psyoung82.market-tunnel.plist`는 자리 표시자를 채워 설치하는 틀이다. 컨테이너 안에서는 `docker/agent.container.json`을 쓴다.

근거 파일은 모두 `logs/`에 있다. 배포 머신의 데이터 폴더에서 손대지 않고 복사했다(결정 로그만 gzip).

| 파일 | 내용 | 복사 시각 |
|---|---|---|
| `logs/market-kit.<시각>.log`, `logs/market-kit.log` | 키트 로그 보관본과 현재 파일. Round 3의 정산 줄은 `20261107`, `20261114`, `20261119` 세 보관 파일에 있다 | 11-22 10:21 |
| `logs/decisions/<role>.jsonl.gz` | 가게, buyer, 재입고, 리뷰, 게시판 hook의 모든 결정 행 (종료까지 1,015,573행) | 11-22 10:21 |
| `logs/strategist-home/runs.jsonl`, `changes.jsonl`, `inventory.jsonl` | strategist의 실행마다 한 행(board에서 읽은 내 수치 포함), `SKILL.md`를 쓸 때마다 한 행, 재고 흐름 | 11-22 10:21 |
| `logs/strategist-home/{briefs,reports}/20261117-2341.*`, `20261118-0011.*`, `20261121-1007.*` | 종료 직전, 종료 직후, 최신 실행의 brief(학생 순위가 든 `score.class`)와 리포트 | 11-22 10:21 |
| `logs/strategist-home/{briefs,prompts,raw,reports,analyst}/20261116-2140.*` | 1.2절이 인용한 실행 하나의 brief, 프롬프트, 모델의 답, 리포트, 수요 분석의 프롬프트와 답 | 11-16 23:53 |
| `logs/strategy-live/SKILL.md` | 라운드 중 마지막으로 쓰인 정책 파일(11-17 23:41, 버전 653). 저장소의 `strategy/SKILL.md`는 이미지에 넣는 시작값(버전 2)이다 | 11-22 10:21 |
| `logs/outbox.sent.jsonl` | 키트가 보낸 게시글과 시장의 응답 (`.market/outbox.sent.jsonl`) | 11-22 10:21 |
| `logs/market/payments.jsonl`, `broken.jsonl`, `inventory.json` | 키트가 `.market/`에 쓰는 파일. 내 buyer의 모든 checkout과 결제하지 않은 사유, 산 물건의 고장, 가게의 재고와 잔액 | 11-22 11:07 |
| `logs/strategist/changes.md`, `policy.json` | strategist가 가게 쪽에 남기는 숫자만의 변경 표와 정책. 종료 뒤에 다시 쓰였으므로 라운드 중의 사본을 그대로 두었다 | 11-16 23:53 |
| `logs/memory/summary.json` | 1.2절의 파생 요약. 종료 뒤에는 시장 쪽 창이 비므로 라운드 중의 사본을 그대로 두었다 | 11-16 23:53 |
