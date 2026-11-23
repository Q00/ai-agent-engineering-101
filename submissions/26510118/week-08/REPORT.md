# 중간 평가 REPORT: Kiro729 (26510118)

Round 3 결과는 Players 표 Class 보기 46명 중 6위다(student_total 1.589, 백분위 0.889). 시장에서 돌린 코드는
`round3/`에 있고, 구조는 `architecture.pdf` 한 장에 그렸다. 3부의 숫자와 로그 인용은 `make_report.py`가
`logs/`에서 뽑아 넣었다.

## 1. 부품별 설계

설계의 출발점은 하나였다. 흥정은 하루 수천 건이고 한 건이 20초 안에 끝나야 한다. 그래서 거래는 코드
규칙이 실시간으로 하고, 모델은 두 자리에만 둔다. 하나는 코드가 읽지 못하는 문장을 읽는 자리, 다른 하나는
3시간마다 기록을 읽고 규칙 변경을 제안하는 자리다. 모델이 낸 숫자는 코드가 범위를 검사한 뒤에만 쓴다.

### 1-1. 모델

모델 호출은 모두 `round3/harness.py` 한 곳을 지난다. OpenAI Responses API와 Decisions API를 쓰고, 호출마다
`records/api_usage.jsonl`에 남긴다. 키가 없거나 호출이 실패하거나 느리면 빈 답을 돌려주고, 부르는 쪽은
규칙으로 계속 간다. 모델은 얼마나 자주, 얼마나 중요한 결정을 하는지에 따라 나눠 배치했다.

| 모델 | 쓰는 곳 | 빈도 | 설정 |
|---|---|---|---|
| gpt-6-luna | 가게와 buyer가 코드로 못 읽는 상대의 문장 해석 (어느 숫자가 호가인지 등) | 흥정 중 필요할 때 | 추론 없음, 20초 안 |
| Decisions API (gpt-6-luna) | 확률 판단: 가게가 설명대로 물건을 줄 확률(리뷰와 포럼 글), buyer가 카운터를 받으면 떠날 확률 | 흥정 중 필요할 때 | 출력 토큰 없음 |
| gpt-6.1-terra | seller 분석과 buyer 분석 | 3시간마다 | 추론 low, 툴 호출 4회까지 |
| gpt-6.1-sol | 통합 분석: 두 분석의 제안을 채택하거나 거절하고, 지표가 나빠진 변경을 되돌림 | 3시간마다 | 추론 medium, 툴 호출 3회까지 |

11/15 기준으로 하루 약 5,400회를 불렀고 실패는 없었으며, 비용은 하루 약 0.4달러였다. 가격은 모델이 정하지
않는다. 분석 모델이 규칙 값을 제안해도 `analysts.py`의 코드가 알려진 필드인지, 범위 안인지, 한 번에 0.05
넘게 움직이지 않는지, 사람이 고정한 값이 아닌지를 검사한 뒤에만 `active_rules.json`에 들어간다.

### 1-2. 컨텍스트 관리 (메모리와 meta-harness)

사실은 코드만 기록한다. 모델이 남기는 것은 보고서, 인사이트, 메모뿐이다.

| 기억 | 내용 | 쓰는 쪽 |
|---|---|---|
| `records/*.jsonl` | 주문, 흥정, 합의, 판매, 거절, 미결제, 보충, 리뷰, 투표, 모델 호출 하나하나 | 코드 |
| `buyer/notebook.json` | 가게별 결과, 낸 값의 정가 대비 비율, 고장, 거짓 등급, 거절 횟수 | 코드 |
| 거래용 상태 파일 | `objective.json`(buyer 하한), `experiments.json`과 `haggle.json`(실험 arm), `line.json`(고가 라인 상한), `breakage.json` | 빠른 루프 |
| `active_rules.json`, `person_rules.json` | 지금 쓰는 규칙, 사람이 고정한 값(모델이 바꾸거나 되돌리지 못한다) | 통합 분석, 사람 |
| `insights.json`, reports, memos, changes | 발견과 근거, 보고서, 다른 분석에 남긴 메모, 채택과 되돌림의 지표 | 분석 모델 |
| `contexts.jsonl` | 모델이 읽은 브리핑 전부 | 코드 |

지난 거래와 평판은 세 가지 시간 단위로 다음 결정에 들어간다.

- 주문마다: buyer는 가게별 기억(`buyer_memory.py`)과 가게별로 학습한 첫 호가, 양보, 성사율(`shop_model.py`)로
  후보 가게에 점수를 매긴다. 여러 번 우리를 거절했거나 등급을 속인 가게는 표시를 달고 뒤로 미룬다.
  배송 확률은 Decisions API가 그 가게의 리뷰와 포럼 글을 읽고 낸다.
- 20분마다(빠른 루프, 코드만): `analysis.py`가 공개 장부를 모으고(`ledger.py`), board 공식을 다시 맞추고
  순위와 속도를 계산한다(`scoreboard.py`). `objective.py`는 남은 시간 동안 saved와 거래 수의 득실을 따져
  buyer가 지켜야 할 마진 몫의 하한을 정한다. `experiments.py`는 첫 호가와 흥정 방식을 주문 id의 해시로 나눈
  A/B arm으로 비교하고, 사람이 이긴 arm을 고정할 수 있다. `linewatch.py`는 잘 팔리는 고가 상품의 재고 상한을
  하나씩 올리고, 안 팔리는 상품은 뺀다.
- 3시간마다(느린 루프, meta-harness): `context.py`가 브리핑을 다섯 부분(지금 상태, 인사이트, 지난 보고서와
  그 제안의 결과, 다른 분석의 메모, 새 숫자)으로 만든다. 부분마다 크기 한도가 있고 넘치면 오래되거나 약한
  것부터 뺀다. 큰 데이터는 브리핑에 넣지 않고 툴 뒤에 둔다. seller 분석과 buyer 분석이 각자 보고서와 규칙
  제안을 내고, 통합 분석이 두 역할을 함께 보고 채택 여부를 정한다. 다음 실행은 지난 두 결정을 지표로 다시
  판정해 유지하거나 되돌린다.

사람의 자리도 따로 있다. 사람이 정한 값은 `person_rules.json`에 들어가 모델이 손대지 못한다. 라운드 마지막
12시간은 모든 규칙을 그 시점 값으로 고정했다(`deploy/freeze_rules.py`, 11/17 12:00). 다른 에이전트의
포럼 글과 리뷰는 모델에게 데이터로만 전달하고 지시로 쓰지 않는다.

### 1-3. 툴

모델에게 주는 툴은 MCP 서버 하나에 모았다(`round3/data_room.py`, 공식 MCP SDK, stdio). `mcp_host.py`가
분석을 돌릴 때마다 이 서버를 띄워 모델에게 툴을 빌려준다.

| 툴 / 자원 | 하는 일 |
|---|---|
| `ledger_query` | 카테고리, SKU, 가게 하나에 대해 최근 장부의 거래 수와 가격 |
| `replay` | 규칙 하나를 바꿨다면 우리의 지난 몇 시간이 어떻게 됐을지 기록으로 다시 계산 |
| `shop_credit` | 가게 하나의 장부 기록, 우리 notebook, 리뷰 |
| `leave_memo` | 다른 분석의 다음 브리핑에 메모를 남김 |
| `kiro://business`, `insights`, `reports`, `memos` | 읽기 전용 자원 |

`--as` 옵션이 호출한 분석의 이름을 정하기 때문에, 한 모델이 다른 분석의 이름으로 메모를 남길 수 없다.
툴 응답은 1,500자로 자른다. 툴로 만든 기준은 "브리핑에 매번 넣기엔 크지만 가끔 정확히 필요한 것"이다.
장부는 수십만 줄이라 브리핑에는 요약만 넣고, 모델이 확인하고 싶은 것을 툴로 묻게 했다.

거래 쪽 부품은 모델의 툴이 아니라 키트가 부르는 코드다(`round3/agent.json`의 훅). `seller.py`(A2A 가게),
`market_buyer.py`(주문마다 실행), `restock.py`(20분마다 보충), `review.py`(정산 영수증으로 별점)가 있다.
배포 전에는 서버 복사본에서 테스트 일곱 묶음(약 680개 검사)을 돌렸다.

## 2. 아키텍처 figure와 키트 기본형과 다른 점

`architecture.pdf`는 시장, 거래 계층, 메모리, 빠른 루프, 느린 루프(meta-harness), MCP 데이터룸 여섯 영역으로
나뉜다. 별표(★)는 키트 기본형에 없는 부분이다. 그림이 보여 주는 흐름은 셋이다.

1. 거래 → 기록: 가게와 buyer가 흥정하고 정산할 때마다 코드가 사실을 `records/`와 notebook에 남긴다.
2. 장부와 board → 빠른 루프 → 하한, 실험 arm, 재고 상한 → 거래: 20분마다 코드가 숫자를 다시 맞춘다.
3. 기록 → 브리핑 → 역할 분석 → 통합 분석 → 코드 검증 → 규칙 → 거래: 3시간마다 도는 meta-harness이고,
   다음 실행이 결과를 지표로 판정해 유지하거나 되돌린다.

같은 구조를 어디서 돌았는지(시장, Azure VM, OpenAI) 기준으로 다시 그린 그림이 `figures/system-overview.html`
(미리보기 `figures/system-overview.png`)이다.

키트 기본형은 `scripts/starter_round3`(가게)와 키트의 기본 보충이다.

| 부분 | 키트 기본형 | Kiro729 |
|---|---|---|
| 흥정의 결정 | 코딩 하네스(Claude Code나 Codex)가 매 수를 정한다 | 코드 규칙이 매 수를 정하고, 모델은 못 읽는 문장과 확률만 맡는다 |
| 가게 가격 | 원가 아래로는 팔지 않는다. 그 밖은 하네스 판단 | SKU 시세에 맞춘 첫 호가(A/B 실험), 원가와 예상 환불을 덮는 하한, 등급별 보증 가산(A 3%, B 11%, C 33%) |
| 출고 단위 | 먼저 만료되는 단위, 품질 언급 없음, 보증 없음 | 요청한 등급과 유통기한에 맞는 단위, 서명한 등급 표시와 인증서, 보증 선택 |
| 재고 보충 | 재고가 20개 아래면 무작위 SKU 몇 개 | 수요에 맞춘 진열과 고가 라인(잘 팔리면 상한 +1, 안 팔리면 제외), 라운드 끝까지 보충 |
| buyer | 리뷰로 가게를 고른다 | 가게별 기억과 학습한 성사율로 점수, 세 곳에 동시에 견적, 마진 몫 하한 아래로는 사지 않음 |
| 기억 | 없음 | 기록, notebook, 인사이트, 보고서, 메모, 브리핑 기록 |
| 분석과 학습 | 없음 | 빠른 루프(20분, 코드)와 meta-harness(3시간, 모델), A/B 실험 |
| 툴 | 없음 | MCP 데이터룸(툴 4개, 자원 4개) |
| 사람의 자리 | 없음 | 사람이 고정한 값은 모델이 바꾸지 못함, 종료 전 12시간 규칙 동결 |

구조에서 가장 크게 다른 점은 모델의 위치다. 기본형은 하네스가 매 흥정의 한가운데에 있다. 우리는 흥정을
코드에 맡기고 모델을 한 단계 위로 올려, 기록을 읽고 규칙을 고치는 자리에 두었다. 대신 모델이 고친 규칙이
실제로 나았는지는 다음 실행이 숫자로 다시 판정하게 해서, 모델의 판단이 틀려도 되돌아올 길을 만들었다.

## 3. Round 3 결과

아래는 `python make_report.py`가 `logs/`에서 만든 부분이다. 손으로 고치지 않는다.

<!-- make_report:part3 start -->
### 3-1. 최종 순위

종료 직전 board(`logs/round3-final/2358/standing.txt`, 2026-11-17 23:58:43 KST 기준)의 Players 표 Class 보기다. 46명 중 6위, student_total 1.589, 백분위 0.889다. 00:00:43과 00:05:17에 다시 읽은 board(`logs/round3-final/0000`, `0005`)도 표가 같다.

| 순위 | Player | student_total | student_pct |
|---|---|---|---|
| 1 | yms979 | 1.783 | 1.0 |
| 2 | SungJinho | 1.731 | 0.978 |
| 3 | inextro | 1.676 | 0.956 |
| 4 | dragonash22 | 1.622 | 0.933 |
| 5 | Nocha12 | 1.593 | 0.911 |
| 6 | **Kiro729** | 1.589 | 0.889 |
| 7 | seochanit | 1.542 | 0.867 |
| 8 | Choo-chu | 1.537 | 0.844 |

standing.txt의 우리 줄:

`logs/round3-final/2358/standing.txt:7`

      6. Kiro729                  student_total 1.589  student_pct 0.889  total 1.631  <- us

### 3-2. 정산 수

라운드 전체 숫자는 종료 직전 board의 우리 행에서 읽었다(`logs/round3-final/2358/board.json`).

| 쪽 | 항목 | 값 |
|---|---|---|
| 가게 | 정산된 판매 (deals) | 47,625 |
| 가게 | 매출 (revenue) | 7,686,528,524 |
| 가게 | Book (현금 + 재고 원가) | 723,802,392 |
| 가게 | 별점 (stars / stars_rank), 리뷰 수 | 4.48 / 4.39, 46,939 |
| 가게 | 정직한 등급 표시 / 전체 표시 | 47,097 / 47,136 |
| 가게 | 환불, 고장 | 862, 1,100 |
| buyer | 받은 주문, 정산한 구매 (deals) | 51,427, 22,565 |
| buyer | saved, forum, posts | 0.7742, 0.898, 3,304 |
| 시장 전체 | 정산, 거절(rejected), 이탈(walkaways) | 1,008,351, 292,977, 899,239 |

키트 로그로 셀 수 있는 기간은 11/16 05:21 ~ 11/17 23:59 KST(42.6시간)다. 키트는 로그가 20 MB를 넘으면 `market-kit.log.1`로 넘기고 그 전의 `.1`을 지우기 때문에 그 앞은 남아 있지 않다. 아래 표는 이 기간의 줄 수다(종료 뒤 줄은 뺐다).

| 쪽 | 결과 | 줄 수 | 비율 |
|---|---|---|---|
| 가게 흥정 | 합의 (`deal at`) | 8,071 | 19.4% |
| 가게 흥정 | 우리 하한 아래 제안이라 거절 (`rejected`) | 9,766 | 23.5% |
| 가게 흥정 | buyer가 떠남 (`walked`) | 23,767 | 57.1% |
| 가게 정산 | 시장 /api/settle 통과 (`[acp] settled`) | 6,517 | 합의의 80.7% |
| buyer | 결제해 정산 (`[acp] paid`) | 3,226 | |
| buyer | 가게가 거절한 흥정 (`TASK_STATE_REJECTED`) | 4,336 | |

정산 줄 예 (가게, 기간의 첫 줄): `logs/market-kit.log.1:39` (11/16 05:22 KST)

    20:22:20 [acp] settled 여성용향수 쁘띠크로와 데일리패션향수_보타닉로즈(장미향) at 12137, ledger #7087479, unit u-7e4844f0eba7 grade A

정산 줄 예 (buyer): `logs/market-kit.log.1:41` (11/16 05:22 KST)

    20:22:22 [acp] paid 783726 through https://interesting-dollars-architecture-cams.trycloudflare.com/acp, settled at ledger #7087489: [다우닝] 플라나 베이직 3인 전체 천연가죽 소파, grade B

### 3-3. 거부 사유

가게가 우리 buyer를 거절할 때 보낸 문장을 사유별로 묶었다. 분류는 `make_report.py`의 `REFUSAL` 규칙이다.

| 사유 | 횟수 | 비율 |
|---|---|---|
| stock: none in stock or no unit fits | 2,208 | 50.9% |
| price: under the shop's floor | 1,962 | 45.2% |
| food: too little shelf life | 99 | 2.3% |
| stock: held in another talk | 32 | 0.7% |
| other | 17 | 0.4% |
| verification | 12 | 0.3% |
| conditions: warranty or grade | 6 | 0.1% |

- stock: none in stock or no unit fits: `logs/market-kit.log.1:129` (11/16 05:22 KST)

    20:22:57 [buyer] [buyer] <- TASK_STATE_REJECTED | Sorry, we have none of that in stock.

- price: under the shop's floor: `logs/market-kit.log.1:109` (11/16 05:22 KST)

    20:22:54 [buyer] [buyer] <- TASK_STATE_REJECTED | No deal below 606735.

- food: too little shelf life: `logs/market-kit.log.1:4055` (11/16 05:56 KST)

    20:56:50 [buyer] [buyer] <- TASK_STATE_REJECTED | Sorry, no unit in stock for this request with 38 h left. Ask by SKU.

- stock: held in another talk: `logs/market-kit.log.1:9284` (11/16 06:42 KST)

    21:42:28 [buyer] [buyer] <- TASK_STATE_REJECTED | Every unit of that is held in another open negotiation right now. Please ask again in a minute.

- other: `logs/market-kit.log.1:22122` (11/16 08:37 KST)

    23:37:33 [buyer] [buyer] <- TASK_STATE_REJECTED | This offer is too far from what we can accept for this set, and we haven't seen any movement in your last two offers, so

- verification: `logs/market-kit.log.1:86456` (11/16 18:15 KST)

    09:15:08 [buyer] [buyer] <- TASK_STATE_REJECTED | This purchase cannot be verified against current inventory and quote limits. No receipt issued.

- conditions: warranty or grade: `logs/market-kit.log.1:54660` (11/16 13:42 KST)

    04:42:00 [buyer] [buyer] <- TASK_STATE_REJECTED | Unsupported or conflicting explicit grade/warranty conditions. Please clarify your conditions in a new inquiry. No recei

우리 가게 쪽에서 시장이나 키트가 거절한 건은 다음과 같다.

| 단계 | 사유 | 횟수 |
|---|---|---|
| checkout | unit not in stock for the order | 55 |
| checkout | 500 Internal Server Error | 2 |
| settle | {'error': 'Internal Server Error'} | 6 |
| settle | ['not_settled_before'] | 3 |
| settle | ['unit_in_stock'] | 1 |

checkout 거절 예: `logs/market-kit.log.1:11226` (11/16 07:00 KST)

    22:00:21 [acp] checkout refused: unit u-080b87b89fe7 is not in stock for this order

settle 거절 예: `logs/market-kit.log.1:10864` (11/16 06:57 KST)

    21:57:15 [acp] settle refused: ['not_settled_before']

종료 뒤에는 시장이 주문을 503으로 돌려보냈다: `logs/market-kit.log:120738` (11/17 23:59 KST)

    14:59:21 [acp] settle refused: {'error': 'Round 3 ended at 2026-11-17 23:59 KST. The market is paused until Round 4 opens on 2026-11-24 at 19:00 KST.'}

### 3-4. 10주차 기준 숫자와 비교

우리 에이전트가 주문 없이 먼저 하는 행동은 buyer의 시세 글(포럼)이다(`analysis.py`의 journal). 기준 숫자는 `analysis.py`가 20분마다 남긴 `logs/round3-final/board_history.jsonl`에서 11/10 11:59 KST 줄을, 지금 숫자는 종료 직전 11/17 23:41 KST 줄을 읽었다.

| 숫자 | 11/10 | 11/17 |
|---|---|---|
| buyer posts (쓴 글) | 1,231 | 3,301 |
| buyer forum (글 평점) | 0.885 | 0.898 |
| buyer deals | 10,102 | 22,549 |
| 가게 deals | 24,242 | 47,570 |
| 가게 revenue | 5,132,125,590 | 7,678,834,999 |
| 가게 stars | 4.48 | 4.48 |
| Class 보기 순위 (총점) | 2위 (1.647) | 6위 (1.592) |

<!-- make_report:part3 end -->
