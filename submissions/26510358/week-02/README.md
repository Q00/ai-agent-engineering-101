# Week 02 작업 준비

학번: `26510358` · 브랜치: `week-02`

강의 원본 커밋 `404ec973a4e77cc074299865fd130055abdb5e18`의 starter를
복사해 시작했다. `TASK.md`의 문제와 성공 기준은 실험 전에 커밋했다.
두 하네스와 공용 도구는 원본을 유지하며, `run_ab.py`에는 실행 전 준비 검사를 추가했다.
모델 제공자와 모델명은 아직 선택하지 않았으며 실제 A/B 실험은 실행 전이다.

## 환경 준비

이 디렉터리에서 실행한다. 준비 환경은 Python `3.13.15`이며,
설치한 SDK와 의존성 버전은 `requirements.txt`에 고정했다.

```bash
uv venv --python 3.13.15 .venv
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
```

실험 전에 사용할 제공자의 API 키와 `AGENT_MODEL`을 환경변수로 설정한다.
`ANTHROPIC_API_KEY`가 있으면 Anthropic을 우선 사용한다. 그렇지 않으면
`OPENAI_API_KEY`를 사용하는 OpenAI 호환 API를 사용하며, 다른 호환 서버는
`OPENAI_BASE_URL`로 지정한다. 키는 파일이나 커밋에 기록하지 않는다.
실험에 사용한 제공자, 모델명, 설정은 `REPORT.md`에 기록한다.

환경변수를 설정한 뒤 아래 명령으로 준비 상태를 확인한다.

```bash
python run_ab.py --check
```

이 검사는 문제 정의, 입력 파일, SDK 설치와 API 키 설정 여부를 확인한다.
API를 호출하지 않으므로 키의 유효성이나 모델 접근 권한까지 확인하지는 않는다.
일반 실행에서도 결과 파일을 만들기 전에 준비 상태를 검사한다.
키가 없으면 실험 실패 행을 만들지 않고 설정 오류로 종료한다.

## 실험과 제출 순서

1. 두 하네스를 읽고 비교할 설계 차이를 확인한다. 모델·문제·도구는 동일하게 유지한다.
2. 설정을 확정하고 커밋한 뒤 `python run_ab.py --runs 3`으로 각 하네스를 3회 실행한다.
3. 생성된 `results.csv`와 `logs/`를 보존하고 커밋한다. 재실행 결과는 CSV에 추가되며 실패도 남긴다.
4. `REPORT.md`에 설계 차이, 측정표, 로그에 근거한 해석을 작성한다.
5. 저장소 루트에서 아래 검사를 통과한 뒤 upstream에 PR을 연다. PR 제목은 `[week-02] 26510358`이다.

```bash
python3 scripts/check_week02.py submissions/26510358/week-02
```

현재 `results.csv`, `logs/`, `REPORT.md`는 실험 전이므로 없다.
이 상태에서는 전체 제출 검사가 통과하지 않는다.
`app.log`와 실행 전 성공 기준을 유지하고, 실패 기록을 삭제하거나 커밋을 squash하지 않는다.
