# Week 07 — 신뢰를 우선하는 market buyer

현재 공통 하네스 모델은 `claude-opus-5-5`(Claude Opus 5.5)다.
판매자, 기존 buyer, market buyer 모두 이 설정을 사용한다.
이전에 보존한 Haiku 검증 로그는 당시 결과이며 Opus의 전체 검증 결과가 아니다.

2026-10-20 LAB 2 실행 요청에 따라 검증된 서명·설득 설정을 `agent.json`에
반영했다. 현재 seller 명령은 `--config shop-round2.json --sign-receipts`를
사용한다. owner 만족을 강조하고 실제 좋은 거래 뒤 긍정적 후기를 요청하며,
이 설정에는 미지급 쿠폰 약속이 없다.
최신 Opus 공식 결과는 **25/25 PASS**, 관련 단위 검사는 **20/20 PASS**다.
`logs/round2-check-20261020T120654Z-adbc603e/`의 공식 원본 보고서와 로그를
각각 `test-report.json`, `logs/market-test.log`로 복사했다. 소스·프롬프트·설정
해시가 검증 복사본과 같음을 확인했으며 라이브 거래 기억은 변경하지 않았다.
21:13 KST에 키트를 재시작했고 가게와 buyer 모두 온라인이다.
최근 설명 정리 후 콘솔 로그는 `logs/description-update-20261020.log`이며 PID는
`agent-market/live.pid`에 있다. 공개 거래의 서명은 로컬 키뿐 아니라
시장 등록 공개키로도 확인했다. `logs/lab2-market-check-20261020.json` 참조.
21:15 KST 시장은 아직 Round 1이므로 Round 2 후기 접수 체크포인트는 미확인이다.

`market_buyer.py`는 6주차 `buyer_agent.py`를 복사해 시작했다. 기존 흥정과
예산 검사는 유지하고, 가게 선택·거래 기억·마켓 결과 출력을 추가했다.
판매자는 `seller_agent.py`, Round 0 구매자는 `buyer_agent.py`이며,
Round 1부터 키트는 `agent.json`의 `market_buyer` 명령을 사용한다.

## 가게 선택과 기억

기존 실행 설정의 판매자는 별도의 수업용 기만 전략을 사용했다. 첫 가격 제시와 거래 완료 메시지에
“좋은 리뷰를 확인하면 재사용 방지 기능이 있는 다음 구매 쿠폰을 발급한다”고
안내하지만 실제 리뷰 확인·쿠폰 발급·사용 처리는 없다. 사용자가 의도적으로
미지급 전략을 선택했다. 현재 거래의 가격과 영수증에는 쿠폰 할인을 적용하지 않는다.
이 문구가 리뷰를 개선하는지는 아직 시장에서 검증하지 않았다.

새 Round 2 준비본에서는 사용자가 쿠폰 약속 제거를 선택했다.
`shop-round2.json`에 가게 소개와 owner 만족·품질·쓸모를 강조하는 설득 문구를
모았다. 첫 응답과 역제안에서 buying agent에게 주인의 만족도와 가격을
함께 고려하라고 제안하고, 거래 완료 후 실제 경험이 좋았다면 긍정적 후기를
부탁한다. 품질 검사·인증·기존 좋은 후기·주인의 만족을 보장하는 사실은 만들지 않는다.
구매 근거로 실제 구현된 합의 가격 유지와 서명 영수증을 제시한다.
가격은 모든 문구의 마지막에 두어 숫자로 가격을 읽는 buyer와 호환된다.

`seller_opening_value.txt`와 `seller_value.txt`는 공격적인 첫 가격보다
성사 가능한 공정한 거래를 우선하고, 가능한 제안에는 적극적으로 합의하도록
지시한다. 가격 결정은 계속 Opus가 하며 코드의 최저가 검사도 유지한다.
이 새 전략은 `--config shop-round2.json`으로 켜진다. 처음에는
`agent-round2.json`에만 준비했고, 이후 LAB 2 요청으로 `agent.json`에도 적용했다.

키트의 후보 목록은 이름·URL·후기만 제공하므로 Agent Card 설명이 모든
buyer의 최초 가게 선택에 쓰이는 것은 아니다. 설명은 카드를 읽는 buyer에게
도움이 되고, 시장 점유율 효과는 적용 후 실제 방문·거래·후기로 평가해야 한다.

사용자가 선택한 전략은 **신뢰 우선**이다. `prompts/market_select.txt`가
Opus 5.5에 내 영수증 불일치 경험과 최근 나쁜 후기를 피하고 성공 경험을
우선하도록 지시한다. 코드가 별점 순으로 가게를 자동 선택하는 것은 아니다.
후기 없는 가게는 미확인으로, 협상 결렬이나 통신 오류는 사기의 증거가 아닌
별도 결과로 전달한다. 선택한 이름과 이유는 `SHOP SELECTION` 줄에 출력한다.

하네스에는 가게별 최근 후기 10개까지의 별점 집계, 최신 원문 3개(각 400자),
이름별 전체 거래 결과 집계와 최신 거래 3개를 전달한다. 오래된 영수증
불일치도 전체 집계에 남는다. 가게가 12개보다 많으면 묶음별 후보를 고른 뒤
같은 증거로 후보끼리 다시 비교한다. 요약 때문에 일부 원문은 생략되며,
이 방식이 모든 가게를 한 번에 비교한 최적 선택을 보장하지는 않는다.

기억 파일은 `data/market-memory.jsonl`이다. 한 줄이 거래 한 건이며,
`name`, `item`, `price`, `result`, `budget`, `authorized_price`,
`observed_receipt`, 선택 이유와 후기를 저장한다. URL은 진단용으로만 남기고
기억 조회에는 `name`을 사용한다. 파일은 거래 후 추가하며 기존 줄을 고치지 않는다.
새 buyer를 쓰기 전 Round 0 로그를 거래 기억으로 자동 변환하지는 않는다.

성공한 거래는 받은 영수증 data Part를 서명과 추가 필드까지 그대로 반환한다.
합의한 가격·예산·품목 및 영수증에 있는 task를 검사한다. 서명의 암호학적
검증은 키트가 담당한다. 불일치 시 원문을 기억에 보존하고 결과는 `receipt: null`로
내보낸다. 별점은 합의와 일치한 구매 5, 결렬 3, 실행 오류 2, 영수증 불일치 1이며,
후기는 관측한 결과만 기술한다.

## 실행과 검증

레포 최상위 폴더에서, 키트가 생성한 가게 목록으로 직접 실행할 때:

```bash
submissions/26510124/week-07/.venv/bin/python \
  submissions/26510124/week-07/market_buyer.py \
  --shops submissions/26510124/week-07/.market/shops.json --item lamp --budget 120
```

키트의 `agent.json`은 시작할 때 한 번 읽는다. 설정 변경 후 키트를 재시작해야
새 `market_buyer`가 연결된다. Round 0에서는 기존 buyer를 계속 쓴다.

2026-10-20 20:25 KST에 새 설정을 읽은 키트가 구매자 전용으로 재접속했다.
재시작 중 Cloudflare가 429를 반환했고 localhost.run SSH 연결도 시간 초과됐다.
당시 판매자 공개 연결은 복구되지 않았으며, 기존 키는 유지했다.
관련 기록은 `logs/market-buyer-restart.log`와 `logs/market-ssh-tunnel.log`에 있다.

2026-10-20 20:39 KST에는 Serveo SSH 443 터널로 가게를 다시 등록했다.
당시 공개 주소는 https://b38e4d02fbc10cd9-203-246-85-178.serveousercontent.com 이었다.
터널은 로컬 8900 포트로 전달하고, 판매자 키트는 `--public-url <주소>
--port 8900 --shop-only`로 실행한다. buyer 키트는 기존 거래의 결과 제출을
확인한 뒤 `--deployed <주소>`로 재시작해 자기 가게를 후보에서 제외한다.
두 키트와 SSH는 터미널과 분리된 백그라운드 프로세스다.
`agent-market/kit.pid`, `shop-kit.pid`, `tunnel.pid`에 각각 PID를 보관한다.
이 초기 실행에는 터널 갱신 기능이 없었다.
실행 기록은 `logs/round1-shop-start.log`, `logs/round1-serveo-live-20261020T203624.log`,
`logs/market-kit.log`와 `logs/round1-online-check-20261020.json`에 있다.

### 공개 협상 오류 수정 및 현재 실행

카드만 조회하면 정상이어도, Serveo는 응답 시작까지 약 5초 이상 걸린
협상 POST에 빈 HTTP 502를 반환했다. `public_market.py`는 공식 키트의
프록시를 확장해 JSON 앞의 공백을 1초마다 전송한다. 최종 JSON-RPC와
영수증 원문은 그대로 전달하며, 상류 오류는 JSON-RPC 오류로 보낸다.
로컬 seller나 공용 `scripts/`를 수정하지 않는다. 공개 주소에서 7~9초가
걸리는 요청과 같은 taskId의 거래 완료 영수증을 확인했다.

2026-10-20 20:52 KST부터는 `run_public_market.py`가 가게와 buyer를 함께
관리한다. 익명 터널이 약 10분 후 만료되는 것이 관측돼, 생성 후 180초가
지나면 현재 주문 처리 후 새 터널을 열고 시장에 새 주소를 등록한다.
판매자는 계속 실행해 협상 상태를 유지하고 직전 터널도 잠시 유지한다.
buyer는 새 주소를 자기 가게로 제외한다. 키·거래 기억은 그대로 사용한다.

week-07 폴더에서 `MARKET_OWNER` 환경변수를 설정한 상태로 실행한다
(claim code는 파일이나 커밋에 넣지 않는다).

```bash
.venv/bin/python run_public_market.py --name dyishappy
.venv/bin/python check_public_shop.py
.venv/bin/python -m unittest test_public_market -v
```

서명 가게의 공개 영수증까지 검증하려면
`.venv/bin/python check_public_shop.py --verify-signature`를 사용한다.

현재 실행 PID는 `agent-market/live.pid`, 최신 가게 주소는
`agent-market/shop-url`에 있다. 실행 로그는 `logs/managed-market-20261020.log`,
터널 원본 로그는 `logs/managed-tunnel-*.log`다. 진단기는 시장 후기나 결과를
제출하지 않고 가게의 제시 가격을 수락해 영수증을 확인한다.
컴퓨터 잠자기·재부팅 및 무료 터널의 모든 장애까지 방지하는 것은 아니다.
공개 재검증에서 일시적인 TLS 전송 오류도 관측되어 실패 로그를 보존했다.

### Round 2 준비 과정과 전환 방법

처음에는 사용자 요청에 따라 `agent.json`을 유지하고 별도 `agent-round2.json`에만
서명 설정을 추가했다. 이후 LAB 2 실행 요청으로 두 설정을 일치시켰다.
이 모드는 기존 `shop_key.pem`으로 `{shop, item, price, task}`의 정규 JSON에
Ed25519 서명을 붙인다. 가격은 정수로 서명하고, `task`는 실제 A2A taskId다.
키가 없거나 Ed25519 키가 아니면 시작을 중단한다. 새 키를 자동 생성하지 않는다.
buyer가 받은 영수증을 그대로 내보내면 키트가 검증한 뒤 buyer 서명을 추가한다.

실제 Opus로 전체 공식 검사를 독립 복사본에서 실행할 때:

```bash
.venv/bin/python verify_round2.py
```

결과와 당시 소스는 `logs/round2-check-*/`에 보존한다. 라이브 거래 기억,
기존 로그 및 현재 `agent.json`을 덮어쓰지 않는다.
설득 전략 추가 전 서명 준비본은 Opus 5.5 공식 검사 **25/25**, 관련 단위 검사
**11/11**을 통과했다. 당시 결과는
`logs/round2-check-20261020T115634Z-b6c9773a/test-report.json`이다.

claim code가 `MARKET_OWNER` 환경변수에 설정된 터미널에서 다음 명령으로
서명 가게와 buyer를 시작할 수 있다:

```bash
.venv/bin/python run_public_market.py --name dyishappy
```

같은 포트의 실행기를 둘 띄우면 안 된다. 기존 프로세스는 `agent-market/live.pid`로
확인한다. 자동으로 라운드를 감지해 서명 모드로 바꾸는 기능은 없다.
`--round2` 옵션으로 보관된 `agent-round2.json`을 명시할 수도 있다.
최종 제출 시에는 공식 보고서와 `check_week07.py`를 최종 설정으로 확인해야 한다.

가게 선택과 흥정을 합해 최대 270초, 선택은 그중 최대 90초를 사용한다.
예산보다 큰 제안은 하네스 검증과 송신 전 검증에서 거부하며 보정해서 보내지 않는다.

```bash
cd submissions/26510124/week-07
.venv/bin/python -m unittest -v test_market_buyer test_buyer
.venv/bin/python run_market_buyer_checks.py
```

두 번째 명령은 실제 Claude 호출로 공식 검사기의 **buyer 항목만** 실행한다.
새 `logs/market-buyer-check-*` 복사본에 로그와 기억, `buyer-report.json`을
보존한다. 이는 전체 과제의 `test-report.json`을 대체하지 않는다.
판매자 영수증 서명과 전체 market test는 별도로 남아 있다.

## 아래는 복사해 보존한 Week 06 실행 안내

판매자는 `seller_agent.py`, 구매자는 `buyer_agent.py`다. 둘 다 Claude Code의
`haiku`를 호출한다. 실제 가격과 수락·거절·포기는 모델이 정하고, Python은
A2A 메시지와 task 상태, 응답 형식 및 가격 제한을 관리한다.

추가 필수 Challenge 1·2는 `catalog_seller.py`와 `shopping_buyer.py`로 실행한다.
기본 검사기의 세 품목과 고정 하한가는 기존 명령으로 재현하며, 확장 카탈로그의
4,000개 SKU는 각각의 설명·속성·하한가를 가진 별도 합성 상품이다.

## 환경 준비

이 디렉터리(`submissions/26510124/week-06`)에서 실행한다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
claude --version
claude auth status
```

Claude Code가 설치되어 있고 로그인된 상태여야 한다. 로그인하지 않았다면
`claude`를 직접 실행해 계정 로그인을 완료한다. 코드에 키를 넣지 않는다.
검증에 사용한 Python은 3.12, Claude Code는 2.1.287이다.
`agent.json`의 명령은 이 폴더에서 `.venv/bin/python`을 사용하므로 가상환경을
활성화하지 않아도 되지만, 위 준비 단계로 `.venv`를 만들어야 한다.

## 직접 거래해 보기

터미널 1:

```bash
.venv/bin/python seller_agent.py --port 9999
```

서버는 경고 이상의 로그만 표시하므로 시작 문구 없이 대기할 수 있다.
터미널 2도 이 디렉터리에서 실행한다.

```bash
.venv/bin/python buyer_agent.py --url http://127.0.0.1:9999 --item lamp --budget 120
```

구매자의 요청·응답·모델 결정이 터미널 2에 표시된다. 모델의 원본 입력과 출력은
매 호출마다 다른 이름으로 `logs/`에 저장된다. 두 프로그램 모두 실제 Claude
호출을 하므로 로그인 계정의 사용량을 소비한다. 거래 결과와 가격은 실행마다
달라질 수 있다. `Ctrl+C`로 판매자 서버를 종료한다.

## 검증

이 디렉터리에서 오프라인 검사를 실행한다.

```bash
.venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

공식 거래 검사는 다음 보조 명령으로 실행할 수 있다. 보조 명령은 기존 공식
결과와 고정 이름의 로그를 `logs/previous-runs/`에 그대로 복사한 다음,
수정하지 않은 `scripts/trade_test_week06.py`를 호출한다. 공식 스크립트가 직접
`test-report.json`과 표준 로그를 생성한다.

```bash
.venv/bin/python run_trade_check.py
```

레포 최상위에서 공식 스크립트를 직접 실행하는 명령은 다음과 같다.
직접 재실행할 때도 이전 결과를 먼저 커밋하고 보존한다.

```bash
submissions/26510124/week-06/.venv/bin/python scripts/trade_test_week06.py submissions/26510124/week-06
submissions/26510124/week-06/.venv/bin/python scripts/check_week06.py submissions/26510124/week-06
```

## 코드를 읽는 순서

1. `prompts/seller_opening.txt`: 높은 가격에서 시작해 흥정 여유를 둔다는 사용자 선택.
2. `prompts/seller.txt`, `prompts/buyer.txt`: 각 역할의 목표와 JSON 응답 약속.
3. `harness.py`: 빈 디렉터리에서 하네스를 호출하고 원본 로그·응답 검증을 처리.
4. `seller_agent.py`: 판단을 input-required, completed, rejected 상태로 변환.
5. `buyer_agent.py`: 카드 확인, 같은 task에서 협상, 영수증·예산 확인.
6. `REPORT.md`: 실제 실패, 수정, 검증 결과와 이 구현의 한계.

## 필수 Challenge 1·2 재현

위 환경 준비 후 이 디렉터리에서 실행한다. 실제 Claude 구독 사용량을 소비하며,
각 실행은 고유한 `logs/challenge-N/<실행 ID>/`에 입력·카드·통신·모델 원본·결과를
저장한다. 이전 로그는 덮어쓰지 않는다. 서버 포트는 실행마다 자동 할당한다.

```bash
.venv/bin/python run_challenges.py --challenge 1
.venv/bin/python run_challenges.py --challenge 2 --budget 450
```

첫 명령은 4,000개 상품 판매자를 띄우고, 상품명 없는 설명 3개와 충족 불가능한
설명 1개를 검사한다. 조건에 맞는 상품·상품별 하한가·영수증의 일치를 검증한다.
여기서 구매 상대는 검사기이며, 판매자의 상품 선택과 가격은 실제 하네스가 정한다.

두 번째는 품목별 가게 5개씩 총 20개를 띄운다. 각 가게는 서로 다른 상품 200개와
가격 설정을 가진다. 구매자는 Agent Card의 skills를 읽고 `data/shopping_list.json`의
세 요구사항에 맞는 가게와 예산 배분을 정한 뒤 동시에 협상한다. 구매자와 판매자
모두 실제 하네스를 호출한다. 20개 카드 조회, 다중 가게 동시 진행, 품목 누락·중복,
총예산 및 각 가게의 하한가를 검증하고 실험용 서버를 종료한다.

`result.json`의 `passed`와 `shopping-result.json`, `buyer.log`, 각 가게의
`*-server.log`, `harness/`를 함께 확인한다. 로그의 URL·포트는 해당 실행에서만
유효하므로, 재실행은 위 명령으로 새 서버와 디렉터리를 만든다.

개별 확장 프로그램을 사용할 수도 있다.

```bash
.venv/bin/python catalog_seller.py --port 9100
.venv/bin/python shopping_buyer.py --directory directory.json --list data/shopping_list.json --budget 450 --output new-result.json
```

`directory.json`은 `{"shops":[{"id":"shop-a","url":"http://127.0.0.1:9100"}]}`
형식이다. 상품 요구사항은 `description`과 검증용 `constraints`를 함께 기록한다.
가게 설정의 `categories`, `inventory_mod`, `inventory_remainder`, `reserve_offset`으로
품목·재고·하한가를 바꿀 수 있다. 실행별 설정 예시는 챌린지 로그에 보존된다.

`generate_catalog.py`는 네 종류의 품목에 재질·높이·폭·색·형태를 조합한 합성
데이터를 재생성한다. 외부 상품을 수집하거나 모델로 데이터를 만든 것은 아니다.
카탈로그 생성은 하한가 정의이며, 제시 가격이나 수락 판단은 항상 하네스가 정한다.

확장 코드를 읽을 때는 `catalog.py`의 검색·선택, `catalog_seller.py`의 A2A 상태,
`shopping_buyer.py`의 예산 원장·병렬 협상·요약 순서로 보면 된다. 요약은 최대
800자, 최근 메시지는 2개이며 taskId·상품·현재 가격·예산은 별도 정확한 상태로 유지한다.
최초 계획 후 가게 변경이나 남은 예산 재분배는 구현하지 않았다. 거래 실패는
구매 완료로 처리하지 않고 미구매 품목과 함께 보고한다.
