# 기본 A/B 실행 전 설정

이 파일의 조건은 실행 전에 고정했다. 이 조건으로 새 기본 실험 6회를 실행했고 모두 최종 정답 기준을 통과했다.
학습자의 예상은 [CHECKLIST.md](CHECKLIST.md)에 실행 전에 기록했다.

| 항목 | 고정한 값 |
|---|---|
| 제공자 | OpenAI |
| 모델 | `gpt-5.6-luna` — 앞서 사용자가 선택한 모델 |
| API | Chat Completions, `https://api.openai.com/v1` |
| 추론 옵션 | 계획·재계획·실행·최종 답변 모두 `reasoning_effort=none` |
| 기타 OpenAI 생성 옵션 | `temperature`, `top_p`, 출력 토큰 상한 등을 코드에서 지정하지 않음; 서버 기본값 사용 |
| Python | `3.13.15` |
| SDK / 환경 로딩 | `openai==3.8.0`, `python-dotenv==1.2.3` |
| 전체 설치 의존성 | `requirements.txt`에 버전 고정 |
| 모델·키 설정 우선순위 | 프로세스 환경변수 → 이 폴더의 `.env` → 코드 기본값 |
| 기준 문제·정답 판정 | 원본 `TASK.md`와 `run_ab.py`의 `judge()` 그대로 사용 |
| 입력과 도구 | 원본 `app.log`, 공통 `read_file` / `count_pattern` |
| ReAct | 원본 `SYSTEM`, `max_steps=8`, 승인 대상 도구 없음 |
| Plan-then-Execute | 원본 `SYSTEM_PLAN`, `SYSTEM_EXEC`, `max_replan=1`, `max_tool_rounds=3` |
| 실행 순서 | 원본 runner 순서: ReAct 3회 → Plan-then-Execute 3회 |

OpenAI 공식 [모델 문서](https://developers.openai.com/api/docs/models/gpt-5.6-luna)는
`none` 추론 설정을 지원한다고 명시한다. 이전에 보존한 이 저장소의 시도에서는
이 모델의 Chat Completions 함수 도구 호출에 기본 추론 설정을 썼을 때 HTTP 400이 발생했고,
API 응답이 안내한 `none`으로 변경해 실행했다. 이번 준비에도 그 연결 설정을 공통 적용한다.
이전 실험 결과를 이번의 새 결과로 재사용하지 않는다.

두 하네스 본문, 시스템 프롬프트, `TASK.md`, `app.log`는 starter와 동일하다.
계획자와 실행자는 같은 모델을 쓰며, 모든 호출은 공통 `Meter`에 합산된다.
재계획 횟수는 `replans`로 별도 기록되고 사람 개입 횟수와 구분된다.

## 원본 대비 공통 연결 수정

- `tools_shared.py`: 이 폴더의 `.env` 로딩, 명시적 제공자 선택, OpenAI 추론 옵션 전달.
- 파일 도구: 환경 파일을 읽지 못하게 하고 경로를 해석한 뒤 작업 폴더 내부인지 확인.
  `app.log`에 대한 동작과 두 도구의 모델용 스키마는 원본과 같다.
- `run_ab.py`: API 호출 없는 `--check`, 결과 파일 생성 전 필수 설정 검사,
  실행별 로그에 키를 제외한 제공자·모델·추론 설정 기록.

## 준비와 실행

이 폴더에서 아래 명령을 사용한다. 현재 작업 폴더의 `.venv`와 `.env`는 준비되어 있다.
`.env`에는 비공개 백업의 OpenAI 키만 복구했으며 Git에서 제외된다.
다른 컴퓨터에서는 환경을 만들고 `.env.example`을 `.env`로 복사해 자신의 키를 넣는다.
API 키가 있는 `.env`는 공유하거나 커밋하지 않는다.

```bash
uv venv --python 3.13.15 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

로컬 준비 검사:

```bash
.venv/bin/python run_ab.py --check
```

수행한 기본 실험 명령:

```bash
.venv/bin/python run_ab.py --runs 3
```

`--runs 3`은 각 하네스 3회, 총 6회다. 실패한 실행도 `results.csv`와 `logs/`에 보존된다.
이번 파일에 고정한 모델 설정과 다른 환경변수가 출력된다면 실행 전에 차이를 확인한다.

## 실행 전 로컬 검증 범위

- Python 문법, 두 하네스의 공통 `Chat` 사용, 입력 파일·하네스 원본 일치 확인.
- 모의 SDK로 도구 사용 여부와 관계없이 동일 모델·추론 옵션 전달, 사용량 합산 확인.
- 환경 파일·작업 폴더 외부 접근 차단, 키가 없을 때 결과 생성 전 중단 확인.
- 실제 키는 로그·Git에 포함하지 않고 로컬 설정 여부만 확인.

위 로컬 검사는 API를 호출하지 않았다. 이후 실제 API로 수행한 기본 실험의 사용량과
성공 여부는 아래 원본 결과를 기준으로 해석한다.

## 기본 실험 완료 기록

- 실행 조건 커밋: `78ab120`; 원본 CSV와 로그 보존 커밋: `ab6ffb6`.
- [results.csv](results.csv)에 ReAct 3회, Plan-then-Execute 3회가 있다. 실행 중 코드·설정 변경은 없었다.
- 6개 실행 로그에 동일 제공자·모델·추론 설정과 최종 판정이 기록되어 있다.
- 두 하네스 모두 최종 정답 기준 3/3 성공. 새 실험의 API 연결 실패나 재실행은 없었다.
- 평균 모델 호출은 ReAct 2회, Plan-then-Execute 6회였다. 평균 토큰은 각각 1812.3, 7341.0이다.
- Plan-then-Execute 실행 5의 중간 집계 오류도 원본 로그에 보존했다. 최종 시간대 판정과 구분한다.
- 상세 측정표와 해석은 [REPORT.md](REPORT.md)에서 다룬다.
