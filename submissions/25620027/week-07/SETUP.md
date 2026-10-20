# 7주차 초기 세팅 — 2026-10-20

상태: **자료 수집·로컬 실행 환경 준비 완료 / 과제 구현·시장 참가 전**.
공식 자료 기준은 `7a2dfb159c199ac257772176662d45161914c08e`다.
이 문서는 준비 기록이며 제출용 REPORT.md나 통과 보고서를 대신하지 않는다.

## 이번에 준비한 것

- 6주차 커밋 `cfc9ad634f58b5404b27c814fe98059114def8bb`의 자체 Python 하네스와 판매자·구매자 런타임을 이 디렉터리로 이어받았다. 기존 6주차 작업·배포·로그는 그대로 보존한다.
- `week-07` 브랜치를 교수 저장소 최신 main에서 분리했다. 이전 보고서·test-report.json·거래 로그는 복사하지 않았다.
- Python 3.12.11 전용 `.venv`와 `requirements-kit.lock`을 준비했다. 기존 로컬 uv 캐시만 이용해 54개 패키지를 설치했다.
- 키트 의존성은 a2a-sdk 1.2.2, uvicorn 0.54.0, httpx 0.28.1, cryptography 50.0.2다. 기존 하네스는 원래 `harness_app.py.lock`과 `run_harness.sh`로 실행한다.
- `*.pem`, `.market/`, `.venv/`, 환경 파일과 Python 캐시를 제외했다. 개인키와 API 키를 복사하거나 새로 만들지 않았다.
- `run_kit.sh`는 이 전용 환경으로 교수의 `scripts/market_kit.py`를 실행한다. 해당 공용 코드는 수정하지 않는다.
- 6주차 공개 주소는 아직 서명 기능이 없으므로 이 7주차의 `deployed`로 등록하지 않았다.

## 관찰한 실행 결과

- 셸 문법, 하네스 import, 키트 `--help`, 하네스 `--help`: 정상.
- 실제 `run_harness.sh seller`를 일회용 점검 값으로 실행하고 로컬 Agent Card만 GET했다. HTTP 200, Sungjin Shop, JSONRPC 1.0을 확인한 뒤 서버를 종료했다. 실제 모델 API 호출은 없었다.
- 공식 `check_week07.py`: Python 14파일 구문은 통과. `market_buyer`, 시장 테스트 결과, 시장 테스트 로그, 실제 시장 ledger 로그, REPORT.md가 없어 **5개 실패**했다. 초기 미완료 상태를 그대로 보존한다. 검사기의 FAIL 뒤에 나오는 포괄적인 agent.json ok 줄을 완성 증거로 해석하지 않는다.
- `market_test_week07.py`, 6주차 trade test 재실행, `join`, 터널 개설, 시장 등록·거래, 개인키 생성은 아직 수행하지 않았다.

원본 준비 출력은 [환경·CLI 확인](logs/setup-environment.log), [초기 공식 검사](logs/setup-structural-check.log), [로컬 카드 조회](logs/setup-agent-card.log)에 있다.

## 다음에 만들 것

1. `market_buyer`: 가게 목록과 후기를 읽고, 가게 **이름**별 과거 거래 기억을 파일에 남기며 선택 이유를 출력한다. 기존 A2A 구매자를 재사용하되 실제 받은 영수증을 보존하고 마지막 줄에 shop·receipt·stars·review JSON을 출력한다.
2. 판매자 영수증: `{shop,item,price,task,sig_seller}`. 가격은 정수이고 task는 실제 A2A task id다. Ed25519 서명 대상은 정렬·공백 없는 UTF-8 JSON의 네 필드다.
3. 기존 하네스가 실행 중 임시 폴더로 이동하므로, 서명 키와 기억 파일의 기준 경로를 제출 디렉터리의 절대 경로로 먼저 정한다.
4. 새 시장 검사 25개를 통과하고 실제 시장 로그를 확보한다. 준비 단계에서는 테스트 결과를 만들어 채우지 않는다.
5. 필수 챌린지: 기록·후기에 따른 호가 변경 전후 점유율·평균가, 실제 시장에서 만난 부정직한 참가자와 회피 경과를 기록한다. 가짜 사례를 실제 시장 경험처럼 쓰지 않는다.

## 실행 명령

아래는 저장소 루트 기준이다. 현재는 환경 확인 명령만 실행했다.

```bash
# 전용 환경 재구성
uv venv --python 3.12.11 submissions/25620027/week-07/.venv
uv pip sync --python submissions/25620027/week-07/.venv/bin/python \
  submissions/25620027/week-07/requirements-kit.lock

# 준비 확인: API 호출·시장 등록 없음
submissions/25620027/week-07/run_kit.sh --help

# 기능 구현 후 실행: 실제 모델 호출이 생긴다
submissions/25620027/week-07/.venv/bin/python scripts/trade_test_week06.py \
  submissions/25620027/week-07
submissions/25620027/week-07/.venv/bin/python scripts/market_test_week07.py \
  submissions/25620027/week-07
submissions/25620027/week-07/.venv/bin/python scripts/check_week07.py \
  submissions/25620027/week-07
```

시장 주소와 본인 claim code는 교수의 시장 안내에서 확인해야 한다. 현재 공개 교재·README에는 실제 MARKET_URL이 없다. 주소 확인과 기능 구현 뒤 최초 한 주문만 시험할 명령은 다음과 같다. 아직 실행하지 않았다.

```bash
# MARKET_URL을 교수 안내의 실제 주소로 설정한 뒤 사용한다.
submissions/25620027/week-07/run_kit.sh join \
  --dir submissions/25620027/week-07 \
  --market "$MARKET_URL" --name 25620027 --tunnel --orders 1
```

`join`은 공개키·가게 이름·URL을 등록하고 주문·영수증·후기를 전송한다.
`--orders`의 기본값 0은 계속 실행이므로 첫 점검에서 생략하지 않는다.
한 주문도 여러 모델 호출을 필요로 하며 주문 수는 API 비용 한도가 아니다.
기존 `run_harness.sh`는 개인 설정의 OpenAI 모델을 사용한다. 6주차 VPS의 무료 공개판 설정과 다르다.
키트가 만든 개인키는 로컬에서만 사용하며 배포가 필요할 때 별도 서버 비밀 설정으로 다룬다.

## 제출 기준

마감은 **8주차 수업 시작 전 PR 생성 시각**이다. 현재 단계에서 PR을 제출하지 않았다.
필수 자료와 판정의 단일 기준은 [공식 README](../../../weeks/week-07/README.md)다.
웹 교재의 참조 구현·대시보드 수치는 본인 결과가 아니다.
