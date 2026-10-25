# Week 07 — Ssadassa Shop

현재 시장 등록 ID는 shop·buyer 모두 `myeonggi`, GitHub 소유자는 `SUNGMYEONGGI`임.
공개 Agent Card의 이름은 `Ssadassa Shop`임. 기존 이름과 두 서명 키를 유지함.

## 현재 거래 정책

- 기본 상품 한 개의 공개 견적: lamp 100, chair 180, bookshelf 260 credits.
- 유효 견적을 충족하는 실제 제안은 같은 task에서 바로 수락함. 검증된 두 번째 실제 제안부터는 유효 하한 이상 제안을 수락함.
- 상품·수량 1·총액·task·적용 가능한 quote 연결을 유지함. 모호한 숫자, 예산 설명과 경쟁 상점 가격은 실제 제안 횟수에 넣지 않음.
- 영수증의 `{shop,item,price,task}`에 기존 Ed25519 키로 서명하고 로컬 검증함. 시장의 주문·자금·등록 키·양측 서명 검사는 공식 키트와 Market이 담당함.
- 구매자는 정상 구매를 추가 할인이나 이벤트 탐색 때문에 지연하지 않음. 현재 견적을 다시 확인하고 같은 task에서 협상함.
- 개인 기억은 방문·영수증 일치·Market 검증 완료를 구분함. 검증 완료는 키트의 정상 서명 결과와 `verified True`가 함께 있어야 증가함.
- 상점별·상품별 검증 결과, 실제 제안 횟수, HTTP 응답 시간, 방문 시각과 선택 이유를 기록함. 과거 가격은 현재 가격으로 가정하지 않음.
- 누적 거래/방문 비율을 실측 전환율이나 가격 효과로 해석하지 않음. 앞으로 가격을 바꾸려면 최근의 비교 가능한 상품·수량·예산 자료를 확인해야 함.

순위별 가격 전환, REVIEW10 판촉, 주기적 구매 탐색, 비활성 리뷰 차단 감시기,
Week 06 다중 상점 도전 실행기와 합성 정책 승격 코드는 제거함.
사용자가 설정한 식별된 구매자의 접근 제한은 `data/buyer-access.json`에 유지함.
시장 전송에 구매자 인증 신원이 없으면 익명 구매를 허용하며 동일인이라고 추측하지 않음.
카탈로그·예산 원장·추론 슬롯·취소 처리·테스트·공식 제출 파일과 과거 로그는 유지함.
기존 시도는 Git 이력과 `logs/`에 보존되며 로그를 정리하거나 삭제하지 않음.

## 실행 파일

- `seller.py`, `buyer.py`, `market_buyer.py`: 판매, 단일 구매, 리뷰·기억을 사용하는 시장 구매.
- `harness.py`: 실제 Codex CLI 판단. `gpt-6-luna`, 추론 강도 `medium` 유지.
- `pricing.py`, `shopfront.py`, `catalog.py`: 승인된 가격, 공개 설명과 상품 검색.
- `evidence.py`, `receipts.py`, `protocol.py`: 예산·계약·영수증·A2A 1.0 검사.
- `market_memory.py`: `.market/buyer-memory.json`의 원자적 기록과 키트 검증 결과 반영.
- `inference_pool.py`, `inference_queue.py`, `tracing.py`: 실행 자원 제한과 원문 기록.
- `shop_service.py`, `join_market.sh`: 기존 배포 관리와 최초 참가.

## 검증

저장소 루트의 가상환경에서 실행함.

```bash
export PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH"
python -m unittest discover -s submissions/26510358/week-07/tests
python scripts/market_test_week07.py submissions/26510358/week-07
python scripts/check_week07.py submissions/26510358/week-07
```

모델 판단에는 기존 Codex CLI 인증을 사용함. 개인키·소유권 코드·`.market/`는 커밋하지 않음.
검사에서 사용하는 모의 결정과 실제 모델 실행 기록을 구분함.

기본 harness는 PATH의 `codex` 링크를 실제 실행 파일 경로로 해석함. 링크가 잠시
사라져도 같은 프로세스가 확인한 실행 파일이 남아 있으면 계속 사용함. 새 설치가
조회되면 새 경로를 사용하며, 실제 실행 파일까지 삭제된 경우에는 복구를 보장하지 않음.
명시적인 `A2A_HARNESS_COMMAND` 설정은 그대로 사용함. 실행 파일이 없으면 모델을
호출하지 않은 설정 오류로 기록함.

## 현재 배포

현재 Linux 머신의 `shop_service.py`와 cloudflared가 공개 URL을 유지함.
공식 키트는 `--deployed`로 그 URL에 연결해 시장 주문을 계속 처리함.

```bash
.venv/bin/python submissions/26510358/week-07/shop_service.py status
.venv/bin/python submissions/26510358/week-07/shop_service.py reload
```

reload는 새 판매자의 준비를 확인한 뒤 신규 요청을 전환하며 기존 task는 원래 판매자가 처리함.
공개 주소·현재 generation은 위 status와 `.market/session.json`에서 확인함.
상점 등록 정보 갱신에는 기존 소유권·이름·키를 사용함. 중복 키트는 실행하지 않음.
이 배포는 현재 머신·프로세스·터널이 살아 있어야 유지됨.

buyer 운영에는 `market_runtime.py join`을 사용함. 공식 키트를 그대로 호출하되
`POST /api/orders`의 연결 오류·시간 초과만 1초부터 최대 30초 간격으로 재시도함.
거래 결과 제출이나 상점 요청은 이 실행기가 재전송하지 않음.
기존 GitHub claim code는 `.market/owner.env`의 `MARKET_OWNER=...`에 저장함.
파일이 비어 있으면 등록을 시도하지 않고 15초 간격으로 코드 입력을 기다림.
코드는 로그나 buyer·모델 하위 프로세스의 환경변수에 넣지 않음.

현재 머신에서는 systemd 사용자 서비스 `week07-myeonggi-buyer.service`가 이 실행기를
관리하며, 종료 시 15초 뒤 재시작함. 아래 명령으로 상태와 중단을 관리함.

```bash
systemctl --user status week07-myeonggi-buyer.service
systemctl --user stop week07-myeonggi-buyer.service
```

서비스가 실행 중이어도 `waiting_for_owner`이면 buyer가 시장에 접속한 상태는 아님.
이 임시 서비스는 호스트 재부팅 뒤 자동 복원되지 않음.

새 참가가 필요하고 실행 중인 키트가 없을 때만 기존 `MARKET_OWNER` 환경변수를 사용해
`bash submissions/26510358/week-07/join_market.sh`를 실행함.
이 명령은 새 quick tunnel을 만들 수 있으므로 정상 배포의 코드 갱신에는 reload를 사용함.


## 거래 완료를 돕는 세 가지 운영 전략

1. `trade_funnel.py`가 수집한 `trade_stage` JSONL을 task별로 묶어 견적·제안·수락·영수증·Market 검증의 관측 단계를 보여 줌. 같은 사건을 다시 읽어도 task 수를 중복 집계하지 않음. 견적만 남은 주문은 관측 종료 시점에 미완료로 남아 있는 것이며, 구매자의 이탈이나 실패로 단정하지 않음.
2. 구매자의 후속 제안 응답이 전송 오류로 사라지거나 작업이 WORKING/SUBMITTED이면 기존 task의 `GetTask`만 최대 3회 조회함. 조회 창은 2초, 각 요청은 최대 0.6초이며 기존 협상 마감도 적용함. 헤더·본문을 조금씩 보내는 응답도 전체 요청 시간 제한으로 중단함. 금액 제안은 재전송하지 않으며 조회 실패·불명확한 스냅샷·영수증 충돌은 예산을 보류한 미해결 상태로 남김.
3. 판매자는 열린 task와 실제 금액 제안으로 바로 수락할 수 있는 요청을 판별해 완료 우선 대기열에 넣음. 실행 중 추론은 중단하지 않음. 완료 요청 최대 2회 연속 처리 후 일반 요청에 기회를 주고, 오래 기다린 일반 요청도 우대함. 일반 요청이 오래 쌓여도 완료 요청을 계속 밀어내지 못하도록 번갈아 처리 기회를 보장함. 이는 입장 순서이며 응답 시간 보장은 아님. 기본 seller 2개·buyer 1개 슬롯과 공급자 호출 제한을 유지함.

단계 기록은 실제 런타임 검증을 통과한 사건만 반영함. `offer_sent`는 전송 시도이며 판매자의 수신 확인은 아님.
서명이 발행됐거나 영수증 계약이 일치해도 Market 검증 완료로 세지 않음.
Market 결과는 실제 협상 task와 받은 영수증의 task 연결이 일치해야 같은 거래에 연결함.
계약이 맞지 않는 영수증은 원문 로그와 기억에 보존하고, 공식 키트에 거래 결과로 제출하지 않음.
분석기는 명시적으로 고른 JSONL 파일만 스트리밍으로 읽으며 전체 Market 노출이나 누락된 단계를 추정하지 않음.
Market 검증 피드백은 다음 주문에서 가져올 수 있으므로 가져온 시각으로 정산 소요 시간을 계산하지 않음.

`trade_funnel.py JSONL_PATH [JSONL_PATH ...] --capture-completeness partial --output NEW_REPORT_PATH`로 분석함.
입력은 원하는 관측 기간의 실제 JSONL 경로로, 출력은 `submissions/26510358/week-07/logs/` 아래 새 파일 경로로 지정함.
기존 출력 파일은 덮어쓰지 않음. 분석 보고서는 모델 입력이나 가격 변경에 자동으로 연결되지 않음.
