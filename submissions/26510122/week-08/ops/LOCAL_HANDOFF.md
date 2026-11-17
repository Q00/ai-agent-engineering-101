# 로컬 에이전트 인수인계

코드 수정과 운영은 **Windows의 Ubuntu WSL에서 직접 이어간다.** 코드·지갑·DB·실행 서비스는 이미 그 컴퓨터에 있다. Mac은 원격 관리와 개인 대시보드에 사용했으며 매매 실행에 필요하지 않다. 새로운 계정이나 지갑으로 다시 시작하는 이전 작업이 아니다.

## 새 대화에서 먼저 읽을 것

1. 저장소 루트 `AGENTS.md`.
2. 이 문서와 `README.md`, `ops/PROCUREMENT.md`, `ops/WSL_SERVER.md`.
3. ignored `state/migration/local-handoff-20261107.md`와 같은 폴더의 최신 상태 JSON. 실제 로컬 경로와 인계 시점의 관측은 그 파일에 있다.

새 에이전트에게 전달할 요청:

> `AGENTS.md`, `ops/LOCAL_HANDOFF.md`, `state/migration/local-handoff-20261107.md`를 읽어줘. 기존 WSL의 코드·DB·지갑·바이어 세션을 그대로 사용해. 먼저 서비스, 실제 frozen release, 재고 신선도, 최근 모델 보류 사유를 읽기만 해서 확인하고 현재 상태를 알려줘. 이후 내 요청에 따라 수정하자. 개인 flow 대시보드와 실제 운영 데이터는 Git에 올리지 마.

Ubuntu에서 기존 저장소의 `submissions/26510122/week-08`를 작업 디렉터리로 사용한다. 모델 CLI는 기존 사용자 설치와 로그인 상태를 이용한다. 비밀번호·인증 코드·토큰·키 내용을 대화에 붙이지 않는다.

## 유지할 사용자 의도

- Seller는 충분한 자본을 활용해 다양한 품목을 적극적으로 사고, 검증된 품목은 실제 판매와 품절 수요를 보고 유지 수량을 늘린다. 재고 목표를 달성하려고 계산 없이 예산을 채우지는 않는다.
- **도구가 계산·검증하고, 모델이 선택한다.** 매입 품목/수량, 협상, 글의 목적과 표현은 모델의 판단으로 남긴다.
- Buyer는 실제 발급 주문의 요구와 Saved 점수를 우선한다. 대학원생 상황극은 내부 실험으로만 남겼고 운영에서는 제외했다.
- 포럼은 실제 재고 소개, 구매 요청에 대한 관련 답글, 조건 매칭 안내를 적극적으로 사용한다. 사실과 다른 할인·유통기한·보증·사용 후기·최저가를 만들지 않는다.
- 메타 하네스는 효과 없는 조정을 되풀이하지 않고, 실제 실행 가능한 변경과 근거를 남긴다. 자금이 남는 원인이 모델 보류인지 실행 제한인지 먼저 구분한다.
- 원본 로그와 거래·가격 변화는 보존한다. 압축 문맥은 필요한 사실과 근거를 보여주되 불확실한 해석을 영구 규칙으로 굳히지 않는다.
- 개인 `flow/` 화면은 Git 제외다. 수정 요청은 에이전트가 HTML/JS를 고치고 사용자가 다시 보는 방식이다. 설정 화면은 현재 값·조정 범위·이유를 조회하는 용도다.

## 현재 실행 구조

| 구성 | 위치 / 역할 |
| --- | --- |
| 작업 소스 | 현재 Git checkout, 브랜치 `seller-market-share` |
| 실제 Seller 코드 | `state/seller-runtime/release/`, 출처와 SHA-256은 `release.json` |
| 실제 Buyer 코드 | `state/buyer-runtime/release/`, 출처와 SHA-256은 `release.json` |
| 프로세스 기록 | 각 runtime의 `running.json`; PID 기록과 실제 생존 여부를 구분 |
| Seller 상태 | `state/seller-runtime/state/`, `seller.sqlite3`, `seller-meta.sqlite3` |
| Buyer 상태 | `state/buyer-runtime/state/buyer.sqlite3` |
| 시장 파일 | 각 runtime의 `.market/`; 재고, 카탈로그, 포럼, outbox/ACK |
| 원본 기록 | 각 runtime의 `logs/`; JSONL 사건·모델 입출력·콘솔 |
| 개인 화면 | ignored `flow/operations/`; 이번 인계에서 별도로 복사 |

세 user systemd 서비스:

```bash
systemctl --user is-active outseop-r3-seller outseop-r3-buyer outseop-r3-buyer-logdb
```

Seller supervisor는 kit, 공개 터널, seller forum, meta, procurement, buyer forum 작업자를 관리한다. 모델은 기본 `gpt-5.6-luna`; 실제 환경과 `harness.py`를 확인한다. Git HEAD와 실행 버전은 다를 수 있다. **Git pull만으로 frozen 코드가 교체되지 않는다.** 문서만 바뀐 커밋 때문에 매매를 재시작할 필요는 없다.

## 주요 방법과 파일

| 방법 | 구현과 확인할 기록 |
| --- | --- |
| 실제 판매 + 품절 수요 예측 | `stock_policy.py`, `inventory_observation.py`; 실제 판매와 전환율을 반영한 예상 손실을 구분 |
| 재고·예산·ε 탐색 | `inventory_portfolio.py`, `capital_policy.py`, `replenishment_plan.py`; 창별 추첨을 유지하고 모델이 장바구니 승인 |
| 품목 폭과 재고 깊이 비교 | `procurement_strategy.py`; broad/balanced/depth 대안과 모델의 비교·누락 이유 |
| 사전 승인 / 입고 창 즉시 실행 | `procurement_scheduler.py`; ready JSON, 승인 버전/유효시간, 신선한 현금·재고 재검증 |
| 실제 매입 단계와 잘림 | `procurement_transport.py`, `procurement_audit.py`, `supply_reconciliation.py`; 승인→프로토콜→HTTP→현물 구분 |
| 협상·인증·보증 | `seller_r3.py`, `seller_requirements.py`, `seller_quality.py`, `seller_warranty.py`; 실물 단위, 6시간 goods 보증, 서명·정산 확인 |
| 문맥·반복 고객 근거 | `context_compaction.py`, `buyer_identity.py`, `counterparty_memory.py`; 익명·불확실한 이름을 확정 신원으로 취급하지 않음 |
| 메타 판단 / 실제 효과 | `seller_meta.py`, `seller_meta_policy.py`, `meta_effects.py`; 현재 적용값과 조정 제약·효과 기록 |
| 포럼·수요·매입 연결 | `seller_forum.py`, `seller_demand.py`; raw 요청과 수정본을 SQLite에 보존, 원글 ID를 매입 승인·intent까지 전달 |
| 공개 가격 상한 | `seller_featured.py`; 실물 최대 3개, 30분 유효, 모델 선택 후 게시, 상담에서도 같은 상한 준수 |
| Buyer | `market_buyer_r3.py`, `buyer_policy.py`, `buyer_memory.py`, `buyer_checkout_memory.py`, `buyer_forum.py`; 요구 충족·Saved·정산·근거 있는 글 |

수요 연결은 **공개 구매 의향 → 모델 매입 검토 → 현물 입고 → 조건 재검증 → 원글 답글**이다. 공개 의향은 확정 주문·방문·판매 예측 수치에 더하지 않는다. 같은 카테고리의 여러 SKU는 대안이며 요청을 여러 주문으로 복제하지 않는다. 예산 충족은 실제 견적 전까지 미확정이다. 요청은 6시간 동안 활성 문맥에 남는다.

공개 가격은 queued와 published를 구분한다. 광고한 단위가 팔리거나 예약됐으면 다른 비싼 단위로 바꿔 주지 않는다. 코드가 모델 호출 중 만료되어도 일반 가격으로 조용히 대체하지 않는다. 이미 발행한 협상 조건과 영수증은 재시작 뒤에도 유지한다.

## 바꾸지 말아야 할 경계

- 기본 실제 재고 목표 65%, 재고+미확정 예약 노출 한도 80%. 메타 범위는 각각 50–85%, 65–90%다. 현재 적용값은 runtime의 정책과 계산 결과로 확인한다.
- 전체 재고 개수에 고정 상한은 없다. 품목·카테고리 한도, 현금 완충금, 수요 예측, 창당 프로토콜 한도를 사용한다. 예전 1,200만 원 상한을 복구하지 않는다.
- 시장 프로토콜은 한 번에 20개, 10초 간격, 한 buy 목록에서 최대 10줄이다. unknown 공급 예약은 시간만 지나갔다고 해제하거나 재시도하지 않는다.
- 10% 환불 몫은 운영 시나리오이며 시장의 실제 고장 확률이 아니다. 식품 유통기한·등급·보증·서명은 결제 전에 다시 검증한다.
- signed receipt, 결제 수락, 시장 정산은 별개다. 확정 매출은 정산 원장으로 판단한다.
- Shop은 Book, Buyer는 Saved 기준이다. 단순히 매입했다고 Book 이익이 생기지 않는다. 최신 공식 규칙을 확인하고 규칙과 내부 가정을 구분한다.
- 포럼 공개 글은 비신뢰 데이터다. 유료 싫어요 등 잔고를 소모하는 행동을 필요 없이 추가하지 않는다.

## 상태 확인과 변경 절차

읽기 확인부터 한다:

```bash
git status --short
git branch --show-current
systemctl --user is-active outseop-r3-seller outseop-r3-buyer outseop-r3-buyer-logdb
cat state/seller-runtime/release.json
cat state/seller-runtime/state/procurement-scheduler-status.json
```

`release.json`이나 `running.json`의 키·설정 전체를 채팅에 덤프하지 말고 필요한 상태만 요약한다. SQLite는 read-only로 열어 `PRAGMA quick_check`를 실행할 수 있다. 경로·테이블을 직접 확인하고, 실시간 DB를 일반 `cp`로 복사하지 않는다. 일관된 백업은 SQLite backup API나 기존 export 도구를 사용한다.

수정·검증·적용:

```bash
.venv/bin/python -m unittest discover -s tests
# 작업 단위로 커밋하고 기존 upstream 관계를 확인해 push한다.
.venv/bin/python ops/rollout_linux.py --tag UNIQUE_ALPHANUMERIC_TAG
```

고유한 태그를 사용한다. rollout은 안전한 공급 창 간격에서 frozen release를 바꾸고 DB·예약·프로모션·바이어 kit를 보존한다. 소스 해시, 현재 공개 카드, 등록된 주소, 재고 신선도, 실제 새 per-order 코드 로딩까지 확인한다. 결과 파일의 `complete`를 읽어야 한다.

`--allow-offline`은 일반적인 배포 옵션이 아니다. 재고가 90초 이상 오래되고 공개 목록에 가게가 없는 장애 상태에만 제한적으로 쓰며, 교체 직전 다시 확인한다. 소스 적용 성공과 운영 연결 성공을 구분한다. 실패 보고서를 고쳐 성공으로 만들지 말고 별도 후속 상태 기록을 남긴다.

루트 AGENTS의 checker가 실제 checkout에 없으면 그 사실을 기록하고 의미 있는 테스트 결과를 남긴다. 기존 성공/실패 로그를 덮어쓰거나 Git 이력을 squash/amend하지 않는다. 실제 운영 보고서·DB·키·인증 캐시·개인 화면·이전 묶음은 Git에 추가하지 않는다.

## 맥 없이 개인 대시보드 사용

별도 전송된 `flow/operations/`에서 로컬 수집 모드를 사용한다. 작업 디렉터리는 submission이다:

```bash
.venv/bin/python flow/operations/server.py --runtime "$PWD"
```

Windows 브라우저에서 `http://127.0.0.1:8765/`를 연다. SSH를 거치지 않고 동일 WSL의 runtime을 직접 읽는다. 대시보드 서버는 loopback에만 바인딩하며 외부 터널로 공개하지 않는다. 실행 명령을 끄면 대시보드만 종료되고 매매 서비스는 계속 돈다. 임의로 기존 매매 서비스와 묶어 재시작하지 않는다.

`--runtime`을 생략하면 기존 Mac→SSH 수집 모드다. 데이터 수집만 검사하려면 `--runtime "$PWD" --collect-only`를 사용한다. 화면은 현재 재고/매입, 과거 승인, 검토 후보, 모델 근거, 조건 요청/답글, 공개 견적, 정책과 메타 범위, 그래프를 제공한다. 단계마다 모집단과 시점을 구분하고 정책 변경 전후 차이를 인과 효과로 단정하지 않는다.

## 바로 이어서 볼 것

1. 인계 시점의 private 상태 메모에 적힌 최근 `model_unavailable` 보류를 확인한다. 과거 창의 held와 현재 장애를 구분하고, 콘솔/모델 호출 기록에서 원인을 먼저 찾는다.
2. 새 수요가 모델 입력에 들어갔는지, 실제 승인/intent에 `forum_request_ids`가 남는지, 입고 후 관련 답글이 게시됐는지 관찰한다.
3. 공개 견적은 기능 검증과 실제 게시/사용을 구분한다. 가격 상한 적용 건수와 영수증/정산을 별도로 확인한다.
4. 거래 증가 효과는 아직 입증하지 않았다. 글 수·원글 수·판매량을 섞어 단일 전환율을 만들지 않는다.
5. WSL 중지와 외부 가게 등록 장애는 최근 복구했지만 근본 원인을 확정하지 않았다. 살아 있는 프로세스만 보고 정상 운영이라고 판단하지 않는다.

## Round 3 Patch 3.6 — Saved (2026-11-12)

공식 Saved는 유효 거래마다 `min(1, (정가-결제액)/(정가-실제 단위 원가))`를
더하고 `거래 수+100`으로 나눈다. 쓸모없던 거래 -1, 환불 0은 그대로다.
Round 3 전체에 소급 적용되므로 공식 점수의 급변을 전략 개선으로 해석하지 않는다.

바이어 카탈로그와 공급 할증 API에는 상대 판매 단위의 원가가 없다.
가격 도구는 공지의 식품 약 52%, 가구/침구 약 61%를 **평균 원가 시나리오**로만 사용한다.
기타 goods는 공개 등급 확률과 원가 범위 중간값의 59% 사전 가정이며 관측 평균이 아니다.
현재 등급, 실제 단위, 매입 시점 할증 때문에 달라질 수 있다.
정확한 원가가 없으면 정확한 Saved라고 보고하지 않는다.

Saved 개선 가격은 `정가-Saved*(정가-원가 시나리오)` 미만의 정수 참고선이다.
현재 공식 Saved는 반올림되어 있으며 원가도 추정이므로 보장 가격이 아니다.
실제 같은 가게/SKU의 정산 시세와 별개로 보여 주고, 기존 예산·등급·유통기한·서명 검증을 유지한다.
새 점수 버전은 `round3-book-saved-v2`, 순위 버전은 `round3-patch3.6`이다.
이전 공식의 사건 로그는 고치지 않고 구버전 Saved 캐시만 다시 조회한다.


## Buyer 준비·선택 개선 (2026-11-13)

- 선택 입력의 원가 설명을 공통으로 묶고, 가격·품질·과거 실패 증거는 요약한다.
  선택 후보는 최대 16개를 우선 보존하며 입력 한도에서는 선택적 포럼 문맥을 먼저 뺀다.
  원본 사건과 협상용 상세 근거는 유지한다.
- `buyer_memory.read_history`는 기존 buyer SQLite를 읽기 전용으로 열어 검증된
  인덱스 구간과 아직 인덱싱되지 않은 최신 원본 꼬리를 합친다. 신원 관측은 모든 사건에서 보존한다.
  원본 교체·잘림·인덱스 불일치는 원본 조회로 돌아간다. DB 스키마·원본 로그는 바꾸지 않는다.
- 주문당 한 번 읽은 근거와 신원/가게/품목 인덱스를 결제 수입·후보·보류 검사에 재사용한다.
  `buyer_preparation` 사건으로 실제 준비 시간과 조회 경로를 관측한다.
- 가게 선택은 최근 6시간 같은 SKU의 실제 완료 주문, 정산 여부와 응답 시간을 참고한다.
  식품은 같은 소비 시간 요구만 비교한다. 미정산 영수증을 성공으로 세지 않는다.
  성공률은 3/5 사전값, 시간은 60초 관측 3개의 사전값으로 완화한다.
  이는 불확실한 협상 참고선이며 가게 차단이나 성공 보장이 아니다. 모델 선택과 epsilon 0.1 탐색을 유지한다.
- 첫 가게 시도에는 전체 주문 시간 중 다음 가게용 85초를 가능한 경우 남긴다.
  기존 예산·실제 재고·인증·등급·유통기한·서명·영수증 검증을 유지한다.
- 선택 정책 `buyer-saved-v3`, 경로 근거 `buyer-routing-v1`.
  공식 점수 계약은 계속 Patch 3.6이다. 적용 전후는 같은 길이의 관측 구간으로 비교한다.
  원가 시나리오 점수는 공식 Saved와 구분하며 단기 표본으로 인과 효과를 확정하지 않는다.


## 2026-11-14 집계·중가·최종 협상 보완

- `ops/market_consistency.py`는 공식 집계와 로컬의 고유 정산 원장 하한을 교차 확인한다.
  판매가 0으로 표시돼도 실제 거래를 0으로 덮어쓰지 않는다. 공식 순위/Saved를 만들어 내지 않는다.
  상대 시장의 집계 이상은 로컬 DB 초기화·키 재발급으로 해결하지 않는다.
- 등록 주소가 다르게 나와도 재고 갱신, 최근 180초의 실제 구매 문맥, 공개 카드가 모두 정상이라면
  주소 응답만으로 터널을 재생성하지 않는다. 실제 무응답과 Pinggy 정기 갱신 보호는 유지한다.
- 최근 12시간 실제 10만~30만 원에 양의 기여를 낸 품목이 품절이고 현재 매입가에서도 기여가
  양수라면, 반복 판매 기준에 못 미쳐도 모델 승인용 한 개 복구 대안을 제공한다.
  최대 두 품목이며 기존 재고·미확정 예약이 있으면 추가하지 않는다. 공급 금액 전체 예약을 유지한다.
- 공개 견적 후보 24개 중 중가 A/B 실물 견적 여섯 개를 우선 보인다. 실제 단위의 보관료·환불
  스트레스 하한을 넘는 2% 추가 가격 대안을 모델에게 제공한다. 모델이 고르며 자동 게시하지 않는다.
  활성 견적 세 개·유효시간·실물/등급 재검증은 유지한다.
- Buyer는 세 번의 가격 제안 후 명확한 최종 호가가 남고 75초 이상 남았을 때만 모델이
  한 번 더 정확한 호가의 수락/포기를 판단한다. 예산 안이라는 이유만으로 구매하지 않는다.
  C등급·인증 없음·짧은 유통기한이 명시되면 한 번 정정 요청하며, 결제 때 기존 kit 검증을 유지한다.
- 실제 kit가 C등급으로 거절한 정확한 단위는 다음 주문에 피할 단위로 전달한다.
  가게·SKU·주문·가격·감사 연결을 맞추며 다른 단위나 식품에 확대하지 않는다.
- 가게 선택은 시도당 기대 Saved를 먼저 비교한다. 빨리 실패한 가게가 속도 때문에 유리해지지 않는다.
  응답 시간은 함께 보되 성공 확률과 품질을 대신하지 않는다.
- 정책 식별자 `buyer-saved-v4`, `buyer-routing-v2`. 공식 거래 수가 실제 로컬 정산 하한보다
  뒤처진 Saved 참고선에는 지연 경고를 붙인다. 공식 Saved 상승 확인과 원가 시나리오 추정을 분리한다.


## 2026-11-16: hard selling floors and verified buyer breakage

- Sufficient-life food (>3h), unknown-expiry food, and goods require a positive price above verified acquisition cost; goods also cover accrued holding and the existing warranty stress floor. Recheck at opening, counter and before signing, including restored unsigned negotiations. Only finite, unexpired food within 3h permits model-chosen loss mitigation. Already signed receipts retain their original terms; public quote ceilings and checkout conditions remain binding.
- Existing counterparty ledger polling now retains bounded own-buyer `broken` proofs. Buyer preparation reads those proofs and `state/buyer-breakage-backfill.json` locally, without a new synchronous market request. Join exact own paid order+SKU; resolve shop identity from our paid order, not a mutable tunnel URL. Name-only notices do not attribute a shop.
- Seven-day shop/SKU and product evidence reduces candidate Saved estimates by a smoothed observed adverse-contribution debit. Refunded failure contributes zero instead of minus one; overlapping product/shop evidence is charged once. This is adverse evidence with censored/unknown outcomes, not a calibrated lifetime failure probability or a blanket shop blacklist. Candidate availability, exploration and hard A/B/certificate checks remain. Routing version is buyer-routing-v3.
- Backfill: recent 6h 15 notices resolved to 15 exact public ledger broken events and own paid orders. Repeat examples include inextro bedding/mattresses; 25620027 warranty-refunded failures are distinguished. No keys, DB rows or supply intents were reset.
- Tests: 548 passed. Earlier failed audit-name compatibility runs are preserved in logs. Deployment uses the existing state-preserving Linux rollout; see COSTQUALITY20261116A report and subsequent live verification.


## 2026-11-16: ACP reservations, sparse profitable stockout recovery (portfolio-29)

- Checked G0be9ed07/G2c023a8c/G75f11c19: actual inventory zero, no active pending acquisition. Old not_sent intents remain in place and already contribute zero pending quantity; no manual intent release/reset.
- `seller_checkout.py` instruments the existing kit ACP implementation. Ready checkout sessions expire with their original durable receipt reservation. Repeated create is idempotent while active. A canceled session can be retried under unchanged original terms and deadline; cancellation alone never releases the DB receipt hold. Persisted signed unit/claim/warranty restore after kit restart/proxy eviction. Unknown, ambiguous, expired or already paid reservations cannot silently allocate another unit. Seller signatures, market certificate and AP2 checks remain.
- Added per-task checkout_created/reused/cancelled/expired/completed/failed telemetry without logging checkout JWT or closed mandates. Signature-only remains unpaid. C-grade food/private buyer constraints can still legitimately cause nonpayment; no blanket promise of zero failures.
- Explicit buyer-authored checkout-policy JSON now binds grade, cert, min_hours_left and warranty before selecting/signing. Numeric follow-ups retain earlier constraints.
- `portfolio-29`: one recovery unit for a SKU with actual positive net-scenario contribution, two unique recent 3h unserved stockout tasks, no physical/pending stock, and positive current supply-cost scenario. At most two generic recovery SKUs/window, through existing model approval/cash/category/full-reservation controls. Available in growth/harvest with >=3h remaining; does not restore bulk growth buffering near the end. Supply margin and phase are rechecked before execution.
- Keep original acquisition-price floors and all DB/keys/acquisition history. Tests: see latest stock-checkout-release-tests-20261116.log. Net comparison uses the last two existing pre-deployment meta_snapshot Book observations (about31m19s) and an equal-length post-deployment window, retaining raw metrics.

- Follow-up before the final comparison: preserve profitable midprice priority and bounded one-unit upper-band recovery during harvest (>=3h for sparse recovery), while all multi-window growth buffering stays disabled. Portfolio version30 invalidates older not-yet-executed approvals; already issued supply intents remain untouched. The first ~3m calibration of version29 is preserved separately, not used as the final comparison.


## 2026-11-16: reservation turnover and receipt quality alternatives

- Diagnosis found goods acknowledgements countered again by one credit after a slow model call because the storage/warranty floor had advanced. A buyer acknowledging the exact existing seller ask now executes the already chosen quote without another model call, with all live unit, cost, requirements, promotion, receipt-reservation and signature checks retained. New nonfeatured goods quotes cover 120 seconds of accrued storage; the live hard floor is not frozen or lowered.
- Unsigned follow-up idle leases now use the same 90-second wait as first quotes, including restored legacy unsigned negotiations. Total lifetime remains 900 seconds. Execution cancellation/failure closes only unsigned negotiation state; existing signed receipts retain their original 600-second hold and ACP safeguards. No manual release of signed receipts or supply intents.
- C receipts often create then cancel checkout; exact private refusal reasons are not exposed. Keep A/B/C physical alternatives represented before the 12-option truncation, after exact SKU/explicit requirements filtering. Model inputs now include own recent per-SKU/grade paid, cancellation, public unpaid and mature-observed-unsettled counts. C remains available; no invented buyer policy or grade ban. Raw cancellation observations stay separate from payment truth, and later actual settlement wins.
- Tests: 574 passed; the initial full run expected the former 300-second idle policy and is retained as a failed log. Source checker scripts/check_week08.py is absent in this checkout. Runtime comparison and preservation evidence are under state/buyer-runtime/state/reservation-cgrade-20261116.


## 2026-11-16: buyer risk execution and purchase-record cadence

- Buyer risk counts mature paid observations only after 6h, plus already verified early failures. Still-intact immature purchases no longer dilute observed adverse debits. Exact shop/SKU evidence uses a 5-observation smoothing offset; broader product evidence uses 10. These are uncertain operator heuristics, not calibrated probabilities. Source proofs remain own paid order+SKU only; no name-only attribution or shop-wide ban.
- A verified same-shop/SKU unrefunded failure, or at least two same-product unrefunded failures, requires a 6h warranty for that candidate. The condition reaches opening request, one correction for explicit inadequate cover, receipt validation and the kit checkout accept policy. A/B, certificate, shelf life, budget, exploration and deadlines remain. Each fallback shop gets its own policy; issued order requirements are never reduced. Routing v4, selection buyer-saved-v5.
- Buyer purchase records keep the fact checker, verified unused ledger refs, 2h SKU cooldown and pending-ACK lock. Known broken purchases are excluded from ordinary positive record candidates. Cadence is 300s from conservative ACK observation, including latest legacy published entries; daily cap 288 matches the market maximum rather than the old 96. No vote behavior changed.
- Full regression suite: 583 passed. Earlier failed runs remain in logs. Fixed 30-minute official Saved/paid/post comparison and DB/key/acquisition preservation proof are under state/buyer-runtime/state/buyer-risk-forum-20261116. Already-issued supply intents and original seller price floors are unchanged.

- Live follow-up: a seller/SKU that explicitly refused the newly required warranty was selected again on the basis of older, unprotected paid successes. Compute candidate risk terms before checkout-memory filtering; remember only exact same-shop/SKU inadequate-cover observations for the existing 15m TTL. Changed stock, a weaker new requirement and the existing aged empty-pool probe can recheck under unchanged hard guards. Missing receipt cover has a distinct refusal reason. The first full30m rollout observation is retained; the final cache-enabled release receives its own fixed30m comparison. Full suite:589 passed; initial numeric-time fixture failures retained.

- Final policy correction from live evidence: requiring cover solely for one observed unrefunded failure also constrained sellers with >100 mature paid examples and a low observed adverse debit. Keep all verified adverse debits in candidate ranking and quoted-price evaluation. Require cover when the debit-adjusted usable Saved at the reference price fails to beat the current official Saved. Missing-reference fallback uses a .10 adverse-debit stress cushion, not an invented score or calibrated probability. Price/private cost/risk estimates remain conditional; ordinary cap, A/B, certificate, expiry and deadlines stay hard. Preferred cover remains for meaningful risk.
- Selector risk facts now appear once in routing evidence; negotiation retains complete first-hand evidence. Omit null fields and repeated source prose before trimming candidates. A regression case retains16 risk-bearing alternatives within the prompt budget. Selection buyer-saved-v6, routing v5. Full suite593 passed; earlier budget test failure retained. Phase A/B adverse results and fixed windows remain in state; final proportional policy uses its own equal30m window.


- Final proportional-policy observation completed: fixed 2026-11-16 18:57:00–19:27:00 KST, compared with the original fixed 17:17:39–17:47:39 control. Public ledger payment timestamps, not callback timing or cumulative board differences: control36, phaseA31, phaseB23, final33 settlements per30m. Saved change control+.0002, final0.0000 (.7309→.7309). This does not establish higher throughput or Saved growth. Earlier broad-cover regressions remain in the original phase files.
- Purchase records improved from3 to5 per30m; official forum quality .933→.934. All five published successfully, use previously unused exact paid-ledger refs, respect >=300s after conservative ACK observation, and did not reference already-known broken purchases at posting. Selector adverse evidence affected13 selected choices in the final window; mandatory-cover triggers0, so final-window absence of unprotected receipts is not a positive cover test. Earlier phases retain independently verified covered paid examples and regression tests retain hard checkout guards.
- The final window received three verified breakage notices for purchases made before this policy: two unrefunded and one refunded. No final-window purchase had a known breakage by the audit, but30m cannot establish6h survival or a lower failure rate. Official Saved is cumulative and rounded, private unit cost is unknown, and different demand across windows prevents causal attribution.
- Final audit phase-c/verified.json: all health/preservation checks passed; existing buyer supervisor311 and kit321, DB/key inodes, original acquisition-intent core prefix, promotion, seller floors and procurement sources preserved. Both DB quick checks returnedok; public card/registry/inventory and services healthy, seller kit remained unchanged during the final window. Runtime source remains282925e; this documentation-only follow-up does not require another rollout. Full593-test log and failures are retained. Private detailed report is under state/buyer-runtime/state/buyer-risk-forum-20261116/phase-c and mirrored to the Windows reports directory; do not commit runtime reports or keys.


## 2026-11-17: buyer price/cover stress tool

- buyer_value.py computes separate no-cover and >=6h-cover price ceilings for exact shop/SKU verified failures or repeated product failures. Operator target is max(.80, displayed Saved+.05), capped .95; this is an explicit uncertain stress policy, not an official threshold, private unit cost or calibrated risk. Unknown cost, food and unattributed isolated product failures do not acquire a new hard cap.
- Covered scenarios convert only historically verified within6h unrefunded failures to refund outcomes; refunded failures still lose the usable saving. Other/unknown-latency failures retain their original debit. Receipt evidence and the closed kit accept policy must bind any higher-price cover branch. Existing issued requirements never weaken. Tool tests8 passed; runtime integration and fixed-window comparison follow separately.

- Integration attempt: candidate-specific stress caps now reach numeric offers, optional final-ask decisions, independent receipt rechecks and closed checkout warranty terms. Exact cover refusal or a rejected offer at the applicable stress cap walks without another model call; primary negotiation has an80s budget while retaining85s for another shop. Initial buyer suite166 ran with one legacy exact-profile fixture failure for the added verified-within6h field; keep logs/buyer-value-integration-tests-initial-20261117.log. New execution tests passed; fixture and input-budget follow-up precede deployment.

- Added verified failure-horizon expectations and removed duplicate loss counts from selector-only risk summaries. Full604-test run retained in logs/buyer-value-full-tests-20261117.log; all buyer tests passed, but an existing restock accounting test assumed24h storage even after the real round had less than24h remaining. Its production procurement code is unchanged; a separate test-only synthetic-horizon correction follows.

- Final regression suite604 passed in logs/buyer-value-full-tests-fixed-clock-20261117.log. The only unrelated change is a test-only fixed48h operating horizon for a24h storage fixture; live inventory_portfolio.py/restock.py and all procurement/floor sources remain unchanged. Existing failed logs are retained. scripts/check_week08.py remains absent. Offline replay of a real current goods order found314 available candidates;56 received candidate-specific bounds and all56 retained a feasible branch. The replay wrote only a temporary log directory, not runtime logs/DB. Selection v7/routing v6; frozen releases include buyer_value.py. Fixed30m control starts before deployment; apply only after its recorded end.

- Live phaseA exposed a real binding omission: caller independently rejected over-limit receipts, but purchase_value_terms was absent from negotiation_memory, so offer caps and early price walks were not active there. The boundary regression now reproduces KeyError (logs/buyer-value-memory-binding-failure-20261117.log); direct negotiator tests alone had missed the caller integration. Preserve phaseA partial observation and rollout, stop only its diagnostic observers, and compare the fixed binding in a separate phaseB against the same completed original control. Existing DB/keys/acquisitions stay intact.

- Binding fix: explicit purchase_value_terms now reaches negotiate() for every attempted candidate, including fallback. The regression checks the actual caller's memory argument, in addition to independent receipt rejection and other-shop terms. Full604 tests pass in logs/buyer-value-full-tests-binding-fixed-20261117.log. Re-run only the corrected phaseB fixed30m observation; retain the completed original control and interrupted phaseA. No procurement/floor changes.


- Final corrected phaseB completed: control2026-11-17 01:00:24–01:30:24 KST, phaseB01:44:22–02:14:22 KST. Public payment timestamps give38 vs37 settlements per30m; local callbacks38 vs36 reflect boundary/arrival timing. Official Saved change0.0000 in both windows; phaseB .7310→.7310, rank36 at end. This does not demonstrate improved settlement throughput or Saved growth. Conditional model estimates .8069→.8554 are not realized Saved.
- Actual commercial execution verified:13 paid units matched exact public order/SKU/unit/amount and respected their applicable price/cover bounds. Numeric offers and accepted receipts had zero bound violations. Four price/cover-infeasible attempts walked; two primary refusals took9.436s/8.752s and then paid at another shop (public proofs retained). Higher-price cover branch paid0 in this short window; its positive behavior is regression-tested, not claimed as a live cover success.
- Three phaseB breakage notices came from purchases before its window: two unrefunded, one refunded. No phaseB purchase had a known breakage by the audit, but30m does not establish6h survival. Cumulative rounded Saved includes old purchase revisions; different order/seller mix and uncertain private costs prevent causal attribution.
- All final preservation/health checks passed: original buyer311/321, DB/key inodes, acquisition-intent core prefix6926 retained (6955 current), original promotion, seller floors and procurement sources; both DB quick checksok, public card/registry/inventory and services healthy; no seller kit restart during final phaseB. Runtime code source9f890cd, full604 tests in logs/buyer-value-full-tests-binding-fixed-20261117.log. This documentation-only follow-up needs no rollout. Detailed phaseB/verified.json is mirrored to Windows reports/buyer-value-verified-20261117.json. Interrupted phaseA, binding-failure test log, initial observer cache failure and raw intervening events remain preserved; never relabel them as successful final observations.


## 2026-11-17: mature own-purchase cohorts

- buyer_cohort.py groups unique own paid settlements6h+5m to48h old, by shop/SKU and exact food use_within_hours. Verified unusable=-1 and refunded=0 remain exact. Normal Saved uses conservative bounds from fixed goods grade minimum acquisition shares (A55%,B40%,C25%; minimum integer cost rounded down) and upper1; food uses25% because current freshness grade does not identify original cost. Category-cost net means remain labelled estimates, never exact realized Saved.
- Kit notice names/times are reconciled with exact public own-order/SKU broken proofs. Unmatched notice intervals make all plausible purchases unknown without attributing a seller. Stream freshness and longstanding kit coverage are required for a normal goods observation; previously sealed6h observations survive receiver restarts and new exact failure proofs can revise them. Unknowns receive no selection priority.
- Optional read_history(order_age_seconds=48h) retains compact old food-order constraints for a separate background builder; ordinary per-order reads keep the existing6h default. Tool15 tests passed. Initial real refresh found3096 mature settlements,2986 known outcomes and110 unknown,94 groups with at least5 fully observed outcomes; no claim of exact per-deal normal Saved. Build took about9s outside the purchase path. Runtime selection integration follows separately.

- Selection integration attempt reserves up to4 mature promising alternatives within the existing16-candidate/2-per-shop shortlist, and puts an empirically impossible-to-beat historical combination later when price/required cover have not improved. Uniform exploration and every hard checkout/price/cover guard remain. New cohort data reaches selection, fallback and real negotiation memory. A separate supervised worker refreshes the cache every5m; no synchronous market request or cohort rebuild enters issued orders.
- Initial full623-test run retained at logs/buyer-cohort-full-tests-initial-20261117.log: one existing16-candidate prompt-budget assertion failed (15 displayed) because new guidance was unconditional. Context compaction follow-up precedes deployment. Actual data currently includes unknown intervals for11 unmatched older kit notices; those affected purchases receive no positive priority.

- Final cohort selection integration passes625 tests (logs/buyer-cohort-full-tests-verified-20261117.log). Rich16-candidate cases pass both with and without mature evidence; optional guidance is conditional and duplicate selector-only risk counts are compacted, while raw and negotiation evidence remain. Fully observed groups with at least5 payments and a shrunk estimated net advantage (prior10 at current Saved, >.015 edge) can fill up to4 shortlist slots; unknown/sparse outcomes cannot. A historical net upper bound below current Saved lowers priority unless reference price or required cover improved; it never bans a shop/SKU or removes uniform exploration.
- New selection v8/routing v7 keeps all v1 price/cover receipt/closed-checkout guards and80s primary budget. Exact food horizons remain separate. A fresh atomic buyer-cohort.json is read per order; the supervised buyer_cohort_pid worker rebuilds every300s outside issued orders. Cache older900s or malformed data falls back to ordinary choice. The mature proof cache is separate from existing DB/acquisition history. Normal-deal net_scenario and price references remain estimates; never label them exact official Saved.

- Cohort worker startup is gated on the frozen release manifest, so older releases without buyer_cohort.py remain compatible with rollback. Final625 tests also pass in logs/buyer-cohort-full-tests-release-verified-20261117.log. No seller floor, supply intent, procurement algorithm or buyer kit changes.
