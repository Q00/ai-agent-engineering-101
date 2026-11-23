# A2A 실습

강의의 echo → hello → 규칙 기반 판매자 → 단일 하네스 응답 순으로 확인한다.
이 실습은 최종 과제의 모델 기반 협상 구현과 구분한다.

## 파일 구성

- `reference/`: `weeks/week-06/lab/`의 원본 3개 파일을 그대로 복사
- `requirements.txt`: 강의 참조 버전의 Python 패키지
- `run_lab.py`: 로컬 서버와 공식 CLI를 시작하고 강의 체크포인트 확인
- `openai_smoke.py`: stdin 프롬프트를 OpenAI Responses API에 보내는 단일 턴 실습 하네스
- `../.venv/`: 로컬 가상환경 (커밋 제외)
- `../.runtime/`: 공식 CLI 실행파일·다운로드·빈 하네스 작업 폴더 (커밋 제외)
- `../logs/lab/`: 실행별 원본 CLI·서버 로그와 요약

원본: `weeks/week-06/lab/hello_agent.py`, `seller_agent.py`, `lab_cli.sh`.
공식 CLI: https://github.com/a2aproject/a2a-cli (실습 버전 v0.3.0).

## 실행 (Windows PowerShell)

6주차 제출 폴더를 현재 디렉터리로 두고 실행한다.

```powershell
# 처음 한 번: Python 3.10+ 실행파일을 지정할 수 있다.
.\lab\setup.ps1 -Python python

# 가상환경을 활성화하지 않고도 실행 가능하다.
.\.venv\Scripts\python.exe lab\run_lab.py
```

이 컴퓨터에서는 Python이 PATH에 없어 Codex에 포함된 Python 3.12.14를
`setup.ps1 -Python` 인자로 지정했다. OS의 PATH나 기본 Python은 바꾸지 않았다.
다른 컴퓨터에서는 설치된 Python 경로를 사용하면 된다.

설치는 공식 CLI v0.3.0 Windows x64 압축파일의 SHA256을 확인하며,
Python 의존성의 실제 설치 버전은 `requirements-lock.txt`에 보관한다.
동일 버전 재현은 가상환경에서 `python -m pip install -r lab/requirements-lock.txt`로 가능하다.

기본 실습은 모델을 호출하지 않는다. 사용 포트는 echo 8080, hello 9000,
판매자 9999이며 이미 사용 중이면 기존 프로세스를 건드리지 않고 중단한다.
직접 시작한 서버만 실습 종료 시 종료한다.

결과와 실패 원인: `RESULTS.md`. 매 실행마다 새 로그 디렉터리를 생성한다.

## 단일 하네스 응답 실습의 현재 상태

프로토콜 실습 22개와 API 하네스 연결 4개 확인 항목이 통과했다.
이전에 Claude는 조직의 구독 접근 제한, Codex CLI는 설정 모델의 ChatGPT 계정 접근 오류로 종료됐다.
이 실패 로그도 삭제하거나 수정하지 않고 보존했다.

사용자는 기존 OpenAI API 키를 로컬 설정으로 사용하는 방식을 선택했다.
`.env.example`은 공개 가능한 빈 설정 예시이며, 실제 키는 Git에서 제외되는 `.env`에만 둔다.
`AGENT_MODEL`의 기본값은 이전 과제와 같은 `gpt-4o-mini`이다.
키를 설정한 뒤 다음 명령으로 단일 응답 실습을 실행할 수 있다.

```powershell
.\.venv\Scripts\python.exe lab\openai_smoke.py --describe
.\.venv\Scripts\python.exe lab\run_lab.py --harness-only --harness openai --shell-directory 'C:\Program Files\Git\bin'
```

첫 명령은 키의 존재 여부와 모델명만 출력하며 API를 호출하지 않는다.
두 번째 명령은 A2A CLI의 `--exec`로 단일 턴 API 하네스를 감싸고,
요청 → Responses API → `TASK_STATE_COMPLETED` + 텍스트 artifact를 확인한다.
CLI v0.3.0의 `--exec`가 `sh -c`를 사용하므로 Windows에서는 Git의 sh.exe 경로를 지정했다.
요청마다 새 task와 단일 응답이 생성되며 협상 기록은 유지하지 않는다.
다중 턴 구매자·판매자는 다음 단계에서 설계한다.

API 호출은 60초 제한·출력 최대 128토큰·서버 저장 비활성화로 실행한다.
인증 헤더·키 값·실패한 API의 원본 응답 본문은 로그에 쓰지 않는다.
성공 시 `api-call.json`에는 모델·사용 토큰·응답 결과만 기록한다.

실행 결과: `logs/lab/20261017T040106.607650Z/`.
공식 API 설명: https://developers.openai.com/api/docs/guides/text
