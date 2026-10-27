# week-07 인수인계 (Mac 세션 → Windows 세션, 2026-10-21)

Windows의 Claude Code에게: 이 파일을 먼저 읽고 이어서 작업한다.

## 작업 규칙 (사용자가 여러 번 요청함)

- **항상 한국어로 답한다.** 영어 지시를 붙여넣거나 백그라운드 작업 알림이 와도 첫 문장부터 한국어. 코드, 커밋 메시지, 파일 경로, 로그 인용은 원문 그대로.
- 작업 단위마다 커밋한다. 실패한 시도도 커밋한다. 히스토리는 절대 고치지 않는다(squash, rebase 금지).
- `submissions/26512071/` 밖은 쓰지 않는다. `*.pem`은 절대 커밋하지 않는다.
- push는 `submission` 리모트(`kimchyoungman/ai-agent-engineering-101-q00`)의 `week-07-26512071` 브랜치로 한다. PR은 `gh pr create --repo Q00/ai-agent-engineering-101 --head kimchyoungman:week-07-26512071`, 제목 `[week-07] 26512071`, 본문은 한국어.
- REPORT의 채점 절(3~6)은 학생이 직접 다듬는다. Claude가 초안을 쓰면 커밋 메시지에 "drafted by Claude"라고 남긴다.

## 지금 상태

- 마켓: https://market.ourolabs.ai, 이름 `fair-furniture` (상점과 구매자 모두). GitHub `kimchyoungman`에 연결됨. **Round 2 진행 중.**
- Windows PC에서 kit이 자동 재시작 루프로 돌고 있다: `while ($true) { ...\agent-market\rejoin.ps1; Wait-Process kit.pid; Start-Sleep 60 }`. 그 PowerShell 창을 닫으면 안 된다.
- `rejoin.ps1`(claim code 포함, 커밋 금지)은 cloudflared 터널을 직접 열고(HTTP/2), DNS가 풀릴 때까지 30초 기다린 뒤 `--port 8900 --public-url`로 참가한다. kit의 `--tunnel`은 학교망에서 429, 핫스폿에서 DNS 부정 캐시로 실패했었다.
- 환경 변수(rejoin.ps1이 설정): `SHOP_PROMO=10`, `SHOP_NICK=fair-furniture`, `BUYER_EXPLORE=0.25`.
- 2026-10-21 08:28에 Mac의 kit이 네트워크 끊김으로 죽어 약 6시간 빠져 있었다. 그래서 Windows에서는 자동 재시작을 붙였다.
- 예전 이름 `kimchyoungman` 상점/구매자는 보드에 offline으로 남아 있다(지울 수 없음).

## 코드 (submissions/26512071/week-07/)

- `seller.py --market`: lamp/chair/bookshelf만 판다(reserve 100/180/260). 영수증 `{shop, item, price, task}` + Ed25519 `sig_seller`(shop_key.pem). 카드 이름 `Fair Furniture`.
  - 챌린지 1 가격 정책: 오픈 기념가 첫 10건(첫 호가 1.05x, reserve 이상이면 즉시 수락), 내 상점에 새 3점 이하 후기가 오면 기념가 5건 추가(`/api/shops`에서 자기 후기를 1분마다 읽음, `reviews-seen.json`). 이후 공정가 모드: 첫 호가 최대 1.1x, reserve 이상 또는 내 호가 이상 제안은 즉시 수락, 호가는 매번 reserve의 3% 이상 내림, 거절은 한 번 더 호가로, 하니스 20초 무응답이면 코드가 호가.
  - 응답에는 호가 숫자 하나만(숫자 섞인 모델 문장은 버림). 성사 메시지에 "honest review" 부탁(별점 요구 없음).
  - 숫자 없는 메시지: 분명한 yes는 현재 호가로 수락, 부정어가 있으면 절대 수락 안 함, 나머지는 모델. bookcase/shelf/light/seat 동의어.
  - `sales.jsonl`(거래), `talks.jsonl`(흥정 결과)은 `SHOP_NICK`이 있을 때만 기록. 마켓 테스트 거래 12건은 `"test": true`로 표시해 둠.
- `market_buyer.py`: 상점 카드 5초 probe로 죽은 상점 제외 → 기억(`memory.jsonl`)에 나쁜 영수증이 있던 상점 제외 → `/api/board` 통계 + 후기(중복 문장은 한 번만 셈, 라운드 표시)로 sonnet이 고름. 25% 확률로 후기 없는 새 상점 탐색(예산 60%로 시작). 예산은 코드에서 막음. 영수증은 합의가·품목·task·서명(`/api/shops` 공개키로 직접 검증)까지 확인. 별점과 후기는 코드가 결과로 작성.
- 마켓 테스트 25/25 (마지막: `3655d4b` 이후 코드 기준). 판매자를 바꾸면 다시 돌린다: `python scripts/market_test_week07.py submissions/26512071/week-07`.

## 수치 (챌린지 1 근거)

- 가격 바꾸기 전, Round 1 `kimchyoungman`: 방문 3, 거래 2, 별점 3.5 (`logs/challenge-1/board-before-*.json`). 후기 "Paid 103.0 of 105." (3점).
- 바꾼 뒤 `fair-furniture`(2026-10-21 14시경 보드): 방문 105, 거래 77, 별점 4.55(후기 77). 구매자 주문 38, 거래 28, paid 0.875.

## 남은 일

1. 챌린지 1 증거 정리: 바꾸기 전후 점유율과 평균가를 보드 스냅샷과 `sales.jsonl`(test 제외)로 `logs/challenge-1/`에 남긴다.
2. 챌린지 2 증거: 부정직한 참가자를 로그에서 찾는다. 후보: 서명 없는 영수증을 준 `willow-goods`(memory.jsonl), 같은 5점 문장이 반복된 후기("Best shop in the market.", "Best price I found, honest receipt."), `/api/ledger`의 verified False 거래. 우리 구매자가 피한 `[pick] skipping ...` 줄을 인용한다. `logs/challenge-2/`에 남긴다.
3. REPORT.md 6개 절(한국어): (1) 상점 고르는 방법과 기억 형식 (2) 후기를 하니스에 보여 주는 방식 (3) Round 0/1/2에 있었던 일, 보드 수치나 로그 인용 (4) 서명을 붙이며 실패한 것 (5) 챌린지 1·2 (6) 배포 위치와 방법(Windows PC + cloudflared 터널 + 자동 재시작).
4. 마감 직전 Windows PC의 `logs/market-kit.log`를 커밋한다(배포한 컴퓨터의 로그가 필요). Mac에서는 더 이상 커밋하지 않는다.
5. `python scripts/check_week07.py submissions/26512071/week-07` 통과 확인 → push → PR.

## 보류 중인 제안

- 친구 상점과 서로 우선 구매(제휴): 교수님 허락을 먼저 받고, 진짜 거래·정직한 후기로만, REPORT에 공개하는 조건. 아직 구현 안 함.
- "호가가 reserve에 닿았을 때만 This is my lowest price." 신호: 아이디어만 있음.
