# REPORT.md

Harness: Claude Code headless `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence`
(claude-haiku-4-5), 6주차와 같다. 가게는 6주차 `seller.py`(2,355개 카탈로그, 검색으로 좁힌 후보를 하네스가 고름,
하한가는 코드에서도 강제)에 영수증 서명만 더했고, `buyer.py`는 6주차 그대로(Round 0용), `market_buyer.py`가 새 코드다.
market test **25/25 PASS** (`test-report.json`, `logs/market-test.log`).

## 1. How the buyer picks a shop, and the memory format

`market_buyer.py`는 `{shops}`(이름·URL·후기 목록)와 자기 기억 파일 `buyer_memory.json`을 읽고 **두 단계**로 고른다.

1. **코드가 먼저 거른다** (`eligible()`): (a) 내 기억에 `avoid`가 켜진 가게, (b) 후기 2개 이상에 평균 별점 2.5 미만,
   (c) 영수증 불일치를 말하는 후기("receipt said", "overcharg", "higher than" …)가 2개 이상. 걸러진 가게에는
   **요청을 한 번도 보내지 않는다**. market test의 `buyer_reads_reviews`가 바로 이 경로였다:
   ```
   [skip] Kit Shop North (http://127.0.0.1:44371): reviews average 1.3 over 3
   [choose] http://127.0.0.1:35879: Kit Shop South is the only shop without a warning (reviews 3, avg 4.7; no past trades)
   ```
   후보가 하나만 남으면 하네스를 부르지 않는다(3/3 모두 하네스 호출 0회로 좋은 가게를 골랐다).
2. **둘 이상 남으면 하네스가 고른다**: 가게마다 한 줄 — 이름, URL, 후기 수, 평균 별점, 최신 후기 2개(80자), 그리고
   **내 이력 요약** — 을 주고 `{"name": ..., "why": ...}`로 받는다. 고른 이유는 `[choose] <url>: <why>`로 출력돼
   키트 로그에 남는다. 하네스가 목록에 없는 이름을 답하면 평균 별점·내 거래 수 순으로 코드가 고른다(fallback 카운트).

기억은 **가게 이름을 키**로 한다(URL은 가게가 키트를 다시 띄울 때마다 바뀐다):
```json
{"shops": {"Kit Shop South": {"trades": [
    {"when": "2026-10-22T18:14:03", "item": "lamp", "budget": 120, "status": "bought",
     "agreed": 110, "receipt_price": 110.0, "mismatch": false, "stars": 5, "rounds": 1, "url": "http://127.0.0.1:35879"}],
  "avoid": false, "avoid_reason": null}}}
```
거래마다 `agreed`(내가 보낸 마지막 숫자)와 `receipt_price`(영수증의 price)를 함께 적고, 둘이 다르면 `mismatch`와
함께 그 가게에 `avoid`를 켠다 — 다음 주문부터 그 가게는 후기가 아무리 좋아도 1단계에서 걸러진다(드라이런:
South에 불일치 기억을 넣자 후기가 없는 West를 골랐다). 하네스에게는 이 파일이 아니라 `my_history()`가 만든 한 줄
("2 past trade(s), 2 deal(s); paid 110, 105" / "AVOID: receipt 135 != agreed 110 (…)")만 보인다.

## 2. How reviews are shown to the harness

후기는 통째로 넣지 않는다. 가게당 **집계 + 최신 2개**다: `reviews=3 avg_stars=4.67 latest: 5★ Fair price, receipt
matched. | 4★ Came down to my budget, honest receipt.` 그리고 프롬프트 한 문장이 읽는 기준을 준다 — "영수증이
합의 가격과 맞았다는 후기와 가격이 내려온 가게를 선호, 영수증 불만이 있는 가게는 피하라". 단, 결정적인 신호(평균
2.5 미만, 불일치 불만 2건 이상)는 하네스가 보기 전에 코드가 처리한다. 이유는 둘이다: (a) 모델이 후기를 어떻게
읽든 명백히 나쁜 가게에 요청이 가면 안 되고(테스트 조건이자 시장에서의 손실), (b) 하네스 호출 1회(5–7초)를 아낀다.
후기를 **쓸 때**도 사실만 준다: 품목·예산·결과·내가 낸 제안들·본 호가들·합의가·영수증가·일치 여부를 한 문단으로
주고 `{"stars", "review"}`를 받되, **영수증 불일치면 별점은 코드가 1로 내린다** — 문장이 어떻든. market test에서
받은 후기 예: 성사 → 5★ "Successfully negotiated the lamp down to 110 … with accurate receipt."; 결렬 → 2★ "Made
multiple offers from 215 to 229 … the seller's final asking price of 260 exceeded my budget".

## 3. Rounds 0, 1 and 2 in the market

내 에이전트는 시장이 **이미 Round 2**일 때 들어갔다 — `[market] shop registered as yeonsong2, round 2`(10/22 18:36). Round 0
(서명 없는 영수증도 거래로 셈, 6주차 buyer를 키트가 무작위 가게에 붙임)과 Round 1(후기는 POST 하나로 누구나)은 수업 중에
지나갔고 내 로그에는 없다. 그래서 내 기록은 전부 Round 2 규칙 — 가게·buyer 양쪽 서명, 시장이 낸 주문, 품목·예산 일치,
새 task id — 아래의 것이다.

**첫 네 주문이 규칙을 그대로 보여 준다** (`logs/market-kit.log`):
```
18:36:51 [order o-1aea6b32] lamp, budget 90, round 2          ← 하한가 100보다 낮은 '불가능 주문'
18:38:35 [market] ledger #309333: no deal, stars 2, verified False, review not shown
18:38:36 [order o-9a30155d] chair, budget 185, round 2
18:38:59 [buyer] [choose] https://exceptions-…trycloudflare.com: Two independent reviews showing chair negotiations down to 180 with receipt matching …
18:39:59 [buyer] [chair @ dariaklt] bought at 183.0 after 4 offers
18:40:07 [sign] sig_seller ok, sig_buyer added
18:40:08 [market] ledger #309473: chair at 183, stars 5, verified True, review accepted
```
결렬된 거래는 ledger에는 남지만 `verified False, review not shown` — 확인할 영수증이 없으니 후기도 보이지 않는다(Round 2의
의도). 성사된 거래는 가게 서명을 키트가 검증하고 내 buyer 키로 덧서명해 `verified True, review accepted`로 들어갔다. 이것이
과제가 요구한 "Round 2로 인정된 거래"다.

**그 뒤 하루** (10/22 18:45 재시작 ~ 10/23 06:24): buyer는 주문 688건을 처리해 **인정 거래 258건**, 결렬 426건(대부분 예산이
하한가 아래인 주문), 키트 오류 0건. 가게는 협상 910건 중 성사 739(81%), buyer가 떠남 120, 거절 51. 터널은 두 번 끊겼고
(`22:07:33 … stopped answering, opening a new tunnel`, `23:12:35 …`) 키트 v2가 새 터널로 `shop registered again as yeonsong2`
— 이름·키가 같아서 거래와 후기는 이어졌다. dashboard(`/api/board?round=2`, `logs/challenge-1/board-*.json`):

| 시점 | 가게 deals | share | 방문 | ★ | 후기 | 매출 | 이익 | 평균가 | buyer 인정 거래 |
|---|---|---|---|---|---|---|---|---|---|
| 10/22 18:45 (가격 규칙 전) | 10 | 0.000 | 14 | 4.4 | 10 | 2,527 | 327 | 252.7 | 1 |
| 10/23 06:25 (약 12시간 뒤) | 747 | 0.006 | 957 | 4.56 | 747 | 147,821 | 10,881 | 197.9 | 260 |

평균가가 내려간 것은 품목 구성 때문이다(처음 10건은 책장·의자 위주, 이후는 세 품목 고루). 성사 수 기준 60개 가게 중 24위.
시장 전체는 그 시점에 에이전트 47개 온라인, GitHub 연결 참가자 41명이었다.

## 4. What failed while adding signatures, and what changed

1. **서명 바이트와 A2A의 실수(double).** 영수증은 `{shop, item, price, task}`를 키 정렬·공백 없음·UTF-8로 직렬화해
   Ed25519로 서명한다(`seller.sign_receipt`). 가격 120은 A2A data Part를 거치면 `120.0`으로 도착하므로 그대로 직렬화하면
   바이트가 달라져 서명이 깨진다. 정수로 서명하고, 검증 쪽(키트·테스트)이 `120.0 → 120`으로 되돌린 뒤 확인한다는
   규칙에 맞춰 왕복 테스트를 먼저 돌렸다: `verify after 120.0 round-trip: OK`, 가격을 200으로 바꾼 위조는
   `InvalidSignature`. 이 검사를 테스트 전에 해 둔 덕에 `seller_signed_receipt`는 첫 실행에 3/3 통과했다.
2. **`task`는 그 협상의 A2A task id**여야 한다. 6주차 영수증은 `{seller, item, item_id, price}`였고 task가 없었다 —
   starter를 그대로 쓰면 테스트가 `receipt lacks shop, task`로 떨어지는 지점. `TaskUpdater`가 쓰는 `task.id`를 그대로
   넣고, 키 이름도 `seller`→`shop`으로 바꿨다(키트는 `seller`를 `shop`으로 고쳐 주지만 서명 바이트는 `shop`이어야 한다).
3. **하네스가 하한가 아래를 역제안했다.** `seller_holds_reserve`에서 책장(하한가 260)에 230이 오자 하네스는
   `{"action": "counter", "price": 250}` — 하한가 아래 — 를 답했고, 코드 가드가 잡았다:
   ```
   [guard] counter 250 below reserve 260; raised to the reserve
   [daa093cc] offer 230 -> counter 260
   ```
   6주차 trade test에서는 한 번도 안 울렸던 가드가 이번엔 울렸다. 서명이 붙은 영수증은 "하한가 아래로 팔지 않았다"를
   밖에서 증명해 주지만, 그 약속을 실제로 지키는 것은 여전히 서명 직전의 코드 한 줄이다.
4. **키 관리.** `shop_key.pem`은 market test와 키트가 먼저 만들고 가게는 읽기만 한다(없을 때만 생성). `*.pem`,
   `.market/`, `buyer_memory.json`은 `.gitignore`에 넣어 커밋되지 않는다(`git ls-files`에 pem 없음).

## 5. Challenges 1 and 2

**Challenge 1 — 가게가 자기 거래와 후기로 호가를 바꾼다** (`seller.py`의 `Pricing`, `logs/challenge-1/`). 가게는 협상이
끝날 때마다 `shop_trades.json`에 한 줄(품목·하한가·첫 호가·마지막 호가·라운드·결과·가격)을 적고, 10분마다 `/api/board`에서
자기 후기를 읽는다. 첫 호가를 정할 때 품목별 최근 6건을 보고 **첫 호가 배수**를 움직인다: 기본 1.5×하한가, 최근 성사가
있으면 "성사가/하한가 ×1.1"과 평균해 **실제로 닫히는 수준**으로 끌리고, 결렬이 2건 이상이고 성사보다 많으면 건당 0.15씩
내리며, 후기 평균이 3★ 미만이면 0.1을 더 내린다(범위 1.05–1.9). 그 결과를 범위(`low–high`)와 이유로 하네스 프롬프트에
넣고, 하네스가 그 안에서(또는 벗어나서) 첫 호가를 고른다. **하한가는 그대로**이고 `ask >= reserve`는 코드가 계속 막는다.
```
[price] bookshelf: suggested opening 364-416 (no past trades for this item yet)          ← 켠 직후
[price] bookshelf: suggested opening 337-389 (4 deal(s) closed at ~1.17x reserve)        ← 12시간 뒤
```
전/후 비교 — 규칙을 켠 10/22 18:45 전후의 dashboard는 3절의 표(10→747건, 점유율 0→0.6%, 4.4→4.56★). 규칙이 켜진 뒤의
910건을 시간순 절반으로 나눈 내 기록(`shop_trades-snapshot.json`):

| 품목 | 첫 호가 평균 | 성사가 평균 | 성사율 | 결렬(no_deal) |
|---|---|---|---|---|
| lamp (하한 100) | 123 → 130 | 106.2 → 110.6 | 84% → 84% | 0% → 5% |
| chair (하한 180) | 221 → 230 | 190.5 → 196.6 | 84% → 79% | 5% → 7% |
| bookshelf (하한 260) | 318 → 333 | 276.3 → 284.5 | 83% → 74% | 4% → 12% |

규칙은 내가 짤 때 생각한 방향(결렬이 쌓이면 내린다)과 **반대로** 움직였다: 성사율이 81%로 높으니 "닫히는 수준×1.1"이
기본값 1.5보다 높게 작용해 첫 호가와 성사가가 **올라갔고**, 그 대가로 책장 성사율이 83%→74%로 떨어졌다. 즉 같은 규칙이
수요가 많은 시장에서는 가격 인상 장치가 된다 — 이익(건당 평균 14.6)은 늘었지만 거래는 줄었다. 하네스는 제안 범위 안에서
주로 **위쪽**을 골랐다(첫 호가 평균이 범위 상단에 가깝다). 후기 항은 거의 작동하지 않았다 — 내 후기 평균이 4.4–4.56★로
3★ 아래로 내려간 적이 없다.

**Challenge 2 — 부정직한 참가자와 내 buyer가 그를 피한 방법** (`logs/challenge-2/`). 시장의 house 가게 **`maple-home`**
(`/api/board`: `house: true, deals: 2, stars: 1.0`)이 그 참가자다. 키트가 내 buyer에게 넘긴 가게 목록에서 그 가게의
후기는 셋 다 같은 패턴이었다(`maple-home-snapshot-1.json`):
```
1★ "Agreed 120, receipt says 135."
1★ "Agreed 310, receipt says 355."
1★ "[WARNING] chair: agreed 185, receipt says 210. Receipt does not match the deal. Avoid."
```
합의한 값보다 높은 가격을 영수증에 적는 가게다 — Round 2에서는 buyer가 그 영수증에 덧서명하지 않으면 거래로 인정되지
않지만, buyer가 눈치채지 못하고 서명하면 그대로 올라간다. 내 buyer는 **첫 주문부터** 이 가게를 1단계(코드 필터)에서
걸렀다: `18:36:52 [buyer] [skip] maple-home (…): reviews average 1.0 over 3`. 하루 동안 688주문에서 같은 줄이 **695번**
찍혔고, 이 가게에 보낸 요청은 **0건**이다. 하네스는 이 결정에 관여하지 않았다(후보에 들어가지도 않았다).

같은 필터가 다른 가게도 걸렀다는 점은 적어 둔다: `[skip] corner-store … : 2 reviews report a receipt that did not match the
agreed price`(62회)와 dariaklt(15회). 두 가게는 보드에서 4.34★/3,527건, 4.77★/907건으로 정직한 가게이고, 내 buyer도 각각
94회·107회 거래해 **영수증 불일치를 한 번도 겪지 않았다**(`[receipt] MISMATCH` 0건, 기억 파일의 `avoid`도 0). 즉 키트가
넘기는 후기 창(최신 몇 개)에 불만 후기 2건이 잠깐 들어온 순간에만 걸러졌고, 다음 주문에서는 다시 후보가 됐다. 키워드
규칙의 거짓 양성이지만 비용은 없었다 — 그 주문은 다른 가게(주로 grand-furniture, 321회)에서 성사됐다. 반대로 maple-home은
창이 어떻게 바뀌어도 평균 1.0★이라 한 번도 후보에 오르지 못했다. 교훈: **집계 신호(평균 별점)**는 안정적으로 부정직한
가게를 걸렀고, **문장 키워드**는 창에 따라 흔들렸다.

## 6. Deployment

가게와 buyer는 **항상 켜져 있는 연구실 NAS**(Linux, 이 과제를 개발한 서버)에서 돈다. 시장이 제공하는
`agent.md`의 Linux 절차를 그대로 따랐다: `install.sh --name yeonsong2 --owner <claim code> --no-join --dir
submissions/26510125/week-07` → `~/agent-market/`에 venv(Python 3.11)·a2a-sdk·키트(market_kit v2)·cloudflared가 설치되고
`join.sh`가 생성됨 → `join.sh --background`(setsid+nohup, pid는 `agent-market/kit.pid`, 로그는 `agent-market/join.log`와
이 폴더의 `logs/market-kit.log`). 이 서버엔 `curl`이 없어서 `agent-market/bin/curl`에 Python으로 만든 `curl -sL/-o`
호환 shim을 두고 install.sh는 원문 그대로 실행했다. 키트가 내 `seller.py`를 띄우고 프록시를 cloudflared 빠른 터널로
공개한다(`[tunnel] https://<random>.trycloudflare.com … [shop] your seller on port 47163; others reach it through the kit
at …`). 터널 호스트명은 이 네트워크에서 DNS가 풀리기까지 **약 60초**가 걸렸고(처음 두 번은 내 launcher가 120초 전에
재시작해 버려 호스트명만 바뀌는 경쟁이 있었다 — 그래서 fallback 스크립트 `run_kit.sh`는 터널을 먼저 띄워 DNS가 풀린
뒤 `--public-url/--port`로 키트에 넘기도록 고쳤다), 그 뒤 키트가 등록했다:
```
18:36:51 [market] shop registered as yeonsong2, round 2
18:36:51 [market] buyer registered as yeonsong2, round 2
```
`/api/board`에는 두 에이전트 모두 `owner.login = "yeonsong2"`(claim code로 GitHub 연결), `alive = true`로 올라 있다.
닉네임은 agent.md의 제안대로 GitHub 로그인과 같게 했다. `agent.json`의 `python`은 a2a-sdk가 깔린 3.10이어야 하므로
join 전에 `PATH=/opt/conda/bin:$PATH`를 둔다. 하네스 바이너리(`claude`)는 PATH에 없고 `harness.py`가 VS Code 확장에
번들된 바이너리를 직접 찾는다. 서명 키 `shop_key.pem`/`buyer_key.pem`과 claim code(`~/.market_owner`, `join.sh`에
내장)는 모두 저장소 밖이거나 `.gitignore`에 있다. 재시작: `~/agent-market/join.sh --background`(같은 이름·키라 거래와
후기가 이어진다); 종료: `kill $(cat ~/agent-market/kit.pid)`.
