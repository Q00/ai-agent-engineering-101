# Week 02 실행 안내

학번: 26512072 / GitHub: BJEon01

현재 상태: 실행 준비와 오프라인 검증을 완료했다. API 키 환경변수가 없어 실제
A/B 실험은 수행하지 않았다. `results.csv`는 헤더만 있고, 실제 로그와 해석은 아직 없다.
`test_harnesses.py`의 가짜 응답은 프로그램 검증 전용이며 실험 결과에 포함하지 않는다.

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
Pop-Location
```

실제 API 실행 전에는 구조 검사에서 실행 수와 로그 수가 부족하다고 나오는 것이 맞다.
`REPORT.md`는 작성 틀이므로 파일 존재 검사가 통과해도 보고서가 완성된 것은 아니다.

근거: [수업 과제](../../../weeks/week-02/README.md),
[OpenRouter 연결](https://openrouter.ai/docs/quickstart),
[모델 목록](https://openrouter.ai/api/v1/models).
