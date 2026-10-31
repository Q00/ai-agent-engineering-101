# 8주차 중간평가 — 두 속도의 시장 에이전트 (Round 3, kit v4)

학번 25620027 · 작성 기준 2026-10-31 · [제출 PR #372](https://github.com/Q00/ai-agent-engineering-101/pull/372) · 브랜치 `week-08-r3-prep`

Round 3(11월 3일~11월 17일 23:59)에 올릴 가게와 buyer를 kit v4 위에서 다시 설계했다. **거래 경로(초 단위)에는 모델이 없다.** 판매자·구매자·재입고·후기는 모두 범위가 정해진 **정책표**를 읽어 밀리초 안에 결정한다. **모델(claude-haiku-5-5)은 5분마다** 공개 원장과 자기 장부의 요약을 읽고 그 정책표를 고친다. 이 보고서는 2026-10-31 로컬 검증 시점의 구현을 설명한다. 실제 R3 정산 수·Players 순위·거부 사유는 개시 뒤 §5에 로그를 인용해 채운다. 이전 제출본(v3 기준, 10/30)은 [REPORT_HISTORY.md](REPORT_HISTORY.md)에 그대로 보존했다.

## 0. 왜 구조를 바꿨나

| 관측·근거 | 설계 결정 |
|---|---|
| 2026-10-31 공개 원장 최근 500행: 시장은 buyer마다 **약 20초에 주문 1건**(최소 간격 20.0초). 상위 buyer는 주문→결과 1.5~16초 | 주문당 처리 시간을 15초 이내로. 모델 호출을 거래 경로에서 제거 |
| 기존 우리 buyer: 선택 40초 + 협상 + 후기 15초, 상한 240초. 라운드2 거래 수 24백분위, 절약 26백분위 | 코드가 가게·SKU를 고르고 2~4턴에 끝낸다 |
| 순위: 가게 ALL = 0.5P(순이익)+0.3P(매출)+0.2P(별점), buyer ALL = 0.5P(잉여)+0.3P(게시판)+0.2P(정산 수). buyer 지표의 0.7이 거래 **양**에 묶임 | 속도, 재고 폭, 게시판을 1차 목표로 |
| 교재 ECONOMY: 잘 팔리는 상품은 비공개이며 정산 기록·내 기록·게시판에서 알아낸다 | 공개 /api/ledger를 30초 증분으로 미러링해 수요·고장률·경쟁가를 메모리로 |
| 8주차 수업: 기억은 증거가 아니라 prior, 외부 루프가 버전별 프로필을 만든다, 하네스가 다시 두꺼워진다 | 모델은 매 턴이 아니라 정책 버전을 만든다 |

## 1. 부품별 설계

### 1-1 모델

| 어디에 | 무엇을 | 경계 |
|---|---|---|
| [r3/manager.py](r3/manager.py) 정책 관리자 (별도 프로세스) | 5분마다 요약(공개 원장 6시간·자기 결과 24시간·현재 정책과 필드 범위)을 받아 `set_policy` 도구를 **한 번** 호출한다. 최대 6개 변경과 이유 | 모든 필드는 pydantic `ge/le` 범위. 범위 밖·미지 필드·도구 미호출·API 오류는 **정책 유지**. 유료 호출은 기존 공유 quota 장부(KST 하루 5,000회)에서 차감. 키 없으면 수집·게시만 수행 |
| 레거시 경로 (`--mode model`, `AX_SELLER_MODE=legacy`) | 10/30까지의 function-tool loop 판매자·구매자 | 비교·회귀용으로 유지. 운영 기본값 아님 |

모델이 **하지 않는 일**: 가게 선택, 가격 제안, 수락, 후기 문장, 재입고 라인. 그 결정들은 정책표의 숫자와 코드 규칙이 한다. 이유는 §0의 20초 주문 간격과 7주차 EP5(모델 429로 1점 6,837건)다.

### 1-2 컨텍스트와 메모리: 신뢰도가 다른 세 층

| 층 | 저장 | 신뢰 | 다음 결정에 들어가는 방식 |
|---|---|---|---|
| **공개 원장 미러** [r3/ledger_db.py](r3/ledger_db.py) | `GET /api/ledger?after=seq` 30초 증분, SQLite. settle 행의 카테고리·SKU·금액·등급·claim, broken 행 | 시장이 검증한 사실 | restock의 수요 신호(카테고리·SKU 체결 수), 관리자 요약(가격/정가 비율·등급별 고장률·우리 정산 수), 게시글의 시장 한 줄 |
| **자기 사건 장부** [r3/events.py](r3/events.py) | buyer 시도·결과, `payments.jsonl` 동기화(paid/refused/사유), 판매자 서명 영수증 | 우리가 관측한 것 | 거절 사유별 가게 가중치: 증명서 없음 ×0.25, 잔여수명·등급 ×0.5, 보증 ×0.6, 그 외 ×0.7 (24시간) |
| **정책표** [r3/policy.py](r3/policy.py) | 버전·사유·시각, 이전 2개 보관 | 파생(모델이 고침) | 판매자 마크업·수락선·양보폭·청산·보증, buyer 첫 제안·즉시 수락선·상한·accept 조건, restock 유보·시드 예산·가격대·보충 |

기존 가게별 기억(SQLite journal, 별도 recorder, 128곳 노트)과 운영자 피드백 프로필은 그대로 두었고 레거시 경로가 읽는다. 5,629개 상품을 프롬프트에 넣지 않는다. 관리자가 모델에 주는 것은 요약 한 장(상위 카테고리 12개, SKU 20개, 등급별 고장률, 자기 결과 수치, 정책 한 벌)이다.

증거가 다음 결정을 바꾸는 구체 분기 네 개:

1. 지급 거절 "no certificate" → 그 가게는 24시간 동안 점수 1/4 → 같은 SKU를 가진 다른 가게로 간다 ([r3/buyer.py](r3/buyer.py) `shop_factor`).
2. 우리 가게에서 unit이 사라짐(판매) → 60초 뒤 restock이 같은 SKU를 보충한다 (`stale_hours` 안에서만).
3. 식품 등급이 3분 안에 바뀔 경계에 있으면 claim을 생략한다. 시장은 checkout 또는 정산 시점의 등급과 claim을 비교하므로 낮게 말하는 것은 정직이 아니라 두 시점 모두 틀린 주장이다(10/31 Codex 검수 F1로 수정).
4. 식품 잔여수명 20% 미만 → 원가의 60%까지 청산 호가를 낮춘다(폐기 손실보다 회수).

### 1-3 툴

| 도구 | 누가 정하나 | 입력 | 출력 | 코드가 강제하는 것 |
|---|---|---|---|---|
| `pick_unit` ([r3/seller.py](r3/seller.py)) | 코드 | 요청 문장, `inventory.json` | SKU·이름·카테고리로 맞는 unit (식품은 먼저 만료, 비식품은 오래된 것) | 다른 협상에 묶인 unit·방금 판 unit·만료 임박 제외 |
| `quote`/`counter`/`decide` ([r3/pricing.py](r3/pricing.py)) | 정책표 | 원가·등급·나이·잔여수명 | 호가, 수락선, 바닥 | 호가 ≤ 정가, 청산 외엔 원가·(1+마진) 이상, 양보는 수락선까지만 |
| 서명 영수증 | 코드 | 합의가·unit·claim·보증 | `{shop,item,price,task,unit,claim,warranty_hours,sig_seller}` | 정확한 합의가만 서명; cert는 항상 포함; 등급이 곧 바뀔 식품은 claim 생략 |
| `choose` ([r3/buyer.py](r3/buyer.py)) | 코드 | `order.json` acceptable, `shops.json` offers, `catalog.json`, 자기 장부 | 가게×SKU | 점수 = 정가 × 증거 가중치 × 공개 별점 보정 |
| `negotiate` | 정책표 | 상대 호가 | 2~4턴, 즉시 수락 또는 역제안 | 상한 = min(예산, 0.95×정가); 역제안은 상한 안에서만 증가; 우리가 제안·수락한 금액이 없는 완료 영수증과 상한 초과 영수증은 키트에 넘기지 않음 |
| `accept_policy` | 정책표 | 주문 종류 | 식품 `cert`+`min_hours_left`(사용시간+1h), 비식품 `cert`+`grades`(A,B) | kit가 결제 전에 검사하고 미충족이면 지급 안 함 |
| `restock.plan` ([r3/restock.py](r3/restock.py)) | 정책표+원장 신호 | 재고·카탈로그·demand·수요 신호 | 최대 10줄 `{sku, qty}` | 유보 현금·시드 예산을 공식 최악 원가(정가의 75%)로 검사하고 수량을 그 안으로 자름. 모델이 이 비율을 낮출 수 없음. 720h 식품·중저가 비식품만 |
| `review.stars_for` ([r3/review.py](r3/review.py)) | 코드 | `deal.json` (시장 영수증) | 1~5점·사실 문장 | 허위 claim 1점, 사용시간 미달 ≤2점, 예산 문구 없음 |
| `forum` ([r3/forum.py](r3/forum.py)) | 코드 | 자기 정산 기록·시장 요약 | buyer 글 5분 1건(원장 refs), shop 글 30분 1건 | 검증 가능한 사실만, 비방·광고 삽입·투표 요청 없음 |
| `set_policy` (모델 도구) | 모델 | 요약 | 변경 집합+이유 | 범위·개수·필드 검증 뒤 버전 저장 |

## 2. 구조: 키트 기본형과의 차이 — [architecture.tex](architecture.tex) · [architecture.pdf](architecture.pdf)

| | 키트 기본형 (`starter_round3/seller.py`, 기본 buyer, 기본 restock) | 이 제출 |
|---|---|---|
| 모델 위치 | 매 협상 턴마다 전사(transcript)를 모델에 보냄 | 거래 경로에 없음. 5분 주기 정책 관리자 |
| 가격 | 원가 하한, 그 위는 모델 자유 | 등급별 마크업 → 수락선 → 양보 → 청산. 모두 표에서 조회 |
| 영수증 | unit만 | unit·정직 claim·A등급 보증·cert |
| 가게 선택 | 재고 있는 가게 중 무작위 | 정가 × 자기 증거 가중치. 거절 이력이 선택을 바꿈 |
| 결제 전 확인 | 없음 | 주문 종류별 accept 정책 |
| 재입고 | 20개 미만일 때 무작위 3카테고리 | 폭 우선(720h 식품·중저가 A 비식품) → 판매 보충 → 원장 수요 추종, 유보 현금 보장 |
| 기억 | 없음 | 공개 원장 미러·자기 장부·정책표 세 층 |
| 게시판 | 없음 | 원장 refs가 붙은 사실 글, 역할별 간격 |

그림은 두 속도의 루프(초/분), 세 기억층과 신뢰 차이, 키트가 맡는 경계(점선), 증거가 다음 결정으로 돌아가는 화살표를 한 장에 그렸다. 새 알고리즘을 발명했다고 주장하지 않는다. 기여는 **검증된 사실(공개 원장) → 관측(자기 장부) → 정책(모델이 고치는 표) → 결정(코드)**의 연결과, 그 연결이 20초 주문 간격 안에서 돌아가게 만든 점이다. 공식 starter의 `find_unit`·A2A 흐름을 참고해 직접 다시 썼고, 빠른 수락 원리의 출처는 REPORT_HISTORY 7절에 있다.

## 3. 검증 (2026-10-31, 로컬)

| 검증 | 결과 | 근거 |
|---|---|---|
| 전체 회귀 | 438개 → 신규 포함 전체 PASS, 실패·건너뜀 0 | `.venv/bin/python -m pytest tests -q` |
| **키트 v4 통합** | 우리 판매자 → **원본 kit `Proxy`**(영수증 terms 관측) → 우리 fast buyer → **원본 kit `acp_pay`**(`check_accept`가 시장 키로 cert 서명 검증) → **원본 `AcpShop.create`**(`pick_unit`이 재고에서 우리 unit 선택) → 로컬 정산. 세션에 unit·claim A·cert 전달 확인. accept 미충족(잔여수명)이면 지급 0 | [tests/test_r3_kit_integration.py](tests/test_r3_kit_integration.py) |
| 판매자 | SKU 요청→정책 호가, 수락선 이상 제안→서명 영수증(unit/claim/warranty), 낮은 제안→감소하는 역제안→거절, 없는 상품 거절, 같은 unit 이중 약속 금지, 라운드2 이하에서 7주차 품목 판매 | [tests/test_r3_seller.py](tests/test_r3_seller.py) |
| 바이어 | 정가 높은 acceptable SKU 가게 선택, 2~4턴 체결, 거절 이력 가게 회피·저가 즉시 수락, 재고 없으면 네트워크 없이 종료, 영수증 검증 | [tests/test_r3_fast_buyer.py](tests/test_r3_fast_buyer.py) |
| 가격·정책 | 등급별 호가·수락선, 청산, 경계 claim, 버전·이전 2개 보관, 범위 밖 거부 | [tests/test_r3_policy_pricing.py](tests/test_r3_policy_pricing.py) |
| 훅 | 48h 식품·고가 비식품 제외, 현금 유보, 판매 보충, 라운드2 무매입, 후기 별점·예산 미노출 | [tests/test_r3_hooks.py](tests/test_r3_hooks.py) |
| 원장·게시판 | 수요·고장률·우리 정산 집계, refs 있는 글, 역할별 간격 | [tests/test_r3_ledger_forum.py](tests/test_r3_ledger_forum.py) |
| CLI·런처·관리자 | `--order` 경로 해석, restock/review JSON, agent.json 훅 3종, 모델 없는 관리자 tick | [tests/test_r3_cli_manager.py](tests/test_r3_cli_manager.py), [tests/test_r3_v4_compat.py](tests/test_r3_v4_compat.py) |
| 라이브 읽기 전용 | `r3.manager --once`가 공개 원장 1,509행을 5초에 수집(라운드2 행이라 settle 0) | 등록·주문·게시·모델 호출 0 |

하지 않은 것: 실제 R3 거래, VPS 이미지 재빌드·배포, 관리자의 Haiku 실호출, Linux 재검증. 기존 412개 PASS와 이번 438개 PASS는 회귀 증거이며 R3 성과 증거가 아니다.

### 3-1 독립 검수(2026-10-31, Codex) 대응

다른 에이전트가 PR을 독립 검수해 경계 결함 다섯 개를 반례로 재현했다. 모두 타당해 같은 날 고쳤고, 반례를 [tests/test_r3_review_regressions.py](tests/test_r3_review_regressions.py)로 고정했다.

| 지적 | 원인 | 수정 |
|---|---|---|
| F1 식품 등급을 경계에서 한 등급 낮게 claim | "낮게 말하면 안전"이라는 잘못된 가정. 시장은 checkout **또는** 정산 시점 등급과 비교하므로 B 주장은 두 시점 모두 틀림 | 현재 등급을 claim하고, 3분 안에 등급이 바뀔 unit은 claim 생략 ([r3/pricing.py](r3/pricing.py) `claimed_grade`) |
| F2 첫 응답 완료 영수증을 제안 없이 수용, 자체 상한 미검사 | 완료 task의 영수증 가격을 그대로 합의가로 복사 | 우리가 제안·수락한 금액이 없으면 영수증을 넘기지 않음. 최종 영수증도 상한 min(예산, 0.95×정가) 검사 ([r3/buyer.py](r3/buyer.py) `valid_receipt`, `price_cap`) |
| F3 모델이 낮춘 원가 추정으로 유보 현금 침범 | 정책표의 `cost_estimate_ratio`가 집행 기준이었음 | 필드 삭제. 유보·시드 예산은 공식 최악 원가(정가의 75%)로 검사하고 수량을 그 안으로 자름 ([r3/restock.py](r3/restock.py)) |
| F4 비정상 도구 인자 한 번에 관리자 종료 | `json.loads` 결과를 dict로 가정, loop가 AttributeError를 안 잡음 | 인자를 pydantic `Proposal`로 검증, 관리자 tick 전체를 예외 격리. host `health-r3.sh`가 컨테이너 안 관리자 프로세스 부재도 감지해 재기동 |
| F5 6시간 요약에 성숙 고장 표본이 없음 | 거래 추세 창(6h)과 고장 성숙 조건(≥6h 경과)이 같은 창을 요구 | 고장률 cohort를 추세 창보다 6h+24h 앞선 구간에서 뽑음 ([r3/ledger_db.py](r3/ledger_db.py)) |

수정 뒤 회귀 447개 PASS. 검수 당시 release `a6a64bb10282`는 F1~F3을 포함한 채 라운드2에서 돌고 있었고, 고친 release로 교체했다(§4-1).

## 4. 운영 전환 (사용자 승인 뒤 실행)

1. 11/2: `deploy.vps.bundle`로 이미지 재빌드. `runtime.json`에 `stop_at` 11/17 23:59 KST, `remaining_orders` 0(무제한). 환경에 `AX_R3_MANAGER_MODEL=1`, Anthropic env, `AX_PAID_QUOTA_PATH`. fast 경로는 OpenRouter 키가 필요 없다. 라운드2 시장에 kit v4로 join해 A2A·영수증·결과 전송을 확인한다(라운드2에는 결제가 없다).
2. 11/3: 개장 공지·agent.md 해시 재확인 뒤 `AX_R3_ACTIVATE=1`. 첫 시간은 재고 폭 매입과 첫 정산 ID를 확인한다.
3. 운영 중: 정책 버전 로그, 역할별 고유 settle ID, 거부 사유, 고장·환불을 §5에 기록한다. 초기 매입 한도(기본 250만)·유보(150만)·후기 정책은 정책표의 사용자 결정 항목이다.

### 4-1 라운드2 실측 (2026-10-31, VPS, kit v4)

release `a6a64bb10282`를 VPS에 설치(컨테이너 안 회귀 422개 PASS, 프리플라이트 통과)하고 `https://ax-week07.treasuresky.xyz`(7주차 named tunnel 재사용) 뒤에서 라운드2 시장에 join했다. 라운드2는 결제가 없어 R3 점수와 무관하지만, 실제 kit v4·실제 상대 에이전트와 종단 경로를 확인하는 자리다.

| 구간 | 주문 | 체결(verified True) | 우리 가게 판매(서명 영수증) | 시간 초과 |
|---|---|---|---|---|
| 첫 배포 `7befff8`, 약 22분 | 29 | 16 (55%) | 23 | 16 |
| 재배포 `a6a64bb`, 첫 11건 | 11 | 9 (82%) | 12 | 1 |

- 첫 배포에서 시간 초과가 절반이었다. 상대 판매자 중 턴마다 모델을 부르는 가게는 한 답에 5~10초를 쓴다. 시장이 주문을 약 20초 간격으로 주므로 18초까지 기다려도 처리량 손실이 없다. 주문 예산 14→18초, 예산의 45%를 쓴 뒤에는 상한 안의 어떤 호가도 즉시 수락하는 규칙을 넣었다([r3/buyer.py](r3/buyer.py) `negotiate`).
- 서명 없는 영수증(하우스 가게)은 거절했고, 상한을 넘는 역제안은 그대로 돌아섰다. 모델 호출 0회, 주문당 처리 1.5~18초.
- 발견한 배포 결함 둘을 고쳤다. 키트의 `load_key`가 키 파일을 chmod하므로 읽기 전용 `/private` symlink가 실패한다 → 런처가 run 디렉터리에 복사. cloudflared 기본 사용자는 0600 설정을 못 읽는다 → 소유자 uid로 실행. 절차는 [deploy/vps/README.md](deploy/vps/README.md).
- 정책 관리자는 모델 OFF 상태로 공개 원장을 30초마다 50~60행씩 미러링했다. 게시글은 라운드2 정산 기록이 없어 0건이다.

## 5. Round 3 결과 (개시 뒤 채움)

| 항목 | 값 | 로그 인용 |
|---|---|---|
| 제출 시점 가게 정산 수 | 개시 전 | `logs/market-kit.log` `[acp] settled … ledger #` |
| 제출 시점 buyer 정산 수 | 개시 전 | `[acp] paid … settled at ledger #` |
| Players 순위·백분위 | 개시 전 | board 스냅숏 |
| 거부 사유 분포 | 개시 전 | `[acp] not paying: …`, `payments.jsonl` reason |
| 정책 버전 수·변경 이유 | 개시 전 | `r3-policy.json`, 관리자 로그 |

## 6. 범위와 정직성

- 설계·구현·검증은 Claude Code와 함께 했고, 왜 그렇게 바꿨는지는 §0의 관측값이 근거다. 참고한 코드는 공식 `starter_round3/seller.py`의 구조와 10/30 보고서에 밝힌 빠른 수락 원리이며, 다른 학생의 코드를 복제하지 않았다.
- 담합·상호 구매·근거 없는 비방·평점 유인은 하지 않는다. 비공개 예산은 후기·게시글·로그 문장에 넣지 않는다. claim은 증명서와 맞춘다.
- 모델이 바꿀 수 있는 것은 정책표의 범위 안 숫자다. 키·예산·정산·운영 시한·키트 코드는 바꾸지 못한다.
