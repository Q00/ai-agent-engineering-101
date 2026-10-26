# daintI 운영 안내

SSH 별칭은 `daintI`, 배포 루트는 `/home/llddyy0319/agent-market-week07`이다.
서비스 이름은 `week07-market.service`이며 사용자 systemd 서비스로 동작한다.
사용자 linger를 활성화했으므로 Mac의 SSH 연결에 종속되지 않는다.
서버 전체를 재부팅하는 검사는 수행하지 않았다.

## 운영 명령

Mac 터미널에서 서버 상태와 최근 로그를 확인한다.

```bash
ssh daintI 'systemctl --user status week07-market.service'
ssh daintI 'journalctl --user -u week07-market.service -n 30 --no-pager'
```

마감 전에는 판매자와 구매자를 모두 유지한다. 사용자가 중지를 결정한 후에는:

```bash
ssh daintI 'systemctl --user stop week07-market.service'
```

다시 실행하거나 설정 변경을 적용할 때는 각각 start 또는 restart를 사용한다.
재부팅 시 자동 시작까지 해제하려면 `systemctl --user disable --now week07-market.service`를
사용한다. stop만 실행하면 해당 시점에는 중지되지만 다음 부팅 때 다시 시작할 수 있다.

## 배포 구조와 인증

- `repo/scripts/`: 원본 공식 키트와 검사기. 공용 저장소 파일을 수정하지 않았다.
- `repo/submissions/26510124/week-07/`: 실행 소스, 전용 `.venv`, 시장 로그와 기억.
- `bin/`: uv와 cloudflared. Python 3.12는 배포 루트의 `python/`에 설치했다.
- `~/.local/bin/claude`: Claude Code 2.1.287. 서버에서 사용자가 직접 로그인했다.
- `secrets/market.env`: GitHub claim code. 사용자만 읽는 0600 파일이며 저장소 밖에 있다.
- `~/.config/systemd/user/week07-market.service`: 이 폴더의 서비스 설정 복사본.

기존 shop_key.pem, buyer_key.pem과 거래 기억을 SSH로 이전했다. 개인키는 0600이고
Git이나 소스 배포 파일에 넣지 않았다. 서버 로그는 서버에서 계속 생성된다.
Mac의 로그 파일은 자동 동기화되지 않으므로 제출 전에 최신 원본을 회수해야 한다.
시장 운영 중 Mac에서 별도의 키트를 중복 실행하지 않는다.

서비스는 공식 키트와 `public_market.py`의 heartbeat 프록시를 이용한다.
cloudflared quick tunnel이 만든 주소를 시장에 등록하고 구매자도 같은 프로세스에서
계속 구동한다. 서비스가 종료되면 20초 후 재시작하도록 설정했다.
터널 주소는 재시작 때 바뀔 수 있으므로 최신 주소는 시장 대시보드에서 확인한다.

인증을 갱신해야 한다면 서버에서 다음을 실행한다. 비밀값은 채팅에 붙이지 않는다.

```bash
~/.local/bin/claude auth login --claudeai
python3 ~/agent-market-week07/repo/submissions/26510124/week-07/deploy/configure_owner.py
```

## 검증과 실패 기록

Mac에서 먼저 공식 25/25 검사를 통과했다. 서버의 첫 단위 검사는 원본 응답 로그
한 개가 배포에서 누락돼 1개가 오류로 끝났다. 실패 원본을 회수해 커밋하고,
`prepare_release.py`에 해당 로그를 포함시킨 후 서버에서도 38/38이 통과했다.
기존 코드를 다른 판단 로직으로 대체하지 않았다.

서버의 실제 Claude 호출도 성공했다. 공개 판매자에 대한 Mac의 A2A 진단 거래는
lamp 125에 완료되고 서명 검증을 통과했다. 이 진단은 시장 결과나 후기로
제출하지 않았으며 점유율 실적으로 계산하지 않는다.
원본은 `../logs/public-trade-check-cdaa8d73c4.log`다.

서버에서 시장이 발급한 주문의 첫 인정 거래는 ledger #877657, lamp 106이며
`verified True, review accepted`로 기록되었다. 기존 17건의 Round 2 구매 이후
서버에서 새 실적이 생겼다는 뜻이다.

배포 묶음은 `prepare_release.py`로 재생성한다. 개인키·인증·라이브 기억을 제외하고
파일별 해시를 새 `logs/deployment-*/package-manifest.json`에 기록한다.
설치 참고: [uv 설치](https://docs.astral.sh/uv/getting-started/installation/),
[Claude Code 설치](https://code.claude.com/docs/en/setup),
[cloudflared 다운로드](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/).
