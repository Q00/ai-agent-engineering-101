# 연구실 Ubuntu로 이전

Windows의 WSL Ubuntu와 독립 Ubuntu 모두 Ubuntu 터미널에서 실행합니다.
Windows에서 WSL을 종료하거나 PC가 절전/종료되면 에이전트도 중단됩니다.

Windows 로그인 시 supervisor 복구 등록은 **관리자 권한으로 연 PowerShell**에서 다음처럼 관리합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\windows_market_recovery.ps1 install
powershell -ExecutionPolicy Bypass -File .\windows_market_recovery.ps1 status
powershell -ExecutionPolicy Bypass -File .\windows_market_recovery.ps1 remove
```

동일한 작업 이름에 예상과 다른 명령이 이미 있으면 덮어쓰거나 삭제하지 않고 `conflict`로 중단합니다.

## 설치

```bash
sudo apt update
sudo apt install -y git python3 python3-venv unzip tmux
git clone --branch codex/week-08 https://github.com/morethanwhatiwant/ai-agent-engineering-101.git
cd ai-agent-engineering-101
python3 -m venv .venv
source .venv/bin/activate
pip install -r submissions/25512088/week-08/requirements.txt
```

cloudflared도 Ubuntu에 설치하고 `cloudflared --version`으로 확인합니다.
공식 설치 안내: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/
현재 Round 3 실행에는 Codex CLI/API 키가 필요하지 않습니다.

## 개인 상태 복원

Mac에서 셀러·바이어를 종료한 뒤 최종 백업을 만드는 것이 가장 정확합니다.
실행 중 생성한 ZIP은 각 SQLite DB는 일관되게 백업하지만 모든 파일의 동일 시점 스냅샷은 아닙니다.
ZIP 이후 거래가 있다면 종료 후 다시 백업해 옮기세요. 두 컴퓨터에서 동시에 실행하지 마세요.

개인 ZIP을 Ubuntu에서 접근 가능한 경로에 놓고 저장소 루트에서:

```bash
unzip /path/to/market-private.zip
chmod 700 submissions/25512088/week-08/.market
chmod 600 submissions/25512088/week-08/*.pem submissions/25512088/week-08/.market/owner.env
```

WSL에서 Windows 다운로드 폴더는 `/mnt/c/Users/<Windows사용자>/Downloads`입니다.
ZIP에는 가게·바이어 키, 소유자 코드, SQLite 기억, 거래 기록이 들어 있습니다. GitHub에는 올리지 않습니다.

## 실행 (터미널 두 개, 각각 저장소 루트)

셀러:
```bash
bash submissions/25512088/week-08/run_ubuntu.sh shop
```

바이어:
```bash
bash submissions/25512088/week-08/run_ubuntu.sh buyer
```

터미널을 닫아도 유지하려면 각각 `tmux new -s shop`, `tmux new -s buyer`로 세션을 먼저 만드세요.
분리는 Ctrl-B 다음 D, 다시 연결은 `tmux attach -t shop` 또는 `tmux attach -t buyer`입니다.
tmux는 PC 절전/종료나 WSL 종료를 막지는 않습니다.
재시작하면 터널 URL이 달라지며 kit가 새 주소로 가게를 등록합니다. 기존 PEM 키는 반드시 유지하세요.

최종 ZIP 재생성 (Mac 저장소 루트에서, 새 파일명 사용):
```bash
python3 submissions/25512088/week-08/backup_private.py ../exports/market-private-final.zip
```

## 시장조사 연결 버전

`run_ubuntu.sh`는 이제 `market_runtime.py`로 실행합니다. 기존 키트 명령으로
실행한 두 프로세스는 한 번 재시작해야 연구 워커와 buyer 전용 포럼 큐가 적용됩니다.
시장조사 보고서: `.market/market-report.json`, 공동 기억: `.market/market_intel.sqlite3`.
수집 중단/표본 부족 시 무리한 발주를 하지 않고, 셀러는 원가 보호 가격을 사용합니다.
이 구성은 규칙 기반이며 LLM이 자동으로 매매를 결정하지 않습니다.

Mac 기존 가상환경에서 새 버전 실행 (저장소 루트, 역할별 터미널):
```bash
python submissions/25512088/week-08/market_runtime.py join --dir submissions/25512088/week-08 --market https://market.ourolabs.ai --name 25512088 --tunnel --shop-only
python submissions/25512088/week-08/market_runtime.py join --dir submissions/25512088/week-08 --market https://market.ourolabs.ai --name 25512088 --buyer-only
```
두 명령을 각각 별도 터미널에서 실행합니다. 기존 프로세스와 중복 실행하지 않습니다.
기존 개인 ZIP에는 새 공동 SQLite가 없으므로, 연구실 이전 직전에 최종 백업을 다시 만듭니다.
