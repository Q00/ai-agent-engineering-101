# LAB 5: 같은 host를 stdio와 HTTP로 실행

## 실행 조건

- Python: `C:\venvs\submissions\Scripts\python.exe` (Python 3.14)
- MCP SDK: 2.2.0
- 비교 모델: `nvidia/nemotron-3-super-120b-a12b:free`, OpenRouter
- 질문: `Read notes.txt and sum the numbers in it.`
- 작업 디렉터리: `submissions/24522014/week-01/`
- 입력 파일: 위 디렉터리의 `notes.txt` (참석자 5, 피자 58000, 음료 5500, 기정산 15000)
- 두 transport에서 동일한 `mcp_agent.py`와 `tools_server.py`를 사용한다.
- 모델의 temperature 등은 기존 host 코드의 기본값을 그대로 사용한다.

`MCP_SERVER`가 비어 있으면 host는 `sys.executable`로 `tools_server.py`를
stdio 자식 프로세스로 실행한다. URL이면 같은 MCP Client가 HTTP로 연결한다.
LAB 5 진행 중 host 루프와 server 소스는 수정하지 않았다.

## 현재 확인 결과

| 실행 | 모델 | 결과 | 로그 |
|---|---|---|---|
| stdio 첫 시도 | poolside/laguna-s-2.1:free | read_file 이후 모델 API 429 | logs/lab-step5-stdio-01.txt |
| stdio 재시도 | poolside/laguna-s-2.1:free | 모델 API 429 | logs/lab-step5-stdio-02.txt |
| stdio 비교 실행 | nvidia/nemotron-3-super-120b-a12b:free | read_file, calculator 호출; 최종 답 78505 | logs/lab-step5-stdio-03.txt |
| HTTP 비교 실행 | nvidia/nemotron-3-super-120b-a12b:free | 초기 MCP 연결에서 RemoteProtocolError; 모델 호출 전 실패 | logs/lab-step5-http-01.txt |
| HTTP 수동 재실행 (2026-10-07) | nvidia/nemotron-3-super-120b-a12b:free | read_file, calculator 호출; 최종 답 78505 | logs/lab-step5-http-manual-20261007-095801.txt |

stdio 비교 실행의 calculator 인자는 `5 + 58000 + 5500 + 15000`이고 결과는
`78505`였다. 사용자가 일반 PowerShell에서 위와 동일한 모델과 질문으로 HTTP를
재실행한 로그도 확인했다. HTTP의 calculator 인자는 `5+58000+5500+15000`이고
결과와 최종 답은 `78505`였다. 두 실행은 모두 도구 2개를 발견하고 `read_file`,
`calculator`를 각각 한 번 호출했다. 수식의 공백과 최종 답의 서식만 달랐다.
호스트 루프를 수정하지 않고 MCP_SERVER 설정으로 transport를 바꾸는 LAB 5의
동작을 확인했다.

HTTP 진단에서는 서버의 ASGI 응답과 h11이 내보낸 바이트에
`Content-Length: 338`이 있었으나, 수신한 응답은 `Transfer-Encoding: chunked`로
바뀌어 있었다. 수신 본문은 chunk 길이 없이 JSON으로 시작했다. curl에서도
`chunk hex-length char not a hex digit: 0x7b`가 발생했다. 응답을 변경한 구성 요소는
특정하지 못했다. 일반 PowerShell에서의 수동 재실행은 성공했으므로 이 실패가
모든 실행 환경에서 발생하는 것은 아니다. 실패 로그도 그대로 보존했으며,
어떤 구성 요소가 응답을 바꿨는지에 대한 결론은 내리지 않는다.

## 재현: PowerShell

두 창 모두 작업 디렉터리는 week-01로 둔다. read_file의 허용 범위는 서버의
현재 작업 디렉터리이므로 입력 파일도 그 안에서 읽는다.

### Host 실행 창의 공통 설정

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-01
$env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'User')
$env:OPENAI_BASE_URL = 'https://openrouter.ai/api/v1'
$env:AGENT_MODEL = 'nvidia/nemotron-3-super-120b-a12b:free'
```

### stdio

```powershell
$env:MCP_SERVER = ''
$logPath = "..\week-05\logs\lab-step5-stdio-manual-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"
& C:\venvs\submissions\Scripts\python.exe -u ..\week-05\mcp_agent.py 'Read notes.txt and sum the numbers in it.' 2>&1 | Tee-Object -FilePath $logPath
```

### HTTP

별도의 PowerShell 창에서 서버를 실행한다.

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-01
& C:\venvs\submissions\Scripts\python.exe ..\week-05\tools_server.py --http
```

Host 실행 창에서 공통 설정을 유지하고 MCP_SERVER만 변경한다.

```powershell
$env:MCP_SERVER = 'http://127.0.0.1:8000/mcp'
$logPath = "..\week-05\logs\lab-step5-http-manual-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"
& C:\venvs\submissions\Scripts\python.exe -u ..\week-05\mcp_agent.py 'Read notes.txt and sum the numbers in it.' 2>&1 | Tee-Object -FilePath $logPath
```

정상 실행에서는 두 도구가 발견되고 `read_file`, `calculator` 호출과 최종 답을
각 로그에서 비교한다. 실행 후 서버 창에서 Ctrl+C로 서버를 종료한다.
