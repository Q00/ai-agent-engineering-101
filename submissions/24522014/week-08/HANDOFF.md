# Round 3 업무 인수인계서

작성: 2026-11-04 약 19:25 KST. 수치와 프로세스 정보는 당시 관측이며 다음 세션에서 다시 확인한다.

## 1. 다음 세션이 먼저 알아야 할 내용

사용자는 자기 개선 모니터링이 실제 개선으로 이어지지 않는 이유를 조사해 달라고 요청했다. **원인 조사는 끝났고, 아래 버그와 설계 병목의 수정은 아직 하지 않았다.** 마지막 요청은 이 인수인계서 작성이다. 이 문서가 새로운 전략 변경의 승인을 대신하지는 않는다. 다음 세션에서 사용자가 수정 진행을 요청하면 아래 우선순위로 이어간다.

가장 급한 문제는 `buyer_fast.py`의 가격 인식 오류다. `asking 110`은 인식하지만 **`asking 110.`은 문장 끝 마침표 때문에 거부**한다. `Our asking price is 42630.`의 `is`도 지원하지 않는다. 흔한 실제 견적이 규칙 경로를 통과하지 못해 제한된 모델 협상으로 넘어가고, 모델 한도에 걸리면 구매를 포기한다. 기존 테스트는 마침표 없는 문장 위주여서 이 문제를 놓쳤다.

현재 공식 목표는 **SHOP ALL + BUYER ALL의 합인 Players 종합 Top 3**다. 이전의 seller 순이익 1위 단독 목표와 연구 전용 buyer는 변경 이력이다. Round 2 전략은 재사용하지 않는다.

## 2. 작업 환경과 지켜야 할 제약

| 항목 | 값 |
|---|---|
| 저장소 | `C:\Submissions\ai_agent_engineering` |
| 작업 폴더 | `submissions/24522014/week-08/` |
| 학번 / GitHub / 시장 이름 | `24522014` / `kcwarni` / `kcwarni` |
| 셸 | Windows PowerShell |
| Python | 작업 폴더의 `.venv/Scripts/python.exe` |
| 모델 | seller/buyer 설정 모두 Codex `gpt-6-luna` |
| 마지막 확인 브랜치 | `week-08` |

필수 작업 규칙:

- 쓰기는 `submissions/24522014/` 안에서만 한다. root 강의 HTML, `weeks/`, `scripts/`, 다른 학생 파일은 읽기 전용이다. root `AGENTS.md`와 사용자가 제공한 지침을 먼저 확인한다.
- API 키, claim code, `.env`, PEM 개인키를 출력하거나 커밋하지 않는다. 새 세션에서 사용자 터미널의 인증 환경이 상속된다고 가정하지 않는다.
- 원본 로그를 수정·정리·삭제하지 않는다. 실패한 테스트 기록도 남긴다. 재검증은 새 로그 파일명으로 저장한다.
- 기존 변경을 초기화하거나 다른 작업을 덮어쓰지 않는다. `README.md`, `logs/market-kit.log`, `MONITORING-NOTICES.md`에는 기존 또는 실행 중 변경이 있으므로 무심코 스테이징하지 않는다.
- 사용자는 잦은 커밋 제안·확인 질문을 원하지 않는다. 검증한 논리 단위로 한 커밋에 정리하고, 요청 없이 push/PR을 하지 않는다. squash/이력 재작성 금지.
- `.git`은 현재 환경에서 쓰기 권한 승인이 필요할 수 있다. 필요한 경우 도구의 승인 절차를 사용하고, 원인은 `.git` 권한이라고 설명한다.
- `git status`나 로그 디렉터리 전체를 무제한 출력하지 않는다. 실행 로그와 미추적 파일이 많다. 범위를 지정한 `rg`, `git diff`, 파일 읽기를 사용한다.
- PowerShell에서 Python stdin으로 한글을 전달하면 인코딩이 깨질 수 있다. 한글 문서는 `apply_patch`, 진단 스크립트는 가능하면 ASCII로 작성한다.
- 별도 세션이 실행 중인 키트를 임의 종료하거나 중복 실행하지 않는다. 사용자에게 재시작이 필요한 경우만 한 번 요청한다.
- 새 세션에서는 상위 지침을 다시 확인한다. 현재 지침상 사용자 또는 적용 지침의 명시적 요청 없이 서브에이전트를 생성하지 않는다.

현재 checkout에는 `weeks/week-08/README.md`와 `scripts/check_week08.py`가 없다. 다음 세션에서 다시 존재 여부를 확인하고, 없는 검사를 실행했다고 주장하지 않는다.

## 3. 승인받아 구현한 정책과 현재 상태

### 목표와 점수

- SHOP ALL: 순이익 백분위 50% + 매출 30% + 별점 20%.
- BUYER ALL: 잉여 백분위 50% + forum 30% + 거래 수 10% + 글 수 10%.
- Players: 위 두 공식 ALL의 합. 자체 추정 점수로 공식 ALL을 대체하지 않는다.
- buyer 주문 예산과 seller 현금은 별도다. buyer 구매 축소를 seller 현금 절약으로 계산하면 안 된다.
- 같은 소유자 buyer와 shop의 자기 거래는 정산되지 않는다. 상호 구매·평점 조작을 새 전략으로 도입하지 않는다.

### 최신 설정

| 영역 | 현재 설정 | 파일 |
|---|---|---|
| buyer | `balanced`, UTC 하루 최대 480회 시도, 최소 30초 간격, 동일 가게·SKU 300초 간격, 최대 3턴 | `buyer_support.json` |
| buyer 모델 | 시간당 최대 2개 주문 / UTC 하루 24개 주문. 명확한 견적은 규칙 처리할 의도이나 파서 버그 존재 | `buyer_support.py`, `buyer_fast.py`, `market_buyer.py`, `buyer.py` |
| 구매 안전 | 인증 A/B, 예산·가치·위험·서명·영수증 검사. 식품은 사용기간 등 여유 조건 유지 | `r3_policy.py`, `r3_selection.py` |
| 포럼 | 수집 5분 / 분석 30분, buyer 최대 12개/일 중 최초 보고 최대 9개, 간격 1시간. shop 4개/일 | `forum_capture*`, `forum_policy.py`, `forum_posting.json` |
| 포럼 반응 방어 | 내 글 3개 이상, 나빠요 3개 이상, 반응의 25% 초과면 buyer 4개/최초 보고 3개로 축소 | `forum_policy.py` |
| 재고 확대 | 전체 16개 / 검증 SKU당 3개 / 회당 최대 6개, 미검증 최대 2개 | `restock_capacity.json`, `restock_policy.py` |
| 매입 예산 | 회당 최대 5%; 현금 예비비·7일 보관비·보증 책임액 확보. 식품 재입고 보류 | `strategy_policy.py`, `restock_policy.py` |
| 가격 실험 | **UTC 하루 6회**. 재입고 예산 실험과 공유하는 한도. 동시 1개, 변경 폭 최대 3% | `strategy_controller.json` |
| 실험 시간 | 분석 30분, 기준선 2시간, 시험 2시간, 성숙 확인 6시간, 최대 12시간. 별도 유휴 대기는 0초 | `strategy_controller.json` |
| 실험 표본 | 신규 자기 정산 10건, 대상 동일 SKU·등급의 전후 각 최소 3건 | `strategy_controller.py` |
| 후기 연결 | 공개 후기 ledger → 로컬 확정 정산 → unit/SKU의 유일한 연결. SKU 3건 미만이면 약함 판정 보류 | `seller_quality.py` |
| 보증 | 인증 A등급 비식품 2시간, 보증/대조 각 최대 5개 견적 배정/일, 10분 견적 유효기간 | `warranty_trial.py`, `warranty_trial.json` |
| 보증 방어 | 20% 전액 환불 스트레스 가격 하한. 전액 책임 준비금이 잔고 10%·100만원 중 작은 값 이내, 기본 현금·보관비 별도 확보 | `warranty_trial.py` |

하루 6회는 상한이다. 동시 실험과 성숙 시간을 유지하므로 매일 6개 완료를 보장하지 않는다. 사용자가 늘려 달라고 승인한 것은 일일 한도이며, 안전·성숙 조건 전체 해제는 아니다.

규칙 모니터링·후기 연결·포럼 생성·점수 분석 자체는 LLM을 호출하지 않는다. buyer 모델의 주문 한도는 호출 수와 다르며, 3턴·JSON 재시도 2회를 고려한 의사결정 하네스 호출 상한은 하루 144회다. seller 모델 호출은 별도다. 실제 토큰 총량은 측정 없이 단정하지 않는다.

## 4. 실행 구조와 증거의 경계

1. `run_market.py`가 공식 `scripts/market_kit.py`를 실행한다. 키트 v4, join/tunnel, ACP/AP2 결제, 재고 동기화는 수업 제공 인프라다. 공유 `scripts/`를 수정하지 않는다.
2. `seller_r3.py`는 상주 프로세스다. 모델 제안 외에 재고·하한·조건 재검사·예약·서명을 코드로 강제한다. 소스 변경 시 보통 키트 재시작이 필요하다.
3. `market_buyer.py`, `restock_policy.py`, `review_policy.py`는 키트가 짧게 실행하는 훅이다. 해당 소스/설정은 다음 호출에서 읽으며 통상 키트 재시작이 필요 없다.
4. `market_intel.py`는 공개 시장 자료를 30분마다 수집한다. restock 훅에서 포럼·가격·선두·후기·보증 관측과 `strategy_controller.run()`을 연결한다.
5. `strategy_controller.py`: 진단 → 작은 정책 시험 → 고정 시험 구간 종료 → 성숙 대기 → 채택/복원. 자동으로 Python 코드를 다시 작성하는 에이전트는 아니다.
6. `improvement_notices.py`: 터미널과 `MONITORING-NOTICES.md`에 상태/개선을 알린다. **대화창 push 알림이나 Windows 알림 기능은 없다.**

중요한 증거 구분:

- 자기 seller 성과는 `local_settlements.py`가 실제 키트 `[acp] settled ... ledger #...` 로그를 R3 시작 표식 이후부터 인덱싱한 로컬 확정 정산을 사용한다.
- `.market/local-settlements.json`: R3 anchor, 충돌 ledger, 신선도, 중복을 검사한다. 서명 제안 metadata로 실제 unit 원가·SKU·실험 ID를 연결한다.
- 공개 ledger 및 `.market/purchase-intel.json`은 시장 가격·조건 비교 자료다. 공개 기록을 우리 확정 정산으로 섞지 않는다.
- buyer A2A에서 `purchased`/영수증을 받아도 **시장 결제 완료는 아니다.** `.market/payments.jsonl`의 `paid:true`와 ledger를 확인한다. 실제로 영수증 수신 뒤 등급·유통기한으로 거부된 사례가 있다.
- 공개 후기·고장·환불은 제한된 표본이다. SKU/이름만 같다는 이유로 귀속하거나 전체 불량률·인과 효과를 단정하지 않는다.

## 5. 마지막 점검 결과: 개선되지 않는 이유

### P0. 가격 파서의 문장 끝 마침표 및 표현 누락

위치: `buyer_fast.py`의 `decision()`, 숫자 뒤 `(?![\d.])`와 가격 라벨 정규식.

로컬 재현:

```python
from buyer_fast import decision
decision('asking 110', 300000)                   # offer
decision('asking 110.', 300000)                  # None: 오류
decision('Counteroffer: 234659.', 300000)         # None: 오류
decision('Our asking price is 42630.', 300000)    # None: 표현 누락 + 마침표
```

수정 시 문장 종결 마침표와 실제 소수를 구별해야 한다. `100.50`, `1,00`, 복수의 상충 가격, 예산 초과, 반복 제안은 여전히 안전하게 거부/보류한다. 마지막 숫자나 상품명 속 숫자를 무조건 가격으로 사용하면 안 된다. 일반 `price` 표현도 list price·과거 가격·시장 중앙값과 혼동할 수 있으므로 무차별 확대하지 않는다.

18:39~19:16:19에 시작된 협상 55개를 고정 표본으로 조사했다. 38개 포기, 14개 판매자 거절, 2개 영수증 수신, 1개 당시 진행 중이었다. 포기 38개 모두 의사결정 로그가 없었고, 현재 코드의 모델 한도 차단 경로와 부합한다. 이 중 가격 라벨과 숫자가 예산 이내인 사례 8개도 파서가 처리하지 못했다. **8개 모두 구매 가능했다는 뜻은 아니다**: C등급·식품 수명 등 추가 조건 검사가 필요하다. 당시 영수증 2개도 실제 결제는 거부됐다.

### P1. 구매 포기 원인이 일반 메시지에 가려짐

`buyer.py`의 `buyer_decision()`은 모델 gate가 막히면 `walk_away`를 즉시 반환한다. 이 경로에는 `decision-NN.json` 기록이 없다. 바깥 결과는 일반적인 `No deal within budget`으로 표시돼 예산 부족·협상 실패·모델 한도를 구분하기 어렵다.

개선 방향: 모든 결정 경로에 source/reason을 남기고, 주문 선택 → 견적 → 제안 → 영수증 → 실제 결제의 전환율과 실패 원인을 집계한다. 로그를 늘리더라도 키/민감 데이터는 넣지 않는다.

### P1. 실험을 완료 평가한 기록이 없음

당시 제어기 journal 46개에서 진단 27회, 실험 시작 2회, rollback 2회, **evaluation 0회·keep 0회**였다. 두 복원 사유 모두 `operator_objective_changed`다. 목표가 `player_top3` → `seller_profit_rank_1` → `balanced_player_top3`로 변경되면서 평가 전에 종료됐다. 이는 사용자 목표 변경에 따른 정상 안전 동작이며 실험 실패가 입증된 것은 아니다.

목표를 안정적으로 유지하며 하나의 실험을 끝까지 관측해야 한다. 목표가 다른 실험의 결과를 새 목표의 성공 증거로 재사용하지 않는다.

### P1. 후보 생성 범위가 좁고 현재 후보는 0개

위치: `strategy_controller.py`의 `choose()`와 `observation()`.

- 같은 SKU·등급·보증 0시간의 가격 표본 최소 3개, 견적과 중앙값 차이 5% 초과가 기본 조건이다.
- 후기 기반 후보는 SKU별 최소 3개 후기, 평균 4.5 미만 및 낮은 후기의 가격 단서 2개 이상이 필요하다.
- 마지막 직접 점검에서 재고 10개 중 7개는 충분한 비교 표본이 없고, 나머지 3개는 중앙값과 가격 차이가 5% 이내여서 `choose()`가 빈 목록을 반환했다.
- `no_eligible_market_candidate` 하나로 대부분을 묶어 표시한다. 데이터 없음·가격 차이 부족·하한·후기 부족·선택 후 자체 표본 부족을 분리해야 한다.
- 구매/가격 분석과 시장 수집은 각각 주기가 있어 분석 원본 시각이 늦을 수 있다. 처리 시각과 `source_observed_at`을 모두 확인한다.

수정 방향은 근거 없는 가격 변경 강제가 아니라, 배제 사유를 관측하고 수요·회전·가치 있는 안전한 소규모 후보를 정의하는 것이다. 기준 완화는 현재 전략 변경이므로 사용자의 후속 요청 범위 안에서 설계한다.

### P2. 진단 지표에 비해 자동 변경 범위가 작음

현재 제어기 action은 `quote_cut`, `quote_raise`, `restock_throttle` 세 종류다. BUYER 거래 정체를 보지만 구매 실패 회피·선택·포럼 전략을 자동 조정하지 않는다. 재고 보충과 후기 반영 등의 별도 규칙은 있지만, 전체 전략을 목적에 맞춰 재작성하는 구조는 아니다.

향후 buyer 전환율과 실패 원인 → 작은 허용 정책 변경 → 실제 결제/잉여 평가 → 악화 복원으로 확장할 수 있다. 상대 가게의 파산이나 품질 방어 해제를 목표로 삼지 않는다.

### P2. 로컬 효과와 전체 순위 점수를 채택 조건에서 혼합

위치: `strategy_controller.py`의 `balanced_player_top3` 평가 분기. 로컬 개선 외에 Players 합계 +0.005를 동시에 요구한다. 경쟁자 성장·BUYER 정체가 seller 가격 시험 채택을 막을 수 있다. 아직 evaluation 0회이므로 **실제로 이 조건 때문에 탈락했다는 증거는 없다.**

개선 방향: SKU의 직접 효과·위험 평가와 전체 순위 추적을 구분한다. 전체 점수만으로 실험의 인과 효과를 주장하지 않는다. 보증 실험도 성숙 전 조기 성과를 주장하지 않는다.

## 6. 다음 작업 권장 순서와 완료 기준

1. 사용자의 후속 수정 요청 범위를 확인하고 `buyer_fast.py` 버그부터 수정한다. 파서 단위 검사와 `market_buyer.trade()`의 모의 HTTP 통합 검사를 함께 보강한다. 실제 시장에 불필요한 시험 결제를 만들지 않는다.
2. 모델 quota 거부를 포함한 모든 구매 결정의 사유를 기록한다. 실제 `paid:true`를 분모/분자에 맞게 사용해 구매 전환율을 보고한다.
3. 제어기 후보 배제 사유를 집계한다. 현재 대기 원인을 구체적으로 설명할 수 있어야 한다. 관측만으로 후보가 생겼다고 주장하지 않는다.
4. P2의 전략 변경은 사용자 요청 범위에서 진행한다. 단순히 LLM 한도를 늘리거나 인증·유통기한 조건을 낮추는 방식으로 해결하지 않는다.
5. 필요한 테스트를 통과한 뒤 새 훅의 실제 기록을 확인한다. 짧은 훅만 바꿨다면 불필요한 재시작을 요구하지 않는다. 상주 seller를 바꿨다면 사용자 재시작 후 feature/heartbeat와 실제 새 영수증을 구분해 검증한다.
6. `REPORT.md`와 최신 전략 문서에 변경·근거·한계를 반영한다. 구현 검증과 실전 순위 개선을 구분해 보고한다.

## 7. 실행과 검증 명령

저장소 root에서 사용자 키트 실행 명령:

```powershell
& ./submissions/24522014/week-08/.venv/Scripts/python.exe -X utf8 -u ./submissions/24522014/week-08/run_market.py --tunnel
```

이미 실행 중이면 위 명령을 중복 실행하지 않는다. 사용자 터미널에서는 별도 로그인 없이 모델 실행이 가능했던 이력이 있지만, 새 세션에서 임의로 재인증하거나 토큰을 요구하지 않는다.

작업 폴더에서 테스트:

```powershell
Set-Location C:\Submissions\ai_agent_engineering\submissions\24522014\week-08
& ./.venv/Scripts/python.exe -X utf8 -m unittest discover -p 'test_*.py'
```

변경 후 콘솔 출력은 새 `logs/<작업명>-<시각>.log`에 보존한다. 기존 파일을 덮어쓰지 않는다. 마지막 전체 검증은 **269개 통과**이고 근거는 `logs/expanded-trial-quota-tests.log`다. 테스트 모의 실행은 실제 시장 결제 성공이나 순위 개선의 증거가 아니다.

제어기 실행은 **실제 정책을 바꿀 수 있는 명령**이다. 단순 관측 목적으로 무심코 실행하지 않는다:

```powershell
& ./.venv/Scripts/python.exe -X utf8 strategy_controller.py
```

위 명령은 `force=True` 진단이며 적격 조건이면 실험을 시작/평가/복원한다. `.market` 상태와 lock을 수동 초기화해 실험 한도나 안전장치를 우회하지 않는다.

아키텍처 변경 시:

```powershell
& ./tools/tectonic/tectonic.exe --keep-logs --outdir tools/architecture-build architecture.tex
Copy-Item -LiteralPath tools/architecture-build/architecture.pdf -Destination architecture.pdf
```

한 페이지 유지와 빌드 로그를 확인한다. 마지막 빌드의 Fontconfig 경고는 있었지만 PDF 한 페이지 생성은 성공했다.

## 8. 다음 세션 시작 시 읽을 파일

| 목적 | 파일 |
|---|---|
| 최신 전략·안전장치 | `BALANCED-STRATEGY.md` |
| 구현·평가 보고서 | `REPORT.md`, `architecture.tex`, `architecture.pdf` |
| 제어기 설계·이력 | `STRATEGY-CONTROLLER.md`, `PROPOSAL-STATUS.md` |
| 이번 진단의 고정 증거 | `logs/improvement-diagnosis/1793787541.json` |
| 최근 검증 | `logs/expanded-trial-quota-tests.log`, `logs/expanded-trial-quota-activation.log` |
| 이전 균형 전략 검증 | `logs/balanced-validation-final.log`, `logs/balanced-deployment/20261104T093758Z.json` |
| 실험 실행 기록 | `logs/strategy-controller/`의 최근 파일 및 start/evaluation/keep/rollback 사건 |
| 구매 실패 원본 | `logs/buyer/20261104T093900...` 이후의 `session.json`, `outcome.json`, `*.response.txt`, `decision-*.json` |
| 실제 결제 | `.market/payments.jsonl`, `.market/buyer-deals/` |
| 현재 관측·설정 반영 | `.market/strategy-state.json`, `strategy-policy.json`, `strategy-consumer.json`, `market-intel.json` |
| 후기·보증 | `.market/seller-quality.json`, `warranty-trial.json`, `warranty-evaluation.json` |
| 알림 | `MONITORING-NOTICES.md`, `logs/market-kit.log`의 `[모니터링]` |

`.market`과 로컬 대용량 로그는 다른 PC/checkout에 없을 수 있다. 없으면 접근 가능 범위를 명시하고, 이 문서의 과거 값을 실시간 상태로 표시하지 않는다. A2A 응답 원본에는 `result.task`로 중첩된 형태가 있으므로 분석 시 이를 풀어야 한다.

## 9. 마지막 관측과 커밋

- 상주 seller PID 18624, `strategy-consumer.json` features에 `actual_grade_receipt`, `bounded_warranty_v1` 확인. 재시작 후 새 코드 로딩은 확인됐다. PID는 다음 세션에서 바뀔 수 있다.
- 마지막 공식 시장 snapshot은 19:09경, Players 25위, SHOP ALL .580 / BUYER ALL .511 / 합계 1.091이었다. buyer 잉여는 12,002,410으로 정체됐다.
- 제어기: `balanced_player_top3`, `waiting_for_comparable_evidence`, `no_eligible_market_candidate`, 진행 실험 없음. 일일 실행 횟수 2회, 한도 6회.
- 직접 점검 당시 자기 확정 정산 366건 모두 원가 연결, 누락 metadata/충돌 ledger 없음. 매출·이익 증가를 자율 실험의 인과 효과로 해석하면 안 된다.
- 보증 견적 배정은 보증 5개·대조 5개까지 있었지만, 성숙 비교 각 0건이었다. 배정은 실제 정산/보증 성공과 다르다.

직전 구현 커밋:

| 커밋 | 내용 |
|---|---|
| `ca0a8dc7` | 일일 실험 한도 2→6, 269개 테스트 통과, 즉시 재진단 |
| `fce29e7f` | SHOP·BUYER 균형 전략, 구매 규칙 경로·후기·보증 시험·공식 ALL 평가, 267개 테스트 |
| `a63ea9cd` | 변경 후 유휴 대기 제거, 재고 16/3/6 확대 |

이 인수인계서 작성 중 **시장 운영 코드·한도·정책을 추가로 변경하지 않았다.**

## 10. 새 세션에 전달할 시작 요청 예시

> `submissions/24522014/week-08/HANDOFF.md`를 먼저 읽어줘. 기존 키트는 실행 중이니 종료하거나 중복 실행하지 마. 최신 상태를 확인한 뒤 P0 가격 인식 버그와 P1 구매 실패 원인 로깅부터 수정하고 검증해줘. 인증·등급·유통기한·예산·정산 안전장치는 유지하고, 운영 정책을 더 바꿔야 할 부분은 근거와 함께 설명해줘.
