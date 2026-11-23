# Week 06 · 26510121

현재 완료: A2A 실습, 기본 판매자·구매자, 필수 도전 1과 도전 2의 기본 장보기 시나리오, 선택 도전 3.
도전 1은 15/15, 최종 기본 trade test는 21/21, 20개 가게 장보기는 3회 모두 전 품목·총예산·중복 없음 조건을 통과했다.
추가 가격 변동 실험의 미완료와 한계도 `CHALLENGE2_RESULTS.md`에 보존했다.
도전 3은 긴 문장 가격 해석·가짜 정책 지시·호가 metadata 검사를 15/15 통과했고 오프라인 검사는 85개 통과했다.

## 실행

아래 명령은 이 6주차 폴더를 현재 디렉터리로 두고 실행한다.

```powershell
# 가상환경은 lab/setup.ps1로 준비. 필요하면 설치된 Python 실행파일 경로를 지정한다.
.\lab\setup.ps1 -Python python
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# .env에는 기존 OpenAI 키와 AGENT_MODEL=gpt-4o-mini를 로컬에만 설정한다.
.\.venv\Scripts\python.exe seller.py --port 9999

# 다른 터미널에서 같은 가상환경 Python으로 구매한다.
.\.venv\Scripts\python.exe buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120
```

판매자 카드는 `http://127.0.0.1:9999/.well-known/agent-card.json`에 있다.
실행마다 `logs/seller/server-<UTC시각>/`에 새 로그를 만든다.
호가·역제안·수락·거절은 모델이 선택하고, 가격·품목·상태 검증을 통과해야 전송된다.
지원하지 않거나 모호한 첫 상품명은 기본 카탈로그의 입력 검사에서 거절한다.
기본 입력은 상품명 lamp/chair/bookshelf와 숫자만 있는 정수 제안이다.
긴 문장 속 가격은 판매자와 구매자의 `--semantic-prices` 모드에서 모델이 해석한다.

## 검증

```powershell
# API를 호출하지 않는 결정 검증·하네스 오류 경로 검사
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 실제 API를 쓰는 판매자 단계 검증: 실행마다 새 로그를 만들고 서버를 종료한다.
.\.venv\Scripts\python.exe lab\check_seller.py

# 도전 1: 실제 모델로 설명 기반 상품 선택·추가 질문·최저가 검사
.\.venv\Scripts\python.exe -u lab\check_catalog.py

# 도전 2: 모델 판매자 20개와 장보기 3회, 종료 시 서버 정리
.\.venv\Scripts\python.exe -u lab\check_shopping.py

# 도전 3: 긴 문장·여러 숫자·가짜 정책 지시와 metadata 검증
.\.venv\Scripts\python.exe -u lab\check_semantic.py
```

실제 결과와 해석은 `SELLER_RESULTS.md`, `BUYER_RESULTS.md`, `REPORT.md`, 전체 구조는 `DESIGN.md`, 진행 상태는 `PLAN.md`에 있다.
도전 1의 구조·실패·결과·실행법은 `CHALLENGE1_RESULTS.md`에 있다.
도전 2의 예산 장부·기억 전달·실패·실행법은 `CHALLENGE2_RESULTS.md`에 있다.
장보기 진입점은 `cart_buyer.py`, 재현 데이터는 `shopping/`이다.
도전 3의 처리 순서·실패 기록·검사 범위는 `CHALLENGE3_RESULTS.md`에 있다.
수동 실행은 판매자 `python seller.py --port 9999 --semantic-prices`,
구매자 `python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120 --semantic-prices`다.
판매자에는 같은 taskId로 긴 자연어 제안을 보낼 수 있다. 구매자는 판매자의 문장을 읽고 숫자 `metadata.ask`와 대조한다.
수동 카탈로그 판매자는 `python seller.py --catalog catalog/products.jsonl --port 9999`로 실행한다.
용도를 설명하면 추가 질문 또는 선택된 상품 data Part와 호가를 받는다. 이후 같은 task에 정수 제안을 보낸다.
기본 `buyer.py`는 상품 ID를 아는 단일 구매자이며, 설명 요청은 도전 1 검사 도구가 보낸다.
판매자 단계 검증은 현재 공식 kit의 고정 구매자를 재사용하지만, 최종 `test-report.json`을 만들지 않는다.
공식 전체 검증은 가상환경을 활성화해 `python`을 사용할 수 있도록 한 뒤 아래처럼 실행한다.

```powershell
.\.venv\Scripts\Activate.ps1
python ../../../scripts/trade_test_week06.py .
python ../../../scripts/check_week06.py .
```

공식 검증기는 고정 이름의 보고서와 로그를 다시 쓴다. 재실행 전 기존 `test-report.json`,
`logs/trade-test.log`, `logs/*-under-test*.log`를 새 `logs/trade-runs/<시각>/snapshot/`에 그대로 복사해 보존한다.
현재도 최초 17/21, 두 번째 15/21, 세 번째 19/21의 실패 기록을 보관하고 있다.

## 코드 흐름

`seller.py`가 A2A 요청을 받는다 → `state.py`가 해당 task의 관찰을 구성한다 →
`harness.py`가 빈 폴더에서 `harness_cli.py`를 실행한다 → 모델의 JSON 결정을 검증한다 →
판매자가 INPUT_REQUIRED / COMPLETED+receipt / REJECTED로 반영한다.

`buyer.py`는 카드·task를 처리하는 `a2a_client.py`, 거래 루프인 `buying.py`, 같은 모델 하네스를 사용한다.
최대 240초·8회 가격 교환, taskId 유지, 예산·영수증 검증, 제안 응답 유실 시 GetTask 확인을 구현했다.
`negotiation/config.py`는 키와 모델을 읽고, `runlog.py`는 비밀 값을 제외한 새 로그를 만든다.
`contracts.py`는 최저가 미만의 가격을 상향 보정하지 않고 오류로 돌려준다.
하네스는 검증 오류에 최대 한 번 재질문하고, 계속 실패하면 거래 성공이나 거절을 꾸며내지 않고 FAILED로 끝낸다.

## 현재 제한

- task 상태는 메모리에 있으며 서버 재시작 후 복원하지 않는다.
- 카탈로그는 합성 가구 5종의 제한된 속성만 지원한다. 임의의 자연어 조건 전체를 검증한 것은 아니다.
- 모든 가격 변동·예산 배분의 성공을 보장하지 않는다. 불확실한 구매는 예약을 유지하고 미완료로 반환한다.
- 긴 문장 해석은 선택한 영어·한국어 및 공격 사례로 검사했다. 모든 표현과 주입 공격의 방어를 보장하지 않는다.
- 실제 결제는 수행하지 않는다.
- `.env`, `.runtime/`, `.venv/`는 커밋하지 않는다. 원본 실행 로그는 수정하지 않는다.
