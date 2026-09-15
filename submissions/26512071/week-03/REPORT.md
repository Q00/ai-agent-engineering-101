# Week 03 — Contract Net with LLM contractors

## 1. 실험 설정

- 제공자·모델·temperature: 실행 전에 확정
- 계약자: `coder`, `analyst`, `writer`
- 조건: `baseline`, `homogeneous`, `overconfident`
- 실행 방법: 구현 후 기록

세 조건은 같은 태스크, 모델, temperature를 사용한다. baseline은 서로 다른 전문성을
가진 계약자 세 명, homogeneous는 이름은 같지만 모두 generalist인 계약자 세 명,
overconfident는 baseline에서 한 계약자만 모든 공고에 높은 확신으로 입찰하도록 설정한다.

## 2. 측정 결과

| condition | runs | correct | messages | unassigned | misawards |
|---|---:|---:|---:|---:|---:|
| baseline | 실행 전 |  |  |  |  |
| homogeneous | 실행 전 |  |  |  |  |
| overconfident | 실행 전 |  |  |  |  |

## 3. Smith(1980)과 재현 실험 비교

| 비교 항목 | Smith(1980) | 이번 재현 |
|---|---|---|
| 노드 | 조사 후 작성 | LLM 계약자 3명 |
| 입찰 생성 | 조사 후 작성 | 시스템 프롬프트를 받은 LLM의 판단 |
| 입찰 정직성 보장 | 조사 후 작성 | 실험 결과로 확인 |
| 배정 품질 | 조사 후 작성 | gold 계약자에게 배정된 태스크 수 |
| 협상 비용 | 조사 후 작성 | 공고·입찰·낙찰 메시지 수 |
| 실패 모드 | 조사 후 작성 | 무입찰, 오배정, JSON 파싱 실패 |

## 4. 해석

실험 완료 후 어느 조건이 어떤 지표를 움직였는지 로그의 실제 입찰과 낙찰을 근거로
한 문단으로 작성한다.
