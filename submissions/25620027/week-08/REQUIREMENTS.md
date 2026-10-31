# 8주차 중간평가 요구사항과 구현 증거

기준: [교수 week-08.html, commit d4b4d53](https://github.com/Q00/ai-agent-engineering-101/blob/d4b4d53e0fc2b30c4d44f7c89e24791dfbcba66b/week-08.html)과 [시장 agent.md](https://market.ourolabs.ai/agent.md), 2026-10-31 확인. 중간평가는 성적 30%, **Round 3 11월 3일(화)~11월 17일(화) 23:59, 제출 마감 11월 17일 23:59(PR 생성 시각)**. 내부 배점 부품 40%·구조 30%·Players 백분위 30%. 이전 공고(11/2~16)에 맞춘 기록은 [REPORT_HISTORY.md](REPORT_HISTORY.md)에 보존했다.

| 구분 | 요구사항 | 구현·검증 또는 남은 증거 |
|---|---|---|
| 부품 40% · 모델 | 어떤 모델을 어디에 썼는가 | claude-haiku-5-5를 5분 주기 정책 관리자에만. [r3/manager.py](r3/manager.py), 범위 검증 [r3/policy.py](r3/policy.py). 거래 경로 모델 0회. [REPORT 1-1](REPORT.md#1-1-모델) |
| 부품 40% · 컨텍스트 | 지난 거래·평판을 다음 결정에 넣는 방식(meta-harness, 메모리) | 공개 원장 미러·자기 장부·정책표 세 층과 네 가지 분기. [REPORT 1-2](REPORT.md#1-2-컨텍스트와-메모리-신뢰도가-다른-세-층), [tests/test_r3_fast_buyer.py](tests/test_r3_fast_buyer.py), [tests/test_r3_ledger_forum.py](tests/test_r3_ledger_forum.py) |
| 부품 40% · 툴 | 무엇을 툴로 만들었고 어떻게 | 판매·구매·재입고·후기·게시·원장·정책 도구 표. [REPORT 1-3](REPORT.md#1-3-툴) |
| 구조 30% | 키트 기본형과 다른 구조, LaTeX 1쪽 figure | [architecture.tex](architecture.tex) → [architecture.pdf](architecture.pdf), 비교표 [REPORT 2](REPORT.md#2-구조-키트-기본형과의-차이--architecturetex--architecturepdf) |
| 순위 30% | 종료 시각 board Players 백분위 | 개시 전. 종료 시 스냅숏을 REPORT §5에 |
| 기본 조건 · 가게 | R3 정산 20건 이상 | 개시 전. `[acp] settled` 줄과 역할별 고유 ledger 번호로 집계 |
| 기본 조건 · buyer | R3 정산 20건 이상, open mandate 상한 초과 checkout 미완료 | 개시 전. 상한은 kit `acp_pay`가 강제하며 우리 상한은 그 아래(min(예산, 0.95×정가)). [tests/test_r3_checkout.py](tests/test_r3_checkout.py) |
| 키트 v4 호환 | offers·acceptable·훅 3종 | [r3/models.py](r3/models.py), [market_a2a/market_decision.py](market_a2a/market_decision.py), [deploy/vps/launch.py](deploy/vps/launch.py), [agent.json](agent.json). [tests/test_r3_v4_compat.py](tests/test_r3_v4_compat.py) |
| 결제 전 확인 | `accept`(증명서·잔여시간·등급·보증) | 주문 종류별 정책. 원본 kit `check_accept`로 통합 검증 [tests/test_r3_kit_integration.py](tests/test_r3_kit_integration.py) |
| 재고·공급 | restock 훅, 최대 10줄, 파산 회피 | [r3/restock.py](r3/restock.py), 유보 현금·시드 예산. [tests/test_r3_hooks.py](tests/test_r3_hooks.py) |
| 식품·비식품 품질 | 등급 claim·보증·cert | 정직 claim(경계는 한 등급 아래), A등급 보증 6h, cert 항상. [tests/test_r3_seller.py](tests/test_r3_seller.py) |
| 게시판 | outbox.jsonl, 역할별 5분 1건 | buyer 사실 글(원장 refs), shop 재고 글. [r3/forum.py](r3/forum.py) |
| 제출물 | 배포 머신 `logs/market-kit.log`(정산 줄), 바꾼 코드, REPORT.md, architecture.tex/pdf | 코드·REPORT·tex/pdf 있음. 실제 R3 로그는 개시 뒤 추가 |
| 형식 | squash 금지, 로그 커밋, 키 미커밋 | 커밋 단위 보존. `*.pem`·env 미커밋 |
| 선택 | acp.json에서 동적 도구 생성 | 미구현. kit의 고정 checkout 사용 |

현재 증거: 전체 회귀 PASS([REPORT 3](REPORT.md#3-검증-2026-10-31-로컬)), 키트 v4 통합 검사, 공개 원장 읽기 전용 수집 1회. 실제 R3 정산·순위·거부 사유는 개시 뒤 수집한다.
