# Week 08 중간 평가 보고서 — Ssadassa Shop · myeonggi (Round 3)

학번 26510358, GitHub `SUNGMYEONGGI`. 가게와 구매자 모두 시장 이름은 `myeonggi`임.
보드 수치는 2026-11-17 13:00 KST 스냅샷(`logs/monitor/20261117/board-20261117T1300.json`), 호출·툴 횟수는 같은 날 10:50 KST에 `logs/r3/*.jsonl`에서 셈. 11-10 이전 진행 보고서는 git 이력에 있음.

| 제출물 | 위치 |
|---|---|
| 아키텍처 그림 (한 페이지, LaTeX/TikZ) | `architecture.tex`, `architecture.pdf` (`xelatex architecture.tex`) |
| 시퀀스 다이어그램 (한 페이지, LaTeX/TikZ) | `sequence.tex`, `sequence.pdf` (`xelatex sequence.tex`) |
| 키트 로그 | `logs/market-kit.log` |
| 바꾼 코드 | `shop_r3.py`, `buyer_r3.py`, `restock_r3.py`, `review_r3.py`, `forum_r3.py`, `restock_review_r3.py`, `r3lib/`, `kit_patches.py`, `market_runtime.py`, `ops/` |
| 판단·배포 기록 | `logs/r3/` (trace jsonl, 배포 노트 26개), `logs/deploy/deploy.jsonl` |

## 1. 결과

| | 값 |
|---|---|
| Players 순위 | 71명 중 15위, 총점 1.497 (가게 ALL 0.776 + 구매자 ALL 0.721), 학생 백분위 0.711 |
| 가게 | 거래 29,148 · 매출 42.5억 · book 5.62억 · 별점 4.59 (stars_rank 4.39) · 파산 0 |
| 구매자 | 주문 42,306 · 거래 24,967 · saved 0.751 · 포럼 0.891 · 글 2,977 |
| 기본 조건 (각 정산 20건 이상) | 충족 (가게 29,148, 구매자 24,967) |
| 등급 정직 표시 | 판매 주장 29,113건 중 29,111건이 시장 검증과 일치 |

11-10 기준선은 68명 중 18위, 총점 1.442였음 (`logs/r3/deploy-baseline-20261110.txt`, `deploy-20261110T1058Z.txt`).

## 2. 구조 (architecture.pdf)

한 문장 요약: **돈은 코드가, 확률은 판단 모델이, 말은 LLM이 정한다. 결정은 걸리는 시간에 따라 네 층으로 나누고,
모든 판단과 실험은 보드 점수 한 가지 단위로 잰다.**

그림은 가운데 네 층을 위(빠름)에서 아래(느림)로 놓음. 왼쪽은 시장, 오른쪽은 모든 층이 함께 쓰는 기억 · 외부 모델 · 점수 모델임.

| 층 | 시간 | 무엇을 정하나 | LLM |
|---|---|---|---|
| ① 돈 결정 | 밀리초 | 호가 · 하한 · 제안가 · 구매 상한 · 보증 | 0회. 규칙 + bandit이 숫자 하나만 고름 |
| ② 내부 조직 + 확률 | 60초–10분 | 매입, 할인, 진열 순서, 포럼 투표, 먼저 행동할지 | 판단 모델은 확률만, 문턱값은 코드 |
| ③ ReAct | 30분 | 카테고리 수요 배율, 구매자 글 | 읽기 전용 툴, 결과는 코드가 범위 제한 |
| ④ 바깥 루프 | 시간–일 | 코드 자체 | 사람 + Claude Code가 trace를 읽고 제안, canary로 판정 |

### 독창적이라고 보는 점

1. **결정 속도로 나눈 층.** 가격 같은 돈 결정은 밀리초 경로에 있어 LLM이 실패하거나 늦어도 거래가 멈추지 않음.
   느린 층일수록 더 넓게 보지만 출력은 빠른 층의 숫자 하나를 범위 안에서 바꾸는 것뿐임.
   원가 하한(L0)은 어느 층도 건드리지 못함.
2. **보드 점수라는 한 가지 단위.** `r3lib/score_model.delta(board, action)`가 행동을 (Δ가게 ALL, Δ구매자 ALL)로 바꿈.
   백분위 계단을 이웃 사이 직선으로 이어 작은 변화도 부호 있는 값을 가짐.
   bandit 보상, Horvitz의 B와 C, 구매 상한 θ, canary 판정이 모두 이 함수를 씀.
   그래서 "이게 좋은가"를 묻는 곳마다 같은 답을 받음.
3. **서명된 내부 A2A 조직.** 운영 두뇌를 CEO · 구매 · 물류 · 품질 · 마케팅 부서로 나눔. 각 부서는 127.0.0.1의 A2A 에이전트임
   (`r3lib/org`). 시장과 같은 신뢰 모양을 씀: Ed25519 서명 봉투(nonce, ±60초, 수신자 확인) → 서명 영수증(req/res sha256).
   하나라도 틀리면 거절함. 서버가 죽으면 같은 스킬을 프로세스 안에서 실행함. 라운드 중 내부 호출 76,564회.
4. **커밋 = 배포 = 실험인 바깥 루프 (meta-harness).** Meta-harness의 "후보 코드 + 점수 + 원본 trace를 파일로 쌓고
   다음 후보를 고른다"를 사람과 Claude Code가 돎. 새 동작은 `sha256(salt:주문 id)`로 주문 절반에만 켬.
   미리 적은 규칙으로 arm별로 판정해 1.0으로 넓히거나 0.0으로 되돌림. 배포는 CI와 헬스체크를 거치고, 실패하면 자동 revert함
   (`ops/deploy_pull.py`, 이력은 지우지 않음).
5. **라이브 전 디지털 트윈.** `r3lib/twin.py`는 실제로 온 구매자 방문을 후보 정책에 다시 돌림.
   `shadow_gate.py`는 같은 창 · 같은 seed로 짝 비교해 PASS/FAIL을 냄. 가게 ALL 추정식은 보드를 53개 가게에서 평균 오차 0.0024로 재현함.

## 3. 부품 구현

### 3.1 모델

| 모델 | 쓰는 곳 | 호출 | 안전장치 |
|---|---|---|---|
| OpenAI Decisions API `gpt-6-luna` (`r3lib/decide.py`) | 포럼 글 조작(주입) 판정, 우리 초안 자가 점검 | 44,073 (판정 41,117 · 자가 점검 2,947) | 보정 확률만 받음. 문턱값은 코드가 적용. LLM 라벨만으로 비추천하지 않음 |
| Codex CLI `gpt-6.1-sol` (`r3lib/llm.py`) | 정규식이 못 읽은 가게 응답의 가격, 포럼 분류·작성, ReAct | 9,242 (가격 3,413 · 분류 3,058 · ReAct 2,558 · 글 213) | 읽기 전용 sandbox, JSON 출력, 원격 텍스트는 인용 데이터로만 넣음. 실패하면 결정적 fallback |
| L1 contextual bandit (`r3lib/bandit.py`) | 첫 호가 배율, C등급 호가, 프리미엄 호가, 재고 정리 | 선택 80,703 | Thompson, 반감기 8시간, 탐색 15%, arm은 상수 ±20% 안. propensity를 남겨 IPS로 오프라인 평가 |

돈이 걸린 결정(가격, 하한, 매입 수량, 구매 상한)은 모두 `r3lib/pricing.py`와 `restock_r3.plan`의 규칙이 함.
TypeSafe Jev 백엔드도 `decide.py`에 있지만 키가 없어 쓰지 않았음.

### 3.2 컨텍스트 (기억, RAG, meta-harness)

**기억 `memory.sqlite`** (`r3lib/memory.py`): 가게 · 구매자 · 훅 · 포럼 · 조직 서버가 같은 파일을 씀. 근거의 강도에 따라 세 층으로 나눔.

| 층 | 표 | 출처 |
|---|---|---|
| 1 시장이 서명한 사실 | `ledger settles breaks shops shop_stock` | `/api/ledger`, `/api/shops` (60초마다 증분) |
| 2 우리 기록 | `purchases negotiations restocks members stockouts notes` | 우리 에이전트 |
| 3 약한 증거 | `forum` (조작 확률을 붙여 저장) | 포럼 |

뷰는 다음과 같음.

- 카테고리 수요
- 정가 대비 시세: `r3lib/oracle.py`, SKU · 카테고리 분포
- 고장률, 가게 평판
- 경쟁 공급 압력: `r3lib/scout.py`, 경쟁 공급 ÷ 시장 정산
- 구매자별 등급 취향: `r3lib/taste.py`

잘 팔리는 상품은 공개되지 않으므로 원장 정산 기록에서 이 뷰를 만들어 매입과 가격에 씀.

**작은 RAG** (`r3lib/primer.py`): 시장학개론 원칙을 정리한 `knowledge/market_primer.md`를 BM25로 검색함.
처음에는 ReAct 툴로 줬지만 0회 호출되었음. 그래서 상황에 맞는 절을 재고 검토 프롬프트에 미리 넣는 방식으로 바꿈.

**meta-harness 바깥 루프**:

1. trace: 요약이 아닌 원본 jsonl을 남김.
   - `buyer-decisions.jsonl` 133,361줄: 주문마다 후보, 견적 도착 시각, arm, 흥정 전문
   - `decisions.jsonl`, `react.jsonl`, `org-calls.jsonl`, `kpi.jsonl`
2. 제안: 사람과 Claude Code가 trace를 직접 읽고 원인을 찾음.
   - 예: "첫 호가를 그대로 받은 거래 1,127건 중 663건이 마진 점유율 0.9 미만"을 찾아 역제안 규칙을 만듦.
3. 배포: CI를 거침 (`ops/ci.py`: 범위 · 비밀 · 컴파일 · lint · import · pytest).
   - `ops/deploy_pull.py`가 2분마다 hub와 맞추고, 코드가 바뀌면 재시작한 뒤 헬스를 봄. 실패하면 revert함.
4. 판정: canary arm별로 미리 적은 규칙을 적용함. 기록은 배포 노트 26개(`logs/r3/deploy-*.txt`)에 있음.

**디지털 트윈 + shadow gate**: 2절 5번 참고. 라이브 경로는 import하지 않는 오프라인 사전 검사임.

### 3.3 툴

| 툴 | 사용자 | 호출 (`react.jsonl`) |
|---|---|---|
| `category_evidence`, `sell_through`, `stock_on_hand`, `plan_preview` | 재고 검토 ReAct (`restock_review_r3.py`, 634회) | 1,103 · 817 · 808 · 642 |
| `recall` (기억 검색), `scout`, `oracle` | 재고 검토 · 포럼 | 463 · 76 · 2 |
| `our_experience`, `market_facts`, `recent_forum`, `my_reception`, `ledger_refs` | 구매자 글 ReAct (`forum_r3.py`, 90회) | 92 · 89 · 127 · 89 · – |
| `ledger_query` (LATM으로 만든 툴) | 둘 다 제공 | 1 |

- ReAct (`r3lib/react.py`): Thought → Action → Observation 루프임. 툴은 모두 읽기 전용임.
  재고 검토 결과는 카테고리 수요 배율 하나이고, 코드가 범위를 제한함. 342회 중 287회 적용됨.
- 툴 제작 (`r3lib/forge.py`, LATM): 제안 → 검증 → 등록의 세 단계임.
  - 검증 1: 정적 검사. 허용 import만 통과하고, 파일 쓰기 · 프로세스 · 네트워크는 금지함.
  - 검증 2: 격리 테스트. 별도 프로세스, `-I`, 키 없음, 소켓 없음, 시간 제한.
  - 검증 3: 시장을 움직이는 툴은 shadow gate PASS가 필요하고, 돈이 걸리면 사람 승인도 필요함.
  - 등록: sha256이 맞는 툴만 다음 ReAct에 제공함.
- Horvitz 행동 판단 (`r3lib/initiative.py`): p > C/(B+C)일 때만 먼저 행동함.
  - 대상: 과잉 재고 할인, 안 팔리는 등급 먼저 보이기, 먼저 글쓰기.
  - 판단 8,974건을 실행 전에 기록함.

## 4. Round 3 진행 (배포 노트 기준)

| 날짜 | 변경 | 판정 |
|---|---|---|
| 11-04 | 물류 개선, 내부 A2A 조직 배포 | 운영 |
| 11-05 | 디지털 트윈 + shadow gate, 포럼 후보 2개(lever2, influencer) | 후보는 스위치 False로 라이브에 넣지 않음 |
| 11-07 | 호가 상한 `ASK_MAX_OVER_MARKET` 0.04 | 7.6시간: 점유율 0.019→0.026, 체결률 0.37→0.41, 시간당 book 2.09M→2.72M |
| 11-09 | 보증 6→72시간 | 운영. 같은 날 ACP URL 불일치 사고: 영수증 113건 미정산 (17.3M), 수정 후 재가입 |
| 11-10 | Horvitz 판단 + bandit + 과잉 재고 할인 + forge, 재고 매입 근거 게이트 | 운영 |
| 11-11 | C등급 먼저 보이기(`shelf`), 재고 상한, 프리미엄 상품 | 운영 |
| 11-12 | 구매자 동시 견적 canary 20% | 채택: 거래/주문 0.542 vs 0.386 |
| 11-12 | 구매자별 등급 취향(`taste`) | 운영 (별점 백분위 방어) |
| 11-13 | patch 3.6 saved 식으로 구매자 점수 계산 | 운영 |
| 11-14 | 첫 제안을 추정 원가로 canary | 채택: saved 0.878 vs 0.860 |
| 11-15 | 견적 조기 마감 canary | 채택: 주문 시간 8.5 vs 23.9초, 시간당 주문 119→135 |
| 11-15 | 재고 상한 0.60→0.70 | **전제가 틀림** (상한이 묶인 적이 없었음), 되돌림 |
| 11-15 | 최소 8초 견적 대기 | **실패**: 거래/주문 그대로, 시간당 주문 162→148, 되돌림 |
| 11-15 | 첫 호가 역제안 canary | 채택(13.5시간): 마진 점유율 0.890 vs 0.872 |
| 11-16 | 10초 견적 단축 canary | **실패**(2.2시간): 거래/주문 0.540 vs 0.645. 늦게 온 견적이 거래였음. 되돌림 |

실패한 실험도 커밋과 배포 노트로 남김 (`git log`, `logs/r3/deploy-20261115T0933Z-short-term.txt`).

## 5. 한계 (정직한 기록)

- LATM 툴 `ledger_query`는 등록과 검증까지 되었지만 ReAct가 라운드 중 1회만 불렀음.
  만드는 경로는 동작하지만 쓰임은 증명하지 못함.
- 트윈 매출은 같은 거래 기준 실제보다 약 20% 낮음. 거래 수 오차는 3% 안임.
  별점 · 다른 가게 · 보증 하한 · 식품 유통기한은 트윈에 없음.
- 포럼 후보 2개(lever2, influencer)는 오프라인 dry run까지만 했고 라이브에 켜지 않음.
- 별점: 계정마다 평균을 내고 4.0 × 20으로 수축하는 식이라 60개 계정에서 거의 움직이지 않음.
  q00 계정의 1점 리뷰는 영수증 검사를 모두 통과한 거래여서 우리 쪽에서 고칠 방법이 없었음.
- 테스트: `tests/` 355개 중 353개 통과. 실패 2개는 벽시계에 의존하는 테스트임
  (`test_restock_plan_evidence_then_coverage`, `test_review_applies_then_expires`).
- 2026-11-17 12:00 KST 이후 코드 배포는 없음 (동결).

## 6. 실행 방법

```bash
# 키트 v4가 agent.json의 훅을 실행함
python scripts/market_kit.py join --dir submissions/26510358/week-08 --market https://market.ourolabs.ai --name myeonggi --tunnel
# 서버에서는 같은 명령을 market_runtime.py(주문 폴링 복구)로 감싼 systemd 서비스로 돌림
pip install -r requirements.txt
pytest -q tests          # 키 파일 대신 일회용 Ed25519 키를 씀
xelatex architecture.tex # 구조 그림
xelatex sequence.tex     # 시퀀스 다이어그램
```

| 훅 (`agent.json`) | 파일 |
|---|---|
| seller | `shop_r3.py` |
| market_buyer | `buyer_r3.py` |
| restock (60초) | `restock_r3.py` → 내부 조직 `r3lib/org`, 백그라운드 `forum_r3.py`(10분), `restock_review_r3.py`(30분) |
| review | `review_r3.py` |

키(`*.pem`, `.market/`, `.env`)는 커밋하지 않음.
