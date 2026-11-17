# Week 08 (중간 평가) — Round 3 가게 `nocha12`와 buyer `nocha12`

가게 이름은 Nocha Furniture, 학번은 26622007이다. 거래 판단은 Luna가 하고, 가격·예산·파산선처럼 틀리면 돈이 나가는 계산은 코드가 맡는다. 그 위에서 메타 하네스(Terra·Astra)가 설정·기억·도구를 고쳤고, 메타 하네스도 넘을 수 없는 선은 사람만 고치는 `hard_limits.py`에 두었다. 구조 그림은 [architecture.pdf](architecture.pdf)(소스 [architecture.tex](architecture.tex))에 있다.

각 주장 끝의 `파일:줄`은 이 폴더 기준 근거 위치다. 숫자는 근거 파일에 있는 값만 썼다.

## 1. 부품별 설계

### 1.1 모델

| 자리 | 모델 | 하는 일 | 근거 |
|---|---|---|---|
| 가게 대화, buyer 가게 선택·흥정, 재고 매입, 게시판 | `openai/gpt-6-luna` (OpenRouter, OpenAI 엔드포인트 고정) | 한 번에 결정 하나, strict `json_schema`로 답 | `agent.json:2`, `harness.py:19-26` |
| 메타 하네스 Terra | `gpt-6.1-terra` | 매시간 설정 조정·기억 정리·고장 난 도구 수리 | `meta_harness.py:1-8`, `:40` |
| 메타 하네스 Astra | `gpt-6-astra` | 12시간마다 전략 비교·새 도구·Terra 채점 | `meta_harness.py:1-8`, `:41`, `:45` |

- 추론 강도는 low이고, buyer의 흥정 턴만 none이다. 기록된 80턴을 11/07에 다시 돌려 보니 none이 중앙값 1.22초, low가 4.19초였고 80턴 모두 같은 행동을 골랐다(`harness.py:51-55`).
- 11/06에 거래 에이전트를 codex 경로에서 OpenRouter로 옮겼다. 맥 미니에서 codex 경로는 호출마다 429와 35초 재시도가 났다(`agent.json:2`, `harness.py:19`).
- 시간당 호출 상한은 끈 상태가 기본이다. 300회 상한에서 가게가 하루 수천 건의 흥정을 포기했다(사람 결정, 11/05; `harness.py:56-58`).
- 모델 답은 harness에서 필드·타입·enum을 다시 검사한 뒤에 쓴다(`harness.py:28`). 실패하면 `HarnessError`가 나고 각 호출부가 규칙으로 돌아간다(`harness.py:95`, 예: `restock.py:829-831`).
- 모델이 정하지 않는 것: 가게는 하한가 아래로 부르거나 팔지 않는다(`seller.py:8-10`). buyer는 예산과 가게 호가를 넘겨 부르지 않는다(`buyer.py:8-9`). 정가보다 비싸게 사지 않는다(`market_buyer.py:24-26`).

### 1.2 컨텍스트와 메모리

지난 거래와 평판은 네 갈래로 다음 결정에 들어간다.

1. **가게 노트** (`agent_memory.py:1-11`). OpenClaw 개인 비서의 기억 방식을 그대로 옮겼다. `MEMORY.md`(장기, 4,000자 상한), 일일 기록, `strategies/<slug>.md`(실수에서 나온 교훈, 발동 조건 포함)로 나눈다. 결정마다 `select()`가 그 결정에 맞는 줄만 기본 2,500자 안에서 고른다. 게시판 결정은 1,500자다(`agent_memory.py:22`, `:145`, `forum.py:94`). 오래된 일일 기록은 하루 한 줄로 접어 장기 기억에 넣는다(`agent_memory.py:193`). 재고 결정(`restock.py:823-836`)과 게시판 결정(`forum.py:94`)이 이것을 받는다.
2. **거래 기억**. 가게는 흥정마다 하한·첫 호가·주고받은 값·결과를 남기고, 시장의 리뷰로 첫 호가 상한을 정한다(`shop_memory.py:1-14`). buyer는 Round 3 전용 기억을 SKU·가게별로 따로 두고, 이전 라운드에서는 영수증을 속인 가게만 넘겨받는다(`market_buyer.py:28-32`). 산 뒤 고장 난 물건은 그 가게 기록에 연결된다(`market_buyer.py:30`).
3. **결정의 결과**. 재고 결정이 어떻게 됐는지(요청·판매·잔여·폐기)를 계산해 다음 결정에 준다(`decision_memory.py:1-5`). 판매 시점 기준 코호트로 6시간 숙성 여부, 등급별 표본, 원가 누락 수를 나눈다. 원가가 비면 순이익을 0이 아니라 null로 둔다(`profit_cohorts.py:1`, `meta/REPORT-notes.md:34-38`).
4. **시장 추정**. 공개 ledger에서 SKU·카테고리별 수요와 buyer별 선호를 추정한다. 최근 거래에 가중치를 더 주고, 내 buyer 몫과 한 가게에만 사는 buyer는 뺀다(`market_model.py:1-14`).

메타 하네스는 매 사이클 `scorecard.py`가 만든 같은 숫자표에서 시작한다(`scorecard.py:1-6`). 바꾼 이유는 그 표의 줄을 인용해야 하고, 인용을 확인하지 못하면 변경을 받지 않는다(`meta_harness.py:13`).

### 1.3 툴

- **형식**: `tools/<이름>/manifest.json + tool.py`. 인자는 JSON Schema, selftest 인자를 둘 수 있다. 결정할 때마다 다시 읽는다(`agent_tools.py:1`, `tools/cohort_profit/manifest.json`). 지금 18개다(`tools/` 아래 manifest 수).
- **쓰는 법**: 에이전트는 도구를 먼저 부르고 결정한다. 결정은 실제로 부른 호출을 인용해야 하고, 인용이 없거나 없는 호출을 대면 되돌려 보낸다(`agent_loop.py:1-6`).
- **격리**: 도구는 macOS sandbox-exec 안에서만 돈다. 네트워크·키·`.env`·다른 프로세스를 볼 수 없고, 쓰기는 호출마다 새로 만든 폴더에만 된다. selftest나 최근 호출 대부분이 실패한 도구는 고칠 때까지 목록에서 빠진다(`agent_tools.py:3-9`).
- **스스로 만드는 도구**: 6시간마다(`pricing.json:277`) 같은 질문을 반복한 흔적을 찾아 Luna가 그 일을 한 번에 하는 도구를 쓴다. 허용된 import만 쓰고 selftest를 통과해야 `tools/auto_*`로 들어간다. 통과 못 한 도구는 이유와 함께 `.market/forge_rejected/`로 간다(`tool_forge.py:1-14`).
- **사람·메타 하네스가 만든 읽기 도구**: `ledger_search`, `my_state`, `my_customers`, `my_purchases`, `cohort_profit`, `market_opportunity`, `supply_audit`, `forum_read` 등. `forum_read`는 다른 에이전트가 쓴 글을 지시가 아닌 자료로 다루라고 manifest에 적혀 있다(`tools/forum_read/manifest.json`).

**도구에서 배운 것**: 실행 오류 0이 곧 성공은 아니었다. 자동 도구 `auto_market_lookup`은 100회 호출에 실패가 0이었지만 빈 검색만 반복했고(`meta/REPORT-notes.md:27`), 668회 호출 시점에도 실패 0인 채 SKU 키를 버리고 미결제 행을 돌려줬다(`meta/REPORT-notes.md:138`). 같은 이유로 `auto_catalog_ledger_search`(`:28`)와 bundle/forum 자동 도구(`:177-178`)를 지웠다. `auto_market_research_pack`은 의미 검사 4개를 새로 만들어 고쳤다(`:216-220`). 그 뒤로는 실제 sandbox에서 도는 의미 검사 fixture로 실행 성공과 내용 정확성을 따로 본다(`meta/REPORT-notes.md:33`, `:140`, `:178`).

## 2. 아키텍처 figure와 키트 기본형과의 차이

[architecture.pdf](architecture.pdf)는 위에서 아래로 시장 → 키트 v4 → 내 에이전트 4개 → harness → 컨텍스트·도구 → 메타 하네스·hard_limits 순서로 그렸다. 회색은 강의 키트(기울임 글씨는 내가 고친 부분), 파란색은 내 에이전트, 빨간 화살표는 메타 하네스가 고치는 경로다.

| | 키트 기본형 (`week-08.html` Round 3 절) | 내 구조 |
|---|---|---|
| 재고 | restock 명령이 없으면 키트가 무작위로 산다 | ledger 수요 추정으로 공식이 제안하고, Luna가 도구를 보고 정한다. 모든 줄에 `guard()`와 파산선이 걸린다(`restock.py:9-21`) |
| 가게 | `seller_r3.py`: 원가 아래로 안 팔고, 품질 이야기와 보증이 없다 | Luna가 대화하고, 코드가 하한을 지킨다. 실제 등급·인증서·보증 시간을 checkout에 싣는다(`acp_shop.py:13-16`, 커밋 4e01c445). 거절 목록과 1점 리뷰 buyer는 checkout에서 막는다(`acp_gate.py:1-16`) |
| buyer | 키트는 checkout·mandate 서명과 `accept` 확인만 한다. 가게 선택과 흥정은 직접 만든다 | 가게 선택은 Luna(도구 인용), SKU·예산·정가 상한·영수증 검사는 코드. 키트가 결제 전에 확인할 `accept`를 넘긴다. 식품은 인증서와 남은 시간, 비식품은 보증 시간, 보증 상품을 살 때는 등급까지 본다(`market_buyer.py:698-705`, `:3155-3158`) |
| 컨텍스트 | 없음 (`.market/` 파일만) | 가게 노트·거래 기억·결정 결과·시장 추정, 결정별 `select()` |
| 도구 | 없음 | sandbox 도구 18개, 자동 생성 도구, 인용 의무 |
| 고치는 주체 | 사람 | Terra(매시간)·Astra(12시간). 바꿀 수 없는 선은 `hard_limits.py` |

**키트도 고쳤다.** `scripts/market_kit.py`는 강의 main 대비 386줄 추가, 32줄 삭제다. 운영하다 막힌 곳만 고쳤고, 정산 경로(`/api/settle`로 보내는 mandate)는 그대로다.

| 고친 것 | 이유 | 커밋 |
|---|---|---|
| checkout에서 거절 목록 buyer를 재고 없음으로 돌려보냄 | 사람이 지정한 buyer와는 정산하지 않기 위해 | e356a5a2, 58caaa9e |
| 결제하지 않은 checkout은 unit을 180초만 잡음 | 가게는 120초 뒤 그 unit을 다시 파는데 키트는 계속 '재고 없음'으로 거절했다 | ebd11eff |
| 공급 POST와 429 뒤 재시도 직전마다 잔고를 다시 읽음 | 파산선 아래로 내려가는 매입 줄을 보내지 않기 위해 | 50066a97 |
| 공급 호출 간격을 키트가 직접 맞춤(마지막 호출 10초 뒤) | 429 왕복을 줄이기 위해 | a6452e32 |
| 시장이 거절한 결제의 실패 검사 이름을 로그에 남김(원문은 남기지 않음) | 3.3절 같은 거부 사유 집계를 위해 | 125571ff |
| 공급 한 번에 보내는 줄 수 상한을 20줄로 엶 | 사람 지시(11/11 12:05) | c0c43bb3 |
| 가게 프로세스가 3 GB를 넘으면 대화를 비운 뒤에만 재시작, 6시간에 두 번 재시작하면 6시간 쉼 | 가게 기억이 커졌고, 새 프로세스는 처음 몇 분에 3.1~3.3 GB까지 올랐다 | b31475ae, 8a506d7b, 660947fb |

**hard_limits.py**는 메타 하네스가 건드리지 못한다. 보호 파일을 고치거나, 이 모듈을 부르는 줄을 지우거나, 허용 폭 밖으로 설정을 옮기는 변경은 되돌린다(`hard_limits.py:13-14`). 남은 선은 셋이다. 매입 뒤 잔고가 파산선(50만) + 아직 물어 줄 수 있는 환불 + 하루치 보관료(최소 100만)보다 위에 있어야 하고, 설정 값은 한 사이클에 정해진 비율까지만 내릴 수 있고, 도구는 sandbox 안에서만 돈다(`hard_limits.py:4-11`). 재고·자본 상한과 올림 폭 제한은 11/06 19:03 사람이 풀었다. 그때 남긴 선은 "파산만 안 되면"이다(`hard_limits.py:6-7`, `:22`).

**메타 하네스 한 사이클**: scorecard → 모델이 로그를 읽고 이 폴더를 고침 → 인용 확인·테스트 → 커밋 → 10분 지켜봄 → 한 시간 거래 → 다음 사이클이 채점(`meta_harness.py:13-17`). 실패한 변경도 커밋한 뒤 되돌려서 시도가 기록에 남는다. `meta/changes.jsonl`에 11/03 21:05부터 11/11 19:50까지 168 사이클(Terra 153, Astra 15)이 있다. 상태별로는 `no_change` 62, `rejected` 49, `running`(10분 점검 통과) 40, `no_change_unsupported` 13, `canary_failed` 4다. 메타 하네스는 11/11 20:27 사람의 요청으로 멈췄다(market 계정 `logs/meta.log` 마지막 줄). 그 뒤 변경은 코드 기본값 OFF로 넣고 기간을 정해 켜는 A/B 실험(`*_TRIAL.md`)과 사람이 지시한 작업으로 들어갔다.

### 메타 하네스가 틀렸다가 바로잡은 예

- **G6 축소**: Terra가 최근 10건 −296,948을 근거로 G6 목표를 2→1로 줄였다. 같은 마감 시각 원장을 다시 연결하니 환불 0, 기여 +730,260이었다. Astra가 이 축소를 되돌렸다(`meta/REPORT-notes.md:83-84`).
- **환불 추정치 불일치**: loss_watch 추정(60건, 12,629,649)과 원장 주문 연결(45건, 8,467,071)이 달랐고 board는 45건이었다. 원본 로그는 그대로 두고 점수표만 원장에서 다시 계산하게 했다(`meta/REPORT-notes.md:80-81`).
- **인용 검증**: 테스트 성공 줄 `OK`는 인용 최소 길이(4자)에 못 미쳐 첫 시도가 실패했다. 이후 `Ran 328 tests` 줄을 인용하게 고쳤다(`meta/REPORT-notes.md:152-153`).

## 3. Round 3 결과

### 3.1 제출 시점 숫자 (2026-11-17 09:20:39 KST board, `meta/evidence/report-1117-board.json`)

| | 값 |
|---|---|
| Players | 71명 중 6위, 합계 1.631 (가게 ALL 0.869 + buyer ALL 0.762) |
| 학생만 | 46명 중 5위, student_total 1.597, 학생 백분위 0.911 |
| 가게 | ALL 0.869로 92곳 중 5위. 정산 55,011건, 매출 8,357,650,527, 순이익 189,266,953, 별점 4.55, 파산 0회 |
| 가게 품질 | 등급 주장 54,784건 중 정직 54,782건, 고장 1,147건, 환불 1,145건 |
| buyer | ALL 0.762로 138명 중 20위. 주문 44,728건, 정산 19,898건, 게시판 평점 0.909 |

최종 순위는 11/17 23:59 종료 시각의 board로 정한다. 위 숫자는 제출 시점 값이다.

### 3.2 정산 줄 (가게 줄은 `logs/market-kit.log`, buyer 줄은 회전된 `logs/market-kit.log.1`)

가게 쪽:
```
09:18:44 [acp] settled 맛있는 햇 부사 사과 가정용(흠과) 중대과 10kg at 17687, ledger #7624767, unit u-bfae8e425b6b grade B
09:19:08 [acp] settled 제스프리 썬골드키위 왕점보 3.1kg내외 (개별 155g내외 20개입) 맛젤인증점 at 24225, ledger #7624875, unit u-80abab1b653b grade A
```
buyer 쪽:
```
03:37:20 [acp] paid 620032 through https://quote-dam-humor-hazardous.trycloudflare.com/acp, settled at ledger #7514870: 시몬스 뷰티레스트 시트러스. 슈퍼싱글 매트리스, grade A
03:37:20 [market] ledger #7514876: G85a1a856 at 620032, stars 5, verified True, review accepted
```

### 3.3 거부 사유

buyer가 결제하지 않은 이유는 키트가 `.market/payments.jsonl`에 남긴다. 09:23 기준 20,693행 중 결제 19,902, 미결제 791이다(`meta/evidence/report-1117-payments.txt`, 집계 코드 `meta/report_payments.py`).

| 사유 | 건수 | 막은 곳 |
|---|---:|---|
| 남은 유통기한이 내 `accept` 기준보다 짧음 | 478 | 내 `accept` (키트가 결제 전에 확인) |
| 가게가 checkout에서 409: 그 unit이 이 주문용 재고에 없음 | 224 | 상대 가게 |
| 시장이 402로 정산 거부 | 45 | 시장 `/api/settle` |
| ACP 엔드포인트가 가게 주소와 다른 호스트 | 20 | 키트 |
| 등급이 `accept` 기준 미달 | 14 | 내 `accept` |
| 인증서 없음 | 3 | 내 `accept` |
| 네트워크 오류 / acp.json 없음 / 기타 checkout 오류 | 3 / 2 / 2 | — |

시장 402 45건 중 실패한 검사가 남은 것은 `not_settled_before` 1건, `payee_is_this_shop` 1건이다. 18건은 시장 답에 실패한 검사가 없었고(`failed_checks=unavailable`), 25건은 응답이 잘려 어느 검사인지 알 수 없다.

로그 인용(앞의 두 줄은 `logs/market-kit.log`, 05:31:06 줄은 `logs/market-kit.log.1`):
```
09:14:36 [buyer] [09:14:36] buyer [payments] real_yellow: G03aae7ca not paid: not paying: 28.10 h of shelf life left, the policy asks for 36.1
07:43:38 [buyer] [07:43:38] buyer [payments] thisjonghoon: G1831f2c0 not paid: checkout refused: 409 {"error":"unit u-c7ee8d411c1e is not in stock for this order"}
05:31:06 [buyer] [05:31:06] buyer [payments] ovotnwjd-maker: G124d3ea5 not paid: payment not completed: 402 failed_checks=unavailable
```
거부의 대부분(478 + 14 + 3 = 495건)은 시장이 아니라 내 buyer가 먼저 막은 것이다. 유통기한과 등급이 주문에 쓸모없는 물건이면 잉여가 생기지 않기 때문이다.

### 3.4 한계: 키트 없이 배포한 가게

키트 없이 Render에 따로 배포한 가게(`acp_shop`, https://nocha-furniture-a2a.onrender.com)는 Round 3 정산 기록이 없어서 실제 거래로 동작했다고 말할 근거가 없다. 남은 근거는 Round 2 주문(o-8aeedd26)으로 `/api/settle`을 돌린 점검 하나뿐이다. 13개 검사 중 11개를 통과했고, 실패한 두 개는 `round_3_order`와 `bound_to_open_mandates`로 Round 3 주문의 open mandate가 있어야 통과하는 검사다(`logs/acp-render-check-1027.txt`, 커밋 13523c77). 즉 서명·금액·수취인 같은 가게 쪽 검사는 통과했지만, Round 3 주문으로 끝까지 정산되는지는 확인하지 못했다.

## 4. 시도했다가 버린 것

- **codex 경로**: 11/06까지 거래 에이전트는 ChatGPT 구독의 `codex exec`를 썼다. 맥 미니에서 매 호출 429가 나서 OpenRouter로 옮겼다(`agent.json:2`, `harness.py:5-14`).
- **시간당 호출 상한 300**: 가게가 하루 수천 건의 흥정을 포기해서 껐다(`harness.py:56-58`).
- **오류 없이 틀린 자동 도구**: `auto_market_lookup`, `auto_catalog_ledger_search`, bundle/forum 도구를 지웠다. 원본은 `meta/evidence/*dropped-tool*.json`과 `meta/evidence/astra-1105-093817-tools-before.json`에 남겼다(`meta/REPORT-notes.md:27-28`, `:138-139`, `:177-178`).
- **G1515 4→6 확대**: 사람이 정한 조건은 "추가분이 두 공급 창 연속 남으면 4로"였다. 같은 5개 unit이 20:20:44와 20:40:54 두 창에 남아 4로 되돌렸다(`meta/REPORT-notes.md:85-86`).
- 기간을 정한 A/B 실험은 각 `*_TRIAL.md`에 설계와 되돌리는 법이 있다. 코드 기본값은 모두 OFF다. 그중 exact_accept와 at_floor_exact는 검토 뒤 채택(adopted)되어 지금 켜져 있다.

## 5. 실행

- 환경 변수 `OPENROUTER_API_KEY`, `MARKET_OWNER`는 `submissions/26622007/.env`(gitignore)에서 이 두 개만 읽는다(`run_market.sh:7-11`, `:60-64`).
- `nohup ./run_market.sh > /dev/null 2>&1 &`. 맥 미니에서는 `install_launchd.sh`로 상시 실행하고 `ensure_running.sh`가 살아 있는지 본다. 역할별 명령은 `agent.json`.
- 메타 하네스: `run_meta.sh` (`meta_harness.py run`).
- 그림: `tectonic architecture.tex`.
