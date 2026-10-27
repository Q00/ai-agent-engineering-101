# Week 07 제출 전 점검

검토일: 2026-10-27. 명세는 `weeks/week-07/README.md`와 `week-07.html`이다. PR은 사용자가 직접 생성하므로 이 작업에서는 열지 않는다.

| 점검 | 근거 |
|---|---|
| 서명 영수증과 하한가 | 공식 market test의 seller 항목 통과 |
| Buyer 선택·기억·후기·예산 | 공식 buyer 항목과 로컬 계약 검사 통과 |
| 공식 market test | 25/25, `test-report.json` 및 원본 콘솔 |
| 로컬 회귀 검사 | 113개 통과 |
| 필수 도전 1 | 실제 두 가격 정책 관찰, 점유율·평균가와 계산·원본·차트 저장 |
| 필수 도전 2 | 실제 시장 주문 130 합의/145 영수증, 검증 거부 후 실제 회피 로그 저장 |
| 보고서 | 여섯 항목 및 실패·중지 기간·집계 한계 포함 |
| 운영 | Windows PC에서 두 역할 실행, R2 인정 거래와 공개 카드 응답 확인 |
| 비밀값 | 최종 PR 대상 파일·이력 점검에서 키, claim, PEM, `.env` 제외 |
| 이력 | 실패 커밋을 유지하고 squash·rebase·force push 없이 제출 브랜치 준비 |

검사 결과는 `logs/hw-completion/20261027-160050/`와 제출 준비 실행이 만든 새 `logs/pr-preparation/`에 보관한다. 현재 upstream의 `market_test_week07.py`, `check_week07.py`는 검증한 파일과 일치한다. upstream에 추가된 Week 08/kit v3는 별도 과제이며 이 보고서의 실거래 결과는 Round 2 범위다.

## PR 설정

- base repository: `Q00/ai-agent-engineering-101`
- base branch: `main`
- head repository: `seochanit/ai-agent-engineering-101`
- compare branch: `week-07-submit`
- 제목: `[week-07] 26510121`
- 본문: [PR_BODY.md](PR_BODY.md)

기존 `week-07`은 미병합 6주차 변경도 포함한다. 제출용 브랜치는 최신 upstream main과 현재 week-07을 부모로 가지는 merge commit을 사용하고, 최종 트리에는 upstream의 나머지 파일과 현재 week-07 디렉터리만 합친다. 원래 브랜치와 모든 시도 커밋을 유지하며, 실행 중인 작업 폴더와 6주차 파일은 바꾸지 않는다. PR의 변경 경로가 `submissions/26510121/week-07/`로 한정되는지 확인한다.

GitHub CI는 PR 생성 후에 실행된다. 로컬 검사 통과를 GitHub CI 통과라고 표시하지 않는다. 마감은 Week 8 수업 시작 전이며 PR 생성 시각 기준이다. 상시 운영은 PC와 네트워크 유지에 달려 있으며 이전 중지 기간을 소급해 가동했다고 주장하지 않는다.

운영 프로세스를 중단하지 않았으므로 커밋 이후에도 로컬 로그와 기억 파일이 계속 늘어난다. 제출 브랜치는 커밋 시점까지의 코드·보고서·증거를 고정한 스냅샷이며, 이후 생기는 로컬 로그 변경을 미완성 코드와 혼동하지 않는다.
