# Week 02 작업 준비

학번: `26510358` · 브랜치: `week-02`

강의 원본 커밋 `404ec973a4e77cc074299865fd130055abdb5e18`의 starter를
복사해 시작했다. `TASK.md`의 문제와 성공 기준은 실험 전에 커밋했다.
두 하네스는 원본을 유지한다. 공용 모듈에는 `.env` 로딩과 제공자 선택을,
파일 도구에는 `.env` 및 작업 폴더 외부 접근 차단을,
`run_ab.py`에는 실행 전 준비 검사를 추가했다.
제공자는 OpenAI를 사용하며, 모델은 사용자가 `.env`에 설정한 `gpt-5.6-luna`를 사용한다.
최초 두 실행은 API 호환성 오류로 실패했고, 원본 결과와 로그를 보존했다.
Chat Completions의 함수 도구를 사용하기 위해 `AGENT_REASONING_EFFORT=none`을
양쪽에 동일하게 적용한 뒤 비교 실험을 진행한다.

## 환경 준비

이 디렉터리에서 실행한다. 준비 환경은 Python `3.13.15`이며,
설치한 SDK와 의존성 버전은 `requirements.txt`에 고정했다.

```bash
uv venv --python 3.13.15 .venv
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
```

현재 작업 폴더에는 키 입력용 `.env`가 준비되어 있다.
`OPENAI_API_KEY=` 뒤에 키를 입력하고 저장하면 두 하네스가 자동으로 읽는다.
새로 clone한 환경에서는 `.env.example`을 `.env`로 복사해 사용한다.
`.env`는 Git에서 제외되며 키가 없는 `.env.example`만 공유한다.

`AGENT_PROVIDER=openai`가 OpenAI 사용을 명시하며, 모델명은 `AGENT_MODEL`로 지정한다.
`.env`는 공용 모듈과 같은 디렉터리에서만 읽고, 이미 설정된 프로세스 환경변수가 우선한다.
`AGENT_PROVIDER`를 생략하면 starter와 같이 `ANTHROPIC_API_KEY`의 존재 여부로 제공자를 고른다.
OpenAI SDK는 `OPENAI_API_KEY` 환경변수를 자동으로 읽는다.
([공식 OpenAI 문서](https://developers.openai.com/api/docs/quickstart))
실험에 사용한 제공자, 모델명, 키를 제외한 설정은 `REPORT.md`에 기록한다.

환경변수를 설정한 뒤 아래 명령으로 준비 상태를 확인한다.

```bash
python run_ab.py --check
```

이 검사는 문제 정의, 입력 파일, SDK 설치와 API 키 설정 여부를 확인한다.
API를 호출하지 않으므로 키의 유효성이나 모델 접근 권한까지 확인하지는 않는다.
일반 실행에서도 결과 파일을 만들기 전에 준비 상태를 검사한다.
키가 없으면 실험 실패 행을 만들지 않고 설정 오류로 종료한다.

## 실험과 제출 순서

실행 전 조건과 측정의 범위는 [EXPERIMENT_PLAN.md](EXPERIMENT_PLAN.md)에 기록했다.

1. 두 하네스를 읽고 비교할 설계 차이를 확인한다. 모델·문제·도구는 동일하게 유지한다.
2. 설정을 확정하고 커밋한 뒤 `python run_ab.py --runs 3`으로 각 하네스를 3회 실행한다.
3. 생성된 `results.csv`와 `logs/`를 보존하고 커밋한다. 재실행 결과는 CSV에 추가되며 실패도 남긴다.
4. `REPORT.md`에 설계 차이, 측정표, 로그에 근거한 해석을 작성한다.
5. 저장소 루트에서 아래 검사를 통과한 뒤 upstream에 PR을 연다. PR 제목은 `[week-02] 26510358`이다.

```bash
python3 scripts/check_week02.py submissions/26510358/week-02
```

`results.csv`와 `logs/`에 모든 실제 실행을 보존한다. `REPORT.md`는 아직 작성 전이다.
전체 제출 검사는 필요한 실행 횟수와 보고서를 갖춘 뒤 통과할 수 있다.
`app.log`와 실행 전 성공 기준을 유지하고, 실패 기록을 삭제하거나 커밋을 squash하지 않는다.
