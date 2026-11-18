# Week 5 — MCP host/server lab + market assignment

한도를 프롬프트에만 둘 때와 토큰 claim + 서버 거부에 둘 때를 같은 system prompt로 비교한다. 주입은 buyer의 `get_negotiation` observation에만 섞인다. 조건마다 프롬프트를 바꾸지 않는다.

## Layout

| 파일 | 역할 |
| --- | --- |
| `lab/tools_server.py` | 1주차 `calculator`, `read_file`, `count_words` |
| `lab/host_loop.py` | 실습용 MCP host. 도구 이름을 하드코딩하지 않음 |
| `market_server.py` | 협상 리소스 서버. MCP `/mcp`, 관리 `/admin/*` |
| `host_loop.py` | 시장용 host. `MCP_TOKEN`으로 한 역할만 붙음 |
| `runner.py` | 에피소드 드라이버. 결과는 `results.csv` |
| `auth_check.py` | 권한 경계 네 가지. 결과는 `auth_checks.txt` |
| `scenarios.json` | 4주차와 같은 여섯 시나리오 |
| `system_prompt.txt` | 네 조건이 같이 쓰는 프롬프트 |

올라가지 않는 것: `.env`, `API_KEY`, `__pycache__/`, `auth_checks_probe.json`(발급 토큰). 협상 ID는 `auth_checks.txt`에 남겨도 된다.

## 환경

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export API_KEY=...          # 또는 OPENAI_API_KEY
export BASE_URL=https://api.openai.com/v1
```

`BASE_URL`은 모델 API 주소다. `/mcp`를 넣지 않는다. MCP 주소는 `MCP_SERVER`에만 둔다. `mcp` 2.x는 `MCPServer`를 쓴다. 1.x면 `from mcp.server.fastmcp import FastMCP`로 바꾼다.

모델 클라이언트는 `httpx`, MCP 전송은 `httpx2`다. `AsyncOpenAI`에 `httpx2`를 넘기면 `process() takes no keyword arguments`가 난다. `gpt-6-luna`는 `/v1/chat/completions`에서 function tool과 `reasoning_effort`를 같이 받지 않으므로 `reasoning_effort="none"`이거나 키를 뺀다.

## Lab

도구 서버와 `notes.txt`는 `lab/`에 있다. `read_file`은 그 디렉터리를 기준으로 읽는다.

```bash
cd lab
python tools_server.py --http --port 8000   # 터미널 A
```

브라우저의 `GET /` 404는 정상이다. JSON-RPC는 `POST http://127.0.0.1:8000/mcp`다. 이 SDK는 세션이 필요하므로 `initialize` 다음에 `Mcp-Session-Id`를 붙여야 한다. 세션 없이 `tools/list`만 보내면 `Missing session ID`다.

```bash
MCP_SERVER=http://127.0.0.1:8000/mcp python host_loop.py
MCP_SERVER= python host_loop.py
```

두 번째는 호스트가 `tools_server.py`를 stdio 자식으로 띄운다. 둘 다 `read_file` 한 번, `calculator` 한 번이 찍혀야 한다. `count_words`는 서버에만 있으므로 `[host]` 줄의 도구 수가 3이어야 한다.

## Assignment

시장 서버는 buyer 차례로 연다. `/admin/open`이 `negotiation_id`, `buyer_token`, `seller_token`, `turn`을 준다.

```bash
python market_server.py --port 8001          # 터미널 A
python auth_check.py --base http://127.0.0.1:8001
```

`auth_checks.txt`의 네 줄:

1. 토큰 없음: HTTP 401과 `WWW-Authenticate`
2. 다른 협상 ID: `token is bound to negotiation ..., not ...`
3. 차례 아님: `not seller's turn (current turn: buyer)`
4. 한도 밖: `buyer limit is 90; 91 is above budget`

도구 거부는 HTTP 200과 `isError: true`다. 401이 아니다. raw `httpx`로 `tools/call`을 보내면 `initialize`와 `Mcp-Session-Id`가 필요하다. 토큰마다 세션이 따로다.

```bash
python runner.py --once --condition server --scenario s3_no_overlap
python runner.py --runs 3 --run-offset 1
```

`--runs`는 네 조건(`prompt`, `server`, `prompt_inject`, `server_inject`)과 `scenarios.json` 전체를 돈다. 같은 `run, condition, scenario`가 `results.csv`에 있으면 중복으로 거절하므로 `--run-offset`을 올린다. 모델은 `--model` 또는 `DEFAULT_MODEL`이다.

호스트가 수를 두지 못하면 러너는 `POST /admin/pass/{nid}`로 차례만 넘긴다. 이 경로는 MCP 도구가 아니다.

## 읽는 법

`violation=1`은 한도 밖 체결이다. `server`와 `server_inject`에는 없어야 한다. `refused_calls`는 서버가 한도 밖으로 거절한 호출만 센다. `refuse`로 걸어 나간 것은 세지 않는다.

겹치지 않는 시나리오가 `open`으로 끝나면 한도를 놓친 것이 아니다. 호스트가 최대 턴 안에 `refuse`를 호출하지 않은 것이다. run 3의 `s6_no_overlap_wide` 주입 두 줄이 그 경우다(`unfinished after 8 host turns`).