# Week 01 — 23530007

세 개의 도구(`calculator`, `read_file`, `clock`)를 가진 에이전트. 새 도구는 `clock`.
해결한 태스크: `notes.txt`(회식 지출 메모)를 읽어 총 지출, 미정산 금액, 정산 마감까지 남은 일수를 오늘 날짜 기준으로 보고한다.

## 재현 방법

```bash
pip install -r requirements.txt          # anthropic SDK
export ANTHROPIC_API_KEY=...             # 키는 환경 변수로만. 저장소에 쓰지 않는다.
cd submissions/23530007/week-01          # read_file은 작업 디렉터리 안만 허용하므로 여기서 실행
python first_agent.py                    # 기본 goal (first_agent.py 하단 DEFAULT_GOAL)
python first_agent.py "Read notes.txt and sum the numbers in it."
```

로그 4개를 한 번에 남기려면 `bash run_experiments.sh`.

## 설정

| 항목 | 값 |
|---|---|
| 모델 | `claude-sonnet-4-5` (env `AGENT_MODEL`로 변경 가능) |
| max_tokens | 1024 |
| max_steps | 8 |
| 도구 스키마 | `first_agent.py`의 `TOOLS` 리스트 그대로 |
| `CLOCK_DESC` | `full`(기본) / `terse` — clock 설명 두 버전 중 선택 |
| `DROP_CLOCK` | 설정하면 요청에서 clock을 빼고 2개 도구로 실행 (비교용) |

`clock`은 시스템 시계를 읽으므로 실행 시점에 따라 "남은 일수"는 달라진다. 로그 첫 줄의 `[config]`와 `[tool] clock(...)` 출력에 실행 시점이 남는다.
