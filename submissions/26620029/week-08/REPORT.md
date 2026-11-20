# Week 08 (Midterm) REPORT: 26620029 (ddolcom)

가게 `ddolcom`과 바이어 `ddolcom`으로 Round 3(11-03 ~ 11-17 23:59)에 참여했다. 키트 v4를 GCP 서버의 Docker 컨테이너에서 돌렸고, 거래 루프는 코드가 맡았다. 모델은 두 곳에만 썼다. 하나는 바이어의 협상 수 결정과 포럼 글 문구에 쓰는 `claude -p --model haiku`이고, 다른 하나는 한 단계 위에서 1시간(초기에는 3시간)마다 로그를 읽고 정책을 고치는 Claude Code(Opus) 메타 하네스다.

> 3절의 수치는 Round 3 종료(11-17 23:59 KST) 뒤의 최종 board(`logs/final-board-1118-1115.txt`)와 마감까지의 로그 기준이다.

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

## 3. Round 3 결과 (최종, 11-17 23:59 KST 종료)

### 3.1 최종 수치

종료 뒤 공개 `/api/board`를 읽어 `logs/final-board-1118-1115.txt`에 저장했다. 컨테이너가 00:05에 watchdog으로 멈춰, `monitor.py` 대신 같은 계산을 하는 스크립트로 읽었다.

| | 값 | 순위 |
|---|---|---|
| **Class view (학생)** | total 1.496 (shop 0.691, buyer 0.805), pct 0.800 | **10 / 46** |
| Players | total 1.576 | 11 / 72 |
| 가게 ALL | 0.763 | 14 / 69 |
| 가게 정산 | 30,846건 | |
| 가게 이익 / 장부 | 4억 702만 / 4억 7,484만 | 장부 17 / 93 |
| 가게 매출 | 61.5억 | 9 / 93 |
| 별점 / 파손 / 환불 / 파산 | 4.46 / 1,146 / 780 / 0 | 별점 54 / 69 |
| 바이어 ALL | 0.813 | 13 / 69 |
| 바이어 거래 | 19,243건 | 26 / 139 |
| saved / 글 / 포럼 | 0.7856 / 3,056건 / 0.885 | 13 / 139, 20 / 139, 50 / 60 |

로그(이 커밋의 `logs/market-kit.log.1` 11-16 07:49 ~ 11-17 06:10, `logs/market-kit.log` 11-17 06:10 ~ 11-18 00:05)에는 가게 `[acp] settled` 3,516줄과 바이어 `[acp] paid ... settled` 1,998줄이 있다. 그 이전 정산은 회전되어 지난 커밋의 같은 파일에 남아 있다. 예:

- `06:11:01 [acp] settled 맛있는 햇 부사 사과 가정용(흠과) 중대과 10kg at 17756, ledger #7564913, unit u-cbd17032a63f grade B`
- `06:10:11 [acp] paid 10046 through https://describe-boots-liberal-dec.trycloudflare.com/acp, settled at ledger #7564673: [동원xGS] 양반 수라 왕갈비탕 460g x4개`
- 마지막 정산: `23:58:23 [acp] settled 삼성전자 LED 4K UHD 125.7cm(50인치) 비즈니스TV ...`
- 종료 뒤: `00:00:16 [market] result refused: 503 {"error":"Round 3 ended at 2026-11-17 23:59 KST. The market is paused until Round ...`

### 3.2 거부 사유 (두 로그 합계, 줄 수)

| 사유 | 횟수 | 로그 인용 | 뜻 |
|---|---|---|---|
| 재고 아닌 단위로 checkout | 198 | `06:12:17 [acp] checkout refused: unit u-070c8fd9be06 is not in stock for this order` | 영수증에 적힌 단위가 이미 다른 주문으로 나간 뒤 들어온 checkout. u-070c8fd9be06 하나로 124회, u-9b03adb42b1a 48회 |
| 유통기한 미달 거절 | 112 | `[seller] [shelf] asked 41.4 h, freshest unit 17 h: declined` | 손님이 요구한 유통기한을 못 맞춘 식품은 팔지 않음 |
| 시장 결제 오류 | 7 | `07:24:15 [acp] payment not completed: 402 {"error":"payment refused by the market","market":{"error":"Internal Server Error"}}` | 시장 쪽 정산 오류 |
| checkout 500 | 3 | `11:32:15 [acp] checkout refused: 500 Internal Server Error` | 상대 가게 쪽 오류 |
| settle 500 | 2 | `[acp] settle refused: {'error': 'Internal Server Error'}` | 시장 쪽 정산 오류 |
| 연결 끊김 | 2 | `08:12:06 [acp] payment failed: ReadError: [Errno 104] Connection reset by peer` | 터널이나 상대 가게 쪽 오류 |
| 정산 조건 | 1 | `07:24:23 [acp] settle refused: ['not_settled_before']` | 시장이 정산 조건을 거부 |

정산되지 않은 거래의 리뷰는 시장이 보여 주지 않는다(`[market] ledger #…: … stars 5, verified False (failed: settled), review not shown`).

### 3.3 week-10 LAB 기준선과 비교

week-10 LAB의 "주문 없이 먼저 하는 행동"으로 11-12 10:42에 `promo.py`를 넣었다. 가게 이름으로 내 재고 상품을 포럼에 광고하고, SKU 해시로 절반은 광고하지 않는 대조군으로 남겨 `promo_eval.py`로 비교한다(`MONITOR_JOURNAL.md` 11-12 10:42). 기준 숫자는 직전 점검 `logs/monitor-1112-0944.txt`와 시작 전 3시간의 promo_eval 값이다.

| 숫자 | 기준선 (11-12 09:44) | 최종 (11-17 23:59) |
|---|---|---|
| Class 순위 / pct | 14 / 46, 0.711 | **10 / 46, 0.800** |
| Class total (shop, buyer) | 1.428 (0.656, 0.772) | 1.496 (0.691, 0.805) |
| Players 순위 | 15 / 69 | 11 / 72 |
| 가게 정산 | 15,904건 | 30,846건 |
| 가게 이익 / 장부 | 2억 4,147만 / 3억 1,530만 | 4억 702만 / 4억 7,484만 |
| 장부 / 매출 / 별점 순위 | 19 / 90, 12 / 90, 31 / 65 | 17 / 93, 9 / 93, 54 / 69 |
| 바이어 거래 / saved | 13,127건 / 0.3297 (17 / 131) | 19,243건 / 0.7856 (13 / 139) |
| 포럼 | 0.878 (45 / 55) | 0.885 (50 / 60) |
| promo: 잘 팔리는 SKU 시간당 거래, 광고군 / 대조군 | 시작 전 3시간 27.3 / 9.7 | 6시간 뒤 41.0 (×1.50) / 24.1 (×2.48) |

- saved는 같은 날 17:44에 확인한 시장 Patch 3.6이 계산식을 `(정가 - 가격) / (정가 - 가게 원가)`로 바꾸고 Round 3 전체에 소급했다(0.3309 -> 0.7599). 두 값은 같은 잣대가 아니다.
- promo는 광고군보다 대조군이 더 늘어 효과가 보이지 않았다. 시장 전체 거래가 같이 늘었다. 그래서 6시간 판정에서 간격을 15분에서 60분으로 줄였다(`d5d56a3`). 순위가 오른 것은 promo보다 같은 날의 다른 변경 때문으로 본다. 다른 변경은 바이어 상한 조정과 Patch 3.6 뒤 등급별 상한, 고가 사입 기준 완화다. 다만 한 번에 여럿이 겹쳐 개별 효과는 단정하지 않는다.
- 별점 순위는 31위에서 54위로 내려갔다. 고가 가구를 늘린 뒤 파손이 599건에서 1,146건으로 늘어난 것과 같은 시기다.

### 3.4 마감 전략

고가 사입은 11-16 00:00, 전체 사입은 11-17 12:00에 코드로 멈췄다. 12:02 이후 `[supply]` 줄은 0이다. 12:00:13 ~ 12:01:46의 10줄(251만)은 11:59:53 restock 결정을 키트가 이어서 실행한 것이다. 사입을 멈춘 뒤 시간당 정산(로그의 `[acp] settled`)은 06~11시대 82~109건에서 13~23시대 28~55건으로 줄었다. 마감 때 재고 약 480개(원가 약 7,000만)가 남았고, 장부는 재고를 원가로 세므로 원가 아래 덤핑은 하지 않았다.

### 돌아보기

- 장부 점수가 원가 기준이라, 마감 전 원가 아래 덤핑은 하지 않았다. 대신 사입을 코드 시각으로 단계적으로 멈췄다.
- 약점은 별점(54 / 69)과 포럼 점수(학생 백분위 0.200)다. C등급 상품은 가격을 내려도 잘 안 팔렸다. 로그상 협상은 0.56 × 정가에서 합의됐는데 결제가 오지 않았으므로, 가격보다 보증과 별점의 문제로 본다.
