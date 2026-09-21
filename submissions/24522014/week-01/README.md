# Week 01 — Tool-Using Agent

`first_agent.py` 한 파일로 동작하는 최소 에이전트. OpenAI 호환 엔드포인트를 사용하므로 OpenAI와 OpenRouter 양쪽에서 같은 코드로 실행된다. 툴 5개(`calculator`, `read_file`, `fetch`, `clock`, `write_note`)를 등록하고, 모델이 툴 호출을 멈출 때까지 최대 8스텝의 루프를 돈다.

각 툴의 **설명문을 왜 그렇게 썼는지**는 [TOOLS.md](./TOOLS.md) 참고.

---

## 1. 설치

```powershell
pip install openai
pip install tzdata      # Windows 필수. clock 툴이 IANA 타임존을 못 찾는 것을 방지
```

`tzdata`는 Linux/macOS에서는 대개 불필요하다. Windows에는 OS 차원의 tz 데이터베이스가 없어서 이게 없으면 `clock`의 모든 호출이 `unknown timezone` 에러로 떨어진다.

Python 3.9 이상이 필요하다(`zoneinfo` 표준 라이브러리 사용).

---

## 2. 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | — | API 키. OpenRouter 키도 그대로 동작한다. |
| `OPENAI_BASE_URL`| OpenAI 공식 엔드포인트 | OpenRouter 사용 시 `https://openrouter.ai/api/v1` |
| `AGENT_MODEL` | `cohere/north-mini-code:free` | 사용할 모델 ID |
| `AGENT_TZ` | `Asia/Seoul` | `clock`이 인자 없이 호출됐을 때의 기본 타임존 |

PowerShell 설정 예시:

```powershell
# 현재 세션에만 적용
$env:OPENAI_API_KEY  = "<your key>"
$env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
$env:AGENT_MODEL     = "gpt-4o-mini"

# 영구 적용 (새 터미널부터 반영)
setx OPENAI_API_KEY "<your key>"
```

확인:

```powershell
python -c "import os; print(bool(os.environ.get('OPENAI_API_KEY')))"
```

### 모델 선택 주의

이 에이전트는 **tool calling(function calling)을 지원하는 모델**에서만 동작한다. OpenRouter 무료 모델 상당수는 tool calling을 지원하지 않거나 요청의 `tools` 필드를 조용히 무시하며, 그 경우 모델은 "툴이 없어서 못 한다"는 사과문을 반환한다. 툴이 등록됐는데도 그런 응답이 나온다면 모델을 먼저 의심할 것.

---

## 3. 툴 스키마

모델에게 전달되는 스키마는 `TOOLS` 리스트에, 실제 구현 매핑은 `TOOLS_IMPL` 딕셔너리에 있다. 둘의 키가 어긋나면 런타임에 `KeyError`가 난다.

### calculator

| 항목 | 값 |
|---|---|
| description | Evaluate an arithmetic expression. |
| `expression` | string, **required** — 예: `3 * (4 + 5)` |

`eval` 대신 `ast`로 파싱하며 `+ - * / ** -(단항)`만 허용한다. 그 외 노드는 `ValueError`.

### read_file

| 항목 | 값 |
|---|---|
| description | Read a text file in the working directory. |
| `path` | string, **required** |

작업 디렉터리 밖이면 거부. 최대 4000자까지만 반환.

### fetch

| 항목 | 값 |
|---|---|
| description | Fetch a public web page or API response over http/https and return the first few thousand characters of its text. Local, private, and non-http URLs are rejected. |
| `url` | string, **required** — 절대 `http://` 또는 `https://` URL |

제약:

| 항목 | 값 |
|---|---|
| 허용 스킴 | `http`, `https` |
| 허용 포트 | 80, 443, 8000, 8080 |
| 차단 대상 | loopback · 사설망 · 링크로컬(169.254.0.0/16) · 예약 대역 · 멀티캐스트 (`ipaddress.is_global` 기준) |
| 타임아웃 | 5.0초 |
| 리다이렉트 | 최대 3회, **매 홉마다 같은 검사 재실행** |
| 본문 읽기 | 200,000 바이트에서 중단 |
| 반환 길이 | 디코딩 후 4,000자로 절단 |
| 허용 content-type | `text/*`, `application/json`, `application/xml`, `application/xhtml+xml` |

거부 시 `denied: <사유>`, 네트워크 실패 시 `error: <타입>: <메시지>` 문자열을 반환한다(예외를 던지지 않으므로 루프가 끊기지 않는다).

**남은 위험**: DNS 리바인딩. 검사 시점의 IP와 실제 연결 시점의 IP가 다를 수 있다. 완전히 막으려면 검증된 IP로 직접 연결하고 `Host` 헤더를 수동 설정해야 하며, 실습 범위를 벗어난다.

### clock

| 항목 | 값 |
|---|---|
| description | Get the current date, time, weekday, and UTC offset. Defaults to Asia/Seoul if no timezone is given. Call this instead of guessing today's date. |
| `timezone` | string, *optional* — IANA 이름. 예: `Asia/Seoul`, `UTC`, `America/New_York` |

반환 예: `2026-09-04 14:56:00 KST (UTC+0900), Friday, tz=Asia/Seoul`

### write_note

| 항목 | 값 |
|---|---|
| description | Save a short text note to a file in the working directory. Appends by default; set append=false to replace the file's contents, which cannot be undone. Only .txt, .md, .log, .csv, .json filenames are accepted, and only inside the working directory. |
| `path` | string, **required** — 상대 경로. 예: `notes.md` |
| `content` | string, **required** — 최대 4,000자 |
| `append` | boolean, *optional* — 기본 `true`(추가), `false`면 덮어쓰기 |

제약:

| 항목 | 값 |
|---|---|
| 경로 | `os.path.realpath` + `commonpath`로 작업 디렉터리 내부만 허용(심볼릭 링크 포함 해석) |
| 확장자 | `.txt`, `.md`, `.log`, `.csv`, `.json` |
| 내용 길이 | 4,000자 |
| 하위 디렉터리 | 작업 디렉터리 내부라면 자동 생성 |

반환 예: `ok: appended to notes.md (10 chars written, file now 11 bytes)`

---

## 4. 실행

### 에이전트 전체 실행

```powershell
python first_agent.py "<goal>"
```

인자를 생략하면 기본 목표 `"Read notes.txt and sum the numbers in it."`가 사용된다.

툴별 예시:

```powershell
python first_agent.py "What is 17 * 23 + 5?"
python first_agent.py "Read notes.txt and sum the numbers in it."
python first_agent.py "Use fetch to retrieve https://encle.co.kr and summarize its main text."
python first_agent.py "What's the time difference between Seoul and New York right now?"
python first_agent.py "Check the time, then save it to log.md as a one-line entry."
```

마지막 두 개는 툴을 두 번 이상 호출하게 되어 멀티스텝 루프를 관찰하기 좋다. 실행 중 각 호출은 다음 형태로 출력된다:

```
  [tool] clock({'timezone': 'Asia/Seoul'}) -> 2026-09-04 14:56:00 KST ...
```

### 툴 함수만 단독 실행 (API 키 불필요)

```powershell
python -c "import first_agent as a; print(a.clock())"
python -c "import first_agent as a; print(a.calculator('3 * (4 + 5)'))"
python -c "import first_agent as a; print(a.fetch('https://example.com')[:300])"
python -c "import first_agent as a; print(a.write_note('notes.md', 'hello'))"
```

### 등록 상태 확인

```powershell
python -c "import first_agent as a; print([t['function']['name'] for t in a.TOOLS]); print(list(a.TOOLS_IMPL))"
```

기대 출력:

```
['calculator', 'read_file', 'fetch', 'clock', 'write_note']
['calculator', 'read_file', 'fetch', 'clock', 'write_note']
```

파이썬이 실제로 어느 파일을 임포트했는지 확인:

```powershell
python -c "import first_agent as a; print(a.__file__)"
```

---

## 5. 루프 파라미터

| 항목 | 위치 | 값 | 비고 |
|---|---|---|---|
| 최대 스텝 | `run(goal, max_steps=8)` | 8 | 초과 시 `"stopped: max steps exceeded"` 반환 |
| 종료 조건 | 툴 호출 없는 응답 | — | `msg.tool_calls`가 비면 최종 답변으로 간주 |
| 로그 절단 | `print(... [:200])` | 200자 | 콘솔 출력만 자르며, 모델에 전달되는 값은 온전하다 |

`max_steps`는 성능 튜닝값이 아니라 **안전장치**다. 툴 호출이 무한히 반복되는 루프를 끊는 것이 목적이므로, 스텝이 모자란다면 값을 올리기 전에 툴 설명이 모호하지 않은지부터 확인하는 편이 낫다.

---

## 6. 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| `TOOLS`에 툴이 안 보임 | 파일이 교체되지 않음 / 다른 경로의 동명 파일을 임포트 | `a.__file__`로 실제 경로 확인 |
| "툴이 없어서 못 한다"는 사과문 | 모델이 tool calling 미지원 | `AGENT_MODEL` 변경 |
| `Could not resolve authentication method` | 키 환경 변수 미설정 | 해당 SDK의 키 변수 확인 (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) |
| `clock`이 `unknown timezone` | Windows에 tz 데이터베이스 없음 | `pip install tzdata` |
| `fetch`가 `denied:` | 스킴·포트·주소 검사에 걸림 | 반환 문자열의 사유 확인 |
| `404 model not found` | 잘못된 모델 ID | 엔드포인트에 맞는 ID인지 확인 |
