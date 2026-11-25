# Linux 홈 서버로 이전

코드·의존성·시장 규칙·실험 기록은 `week-08-26510122` 브랜치에 있다. 기존 가게/바이어를 그대로 이어가려면 **Git clone + 별도 비공개 상태 묶음**이 필요하다. 키, owner code, DB, Codex 로그인은 Git에 올리지 않는다. 개인용 `flow/`도 기존 요청대로 제외한다.

준비 도구는 서비스를 자동으로 중단하거나 시작하지 않는다. 현재 맥 서비스는 그대로 돌고 있다. 실제 전환 시 아래 순서대로 **맥 중단 → 최종 export → 전송 → Linux restore → 시작**한다. 두 컴퓨터에서 같은 buyer/shop을 동시에 실행하지 않는다.

## 1. Linux에서 코드와 환경 준비

Python **3.10 이상**, Git, OpenSSH client, systemd 사용자 서비스가 필요하다. 현재 맥은 Python 3.13.14 / Codex CLI 0.163.0 / market kit v4로 실행했다. Python 가상환경은 맥에서 복사하지 않고 Linux에서 새로 만든다. 별도 GPU는 사용하지 않는다.

```bash
git clone --branch week-08-26510122 https://github.com/outSeop/ai-agent-engineering-101-submissions.git
cd ai-agent-engineering-101-submissions/submissions/26510122/week-08
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Debian/Ubuntu에서 venv/SSH가 없으면 해당 배포판의 `python3-venv`, `openssh-client` 패키지를 먼저 설치한다.

Codex CLI를 설치하고 **서비스를 돌릴 Linux 사용자로** 로그인한다. 공식 설치 방법의 하나는 `npm install -g @openai/codex`이다. 브라우저 없는 서버는 `codex login --device-auth`를 사용할 수 있고, 계정 설정에 따라 device-code 로그인을 먼저 활성화해야 한다. API 키 방식은 별도 API 과금이므로 기존 로그인 방식을 임의로 바꾸지 않는다. 로그인 캐시는 이 이전 도구에 포함되지 않는다.

```bash
codex --version
codex login --device-auth
codex login status
codex exec --help
```

현재 `harness.py`가 쓰는 `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, `--strict-config`, `--output-schema`, `--output-last-message`를 설치된 CLI가 지원하는지 확인한다. 서비스의 PATH에는 render를 실행한 시점의 Codex 설치 경로가 들어간다. nvm으로 설치했다면 해당 Node 경로를 삭제하지 않는다.

출처: [OpenAI 공식 CLI 설치](https://developers.openai.com/codex/cli), [서버 로그인·인증](https://developers.openai.com/codex/auth).

전환 전에 Linux에서 검증한다. 아래 모델 점검은 작은 실제 모델 호출 1회이며 시장에는 접속하거나 거래하지 않는다.

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python - <<'PY'
from harness import decide_json
answer = decide_json('migration smoke check', {'instruction': 'Reply ok=true.'}, {
    'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
    'required': ['ok'], 'additionalProperties': False})
assert answer['ok'] is True
print('Model authentication and schema output OK')
PY
```

## 2. 맥에서 중단 후 최종 상태 내보내기

맥의 이 브랜치를 최신으로 맞춘 뒤 해당 `week-08` 폴더에서 실행한다. 먼저 정상 실행 중인 buyer/seller에 대한 최종 중단 시점을 잡는다. 아래 bootout은 launchd의 자동 재시작도 함께 중단한다. 단순 kill만 하지 않는다.

```bash
launchctl bootout gui/$(id -u)/ai.ourolabs.market.outseop.r3-buyer
launchctl bootout gui/$(id -u)/ai.ourolabs.market.outseop.r3-buyer-logdb
launchctl bootout gui/$(id -u)/ai.ourolabs.market.outseop.r3-seller
.venv/bin/python ops/home_server.py export --output state/migration/home.handoff.tar.gz
```

자식 프로세스가 종료 중이면 export가 거절된다. 종료를 기다린 뒤 export를 다시 실행한다. 이미 생성한 파일을 덮어쓰지는 않으므로 이전 실패 파일이 있으면 새로운 출력 이름을 쓴다.

묶음에는 두 runtime의 기존 서명 키, owner code, SQLite DB, 정책/미확정 매입 intent, 최근 원본 로그, `.market` 자료, 검증된 frozen release가 들어간다. SQLite는 backup API로 복사해 WAL에 있는 확정 기록도 보존한다. macOS 절대 경로를 가리키던 키 링크는 실제 파일로 담는다. 옛 release 백업·PID·잠금·터널 known_hosts·가상환경은 이전하지 않는다. 묶음은 권한 0600이며 `.gitignore` 대상이다.

SSH로 홈 서버에 전송한다. 아래 `USER@HOME_SERVER`는 실제 접속 주소로 바꾼다. 접속 정보는 저장소에 넣지 않는다.

```bash
scp state/migration/home.handoff.tar.gz USER@HOME_SERVER:~/home.handoff.tar.gz
```

## 3. Linux에서 복원 및 서비스 등록

Linux의 `week-08` 폴더에서 실행한다. 복원은 기존 runtime이 있으면 덮어쓰지 않고 거절한다. 기존 market 키를 새로 만들거나 `--prepare-only`로 state를 초기화하지 않는다.

```bash
.venv/bin/python ops/home_server.py restore ~/home.handoff.tar.gz
.venv/bin/python ops/home_server.py render
mkdir -p ~/.config/systemd/user
install -m 644 state/systemd/outseop-r3-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now outseop-r3-seller.service outseop-r3-buyer.service outseop-r3-buyer-logdb.service
```

로그아웃 후에도 사용자 서비스를 유지하고 부팅 시 시작하려면 관리자가 해당 사용자에 대해 linger를 활성화한다.

```bash
sudo loginctl enable-linger "$USER"
```

복원은 Linux 경로에 맞춰 `agent.json`을 재작성하고, release 해시를 확인한다. seller 로그는 다시 현재 `week-08/logs`에 연결해 기존 분석 도구와 기록을 이어간다. 새 터널 URL은 supervisor가 시장에 등록한다. 기존 미확정 매입은 초기화하거나 재요청하지 않는다. buyer는 plain 모드, epsilon 0.1이며 역할극과 buyer 포럼 게시를 켜지 않는다. 판매 행사 종료 시각과 할인율도 그대로 보존한다.

## 4. 가동 확인

```bash
systemctl --user status outseop-r3-seller outseop-r3-buyer outseop-r3-buyer-logdb
journalctl --user -u outseop-r3-seller -u outseop-r3-buyer -n 60 --no-pager
cat state/seller-runtime/running.json
cat state/buyer-runtime/running.json
cat state/buyer-runtime/state/buyer-logdb-status.json
```

seller의 새 URL `/.well-known/agent-card.json`이 200을 반환하고, 시장 등록 주소가 같으며, `.market/inventory.json`이 갱신되는지 확인한다. 이후 실제 새 receipt/정산, buyer DB 인덱싱, 원래 가게의 잔고·재고가 이어지는지 본다. 비공개 묶음은 확인 후 안전한 위치에 보관하거나 삭제한다.

프로토콜 task/협상과 로컬 기억은 DB에서 이어지지만, kit 메모리에만 있던 진행 중 ACP checkout session이나 Codex 프로세스 자체는 컴퓨터 간 이동되지 않는다. 전환 중 진행 중이던 일부 주문은 완료되지 않을 수 있다. 맥을 다시 켤 때도 서비스 중복 실행 여부부터 확인한다.

## Git에 포함한 범위와 검증 한계

실행 소스, pinned Python 의존성, 운영 도구, 테스트, 규칙/방법론, 실험·감사 결과, vendor kit 출처와 원본 로그 스냅샷을 포함한다. 계속 커지는 운영 로그는 `logs/home-server-handoff-20261104/`에 변경하지 않은 복사본과 해시/시각을 남겼다. 실행 중인 원본은 계속 기록하며 Git 추적에서 제외한다. 실제 전환 시점의 최종 상태는 위 private export로 가져간다.

이전 도구는 임시 runtime으로 키 링크 해제, DB/로그/정책 보존, 경로 재작성, active-source 차단, 기존 runtime 덮어쓰기 차단, 위험한 tar 경로 거절을 테스트했다. 현재 맥 서비스는 중단하지 않았다. 홈 Linux의 실제 설치/로그인/네트워크/시작은 아직 실행하지 않았다.
