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

## STEP 3 — hello A2A 서버 완료

`hello_agent.py`는 강의의 STEP 3 예제를 저장한 파일이다.
`Hello.execute()`는 메시지를 읽고 task를 만든 다음
`Hello! You said: ...`라는 응답을 붙여 task를 완료한다.
Agent Card에는 `Hello Agent`, `Say hello` skill과 JSON-RPC 1.0 접속 주소를
적었다. handler와 Starlette app은 카드 조회 경로와 JSON-RPC 경로를 연결한다.
이 예제의 `cancel()`은 강의대로 빈 구현이므로 취소 동작을 검증하는 단계는 아니다.

직접 실습하려면 터미널 두 개를 열고 각각 다음 폴더로 이동한다.
코드 블록에는 프롬프트 표시인 `$`가 없으며, 큰따옴표는 양쪽 모두 입력한다.

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-06\a2a-lab
```

터미널 1에서 가상환경의 Python으로 서버를 실행한다.
가상환경이 활성화되어 있지 않아도 이 명령은 같은 환경을 사용한다.

```powershell
.\.venv\Scripts\python.exe hello_agent.py
```

`Uvicorn running on http://127.0.0.1:9000`이 보이면 켜 둔다.
터미널 2에서는 CLI 경로를 등록하고 카드 조회, 메시지 전송을 실행한다.

```powershell
. .\use-a2a.ps1
a2a card get http://127.0.0.1:9000
a2a send -a http://127.0.0.1:9000 "hi there"
```

직접 확인할 것:

1. 카드 이름 `Hello Agent`, 인터페이스 `JSONRPC`, skill `Say hello`.
2. 메시지 결과 `Status: completed`와 `Hello! You said: hi there`.
3. 터미널 1 로그의 `GET /.well-known/agent-card.json` 다음 `POST /`.
4. 다른 문자열을 보내서 `Hello! You said: ...`의 뒷부분이 바뀌는지 확인.
5. 끝나면 터미널 1에서 Ctrl+C로 종료.

STEP 2는 echo 결과를 artifact로 반환했고 이 예제는 task의 완료 메시지로
인사말을 반환한다. 따라서 이 단계에서는 `Artifacts` 표시가 없어도 정상이다.

실제 검증에서 문법 검사, 카드 조회, `hi there` 전송이 성공했고
서버 로그의 GET과 POST 모두 200 OK였다. 저장된 로그 내용도 확인했다.
검증 후 에이전트가 시작한 서버는 종료했다.

- `../logs/step3-syntax-20261013-201128.stdout.txt`와 `.stderr.txt` (성공 시 출력 없음)
- `../logs/step3-card-20261013-201128.txt`
- `../logs/step3-send-20261013-201128.txt`
- `../logs/step3-server-20261013-201128.stdout.txt`와 `.stderr.txt`

API 키나 모델 호출은 필요 없다. 과제용 seller/buyer의 구현과 전체 과제
검사는 아직 진행하지 않았다.

## STEP 4 — 가게 서버 준비 완료

`seller_agent.py`는 강의의 STEP 4 예제를 그대로 저장한 규칙 기반 가게다.
모델을 호출하지 않으며, 과제에서 요구하는 하네스 기반 seller는 아니다.
판매 품목과 하한가는 lamp 100, chair 180, bookshelf 260이다.
첫 제시가는 하한가의 1.3배를 올림한 값이며 lamp는 130이다.
같은 task의 거래 상태를 `self.open`에 기억한다. 낮은 가격에는 역제안하고,
하한가 이상의 제안에는 영수증 artifact와 함께 완료한다.
낮은 제안이 세 번 오면 거절하고, 취소 요청도 처리한다.
실제로 이 상태들을 만들어 보는 거래 실습은 STEP 5에서 진행한다.

직접 실습할 때 두 터미널 모두 `a2a-lab` 폴더로 이동한다.
이전에 실행한 hello 서버는 해당 터미널에서 Ctrl+C로 종료해도 된다.

터미널 1:

```powershell
.\.venv\Scripts\python.exe seller_agent.py
```

기본 주소는 `http://127.0.0.1:9999`다. 예제는 `log_level="warning"`으로
실행하므로 시작 문구 없이 입력을 기다리는 모습이어도 정상일 수 있다.
터미널 2에서 카드로 확인한다.

```powershell
. .\use-a2a.ps1
a2a card get http://127.0.0.1:9999
```

확인할 값은 `Lamp Shop`, `JSONRPC http://127.0.0.1:9999/`,
`Streaming: true`, `negotiate / Negotiate a purchase`다.
STEP 5로 이어갈 때 서버를 켜 두고, 실습을 마칠 때 Ctrl+C로 종료한다.

검증 중에는 9999번 포트가 이미 사용 중이었다. 기존 프로세스를 종료하지
않고 예제의 `--port` 옵션을 사용해 10099번 포트에서 서버 시작과 카드
조회를 확인했다. 직접 실습에서도 같은 충돌이 있으면 다음을 사용한다.

```powershell
# 터미널 1
.\.venv\Scripts\python.exe seller_agent.py --port 10099
# 터미널 2 (CLI 경로 등록 후)
a2a card get http://127.0.0.1:10099
```

대체 포트를 사용하면 STEP 5의 모든 가게 URL도 같은 포트로 바꿔야 한다.
문법 검사와 카드 검증이 성공했으며 검증용 10099 서버는 종료했다.
원본 로그는 다음 파일에 보존했다.

- `../logs/step4-syntax-20261013-204657.stdout.txt`와 `.stderr.txt` (성공 시 출력 없음)
- `../logs/step4-port-conflict-20261013-204928.txt`
- `../logs/step4-card-20261013-204928.txt`와 `.stderr.txt`
- `../logs/step4-server-20261013-204928.stdout.txt`와 `.stderr.txt`

STEP 4에서 거래 명령은 아직 보내지 않았다. API 키가 필요 없는 실습이다.

## STEP 5 — 사용자 요청으로 건너뜀

흥정, 거래 완료, 거절, 취소와 헤더 검증 실습은 실행하지 않았다.

## STEP 6 — Claude Code를 A2A 서버로 감싸기 완료

Claude Code 2.1.296과 Haiku를 사용했다. CLI의 `--exec`가 받은 메시지를
명령의 표준 입력으로 보내고, 명령의 표준 출력을 응답 artifact로 반환한다.
Windows 실행을 위해 `start-claude-shop.ps1`을 만들었다. CLI 경로와 Claude
경로를 현재 세션 PATH에 추가하며, 필요한 `sh.exe`는 Git for Windows의
bin 폴더에서 찾는다. 영구 PATH는 변경하지 않는다.
하네스는 프로젝트 지침을 읽지 않도록 `harness-work/` 빈 폴더에서 실행한다.

실제 하네스 명령:

```text
claude -p --model haiku --system-prompt 'You are a furniture shop. You sell lamps. Answer in English, in one short sentence.'
```

직접 실습할 때는 먼저 이전 가게 서버를 해당 터미널에서 Ctrl+C로 종료한다.
두 터미널 모두 `a2a-lab` 폴더로 이동하고 다음을 실행한다.

터미널 1:

```powershell
.\start-claude-shop.ps1
```

터미널 2:

```powershell
. .\use-a2a.ps1
a2a card get http://localhost:8090
a2a --timeout 120s send -a http://localhost:8090 "Do you sell lamps? How much?"
```

사람이 확인할 것:

1. 카드 이름이 `Claude Shop`인가?
2. 결과 상태가 `completed`이고 `Artifacts`에 모델의 답이 있는가?
3. 끝나면 터미널 1에서 Ctrl+C로 종료한다.

이 단계에는 로그인된 Claude Code가 필요하며 구독 사용량이 소모된다.
로그인이 필요하다는 오류가 나오면 별도 터미널에서 `claude`를 실행해
화면의 로그인 안내를 직접 따른 후 서버를 다시 시작한다. API 키를 파일에
넣을 필요는 없다. 응답 내용은 실행할 때마다 달라질 수 있다.

첫 검증에서는 다음 오류로 task가 실패했다.

```text
Status:   failed
exec: "sh": executable file not found in %PATH%
```

Git for Windows의 `sh`를 실행 세션 PATH에 추가해 해결했다.
명령 문자열에는 `claude`를 사용하고 실행 파일 위치는 PATH로 전달한다.
첫 실행의 원본 로그 `step6-*-20261013-210235*.txt`와 실패 커밋도 보존했다.

재검증 결과는 `completed`였고 다음 모델 응답이 artifact에 저장됐다.

```text
Yes, we sell lamps, but I don't have current prices in front of me, so please check our catalog or ask our staff for exact pricing.
```

성공 로그:

- `../logs/step6-card-20261013-211230.txt`와 `.stderr.txt`
- `../logs/step6-send-20261013-211230.txt`와 `.stderr.txt`
- `../logs/step6-server-20261013-211230.stdout.txt`와 `.stderr.txt`

검증용 서버는 종료했다. 이 방식은 메시지마다 새 task를 만들어 완료한다.
앞선 대화를 이어가는 흥정 상태를 제공하지 않으므로, STEP 4 가게의
여러 턴 거래나 과제의 하네스 기반 buyer/seller 구현을 대신하지 않는다.
