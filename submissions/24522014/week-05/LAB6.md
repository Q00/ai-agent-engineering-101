# LAB 6: 같은 MCP server를 두 번째 client에 연결

## 등록과 실행

첫 번째 host는 LAB 4~5의 `mcp_agent.py`이고, 두 번째 host는 Codex CLI
0.160.1이다. Codex는 기존 ChatGPT 로그인을 사용했고, 설정된 모델은
`gpt-6.1-sol`이었다. 같은 `tools_server.py`를 stdio 방식으로 등록했다.
호스트 루프와 서버의 도구 구현은 수정하지 않았다.

사용자 Codex 설정에 다음 명령으로 등록했다.

```powershell
codex.cmd mcp add week01-tools -- C:\venvs\submissions\Scripts\python.exe C:\Submissions\ai_agent_engineering\submissions\24522014\week-05\tools_server.py
```

추가한 `[mcp_servers.week01-tools]` 항목의 작업 디렉터리를 다음과 같이 지정했다.
다른 Codex 설정은 유지했다. 이 설정에는 API 키가 없다.

```toml
[mcp_servers.week01-tools]
command = 'C:\venvs\submissions\Scripts\python.exe'
args = ['C:\Submissions\ai_agent_engineering\submissions\24522014\week-05\tools_server.py']
cwd = 'C:\Submissions\ai_agent_engineering\submissions\24522014\week-01'
```

등록 확인 출력은 `logs/lab-step6-registration.txt`에 보존했다.
등록된 서버는 이후 Codex 세션에서도 사용할 수 있다.

## 재현: PowerShell

아래 환경 변수 제거는 이 PowerShell 프로세스에만 적용된다. Codex가 기존
ChatGPT 로그인을 사용하도록 OpenRouter용 환경 변수의 영향을 제거한다.
Windows 사용자 환경 변수에 저장된 키는 삭제하지 않는다.

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-05
$env:OPENAI_API_KEY = $null
$env:OPENAI_BASE_URL = $null
$env:CODEX_API_KEY = $null
$logPath = ".\logs\lab-step6-codex-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"
codex.cmd exec --ephemeral --json --color never --sandbox read-only -C C:\Submissions\ai_agent_engineering\submissions\24522014\week-01 -c mcp_servers.week01-tools.default_tools_approval_mode=approve -c mcp_optional_startup_grace_ms=0 'Read notes.txt and sum the numbers in it. For this LAB 6 check, use the registered week01-tools MCP tools to read the file and calculate the sum. Use no shell or other tools. Do not change any files or perform repository work. Report the total and the MCP tools used.' 2>&1 | Tee-Object -FilePath $logPath
```

원래 질문은 `Read notes.txt and sum the numbers in it.`이다.
뒤에 붙인 지시는 MCP 경유 실행과 읽기 작업으로 범위를 제한하는 내용이었다.

이번 실행에 한해 `week01-tools`의 도구 승인 설정을 `approve`로 지정했다.
다른 서버의 승인 설정은 변경하지 않았다.

## 실행 결과

`logs/lab-step6-codex-01.txt`의 JSONL 이벤트에서 다음을 확인했다.

| 순서 | server / tool | 인자 | 결과 |
|---|---|---|---|
| 1 | week01-tools / read_file | `{"path":"notes.txt"}` | week-01의 같은 메모 |
| 2 | week01-tools / calculator | `2026 + 9 + 1 + 5 + 58000 + 5500 + 15000` | `80541` |

두 MCP 호출 모두 `status: completed`, `error: null`로 완료되었고,
최종 답은 "Total: **80,541**, including the date's numbers (2026, 09, 01)."였다.
`turn.completed` 이벤트도 기록되어 있다. shell 호출이나 파일 변경은 없었다.

LAB 5의 모델은 라벨이 붙은 네 항목을 합해 `78505`를 답했다.
Codex의 모델은 날짜 숫자도 더했으므로 `2026 + 9 + 1 = 2036`만큼 큰
`80541`을 답했다. 같은 server를 이용할 수 있다는 점은 확인했지만, 자연어
"numbers"의 해석은 host의 모델에 따라 달랐다. calculator는 전달된 식을
정확히 계산했으며, 무엇을 합산할지는 모델이 결정했다.

콘솔에는 HTTP 연결 진단 오류도 포함되었고, 실행 도구는 종료 코드 1을
보고했다. 위의 stdio MCP 호출과 모델 턴 완료는 JSONL에서 확인했다.
진단 출력을 삭제하거나 정리하지 않고 원본 로그를 그대로 보존했다.

## 참고

- [OpenAI Docs: MCP 등록, cwd, 도구 승인](https://developers.openai.com/codex/mcp)
- [OpenAI Docs: non-interactive mode, JSONL 출력](https://developers.openai.com/codex/noninteractive)
