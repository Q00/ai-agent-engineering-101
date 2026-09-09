## What I built

week-02 starter를 제출 폴더에 복사한 뒤, 원본 `app.log`에서 ERROR가 가장 많이 발생한
시간대를 찾는 문제로 ReAct와 Plan-then-Execute를 비교함.
두 하네스에 동일한 OpenAI `gpt-5.6-luna`, `reasoning_effort=none`, 입력 파일과
`read_file`·`count_pattern` 도구를 적용함.
`TASK.md`에 실행 전에 기록한 기준에 따라 최종 답변에 `14:00`이 포함되면 성공으로 판정함.

각 하네스를 3회씩 실행했으며, 6회 전체 결과는 아래와 같음.

| 방식 | 성공 / 실행 수 | 평균 토큰 | 평균 모델 호출 | 평균 사람 개입 |
|---|---|---:|---:|---:|
| ReAct | 3/3 | 1,812.3 | 2회 | 0회 |
| Plan-then-Execute | 3/3 | 7,341.0 | 6회 | 0회 |

6회 모두 파일 읽기 도구를 1회 사용했고 `count_pattern`은 선택하지 않았음.
ReAct는 파일 읽기 요청 후 다음 모델 호출에서 종료함. Plan-then-Execute는
계획 생성, 3개 단계의 처리, 별도 최종 답변 과정에서 6회 호출함.
세 실행 모두 재계획은 0회였으므로 이번 추가 호출의 원인은 재계획이 아니었음.

- 원본 측정값: [results.csv](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/ab6ffb6/submissions/26510358/week-02/results.csv)
- 대표 로그: [ReAct 실행 1](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/ab6ffb6/submissions/26510358/week-02/logs/react-01.txt), [Plan-then-Execute 실행 4](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/ab6ffb6/submissions/26510358/week-02/logs/plan_exec-04.txt)
- 설계 비교·측정표: [REPORT.md](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/week-02/submissions/26510358/week-02/REPORT.md)
- 코드 구조와 Mermaid: [CODE_WALKTHROUGH.md](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/week-02/submissions/26510358/week-02/CODE_WALKTHROUGH.md)

**현재 Draft 상태임.** 측정과 로그 확인은 완료했으며, `REPORT.md`의 본인 해석 문단 작성 중임.

## What I tried and discarded

두 하네스 본문·시스템 프롬프트·`TASK.md`·`app.log`를 starter 원본대로 유지함.
공통 모듈에 환경 파일 로딩, 제공자 선택, OpenAI 추론 옵션, 환경 파일·외부 경로 접근 제한을
추가함. runner에는 로컬 준비 검사와 실행별 모델 설정 기록을 추가함.
실행 전 예상과 비교 조건을 기록한 뒤, 고정된 조건으로 각 하네스를 3회씩 실행함.
이번 6회는 모두 정상 종료했으며, 하네스를 재실행하거나 기록을 제외하지 않았음.

[Plan-then-Execute 실행 5](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/ab6ffb6/submissions/26510358/week-02/logs/plan_exec-05.txt)는
14시 ERROR를 실제 6개 대신 7개로 중간 집계함. 최종 시간대는 맞아 사전 기준상
성공으로 판정됨. 다만 이 판정이 중간 계산의 정확성까지 보장하지는 않음.
해당 오류를 포함해 모든 실행 로그를 원본 그대로 보존함.

코드 탐색·환경 구성·실행·측정 정리에는 코딩 어시스턴트의 도움을 받음.
실행 전 본인 예상은 계획과 반복 때문에 Plan-then-Execute의 모델 호출이 더 많을 것이라는 내용이었음.
학습 과정과 남은 작업은
[CHECKLIST.md](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/week-02/submissions/26510358/week-02/CHECKLIST.md)에 기록함.
이번 결과는 한 로그 파일, 각 3회, ReAct 먼저 실행한 조건에 한정됨.

## How to run

Python 3.13.15, OpenAI SDK 3.8.0을 사용함. 전체 의존성 버전은 `requirements.txt`,
생성 옵션과 공통 연결 수정 내역은
[RUN_SETTINGS.md](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/week-02/submissions/26510358/week-02/RUN_SETTINGS.md)에 기록함.

새로 받은 저장소에서는 아래 명령으로 환경을 준비하면 됨.

```bash
cd submissions/26510358/week-02
uv venv --python 3.13.15 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

`.env.example`을 참고해 로컬 `.env`에 `OPENAI_API_KEY`를 넣고,
`AGENT_PROVIDER=openai`, `AGENT_MODEL=gpt-5.6-luna`, `AGENT_REASONING_EFFORT=none`,
`OPENAI_BASE_URL=https://api.openai.com/v1`을 설정하면 됨. 키 값과 가상환경은 Git에서 제외함.

```bash
.venv/bin/python run_ab.py --check
.venv/bin/python run_ab.py --runs 3
```

`--check`는 API를 호출하지 않음. `--runs 3`은 실제 API로 각 하네스를 3회 실행하고
`results.csv`에 결과를 추가하며 실행별 로그를 저장함.

공식 구조 검사는 저장소 루트에서 커밋된 제출 파일만 내보내 수행함.

```bash
week02_check_dir=$(mktemp -d)
git archive HEAD submissions/26510358/week-02 | tar -x -C "$week02_check_dir"
python3 scripts/check_week02.py "$week02_check_dir/submissions/26510358/week-02"
```

## Checklist

- [x] 커밋된 제출 파일 대상으로 `scripts/check_week02.py` 로컬 검사 통과함
- [x] 실행 로그 6개를 `logs/`에 커밋함
- [x] diff에 API 키 없음
- [x] 커밋 이력을 squash하지 않음
