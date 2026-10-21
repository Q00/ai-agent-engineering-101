# 연구실 서버 buyer 운영

대상: SSH 별칭 `VPN_HAILAB-sv117`, 사용자 `danayou`.
서버 작업 폴더: `~/submissions/26520057/week-07`.
가게는 Cloudflare Workers, buyer와 변경하지 않은 수업 market_kit.py는 연구실 서버에 둔다.

- Python 3.10 venv와 Claude Code는 runtime/에 따로 설치한다.
- 기존 서버 Claude 인증을 사용한다. 인증 파일을 제출물에 복사하지 않는다.
- 기존 buyer_key.pem, shop_key.pem과 memory.json을 전송하여 이름/키/기억을 유지한다.
- `.market/server.env`에는 MARKET_OWNER와 실행 PATH를 저장하며 권한은 600이다. 커밋하지 않는다.
- systemd 사용자 서비스로 실행하고, 로그아웃 후에도 유지하는 linger를 활성화한다.
- 비정상 종료 시 15초 뒤 재시작하며 서버 부팅 후 자동 시작한다.
- 서버 인터넷이 끊긴 동안에는 거래할 수 없다. 로컬 VPN은 관리용 접속에만 필요하다.

## 상태와 로그
```bash
ssh VPN_HAILAB-sv117 'systemctl --user status market-buyer --no-pager'
ssh VPN_HAILAB-sv117 'tail -30 ~/submissions/26520057/week-07/logs/market-kit.log'
```

## 수정 반영
buyer.py, market_buyer.py, harness.py는 서버에 복사하면 다음 주문부터 반영된다.
agent.json이나 환경변수를 바꾸면 다음 명령으로 재시작한다.
```bash
ssh VPN_HAILAB-sv117 'systemctl --user restart market-buyer'
```
중지: `systemctl --user stop market-buyer`.
서버 로그를 내려받을 때 기존 Mac 로그를 덮어쓰지 말고 별도 파일로 보존한다.

## 현재 진행 상태 (2026-10-21)
Python 패키지/Claude Code 2.1.197 설치, 키 전송, 서비스 링크, linger 활성화 완료.
서버의 `claude auth status`가 loggedIn:false여서 아직 서비스는 시작하거나 enable하지 않았다.
Mac buyer는 유지 중이며 memory.json 최종 복사는 전환 직전에 한다.
서버에서 Claude 로그인 및 실제 AI 호출을 확인한 뒤 Mac buyer를 멈추고 서버 서비스를 시작한다.
설치 첫 시도는 진행이 멈춰 종료했고 --no-audit --no-fund로 재시도해 설치했다.
