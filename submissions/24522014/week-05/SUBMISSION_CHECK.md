# 최종 제출 검증 — 2026-10-07

**week-05 제출 파일 검증은 통과했다. 현재 브랜치는 공용 HTML 4개의 충돌로 upstream에 병합할 수 없다.**

| 검증 항목 | 결과 | 기록 |
|---|---|---|
| 최신 upstream의 공식 week-05 구조 검사 | PASS: 14개 Python 소스, 시나리오 4개, 조건별 12개 결과, 로그, 인증 기록, 보고서 | [공식 검사](logs/final-official-check-03.txt) |
| 서버·host·runner 및 실제 TCP | PASS: 27개 테스트 | [실행 검사](logs/final-runtime-check-01.txt) |
| 실험 데이터와 원본 도구 로그 | PASS: 24개 고유 에피소드, 두 조건 모두 각 시나리오 3회, 모든 측정값 일치 | [실험 대조](logs/final-experiment-check-01.txt) |
| 보고서·인증 기록·재현 환경 | PASS: 보고서 4부·24행·로컬 링크, 실제 인증 결과 4줄, 설치 버전 6개 일치 | [무결성 검사](logs/final-integrity-check-01.txt) |
| CI의 Python 3.12 문법 | PASS: 14개 소스를 Python 3.12 문법으로 파싱 | [무결성 검사](logs/final-integrity-check-01.txt) |
| 자격 증명 패턴 | PASS: UTF-16 로그를 포함한 현재 파일 및 과거 week-05 blob 106개에서 완전한 키/긴 bearer 값 패턴 미발견 | [무결성 검사](logs/final-integrity-check-01.txt) |
| 시나리오 선행 커밋 | PASS: `b77100e`가 첫 실험 결과 커밋의 조상이며 현재 시나리오와 동일 | [무결성 검사](logs/final-integrity-check-01.txt) |
| week-05 작업 커밋의 소유권 | PASS: `7a5ee59..c278a7f`의 76개 파일 모두 본인 week-05 경로 | [무결성 검사](logs/final-integrity-check-01.txt) |
| 전체 브랜치의 PR 병합 | FAIL: 공용 HTML 4개 충돌. PR 병합 후 전체 변경에 대한 CI 소유권 판정은 아직 완료할 수 없음 | [병합 시뮬레이션](logs/final-pr-merge-01.txt) |

이번 검증에서는 모델 API를 호출하지 않았다. 27개 테스트의 모델 응답은 scripted 응답이며,
제출용 24개 실제 모델 결과와 원본 실험 로그는 그대로 보존했다.

## 공식 검사 출처와 실행 방식

`git ls-remote upstream refs/heads/main`으로 확인한 최신 공식 커밋은
`48d1d0b29c41675b49a7ac0b871a58303ff63e9e`다. `git fetch upstream main`으로 객체와
원격 참조를 갱신했으며 공용 작업 파일은 변경하지 않았다.
공식 [scripts/check_week05.py](https://github.com/Q00/ai-agent-engineering-101/blob/48d1d0b29c41675b49a7ac0b871a58303ff63e9e/scripts/check_week05.py)의
SHA-256은 다음과 같다.

```text
f30658dad828fd324e2ab2dc1ac505131b52de5dd8dd0ed4be53ea5bb45871d1
```

작업 트리의 `scripts/check_week05.py`는 없지만 공식 소스는 Git 객체에 있다.
[run_official_check.py](run_official_check.py)가 그 원문을 읽어 메모리에서 그대로 실행한다.
공식 검사 코드를 수정하거나 학생 폴더 안에 복사하지 않았다.
검사의 `all checks passed`와 종료 코드 0을 확인했다.

```powershell
Set-Location C:\Submissions\ai_agent_engineering\submissions\24522014\week-05
& C:\venvs\submissions\Scripts\python.exe -B .\run_official_check.py
& C:\venvs\submissions\Scripts\python.exe -B .\validate_experiment.py
& C:\venvs\submissions\Scripts\python.exe -B -m unittest -v `
  test_market_server test_market_conditions test_negotiation_host `
  test_run_experiment test_local_runner
```

`run_official_check.py`는 위 커밋으로 고정돼 있다. 나중에 갱신한 원격 참조의 검사로
실행하려면 `--revision upstream/main`을 지정할 수 있다.

### 검증 중 발견한 문제와 수정

1. 첫 공식 실행은 키 검사 오류 메시지의 문자를 Windows 기본 출력 인코딩이 표현하지 못해
   중단됐다. [첫 로그](logs/final-official-check-01.txt)를 보존하고 실행 도구의 출력을 UTF-8로 설정했다.
2. [두 번째 실행](logs/final-official-check-02.txt)에서 실제 키가 아닌
   `validate_experiment.py`의 탐지 정규식 문자열이 공식 부분 문자열 검사에 걸렸다.
   같은 정규식을 런타임에 구성하도록 리터럴을 분할했다. 완전한 키 탐지 동작은 동일하다.
3. 수정 후 [공식 검사](logs/final-official-check-03.txt)와
   [실험 로그 대조](logs/final-experiment-check-01.txt)가 통과했다.
   별도의 무결성 검사에서도 UTF-16 파일과 과거 blob을 디코딩해 실제 키 형태를 검사했다.

## 현재 PR 병합을 막는 항목

현재 `week-05`는 이전 4주차 브랜치의 공용 파일 변경을 상속했다.
공식 CI는 PR을 현재 upstream에 병합한 뒤 그 병합 결과와 base의 차이를 검사하므로,
이력을 읽어 작업 범위를 확인하는 검사와 실제 PR 병합 검사를 구분했다.

다음 명령은 Git 객체에서만 병합을 계산하고 작업 파일과 브랜치 포인터를 바꾸지 않는다.

```powershell
Set-Location C:\Submissions\ai_agent_engineering
git merge-tree --write-tree 48d1d0b29c41675b49a7ac0b871a58303ff63e9e HEAD
```

`2d2bad8`을 head로 한 실제 계산은 종료 코드 1이었고 다음 네 파일에서 충돌했다.
그 뒤의 `c278a7f` 수정도 학생 week-05 경로 안의 변경이다.

| 공용 파일 | 충돌 |
|---|---|
| `index-en.html` | content |
| `index.html` | content |
| `week-04-en.html` | add/add |
| `week-04.html` | add/add |

공용 파일은 `AGENTS.md`의 읽기 전용 영역이며 이력 재작성도 금지돼 있다.
이번 단계는 학생 디렉터리의 검증 수정과 결과 기록까지 수행했다.
**제출 전에는 기존 실패·실험 커밋을 보존하면서 브랜치의 공용 파일 충돌을 해결해야 한다.**
현재 결과를 전체 PR 통과로 판정하지 않았다.
