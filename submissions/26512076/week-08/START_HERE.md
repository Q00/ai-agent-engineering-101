# Round 3 코드 적용 안내

이 묶음은 기존 week-08 폴더에 적용하는 **업데이트 파일**입니다. 전체 폴더를 지우지 마십시오. 키트나 개인키는 이 ZIP에 넣지 않았습니다. 기존 week-07을 키까지 복사한 week-08 위에 파일을 덮어쓰십시오. 7주차 제출 코드와 실행 파일은 수정하지 않습니다.

대상 경로:
`C:\Users\bhj72\ai-agent-week08-submit\submissions\26512076\week-08`

## 1. 파일 배치

압축 안의 `week-08`을 열고, 그 안의 파일들을 위 경로에 복사하십시오. 같은 이름의 `agent.json`, `.gitignore`, `REPORT.md`는 이 8주차 폴더에서만 바꿉니다. 기존 `buyer.py`는 예전 라운드용 호환 명령으로 남지만 Round 3에서는 `buyer_r3.py`가 사용됩니다.

`shop_key.pem`, `buyer_key.pem`은 원래 로컬 키를 그대로 사용합니다. 생성·교체하지 않습니다. 키는 커밋하지 마십시오. `.market/catalog.json`은 키트가 가져오는 실제 카탈로그입니다. 폴더 루트의 예전 `catalog.json`은 이 코드에서 사용하지 않습니다. 예전 `test-report.json`은 7주차 결과이며 이 코드의 통과 증거가 아닙니다.

## 2. 같은 Anaconda Prompt에서 확인

```bat
cd /d C:\Users\bhj72\ai-agent-week08-submit
python -m pip install -r submissions\26512076\week-08\requirements-r3.txt
set /p "GEMINI_API_KEY=Gemini API key: "
set /p "GEMINI_MODEL=Working Gemini model ID: "
set "MARKET_SHOP_NAME=26512076"
set "PYTHONUTF8=1"
python submissions\26512076\week-08\preflight_r3.py --model
```

API 키와 모델은 실제 사용 가능한 본인 값을 입력하십시오. 키 입력은 CMD 화면에 보일 수 있으므로 그 부분은 공유하지 마십시오. 기본 모델명은 기존 코드의 `gemini-3.5-flash-lite`를 보존했으며, 이 작업 환경에서는 해당 모델의 존재·접근 권한을 실제 API로 확인하지 않았습니다. 본인 계정에서 성공했던 모델 ID를 사용하고 preflight로 1회 확인하십시오. 모델 변경 시 수업 보고서에도 기록해야 합니다.

## 3. 로컬 오프라인 검증

```bat
set "R3_KIT_PATH=C:\Users\bhj72\ai-agent-week08-submit\scripts\market_kit.py"
python submissions\26512076\week-08\test_round3_local.py
```

12개 검사는 임시 키·임시 재고와 모델 대역을 사용합니다. 실제 A2A SDK 서버와 buyer 왕복, 영수증 서명, 키트 v4의 인증서 정책 함수를 검사합니다. 실제 Gemini나 시장에는 접속하지 않습니다. 시장에서 정산된 거래 20건을 대신하지 않습니다.

## 4. ngrok와 키트 실행

ngrok용 창:

```bat
"C:\Users\bhj72\ngrok.exe" http http://127.0.0.1:8900
```

API 키를 설정한 키트용 창:

```bat
cd /d C:\Users\bhj72\ai-agent-week08-submit
set /p "MARKET_OWNER=Existing claim code: "
set /p "R3_PUBLIC_URL=ngrok HTTPS URL: "
python scripts\market_kit.py join --dir submissions\26512076\week-08 --market https://market.ourolabs.ai --name 26512076 --port 8900 --public-url %R3_PUBLIC_URL%
```

키트 첫 줄에 `market_kit 4`, 가게와 buyer 등록이 모두 나오는지 확인하십시오. 공지 기준 Round 3 시작은 11월 3일입니다. 그 전에는 Round 2로 등록될 수 있으며 이 seller는 새 재고가 없으면 거절합니다. 예전 3개 품목 하한가 협상을 새 재고인 것처럼 흉내 내지 않습니다.

## 5. 구조와 로그

- `seller_r3.py`: 재고 검색 → Gemini 호가/수락 → 재고 예약·가격 형식 검사 → 서명 영수증. 원가 미만 판매는 허용합니다. 인증서는 항상 표시합니다.
- `buyer_r3.py`: 주문과 실제 보유 offers 검색 → Gemini 가게/SKU 선택 → Gemini 흥정 → 예산/품목/task 검사 → 서명 원본과 acceptance policy 반환. 아직 정산으로 세지 않습니다.
- `restock_r3.py`: 공개 ledger의 settle 기록과 자기 주문 관찰 → 카탈로그 후보 검색 → 5분 간격 모델 재입고 계획 → 현금/품목/수량 한도 검사. 구매 실행은 키트가 합니다.
- `review_r3.py`: 키트가 정산 후 호출 → 실제 상품·등급·보증·원가가 아닌 구매가격 저장 → 모델 후기 → 선택적으로 사실만 있는 게시글을 outbox에 예약합니다. 무작위 추천이나 좋아요 교환은 하지 않습니다.
- `r3_common.py`: SQLite 기억, 재고 예약, 후기·고장 관찰, 공통 API 호출 간격과 cooldown. SQLite는 로컬 상태이며 `memory-r3.sqlite3*`는 커밋에서 제외합니다. 판단 근거는 logs에 남깁니다.

실행 증거: `logs/market-kit.log`, `logs/r3-seller.jsonl`, `logs/r3-buyer.jsonl`, `logs/r3-restock.jsonl`, `logs/r3-review.jsonl`. 가격뿐 아니라 모델 입력과 출력 이유도 기록합니다.

한계: 시장 정산 및 실제 모델 연결은 여기서 실행하지 않았습니다. 공개 ledger는 60초 간격 한 페이지(최대 500건)를 따라가므로 시장이 빠르면 최근 흐름을 따라잡는 데 시간이 걸릴 수 있습니다. 후보 수를 줄이는 검색은 코드의 편향을 가집니다. 같은 이름의 물건을 여러 번 샀을 때 고장 책임을 하나의 가게에 확정하지 않습니다. 보증/인증서 검사에 실패한 주문은 키트가 결제하지 않고, 그때 이미 buyer는 종료되어 같은 주문을 자동 재협상하지 않습니다. 최초 6시간 동안은 고장률 추정 자료가 부족합니다.

## 6. 제출

`REPORT.md`는 구현 기준의 준비본이며 실제 Round 3 결과는 빈 상태로 명시했습니다. 순위·정산 거래 수·실패/수정 로그는 시장 운영 후 실제 수치로 갱신해야 합니다. `architecture.tex/pdf`는 현재 코드 흐름의 한 페이지 도식입니다. 개인키/API 키/SQLite 원본을 공개하지 마십시오. 양쪽 정산 20건과 마감 PR은 별도 확인해야 합니다.
