## What I built

`gpt-4o-mini`와 OpenAI Responses 하네스로 가게 선택·기억·후기를 가진 Market Buyer, Ed25519 서명 영수증을 발급하는 A2A Shop을 구현했습니다. 예산과 하한가를 코드에서 지키고, 정상 거래가 시장 검증을 통과했는지도 기억에 반영합니다.

- 이름으로 가게를 기억하고 후기와 직접 거래 경험을 활용합니다. 자기 가게는 선택하지 않습니다.
- 영수증은 품목·가격·예산·task·등록 공개 키의 서명을 검증하고 원본 data Part를 유지합니다.
- 필수 도전 1: 실제 시장에서 두 가격 정책을 약 3분씩 관찰했습니다. 거래 15→19건, 구간 점유율 12.93→14.84%, 구간 평균가 165.33→185.00을 기록했습니다. 품목 구성과 집계 시점 차이가 있어 정책만의 인과 효과로 주장하지 않습니다.
- 필수 도전 2: 실제 시장 주문에서 `maple-home`의 130 합의/145 영수증을 확인했습니다. 서명은 유효했지만 예산 140을 넘겨 거부했고, 후속 실제 주문에서 이 상대를 피한 로그를 보존했습니다.
- Windows PC에서 Buyer와 Shop을 실행하며, 무료 터널이 끝나면 기존 키와 판매 프로세스로 새 URL을 등록합니다.

## What I tried and discarded

- 기존 미서명 구현은 공식 테스트 22/25였고 `shop`, `task` 누락 및 서명 검증 실패를 수정했습니다. canonical JSON과 정수 가격 정규화를 추가해 25/25를 통과했습니다.
- 공개 후기를 곧바로 사실로 취급하지 않고, 실제 주문 한 건으로 금액 불일치를 확인한 뒤 정상 선택으로 돌아왔습니다.
- 접속 오류나 예산 부족을 부정직함으로 분류하지 않았습니다. 시장 집계와 로컬 영수증의 경계 차이도 보고했습니다.
- Cloudflare quick tunnel 429와 SSH 실패 후 Pinggy를 사용했습니다. 이전 실패 로그와 정책 변경 커밋을 보존했습니다.
- 10월 20일 비용 중단 요청으로 종료한 기간이 있으며 10월 27일 재기동했습니다. 과거 중지 기간을 상시 가동했다고 주장하지 않습니다. 현재 운영도 PC·네트워크 유지가 필요합니다.

자세한 근거는 `REPORT.md`, `logs/challenge-1/README.md`, `logs/challenge-2/README.md`, `OPERATIONS.md`에 있습니다.

## How to run

Python 3.10 이상(검증 환경 3.12), 기본 모델 `gpt-4o-mini`입니다. `OPENAI_API_KEY`는 실행 환경에 설정하며 값은 제출하지 않습니다.

```powershell
python -m pip install -r submissions/26510121/week-07/requirements.txt
python -m pip install -r submissions/26510121/week-07/agent-market/requirements.txt
python scripts/market_test_week07.py submissions/26510121/week-07
python scripts/check_week07.py submissions/26510121/week-07 --github seochanit
```

공식 테스트를 재실행하기 전에 기존 결과와 루트 테스트 로그를 새 snapshot 폴더에 보관해야 합니다. 실거래 기동·중지 방법은 `OPERATIONS.md`에 있습니다. 모델 호출은 실제 API 비용을 발생시킵니다.

## Checklist

- [x] 공식 market test 25/25 통과
- [x] 로컬 테스트 113개 통과
- [x] `check_week07.py` 로컬 통과
- [x] 두 필수 도전의 실제 실행 증거와 보고서 포함
- [x] 운영 컴퓨터의 `logs/market-kit.log` 포함
- [x] API 키·claim·개인키·`.env` 제외
- [x] 실패 커밋을 보존하고 squash하지 않음

GitHub CI와 PR 생성 시각은 PR을 연 뒤 확인합니다. 순위 1등이나 미래의 무중단 운영을 보장한다는 주장은 하지 않습니다.
