# 8주차 중간평가 요구사항과 구현 증거

기준: [교수 week-08.html](https://wpti.dev/ai-agent-engineering-101/week-08.html), 2026-11-03 원문 재확인(HTML SHA256 `b2d438c8cd83847b847a3e71baea942a599b0210a1fe8a111daec0e7f819c0a2`). 기존 규칙 원본은 [commit d4b4d53](https://github.com/Q00/ai-agent-engineering-101/blob/d4b4d53e0fc2b30c4d44f7c89e24791dfbcba66b/week-08.html)과 [시장 agent.md](https://market.ourolabs.ai/agent.md)다. **Round 3 11/3~11/17 23:59, PR 생성 마감11/17 23:59**. 과제는 전체 성적30%, 과제 안에서는 부품40%·구조30%·Players30%다. 이전 공고 이력은 [REPORT_HISTORY.md](REPORT_HISTORY.md)에 보존했다.

**전수점검 결론:** 평가 방향에 맞는 로컬 구현·검사는 있으나 제출·운영·R3 실적 전체 완료 판정은 아니다. 확인 당시 시장R2, 서버`11f627c9dbb8`, PR #372 head`0870e08`, 후속 수정은 로컬에만 있다.

| 구분 | 요구사항 | 구현·검증 또는 남은 증거 |
|---|---|---|
| 부품 40% · 모델 | 어떤 모델을 어디에 썼는가 | Haiku 통역·정찰5분·리뷰30분·전술15분, Sonnet 전략30분+경고·자문6시간. [REPORT 1-1](REPORT.md#1-1-모델). 실제 R3 역할별 작동은 개장 뒤 확인 |
| 부품 40% · 컨텍스트 | 지난 거래·평판이 다음 결정을 어떻게 바꾸는가 | 실제 지급 기반 가게 기억·실패 휴식, 자기/상대 리뷰·상위권 비교, 정책 버전 전후 R3 복기, 사람의 승인/거절 기록. [REPORT 1-2](REPORT.md#1-2-컨텍스트와-메모리-증거관측파생-자료의-분리), [tests/test_r3_reflection.py](tests/test_r3_reflection.py) |
| 부품 40% · 툴 | 무엇을 툴로 만들었고 어떻게 | 판매·구매·재입고·후기·게시·원장·정책 도구 표. [REPORT 1-3](REPORT.md#1-3-툴) |
| 구조 30% | 키트 기본형과 다른 구조, LaTeX 1쪽 figure | [architecture.tex](architecture.tex) → [architecture.pdf](architecture.pdf), 비교표 [REPORT 2](REPORT.md#2-구조-키트-기본형과의-차이--architecturetex--architecturepdf) |
| 순위 30% | 종료 시각 board Players 백분위 | 개시 전. 종료 시 스냅숏을 REPORT §5에 |
| 기본 조건 · 가게 | R3 정산 20건 이상 | 개시 전. `[acp] settled` 줄과 역할별 고유 ledger 번호로 집계 |
| 기본 조건 · buyer | R3 정산 20건 이상, open mandate 상한 초과 checkout 미완료 | 개시 전. 상한은 kit `acp_pay`가 강제하며 우리 상한은 그 아래(min(예산, 0.95×정가)). [tests/test_r3_checkout.py](tests/test_r3_checkout.py) |
| 키트 v4 호환 | offers·acceptable·훅 3종 | [r3/models.py](r3/models.py), [market_a2a/market_decision.py](market_a2a/market_decision.py), [deploy/vps/launch.py](deploy/vps/launch.py), [agent.json](agent.json). [tests/test_r3_v4_compat.py](tests/test_r3_v4_compat.py) |
| 결제 전 확인 | `accept`(증명서·잔여시간·등급·보증) | 주문 종류별 정책. 원본 kit `check_accept`로 통합 검증 [tests/test_r3_kit_integration.py](tests/test_r3_kit_integration.py) |
| 재고·공급 | restock 훅, 최대 10줄, 파산 회피 | [r3/restock.py](r3/restock.py), 유보 현금·시드 예산. [tests/test_r3_hooks.py](tests/test_r3_hooks.py) |
| 식품·비식품 품질 | 등급 claim·보증·cert | 현재 등급 claim, 3분 안에 등급이 바뀔 때는 claim 생략, A등급 보증 6h, cert 항상. [tests/test_r3_seller.py](tests/test_r3_seller.py) |
| 게시판 | outbox.jsonl, 역할별 5분 1건 | buyer 사실 글(원장 refs), shop 재고 글. [r3/forum.py](r3/forum.py) |
| 제출물 | 배포 머신 `logs/market-kit.log`(정산 줄), 바꾼 코드, REPORT.md, architecture.tex/pdf | 코드·REPORT·tex/pdf 있음. 실제 R3 로그는 개시 뒤 추가 |
| 형식 | squash 금지, 로그 커밋, 키 미커밋 | 커밋 단위 보존. `*.pem`·env 미커밋 |
| 선택 | acp.json에서 동적 도구 생성 | 미구현. kit의 고정 checkout 사용 |

## 선순환 경로 전수점검

| 경로 | 구현·검증 | 판정 |
|---|---|---|
| 자기 거래·실패 → 다음 구매 | `events`의 실제 지급 결과·가격 비율 → `buyer.choose`, 3회 실패 휴식·안전 탐색 | 로컬 검사 완료, 후속판 미배포 |
| 자기 리뷰 → 판매 정책 | `reviews.run_digest` → 전략 및 전술의 `review_digest`; 표본 초과·중복 주제 거부 | 로컬 검사 완료 |
| 상대 거래·리뷰 → 전술 | 원장 미러·benchmark·정찰 신뢰 → 전술/전략. 자작 리뷰·허위 claim·같은 소유자 거래는 모방 근거에서 제한 | 구현·기존 검사. 전략 효과는 미측정 |
| 정책 변경 → 복기 → 다음 판단 | `reflection.policy_reviews`의 버전별 전후15분 R3 정산 → 전술·전략·자문에 같은 기준의 복기 전달 | 연결 검사 완료. 대기/누락/소표본/혼재 구분, 인과 주장 안 함 |
| 중요한 변경 → 사용자 판단 | 자문 도구는 번호 있는 제안만 저장. Telegram 전달 확인 후에만 보고 완료, 실패 시 기존5분 watcher가 재전달. 승인/거절은 운영자가 기록하고 다음 자문이 읽음 | 로컬 검사 완료. 전달 ack는 승인·적용이 아님 |
| 실패 → 안전한 지속 | 잘못된 모델 응답은 정책 유지, 전술 TTL·이전 버전, 장부 불명 시 유료 모델 차단, 키트/관리자 실패는 기존 복구 경로 | 로컬 검사·기존 배포 복구 코드. 새 guard는 미배포 |

## 자동 처리와 사람 승인 경계

| 자동 처리 가능 | 사람 확인·승인이 필요한 것 |
|---|---|
| 사전 허용 범위 내 가격·양보·재입고 정책, 가게 선택·실패 휴식, 기록/요약, 입력 검증, TTL 복귀, 이미 허용된 장애 복구 | 코드/규칙·배포 변경, 하드 가드 범위 확대, 실제 API 비용/모델·키·운영 시한 변경. 성과만 보고 자동 코드 수정·재배포하지 않음 |

Telegram은 알림 채널이다. 사용자가 Codex/Claude 세션에 승인·거절을 전달하면 담당자가 제안 ID·범위를 대조해 기록하고 검사·적용한다. 서버가 Telegram 자유 문장을 바로 코드 명령으로 실행하지 않는다. 이 승인/적용 절차를 완전 자동 배포 시스템으로 주장하지 않는다.

## 교수 속기록과의 대조

원문 `261027_AX이해와활용_8주차_속기록_전체.txt`: 311~325행은 모델·컨텍스트·툴과 기억·평가 피드백, 376~380행은 위임 범위 안 동작, 792~795행은 여러 겹 가드, 932~942행은 오염된 거래 컨텍스트 바깥의 루프·사람 피드백·버전별 프로필, 956~959행은 메모리·툴·컨텍스트와 구조 평가다. 여기서는 그 방향을 버전 정책·별도 분석·정산 기반 복기·사람 승인으로 구현했다. 교수님이 자동 코드 작성/자동 배포를 필수로 요구했다고 해석하지 않는다. 순위 배점·기간·제출 형식은 이후 개정된 공식 웹 공고를 우선한다.

1037행의 별도 verifier·도구/구매 평가 사례,1073~1081행의 주기적 관찰·시간/행동별 기억 분할도 대조했다. 전후 복기는 거래 경로 밖에서 계산하고 최신6버전·같은15분 창·유한한 최근 기록만 입력한다. 원장·실행 증거를 보존하는 것과 전체 원문을 매번 모델에 넣는 것을 구분했다.

## 남은 제출·운영 증거

- 로컬 최신 전체532개 PASS(최종 원로그 [harness-audit-20261103-complete.log](logs/harness-audit-20261103-complete.log)); 11/1의465개와 배포판445개는 이전 시점/다른 범위 이력이다.
- 서버는 조회 당시 R2, 통역 ON·관리자 OFF. 양역할 온라인 재확인, 공개 카드·ACP는 재확인200. 한 차례 판매자 오프라인/GET 오류도 관측했으므로 무중단으로 포장하지 않는다.
- R3 원장 정산0건: 아직 가게20·buyer20 정산과 Players 결과가 충족된 것은 아니다. R2 누적 실적을 R3 실적으로 바꾸지 않는다.
- PR #372는 OPEN/non-draft,10/30 생성으로 PR 생성 마감 조건은 충족한다. 그러나 최신 로컬 내용은 PR에 아직 반영되지 않았다. [배포 시 주의](deploy/vps/README.md): 서비스 이미지와 host `notify_r3.py`를 같은 판으로 맞춰야 새 전달 확인 프로토콜이 동작한다.
- 자문 근거는 숫자 포함·스크린·중복 검사까지이며 자연어 주장의 진위를 자동 증명하지 않는다. 중요한 제안은 원자료 대조 후 승인한다.
