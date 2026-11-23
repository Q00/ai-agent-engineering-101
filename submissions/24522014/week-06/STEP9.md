# STEP 9 — 공식 전체 trade test

## 실행 범위

현재 `agent.json`의 `claude` 설정으로 공식 `scripts/trade_test_week06.py`를 수정 없이 실행한다.
seller와 buyer 모두 Claude Code + Haiku를 사용한다. Step 8의 네 조합 연결 검사와 달리,
공식 테스트는 고정 buyer로 우리 seller를, 규칙 기반 seller로 우리 buyer를 각각 검사한다.
실제 모델을 호출하므로 CLI 로그인과 사용 가능한 구독이 필요하다. API 키를 코드에 넣지 않는다.

## 직접 실행하는 명령

저장된 이번 결과를 읽는 데에는 재실행이 필요하지 않다. 다시 실행할 때는 이전
`test-report.json`, `logs/trade-test.log`, `logs/seller-under-test.log`,
`logs/buyer-under-test-*.log`를 별도의 시각별 증거 폴더에 복사하고 커밋해 먼저 보존한다.
공식 테스트는 이 고정 파일명에 새 결과를 기록한다. 과거 실패 기록도 남긴다.

저장소 루트의 PowerShell에서:

```powershell
cd C:\Submissions\ai_agent_engineering
$work = Join-Path (Get-Location).Path 'submissions/24522014/week-06'
$env:Path = (Join-Path $work 'a2a-lab/.venv/Scripts') + ';' + $env:Path
& (Join-Path $work 'a2a-lab/.venv/Scripts/python.exe') scripts/trade_test_week06.py submissions/24522014/week-06
```

`agent.json`의 명령이 `python`으로 시작하므로 자식 프로세스도 같은 가상환경의 Python을
찾도록 현재 세션 PATH에 추가한다. 별도 seller 터미널을 열 필요는 없다.

`--profile`을 받는 Step 8 검사와 달리 공식 테스트는 `agent.json`만 읽는다.
프로필이나 명령을 변경하면 제출할 공식 결과도 해당 설정으로 다시 생성해야 한다.

## 결과 읽기

총 21개 항목이 모두 통과해야 한다.

| 검사 | 횟수 | 의미 |
|---|---:|---|
| seller_card | 1 | Agent Card와 JSON-RPC 1.0 인터페이스 |
| seller_opens | 1 | 첫 가격과 INPUT_REQUIRED 응답 |
| seller_deal | 3 | lamp를 100 이상 120 이하에 판매 |
| seller_terminal | 1 | 종료된 task의 추가 메시지에 오류 |
| seller_holds_reserve | 3 | bookshelf 제시가·판매가의 하한가 260 준수 |
| buyer_deal | 3 | lamp를 예산 120 안에서 정확히 한 번 구매 |
| buyer_holds_budget | 3 | 예산 230으로 bookshelf 구매하지 않음 |
| buyer_protocol_deal | 3 | 버전 헤더와 동일 taskId 후속 요청 |
| buyer_protocol_holds_budget | 3 | 거래 불성립 상황에서도 버전 헤더 준수 |

`test-report.json`에는 테스트 코드의 해시, 실행 명령, 실행 시각, 각 결과가 담긴다.
결과를 수동으로 고치지 않는다. `passed: true`뿐 아니라 21개 항목이 모두 있는지 확인한다.

```powershell
$result = Get-Content -Raw -Encoding UTF8 "$work/test-report.json" | ConvertFrom-Json
$result | Select-Object started, passed, seller_cmd, buyer_cmd
$result.results | Format-Table test, run, pass, detail -AutoSize
```

## 실제 실행 결과

2026-10-16 16:17:56 KST에 시작한 공식 테스트가 첫 전체 실행에서 **21/21 PASS**로 끝났다.
공식 스크립트를 수정하거나 프로세스 실행 함수를 바꾸지 않았다.

| 항목 | 1회 | 2회 | 3회 |
|---|---|---|---|
| 우리 seller의 lamp 판매가 | 120 | 100 | 100 |
| 우리 seller의 bookshelf | 거절 | 거절 | 거절 |
| 우리 buyer의 lamp 구매가 | 105 | 105 | 100 |
| 우리 buyer의 bookshelf | 취소, 구매 없음 | 취소, 구매 없음 | 취소, 구매 없음 |

모든 제시 가격과 판매 가격이 하한가를 지켰고, buyer는 예산을 넘지 않았다.
모든 buyer 요청의 버전 헤더와 lamp 거래의 taskId 후속 요청 검사도 통과했다.
모델이 판단하므로 재실행 시 가격과 제안 횟수는 달라질 수 있다.

보존한 증거:

- `test-report.json`: 공식 생성 결과, 21개 항목 모두 통과.
- `logs/trade-test.log`: 공식 프로토콜·판정 로그.
- `logs/seller-under-test.log`: 실제 seller 출력 및 모델 로그 위치.
- `logs/buyer-under-test-*.log`: 6회 buyer 실행 원본 출력 및 모델 로그 위치.
- `logs/step9-trade-20261016-161756.stdout.txt`, 같은 이름의 `stderr.txt`: 전체 콘솔 원본.
- 위 출력에 연결된 `logs/harness/`, `logs/seller/`, `logs/buyer/`: 요청·응답·판단 원본.

공식 테스트 코드의 SHA-256:
`8e00baa43b319e6c0067eab8eedc6834cbe4b9dc3e9e2ab5bd16e9572c99f120`.

## 구조 검사에서 발견한 문제

거래 테스트 실행 중 제출 전 구조 검사도 확인했다. 이전 LAB 파일의 UTF-8 BOM 때문에
다음 오류가 발생했다.

```text
FAIL  a2a-lab\seller_agent.py has a syntax error: invalid non-printable character U+FEFF (<unknown>, line 1)
```

원본: `logs/step9-structure-before-20261016-161935.stdout.txt`.
`a2a-lab/seller_agent.py`의 첫 BOM만 제거했으며, 재검사에서 다음 결과를 확인했다.

```text
ok    21 source file(s), .py files parse
```

원본: `logs/step9-structure-bom-fixed-20261016-162007.stdout.txt`.
당시에는 거래 테스트가 진행 중이어서 `test-report.json`, `trade-test.log`가 아직 없었고,
`REPORT.md`도 작성 전이었다. 실패·수정 결과를 각각 별도 커밋으로 보존했다.

거래 테스트 완료 후 재검사에서는 소스 파싱, agent.json, 현재 테스트·명령과 일치하는
21개 통과 결과, 프로토콜 로그가 모두 확인됐다. 남은 실패 항목은 `REPORT.md missing`
하나다. 원본은 `logs/step9-structure-after-20261016-162336.stdout.txt`에 보존했다.

최종 `REPORT.md` 작성과 제출 전 구조 검사 마무리는 STEP 10에서 진행한다.
