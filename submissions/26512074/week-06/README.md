# Small Room Furniture — Week 06

작은 방에 맞는 가구를 설명으로 찾고, 모델이 가격을 협상하는 A2A 상점입니다.
높이·소재·책 수납량·USB-C·허리 지지대 조건을 활용합니다. 여러 상점에서
구매할 때에는 견적을 먼저 확인하고, 총예산을 항목별로 나누어 동시에 협상합니다.

## 실행 환경

Python 3.12, Codex CLI와 로그인된 구독 계정이 필요합니다. 검증한 모델은
`gpt-5.6-terra`, CLI는 `0.164.0-alpha.11.1`입니다. API 키 파일은 필요하지 않습니다.
다른 계정에서 해당 모델을 사용할 수 있는지는 별도로 확인해야 합니다.

저장소 루트의 PowerShell에서:

```powershell
python -m venv submissions/26512074/week-06/.venv
submissions/26512074/week-06/.venv/Scripts/python.exe -m pip install -r submissions/26512074/week-06/requirements.txt
$env:PATH = (Resolve-Path submissions/26512074/week-06/.venv/Scripts).Path + ';' + $env:PATH
$env:PYTHONIOENCODING = 'utf-8'
python submissions/26512074/week-06/harness.py
python -m unittest discover -s submissions/26512074/week-06/tests -v
python scripts/trade_test_week06.py submissions/26512074/week-06
python scripts/check_week06.py submissions/26512074/week-06
```

Windows에서는 공용 검사기도 모델 응답을 콘솔에 출력하므로 UTF-8 설정이 필요합니다.
macOS/Linux에서는 `python -m venv ...` 후 `.venv/bin/activate`로 가상환경을 활성화합니다.
`agent.json`의 `python`은 이 활성화된 환경의 인터프리터를 사용합니다.

제출 폴더에서 독립 실행:

```powershell
python seller.py --port 9999
# 다른 터미널에서, 같은 가상환경과 UTF-8 환경을 설정한 뒤:
python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120
python run_challenges.py 1
python run_challenges.py 2
python run_challenges.py 3
```

챌린지 실행기는 필요한 서버를 직접 띄우고 종료합니다. 결과는
`logs/challenge-N/<UTC 실행 시각>/`에 새로 기록되며 이전 로그를 덮어쓰지 않습니다.
챌린지 2의 `directory.json`에는 그 실행에서만 유효한 로컬 서버 주소가 들어 있습니다.
재현하려면 실행기를 다시 실행해야 합니다.

직접 운영하는 상점 목록과 쇼핑 목록을 사용할 경우:

```powershell
python market.py --directory directory.json --shopping-list shopping-list.json --budget 850 --ledger new-ledger.json
```

디렉터리 형식은 `[{"id":"lamp-shop","url":"http://127.0.0.1:9999"}]`, 쇼핑 목록은
`[{"id":"study-light","item":"amber-study-lamp","category":"lamp"}]`입니다.
상점은 견적 메시지의 `metadata.ask`에 숫자를 제공해야 합니다. 원장 파일이
이미 있으면 중복 구매 방지를 위해 자동 재실행을 거부합니다. 결과가 `unknown`인
거래는 서버 상태·영수증을 확인하기 전까지 다른 상점에서 다시 구매하면 안 됩니다.

## 코드 읽는 순서

1. `harness.py`: 프롬프트, JSON 스키마, 모델 호출과 검증 실패 시 1회 교정.
2. `catalog.py`: 조건 필터링과 검색. 모델에 전체 카탈로그를 보내지 않습니다.
3. `seller.py`: A2A Task와 모델 결정을 연결하고 최저가를 검사합니다.
4. `buyer.py`, `protocol.py`: 같은 taskId로 협상하고 실제 영수증을 검사합니다.
5. `market.py`: 카드 탐색 → 모델의 상점 선택 → 견적 → 예산 확정 → 동시 구매.
6. `REPORT.md`: 실패와 수정, 실제 증거, 프로토콜 밖의 합의와 한계.

`catalog.jsonl`은 자체 생성한 가상 상품 데이터입니다. 생성기는
`generate_catalog.py`이며 정확히 같은 데이터가 생성되는지도 테스트합니다.
공개 배포와 타 수강생의 다른 하네스와 거래하는 선택 과제는 수행하지 않았습니다.
