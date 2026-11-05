# Week 05 실습: 1주차 도구를 MCP 서버로 옮기기

`weeks/week-01/starter/first_agent_openai.py`의 `calculator`와 `read_file`을
MCP 서버로 옮기고, 1주차 제출물의 세 번째 도구 `write_note`도 서버에 추가했다.
이 폴더는 강의의 도구 이전 실습이며, 협상 시장 과제의 제출물은 아니다.

## 무엇이 달라졌나

| 부분 | 1주차 starter | 이번 실습 |
|---|---|---|
| 도구 구현 | 호스트 안의 함수와 `TOOLS_IMPL` | `tools_server.py`의 `@mcp.tool()` 함수 |
| 도구 목록 | 호스트에 하드코딩한 `TOOLS` | `tools/list` → 이름·설명·`inputSchema`를 OpenAI 형식으로 변환 |
| 도구 실행 | `TOOLS_IMPL[name](**args)` | `tools/call(name, args)` |
| 루프 | 모델 호출 → 도구 실행 → 결과 추가 → 다시 모델 호출 | 동일. MCP/모델 I/O에 `async`/`await`만 적용 |
| 종료 | 최종 답변 또는 최대 8단계 | 동일 |

`mcp_agent.py`에는 세 도구의 이름이나 구현이 없다. 서버 함수의 docstring과
타입 힌트가 도구 설명과 입력 스키마가 된다. 모델이 이름과 인자를 선택하면
호스트는 이를 서버에 전달하고, 서버의 텍스트 응답을 다음 모델 호출에 넣는다.
MCP의 `isError`도 모델이 볼 수 있는 `tool error:` 관찰로 전달한다.

`read_file`의 기준 디렉터리는 기본적으로 **이 lab 폴더**다. Codex가 다른
디렉터리에서 실행되어도 같은 `notes.txt`를 읽는다. `--root`로 바꿀 수 있다.
starter의 문자열 접두사 검사 대신 실제 경로를 해석한 후 경계를 검사하여
`..`, 비슷한 이름의 이웃 디렉터리, 외부를 가리키는 심볼릭 링크를 차단한다.
최대 4000문자만 읽고, 계산기는 `eval` 없이 숫자와 산술 AST만 처리한다.
`write_note`도 같은 기준 디렉터리와 경로 검사를 적용한다. UTF-8 파일이 없으면
생성하고, 있으면 `content + "\n"`을 추가하여 기존 내용을 보존한다.
쓰기 도구이므로 `readOnlyHint=false`, 반복 호출하면 줄이 중복되므로
`idempotentHint=false`로 선언했다.

## 준비

레포 루트에서:

```bash
cd submissions/26510124/week-05/lab
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
```

검증 환경: Python 3.12.6, `mcp==2.2.0`, `openai==3.20.0`.
직접 의존성은 `requirements.txt`, 실제 설치한 전체 버전은 `requirements-lock.txt`에 있다.
서버와 `check_lab.py`는 API 키 없이 동작한다. 모델을 사용하는 호스트 실행에는
환경변수 `OPENAI_API_KEY`가 필요하다. 키는 파일이나 Git에 넣지 않는다.

## 같은 루프로 두 transport 실행

### stdio

호스트가 같은 가상환경의 Python으로 서버를 자식 프로세스로 시작하고 종료한다.

```bash
unset MCP_SERVER
.venv/bin/python mcp_agent.py
# 또는 직접 목표를 전달한다
.venv/bin/python mcp_agent.py "Read notes.txt and sum the numbers in it."
# 세 번째 도구까지 쓰는 목표 (재실행하면 기존 파일에 한 줄 더 추가한다)
.venv/bin/python mcp_agent.py "Read notes.txt, use the calculator to sum the four numeric fields excluding the date, and append the total to memo-demo.txt."
```

### Streamable HTTP

첫 번째 터미널:

```bash
.venv/bin/python tools_server.py --http
```

두 번째 터미널에서 같은 lab 폴더로 이동한 뒤:

```bash
MCP_SERVER=http://127.0.0.1:8000/mcp .venv/bin/python mcp_agent.py
```

`--port`로 포트를 바꿀 수 있다. HTTP 서버는 `127.0.0.1`에만 바인딩한다.
모델 기본값은 starter와 같은 `gpt-4o-mini`이며 `AGENT_MODEL`로 바꾼다.
`OPENAI_BASE_URL`은 선택 사항이고, 미설정 시 OpenAI API를 사용한다.
temperature 등 생성 옵션은 starter처럼 따로 지정하지 않는다.

## HTTP 요청을 직접 확인

SDK 2.2.0의 `2026-07-28` 프로토콜을 사용한다. HTTP 서버를 켠 뒤:

```bash
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/list' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```

`tools/call`은 `Mcp-Method: tools/call`, `Mcp-Name: calculator` 헤더와
`params.name`, `params.arguments`를 사용한다. SDK 클라이언트가 이 처리를 담당하므로
호스트 루프에서 직접 HTTP 요청을 만들 필요는 없다.

실제 curl로 정상 요청과 두 가지 누락 요청을 한꺼번에 확인하려면:

```bash
bash check_http_curl.sh
# 다른 포트라면 URL을 전달한다
bash check_http_curl.sh http://127.0.0.1:8001/mcp
```

실제 실행 기록 `logs/curl-check-01.txt`에는 `200`, `400`, `400`이 순서대로 남아 있다.

## Codex에 같은 서버 연결

현재 컴퓨터에는 아래 방식으로 `week01-tools`를 등록했다. 다른 컴퓨터에서는
설치 후 이 lab 폴더에서 실행한다. 절대 경로를 등록하므로 디렉터리 이름의 공백도 처리된다.

```bash
codex mcp add week01-tools -- "$PWD/.venv/bin/python" "$PWD/tools_server.py"
codex mcp get week01-tools
```

새 Codex 세션에서 `/mcp`로 확인하고 다음처럼 요청한다:

> week01-tools의 read_file로 notes.txt를 읽고, 날짜를 제외한 Attendees,
> Pizza budget, Drinks, Reimbursed so far 네 값을 calculator로 합산해줘.

stdio 등록은 별도 HTTP 서버를 계속 켜 둘 필요가 없다. HTTP로 연결하려면
서버를 실행한 상태에서 `codex mcp add week01-tools-http --url http://127.0.0.1:8000/mcp`를 사용한다.
등록한 서버는 `codex mcp remove week01-tools`로 해제할 수 있다.

현재 설치된 `codex-cli 0.149.1`은 사용자 기본 모델 `gpt-6-astra`로 실행 시
CLI 업그레이드를 요구했다. 사용자 기본 설정은 바꾸지 않았다. 연결 시연에는
로컬 모델 목록에 표시된 `gpt-5.6-sol`을 실행 옵션으로 지정했다.

## 검증과 로그

```bash
# 모델 API를 사용하지 않는 검사
.venv/bin/python check_lab.py
# HTTP 서버도 실행 중이라면 두 transport와 HTTP 메타데이터를 함께 검사
.venv/bin/python check_lab.py --http-url http://127.0.0.1:8000/mcp
```

검사는 실제 MCP 연결로 도구 목록·docstring·입력 스키마, 정상 호출, 잘못된 인자,
0으로 나누기, 허용하지 않은 식, 경로 탈출, 없는 파일·도구를 확인한다.
별도로 심볼릭 링크 경계와 4000문자 제한도 확인한다.
HTTP의 정상 요청은 200, `Mcp-Method` 또는 `clientCapabilities` 누락은 400이어야 한다.

| 실행 기록 | 결과 |
|---|---|
| `logs/stdio-run-01.txt` | 실제 `gpt-4o-mini`가 두 도구를 호출하고 69,504 반환 |
| `logs/http-run-01.txt` | 동일한 루프·모델·도구로 69,504 반환 |
| `logs/check-lab-02.txt` | 두 transport 각각 10개 도구 호출, 스키마, 경로 경계, HTTP 검증 통과 |
| `logs/codex-run-01.jsonl` | 기존 CLI와 기본 모델의 버전 불일치로 모델 호출 실패 |
| `logs/codex-run-02.jsonl` | Codex(`gpt-5.6-sol`)가 등록된 MCP 도구 두 개를 실제 호출하고 69,504 반환 |
| `logs/check-lab-03.txt` | 세 도구의 스키마·설명, 두 transport 각각 15개 호출, 파일 생성·추가 기록·경로 차단 검증 통과 |
| `logs/curl-check-01.txt` | 실제 curl 요청: 정상 200, `Mcp-Method` 누락 400, `clientCapabilities` 누락 400 |
| `logs/stdio-three-tools-01.txt` | `[host] 3 tools`, 세 도구 호출, `memo-stdio.txt`에 `Total: 69504` 기록 |
| `logs/http-three-tools-01.txt` | 같은 host로 세 도구 호출, `memo-http.txt`에 `Total: 69504` 기록 |
| `logs/checkpoint-final-01.txt` | host 원본 일치, 도구 이름 하드코딩 없음, 두 실행의 호출 순서·파일 내용 대조 |

샘플 `notes.txt`는 공개 starter를 그대로 복사했다. 날짜를 제외한 네 항목의 합은
`4 + 48000 + 9500 + 12000 = 69504`다. 실제 모델 실행 원본 로그를 수정하지 않았다.

처음 `check-lab-01.txt`에서는 경로 거절을 `ValueError`로 표현하여 SDK가
오류 이유를 숨기는 현상을 발견했다. 예상 가능한 오류를 명시적 `ToolError`로
수정한 뒤 `check-lab-02.txt`에서 재검증했다. 실패 기록도 보존했다.
`http-server-01.txt`는 샌드박스의 포트 바인딩 거절 기록이며,
`http-server-02.txt`, `http-server-03.txt`는 승인 후 실행한 서버 기록이다.
`http-server-04.txt`는 세 번째 도구 추가 후 HTTP 서버 기록이다.
Codex 시연은 `--ephemeral --sandbox read-only -m gpt-5.6-sol`로 실행했고,
해당 실행에만 `-c 'mcp_servers.week01-tools.default_tools_approval_mode="approve"'`를
적용했다. 두 도구의 호출 성공은 JSONL의 `mcp_tool_call` 이벤트로 확인할 수 있다.
Codex stderr에는 별도 MCP 클라이언트의 종료 시 초기화 경고도 남아 있지만,
`week01-tools` 두 호출과 최종 응답은 모두 완료되었다.

## 최종 체크포인트

- [x] `tools/list`의 세 도구 description이 서버 함수의 docstring과 일치한다.
- [x] host 코드에 도구 이름을 하드코딩하지 않았다.
- [x] stdio와 HTTP 모두 `read_file → calculator → write_note`를 호출했다.
- [x] 두 번째 client인 Codex에서도 같은 서버의 도구를 실제 호출했다.
- [x] 세 번째 도구를 서버에만 등록했고, host 수정 없이 조회·호출했다.

세 번째 도구 추가 전후 `mcp_agent.py`의 Git blob 해시는 모두
`0c2cf0afa56d4dd83b11441c32d78274fc4f3c42`다. 두 live 실행은 같은 모델과
같은 목표를 사용하되 원본 출력을 각각 보존하기 위해 출력 파일명만 다르게 했다.
두 결과 파일은 모델이 도구로 직접 생성한 것이며, 내용을 사후 수정하지 않았다.

## 참고

- [강의 실습](https://github.com/Q00/ai-agent-engineering-101/blob/main/week-05.html)
- [MCP Python SDK Client](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/index.md)
- [OpenAI 공식 Codex MCP 문서](https://developers.openai.com/codex/mcp)
