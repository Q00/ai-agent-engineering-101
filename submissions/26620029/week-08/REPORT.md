# Week 08 (Midterm) REPORT: 26620029 (ddolcom)

가게 `ddolcom`과 바이어 `ddolcom`으로 Round 3(11-03 ~ 11-17 23:59)에 참여했다. 키트 v4를 GCP 서버의 Docker 컨테이너에서 돌렸고, 거래 루프는 코드가 맡았다. 모델은 두 곳에만 썼다. 하나는 바이어의 협상 수 결정과 포럼 글 문구에 쓰는 `claude -p --model haiku`이고, 다른 하나는 한 단계 위에서 1시간(초기에는 3시간)마다 로그를 읽고 정책을 고치는 Claude Code(Opus) 메타 하네스다.

> 수치는 갱신 시점(11-17 15:44 KST, `logs/monitor-1117-1543.txt`) 기준이다. 최종 순위는 종료 시각의 board로 정해진다.

## 1. 부품별 설계

### 모델: 어디에 썼고 어디에 안 썼나

| 자리 | 쓴 것 | 이유 |
|---|---|---|
| 가게 가격 결정 (`seller_r3.py`) | 코드만 | 메시지마다 모델을 부르면 응답이 7초 이상 걸리고(서버에서 `claude -p` 한 번이 약 216 MB, 7 s), 하한을 어길 수 있다. 정가 × 등급별 시작 비율에서 하한(`floor_price`)까지 정해진 순서로 내려간다. |
| 가게 문구 | haiku (기본값 꺼 둠, `WORDING_TIMEOUT = 0`) | 문구가 숫자를 바꾸면 안 되므로 숫자가 빠지거나 늦으면 코드 문장을 쓴다. |
| 바이어 협상 (`market_buyer.py`) | haiku가 각 수를 정한다 | week-06 하네스 루프를 그대로 썼다. 예산 상한은 코드가 막는다(`offer > limit`이면 보내지 않음). |
| 포럼 글 (`forum.py`) | haiku가 코드 문장을 다듬는다 (분리 실행, 45 s) | 글의 근거(ledger 번호)는 코드가 붙인다. |
| 사입 (`restock.py`) | 코드만 | 60초마다 실행되는 훅이라 모델 호출 시간이 사입 창(20분마다 3분)을 놓치게 한다. |
| 운영 판단 | Claude Code (Opus), 메타 하네스 | 아래 2절. |

### 컨텍스트와 메모리 관리

- **메타 하네스.** Claude Code 세션이 cron으로 1시간마다 같은 프롬프트(`deploy/hourly_check.txt`)를 받아 `MONITOR_RULES.md`의 규칙대로 점검한다. 규칙 파일은 한 번만 읽고, 이후에는 `git log -1 --format=%h -- MONITOR_RULES.md` 해시가 바뀌었을 때만 diff를 읽는다. 판단 기록은 `MONITOR_JOURNAL.md`에 쌓고, 매번 `tail -n 30`만 읽어 컨텍스트를 아낀다.
- **점검 출력은 파일로.** `monitor.py`(순위, 판매, 파손, 재고)와 `trend.py 3`(최근 3시간 잘 팔리는 SKU와 내 재고)를 `logs/monitor-*.txt`, `logs/trend-*.txt`로 저장하고, 모델은 grep으로 몇 줄만 본다.
- **권한과 한도는 규칙 파일에 둔다.** 실행당 변경은 하나만 한다(효과를 구분하기 위해). CASH_FLOOR와 한 단위 원가 한도처럼 파산을 막는 값은 코드 상수로 둔다. 키, push, PR은 금지 목록에 둔다.
- **에이전트 메모리(JSON).** `buyer_memory.json`(가게별 거래와 평판), `restock_memory.json`(보유, 판매, 만료, ledger 커서, 고객별 선호 카테고리), `forum_memory.json`, `promo_memory.json`. 세션이 바뀌거나 재시작해도 이 파일과 공개 ledger로 상태를 다시 만든다.
- **서버 이전.** 노트북에서 GCP VM으로 옮기면서 코드, 로그, 메모리(`/srv/market/repo`)와 실행 상태(`/srv/market/state`), 비밀(`/srv/market/secrets`, git과 이미지 밖)을 나눴다(`deploy/compose.yaml`).

### 툴

- `seller_r3.py`: A2A v1.0 가게. 영수증에 실제 단위, 등급, 시장 인증서를 넣고, A/B 상품은 파손 보증을 붙이며, 식품은 남은 시간을 밝힌다. 손님이 요구한 유통기한보다 짧은 식품은 거절한다(`[shelf] ... declined`).
- `restock.py` + `customers.py`: 공개 ledger에서 내 고객이 시장 어디서든 사는 카테고리를 세어 먼저 채운다. 다음으로 시장 매출액 상위 SKU를 판매 속도에 맞춰 깊게 산다. 마감 전략은 코드 시각으로 박았다: 고가 사입 중지 11-16 00:00(`HIGH_STOP_TS`), 전체 사입 중지 11-17 12:00(`ALL_STOP_TS`).
- `market_buyer.py`: 리뷰와 메모리로 가게 점수를 매겨 고르고(`[score]`, `[choose]` 줄), 예산은 코드가 지킨다.
- `forum.py`, `promo.py`: ledger 근거가 있을 때만 글을 쓴다. promo는 SKU 절반을 대조군으로 남겨 `promo_eval.py`로 효과를 잰다.
- 분석 툴: `monitor.py`, `trend.py`, `saved_study.py`, `supply_study.py`, `break_rate.py`, `star_price.py` 등. 정책을 바꾸기 전에 데이터를 보는 용도로만 쓴다.
- `shop-worker/`: week-07 Cloudflare Workers 가게. ACP 엔드포인트를 직접 만들려고 키트 `AcpShop`을 분석했다(`NOTES.md`). Round 3에서는 키트 가게로 갔다.

## 2. 아키텍처 figure와 키트 기본형의 차이

![architecture](architecture.png)

*그림: `architecture.pdf`(원본 `architecture.tex`)를 캡처한 것.*

`architecture.tex`(`architecture.pdf`)는 세 층을 그린다. 시장(ACP checkout, AP2 mandate, `/api/settle`, ledger), GCP 컨테이너 안의 키트와 내 훅들, 그리고 그 위에서 1시간마다 도는 메타 하네스다.

| | 키트 기본형 | 내 구조 |
|---|---|---|
| 가게 협상 | 메시지마다 하네스(haiku)가 수를 정함 | 코드의 양보 일정(정가 × 등급 시작 비율 → 하한). 모델은 문구만 맡는데, 이마저 꺼 두었다. |
| 사입 | 기본 훅 | 고객 선호 + 시장 매출액 SKU 집중 + 코드로 박은 마감 중지 |
| 재고 처리 | 없음 | 48 h 넘은 재고는 원가 90%까지, C 상품 하한 0.50 × 정가 |
| 바이어 | 하네스 루프 | 같은 루프 + 리뷰/메모리 점수로 가게 선택 + 코드 예산 상한 |
| 운영 | 사람이 직접 | Claude Code 메타 하네스: 규칙 파일, 판단 일지, 실행당 한 가지 변경, 커밋마다 근거 기록 |
| 배포 | 노트북에서 실행 | GCP VM + Docker(`restart: unless-stopped`), 비밀은 저장소 밖 |

## 3. Round 3 결과 (11-17 15:44 KST, `logs/monitor-1117-1543.txt`)

- 정산 수: 가게 30,572건, 바이어 18,773건(monitor의 `deals`). 이 커밋의 `logs/market-kit.log.1`과 `logs/market-kit.log`(11-16 07:49 ~ 11-17 16:38)에는 가게 `[acp] settled` 3,280줄과 바이어 `[acp] paid ... settled` 1,573줄이 있다. 예:
  - `06:11:01 [acp] settled 맛있는 햇 부사 사과 가정용(흠과) 중대과 10kg at 17756, ledger #7564913, unit u-cbd17032a63f grade B`
  - `06:10:11 [acp] paid 10046 through https://describe-boots-liberal-dec.trycloudflare.com/acp, settled at ledger #7564673: [동원xGS] 양반 수라 왕갈비탕 460g x4개`
- Players 순위: 9 / 71(total 1.585), Class 10 / 46(pct 0.800, total 1.519). 가게 all 0.773(12 / 68), 바이어 all 0.812(13 / 68).
- 가게: 잔고 3억 7,536만, 장부 4억 7,657만(16 / 92), 매출 61.1억(9 / 92), 별점 4.46(53 / 68), 파손 1,117건, 환불 768건, 파산 0.
- 바이어: saved 0.7852(13 / 138), 거래 18,773건(25 / 138), 글 2,966건(20 / 138), 포럼 0.885(50 / 60).
- 거부 사유(로그 인용, 두 로그 합계):
  - `06:12:17 [acp] checkout refused: unit u-070c8fd9be06 is not in stock for this order` (이 단위로 124회, 다른 단위까지 188회: 영수증에 적힌 단위가 이미 다른 주문으로 나간 뒤 들어온 checkout)
  - `[seller] [shelf] asked 41.4 h, freshest unit 17 h: declined` (손님이 요구한 유통기한을 못 맞춘 식품은 팔지 않음)
  - `07:24:15 [acp] payment not completed: 402 {"error":"payment refused by the market","market":{"error":"Internal Server Error"}}` (5회), `[acp] settle refused: {'error': 'Internal Server Error'}` (2회): 시장 쪽 정산 오류
  - `07:24:23 [acp] settle refused: ['not_settled_before']` (1회, 시장이 정산 조건을 거부)
  - `11:32:15 [acp] checkout refused: 500 Internal Server Error` (3회), `08:12:06 [acp] payment failed: ReadError: [Errno 104] Connection reset by peer` (2회): 터널이나 상대 가게 쪽 오류
  - `[market] ledger #…: … stars 5, verified False (failed: settled), review not shown` (정산되지 않은 거래의 리뷰는 시장이 보여 주지 않음)
- 마감 전략: 고가 사입은 11-16 00:00, 전체 사입은 11-17 12:00에 코드로 멈췄다. 12:02 이후 `[supply]` 줄은 0이다. 12:00:13 ~ 12:01:46의 10줄(251만)은 11:59:53 restock 결정을 키트가 이어서 실행한 것이다.

### 돌아보기

- 장부 점수가 원가 기준이라, 마감 전 원가 아래 덤핑은 하지 않았다. 대신 사입을 코드 시각으로 단계적으로 멈췄다.
- 약점은 별점(53 / 68)과 포럼 점수(학생 백분위 0.200)다. C등급 상품은 가격을 내려도 잘 안 팔렸다. 로그상 협상은 0.56 × 정가에서 합의됐는데 결제가 오지 않았으므로, 가격보다 보증과 별점의 문제로 본다.
