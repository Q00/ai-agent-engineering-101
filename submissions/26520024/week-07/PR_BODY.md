## What I built

기존 week-07의 후기·거래 기억 기반 buyer와 Ed25519 서명 영수증 seller를 제출합니다.
실제 시장 로그로 라운드별 결과, 가격 변경 전후, 부정 영수증 사례와 이후 가게 선택을 분석했습니다.

## What I tried and discarded

서명 누락과 SDK 실패 처리 문제를 수정한 과정을 로그와 커밋에 보존했습니다.
별도 week-07-2의 미배포 개선은 제외하고 기존 배포 코드만 사용했습니다.
코딩 도우미를 사용했으며, 가격 변경의 인과 효과나 사기 경험을 기억한 회피라고 단정하지 않았습니다.

## How to run

Python 3.10+, requirements.lock의 의존성, 인증된 Codex CLI가 필요합니다.
모델은 gpt-6-astra, reasoning low입니다. 배포는 기존 키와 MARKET_OWNER 환경변수를 사용하며 비밀값은 포함하지 않습니다.

```bash
submissions/26520024/week-07/.venv/bin/python submissions/26520024/week-07/run_lab1_checks.py --phase lab2
submissions/26520024/week-07/.venv/bin/python scripts/check_week07.py submissions/26520024/week-07 --github LeeUichann
```

오프라인 65/65, 공식 시장 테스트 25/25, 구조·소유권 검사를 통과했습니다.
10월 27일 확인 시 seller와 buyer 모두 GitHub 계정으로 연결되어 온라인이었습니다.
큰 운영 로그는 전체 gzip 스냅샷과 연속 원문 발췌로 제출하며 범위와 SHA-256은 REPORT.md에 명시했습니다.

## Checklist

- [x] `python scripts/check_week07.py submissions/26520024/week-07 --github LeeUichann` passes locally.
- [x] Run logs are committed under `logs/`.
- [x] No API keys or private key files anywhere in the diff.
- [x] History is not squashed.
