## 구현

Claude Code + Haiku가 가격을 판단하는 A2A 1.0 JSON-RPC seller와 buyer를 제출합니다.
seller는 task별 흥정·영수증·거절·취소를 처리하고, buyer는 Agent Card를 읽어 같은
taskId로 제안하며 예산 안에서 구매하거나 철수합니다. 하한가와 예산은 프롬프트와
코드에서 검증하고, 오류 시 규칙 기반 가격으로 대체하지 않습니다.

Codex + Luna 및 두 방향의 교차 조합도 선택할 수 있습니다. 현재 agent.json과 공식
제출 결과는 seller·buyer 모두 Claude Code + Haiku인 설정입니다.

## 검증

- 공식 trade test: 21/21 PASS. 실제 모델을 사용하는 결정 검사는 각각 3회 통과했습니다.
- 공식 check_week06.py: all checks passed. 현재 테스트 해시·명령과 결과가 일치합니다.
- 소유권 검사 통과. PR 최종 변경은 submissions/24522014/week-06/ 안에만 있습니다.
- 최신 upstream/main에 대한 로컬 병합 계산에서 충돌이 없습니다.

REPORT.md에 프롬프트·응답 형식·제약 검증 위치, 실패 로그와 수정,
A2A와 별도로 합의한 가격·수락 문법을 기록했습니다. 실패 시도와 원본 로그를 보존했고
커밋을 squash하거나 이력을 다시 쓰지 않았습니다. 이전 주차 파일은 로컬 및 기존
브랜치에 보존하면서 이번 PR의 최종 변경 범위에서 제외했습니다.
