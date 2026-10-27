# Week 07 · 26510121 · seochanit

가게 선택·기억·후기를 가진 Market Buyer와 Ed25519 서명 영수증을 발급하는 A2A Shop을 구현했다. 기본 모델은 `gpt-4o-mini`다. 실제 Round 2 시장에서 두 필수 도전을 수행했고 공식 market test 25/25, 로컬 테스트 113개, 제출 구조 검사를 통과했다.

## 먼저 볼 문서

- [과제 보고서](REPORT.md): 필수 여섯 항목, Round 0·1·2 결과, 실패와 수정, 배포 및 한계.
- [도전 1](logs/challenge-1/README.md): 같은 가게의 가격 정책을 바꾼 실제 관찰과 점유율·평균가 비교.
- [도전 2](logs/challenge-2/README.md): 실제 주문의 합의 금액/영수증 불일치와 후속 회피 증거.
- [운영 방법](OPERATIONS.md): Buyer·Shop 기동, 상태 확인, 중지와 재검증.
- [제출 전 점검](PRE_SUBMISSION_REVIEW.md), [PR 본문 초안](PR_BODY.md).

## 검증

저장소 루트에서 Python 3.10 이상 환경을 사용한다. 검증 환경은 Python 3.12다.

```powershell
python -m pip install -r submissions/26510121/week-07/requirements.txt
python -m pip install -r submissions/26510121/week-07/agent-market/requirements.txt
python scripts/market_test_week07.py submissions/26510121/week-07
python scripts/check_week07.py submissions/26510121/week-07 --github seochanit
```

market test에는 실행 환경의 `OPENAI_API_KEY`가 필요하다. 실제 키를 명령이나 문서에 넣지 않는다. 공식 테스트를 다시 실행하기 전 기존 `test-report.json`과 루트 테스트 로그를 새 폴더에 복사해 보존한다. 실험 차트 재생성에만 `experiments/requirements.txt`의 matplotlib이 추가로 필요하다.

최신 공식 실행: [원본 콘솔](logs/hw-completion/20261027-160050/final-market-test/console.log). 원본 운영 기록은 `logs/market-kit.log`, 시장 검증 피드백은 `memory/market-verification.jsonl`이다.

## 범위와 한계

구간별 가격 실험은 비무작위 관찰이며 정책만의 인과 효과를 보장하지 않는다. 시장 집계와 현지 영수증 발행 시점이 다른 점도 보고했다. 운영 장소는 현재 Windows PC이며 PC·네트워크 유지가 필요하다. 키트 실행 중 자동 유휴 절전은 방지하지만 수동 종료·정전·로그아웃을 막지는 않는다. 실제 API 비용이 발생한다.

Week 06 구현을 출발점으로 사용했다. 기존 `CHALLENGE*_RESULTS.md`, `BUYER_RESULTS.md`, `SELLER_RESULTS.md`, `catalog/`, `shopping/`와 오래된 로그는 그 이력이며 Week 07 두 도전의 증거와 혼합하지 않는다. Week 07의 채점 근거는 위 보고서와 `logs/challenge-1/live-20261027-*`, `logs/challenge-2/live-20261027-*`다.

PR은 사용자가 직접 생성한다. 제목은 `[week-07] 26510121`, base는 `Q00/ai-agent-engineering-101:main`, head는 포크의 `week-07-submit`이다. 이 제출 브랜치는 원래 `week-07`의 모든 커밋을 조상으로 보존하면서, PR 파일 변경에는 이 디렉터리만 포함하도록 준비한다. API 키·claim·PEM·`.env`는 제출하지 않는다.
