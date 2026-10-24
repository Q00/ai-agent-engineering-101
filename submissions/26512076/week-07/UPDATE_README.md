# 7주차 코드 업데이트 안내

이 패키지는 기존 `week-07`에 덮어쓰는 수정 파일 모음입니다. 공식 키트, 기존 영수증 키, `agent.json`, 이전 로그, 기존 `test-report.json`은 교체하지 않습니다. 기존 파일은 먼저 별도로 백업해 주세요. 이 패키지에는 개인키, API 키, 시장 claim code가 없습니다.

## 1. 설치 및 공식 검사

ZIP의 `week-07` 안에 있는 파일들을 다음 폴더에 복사합니다.

```text
C:\Users\bhj72\ai-agent-engineering-101\submissions\26512076\week-07
```

기존의 `shop_key.pem`, `buyer_key.pem`은 그대로 유지합니다. 시장에 등록한 키를 임의로 재생성하지 않습니다. 키는 커밋하지 않습니다.

아나콘다 프롬프트에서 기존에 사용하던 Python 환경을 활성화하고, 필요한 패키지가 없다면 설치합니다.

```bat
python -m pip install "a2a-sdk[http-server]" uvicorn httpx openai cryptography
```

API 키는 기존 `GROQ_API_KEY` 환경 변수를 사용합니다. 기본 모델은 `openai/gpt-oss-20b`이며 `GROQ_MODEL`을 설정하면 변경됩니다. 모델 변경은 직접 선택한 경우에만 적용합니다.

공식 테스트는 저장소 최상위에서 실행합니다. 먼저 `MARKET_RUN_ID`를 validation으로 지정하여 테스트 거래가 실제 시장 실험 집계에 포함되지 않게 합니다.

```bat
cd /d C:\Users\bhj72\ai-agent-engineering-101
set "MARKET_RUN_ID=validation"
python scripts/market_test_week07.py submissions/26512076/week-07
```

`25/25 passed -> PASS`를 확인해야 합니다. 현재 패키지는 실제 API를 사용하는 공식 검사를 대신 실행한 결과물을 포함하지 않습니다. `agent.json`의 명령은 기존과 같지만 코드가 변경되었으므로 재검사해야 합니다. API의 일일 한도가 소진되면 대기하거나 운영자가 사용할 수 있는 제공자 한도를 확보해야 합니다. 재시도만으로 일일 사용량 제한을 없앨 수는 없습니다.

오프라인 검사는 제출 폴더에서 다음과 같이 실행합니다. 모델과 A2A 응답을 대역으로 처리한 안전성 검사이며 공식 25개 테스트와는 다릅니다.

```bat
cd /d C:\Users\bhj72\ai-agent-engineering-101\submissions\26512076\week-07
python verify_week07_local.py
```

## 2. 바뀐 동작

- 판매자는 매 task가 시작할 때 최근 자체 판매 결과와 등록한 자기 가게의 후기 스냅샷을 읽습니다. `PRICE_PHASE=after`이면 이를 모델에 전달하여 호가를 다시 판단하도록 합니다. 가격은 모델이 정하며, 기존 하한가 검사는 코드에서 계속 수행합니다.
- `PRICE_PHASE=before`에서는 피드백을 모델에 전달하지 않아 기준 기간을 기록할 수 있습니다. 모델의 기본 협상 프롬프트와 하한가는 동일합니다. 가격 변경은 보장하지 않으며 실제 `quote` 이벤트를 비교해야 합니다.
- 판매자 관측과 모델의 가격 판단은 `logs/challenge-1/seller-events.jsonl`에 기록합니다. task마다 받은 피드백, 최초 호가, 최종 판매 영수증, 거절·오류가 남습니다.
- buyer는 영수증의 정수 가격, 주문 품목, 예산, 실제 제안 가격, task ID를 검사합니다. 불일치가 있으면 거래 성공으로 보고하지 않고 증거를 기록합니다. 서명의 암호학적 검증과 buyer 서명은 원래대로 키트가 담당합니다.
- 자기 기억에 영수증 불일치가 기록된 가게 이름은 다음 선택에서 제외합니다. URL이 바뀌어도 이름으로 기억합니다. 모든 가게가 제외 대상이면 아무 요청도 보내지 않고 영수증 없는 결과로 종료합니다. API 오류와 단순 협상 실패는 부정직함으로 분류하지 않습니다.
- `logs/challenge-2/avoidance.jsonl`에는 영수증 불일치의 구체적 내용과, 이후 선택에서 해당 이름을 제외한 근거가 남습니다. 이 로그에는 테스트와 시장 기록이 함께 생길 수 있으므로 실제 시장 사례와 원본 `market-kit.log`를 연결해야 합니다.
- 코드로 후기 점수와 기억을 비교하여 가게를 고르고, 선택한 가게의 최근 후기와 기억을 협상 모델에도 전달합니다. 가게 선택을 모델이 수행한다고 보고서에 쓰지 않습니다.
- 판매자·시장 buyer의 모델 호출에 제한된 재시도와 429 대기 기록을 추가했습니다. 오래 기다려야 하는 제한은 `.market/provider-cooldown.json`에 기록하여 뒤따르는 호출을 잠시 중단합니다. 이 파일은 비밀키를 포함하지 않습니다.

## 3. 필수 도전 1: 가격 변경 전후 관측

처음에는 기준 기간을 실행합니다. 아래 두 환경 변수는 **시장 키트를 실행할 터미널**에 설정합니다.

```bat
set "MARKET_RUN_ID=live-market"
set "MARKET_SHOP_NAME=26512076"
set "PRICE_PHASE=before"
```

가게와 buyer를 실제 시장에서 운영한 뒤 키트를 Ctrl+C로 종료하고, 제출 폴더에서 다음 명령으로 기준 기간 요약을 저장합니다.

```bat
python market_challenges.py snapshot --phase before
```

이 요약의 `local`은 내 판매자의 관측입니다. 시장 검증 여부와 관계없이 로컬에서 서명 영수증을 발행한 수이며, 시장 전체의 점유율이 아닙니다. 진행 중 task가 버려지면 로컬 성사율도 낮아질 수 있습니다.

### 내 가게의 후기를 모델에 전달하는 방법

키트의 `.market/shops.json`에는 자신의 가게가 제외되어 있습니다. 따라서 자기 가게의 후기는 dashboard에서 확인한 내용을 별도 파일로 저장해야 합니다. 과제 규칙에 따라 에이전트 코드가 시장 API에 직접 접속하지는 않습니다.

dashboard에서 내 등록 이름과 실제 후기의 stars·review·round를 복사해 `own_reviews.json`을 작성합니다. 형식은 다음과 같으며, 빈 배열을 실제 후기들로 채워야 합니다. 후기를 만들어 넣지 않습니다.

```json
{"name":"26512076","reviews":[]}
```

이 파일을 입력으로 사용합니다.

```bat
python market_challenges.py feedback --input own_reviews.json --name 26512076
```

명령은 `shop_feedback.json`과 `logs/challenge-1/reviews-시간값.json`을 저장합니다. 이후 task부터 판매자가 이를 읽으며, task 안에서는 받은 피드백을 고정하여 사용합니다. 후기가 추가되면 원본 내용을 갱신하고 같은 명령을 다시 실행합니다.

이제 키트를 실행할 터미널에서 다음을 설정하고 같은 키트 명령으로 다시 운영합니다.

```bat
set "PRICE_PHASE=after"
```

운영 후 제출 폴더에서 다음을 실행합니다.

```bat
python market_challenges.py snapshot --phase after
python market_challenges.py compare
```

다른 실험 실행과 분리하려면 `MARKET_RUN_ID`에 별도 이름을 지정하고 snapshot에 동일한 `--run-id`를 전달합니다. snapshot의 `--since`와 `--until`에는 seller 이벤트와 같은 UTC ISO 시각을 사용하여 관측 기간을 지정할 수 있습니다.

### 점유율과 평균 거래가

과제가 요구하는 점유율은 동일한 관측 기간·집계 범위에서의 내 가게 거래 수 / 전체 시장 거래 수입니다. 로컬 판매자 로그만으로 분모를 알 수는 없습니다. before와 after 각각 dashboard 수치를 캡처하여 같은 기간의 검증된 전체 거래 수, 내 거래 수, 내 평균 거래가를 기록합니다. 누적 수치만 있으면 기간의 시작·끝 누적값의 차이로 거래 수를 구하고, 평균도 해당 기간 기준으로 산출합니다.

`snapshot`에 다음 인자 네 개를 모두 제공하면 해당 시장 수치와 증거 파일을 요약에 연결합니다. 값은 실제 관측 결과를 사용해야 합니다.

- `--market-sales`: 해당 기간 전체 시장의 검증된 거래 수입니다.
- `--own-market-sales`: 같은 기간 내 가게의 검증된 거래 수입니다.
- `--market-average`: 같은 기간 내 가게의 평균 거래가입니다.
- `--market-source`: 수치를 확인할 수 있는 로컬 캡처/내보내기 파일 경로입니다.

입력 수치를 독립적으로 검증하는 기능은 아니며, 사람이 dashboard에서 옮긴 값임을 표시합니다. 이 인자를 생략하면 시장 점유율은 `null`로 남습니다. 수치나 실제 판매 데이터가 없는 상태에서는 도전 통과를 주장할 수 없습니다. 품목 구성, 유입량, 시간대가 다르면 가격만의 인과 효과로 해석하지 않습니다.

## 4. 필수 도전 2: 실제 상대의 불일치와 이후 회피

시장에서 상대가 합의 가격과 다른 금액, 다른 품목 또는 다른 task ID의 영수증을 보내면 buyer가 `receipt_mismatch` 이벤트에 원본 영수증·내 제안·예산·이름·task를 남깁니다. 기억 파일에는 해당 이름과 결과를 보관하며, 후속 선택에서 그 이름을 제외한 `selection` 이벤트가 남습니다.

실제 시장 사례의 원본 `logs/market-kit.log`, `buyer_memory.jsonl`, `logs/challenge-2/avoidance.jsonl`을 함께 보관합니다. 잘못된 영수증만으로 상대의 의도를 완전히 확정할 수는 없으므로 보고서에는 관찰된 불일치와 회피를 구체적으로 설명합니다. 서명 누락·서명 실패나 낮은 별점만으로 사기라고 단정하지 않습니다.

기존 업로드의 buyer 기억에는 `receipt_mismatch`가 없었습니다. 따라서 실제 시장 증거를 더 모아야 합니다. 오프라인 검사의 가짜 상대를 실제 시장에서 만난 상대처럼 제출하면 안 됩니다. 실제로 그런 상대를 만났지만 코드가 놓친 경우, 원본 로그를 분석하여 별도의 근거를 작성해야 합니다.

## 5. 시장 운영 및 배포

가게와 buyer는 마감까지 켜 둔 PC에서 운영할 수 있습니다. 이미 사용하는 ngrok를 계속 쓸 경우, 별도 터미널에서 다음을 실행합니다.

```bat
ngrok http 8900
```

표시된 공개 URL을 확인하고, API 키와 claim code를 환경 변수로 설정한 **다른 터미널**에서 저장소 최상위로 이동한 뒤 키트를 실행합니다. 아래 URL 예시를 실제 ngrok URL로 바꾸어야 합니다.

```bat
cd /d C:\Users\bhj72\ai-agent-engineering-101
python scripts/market_kit.py join --dir submissions/26512076/week-07 --market https://market.ourolabs.ai --name 26512076 --port 8900 --public-url https://YOUR-NGROK-DOMAIN.ngrok-free.app
```

`MARKET_OWNER`에는 https://market.ourolabs.ai/join 에서 내 GitHub 계정으로 받은 claim code를 설정합니다. 키트가 가게와 buyer를 모두 등록해야 하므로 이 명령에는 `--buyer-only`를 넣지 않습니다. ngrok URL이 바뀌면 새 URL로 다시 join합니다. ngrok는 가게가 아니라 키트의 프록시 포트 8900을 연결합니다. 키트가 seller를 별도 포트에서 시작합니다.

cloudflared가 설치된 PC라면 같은 목적에 키트의 `--tunnel`을 사용할 수 있습니다. 외부 서버에 이미 배포한 가게를 쓸 때에만 `--deployed`를 쓰며, 그 가게가 로컬 `shop_key.pem`과 같은 키로 서명해야 합니다.

등록과 운영에는 실제 시장의 상태가 관여합니다. 이 패키지를 실행하는 것만으로 GitHub 소유자 연결, 가게 접근 가능 여부, 마감 시점의 생존 또는 Round 2 인정 거래가 보장되지는 않습니다. dashboard와 `market-kit.log`에서 직접 확인해야 합니다. PC의 절전·종료와 터널·키트 중단도 피해야 합니다.

기존 로그에 이어서 운영하면 Round 0·1 기록을 보존할 수 있습니다. 키트는 로그에 추가 기록합니다. `seller.py` 수정 후에는 실행 중 키트를 재시작해야 합니다.

## 6. 확인과 제출

제출 폴더에서 다음 명령으로 **저장된 기록**을 요약할 수 있습니다.

```bat
python market_challenges.py status
```

`round2_verified_ledger_evidence`가 비어 있으면 이 로그에서 Round 2 인정 거래를 확인할 수 없다는 뜻입니다. 이 명령은 실시간 등록이나 GitHub owner를 조회하지 않습니다.

실제 운영 결과를 확보한 다음 REPORT.md를 7주차 필수 6개 항목으로 다시 작성해야 합니다. 기존 REPORT.md와 CHALLENGE1/2_README는 6주차 자료이며 이 패키지는 이를 덮어쓰지 않습니다. 기존 6주차 challenge 로그도 7주차 도전의 증거로 대신 쓰지 않습니다.

마지막으로 저장소 최상위에서 다음을 실행합니다.

```bat
python scripts/check_week07.py submissions/26512076/week-07
```

현재 코드로 생성한 공식 검사 결과, 실제 시장과 도전 로그, 7주차 보고서를 커밋하고 PR을 엽니다. 실패 시도도 별도 커밋으로 보존하고 squash하지 않습니다. `*.pem`, `.env`, API 키와 claim code는 커밋하지 않습니다.
