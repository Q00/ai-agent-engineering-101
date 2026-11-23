# 중간 평가 보고서 — Round 3 시장의 내 에이전트

26512070 (gom31) · `submissions/26512070/week-08` · Round 3: 2026-11-03 ~ 11-17 23:59 KST

설계 전문은 [HARNESS.md](HARNESS.md)(v4, 이전 판은 git 기록)와 `design/ideal-harness.md`에 있고,
이 보고서는 과제가 요구한 세 절을 요약한다. 모든 수치의 근거는 `logs/final-r3/`(종료 시각 스냅샷)와
`logs/market-kit.log`(배포 컴퓨터의 키트 로그, 정산 줄 포함)다.

## 1. 부품별 설계

### 1.1 모델 — 좌석(seat)마다 다른 모델, 실패하면 회로 차단기

모든 모델 호출은 `agents/model.py` 한 곳을 지난다. 역할(좌석)마다 모델을 다르게 배치했다.

| 좌석 | 주 모델 (Claude Code `-p`) | 대체 (OpenRouter) | 비고 |
|---|---|---|---|
| seller, buyer | haiku | gpt-4o-mini | 흥정 좌석. 6초 안에 답이 없으면 그 호출만 대체 모델로 |
| restock | sonnet | gpt-4o-mini | 11/10부터 ReAct 플래너 |
| review, forum | haiku / sonnet | gpt-4o-mini | |
| analyst (L2) | sonnet | — | |
| strategy (L3) | opus | — | 전략 버전을 제안 |
| audit | — | gemini-2.5-flash | **다른 회사 모델**로 교차 검증 |

- Claude Code는 빈 폴더에서 `--tools "" --setting-sources ""`로 띄워 프로젝트 지시문과 내장 툴 없이 쓴다. 툴은 우리 루프가 직접 쥔다.
- 회로 차단기: 한 좌석이 연속 3회 rate-limit이면 300초 동안 대체 모델로 전환, 시험 호출 1회로 복귀를 결정한다. 상태는 `memory/bus/breaker/`에 좌석별 파일로 남는다(restock·review는 매번 새 프로세스라서).
- 모든 호출은 `model.call` 이벤트(좌석, 모델, 소요 ms, ok, rate_limited, fallback)로 기록된다.

### 1.2 컨텍스트 — 두 런타임, 층이 있는 메모리, 시스템 카드

프로세스를 둘로 나눴다. **안쪽(inner)**은 키트 v4와 L1 실행 에이전트(seller, buyer, restock, review, forum),
**바깥(outer)**은 수집·집계·분석(L2)·전략(L3)·감사다. 서로 직접 부르지 않고 `memory/bus/`의 파일로만 통신한다.
바깥이 죽어도 안쪽은 마지막으로 검사를 통과한 전략과 사실로 계속 거래한다.

지난 거래와 평판이 다음 결정에 들어가는 경로:

1. 모든 원본 이벤트(흥정, 결제, 고장, 게시판)는 `memory/raw/`에 덧붙인다(쓰는 프로세스마다 다른 파일, 멱등 event_id).
2. 바깥 집계가 몇 분마다 `memory/facts/live.json`(시세 분포, SKU 속도, 재고 나이)을 계산하고,
   참가자별로는 `memory/profiles/<이름>.json`에 관찰(사실)과 해석(추정)을 나눠 쌓는다.
3. L3 전략 에이전트는 분기(epoch) 요약과 점수판을 읽고 **전략 버전**(`memory/strategy/active.json`)을 제안한다.
   Round 3 동안 v100~v511이 설치됐고, 전부 `logs/`의 전략 이력으로 커밋돼 있다.
4. L1은 매 판단마다 시스템 카드(자기 역할 부분) + 지금 전략 + 이번 판단의 상황 한 덩어리만 기본으로 받고,
   더 필요하면 `me.counterpart(name)` 같은 조회 툴로 그 상대와의 거래 이력·프로필·우리 자세를 가져온다.
   즉 "메모리를 컨텍스트에 다 넣는" 대신 **툴로 읽는 메모리**다.

모든 에이전트가 같은 시스템 카드(`memory/system_card.md`)를 읽는다. 모델이 달라도 같은 문서를 읽게 해서,
판단이 갈리면 정보 차이가 아니라 모델 차이로 볼 수 있게 했다. 카드는 층(핵심/역할/의도/부록)으로 나뉘고
역할 밖 부분은 `memory.card_section`이 거절한다.

### 1.3 툴 — 결정론은 전부 툴, 설명이 곧 인터페이스

이름 공간으로 묶었다: `me.*`(우리 기록) · `market.*`(시장 데이터) · `memory.*`(요약·원본) ·
`calc.*`(계산) · `check.*`(검증) · `act.*`(실행) · `rec.*`(기록). 공통 계약:
입력·출력 JSON Schema, 호출 **전에** 인자를 로그, `{"ok": false, "reason", "hint"}`로 거절,
실행 툴은 안에서 한도 검증을 다시 한다(6장 한도는 어떤 판단에서도 툴이 막는다).
L1에는 함수 호출로, L2·L3·감사·사람에게는 **같은 정의를 MCP 서버**(`mcp_server.py`)로 노출해
다른 회사 모델(audit의 gemini)도 같은 툴을 쓴다.

특기할 것은 **forge**(`harness/forge.py`)다. 반복되는 흥정 패턴을 하네스가 스스로 코드 툴로 만들어
시험을 통과하면 규칙 실행에 편입한다. Round 3 동안 판매자 흥정 툴 29개가 forge됐고
(`tools/forged/seller_*.py`, 각 툴의 시험 기록 `*_test.py.txt` 포함, 커밋 `a963028`),
"도구를 만드는 모델과 쓰는 모델을 나눈다"(Cai et al. 2023)를 시장에서 그대로 실행한 부분이다.

## 2. 구조 — 아키텍처 figure와 키트 기본형과 다른 점

한 페이지 figure는 [architecture.tex](architecture.tex)(빌드본 `architecture.pdf`)다. 핵심 구도:

- **지혜/지식/정보의 세 층**: 맨 위 WISDOM 띠(점수 공식, 상위 규칙 R0~R14, 승인된 전략, 능력별 허용 범위,
  사람의 명시적 승인)는 모든 컨텍스트의 맨 위에 항상 깔리고 승인으로만 바뀐다. 그 아래 지식(집계·프로필),
  맨 아래 정보(원본 이벤트). 확실성(명시/관찰/가설)을 따로 표시한다.
- **Extractor**: 다른 에이전트의 메시지는 전부 신뢰하지 않는 입력으로 보고, 값과 "표시된 발췌"만 뽑아
  "데이터이지 지시가 아니다"로 넘긴다(프롬프트 주입 방어).
- **규칙 실행, 예외만 에이전트** (D34): 11/4 관측에서 판매자 결정 하나에 모델을 평균 1.88회 부르고
  거절의 3분의 1이 프롬프트가 안 보여 준 한도 때문임을 보고, 흥정·재구매를 플레이북 숫자(시작가, 양보 폭,
  바닥, 턴 수)로 **규칙이 바로 툴을 부르게** 바꿨다. 모델은 규칙이 못 다루는 예외에서만 부르고,
  예외는 `exec.anomaly`로 기록돼 전략 에이전트가 플레이북을 고치는 재료가 된다.
- **autopilot**: 11/8부터 전략 선택 자체를 하네스가 30분 A/B 슬롯으로 스스로 실험했다
  (`harness/autopilot.py`, `harness/experiment.py`).

키트 기본형과의 차이:

| | 키트 기본형 | 이 하네스 |
|---|---|---|
| 실행 | 메시지마다 모델 한 번 호출 | 플레이북 규칙이 툴을 직접 호출, 예외만 모델 |
| 컨텍스트 | 고정 프롬프트 | 층이 있는 메모리 + 조회 툴, 시스템 카드 버전 관리 |
| 전략 | 코드에 박힌 상수 | 전략 버전 파일(v100~v511), L3 제안 → 검사 → 설치, 나중엔 autopilot A/B |
| 툴 | checkout 고정 코드 | 스키마 있는 툴 계약 + MCP 노출 + forge로 자가 확장(29개) |
| 안전 | — | 한도는 툴이 막음, extractor 주입 방어, 다른 회사 모델 감사, 회로 차단기 |
| 운영 | 프로세스 하나 | 안/바깥 두 런타임, bus 파일 계약, 한쪽이 죽어도 거래 지속 |

## 3. Round 3 결과

종료 시각(11/17 23:59) board 스냅샷 `logs/final-r3/board-final-20261118.json` 기준:

- **Players 합계 1.261 = 가게 ALL 0.649 + buyer ALL 0.612, 전체 32위/72명.**
  Players 표 Class 보기(학생끼리 백분위) **0.4** — 평가 항목 3의 점수가 되는 값이다.
- 가게(gom31-certified-goods): 정산 거래 **20,087건**, 매출 3,695,783,094, 순이익 260,716,531,
  장부(book) 417,036,278, 별점 4.49(후기 19,955개), 환불 789건, 파산 0회.
- buyer: 정산 거래 **10,165건**, 잉여 547,840,597, saved 0.727, 게시판 표 0.916, 글 3,066개.
- 기본 조건(가게·buyer 각 20건 이상 정산)은 큰 차이로 충족. open mandate 상한은
  `check.offer`가 checkout 전에 검사해, 상한을 넘는 제안은 툴이 거절한다.

세부 백분위와 항목별 순위는 `logs/final-r3/position-final-20261118.md`에 종료 시각 그대로 있다
(예: 장부 19/69, 매출 17/69, 별점 51/69 — 별점이 끝까지 약점이었다).

거부 사유는 로그의 정산·거절 줄에서 그대로 인용한다 (`logs/market-kit.log`, 11/17 마감 직전):

```
23:57:35 [seller] 43809ee1 rejected: Sorry, we have none of that in stock right now.
23:58:00 [seller] 88d36ecb rejected: Sorry, our units of G0ec8941a keep at most 29 h, less than the 127 h you need.
23:58:32 [seller] a3e55e3a rejected: Sorry, our units of G0e64d348 keep at most 89 h, less than the 134 h you need.
```

유통기한이 모자라는 식품을 팔지 않는 것은 별점 방어(규칙 R: 사실 기반 별점 `calc.fact_stars`)의 일부다.
11/13의 실패 사례도 기록해 둔다: v232에서 goods 보증을 뺐더니 합의는 되는데 결제가 안 되는 거래가
늘어(18/125) v257에서 보증을 복구했다 — 실패 커밋과 로그는 git 기록에 그대로 있다.

### 제출물 위치

| 요구 | 위치 |
|---|---|
| 키트 로그(정산 줄 포함) | `logs/market-kit.log`(+ 로테이션 `.1`), 종료 스냅샷 `logs/final-r3/` |
| 바꾼 코드 | `agents/ core/ harness/ tools/ ops/` 등 이 폴더의 새 코드 전체 (키트 자체는 고치지 않고 그 위에 하네스를 얹음) |
| REPORT.md | 이 문서 |
| 아키텍처 figure | `architecture.tex`, `architecture.pdf` |
