# ASSIGNMENT: WEEK 06 — 제출 확인

강의의 「내 하네스로 만드는 buyer와 가게」와 ASSIGNMENT: WEEK 06은 같은 과제다.
STEP 1–10에서 필수 구현·반복 거래 검증·보고서를 완료했고, 이 단계에서는 PR 제출
범위와 최신 upstream 기준의 검사를 확인했다.

## 제출물

- `seller.py`, `buyer.py`, `harness.py`, `prompts/`, `requirements.txt`.
- `agent.json`: seller·buyer 모두 Claude Code + Haiku.
- `test-report.json`: 공식 테스트에서 생성한 21/21 통과 결과.
- `logs/trade-test.log` 및 seller·buyer 실행·모델 원본 로그.
- `REPORT.md`: 실행 명령, 판단 방식, 실패·수정의 로그 인용, A2A 밖의 계약.

다른 하네스를 사용하는 타 수강생과의 거래는 선택 과제다. 현재 보존한 교차 실행은
자체 에이전트의 Claude/Codex 조합이며, 타 수강생과 거래했다고 기록하지 않았다.

## PR 범위 정리

week-06 브랜치가 상속한 Week 05 파일 85개는 아직 upstream에 병합되지 않은 별도 과제였다.
기존 `week-05` 브랜치 `9aeb260`과 원래 로컬 파일을 모두 보존하고, 이번 제출 브랜치의
Git 인덱스에서만 제외하여 최종 PR 차이가 week-06 경로 안에만 남도록 했다.
기존 커밋 이력을 다시 쓰거나 실패 로그를 삭제하지 않았다.

## 검증 근거

2026-10-17에 최신 upstream/main `3defe07be2f0f5d78e11a839aa5210f633e82700`을 가져왔다.
공식 테스트·구조 검사·소유권 검사 스크립트는 로컬과 최신 upstream에서 동일하다.

`logs/assignment-submit-check-20261017-082349.txt`에 다음 결과를 보존했다.

- 소스 21개 파싱, agent.json, 공식 결과 21건, 프로토콜 로그, 보고서: 전체 통과.
- 당시 PR 차이 2,722개 파일이 모두 본인 week-06 안에 있음.
- 전체 파일에 대한 공식 `check_pr_paths.py kcwarni` 검사 통과.

그 뒤 추가하는 제출 안내·PR 설명·검증 로그도 모두 같은 week-06 경로에 있다.
`git merge-tree --write-tree upstream/main HEAD`의 종료 코드는 0이었고 계산된 트리는
`b69fb896acf55c23d2f09892f4b92cbf70a2ff0e`다. 작업 파일이나 브랜치를 병합하지 않고
Git 객체에서만 충돌 여부를 계산했다. GitHub의 최종 CI·병합 상태는 PR에서 확인한다.

PR 제목: `[week-06] 24522014`.
PR 설명 원본: `PR_BODY.md`.

## 원격 제출 결과

2026-10-17 08:39:45 KST에 upstream PR을 생성했다.

- PR: [#299 — [week-06] 24522014](https://github.com/Q00/ai-agent-engineering-101/pull/299).
- base: `Q00/ai-agent-engineering-101:main`.
- head: `kcwarni/ai-agent-engineering-101:week-06`.
- 생성 당시 head `75ec4b1`의 GitHub `check`가 completed / success였고,
  mergeable=true, mergeable_state=clean을 확인했다.
- [확인한 CI 실행](https://github.com/Q00/ai-agent-engineering-101/actions/runs/38982606348/job/117246693696).

PR 생성 응답과 해당 시점의 CI 상태는 `logs/assignment-pr-*.json`,
`logs/assignment-ci-*.json`에 기록했다. 이 제출 기록을 추가로 push하면 GitHub가
새 head에 대해 검사를 다시 실행하므로 최신 상태는 PR 페이지가 기준이다.
PR은 제출을 위해 열어 두며 병합하지 않는다.
