# Week 08 제출 점검

- 계정: outSeop / 학번26510122
- 제출 대상: Q00/ai-agent-engineering-101, 제목 `[week-08] 26510122`
- 마감: 2026-11-23 23:59 KST, PR 개설 시각 기준
- 보고서: 부품별 설계, 기본 키트와의 차이, 정산·Players Class·거부 로그·10주차 비교 포함
- 제출 시점 Round3 조회: 2026-11-22 14:34 KST, seller20704 / buyer14219 / Class22위(46명), student_pct0.533
- 종료 당일23:59 보관 스냅샷은 미확보. 마감 후 현재 조회값과 구분
- architecture.tex / architecture.pdf: 원본과 한 페이지 PDF 존재
- 전체 회귀625개 통과. 날짜 의존 실패와 공유 종료 시각 실패 로그 보존
- 운영 코드·실제 종료 상수·기존 DB·매입 원장 유지
- upstream 대비 변경은 본인 submissions/26510122/ 내부. 공식 소유권 검사 통과
- check_week08.py: 로컬과 최신 upstream에 없어 실행했다고 주장하지 않음
- 공개할 과거 Git blob1317개, 약163MB에 대해 키·등록 코드 패턴 검사 결과 후보0
- 루트 logs/market-kit.log는 배포 로그의 고정 사본을 제출 전용 브랜치에 포함. 운영 중인 원본은 수정하지 않음
- 원격 제출: [PR #409](https://github.com/Q00/ai-agent-engineering-101/pull/409), 제목 `[week-08] 26510122`, open / 일반 PR
- PR 개설: **2026-11-22 14:47:24 KST**, 11월23일23:59 제출 마감 전
- 최초 제출 head: e2b13a3e77fc094707925aae72e0471279a1ec8f
- 원격 변경732개 모두 본인 제출 폴더. REPORT.md / agent.json / architecture.tex / architecture.pdf / logs/market-kit.log 포함 확인
- 최초 GitHub 자동 검사: [pr-check #744](https://github.com/Q00/ai-agent-engineering-101/actions/runs/44990910715) 성공
- 최신 검사와 병합 상태는 [PR 검사 화면](https://github.com/Q00/ai-agent-engineering-101/pull/409/checks)에서 확인. 제출은 PR 개설 기준이며 교수자의 병합과 구분
- 기본 브랜치 병합·운영 재배포·DB 초기화는 수행하지 않음

## 필수 로그 재확인

- `logs/market-kit.log`는 PR Files changed에 포함돼 있다.
- 제출 파일의 원격 바이트를 내려받아 배포 로그 보존본과 일치함을 확인했다.
- 실제 `[acp] settled ... ledger #...` 정산 줄111개.
- API 키·개인키·Bearer·등록 코드 패턴 검사에서 후보0.
- 제출 브랜치 `.gitignore`에 `!/logs/market-kit.log` 예외 추가. 키·DB·런타임 상태 무시 규칙은 유지.
- 운영 원본과 seller-market-share의 실시간 로그 무시 규칙은 수정하지 않았다.
- 상세 해시·원문 정산 예시는 `logs/required-market-kit-submission-check-20261122.json`에 보존.
