## What I built

5주차 Python 하네스의 모델→function tool→실행 결과→재판단 루프를 구매자와 판매자 양쪽에 연결한 A2A 에이전트입니다. OpenAI `gpt-4.1-nano-2025-04-14`를 사용하며, `agent.json`에 판매자·구매자 실행 명령을 등록했습니다.

- 공식 기본 검사: **21/21 통과**.
- 필수 챌린지 1: **3,008개 카탈로그, 고정 설명형 요청 6/6 통과**, 잘못된 SKU·reserve 위반 0건.
- 필수 챌린지 2: **20개 가게에서 5품목 구매, 총 1,757/2,200**, 중복 구매 0건·남은 예약액 0.
- 추가 실패 유도 실험: 실제 협상 미체결 후 기존 구매를 유지하고 예약 해제→재계획→대체 가게 구매를 관찰했습니다. **5품목, 총 1,686/2,200**으로 완료했습니다. 일반 챌린지 2에서도 실제 미체결 후 재계획을 관찰했습니다.

[최종 보고서](https://github.com/SungJinho/ai-agent-engineering-101/blob/week-06/submissions/25620027/week-06/REPORT.md) · [기본 검사 결과](https://github.com/SungJinho/ai-agent-engineering-101/blob/week-06/submissions/25620027/week-06/test-report.json) · [일반 챌린지 2 로그](https://github.com/SungJinho/ai-agent-engineering-101/tree/week-06/submissions/25620027/week-06/logs/challenge-2/attempt-6) · [실패 유도·재계획 로그](https://github.com/SungJinho/ai-agent-engineering-101/tree/week-06/submissions/25620027/week-06/logs/challenge-2/replanning-attempt-3)

공개 배포용 단일 프로세스 실행 구성·Docker·HTTPS 터널 설정과 외부 거래 검증 도구도 추가했습니다. 공개판에만 Cloudflare Workers AI 무료 모델을 별도 지정하도록 구성했고, 기존 OpenAI 실험 설정은 유지했습니다. 인증 설정·오류 표시·주변 환경 헤더를 분리하고 HTTP 리디렉션을 거부합니다. 추가 구성까지 포함한 오프라인 테스트는 **117개 통과**했습니다. **현재 Cloudflare 로그인·전용 AI 키 생성·계정 Free 상태 확인 대기이며, 무료 모델의 공개 실거래를 검증하지 않아 배포 가산점을 완료했다고 주장하지 않습니다.** [공개 배포 준비 상태](https://github.com/SungJinho/ai-agent-engineering-101/blob/week-06/submissions/25620027/week-06/deployment/README.md)

## What I tried and discarded

초기 기본 검사의 조기 협상 종료·존재하지 않는 제안 수락 문제를 수정했습니다. 이후 쇼핑에서 동일한 실패 계획 반복, 품목별 한도 부족, 잘못된 상품군 선택을 관찰하고 실패 가게·한도·공개 관찰을 다음 계획에 전달하도록 보완했습니다. 실패 시도와 수정 커밋, 원본 로그를 모두 보존했습니다. 기존 plain/inflated 24회 실험도 별도 보고서와 원본 자료로 남겼습니다.

해석의 한계도 보고서에 명시했습니다. 챌린지 1의 6개 요청은 코드 필터 뒤 후보가 각각 1개이므로 일반적인 다중 후보 선택 능력을 입증하지 않습니다. 예산 안전성에는 코드 가드가 기여하며, 실제 재계획 성공 2회로 일반적인 복구 신뢰도나 최저가를 주장하지 않습니다. 원격 체결 여부가 불명인 통신 오류 복구와 다른 학생 하네스 간 교차 검증은 이번 검증 범위 밖입니다.

## How to run

실거래 실행에는 본인 환경의 `OPENAI_API_KEY`가 필요합니다. Python 3.12.11·a2a-sdk 1.2.2와 잠금 파일을 사용합니다.

저장소 루트:

```bash
python scripts/check_week06.py submissions/25620027/week-06
python scripts/trade_test_week06.py submissions/25620027/week-06
```

제출 폴더에서 별도 터미널로 실행:

```bash
./run_harness.sh seller --port 9998
./run_harness.sh buyer --url http://127.0.0.1:9998/ --item lamp --budget 120
```

챌린지 재현 명령과 새 attempt 번호 사용 방법은 REPORT.md에 있습니다. 검증 기록은 [VALIDATION.md](https://github.com/SungJinho/ai-agent-engineering-101/blob/week-06/submissions/25620027/week-06/.omo/evidence/week06-report-followup-20261014/VALIDATION.md)에 정리했습니다. 로컬 공식 검사와 Ruff·포맷·strict basedpyright 검사를 통과했습니다. 기존 필수 과제 후속 검증은 pytest 89개였고, 추가 공개 실행 구성·검증 도구·무료 모델 설정까지 포함한 오프라인 테스트는 현재 117개입니다. 공개 가동이나 무료 모델 거래, 새 유료 기본 검사 재실행을 완료했다는 뜻은 아닙니다.

## Checklist

- [x] `python scripts/check_week06.py submissions/25620027/week-06` 로컬 통과
- [x] 공식 기본 검사 21개 및 필수 챌린지 1·2의 실행 로그 제출
- [x] 실제 실패 후 재계획의 원본 로그·영수증·예산 원장 제출
- [x] 변경은 `submissions/25620027/week-06/` 안에만 있음
- [x] API 키·토큰·환경 파일 없음
- [x] 실패 이력을 포함한 커밋 기록 보존, squash·history rewrite 없음

- [ ] 선택 공개 배포 가산점: 무료 API 설정 준비, 계정 로그인·전용 키·실제 공개 거래 검증 대기
