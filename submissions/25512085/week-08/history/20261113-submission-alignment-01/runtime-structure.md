# 보고서·그림 담당 handoff: 운영 노드와 연결

모든 행 번호는 이 폴더 `runtime-source/` 아래 **운영 mirror**를 기준으로 한다. `S = runtime-source/submissions/25512085/week-08/`, `K = runtime-source/scripts/market_kit.py`. 기존 보고서·그림은 수정하지 않았다.

## 그림용 노드 목록

| 노드 | 실제 역할 / 근거 |
|---|---|
| kit v4 + guarded adapter | `K`의 A2A proxy, ACP checkout, buyer 결제, sync/stock/outbox orchestration. `guarded_market_kit.py`는 기존 kit import 후 visibility 보호를 설치한다. kit PID 43679 시작 2026-11-05 09:40:10 UTC |
| cloudflared tunnel | 외부 HTTP를 kit proxy로 연결. PID 43696 시작 2026-11-05 09:40:12 UTC. URL은 증거에 내보내지 않음 |
| Seller / A2A AgentExecutor | `S/seller.py:939–1111`, `1161–1177`; 재고 선택, 조건별 견적, 규칙 협상, 서명 영수증. PID 974042 시작 2026-11-10 14:23:45 UTC |
| Monolithic cash policy | `S/seller_cash_policy.py` 588행, `config:191`, `quote:348`, `procurement:417`, `allocate:503`, `availability_plan:540`. 별도 `_cash_*` 노드 없음 |
| Market buyer | `S/market_buyer.py:583`, `772–885`, `983` 이후: 후보 순위·재시도·정수 제안·accept gate. PID 1166242 시작 2026-11-12 16:20:25 UTC |
| Store / hub | `S/store.py`: 공통 파일 IO·hub lock·원장 정규화·후속 journal. `load_hub:241`, `mutate_hub:308` |
| Restock | kit의 선택 cmd가 `restock.py --state {state} --visibility-guard`. `S/restock.py:332–405`에서 수급 계획 및 재고 안내를 만든다. 관측 순간 상주 PID는 없었음 |
| Review + detached forum writer | 정산 뒤 kit review hook → `review.py` → `forum_writer.py`. `review.py:42–54,98–114,150–170`; 정산 후 구매·메모리·trust 갱신, 실제 근거 기반 게시 초안 |
| Buyer outcomes journal | `S/buyer_outcomes.py:167–193`. hub lock 밖 append/flush/fsync, 허용 크기 초과 시 unknown 진단. 원문 journal은 읽거나 복사하지 않음 |
| Local state/context | `.market/` catalog/inventory/shops/forum/payments/broken 등, `state_hub.json`, `memory.json`, `seller_history.json`. 파일 내용을 조사용으로 수집하지 않았음 |
| Market service | 시장의 shop discovery/order, 실제 재고·수급, AP2 정산과 공개 board. 이번 작업은 board GET 한 번만 호출했음 |

일시적 review/restock/forum 프로세스의 부재를 기능 부재로 해석하지 않는다. venv Python로 probe 실행했으며 `/proc/exe`가 `/usr/bin/python3.10`인 것은 관측값이다. 프로세스 인자 전체·환경은 출력하지 않았다.

## 그림용 방향 연결

1. 외부 buyer → tunnel → kit A2A proxy (`K:228–312`) → seller A2A → 서명 영수증. Seller가 직접 `/api/settle`하는 구조로 그리지 않는다.
2. buyer kit → shop `/.well-known/acp.json` → create checkout → checkout 영수증/서명/금액/품질 검사 → complete checkout → shop kit `AcpShop` → market `/api/settle` (`K:314–440,484–552`). **이것은 배포 코드의 정상 경로 설명이며 이번 조사에서 POST한 것이 아니다.**
3. market order + shop list → kit → market_buyer → 상대 가게 A2A → receipt + accept → kit 검증/결제 (`K:705–765`). buyer.py는 별도 legacy 호환 경로.
4. kit sync → `.market` 스냅샷 → seller / buyer / restock. Store는 hub에 수요·가격·정산·신뢰 근거를 반영.
5. restock → monolithic cash policy / visibility guard → 수급 계획(JSON) → kit → 시장 수급. restock가 직접 거래를 완료하는 것으로 그리지 않는다.
6. paid deal → review hook → hub / memory / bounded purchase history → detached forum writer → `.market/outbox.jsonl` → kit forum 게시.
7. restock `finish` → `cash_policy.availability_plan` → outbox → kit forum. **독립 seller_forum.py 운영 노드는 없다.**
8. store `apply_ledger_rows` → in-memory sanitized event batches → hub lock 해제 → buyer_outcomes append journal (`store.py:308–331,450–468`). journal과 최근 구매 목록은 별도 저장 계층.

## Seller 선택·예약·캐시의 정확한 의미

- 상품 선호/SKU 선택 후 **동일 SKU·등급·보증 cohort 안**에서 실제 견적을 비교한다. 최저 견적과 보유 비용 기반 tie band 안에서 오래된 unit 우선 (`seller.py:321–373`). 전체 재고에서 무조건 최저가 선택이라고 쓰지 않는다.
- 식품은 명시한 사용기간과 checkout 여유를 먼저 확인하고 FEFO/grade 선호를 적용 (`seller.py:376` 이후, `1015–1024`, `1076–1109`). 사용기간 미확인 질문 경로도 존재한다.
- `self.open`과 `self.committed_units`는 **메모리 딕셔너리**. 운영에서 예약 파일 복구·durable fsync 저장은 없다 (`seller.py:939–945`).
- 협상 상태 만료: `now-created_at > 90`. 활동 시각으로 갱신되지 않는 **생성 후 TTL**이다. committed 만료: `now-signed_receipt_timestamp > 120`. 새 execute의 시작에서만 정리한다 (`958–975`). `cancel`은 open 상태를 제거한다 (`1412–1416`).
- 협상 TTL은 ACP의 `self.sessions` 저장소와 다른 층이다. kit checkout 세션에 300초 TTL이 구현됐다는 근거는 없다.
- `load_hub`, `load_seller_state`, `load_catalog`는 해당 구현상 파일을 다시 읽는다 (`store.py:241–268,334–353`, `seller.py:806–841`). **로컬 추가 catalog snapshot cache를 운영 기능으로 표시하지 않는다.**
- 실제 존재하는 별도 캐시는 restock의 **공개시장 비율 fallback cache 30분** (`restock.py:33,103–115`) 및 kit의 catalog 최초 조회 재사용 (`K:788–803`)이다. 이를 hub/history 파일 캐시와 구분한다.

## Buyer 정책과 메모리 보존

- goods cap: 정가·개인 한도·할인 정책·시장 관측값의 최소값이며, 샘플 5개부터 mature cap을 강화한다 (`market_buyer.py:499–520`). 정책 상수는 `50–60`행. 한도 값 자체는 증거에 저장하지 않았다.
- goods Expected Saved는 **등급별 고장 사전확률을 쓰는 후보 1건 proxy**, board의 집계 Saved 또는 실측 고장률과 동일하지 않다 (`523–580`). 보증 >=6h에서 고장 손실 차감을 달리하고, 알 수 없는 grade는 C로 보수 처리한다.
- goods receipt의 양의 Saved, mature 무보증 조건 등 검증 뒤 cert/grade/warranty accept를 구성하며 kit가 실제 checkout을 검증한다 (`772–801`, `1454–1463`). 모델이 가격을 결정하는 구조 아님.
- food cap과 동등 잔여수명 비교 (`840–885`), 실제 사용기간 +0.25h minimum accept, receipt expiry precheck (`772–825`). 힌트가 인증서를 대체하지 않는다.
- **가게별 최근 5개** memory + 유효 평점만 archived count/average로 축약 (`349–379`, 상수 `50`). 모든 메모리가 총 5개인 것은 아니다.
- **실제 정산 후 `my_purchases` 최근 150개** (`review.py:98–114`). 영수증만 받은 pending deal을 정산 성공으로 세지 않는다.
- hub structured comparables **SKU별 최대 50개/3h**, scalar settle price 최근 30개; buyer scalar 조회는 유효 최근15개 (`store.py:218–238,638–659`, `market_buyer.py:487–496`). 별도 shop tag는 전역120행, 조건당3개로 제한 (`store.py:56–58,175–216`).
- seller legacy 상태: trades25, recent reviews5, 품목 가격40 (`seller.py:838–879,1384–1391`). 이는 Round 3 실정산의 장기 전체 이력과 다르다.
- buyer-outcomes는 **append 전용 256 MiB cap**. 초과 시 과거 삭제·rotation 대신 `journal_capacity_unknown`으로 기록을 중단한다 (`buyer_outcomes.py:14–17,167–193`). 요약 재생도 MAX_EVENTS200,000/MAX_ORDERS20,000 cap이 있다. 크기 cap이 시간 TTL은 아니다.

## 모델 표기: 실행 여부와 잔존 코드를 분리

| 위치 | 확인한 코드 | 보고 가능 결론 |
|---|---|---|
| seller | `1161–1177`, subprocess/LLM 호출 없음 | 협상 규칙 기반; 주석의 “outside negotiation model”만으로 실제 모델 사용 주장 금지 |
| Round 3 market_buyer | `1243`의 `if not is_round3` 아래에만 `1253 call_harness` | 현재 Round 3 정책 경로는 결정론적; LLM 협상이라고 쓰지 않음 |
| legacy market_buyer | `259–285`, `288–331` | Codex cmd `-m gpt-5.6-sol`; fallback 후보 문자열 `gemini-3.5-flash-lite`, `gemini-3.8-flash`. 문자열/시도 경로만 입증, 실제 지원·호출 성공 미입증. docstring의 gemini-2.5 표기는 실행 loop와 다름 |
| buyer.py legacy | `82–121,206` | 별도 호환 코드; 현재 Round 3 cmd의 주 실행 파일과 혼동 금지 |
| forum_writer | `34 FORUM_USE_MODEL=False`, `303–321` | 템플릿 게시 경로. opt-in model helper 존재를 현재 LLM 실행으로 바꾸어 설명하지 않음 |
| 이번 증거 분석 assistant | 대화 메타데이터 GPT-6.1 Sol | 시장 운영 모델이 아님. 과거 구현 모델은 미확인 |

모델 성공 payload/호출 사용량/비용은 읽지 않았고 새 모델 호출도 하지 않았다. 모델 선택·실험 성능 비교를 수행한 작업이 아니다.

정확성 주의: 여기서 결정론적이라는 말은 **입력이 주어진 가격·accept 정책의 규칙 계산**을 뜻한다. `market_buyer.py:1472`는 no-deal 후기 템플릿을 `random.choice`로 고른다. 따라서 비LLM 구현이라고 해서 전체 실행·모든 출력 문자열까지 완전히 결정론적이라고 주장하지 않는다.
