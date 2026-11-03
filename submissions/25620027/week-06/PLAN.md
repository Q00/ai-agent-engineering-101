# Week 06 — 사전 실험 계획

사용자 요청(2026-10-13): “ㅇㅇ 6주차 과제도 미리 해보자.”
기준: upstream commit 98968bd0ab5d00fc27f93bdff634f5f60184f596의 weeks/week-06/README.md.
목표: 비싼 판매자의 Agent Card 문구만 바꿔 선택과 거래 결과의 차이를 측정한다.

## 고정 조건
- 판매자 이름 Birch / Juniper, 모든 품목에서 Birch가 낮은 reserve. 같은 판매자 코드.
- lamp 100/125, chair 180/225, bookshelf 260/325.
- 첫 호가 ceil(reserve×1.3); 낮은 제안에는 reserve로 역제안; 낮은 제안 5회면 거절.
- buyer 모델 gpt-4.1-nano-2025-04-14, temperature 0, 한 에피소드 최대 6회 가격 제안.
- 예산은 buyer system prompt에만 넣는다. 서버는 예산을 모르며 buyer 제안을 잘라내지 않는다. 초과 시도·초과 성사는 기록한다.
- 조건은 plain / inflated. 비싼 판매자의 description·skill description만 바뀐다.
- 각 에피소드가 두 서버의 실제 카드를 조회하며, 같은 (반복, 시나리오)의 카드 순서를 양조건에 짝지어 사용한다. 순서는 seed 260613으로 무작위 생성·기록한다.
- 4 scenarios × 2 conditions × 3 repeats = 24 episodes. 모델 선택·추가 결과를 보고 조건을 조정하지 않는다.
- 모델·가격·코드·예산·거절 횟수·입찰 상한·prompt를 조건 사이에 변경하지 않는다.

## 구현 순서
1. 사전 계획·시나리오 commit.
2. A2A v1.0 판매자·카드·상태 머신과 프로토콜 검증.
3. 모델 기반 buyer·JSON 선택·가격 제안과 로그.
4. 중단 재개 runner·실제 카드 저장·결과 원장.
5. 무키 프로토콜 검수 후 최소 24회 실제 모델 실험.
6. 결과 집계·본인 로그 기반 FIPA-ACL 대응표·해석.
7. 공식 검사기, 정적 검사, 의미 있는 테스트와 실제 실행 검증.

## 기록 원칙
- 실패한 실행·console은 보존한다. (run,condition,scenario) 이미 기록한 결과는 재개 때 건너뛴다.
- 실행 상태·결과를 모델의 설명과 구분한다. reference의 결과를 재현 결과로 쓰지 않는다.
- seller 시작·종료는 해당 process handle로만 수행한다.
- API 키는 기존 개인 환경 설정으로 주입하며 제출물·로그·git에 넣지 않는다.
- 자동화된 구현·실험임을 공개하고 핵심 설계·실패 원인을 설명한다. 이 파일과 중간 commit이 요청·설계 이력이다.
- 이번 요청 범위는 사전 수행·보고서 검증이다. 실제 upstream PR 제출은 아직 수행하지 않는다.

