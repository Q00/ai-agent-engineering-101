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

새 참가가 필요하고 실행 중인 키트가 없을 때만 기존 `MARKET_OWNER` 환경변수를 사용해
`bash submissions/26510358/week-07/join_market.sh`를 실행함.
이 명령은 새 quick tunnel을 만들 수 있으므로 정상 배포의 코드 갱신에는 reload를 사용함.
