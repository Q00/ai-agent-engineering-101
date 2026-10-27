# Week 08 강의 위치와 진행 기록

기준: 저장소 루트 `week-08.html` 전체, upstream 커밋 `c89c58d`.
작업일: 2026-10-27 (Asia/Seoul).

사용자 요청: 진행할 때마다 HTML의 현재 부분, 확인 결과, 다음 부분을 매칭해서 설명한다.

| 순서 | HTML 위치 | 역할 | 이번 작업 범위 |
| --- | --- | --- | --- |
| OPEN | 7주차 웃긴 에피소드 | 담합, 후기 조작, 예산 노출 등의 관찰 | 공개 거래 편중은 의심 신호이며 담합 확정 근거와 구분 |
| SETUP STEP 1 | 키트 v3 받기, 386행 | `KIT_VERSION = "3"` 확인 | 확인 완료 |
| SETUP STEP 2 | 다시 join, 394행 | 기존 `week-07` seller/buyer를 그대로 재실행 | 현재 작업 |
| SETUP STEP 3 | 확인, 400행 | v3 시작 로그와 공개 `/.well-known/acp.json` 확인 | 재실행 후 검증 |
| L1 | 거래 스택과 빠진 층, 421행 | 발견·협상·실행·결제·기록 구분 | 결제 층의 추가를 설명 |
| L2 | ACP와 AP2, 442행 | checkout 및 open/closed mandate 설명 | 수업 키트 구현을 사용 |
| DEMO | 공격 장면 재현, 559행 | 강사의 정상·위조·상한 초과·재사용·숨긴 공지 시연 | 로컬 테스트와 실제 시장 거래를 구분 |
| R3 | 정산된 거래만 인정, 577행 | 주문 → 협상 → checkout → closed mandate → 정산 → 기록 | 현재 시장 Round를 조회하고 v3로 대기 |
| LAB STEP 1 | 내 buyer의 결제 로그, 626행 부근 | `[acp] paid`, `verified True`, `[acp] settled` 추적 | Round 3 개장 후 할 일 |
| LAB STEP 2 | ledger에서 사라진 예산 | `budget_commitment`, `settle`, `checkout_hash` 확인 | Round 3 개장 후 할 일 |
| LAB TRY IT | 정산 위조해 보기 | 가짜 요청이 실패하는 검사 관찰 | 아직 실행하지 않음 |
| L3 | MeshKit으로, 661행 | 9주차 프로젝트와 부품 대응 | 설명 단계 |
| HW | Round 3 시장에 남기, 680행 | seller/buyer 정산, 호가 전략 비교, REPORT | 이번에는 시작 준비까지만 진행 |
| MIDTERM | 재현 실험 보고서, 712행 | W03~W07 중 하나를 확장 | 별도 과제 |
| READ | 읽기자료, 756행 | ACP, AP2 필수 읽기 | 필요할 때 해당 원문 확인 |

## 경로와 보존 원칙

- SETUP의 명령은 `--dir submissions/<학번>/week-07`이다. 새 에이전트를 week-08에 다시 구현하라는 지시는 없다.
- week-08 README가 아직 없으며, HTML의 HW 주석도 README가 생길 때까지 경로와 테스트는 week-07을 따른다고 명시한다.
- 실행 폴더는 `submissions/26510121/week-07/`로 유지한다. 이 폴더의 본인 제출 코드만 `week-07-submit`에서 복원했다.
- week-07-submit 브랜치와 기존 stash는 보존한다. 기존 로그를 편집하지 않는다.
- week-08 폴더에는 강의 위치, 준비 과정, 새 검증 자료만 저장한다. 수업 공용 `scripts/`와 HTML은 수정하지 않는다.
- 기존 API 키와 서명 키를 재사용한다. API 키, claim code, PEM 및 시장 토큰을 커밋하지 않는다.

## 전략의 범위

- 사용자 요청: 담합으로 의심되는 상대를 찾아 내 buyer가 불리해지지 않도록 한다.
- 공개 ledger와 registry가 확인해 주는 구매 편중·상호구매만 측정하고, 거래 범위와 표본 수를 함께 남긴다.
- 공개 자료만으로 비공개 공모를 확정하거나 모든 상대를 탐지했다고 주장할 수 없다.
- 다른 참가자에게 공개 제재를 가하거나 자동 신고·후기 공격을 하는 대신, 개인 거래 선택과 검증 가능한 증거 기록에 사용한다.
- 기존 협상·가격 프롬프트는 SETUP 단계에서 변경하지 않는다. HW의 호가 전략은 사용자가 이해하고 선택할 후속 실험이다.

## 진행 중 발견한 문제

- 기존 가게 전용 `shop_runtime.py`는 v2에서 만든 실행 경로다. v3의 `Proxy.acp = AcpShop(...)` 연결이 없어 ACP descriptor가 제공되지 않는다.
- 본인 폴더의 실행기만 수정해 수업 v3의 ACP 구현을 연결하고 회귀 테스트한다.
- 2026-10-27 공개 `/api/shops` 첫 조회에서 현재 시장은 Round 2, registry는 42개 가게를 반환했다. Round 3 정산 완료를 뜻하지 않는다.
