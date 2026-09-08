# Week 02 실행 안내

학번: 26512072 / GitHub: BJEon01

현재 상태: 2026-09-08에 조건 A로 실제 A/B 실험 6회를 완료했다. ReAct는 3/3 성공,
Plan-then-Execute는 0/3 성공(모두 최초 계획 JSON 파싱 실패)이었다.
`results.csv`와 `logs/`에 실패를 포함한 원본 측정이 있고, `REPORT.md`에는 학생이 제시한
가설을 실제 로그와 대조한 해석을 반영했다.
`test_harnesses.py`의 가짜 응답은 프로그램 검증 전용이며 실험 결과에 포함하지 않는다.

조건 B와 C는 코드와 절차만 준비했고 아직 실행하지 않았다. 아래 「추가 조건」을 본다.

## 고정 조건

- Provider: OpenRouter, `https://openrouter.ai/api/v1`
- Model: `nvidia/nemotron-3.5-lightning:free`
- 공용 도구: `read_file(path)`와 `count_pattern(path, pattern)`.
  후자는 파일 전체에서 정규식과 일치하는 **줄 수**를 센다.
  정확한 설명과 JSON 스키마는 `tools_shared.py`의 `TOOL_SPECS`에 있다.
- 입력: 수업 스타터의 `app.log` 그대로. 정답 판정은 사전 커밋된 `TASK.md` 그대로.
  두 도구 모두 이 공개 예제 파일 하나만 접근할 수 있다. 다른 파일 요청은 거부한다.
- `temperature=0.2`, `max_tokens=2048`, 모델 호출 상한 `max_steps=16`.
- 계획, 재계획, 최종 답변도 같은 16회 예산에 포함한다. 재계획은 1회,
  단계 안 도구 왕복은 스타터의 `max_tool_rounds=3`을 사용한다.
- 타임아웃 45초, SDK 재시도 0회. API 오류도 실패로 보존한다.
- `iters`: 시도한 모델 호출 수. `tokens`: API가 보고한 입력+출력 토큰의 합.
  응답을 받지 못한 호출의 사용량은 알 수 없으므로 note에 이 한계를 남긴다.
- `interventions`: 사람의 승인+거부 횟수. 두 도구는 읽기 전용이라 기본값은 0.
  자동 재계획은 사람의 개입에 포함하지 않는다.
- ReAct, Plan-then-Execute를 교대로 3회씩 실행한다. 모델 자동 선택/대체는 없다.

무료 모델의 현재 가용성은 바뀔 수 있다. 다른 `:free` 모델을 선택하려면 실험 전에
`AGENT_MODEL`을 설정하고 여섯 실행에 동일하게 적용한다. 조건을 바꾼 실험은
기존 결과를 지우지 않고 별도로 비교한다.

## 추가 조건

조건 A에서 Plan-then-Execute가 3회 모두 계획 호출에서 실패했다. 원인 후보는 두 개다.
계획 프롬프트가 약했거나, 계획 파서가 엄격했다. 두 후보를 한꺼번에 바꾸면 어느 쪽이
효과가 있었는지 알 수 없으므로 하나씩만 바꾼 조건을 따로 둔다.

| 조건 | `--plan-prompt` | `--plan-parser` | 바꾼 것 | 상태 |
| --- | --- | --- | --- | --- |
| A | v1 | strict | (기준) | run 1–6 완료 |
| B | v1 | tolerant | 파서만 | 미실행 |
| C | v2 | strict | 프롬프트만 | 미실행 |

- v1은 스타터의 계획 프롬프트다. v2는 출력 형태를 강제하고 **다른 태스크**의 예시를
  하나 보여준다. 예시가 이 태스크의 정답을 알려주지 않도록 WARN·요일 예시를 쓴다.
- strict는 응답 전체가 JSON 배열이어야 한다. tolerant는 응답 안에 들어 있는 첫 번째
  유효한 배열을 계획으로 받는다.
- ReAct는 계획 프롬프트와 파서를 쓰지 않으므로 조건 A의 ReAct 3회가 그대로 기준선이다.
  그래서 B와 C는 `--only plan_exec`로 Plan 쪽만 3회씩 추가한다.
- 조건은 각 로그의 `[conditions]`와 `results.csv`의 `note`에 함께 기록된다.
  `summarize_results.py`는 조건이 섞인 행을 평균 내기를 거부한다.

```powershell
$env:OPENROUTER_API_KEY = "재발급한-키"
.\submissions\26512072\.venv\Scripts\python.exe submissions/26512072/week-02/run_ab.py `
  --runs 3 --only plan_exec --plan-parser tolerant
.\submissions\26512072\.venv\Scripts\python.exe submissions/26512072/week-02/run_ab.py `
  --runs 3 --only plan_exec --plan-prompt v2
Remove-Item Env:\OPENROUTER_API_KEY
```

실행 후 조건별 집계는 다음과 같다. 조건을 섞은 평균은 쓰지 않는다.

```powershell
.\submissions\26512072\.venv\Scripts\python.exe submissions/26512072/week-02/summarize_results.py --by-condition
```

### 조건 B의 사전 예측

`replay_plan_failures.py`는 API를 호출하지 않고, 이미 기록된 계획 응답 3개를 두 파서에
다시 통과시킨다. 결과는 `verification/plan-parser-replay.txt`에 있다. tolerant는 3/3을
파싱하지만 그중 둘은 모델이 예시로 흘린 `step1, step2, step3` 류의 자리표시자였다.
따라서 조건 B는 파싱 성공률이 아니라 **건져낸 계획의 단계 내용과 최종 성공 여부**로
평가한다. 이 예측은 실제 실행을 대신하지 않는다. B를 돌리면 모델은 새 응답을 낸다.

## Windows PowerShell

저장소 루트에서 최초 설치한다. 이번 작업에서 이 가상환경은 이미 생성했다.

```powershell
python -m venv submissions/26512072/.venv
.\submissions\26512072\.venv\Scripts\python.exe -m pip install -r submissions/26512072/week-02/requirements.txt
```

저장소 루트에서 실행한다. 키가 환경변수에 없으면 **본인 터미널에서만** 숨김 입력창이
열린다. 입력값은 자식 Python 프로세스의 환경변수로 전달되며 파일에 저장하지 않는다.
채팅에 노출했던 키는 폐기하고 재발급한 키를 사용한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\submissions\26512072\week-02\run_openrouter.ps1
```

스크립트는 먼저 성공 기준의 사전 커밋 여부를 확인하고 실제 실행만 `results.csv`에
추가한다. 각 `logs/react-NN.txt`, `logs/plan_exec-NN.txt`에는 조건, 모델 응답,
Action, Observation, 최종 답변과 측정값이 남는다. 생성한 로그는 수정하지 않는다.

API 호출 없는 준비 상태 확인:

```powershell
.\submissions\26512072\.venv\Scripts\python.exe submissions/26512072/week-02/run_ab.py --check
```

## 실행 후

```powershell
.\submissions\26512072\.venv\Scripts\python.exe submissions/26512072/week-02/summarize_results.py
python scripts/check_week02.py submissions/26512072/week-02
```

집계 출력의 원본 표와 평균/표본분산 표를 `REPORT.md`에 넣고 로그를 근거로
해석 문단을 직접 작성한다. 이 실험은 실패를 제거하면 안 된다.
실행 묶음과 오류 수정은 각각 별도 커밋으로 보존한다. squash/amend는 하지 않는다.
검사가 통과하고 본인 포크에 push한 뒤 PR 제목은 `[week-02] 26512072`로 한다.
현재 origin은 강의 원본 `Q00/ai-agent-engineering-101`이므로 본인 포크와 혼동하지 않는다.

## 코드 이해와 오프라인 검증

두 하네스의 기본 알고리즘은 `weeks/week-02/starter/`에서 복사했다.
ReAct는 매 응답의 도구 호출을 실행하고 그 결과를 다음 호출에 다시 제공한다.
Plan-then-Execute는 JSON 계획을 먼저 만들고 각 단계를 순서대로 수행하며,
`OFF_PLAN`이 오면 남은 계획을 한 번까지 교체한다.
이번 수정은 OpenRouter 연결, 공통 호출 예산, 원문 로그와 실패 계측을 보완했다.

```powershell
Push-Location submissions/26512072/week-02
..\.venv\Scripts\python.exe -m unittest -v test_harnesses
..\.venv\Scripts\python.exe replay_plan_failures.py
Pop-Location
```

오프라인 테스트는 16개다. 조건 추가 시점의 출력은
`verification/offline-tests-conditions.txt`에 있다.

구조 검사는 실제 6회 실행 후 통과했다. 다만 모델이 명시적인 Thought: 표기를 일부
생략했고, 계획 파싱 실패 실행에는 Observation이 없다. 이 한계는 REPORT.md에 기록했다.
과제 체크포인트 중 "실행마다 로그에 Thought와 Observation이 남는다"는 조건 A에서
충족하지 못했다. 조건 B와 C는 Plan이 실행 단계에 도달하면 Observation이 남는지를
확인할 수 있는 실행이다. 구조 검사가 통과해도 로그 내용과 본인의 해석까지 검증한
것은 아니다.

근거: [수업 과제](../../../weeks/week-02/README.md),
[OpenRouter 연결](https://openrouter.ai/docs/quickstart),
[모델 목록](https://openrouter.ai/api/v1/models).
