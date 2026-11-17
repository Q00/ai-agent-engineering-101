# Tool Forge (Week 10 실습)

에이전트가 도구 코드를 직접 작성하면, forge가 그 도구에 딸린 테스트를 돌려 보고 **통과한 도구만** 게시·등록하는 교육용 구현입니다.
참고 자료: https://wpti.dev/ai-agent-engineering-101/week-10.html (과제에서 제공된 링크이며, 이 README를 쓰면서 새로 받아 오지는 않았습니다).

## 빠른 실행

의존성은 없습니다(표준 라이브러리만 사용). 저장소의 `.venv` Python으로 실행합니다.

PowerShell (`tool_forge` 디렉터리에서):

```powershell
C:\Users\qkrgk\ai-agent-engineering-101\.venv\Scripts\python.exe -m unittest test_forge -v
C:\Users\qkrgk\ai-agent-engineering-101\.venv\Scripts\python.exe demo.py
C:\Users\qkrgk\ai-agent-engineering-101\.venv\Scripts\python.exe boundary_probe.py
```

Git Bash (`tool_forge` 디렉터리에서):

```bash
../../../../.venv/Scripts/python.exe -m unittest test_forge -v
../../../../.venv/Scripts/python.exe demo.py                       # verification/demo-library-* 를 새로 만들고 지우지 않음
../../../../.venv/Scripts/python.exe demo.py --library verification/demo-library   # 같은 경로로 재실행하면 복원부터 확인
../../../../.venv/Scripts/python.exe boundary_probe.py
```

## 핵심 예제

```python
1  from forge import Forge
2  forge = Forge("my_tools", mode="restricted")
3  bad  = "def add(a, b):\n    return a - b\n"
4  good = "def add(a, b):\n    return a + b\n"
5  test = "assert add(2, 3) == 5\n"
6  print(forge.forge_tool("add", "Add two numbers.", bad, test))
7  print(forge.forge_tool("add", "Add two numbers.", good, test))
8  description, fn = forge.TOOLS["add"]
9  print(fn(2, 3))
10 print(Forge("my_tools", mode="restricted").TOOLS["add"][1](4, 5))
```

1. forge 모듈을 가져옵니다.
2. 이미 존재하는 디렉터리 `my_tools`를 tool_dir로 씁니다. `mode="restricted"`를 명시해야 합니다(기본값 `"os"`는 실패 처리, 아래 참고).
3. 버그가 있는 첫 시도 코드입니다.
4. 고친 코드입니다.
5. 도구의 테스트입니다. 단순한 `assert`만 허용되며, 실제로 도구를 **실행**해야 합니다.
6. `testfailed:assertion_failed:1`을 반환합니다. 아무것도 게시·등록되지 않습니다.
7. `registered:add`를 반환합니다. `my_tools/add.py`와 `my_tools/manifest.json`이 생깁니다.
8. 레지스트리 항목은 `(description, callable)` 튜플입니다.
9. `5`를 반환합니다. 호출할 때마다 새 자식 프로세스에서 worker.py가 실행됩니다.
10. 새 Forge 인스턴스가 manifest에서 복원한 뒤 테스트를 다시 돌리고 `9`를 반환합니다. 실제 새 프로세스에서 확인하는 과정은 `demo.py` 6단계에 있습니다.

## API

- `Forge(tool_dir, mode="os" | "restricted")`: `tool_dir`은 이미 존재해야 합니다. restricted 모드에서는 생성할 때 복원을 수행하고, 실패 내역을 `restore_errors`(`[{"name", "reason"}]`)에 남깁니다.
- `forge_tool(name, description, code, test) -> str`: `"registered:<name>"` 또는 `"testfailed:<reason>"`을 반환합니다. 예외를 던지지 않으므로 에이전트 루프에서는 이 문자열을 그대로 **observation**으로 넘기면 됩니다. 첫 시도 실패 → 수정 → 재시도로 이어지는 흐름이 바로 ReAct 루프입니다.
- `TOOLS[name] -> (description, proxy)`: `proxy(*args)`는 값을 반환하고, 실패하면 `ToolInvocationError("<reason>")`를 던집니다.

### 강의 코드와 다른 점: 레지스트리 프록시

강의 예제는 게시된 `.py`를 import해서 실제 함수 객체를 `TOOLS`에 넣습니다. 이 구현은 **생성된 코드를 호스트에서 import하거나 exec하지 않습니다.** 테스트를 통과했더라도 마찬가지입니다. `TOOLS`에는 프록시가 들어가며, 이 프록시는 테스트를 통과한 **정확한 바이트**를 메모리에 들고 있습니다. 그래서 호출 시점에 디스크의 파일이 바뀌거나 지워져도 이미 검증된 바이트만 실행되고, 검사 시점과 사용 시점이 달라지는 TOCTOU 문제를 피합니다. 다만 다음 실행에서는 디스크를 다시 읽어 검증합니다.

## 허용되는 코드 (restricted-v1)

- 도구: `def name(a, b, ...)` 하나만 쓸 수 있습니다. 위치 인자는 최대 8개, 본문은 `return <식>` 한 줄입니다.
- 식에 쓸 수 있는 것: 상수, 인자, `+ - * / // % **`(크기 제한 있음), `not`/`and`/`or`, 비교, 조건식, list/dict 리터럴, 인덱싱, `abs len max min sorted sum`.
- 쓸 수 없는 것: import, 속성 접근, 루프, 컴프리헨션, lambda, 슬라이스, 임의 호출, 재귀, **도구 안에서 다른 도구 호출**. 임의 Python, 네이티브 코드, 네트워크, 파일 접근은 모두 불가능합니다.
- 테스트: `assert <식>` 1~32개입니다. 정적으로 도구 호출이 보이는 것만으로는 부족하고, 실행 중에 도구가 실제로 1번 이상 호출되어야 합니다. 예를 들어 `assert True or f(0)`은 단락 평가 때문에 도구가 호출되지 않으므로 `test:must_execute_tool`로 거부됩니다(GREEN-03).

## 영속성과 재시작

- `<name>.py`에는 정확한 소스 바이트를 저장합니다. `manifest.json`(version 1)에는 name, description, code_sha256, test, test_sha256과 정책 버전 `restricted-v1`을 저장합니다.
- 재시작하면 manifest를 엄격하게 검증하고, 각 파일을 한 번 읽어 SHA-256과 UTF-8을 확인한 뒤, 저장된 테스트를 같은 바이트로 자식 프로세스에서 다시 돌리고 프록시를 등록합니다. manifest에 없는 `.py`는 무시합니다(자동 탐색 없음).
- SHA-256은 실수로 생긴 손상이나 오래된 파일을 탐지하는 용도일 뿐 **서명이 아닙니다.** tool_dir에 쓸 수 있는 사람은 소스와 manifest를 함께 고칠 수 있습니다. 따라서 tool_dir은 신뢰하는 사용자만 쓸 수 있어야 합니다.
- 단일 writer를 가정합니다(tool_dir당 Forge 하나, 스레드 하나). 게시는 소스 파일과 manifest 순서로 두 파일을 `os.replace`하는 방식이라 두 파일에 걸쳐 크래시 원자성을 보장하지 않습니다. 중간에 끊기면 해시가 불일치하게 되고, 복원 단계에서 이를 거부합니다(fail closed). manifest가 잘못되어 있으면 아무것도 복원하지 않고, 이후 `forge_tool`도 막습니다.

## 보안 경계 (정직한 범위)

- **기본값 `mode="os"`는 fail closed입니다.** OS 샌드박스가 없으므로 `testfailed:os_sandbox_unavailable`을 반환하고 아무것도 실행하지 않습니다.
- restricted 모드의 경계는 **AST로 해석하는 기능 부분집합**일 뿐 OS 샌드박스가 아닙니다. 생성된 코드는 `ast.parse` 후 화이트리스트 검사를 거쳐 worker.py가 직접 해석합니다.
- 별도 프로세스(`python -I -S worker.py`, 최소 env, 작업용 cwd/TEMP)는 인터프리터 버그의 피해 범위를 줄일 뿐 **보안 샌드박스가 아닙니다.** OS 수준의 CPU, 메모리, PID 하드 리밋은 없습니다. 언어 차원의 복잡도·크기 제한(step 5000, int 256bit, 항목 256개 등)과 10초 wall-clock watchdog만 있습니다.
- `boundary_probe.py`가 이 내용을 실측합니다. 소켓 연결, DNS 조회, 외부 파일 쓰기, 프로세스 생성, 리플렉션/import를 시도하는 생성 코드는 정적으로 거부되어 리스너 연결도 마커 파일도 생기지 않습니다. 반면 같은 플래그로 띄운 **신뢰된 진단 자식**은 실제로 localhost에 연결할 수 있으므로, 연결에 성공하면 `"os_network_isolation": false`를 출력합니다. DNS 실패만으로는 차단되었다고 판단하지 않습니다(inconclusive).
- 실측 결과 Docker와 WSL 배포판이 없었습니다. 강의의 "임의 Python을 별도 프로세스에서 네트워크 없이 실행"하는 경로는 **이 구현에 없습니다.** 그 기능에는 별도로 검토된 OS 백엔드가 필요하며, 그런 코드가 존재하거나 준비되어 있다고 오해해서는 안 됩니다.

## 증거 (Evidence)

- 테스트, 데모, 프로브 결과는 상위(parent) 에이전트가 직접 실행해 `verification/`에 저장하고 해시로 봉인합니다. 이 README에는 실행 결과를 미리 적지 않았습니다. `verification/red-03-tests.json`은 GREEN-03 이전에 14개 중 2개가 실패한 RED 기록입니다(실패 1: 단락 평가 우회, 실패 2: 테스트 fixture가 항목 수 제한보다 바이트 제한에 먼저 걸린 문제. fixture만 `[None] * 257`로 교정했고 제한값은 바꾸지 않았습니다).
- 개발에는 실제 Claude Code(`claude-opus-5-5`)를 사용했으며, 그 기록은 `verification/*receipt.json`에 있고 상위 에이전트가 직접 실행해 검증했습니다. 프로덕션 모델은 바꾸지 않았습니다.
- 이 forge는 market이나 런타임 에이전트에 연결되어 있지 않습니다. 사용자 확장(A2A 등)은 **보류(ON HOLD)** 상태이며 배포하지 않았습니다.
