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
