# week-01 에이전트: MCP 전환 전 / 후

## 전 — week-01 `first_agent.py` (프로세스 1개)

도구 정의(스키마), 도구 구현, 에이전트 루프가 한 파일·한 프로세스에 섞여 있다.
스키마는 `TOOLS` 리스트에 손으로 쓰고, 구현은 `TOOLS_IMPL[name](**input)`으로 직접 호출한다.

```mermaid
flowchart LR
    U([사용자 goal]) --> A
    subgraph P["프로세스 1개: first_agent.py"]
        A["에이전트 루프<br/>(for step in range)"]
        S["TOOLS 리스트<br/>손으로 쓴 JSON 스키마"]
        I["TOOLS_IMPL<br/>calculator / read_file / clock"]
        A -- "스키마를 요청에 포함" --> S
        A -- "TOOLS_IMPL[name](**input)<br/>함수 호출" --> I
    end
    A <-- "messages.create<br/>(tool_use / tool_result)" --> M[(Claude API)]
```

## 후 — `host.py` + `server.py` (프로세스 2개, MCP stdio)

루프는 그대로 호스트에 남고, 도구는 서버로 나갔다.
호스트는 도구를 하나도 모른다. 시작할 때 `tools/list`로 물어보고, 모델이 부르면 `tools/call`로 넘길 뿐이다.

```mermaid
flowchart LR
    U([사용자 goal]) --> H
    subgraph HP["프로세스 A: host.py (MCP 호스트 + 클라이언트)"]
        H["에이전트 루프<br/>(week-01과 동일)"]
        C["ClientSession<br/>서버 연결 1개 담당"]
        H --> C
    end
    subgraph SP["프로세스 B: server.py (MCP 서버, 자식 프로세스)"]
        R["FastMCP<br/>@mcp.tool()"]
        T["calculator / read_file / clock<br/>스키마는 타입 힌트에서 자동 생성"]
        R --> T
    end
    C <-- "stdio · JSON-RPC 2.0<br/>tools/list · tools/call" --> R
    H <-- "messages.create<br/>(tool_use / tool_result)" --> M[(Claude API)]
```

## 한 스텝의 흐름 (후)

```mermaid
sequenceDiagram
    participant H as host.py
    participant S as server.py
    participant M as Claude API
    H->>S: 자식 프로세스로 실행 + initialize
    H->>S: tools/list
    S-->>H: 이름 · description · inputSchema
    H->>M: goal + 도구 스키마
    M-->>H: tool_use (예: clock)
    H->>S: tools/call (name, arguments)
    S-->>H: content (실행 결과 텍스트)
    H->>M: tool_result
    M-->>H: 최종 답변
```

## 무엇이 어디로 갔나

| 항목 | 전 (`first_agent.py`) | 후 |
|---|---|---|
| 도구 구현 | 에이전트 파일 안의 함수 | `server.py` |
| 도구 스키마 | `TOOLS` 리스트에 손으로 작성 | `@mcp.tool()`이 시그니처에서 생성, `tools/list`로 전달 |
| 도구 호출 | `TOOLS_IMPL[name](**input)` | `session.call_tool(name, input)` (JSON-RPC) |
| 에이전트 루프 | 같은 파일 | `host.py` (로직 동일) |
| 도구 실행 위치 | 에이전트 프로세스 | 별도 자식 프로세스 |
| 도구 추가 | 에이전트 코드 수정 | 서버에만 추가, 호스트는 그대로 |
| `DROP_CLOCK` | 스키마 리스트에서 제거 | `tools/list` 결과에서 제거 (호스트 쪽) |
| `CLOCK_DESC` | 에이전트가 읽음 | 서버가 읽음 (호스트가 환경 변수를 자식에게 전달) |

## 바뀌지 않은 것

- 모델, `max_tokens=1024`, `max_steps=8`, 기본 goal, 도구 설명 문구.
- `read_file`의 작업 디렉터리 제한. 이제는 서버 프로세스의 cwd 기준이며, 호스트를 `week-05/`에서 실행하면 같은 범위다.

## 아직 확인되지 않은 것

이 환경에는 Python 3.10 이상과 `mcp` SDK, API 키가 없어서 `py_compile`만 통과시켰고 **실제 실행은 하지 않았다.** `logs/`에 실행 로그가 없는 이유다.
