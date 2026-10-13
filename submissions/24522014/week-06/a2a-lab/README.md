# Week 06 · PART B: LAB

## STEP 1 — 준비 완료

- Python: 3.14.6 (강의 요구 조건: 3.10 이상)
- 가상환경: 이 폴더의 `.venv/` (Git에서 제외)
- a2a-sdk: 1.2.2, http-server extra 포함
- uvicorn: 0.54.0
- 공식 A2A CLI: 0.3.0 (Windows amd64)

Python 패키지는 이 실습의 가상환경에만 설치했다. CLI는 강의의 Windows
설치 명령을 따라 winget으로 설치했다. 공식 설치 안내:
https://github.com/a2aproject/a2a-cli#installation

저장소 루트에서 PowerShell 터미널을 열고 실행한다. CLI 설치 전에 실행한
VS Code나 Windows Terminal은 새 탭에도 이전 PATH를 전달할 수 있다.
`use-a2a.ps1`을 dot-source하면 현재 세션에 설치된 CLI 경로를 추가한다.
기존 PATH를 유지하므로 활성화된 Python 가상환경도 그대로 사용할 수 있다.

```powershell
cd submissions/24522014/week-06/a2a-lab
.\.venv\Scripts\Activate.ps1
. .\use-a2a.ps1
python --version
a2a version
```

PowerShell 실행 정책으로 활성화가 막히면 가상환경의 Python을 직접 사용한다.
활성화는 Python과 pip의 검색 경로를 바꾸는 편의 기능이다.

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
```

다른 컴퓨터에서는 다음 명령으로 다시 준비할 수 있다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
winget install --id a2aproject.a2acli --exact --source winget --version 0.3.0
```

`requirements.txt`는 강의 참조 버전, `requirements-lock.txt`는 설치 후
`pip freeze`로 기록한 전체 Python 의존성이다.

## 확인 결과와 과정

`pip check`는 `No broken requirements found.`를 출력했다.
이후 LAB에서 필요한 `create_agent_card_routes`, `create_jsonrpc_routes`,
`TaskUpdater`를 import했고 성공했다. `a2a version`은 `a2a 0.3.0`을 출력했다.
서버 실행과 메시지 전송은 STEP 2에서 진행한다.

첫 환경 확인에서는 샌드박스가 WindowsApps의 Python 실행을 차단했다.
승인된 실행으로 Python 3.14.6을 확인했다. winget의 최초 설치 여부 조회는
msstore 소스 오류가 있었으며, 설치에는 공식 패키지가 있는 `winget` 소스를
명시해 성공했다. 설치와 검증의 원본 출력은 다음 파일에 보존했다.

- `../logs/step1-python-setup.txt`
- `../logs/step1-cli-install.txt`
- `../logs/step1-verification.txt`

## 후속 수정 — PowerShell에서 a2a를 찾지 못하는 경우

사용자의 터미널에서 `CommandNotFoundException`이 발생했다. 실제 사용자
PATH에는 winget 패키지 경로가 등록되어 있고 해당 경로의 `a2a.exe`도
존재했다. 현재 터미널에 설치 전 PATH가 남아 있는 것으로 판단했다.
위의 `. .\use-a2a.ps1`로 현재 세션만 갱신하거나, 다음처럼 직접 실행한다.

```powershell
& "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\a2aproject.a2acli_Microsoft.Winget.Source_8wekyb3d8bbwe\a2a.exe" version
```

최초 `Start-Transcript` 로그는 외부 프로그램 출력이 빠져 있었다.
원본 로그는 그대로 보존하고, 후속 확인에서는 `Tee-Object`로 외부 프로그램
출력을 별도 로그 `../logs/step1-cli-path-fix.txt`에 저장했다.

STEP 1에는 모델 호출이나 API 키가 필요 없다. 제출용 seller/buyer를 아직
작성하지 않았으므로 전체 과제 검사와 trade test는 실행하지 않았다.

## STEP 2 — echo 서버 실행과 메시지 전송 완료

직접 실습하려면 PowerShell 터미널 두 개를 열고 **각각 이 폴더로 이동**한다.
CLI만 사용하므로 이 단계에는 Python 가상환경 활성화가 필요하지 않다.

터미널 1:

```powershell
. .\use-a2a.ps1
a2a server --echo --port 8080
```

`Listening on 127.0.0.1:8080`이 나오면 서버를 계속 실행해 둔다.
명령 프롬프트가 돌아오지 않는 것은 서버가 실행 중이기 때문이다.

터미널 2:

```powershell
. .\use-a2a.ps1
a2a send -a http://localhost:8080 "hello A2A"
a2a card get http://localhost:8080
```

직접 확인할 것:

1. `Task`와 `Context`에 ID가 표시되는가? ID는 실행할 때마다 달라진다.
2. `Status`가 `completed`인가?
3. `Artifacts`에 보낸 문자열 `hello A2A`가 나타나는가?
4. Agent Card에 `Echo Agent`와 인터페이스 주소가 표시되는가?
5. 확인 후 **터미널 1에서 Ctrl+C**로 서버를 종료한다.

메시지 하나로 task 하나가 만들어지고, echo 서버가 같은 문자열을
artifact로 반환하면서 task를 완료했다. 카드는 서버 이름과 통신
인터페이스를 알려 준다. 이 CLI의 echo 서버는 기본적으로 `HTTP+JSON`
인터페이스를 제공한다.

에이전트가 실제로 검증한 결과:

```text
Task:     01a134e2-569a-7711-91e0-ccb1d8ce0040
Context:  01a134e2-569a-7712-94c6-18c5f47c5be0
Status:   completed (2026-10-13T19:50:10+09:00)
Artifacts:
  [01a134e2-569a-7713-9984-4bedeb0ae448] hello A2A
History:
  [user] hello A2A
```

파일 내용까지 확인한 원본 로그 (재검증 실행):

- `../logs/step2-send-20261013-195132.txt`
- `../logs/step2-card-20261013-195132.txt`
- `../logs/step2-server-20261013-195132.stderr.txt`
- `../logs/step2-server-20261013-195132.stdout.txt`

두 번째 시도의 `Tee-Object` 로그는 외부 프로그램
출력을 담지 못해 비어 있었다. 화면의 성공 결과와 별개로 이 기록 문제를
확인한 뒤 `Start-Process`의 표준 출력 파일 리디렉션으로 다시 실행했다.
재검증 로그의 `completed`, `hello A2A`, `Echo Agent` 문자열까지 확인했다.
빈 로그도 수정하지 않고 이전 시도의 증거로 보존했다.

첫 시도는 서버를 띄웠지만 추가한 PowerShell `Invoke-WebRequest` 준비
확인이 제한 시간 내 성공하지 못했다. `Echo server readiness timed out.`
오류 후 서버를 종료했다. 원인은 확정하지 않았다. 두 번째 시도에서는
이 사전 검사를 빼고 강의의 CLI 명령으로 직접 검증했고 성공했다.
첫 서버 출력은 `step2-server-20261013-194848.*.txt`로 그대로 보존했다.
검증 후 에이전트가 시작한 서버는 종료했으므로 직접 실습할 때 다시 켠다.
