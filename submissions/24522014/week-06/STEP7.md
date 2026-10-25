# STEP 7 — 하네스가 제안과 철수를 결정하는 buyer

## 구현과 검증 결과

`buyer.py`에 자율 협상 루프와 `prompts/buyer.txt`를 추가했다.
하네스가 모든 숫자 제안과 `walk_away`를 결정하고, Python이 예산 검사와
실제 A2A 요청을 담당한다. 하네스를 바꾸는 자동 대체 동작은 없다.

공식 검사 코드의 `buyer_tests()`를 두 하네스에서 각각 실행했다.
각각 lamp 구매 3회와 bookshelf 예산 부족 3회, 각 거래의 프로토콜 검사를
포함한 12개 항목이 모두 통과했다.

| 하네스 | 검사 | lamp 예산 120 구매 가격 3회 | bookshelf 예산 230 결과 3회 |
|---|---|---|---|
| Claude Code + Haiku | 12/12 | 100, 100, 100 | 모두 하네스 결정으로 철수·취소, 구매 없음 |
| Codex + Luna | 12/12 | 120, 120, 120 | 모두 하네스 결정으로 철수·취소, 구매 없음 |

표의 가격은 이번 모델 출력이며 코드의 고정값이 아니다.
가게가 기록한 모든 POST에 버전 헤더가 있었고, 구매 시 같은 taskId로
후속 제안을 보냈다. buyer에게 seller의 비공개 하한가를 알려 주지 않았다.
최종 CLI 결과도 구매는 `purchased`, 예산 부족은 `walked_away`로 확인했다.

검증 상대는 공식 코드의 규칙 기반 가게였다. 하네스 seller와의 실제 연결 및
서로 다른 하네스의 조합은 STEP 8에서 확인한다.

## 직접 실습

두 터미널 모두 과제 폴더에서 실행한다.

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-06
```

터미널 1: 규칙 기반 실습 가게를 실행한다. 같은 가게가 이미 11099번 포트에
실행 중이면 그대로 사용할 수 있다.

```powershell
.\a2a-lab\.venv\Scripts\python.exe verify_step6.py --serve --port 11099
```

터미널 2: Claude buyer가 스스로 가격을 정하게 한다.

```powershell
.\a2a-lab\.venv\Scripts\python.exe buyer.py --mode claude --url http://127.0.0.1:11099 --item lamp --budget 120
```

Codex buyer:

```powershell
.\a2a-lab\.venv\Scripts\python.exe buyer.py --mode codex --url http://127.0.0.1:11099 --item lamp --budget 120
```

예산 부족 시 철수:

```powershell
.\a2a-lab\.venv\Scripts\python.exe buyer.py --mode claude --url http://127.0.0.1:11099 --item bookshelf --budget 230
```

직접 가격이나 Task ID를 입력하지 않는다. `--offer`와 `--cancel`은 `--manual`
전용이며 자율 모드와 함께 사용할 수 없다. 실제 하네스 로그인과 사용량이
필요하다. 실습이 끝나면 터미널 1에서 Ctrl+C로 종료한다.

확인할 것:

1. lamp는 `outcome: purchased`, `state: TASK_STATE_COMPLETED`와 예산 이하 영수증을 반환하는가?
2. bookshelf는 구매 없이 `walked_away`, `TASK_STATE_CANCELED`, `receipt: null`로 끝나는가?
3. `logs/buyer/<실행>/decision-*.json`의 모델 제안과 실제 HTTP 요청 금액이 일치하는가?
4. 정해 둔 가격 목록 없이 하네스가 매번 다음 행동을 선택하는가?

모델 출력은 실행마다 달라질 수 있다. seller가 먼저 거절하면
`seller_rejected`로 끝나며, 호출·프로토콜·시간 초과 오류는 종료 코드 1로
실패한다. 이 오류를 정상 철수로 처리하지 않는다.

## 판단과 제약 처리

`buyer_decision()`은 품목, 예산, seller의 현재 가격·메시지, 거래 이력,
남은 시간과 턴 수를 프롬프트에 넣는다. 응답 형식은 다음과 같다.

```text
offer: 양의 정수 price <= budget
walk_away: price = null
두 경우 모두 비어 있지 않은 reason 포함
```

`validate_buyer_decision()`은 JSON 형식, buyer action과 예산을 검사한다.
잘못된 판단에는 오류를 설명해 한 번 정정을 요청한다. 예산 초과 가격을
예산으로 잘라 보내거나 임의 가격으로 대체하지 않는다.
반복 무효 응답과 실행 실패는 오류로 기록하며 제안하지 않는다.

`run_autonomous()`는 유효한 `offer`를 동일 task에 보내고,
`walk_away`에는 CancelTask를 보낸다. 완료된 영수증은 기존 통신 계층에서
가격·예산·품목을 검사한다. 정상 판단과 상관없이 지켜야 하는 실행 제한은
세션 290초, 판단 한 번과 정정 호출 합계 최대 90초, 최대 12턴이다.
제한에 걸리면 실패하며 코드가 철수 결정을 꾸며 내지 않는다.

세 가지 모드의 선택 방식은 유지한다. buyer의 혼합 모드 기본값은 Codex이고
다음처럼 반대로 선택할 수 있다. seller 설정은 별도 서버의 실행 옵션이다.

```powershell
.\a2a-lab\.venv\Scripts\python.exe buyer.py --mode mixed --buyer-harness claude --url http://127.0.0.1:11099 --item lamp --budget 120
```

## 검사와 원본 기록

- 로컬 검사 18개 통과: 통신 10개와 자율 판단 8개. 합성 응답으로 예산 초과
  정정, 반복 무효 응답, 하네스 실패, 철수·취소, 이력 전달과 실행 제한을 확인했다.
- 기존 수동 모드의 실제 HTTP 시나리오 4개도 통과했다.
- 실제 하네스의 공식 buyer 검사 24개가 모두 통과했다.
- 이번 단계에서 실패한 검사는 없었다. 잘못된 응답을 넣는 로컬 테스트의
  실패 기록은 의도한 합성 데이터이며 실제 모델 실패와 구분한다.

```powershell
# 모델 사용량 없는 로컬 검사
.\a2a-lab\.venv\Scripts\python.exe -m unittest test_buyer_transport test_buyer_decisions -v
# 각 하네스를 여러 번 실제 호출하는 공식 buyer 부분 검사
.\a2a-lab\.venv\Scripts\python.exe verify_step7.py --mode claude
.\a2a-lab\.venv\Scripts\python.exe verify_step7.py --mode codex
```

원본 증거:

- Claude: `logs/step7/20261016T054810029216Z-claude/`
- Codex: `logs/step7/20261016T054811190137Z-codex/`
- 로컬 콘솔: `logs/step7-tests-20261016-144701.stderr.txt`
- 로컬 합성 응답 자료: `logs/step7-tests/`, 기존 통신 회귀 자료: `logs/step6-tests/`
- 수동 회귀: `logs/step6/20261016T055244858804Z-transport/`
- 실제 buyer HTTP·판단·outcome: `logs/buyer/`
- 모델 요청·원본 응답: `logs/harness/`

검사 실행기는 원본 `scripts/trade_test_week06.py`를 읽기 전용으로 import한다.
검사 조건은 유지하고 Windows 프로세스 시작만 native 방식으로 바꾼다.
보고서에는 원본 검사 스크립트의 해시도 기록한다. 최종 제출용 `agent.json`,
전체 `test-report.json`과 보고서는 이후 단계에서 준비한다.
