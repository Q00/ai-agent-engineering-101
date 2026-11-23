# LAB 7: 세 번째 도구를 server에 추가

1주차 `first_agent.py`의 세 번째 도구는 `fetch`이다. 이 함수와 URL 검사,
리다이렉트 검사, 응답 크기 제한을 `tools_server.py`로 옮기고 `@mcp.tool()`을
붙였다. docstring은 1주차 `TOOLS.md`에 기록된 도구 설명을 사용했다.

`mcp_agent.py`는 수정하지 않았다. LAB 4의 `9918c4a` 커밋에 저장된 host와
현재 파일의 Git blob 해시가 같은 것을 확인했다. host는 기존 `list_tools()`와
`call_tool()` 코드로 새 도구를 발견하고 실행했다.

## 확인 결과

- Python 문법 검사 통과.
- `file:///notes.txt`, `http://127.0.0.1/`, `https://example.com:444/` 요청 거부 확인.
- 기존 host를 stdio로 실행해 아래 도구 목록 확인.
- 모델이 `fetch`를 호출해 `HTTP 200 text/html` 응답을 받고 제목 `Example Domain`을 답함.

```text
[host] 3 tools: ['calculator', 'read_file', 'fetch']
  [tool] fetch({'url': 'https://example.com'}) -> HTTP 200 text/html
```

실제 실행 로그는 `logs/lab-step7-stdio-01.txt`에 그대로 보존했다.
모델은 LAB 5와 같은 `nvidia/nemotron-3-super-120b-a12b:free`를 사용했다.

## 재현: PowerShell

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-01
$env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'User')
$env:OPENAI_BASE_URL = 'https://openrouter.ai/api/v1'
$env:AGENT_MODEL = 'nvidia/nemotron-3-super-120b-a12b:free'
$env:MCP_SERVER = ''
$logPath = "..\week-05\logs\lab-step7-stdio-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"
& C:\venvs\submissions\Scripts\python.exe -u ..\week-05\mcp_agent.py 'Fetch https://example.com and report its page title. Use the fetch tool.' 2>&1 | Tee-Object -FilePath $logPath
```

이미 실행 중인 HTTP server에서 추가한 도구를 사용하려면 server를 재시작해야 한다.
Codex의 stdio 등록도 같은 server 파일을 가리키므로 새 MCP 연결에서 이 도구를
발견할 수 있다.
