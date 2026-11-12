# Week 08 — mjforge Round 3 운영 보고서

학번 **25512085** · 작성 기준 **2026-11-13** · **제출 준비 중(미완료)**

기준은 로컬 최신 코드가 아니라 VM HEAD **`6bb3cd855fe76a29e1412699688595069ca5fa45`**이다. 11월 13일 01:20–01:26 KST 읽기 전용 조사에서 허용 소스 15개가 VM HEAD와 해시 일치하고 tracked dirty가 0이었다. 이는 디스크 증거이지 실행 중 모든 import의 동일성을 증명하는 것은 아니다. buyer `17e0f24` 대응은 메인 handoff의 버전 설명이며 이 문서 작업에서 Git ancestry를 재검증하지 않았다.

과제 기준: [한국어 736–766행](../../../week-08.html), [영어 730–760행](../../../week-08-en.html). 기존 구현 시점의 [보고서 원문](history/20261113-report-figure-01/prior-report.md)은 별도 보존했다. 기존 로그·실패 이력·소스는 수정하지 않았다.

**근거 표기:** [E: 운영 증거 요약](history/20261113-submission-alignment-01/evidenceSummary.md), [R: 구조와 소스 행 번호](history/20261113-submission-alignment-01/runtime-structure.md), [S: 운영 소스 사본](history/20261113-submission-alignment-01/runtime-source/submissions/25512085/week-08/), [K: 키트 소스](history/20261113-submission-alignment-01/runtime-source/scripts/market_kit.py), [M: 지표 snapshot](history/20261113-submission-alignment-01/sanitizedmetrics.json). 아래 행 번호는 로컬 pending 코드가 아니라 S/K 기준이다.

## 1. 부품 구현 (40%)

### 모델: 운영 정책과 개발 AI를 구분한다

현재 seller의 가격·협상·수락은 규칙 기반이다. Round 3 `market_buyer.py`는 `if not is_round3` 아래의 `call_harness`를 우회한다(1243–1253행). `forum_writer.py`는 `FORUM_USE_MODEL=False`로 템플릿을 사용한다. no-deal 후기에는 `random.choice`가 있으므로 전체 출력까지 결정론적이라고 주장하지 않는다.

legacy 경로의 `gpt-5.6-sol`, Gemini 후보 이름은 호출 성공 증거가 아니다. **현재 Round 3 LLM 사용·성능 비교·비용 측정은 입증하지 못했다.** 개발/문서 작성에는 AI를 사용했다(이번 문서 초안: GPT-6 Astra). 이는 시장 운영 모델과 다르며 과거 구현 담당 모델은 미확인이다. 과제의 모델 부품 평가를 규칙 엔진만으로 충족한다고 단정할 수 없다. 학생이 이 선택과 한계를 설명하고 평가 해석을 확인해야 한다(R 모델 표).

### 컨텍스트: 무엇을 남기고 다음 결정에 쓰는가

| 저장 계층 | 갱신·보존 한계 | 다음 결정의 입력 |
|---|---|---|
| seller working memory | `self.open`, `self.committed_units` 메모리 dict. 생성 후 >90초 / 서명 후 >120초, 다음 execute에서 정리. 재시작 복구 없음 | 진행 중 협상과 중복 unit 선택 억제 |
| A2A SDK / kit 세션 | SDK task 및 kit ACP 세션은 별도 메모리 계층. durable 복구나 ACP 300초 만료 보장 없음 | task 진행, checkout 상태 |
| `seller_history.json` (legacy) | trades 최근25, reviews 최근5, 품목 가격40. 전체 실정산 원장 아님 | legacy 경로의 기록이며 현행 Round 3 견적의 주 입력으로 표시하지 않음 |
| `memory.json` | 가게별 최근5개와 유효 평점 archived count/average | buyer가 직접 읽어 trust, 재방문·다양성·실패/cooldown 순위에 사용(hub를 경유하는 입력 아님) |
| `.market` 스냅샷 + `state_hub.json` | kit 스냅샷과 별도로 restock→store의 공개 원장 직접 GET·정규화·hub 반영. structured comparables SKU별50개/3h, scalar 가격30개(구매자는 최근 유효15개), shop tag 전체120행/조건3개 | 현행 seller는 hub/cash policy를 사용. buyer의 조건별 가격 비교, 수요·매입·신뢰 판단에도 사용 |
| 정산 후 구매 목록 | review hook에서 `my_purchases` 최근150개로 갱신 | 실제 구매 후기·게시 근거; receipt만 받은 pending은 성공 아님 |
| append journal | hub lock 밖 append/flush/fsync. 256 MiB 상한 초과 시 `journal_capacity_unknown`; replay도 event/order 상한 존재 | 사후 주문 결과 연결·감사. 전체 공식 Saved나 다음 가격 입력으로 자동 환산하지 않음 |

근거: R 「Buyer 정책과 메모리 보존」, S `store.py:218–238,308–331,450–468`, `buyer_outcomes.py:167–193`. 원본 state/journal 내용은 이 문서 작업에서 읽지 않았다. 원장 coverage, food served/value, legacy kind 등이 없으면 **unknown**을 유지한다. 이벤트 부재를 무고장·성공으로 보지 않는다.

### 툴: 자체 정책과 키트 책임 경계

- **우리 코드:** `market_buyer.py`가 후보 ranking, 재시도, 정수 제안, 영수증 가격/허용 SKU/서명 존재/예산 gate, `accept` 조건을 구성하고 `{shop, receipt, accept, ...}`를 kit에 반환한다. goods 최종 receipt gate는 정산 표본5개 미만이면 ExpectedSaved>0, 5개 이상이면 **보증 유무와 관계없이 >0.25**이며 등급·보증도 확인한다. food는 사용기간+0.25h를 요구한다. A2A 상대 가게와 협상하지만 실제 인증서 검증과 결제는 kit에 위임한다.
- **우리 seller:** A2A AgentExecutor에서 재고·조건 파싱 → SKU/cohort 선택 → 단일 `seller_cash_policy.py`의 견적 → Ed25519 서명 영수증. 직접 `/api/settle`하지 않는다. cohort 내 실제 견적과 보유 비용 tie band를 비교한다. 식품은 사용기간과 만료를 먼저 확인한다.
- **우리 연결부:** `guarded_market_kit.py`/`visibility_guard.py`는 kit에 가시성 보호를 연결한다. `restock.py`는 `{buy: [...]}` 수급 계획 JSON을 반환하며 실제 매입은 kit가 실행한다. `review.py`는 정산 뒤 기억·신뢰를 갱신하고 `{stars, review}`를 반환하며 `forum_writer.py`가 outbox 초안을 만든다. 실제 게시도 kit 책임이다.
- **제공 키트:** v4 proxy, `/.well-known/acp.json`, checkout create/complete, 영수증·서명·금액·인증 조건 확인, `checkout_jwt` 및 AP2 closed mandates 처리, 시장 `/api/settle`, sync/outbox orchestration을 사용한다(K:228–552,705–765). 이를 자체 제작 결제 프로토콜이라고 하지 않는다. acp.json에서 임의 툴을 자동 생성하는 선택 과제도 구현했다고 주장하지 않는다.

**예산 기본 조건:** buyer는 제안 상한과 실제 receipt 가격을 재검사하고 kit의 open mandate 범위 검증을 거친다. [합성 테스트 코드](test_buyer_savings_guard_offline.py)의 `test_cheap_completed_and_budget_boundary`는 budget 70000에 receipt 70001을 거절하도록 검증한다. 테스트의 존재와 이번 실행 성공은 다르다(이번 미실행). acceptable SKU가 checkout에서 교체될 때 개인 risk/정가 할인 cap을 완전히 보장하는 것은 별도 미해결이며, 이를 open mandate 상한과 혼동하지 않는다.

### 가격·Saved·재고 정책과 한계

buyer 후보의 할인율과 **공식 Saved는 다르다.** [11월 9일 step1 기록](history/20261109-buyer-saved-strategy-step1/IMPLEMENTATION.md)(10–14행)은 메인이 당시 공식 agent.md에서 확인해 전달한 정의를 보존한다: surplus=`value-price+refund`, 거래별 Saved는 surplus/list, 아무것도 제공하지 못한 거래는 -1(환불 시 0), 합계를 `(deals+100)`으로 나눈다. 이 문서 작업은 외부 URL을 재조회하지 않았고 해당 기록에 정확한 URL이 없어 독립 공식 검증은 미완료다. 부분 환불 해석도 임의 보완하지 않는다.

운영 goods 후보 proxy는 `d=(list-price)/list`, 정책 prior `q`(A=.03/B=.15/C=.35)에 대해 보증>=6h이면 `(1-q)d`, 그 외 `(1-q)d-q`이다. 최종 receipt는 해당 SKU 정산 표본5개 미만이면 ExpectedSaved>0, **5개 이상이면 보증 유무와 관계없이 >0.25**여야 한다(S `market_buyer.py:1454–1460`). 상수 이름의 UNCOVERED만 보고 무보증 전용 조건으로 해석하지 않는다. 표본5개 이상인 mature 가격 cap은 goods 정가의 **67%**, food **66%** 이하이며 예산·중앙가 등과의 최솟값으로 더 낮아질 수 있다(499–520,840–857행). 이 비율이 고정 구매가라는 뜻은 아니다. q는 실측 고장률이 아니며 순위용 조건부 중앙가는 표본 부족 시 보수 fallback을 쓴다. [step2 개발 당시 기록](history/20261109-buyer-saved-strategy-step2/IMPLEMENTATION.md)보다 현재 운영 소스의 최종 gate를 우선한다.

seller의 C 우선 pilot은 **3SKU**(`g021aae64`, `g0be9ed07`, `g81ade83e`)에서 무제약 문의이며 C 재고가 있을 때 C cohort만 선택한다. 모든 품목·모든 요청의 C-only 판매가 아니다. 명시 등급·보증 조건은 우선하며 A72h/B6h/C0h 고정 조건을 유지한다(S `seller.py:203,445–475,570`).

restock의 수급창별 `reserved`/`planned_skus`는 중복 매입·예산 억제 기록이지 seller의 협상 예약이 아니다. hot/popular/observe 수요 분류(예: 문의>=2이면 popular, >=4이면 hot 및 최소 깊이3), 성장기 cash allocation, 종료 전 축소를 함께 사용한다. 공개시장 비율 fallback cache **30분** 및 kit catalog 최초 조회 재사용은 존재하지만, 로컬 추가 catalog snapshot cache와는 다르다.

**현재 운영 제약:** hub lock은 기본 8초 실패 후에도 진행하는 fail-open이다(S `store.py:112–142`). seller 예약은 휘발성이며 SDK/ACP 메모리 상태의 장기 보존도 보장하지 않는다. journal의 unknown과 상한도 남는다. 로컬의 fail-closed, persistent 예약, committed 5분, catalog cache 및 `_cash_*` 모듈 분리는 **미배포**다. 로컬 catalog cache의 재사용 계약은 `mtime_ns + size`이며 “같은 1초 동안 재사용”하는 TTL 계약이 아니다.

## 2. 구조의 독창성 (30%)

[architecture.tex](architecture.tex)는 협상과 결제를 분리한다. **시장 주문 → buyer kit → 우리 buyer ↔ 상대 seller A2A**, 그 뒤 **buyer의 `{shop, receipt, accept, ...}` → buyer kit ACP/AP2 client → 상대 shop ACP server → 시장 `/api/settle`**이다. buyer kit가 시장에 직접 정산 요청하는 것으로 그리지 않는다(K:360–365,493–537). 컨텍스트는 kit의 `.market` 스냅샷 외에 **restock → store의 공개 원장 GET → hub 반영** 경로가 있다(S `restock.py:161–188`, `store.py:401–415,450–468`). hub는 현행 seller/cash policy와 buyer/restock에, `memory.json`은 buyer에 직접 입력된다. seller_history legacy 경로는 현행 feed와 분리했다. 우리 seller의 A2A/영수증 발행은 별도 역할이며 그림은 자기 가게 거래를 뜻하지 않는다.

키트 기본 결제 경로 위에 조건부 가격/위험 정책, bounded shared hub, 수급·후기·게시 feedback, 별도 append journal을 연결한 점이 추가 구조다. working memory와 감사 journal, 수급 예산 예약과 협상 unit 예약을 분리한다. 운영 cash policy는 **588행 단일 파일**이며 로컬 refactor를 독립 운영 노드로 그리지 않았다.

그림은 **AI 작성 초안**이다. 과제의 “직접 그린” 요구 충족을 주장하지 않는다. 학생이 실제 화살표와 경계를 확인·수정하고 설명해야 한다. 메인의 `Get-Command` 확인 결과 `pdflatex`, `xelatex`, `lualatex`, `tectonic` 모두 **현재 PATH에서 발견되지 않았다**(2026-11-13 handoff). 시스템 전체 미설치를 뜻하지 않는다. 설치·환경 변경 없이 **PDF 미빌드, architecture.pdf 미산출**, 1페이지·폰트·텍스트·렌더 검증 미실행 상태다.

### 실패·정정 과정과 인과 해석

- 기존 보고서의 300초 운영 TTL, “100% 정산”, 구버전 가격전략을 현재 사실로 재사용하지 않았다. 원문과 Git 역사는 보존 대상이다.
- step2의 허브 크기 검증 실패와 visibility 실패는 [동일 구현 기록](history/20261109-buyer-saved-strategy-step2/IMPLEMENTATION.md)에 남겼다. 크기 제한을 높이거나 오래된 실패 로그를 고쳐 성공처럼 만들지 않는다.
- [11월 9일 배포 기록](history/20261109-buyer-saved-strategy-deploy-01/DEPLOYMENT.md)(93–116행)은 최초 관측 일부가 교체 전이라는 사실을 정정하고 별도 post 관측을 남겼다. 자연 journal append는 collector 건강 근거이지 새 정책 선택·실거래 개선의 증명이 아니다.
- Saved와 normalized surplus를 동일시한 해석은 step1에서 분리했다. 공식 합계는 coverage 미확인 때문에 `official_saved=null`, `complete=false`를 유지한다.
- 오프라인 회귀 통과 기록이 있더라도 순위·수익 상승의 인과효과는 **미검증**이다. 이번에는 테스트를 재실행하지 않았다.

## 3. Round 3 결과 (30%)

출처: **https://market.ourolabs.ai/api/board**, 기존 수집의 **2026-11-13 01:23:22 KST**(11월 12일 16:23:22 UTC), HTTP 200, round/current=3. 아래는 M 저장본을 인용하며 이번에 재조회하지 않았다.

| 지표 | 관측값 |
|---|---:|
| shop 누적 정산 | 17,732 |
| buyer 누적 정산 | 17,954 |
| shop ALL / buyer ALL | 0.775 / 0.657 |
| Players total | 1.432 |
| 학생 Players 순위 | 17 / 46 |
| student_total / student_pct | 1.323 / 0.644 (64.4%) |

17/46은 학생 `student_total` 내림차순 경쟁순위다. 전체 Players total과 학생 지표를 섞지 않는다. **shop·buyer 각각20건 조건은 이 관측에서 충족**하지만 종료시각 최종 순위·점수도, 제출 완료 증거도 아니다.

### 정산·거부 로그 인용의 범위

E/M의 VM `market-kit.log.1`과 `market-kit.log` **각 끝4 MiB**(두 tail 사이 공백 있음)에서 seller settled 3줄, buyer paid 6줄, checkout refused 2줄을 집계했다. 누적 board 값이 아니며 seller+buyer를 고유 거래9건으로 합하지 않는다.

같은 원문 줄의 안전한 부분 문자열 인용([추출본](history/20261113-submission-alignment-01/logs/settlement-substrings-excerpt.txt)); 시계에 날짜/시간대 자체가 없어 KST로 임의 변환하지 않는다. `…`는 원문 생략이다.

- `.log.1` 15:43:24: `[acp] settled` … `ledger #5703603`
- `.log` 16:23:08: `[acp] paid` … `ledger #5714330`

거부 관련 문구(부분 범위, 비배타적 포함 줄 수): `out of stock` 51, `timeout` 43, `declined-price-policy` 31, `nonpositive expected Saved` 31, `not paying:` 1. 두 price-policy 문구는 겹칠 수 있다. broad timeout/stock 문구는 고유 실패 주문 수나 시장 정산 거부 코드가 아니다. **거부 사유의 전체 원문 줄 인용은 아직 확보하지 못했다.** `settle-refused` 0줄은 전체 운영 오류0을 뜻하지 않는다.

## 제출 미완료 사항

1. 기존 로컬 `logs/market-kit.log`는 11월 5일 파일이며 최신 VM 전체 원본과 다르다. 위 excerpt는 필수 원본 로그를 대체하지 않는다. 별도 승인 아래 최신 원본 확보·민감정보 검토가 필요하다.
2. [runtime manifest](history/20261113-submission-alignment-01/runtime-manifest.json)와 allowlist source mirror는 재현 증거다. **최종 제출 코드의 VM 동일성은 미완료**: 정규화 기준9개 동일/6개 차이, VM에 없는 로컬 파일7개([차이 목록](history/20261113-submission-alignment-01/pending-differences.md)). 다음 승인 없이 로컬 전체를 VM에 덮어쓰거나 재배포하지 않는다.
3. 메인 확인에서 LaTeX 엔진4종은 현재 PATH에 없었다. PDF 미빌드이며 기존 설치 엔진의 허용된 경로가 확인될 경우에만 빌드·1페이지/폰트/텍스트/렌더 검증을 진행한다. 학생 검토, 최종 시점 지표/거부 원문 및 PR 개설 확인도 남았다. [제출 체크리스트](SUBMISSION_CHECKLIST.md)를 따른다. 이번 checkout에는 week08 README/checker가 없어 week07 checker로 대체하지 않았다.
