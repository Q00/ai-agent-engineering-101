# STEP 5 — seller의 제한 조건 검증

## 검사 범위

이번 단계에서는 seller의 판단 전략을 바꾸지 않고 제한 조건 검사를 추가했다.
buyer는 아직 구현하지 않았다.

| 확인할 조건 | 검증 방법 |
|---|---|
| lamp 100, chair 180, bookshelf 260 하한가 | 세 품목 모두 잘못된 첫 가격·역제안·수락 응답을 넣어 확인 |
| 수락 가격이 현재 buyer 제안과 일치 | 서로 다른 가격을 수락하는 모델 응답을 거부하는지 확인 |
| 완료·거절·취소된 task를 다시 열 수 없음 | 후속 메시지와 재취소를 보내 오류·상태·영수증 개수 확인 |
| 거래별 품목·하한가·이력이 분리됨 | lamp와 bookshelf task를 번갈아 진행 |
| 미지원 품목, 잘못된 taskId, 버전 헤더 누락 | 모델 호출 없이 오류를 반환하는지 확인 |
| 실제 하네스의 반복 협상 | 공식 검사 코드의 seller 항목을 두 하네스에서 실행 |

## 로컬 검사

기존 검사와 새 제한 조건 검사를 합쳐 21개가 통과했다.
모델 대신 합성 응답을 사용해, 하한가보다 낮은 값과 잘못된 수락 가격을
의도적으로 반환하게 했다. 코드가 이를 한 번 정정 요청한 뒤에도 잘못된
응답을 받으면 `TASK_STATE_FAILED`로 끝내고 영수증을 만들지 않았다.
가격을 하한가로 임의 보정하거나 정상 거래처럼 처리하지 않는다.

반대로 사용자가 숫자가 아닌 제안을 보내는 경우에는 STEP 4에서 구현한 대로
이미 검증된 마지막 제시가를 안내하고 같은 task를 유지한다.
모델의 잘못된 판단과 사용자의 잘못된 입력은 처리 방식이 다르다.

원본 콘솔: `logs/step5-tests-20261016-135332.stderr.txt`.
합성 모델 응답·판단 기록은 `logs/step5-tests/`에 보존했다.
기존 회귀 검사의 자료는 `logs/step2-tests/`, `logs/step3-tests/`, `logs/step4-tests/`에 있다.
로그의 `Decision failed`, `Version mismatch`, 미지원 품목 예외는 해당 오류 입력
검사에서 의도한 입력의 결과다. 테스트의 성공 여부는 마지막의 `Ran 21 tests`,
`OK`로 확인한다. SDK의 Dispatcher 종료 진단도 원본 그대로 보존했다.

과제 폴더에서 직접 실행하려면:

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-06
.\a2a-lab\.venv\Scripts\python.exe -m unittest test_harness test_seller_opening test_seller_negotiation test_seller_limits -v
```

이 명령은 서버를 따로 켤 필요가 없고 실제 모델 사용량도 소모하지 않는다.

## 실제 하네스 검사 방식

`verify_step5.py`는 `scripts/trade_test_week06.py`를 읽어 import하고,
그 안의 `seller_tests()`를 그대로 호출한다. 비교 조건과 3회 반복 횟수는
바꾸지 않는다. 저장소의 `scripts/` 파일도 수정하지 않는다.
Windows에서 시작한 서버를 정확히 종료하도록 프로세스 시작 함수만 메모리에서
native subprocess 실행으로 바꾼다. 가격 판단이나 합격 조건을 대체하지 않는다.

각 하네스의 확인 항목은 다음 9건이다.

- 카드 1건, 첫 제안 1건, 완료된 task 재요청 오류 1건.
- lamp 예산 120 거래 3건: 수락 가격이 100 이상 120 이하이고 모든 제시가가 100 이상.
- bookshelf 예산 230 협상 3건: 모든 제시가와 판매가가 하한가 260 미만으로 내려가지 않음.

실행마다 빈 포트에 별도 seller를 시작하고 자기 서버만 종료한다.
기존 실습 서버는 건드리지 않는다. 로그는 새 타임스탬프 폴더에 보존한다.

```powershell
.\a2a-lab\.venv\Scripts\python.exe verify_step5.py --mode claude
.\a2a-lab\.venv\Scripts\python.exe verify_step5.py --mode codex
```

위 명령은 각 모델을 여러 번 호출하며 구독 사용량이 소모된다.
매 실행 결과는 `logs/step5/<실행시각>-<하네스>/verification.json`,
협상 기록은 `protocol.log`, 서버 출력은 `logs/seller-under-test.log`에 저장된다.
개별 하네스 원본 입출력과 taskId별 판단은 기존처럼 `logs/harness/`,
`logs/seller/`에 저장된다.

이 결과는 seller 전용 검사다. buyer 검사를 포함한 최종 제출용
`test-report.json`은 전체 trade test를 실행할 때 생성해야 한다.

## 실제 실행 결과

두 하네스 모두 9개 항목을 전부 통과했다. 이번 단계에서는 seller 코드나
가격 프롬프트를 수정할 필요가 없었다.

| 하네스 | 검사 | lamp 거래 가격 3회 | bookshelf 예산 230 결과 3회 |
|---|---|---|---|
| Claude Code + Haiku | 9/9 통과 | 100, 120, 120 | 모두 REJECTED, 하한가 미만 제시·판매 없음 |
| Codex + Luna | 9/9 통과 | 120, 120, 115 | 모두 REJECTED, 하한가 미만 제시·판매 없음 |

실행별 원본 증거:

- `logs/step5/20261016T045344292719Z-claude/`
- `logs/step5/20261016T045344984718Z-codex/`
- `logs/step5-claude-check-20261016-135344.stdout.txt`와 `.stderr.txt`
- `logs/step5-codex-check-20261016-135344.stdout.txt`와 `.stderr.txt`

검사에 사용한 원본 스크립트의 해시도 각 `verification.json`에 기록했다.
프로토콜 로그는 편집하지 않았으며 검증용 서버는 종료했다.
모델의 거절 시점은 실행마다 달랐다. 정확한 횟수를 Python이 정하는 대신
모든 제시가와 영수증이 하한가를 지켰는지, 완료된 task가 다시 열리지
않는지를 기준으로 검증했다. 전체 buyer/seller 조합의 제출 검증은 이후 단계다.

## 직접 관찰할 실습

이미 실행 중인 seller를 사용하거나 STEP 4 안내대로 시작한다.
클라이언트 터미널에서 경로를 등록한 뒤 새 bookshelf 거래를 연다.

```powershell
. .\a2a-lab\use-a2a.ps1
a2a --timeout 120s send -a http://127.0.0.1:10099 "I want to buy a bookshelf"
```

다음 명령을 실행하면 입력 안내가 나타난다. **안내가 나타난 뒤** 출력된
Task ID를 붙여넣고 Enter를 누른다. `Read-Host` 명령 뒤에 ID를 안내 문구로
적는 것과 구분한다.

```powershell
$bookTaskId = Read-Host "방금 출력된 bookshelf Task ID를 입력하고 Enter"
a2a --timeout 120s send -a http://127.0.0.1:10099 --task-id $bookTaskId "230"
```

직접 확인할 것:

1. `input-required`이면 마지막 제시 가격이 260 이상인가?
2. 같은 230 제안을 반복해도 260 미만에 판매하지 않는가? `rejected`이면 중단한다.
3. 거절된 task에 한 번 더 보내면 오류가 나는가?

```powershell
# rejected 상태를 확인한 후에만 실행
a2a --timeout 120s send -a http://127.0.0.1:10099 --task-id $bookTaskId "230"
```

각 제안은 실제 모델 호출이며, 종료된 task 재요청은 모델 호출 전에 거절된다.
직접 실습이 끝나면 서버 터미널에서 Ctrl+C로 종료한다.

다음은 STEP 6의 buyer A2A 통신 구현이다.
