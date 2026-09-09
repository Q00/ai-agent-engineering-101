## 무엇을 만들었는가

원본 `app.log`에서 ERROR가 가장 많이 발생한 시간대를 찾는 문제로 ReAct와
Plan-then-Execute를 비교했습니다. 두 하네스에 동일한 OpenAI `gpt-5.6-luna`,
`reasoning_effort=none`, 입력 파일과 `read_file`·`count_pattern` 도구를 적용했습니다.
`TASK.md`에 실행 전에 기록한 기준에 따라 최종 답변에 `14:00`이 포함되면 성공으로 판정했습니다.

각 하네스를 3회씩 실행한 6회 전체 결과는 다음과 같습니다.

| 방식 | 성공 / 실행 수 | 평균 토큰 | 평균 모델 호출 | 평균 사람 개입 |
|---|---|---:|---:|---:|
| ReAct | 3/3 | 1,812.3 | 2회 | 0회 |
| Plan-then-Execute | 3/3 | 7,341.0 | 6회 | 0회 |

6회 모두 파일 읽기 도구를 1회 사용했고 `count_pattern`은 선택하지 않았습니다.
ReAct는 파일 읽기 요청 후 다음 모델 호출에서 종료했습니다. Plan-then-Execute는
계획 생성, 3개 단계의 처리, 별도 최종 답변 과정에서 6회 호출했습니다.
세 실행 모두 재계획은 0회였으므로 이번 추가 호출을 재계획 때문으로 해석할 수는 없습니다.

- 원본 측정값: [results.csv](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/ab6ffb6/submissions/26510358/week-02/results.csv)
- 대표 로그: [ReAct 실행 1](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/ab6ffb6/submissions/26510358/week-02/logs/react-01.txt), [Plan-then-Execute 실행 4](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/ab6ffb6/submissions/26510358/week-02/logs/plan_exec-04.txt)
- 설계 비교·측정표: [REPORT.md](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/week-02/submissions/26510358/week-02/REPORT.md)
- 코드 구조와 Mermaid: [CODE_WALKTHROUGH.md](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/week-02/submissions/26510358/week-02/CODE_WALKTHROUGH.md)

**현재 Draft입니다.** 측정과 로그 확인은 완료했으며, `REPORT.md`의 본인 해석 문단을 작성 중입니다.

## 시도하고 폐기한 것

처음에는 GPT·Gemini·Solar를 이용한 하네스 개선으로 범위를 넓혔으나, 기본 A/B 실습에
집중하기 위해 초기화하고 starter부터 다시 시작했습니다. 이전 API 호환성·응답 형식 실패와
초기화 과정은 커밋 이력에 남겼고 squash하지 않았습니다.

이번 기본 실험에서는 두 하네스 본문·시스템 프롬프트·`TASK.md`·`app.log`를 원본대로 유지했습니다.
공통 모듈에 환경 파일 로딩, 제공자 선택, OpenAI 추론 옵션, 환경 파일·외부 경로 접근 제한을
추가했고, runner에는 로컬 준비 검사와 실행별 모델 설정 기록을 추가했습니다.
실행 중 조건을 바꾸지 않았고 이번 6회는 모두 정상 종료했습니다.
하네스를 재실행하거나 기록을 제외하지 않았습니다.

[Plan-then-Execute 실행 5](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/ab6ffb6/submissions/26510358/week-02/logs/plan_exec-05.txt)는
14시 ERROR를 실제 6개 대신 7개로 중간 집계했습니다. 최종 시간대는 맞아 사전 기준상
성공이지만, 그 판정이 중간 계산의 정확성까지 보장하지는 않습니다. 해당 로그도 수정하지 않았습니다.

코드 탐색·환경 구성·실행·측정 정리에는 코딩 어시스턴트의 도움을 받았습니다.
실행 전에는 직접 “Plan-then-Execute는 계획하고 반복하기 때문에 모델 호출이 더 많을 것으로
예상한다”라고 기록했습니다. 학습 과정과 남은 작업은
[CHECKLIST.md](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/week-02/submissions/26510358/week-02/CHECKLIST.md)에 있습니다.
이번 결과는 한 로그 파일, 각 3회, ReAct 먼저 실행한 조건에 한정합니다.

## 실행 방법

Python 3.13.15, OpenAI SDK 3.8.0을 사용했습니다. 전체 의존성 버전은 `requirements.txt`,
생성 옵션과 공통 연결 수정 내역은
[RUN_SETTINGS.md](https://github.com/SUNGMYEONGGI/ai-agent-engineering-101/blob/week-02/submissions/26510358/week-02/RUN_SETTINGS.md)에 기록했습니다.

새로 받은 저장소에서는 다음과 같이 환경을 준비합니다.

```bash
cd submissions/26510358/week-02
uv venv --python 3.13.15 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

`.env.example`을 참고해 로컬 `.env`에 `OPENAI_API_KEY`를 넣고,
`AGENT_PROVIDER=openai`, `AGENT_MODEL=gpt-5.6-luna`, `AGENT_REASONING_EFFORT=none`,
`OPENAI_BASE_URL=https://api.openai.com/v1`을 설정합니다. 키 값과 가상환경은 Git에서 제외합니다.

```bash
.venv/bin/python run_ab.py --check
.venv/bin/python run_ab.py --runs 3
```

`--check`는 API를 호출하지 않습니다. `--runs 3`은 실제 API로 각 하네스를 3회 실행하고
`results.csv`에 결과를 추가하며 실행별 로그를 저장합니다.

공식 구조 검사는 저장소 루트에서 커밋된 제출 파일만 내보내 수행했습니다.

```bash
week02_check_dir=$(mktemp -d)
git archive HEAD submissions/26510358/week-02 | tar -x -C "$week02_check_dir"
python3 scripts/check_week02.py "$week02_check_dir/submissions/26510358/week-02"
```

## 체크리스트

- [x] 동일 모델·태스크·입력·도구로 하네스별 3회 실행
- [x] 성공 기준과 조건을 실행 전에 커밋
- [x] 원본 `results.csv`와 실행 로그 6개 커밋
- [x] 실행 방법·모델 옵션·의존성 버전 기록
- [x] 로컬 공식 구조 검사 및 GitHub `pr-check` 통과 확인
- [x] 변경 범위는 `submissions/26510358/week-02/` 안에만 있음
- [x] 제출 파일에 API 키가 없고 기존 이력을 squash하지 않음
- [ ] `REPORT.md`의 본인 해석 문단 완성
- [ ] 최종 내용을 확인한 뒤 Ready for review로 전환
