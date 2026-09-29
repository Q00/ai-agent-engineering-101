# 실행 환경 확인 기록

2026-09-29, Windows PowerShell에서 확인했다. 모델 요청은 실행하지 않았다.

| 항목 | 실제 확인 결과 |
|---|---|
| Python | C:/Users/MASTER/anaconda3/python.exe, Python 3.12.4 |
| 작업 전용 환경 | submissions/26510121/week-05/.venv (Git 제외) |
| Codex CLI | 0.156.1 |
| CLI 로그인 상태 | codex login status: Not logged in |
| 선택 host | codex exec, ChatGPT 로그인 사용 예정 |
| 선택 모델 / reasoning | gpt-6-luna / low; 계정 접근은 미검증 |
| temperature | CLI help에 temperature 옵션 없음; 미지정 |
| MCP SDK | mcp 2.2.0 |
| 모델 호출 / 예비 실행 / 본 실험 | 모두 0회 |

초기 점검에서는 PATH에 python이 없고 py launcher에 Python이 등록되지 않아 환경이 없다고 판단했다. 이후 Anaconda의 Python 3.12.4를 발견해 이 기록을 정정한다.

## 환경 구성 과정

1. Anaconda Python으로 제출 디렉터리 내부에 .venv를 생성했다.
2. mcp>=2,<3 설치를 시도했으나 샌드박스 네트워크 제한으로 WinError 10013과 'No matching distribution'을 반환했다. 이는 SDK 버전 부재를 증명하지 않는다.
3. 네트워크 접근 승인 후 동일 명령을 재실행해 mcp 2.2.0 및 의존성 설치에 성공했다.
4. requirements.txt는 직접 의존성, requirements-lock.txt는 실제 설치된 의존성 버전을 기록한다. lock은 Windows/Python 3.12 기준이며 pywin32가 포함되므로 다른 OS에 그대로 적용하지 않는다.

SDK 선택 근거: 공식 Python SDK 저장소는 v2를 2026-07-28 규격을 지원하는 안정 릴리스로 안내한다: https://github.com/modelcontextprotocol/python-sdk . 공식 migration 페이지 직접 조회는 실패했으므로 저장소 안내와 실제 설치 결과로 확인했다.

## 재현 명령

저장소 루트에서 아래 명령을 실행한다. 다른 컴퓨터에서는 첫 줄의 Python 경로만 자신의 Python 3.12 실행 파일로 바꾼다.

```powershell
& C:/Users/MASTER/anaconda3/python.exe -m venv submissions/26510121/week-05/.venv
& ./submissions/26510121/week-05/.venv/Scripts/python.exe -m pip install -r submissions/26510121/week-05/requirements-lock.txt
& ./submissions/26510121/week-05/.venv/Scripts/python.exe -X utf8 submissions/26510121/week-05/verify_design.py
```

향후 모델 실행 전 사용자가 codex login으로 ChatGPT 계정 로그인을 완료하고, codex login status로 확인해야 한다. 설치되었다는 사실과 로그인되었다는 사실은 다르다. 이 점검에서는 API 키를 발급하거나 저장하지 않았고, CLI 모델 요청도 보내지 않았다.
