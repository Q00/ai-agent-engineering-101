# Week 08 (중간과제) 보고서 — ggari0703 (25512087)

- **가게**: `ggari-shop`. 키트 v4 위에서 `seller_r3.py`와 `restock.py`를 돌린다(`agent.json`).
- **바이어**: `ggari0703`. 키트 v4의 buyer 경로에서 `market_buyer.py`, `r3_buyer.py`, `forum_post.py`를 쓴다.
- **구조도**: `architecture.tex`, `architecture.pdf`(직접 그린 TikZ 한 장).
- **자기개선 기록**: `improve/`
  - 측정·조정 코드
  - `policy.json`
  - `changes.jsonl`(파라미터 변경 32건)
  - `reports/R3-00 … R3-44`(3시간 회차 보고서 47개)
- 시각은 KST다. 숫자의 출처는 `logs/market-kit.log`(회전본은 `logs/archive/`), `.market/payments.jsonl`, `improve/metrics/*.json`, `/api/board`다.

---

## (1) 컴포넌트 설계

### 모델: 거래 루프 안에는 LLM이 없다

- 가게와 바이어 모두 메시지마다 **코드가 답한다**(`agent.json`의 `harness` 항목).
- 키트 기본값은 메시지마다 `harness.py`로 Claude haiku를 한 번씩 부른다. 하지만 Round 0·2에서 이 방식은 문제가 있었다.
  - 사용량 한도에 걸려 응답이 10~20초씩 늦었다.
  - 가격을 잘못 읽는 사고가 났다.
  - 시장 상대 대부분이 규칙 기반이었다. 7주차 자료에 따르면 LLM buyer가 홍보 문구를 이유로 든 경우는 0.3%뿐이다.
- 그래서 결정은 모두 코드로 옮겼다.
- **모델은 한 단계 위에서 쓴다.** Claude(Cowork)를 **메타 하네스**로 썼다. 3시간마다 로그를 읽고 보고서를 쓰고 파라미터를 조정하며, 코드 변경은 제안한다. 아래 "컨텍스트" 항목과 (2)에서 자세히 적었다.

### 컨텍스트와 메모리: 파일 기반, 가게와 바이어가 공유

| 파일 | 내용 | 누가 읽나 |
|---|---|---|
| `improve/policy.json` | 손잡이: `ask_of_list` 0.89, `value_floor` 0.65, buyer `max_share` 0.70, `shelf_strikes` 3, restock 상한(`MAX_LIST_PRICE` 500,000, `STOCK_FLOOR_SHARE` 0.8, `STOCK_HOURS` 5, `PER_SKU_CAP` 0.1) | `seller_r3.py`, `restock.py`, `r3_buyer.py`가 호출마다 읽음 |
| `.market/buyer-memory.jsonl` | 협상마다 JSON 한 줄. 가게 이름, SKU, 제안 순서, 결과(deal / walk / bad_receipt / error) | `choose_shop` |
| `.market/payments.jsonl` | 키트가 남기는 결제 결과. paid 또는 refused와 사유 | `choose_shop`(가게별 거절 횟수), `forum_post.py`, `measure.py` |
| `.market/inventory.json` | 재고 단위별 원가, 유통기한, 등급 | `seller_r3.py`, `restock.py` |
| `logs/market-kit.log` | 키트 로그. `[acp] settled / paid / not paying`, checkout 409 | `seller_r3.refused_units()`(최근 1시간), `measure.py` |

- **과거 거래와 평판이 다음 결정에 들어가는 길**
  - 나쁜 영수증을 준 가게는 이름으로 영구 차단한다.
  - 인증서 없이 보낸 가게는 건너뛴다.
  - 유통기한이 모자란 식품으로 결제 거절이 3번 이상 난 가게는 식품 주문에서 건너뛴다(`shelf_strikes`).
  - 후기는 문장 그대로 쓰지 않고 숫자 세 개(베이지안 별점, 부정 영수증 언급 비율, 서로 다른 후기 수)로만 바꾼다. 광고나 지시문이 판단에 섞이지 않게 하기 위해서다.
- **메타 하네스(느린 루프)**: 측정 결과 → 회차 보고서 → 파라미터 조정 → 코드 제안이다. 3시간마다 돌았다.
  - 회차 상태와 결정은 프로젝트 문서에, 회차별 근거는 `improve/reports/`에 남겼다.
  - 그래서 다음 회차의 판단이 이전 회차의 근거를 이어받는다.

### 도구: 무엇을 도구로 만들었나

| 도구 | 하는 일 |
|---|---|
| `seller_r3.py` (협상) | 첫 호가는 정가의 일정 비율로 내되, 원가×(1+마진+보유일당 1%) 아래로는 내리지 않는다. 식품은 마지막 2시간에 원가의 절반까지 손절한다. 역제안은 항상 1 이상 내린다. 같은 제안을 두 번 받거나 세 번째 제안이면 하한가 이상에서 체결한다. 영수증에는 출고할 unit을 적고, 인증서는 항상 붙인다. |
| `seller_r3.find_unit` (11/9, 승인 후 추가) | 요청한 남은 시간("N h", "N시간")을 만족하는 unit 중 가장 먼저 만료되는 것을 고른다. 최근 1시간에 checkout 409를 두 번 낸 unit은 건너뛴다. 같은 귤 unit에서 409가 반복되던 문제가 이 변경 뒤 회차당 0~4건으로 줄었다. |
| `restock.py` (매입, 키트 hook으로 60초마다 실행) | 수요는 시장 원장의 `settle` 줄을 SKU별로 세고 반감기로 줄여 추정한다. 위험 한도: 현금 하한, SKU당 자본의 10% 이하, 재고 총량은 판매 5시간분 이하, 정가 50만 이하, surge 1.03 초과 SKU 제외, 마감 12시간 전 매입 중단. |
| `market_buyer.choose_shop` (가게 선택) | 신뢰 점수 순으로 고른다. 우리 기억이 먼저이고, 그다음 키트 피드백, 그다음 후기 숫자다. 자기 가게는 제외한다. |
| `r3_buyer.py` (가치 사다리) | 기대 잉여(정가 − 예상가)가 가장 큰 SKU를 고른다. 오퍼는 예산이 아니라 **정가**에 맞춰 올리고, 상한은 min(0.70×정가, 예산)이다. 키트에 넘기는 `accept` 조건: 인증서 필수, 식품은 사용 기한 + 여유 시간 이상 남아야 함. |
| `forum_post.py` (게시판) | 우리가 실제로 결제한 거래의 사실만 쓴다(가격/정가, 등급, 가게, ledger 번호). 투표나 유료 나빠요는 하지 않는다(`ALLOW_VOTES = False`). |
| `improve/measure.py`, `improve/tune.py` (자기개선) | measure는 로그를 읽기만 하고 지표를 낸다. tune은 `tuning.BOUNDS` 안에서 손잡이마다 한 단계만 움직인다. 목적 함수가 40% 넘게 떨어지면 되돌리고, 변경은 모두 `changes.jsonl`에 남긴다. |

테스트: Python 단위 테스트 150개 통과(11/9 기준, `test_round3_*.py` 포함).

## (2) 구조도와 키트 기본값과의 차이

`architecture.pdf`는 왼쪽에서 오른쪽으로 다섯 덩어리를 보여 준다.

1. 시장
2. 키트 v4(수정하지 않음)
3. 가게와 바이어(코드로 된 도구)
4. 공유 메모리 파일
5. 3시간 느린 루프

| | 키트 기본값 | 우리 구조 |
|---|---|---|
| 협상 결정 | 메시지마다 LLM(haiku) 한 번 호출 | 코드가 답함. 트래픽이 늘어도 지연과 비용이 늘지 않음 |
| 출고 unit | 가장 먼저 만료되는 unit, 품질 언급 없음 | 요청한 남은 시간을 맞추는 unit, 409를 낸 unit은 회피, 인증서 항상 첨부 |
| 재고 | 매입 정책 없음 | 원장 수요 기반 매입과 위험 한도 |
| 바이어 오퍼 | 예산 기준 | 정가(가치) 기준 사다리. 예산을 드러내지 않음 |
| 기억 | 실행 사이에 이어지지 않음 | 가게와 바이어가 파일 메모리와 `policy.json` 하나를 공유 |
| 개선 | 없음 | 측정 → Claude 분석·보고 → 파라미터 자동 조정(범위 안, 되돌리기 있음) → 코드는 사람이 승인 |

### 느린 루프가 실제로 바꾼 것 (일부)

| 회차 | 근거 | 변경 |
|---|---|---|
| R3-33 (11/9 22:18) | 재고 원가가 상한에 닿았고 그 88%가 정가 10만 이상이라, 싼 상품 매입이 끊겼다 | `MAX_LIST_PRICE` 3,000,000 → 500,000, `STOCK_FLOOR_SHARE` 0.7 → 0.8. 방문→거래 전환율 9% → 50%로 회복(R3-36) |
| R3-code (11/9) | 같은 unit에서 checkout 409가 반복됐고, 서명률은 장난성 저가 제안에 묻혀 있었다 | `find_unit` 변경과 진짜 협상 기준 서명률(사용자 승인 "1,2 ㄱㄱ"). 409가 회차당 0~4건으로 줄었다 |
| 11/10 15:50 | buyer Saved 순위가 활성 61명 중 약 24위 | `max_share` 0.78 → 0.70(사용자 요청) |
| R3-44 (11/13) | board Saved 0.293이 우리 계산(결제분만) 0.342보다 낮았다. 거절 1,145건을 할인 0% 거래로 넣으면 0.297이 되어 board와 맞는다 | 결제 거절된 거래가 Saved를 깎는다는 걸 확인했다. `shelf_strikes` 5 → 3. 원래 2였는데, tune의 "거절이 10% 이하면 완화" 규칙이 5까지 올려 둔 상태였다 |

- ### 11/17 구조 검토와 변경 (마감 당일, 사용자 승인 "1,2,3,4")

진단(`improve/reports/R3-review-20261117-1910.md`):
- 가장 큰 손실은 가동 시간이었다. 11/11~11/17 약 147시간 정지, 당일 16:57에도 네트워크가 끊겼다.
- 매입 중단(마감 12시간 전) 이후 오프닝이 시간당 564 → 122로 줄었다.
- endgame 영수증 466건이 모두 하한가에서 체결됐는데, 첫 호가는 하한가의 1.48배였다.
- 코드 제안은 일주일 동안 승인 대기였다.

| | 변경 | 형태 |
|---|---|---|
| ① | `ops/watchdog.sh` + launchd: 키트 로그가 10분 넘게 멈추거나 키트 프로세스가 없고 시장에 접속되면 `restart.sh --no-test` (15분 쿨다운) | 운영 |
| ② | endgame 첫 호가 ≤ 하한가 × 1.15(`endgame_ask_mult`), 마지막 6시간 하한가 = 원가 × 0.97(`endgame_floor`) | 플래그 |
| ③ | 식품 주문은 첫 메시지에서 남은 기한·인증서를 요구하고, 유통기한 거절 이력이 있는 가게가 기한을 밝히지 않으면 수락하지 않음(`shelf_verify`) | 플래그 |
| ④ | 코드는 꺼진 플래그 뒤에 테스트와 함께 넣고, 켜는 것은 손잡이 변경으로 기록한다. 지표가 40% 넘게 떨어지면 tune.py가 자동으로 되돌린다(`improve/README.md`) | 루프 구조 |

- 같은 날 파라미터도 바꿨다: `LAST_BUY_HOURS` 12 → 2(남은 재고는 원가로 Book에 잡힌다), `ask_of_list` 0.89 → 0.86.
- 테스트: `test_round3_1117.py` 9개가 통과했다.

**승인을 기다리다 끝난 코드 제안**: 식품 주문은 buyer가 서명 전에 남은 기한을 요구하고, 근거가 없으면 수락하지 않는 변경이다.
- 키트는 서명 **뒤** checkout에서야 인증서를 받는다. 그리고 거절이 잦은 가게는 대화에서 남은 시간을 말하지 않는다. 그래서 정규식을 고치는 것으로는 해결되지 않는다(`improve/reports/R3-44-20261113-1828.md`).

## (3) Round 3 결과 (제출 시점, 11/17 10:40 KST)

**board (`/api/board`)**

| | 값 |
|---|---|
| Players | 71명 중 **43위**(total 1.002). 학생 46명 중 34위(student_pct 0.244) |
| 가게 ggari-shop | ALL 0.485. 거래 6,554, 매출 400,186,936, Book 59,490,973, 별점 4.53(후기 6,368), 환불 30, 파산 0, 정직 claim 4,623/4,623 |
| 바이어 ggari0703 | ALL 0.517. 주문 22,644, 거래 7,410, 지불률 0.682, 게시판 0.926(글 1,421) |

**정산 수(로그 기준)**

- **가게**: `[acp] settled` 6,521건, 마진 71,627,149.
  - `improve/metrics` 누적, 11/11 04:36 기준. 이후 키트가 멈춰 변화 없음.
  - 첫 정산 줄 예시: `13:11:01 [acp] settled 풀무원 워터루틴 무라벨 생수 500mL X 60병(무라벨/유라벨 랜덤발송) at 9880, ledger #4737772, unit u-9821118169d4 grade B`
- **바이어**: `.market/payments.jsonl` 8,556건 중 결제 7,411건, 거절 1,145건.
  - 결제 줄 예시: `13:11:09 [acp] paid 20764 through https://…/acp, settled at ledger #4737811: 맛있는 햇 부사 사과 가정용(흠과) 중대과 10kg, grade A, 39.5 h of shelf life`

**거절 사유(바이어 측, `payments.jsonl` 집계와 로그 인용)**

| 사유 | 건수 | 로그 예시 |
|---|---|---|
| 남은 유통기한 부족 | 908 | `13:46:49 [acp] not paying: 8.80 h of shelf life left, the policy asks for 126.5` |
| 인증서 없음 | 59 | `13:54:47 [acp] not paying: no certificate, and the policy asks for one` |
| 시장이 결제 거부 | 13 | `payment not completed: … {"error":"payment refused by the market"}` |
| checkout 409(그 unit이 주문에 맞지 않음) 등 | 165 | `checkout refused: 409 {"error":"unit u-… is not in stock for this order"}` |

**정지 구간과 순위 하락**

- 가동 중 Players 순위는 23~28위였다(11/7~11/11).
- 11/11 07:39 이후 가게와 바이어 키트가 멈췄다. Mac 연결이 끊긴 뒤 키트가 다시 올라오지 않았다.
- 그 상태로 마감 전까지 거래가 없어 43위로 내려갔다.
- 가장 큰 교훈은 **가동 시간**이다. 키트를 노트북이 아니라 항상 켜진 서버에 두고, 멈추면 자동으로 다시 띄우는 감시가 필요했다.
- 최종 순위는 마감 시점 board 기준이다.

## 실행 방법

```bash
# 키트 재시작 (반드시 --no-test)
bash ~/AXtransformation/agent-market/restart.sh --no-test
# 자기개선 루프 한 회차
python3 improve/measure.py && python3 improve/tune.py --dry-run
# 테스트
python -m unittest discover -p "test_*.py"
# 구조도
pdflatex architecture.tex
```

커밋하지 않는 것: `*.pem`, `.market/admin-token`, `agent-market/join.sh`, claim code.
