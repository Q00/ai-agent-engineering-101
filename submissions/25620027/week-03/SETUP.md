# 실행 및 재개

## 설치와 실행

Python 3.12.11과 uv를 사용한다. 의존성 전체 버전은 `uv.lock`으로 고정했다.

```bash
cd submissions/25620027/week-03
./run_lab.sh --config config-nemotron.json --output experiments/nemotron-free --runs 3
```

Mac의 기존 개인 설정 파일 `~/.config/ax-agent/openrouter.env`에서 키만 읽는다. 이 파일을 제출 폴더에 복사하지 않는다. API 주소와 모델은 환경변수보다 **선택한 설정 파일(`--config`)을 기준**으로 한다.

다른 컴퓨터에서는 OPENAI_API_KEY를 안전하게 환경변수로 설정한 뒤 실행한다.

```bash
AX_LAB_ENV_FILE='' ./run_lab.sh --config config-nemotron.json --output experiments/nemotron-free --runs 3
```

Windows PowerShell에서는 환경변수 설정 후 `uv run --frozen --python 3.12.11 python run.py --config config-nemotron.json --output experiments/nemotron-free --runs 3`을 실행한다. 실제 Windows 실행은 미검증이다.

현재 채점용 `results.csv`는 GLM 실패 시도와 Nemotron 비교 실행을 함께 모은 집계본이다. 각 실행의 원본은 `runs/`(GLM)와 `experiments/nemotron-free/runs/`에 남는다. `run` 값은 `glm-free:1`처럼 실험 이름과 원본 번호를 합친 식별자다.

실행이 끝난 뒤 제출용 결과표를 갱신한다. 기존 로그를 수정하지 않고 바이트가 같은 사본을 만든다.

```bash
uv run --frozen python collect_results.py
```

초기 `config.json`과 루트 `experiment.json`은 GLM 실패 당시 설정의 기록이다. 현재 구현에서 그 출력 폴더로 다시 실행하면 소스 변경 검사가 거절한다. 위 Nemotron 전용 명령을 사용한다.

## 현재 중단 지점

2026-09-16 09:29 KST 무료 Nemotron 호출도 HTTP 429로 중단됐다. baseline·homogeneous 각 1회 완료, overconfident는 실패 보존 상태다. 위 명령을 다시 실행하면 **baseline 2회·homogeneous 2회·overconfident 3회**를 추가한다. 제한이 해제되지 않았으면 다시 실패 행을 남기고 멈춘다. 응답만으로 재개 가능 시각을 확인하지 못했으므로 반복적인 즉시 재시도는 하지 않는다. 현재 과제의 반복 횟수 요건은 미충족이다.

## 같은 실험을 이어가기

같은 명령을 다시 실행하면 원본 로그를 덮어쓰지 않고 조건별로 부족한 **완료 실행**을 추가한다. 429·401·402가 발생하면 현재 시도를 실패로 기록하고 배치가 종료 코드 2로 멈춘다. 오류 응답을 정상 불입찰로 바꾸거나 9행을 채우기 위해 무의미한 요청을 반복하지 않는다.

태스크·설정·코드를 바꾸면 기존 실험에 섞어 넣을 수 없다. 별도 출력 폴더를 사용한다.

```bash
./run_lab.sh --config config-nemotron.json --output experiments/new-experiment --runs 3
```

프로세스가 강제 종료되면 다음 실행에서 미완료 meta를 실패 행으로 복구한다. 강제 종료로 `.run.lock` 디렉터리가 남았을 때는 실행 중인 프로세스가 없는지 먼저 확인하고 빈 잠금 디렉터리를 제거한다. 정상 종료·오류·Ctrl+C에서는 잠금을 해제한다.

유료 모델은 기본 차단되며, 사용자가 비용을 승인한 경우에만 config를 별도로 선택·확정하고 `--allow-paid`를 사용한다. 무료 모델에서 유료 모델로 자동 전환하지 않는다.

## 증거 파일

| 위치 | 내용 |
|---|---|
| experiment.json | 실제 설정, 태스크·소스 SHA-256 |
| results.csv | 공식 헤더의 누적 결과, 실패 counts는 공란 |
| logs/run-NNN-condition.txt | 원본 콘솔 기록: 설정·프롬프트·공고·응답·낙찰·종료 |
| runs/run-NNN/meta.json | 실행 번호와 조건 |
| runs/run-NNN/result.json | CSV의 원본 행 |
| runs/run-NNN/state.sqlite | 원자적으로 보존한 상태·메시지·이벤트 |
| runs/run-NNN/events.jsonl | 두 이벤트 스트림의 내보내기. 스트림 간 전역 순서로 해석하지 않는다 |
| runs/run-NNN/allocations.json | 완료된 실행의 태스크별 배정. 중단 실행에는 없음 |

## 검증 명령

제출 폴더에서:

```bash
uv run --frozen python -m pytest -q
uv run --frozen ruff check cnp tests run.py collect_results.py
uv run --frozen basedpyright
```

저장소 루트에서:

```bash
python3 scripts/check_week03.py submissions/25620027/week-03
```

공식 검사는 구조만 확인한다. API가 성공했는지, 보고서의 해석이 타당한지는 CSV와 원본 로그를 별도로 확인해야 한다. `--runs 3`의 최소 규모는 6태스크 × 3조건 × 3회 = 54개 계약, 162회 Contractor 호출이다. 네트워크 실패로 끝난 시도의 호출은 이 수에 추가된다.
