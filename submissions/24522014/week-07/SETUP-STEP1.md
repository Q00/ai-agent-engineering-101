# SETUP Step 1: 준비

2026-10-20, Windows PowerShell, 학번 24522014 (kcwarni).

- `week-07` 브랜치에 upstream/main (9d34963)을 병합했다.
- `scripts/market_kit.py`, `scripts/trade_test_week06.py`가 있는지 확인했다.
- 설치된 Python 3.14.6으로 이 디렉터리에 `.venv`를 만들었다.
- `requirements.txt`의 패키지를 설치했다. 설치 버전은 `requirements-lock.txt`에 기록했다.
- 공식 Cloudflare GitHub 릴리스의 Windows amd64 실행 파일을 `tools/cloudflared.exe`에 설치했다.
- 저장소 AGENTS.md의 쓰기 범위를 지키기 위해 가상환경과 도구를 학생 제출 디렉터리 안에 두었다. 두 디렉터리와 비밀키는 Git에서 제외한다.

저장소 루트에서 새 PowerShell 터미널마다 다음 명령으로 활성화한다.

```powershell
$env:VIRTUAL_ENV = (Resolve-Path ./submissions/24522014/week-07/.venv).Path
$env:PATH = "$env:VIRTUAL_ENV\Scripts;${PWD}\submissions\24522014\week-07\tools;$env:PATH"
python --version
cloudflared --version
```

설치를 재현하려면 설치된 Python 3.10 이상으로 가상환경을 만든 뒤 설치한다.

```powershell
py -3.14 -m venv submissions/24522014/week-07/.venv
$env:VIRTUAL_ENV = (Resolve-Path ./submissions/24522014/week-07/.venv).Path
$env:PATH = "$env:VIRTUAL_ENV\Scripts;${PWD}\submissions\24522014\week-07\tools;$env:PATH"
python -m pip install -r submissions/24522014/week-07/requirements-lock.txt
Invoke-WebRequest -Uri https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe -OutFile submissions/24522014/week-07/tools/cloudflared.exe
```

기본 `python` 실행 별칭은 이 세션에서 실행되지 않아 설치된 Python의 실제 경로를 사용했다. 최초 pip 실행은 설치 성공 후 버전 확인 경고를 출력했다. `pip check`와 키트 `--help`로 실제 설치 상태를 별도 확인했다. 최초 cloudflared 다운로드는 연결 오류로 실패하여 승인된 네트워크 실행으로 재시도했다.

PowerShell 실행 정책으로 `activate.ps1`이 차단되어 위의 세션 환경변수 설정으로 검증했다. 시스템 실행 정책은 변경하지 않았다. 최초 검증 로그는 보존하고 재검증 결과는 `logs/setup-step1-verification-retry.log`에 남겼다. `activate.ps1`은 스크립트 실행이 허용된 환경에서만 사용할 수 있다.

이 단계는 실행 환경 준비까지만 수행한다. Step 2의 week-06 코드 복사와 이후 거래 테스트·시장 참가는 다음 단계다.
