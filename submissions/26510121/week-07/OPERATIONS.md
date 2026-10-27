# 운영 방법

저장소 루트의 Python 3.12 가상환경과 `agent-market/join.ps1`을 사용한다. 기본 모델은 `gpt-4o-mini`다. 키는 승인된 기존 `../week-06/.env`에서 실행 환경으로만 읽는다. API 키, claim code, PEM은 커밋하지 않는다.

## 기동과 상태

claim code를 보호된 `MARKET_OWNER` 환경변수에 넣은 환경에서 다음 두 역할을 별도로 실행한다. 이미 같은 역할의 PID가 살아 있으면 중복 실행을 거부한다.

```powershell
& .\submissions\26510121\week-07\agent-market\join.ps1 -BuyerOnly
& .\submissions\26510121\week-07\agent-market\join.ps1 -ShopOnly -TunnelProvider pinggy
```

현재 과제 작업에서는 두 명령을 에이전트가 직접 실행했다. 다시 기동할 때도 기존 `shop_key.pem`과 `buyer_key.pem`을 유지해야 같은 참가자로 이어진다. `agent.json`을 바꾸면 해당 키트를 다시 실행한다.

- PID와 콘솔 로그 경로: `.market/session.json`, `.market/shop-session.json`.
- 공통 원본 운영 기록: `logs/market-kit.log`.
- 실제 시장의 승인 결과: `memory/market-verification.jsonl`.
- 공개 상태: <https://market.ourolabs.ai/board>의 Round 2, `seochanit`.
- 공개 URL은 세션마다 바뀌며 키트가 registry에 새 URL을 등록한다. 특정 과거 URL을 고정 운영 주소로 사용하지 않는다.

## 계속 켜 두기

배포 장소는 이 Windows PC다. VPS나 Workers에 새로 배포했다고 주장하지 않는다. 키트가 실행되는 동안 Windows의 자동 유휴 절전을 방지하며, 종료하면 해당 요청도 해제된다. 수동 종료·로그아웃·뚜껑 닫기·정전·인터넷 단절까지 막지는 못한다. 마감 점검 때 이 PC와 네트워크가 계속 동작해야 한다.

Shop의 무료 Pinggy 세션이 종료되면 같은 판매 프로세스와 키로 터널을 다시 열고 새 주소를 등록한다. 연속 3회 실패하면 로그를 남기고 종료하므로, 외부 장애가 무한히 복구된다고 보장하지 않는다. Buyer와 Shop의 거래 판단은 실제 API 비용을 발생시킨다. 현재 코드에는 누적 비용 상한이 없다.

## 종료

```powershell
& .\submissions\26510121\week-07\agent-market\stop.ps1
& .\submissions\26510121\week-07\agent-market\stop.ps1 -ShopOnly
```

각 명령은 기록된 PID가 실제 키트인지 검사하고 해당 프로세스 트리만 종료한다. Buyer·판매자·모델 worker·공개 터널이 함께 끝난다. 이 종료 요청 이후 다시 기동할 때까지 추가 거래 판단을 하지 않는다.

## 재검증

```powershell
.\.venv\Scripts\python.exe scripts\market_test_week07.py submissions\26510121\week-07
.\.venv\Scripts\python.exe scripts\check_week07.py submissions\26510121\week-07 --github seochanit
```

market test에는 실행 환경의 기존 API 키가 필요하다. 테스트 메모리는 실거래 메모리와 구분하고, 이전 결과를 보관한 뒤 공식 스크립트가 만든 `test-report.json`을 사용한다. 원본 로그는 편집하지 않는다.
