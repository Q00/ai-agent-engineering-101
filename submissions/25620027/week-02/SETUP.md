# 2주차 실습 준비 — 하네스 A/B

공식 자료: [웹페이지의 PART B: LAB](https://wpti.dev/ai-agent-engineering-101/week-02.html) · [과제 명세](https://github.com/Q00/ai-agent-engineering-101/blob/main/weeks/week-02/README.md)

## 지금 준비된 것

- 공식 시작 코드 6개를 그대로 복사했다. 기준 버전: `c28f122e6c55e72abbe23deb171979455685ae53`.
- 로컬 브랜치는 `week-02`다. 지난주 작업은 기존 `week-01` 브랜치에 보존되어 있다.
- Python `3.12.11`, OpenAI SDK `3.8.0`, Anthropic SDK `1.4.0`을 `uv`의 격리된 환경에서 사용한다. `run_lab.sh`가 이 버전을 지정한다.
- 태스크와 성공 기준은 실험 전 커밋에 들어 있다. `app.log`와 `TASK.md`는 공식 시작 코드와 동일하다.
- API 키는 코드나 파일에 저장하지 않는다. 실제 모델 실행 결과·로그·보고서는 아직 만들지 않았다.

## 무엇을 하는 실습인가

같은 로그 파일을 두 방식으로 분석하고 차이를 측정한다.

| 방식 | 진행 순서 |
|---|---|
| ReAct | 상황 판단 → 도구 호출 → 결과 관찰 → 다음 판단을 반복 |
| Plan-then-Execute | 먼저 전체 계획 작성 → 각 단계 실행 → 필요하면 한 번 재계획 |

공통 질문은 “`app.log`에서 ERROR가 가장 많이 발생한 시간대는?”이다. 공식 성공 기준은 `14:00`이며 해당 시간의 ERROR는 6건이다. 성공·실패뿐 아니라 토큰·모델 호출 횟수·사람 개입 횟수를 비교한다.

## 시작하기

터미널에서 이 폴더로 이동한다.

```bash
cd '/Users/sungjinho/Documents/Obsidian_Raw/jin_icloud2V2_raw/Vscode_Raw/20260529_cleaned_workspace/20_Study_Coursework/ai-agent-engineering-101/submissions/25620027/week-02'
./run_lab.sh check
```

`check`는 API 키 없이 모듈과 로컬 도구가 동작하는지만 확인한다. 모델을 호출하거나 `results.csv`를 만들지 않는다.

### 모델 연결 — 사용할 제공자 한 가지 선택

공식 과제는 OpenRouter를 통한 무료 모델 사용을 안내한다. 아래 모델명은 공식 README의 예시이며, 실제 사용 가능 여부·무료 한도는 실행 시 서비스에서 확인한다. API 키는 해당 제공자의 키를 **자신의 터미널 환경변수**에만 설정한다. 채팅이나 Git 파일에 붙여 넣지 않는다.

OpenRouter를 사용할 경우:

```bash
unset ANTHROPIC_API_KEY
export OPENAI_BASE_URL='https://openrouter.ai/api/v1'
export AGENT_MODEL='nvidia/nemotron-3.5-lightning:free'
# 이 터미널의 OPENAI_API_KEY 환경변수에 본인의 OpenRouter 키를 설정한다.
```

OpenAI를 직접 사용할 경우:

```bash
unset ANTHROPIC_API_KEY
export OPENAI_BASE_URL='https://api.openai.com/v1'
export AGENT_MODEL='gpt-4o-mini'
# 이 터미널의 OPENAI_API_KEY 환경변수에 본인의 OpenAI 키를 설정한다.
```

Anthropic을 사용할 경우 공식 코드의 `ANTHROPIC_API_KEY`를 설정하고, 필요하면 `AGENT_MODEL`을 지정한다. OpenAI/OpenRouter 예시의 모델명이 남지 않도록 선택한 제공자에 맞춘다. 공식 코드는 `ANTHROPIC_API_KEY`가 있으면 이를 우선 사용한다.

`run_lab.sh`는 키·제공자·모델을 자동으로 바꾸지 않는다. API 키가 두 제공자 모두 없으면 실험 시작 전에 중단한다. 제공자 주소와 키를 맞추고 A/B 동안 모델을 바꾸지 않는다.

### 실제 실험

```bash
./run_lab.sh run --runs 3
```

- ReAct 3회, Plan-then-Execute 3회로 총 6회 실행된다.
- 결과표는 `results.csv`, 각 실행 기록은 `logs/`에 생성된다.
- 실패 결과도 그대로 보존한다. 다시 실행하면 CSV에 행을 추가한다.
- 모델·태스크·도구·설정을 실험 중 바꾸지 않는다. 의도적으로 바꾼 경우 비교 조건을 명확히 분리해 기록한다.
- 사용한 제공자·모델명과 실행 명령은 보고서에 적는다. API 키는 적지 않는다.

### 실험 후

`REPORT.md`에 ① 하네스 차이 ② 측정표 ③ 로그 근거를 든 해석을 1쪽으로 작성하고 검사한다.

```bash
./run_lab.sh submission-check
```

실험 전에는 `results.csv`, 6개 로그, `REPORT.md`가 없으므로 제출 검사를 통과하지 않는 것이 정상이다. 수업 전 `check` 통과와 과제 제출 검사 통과는 서로 다르다.

마감은 3주차 수업 시작 전 PR 생성 시각 기준이다. 기존 시간표 기준 2026-09-15(화) 19:00 전이며, 변경 공지가 있으면 공지를 우선한다. 이 준비 작업에서는 외부 푸시나 PR을 만들지 않았다.

## 수업 중 코드를 볼 순서

1. `TASK.md`: 입력과 성공 판정 기준.
2. `tools_shared.py`: 두 하네스가 공유하는 도구·모델 연결·측정기.
3. `harness_react.py`: 매 호출 뒤 관찰을 누적하는 루프, 최대 8단계.
4. `harness_plan_execute.py`: 계획 생성, 단계 실행, 최대 1회 재계획.
5. `run_ab.py`: 반복 실행, 성공 판정, CSV와 로그 저장.

## 실제 시작 코드에서 알아둘 점

- 두 도구는 `read_file(path)`와 `count_pattern(path, pattern)`이다. 웹 설명의 간략 예제와 달리 집계 도구도 **파일 경로**를 받는다.
- `iters`는 도구 호출 수가 아니라 모델 호출 수다. 계획 생성·재계획·최종 답변 호출도 포함된다.
- 기본 도구는 읽기 전용이므로 `interventions=0`이 정상이다. 0이라는 이유로 지표가 고장 났다고 보지 않는다.
- 성공 판정은 최종 답에 `14:00` 문자열이 있는지를 검사한다. 답변의 의미까지 완전히 판정하는 방식은 아니므로 실제 답과 로그도 읽는다.
- 일부 도구 결과·답변은 로그에 앞부분만 기록된다. 저장된 콘솔 로그를 전체 관찰 내용과 동일시하지 않는다.
- 시작 코드의 설계와 한계를 관찰하는 단계다. 수정이 필요하면 자신의 제출 폴더에서 변경 이유와 전후 실행을 기록한다.

## 준비 확인 기록

- 2026-09-08: 공식 starter 원본과 복사본 6개 일치 확인.
- 2026-09-08: 원본 코드의 실행 방식·입력·측정 정의를 검토하고 실행 환경을 준비.
- 2026-09-08: `./run_lab.sh check` 통과. 실제 출력은 `SETUP_CHECK.txt`에 보존했다.
- 2026-09-08: 공식 제출 검사의 문법·공통 도구 import·TASK 항목 통과. 결과표·6개 실행 로그·보고서는 실험 전이라 미충족 3건으로 확인했다.
- 현재 모델 실행 환경에 API 키 없음. 사전 점검만 실행하며 실제 A/B 결과를 생성하지 않음.
