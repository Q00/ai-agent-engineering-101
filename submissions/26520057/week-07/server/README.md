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
서버 Claude 로그인과 실제 AI 응답(OK)을 확인했다. Mac buyer를 중지하고 최신 memory.json을 복사한 뒤 서버 서비스를 활성화했다.
서비스 active/enabled, Linger=yes를 별도 SSH 접속에서 확인했다. SSH나 Mac을 종료해도 서버가 켜져 있고 인터넷에 연결돼 있으면 계속 실행된다.

첫 실행은 Claude 실행 파일이 없어 주문이 실패했다(ledger #86363). 설치 폴더에서 node_modules가 사라진 것은 확인했지만 원인은 확인하지 못했다.
Claude Code 2.1.197을 같은 위치에 재설치하고 서비스와 같은 환경에서 harness 응답을 확인했다.
이후 시작할 때 실행 파일을 먼저 검사하도록 ExecStartPre를 추가했다. 검사 실패 시 주문을 받기 전에 서비스 시작이 실패하며 15초 후 재시도한다.

15:17:06 서버 거래 ledger #86598: lamp 100, verified True, review accepted.
원본 서버 로그 사본: `../logs/server/market-kit-20261021.log` (실패 기록 포함).
서비스 정의 검증(systemd-analyze --user verify) 통과. 실행 중인 거래를 방해하지 않고 daemon-reload로 최신 정의를 반영했다.

재설치 명령:
```bash
npm install --prefix ~/submissions/26520057/week-07/runtime/claude @anthropic-ai/claude-code@2.1.197 --no-audit --no-fund
```
인증 만료나 서버 장애가 생기면 추가 조치가 필요하다. 기존 로그는 수정하지 않는다.


## 연구실 GPT 판매자 (2026-10-21 전환)

`market-gpt-shop`이 Node.js `server/gpt_shop.mjs`를 127.0.0.1:8900에서 실행한다. `market-shop-tunnel`은 SSH localhost.run으로 공개 HTTPS 주소를 연결하고, 주소가 바뀌면 카드 URL과 market kit 등록을 자동 갱신한다. `market-buyer`는 `server/run_buyer.py`를 통해 현재 주소로 kit를 실행한다. `.market/gpt-cutover-enabled`가 전환 스위치다. 현재 주소는 서버 `.market/gpt-public-url`에서 확인한다.

GPT 모델은 `gpt-5.4-mini`, Responses API와 strict JSON schema, reasoning none이다. nano는 정상 가격/수락을 누락한 검사 실패가 있어 mini로 변경했다. 모든 구매 메시지가 AI를 거치고 가격·task·서명은 코드가 검증한다. API 오류에는 결제하지 않는다. API 사용량은 유료이며 ChatGPT 구독과 별개다.

API 키는 `python3 server/configure_gpt.py`의 숨김 입력으로 서버 `.market/gpt.env`에 0600 권한으로 저장한다. 키를 채팅이나 커밋에 넣지 않는다. `shop_key.pem`은 기존 시장 등록 키를 유지한다. 협상 기록은 `.market/gpt-tasks`의 파일로 원자적으로 저장해 재시작 후에도 보존한다. 기존 Cloudflare의 진행 중 task는 이관하지 않는다.

운영 명령:
```bash
systemctl --user status market-gpt-shop market-shop-tunnel market-buyer
cat .market/gpt-public-url
tail -30 logs/gpt-shop.log
```

서버 전원·네트워크·SSH 터널·OpenAI API 한도/잔액에 의존한다. 무료 터널 주소가 바뀔 수 있고, 이때 진행 중 외부 요청은 실패할 수 있다. Codex 대화의 10분 간격 자동화로 API 장애와 복구를 점검·알림한다. 자동화는 로컬 Codex에서 실행되므로 연구실 서비스 자체와 별개다.
