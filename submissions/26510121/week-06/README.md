# Week 06 · 26510121

현재 완료: A2A 실습, 협상 설계, 기본 판매자 구현과 단계 검증.
다음 단계: 기본 구매자 구현. 필수 도전 1·2와 공식 전체 trade test는 이후 진행한다.

## 실행

아래 명령은 이 6주차 폴더를 현재 디렉터리로 두고 실행한다.

```powershell
# 가상환경은 lab/setup.ps1로 준비. 필요하면 설치된 Python 실행파일 경로를 지정한다.
.\lab\setup.ps1 -Python python
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# .env에는 기존 OpenAI 키와 AGENT_MODEL=gpt-4o-mini를 로컬에만 설정한다.
.\.venv\Scripts\python.exe seller.py --port 9999
```

판매자 카드는 `http://127.0.0.1:9999/.well-known/agent-card.json`에 있다.
실행마다 `logs/seller/server-<UTC시각>/`에 새 로그를 만든다.
호가·역제안·수락·거절은 모델이 선택하고, 가격·품목·상태 검증을 통과해야 전송된다.
지원하지 않거나 모호한 첫 상품명은 기본 카탈로그의 입력 검사에서 거절한다.
현재 기본 입력은 상품명 lamp/chair/bookshelf와 숫자만 있는 정수 제안이다.

## 검증

```powershell
# API를 호출하지 않는 결정 검증·하네스 오류 경로 검사
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 실제 API를 쓰는 판매자 단계 검증: 실행마다 새 로그를 만들고 서버를 종료한다.
.\.venv\Scripts\python.exe lab\check_seller.py
```

실제 결과와 해석은 `SELLER_RESULTS.md`, 전체 구조는 `DESIGN.md`, 진행 상태는 `PLAN.md`에 있다.
단계 검증은 현재 공식 kit의 고정 구매자를 재사용하지만, 최종 `test-report.json`을 만들지 않는다.
구매자가 구현된 뒤 `agent.json`과 공식 전체 테스트를 연결한다.

## 코드 흐름

`seller.py`가 A2A 요청을 받는다 → `state.py`가 해당 task의 관찰을 구성한다 →
`harness.py`가 빈 폴더에서 `harness_cli.py`를 실행한다 → 모델의 JSON 결정을 검증한다 →
판매자가 INPUT_REQUIRED / COMPLETED+receipt / REJECTED로 반영한다.

`negotiation/config.py`는 키와 모델을 읽고, `runlog.py`는 비밀 값을 제외한 새 로그를 만든다.
`contracts.py`는 최저가 미만의 가격을 상향 보정하지 않고 오류로 돌려준다.
하네스는 검증 오류에 최대 한 번 재질문하고, 계속 실패하면 거래 성공이나 거절을 꾸며내지 않고 FAILED로 끝낸다.

## 현재 제한

- task 상태는 메모리에 있으며 서버 재시작 후 복원하지 않는다.
- 대규모 카탈로그와 설명 기반 상품 선택은 도전 1에서 구현한다.
- 긴 문장 속 제안 금액 해석은 아직 구현하지 않았다.
- 실제 결제는 수행하지 않는다.
- `.env`, `.runtime/`, `.venv/`는 커밋하지 않는다. 원본 실행 로그는 수정하지 않는다.
