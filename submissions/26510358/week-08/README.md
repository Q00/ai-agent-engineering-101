# Week 08 키트 v3 운영

- 수행 범위: 강의 SETUP의 Step 1 다운로드, Step 2 재가입, Step 3 확인 완료함
- 공식 원본: https://github.com/Q00/ai-agent-engineering-101/blob/main/scripts/market_kit.py
- 원본 Git blob: `a1c7a5cfff4c1783ecaaf128a129c8f8fe70c9d6`
- `market_kit.py`는 공식 원본과 바이트 단위로 동일하며 `KIT_VERSION = "3"`임
- 저장소 AGENTS 지침의 공유 `scripts/` 읽기 전용 규칙에 따라 학생 폴더에 저장함
- 구문 파싱, 기존 가상 환경에서 의존성 import, CLI 도움말 실행 확인함
- 기존 Week 07 에이전트 폴더, 이름 `myeonggi`, 소유 계정 `SUNGMYEONGGI`, 개인키를 그대로 사용함
- 외부 배포 모드에서 v3 터널 모드로 재가입해 공식 키트가 ACP checkout 경로를 제공함
- `week08-myeonggi-market.service` 사용자 서비스가 운영하며 이전 Buyer 서비스는 중지함
- 현재 공개 주소는 https://customs-whilst-thy-gate.trycloudflare.com 임
- 공개 ACP 안내는 https://customs-whilst-thy-gate.trycloudflare.com/.well-known/acp.json 에서 HTTP 200 확인함
- 새 `week-07/logs/market-kit.log` 첫 줄의 `market_kit 3`와 재가입 후 실제 검증 거래를 확인함
- 2026-10-27 재가입 시 시장은 Round 2였으므로 Round 3의 정산 성공을 주장하지 않음

저장소 루트에서 도움말 확인하는 명령임

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python submissions/26510358/week-08/market_kit.py --help
```

다운로드 검증과 실행 출력은 `logs/kit-v3-step1-*`에 보존함

현재 서비스가 사용하는 실행 명령임
같은 이름의 Buyer를 중복 실행하지 않도록 서비스 운영 중에는 별도로 실행하지 않음
소유권 코드는 기존 `week-07/.market/owner.env`에서 읽고 하위 에이전트 환경에 전달하지 않음

```sh
MARKET_KIT_DIR="$PWD/submissions/26510358/week-08" \
  .venv/bin/python -u submissions/26510358/week-07/market_runtime.py join \
  --dir submissions/26510358/week-07 --market https://market.ourolabs.ai \
  --name myeonggi --tunnel
```

```sh
systemctl --user status week08-myeonggi-market.service
```

재가입 전후 공개 보드, ACP 안내, 검증 결과, 시작부터 첫 거래까지의 원본 출력 스냅샷은
`logs/v3-rejoin-20261027T105244Z/`에 보존함
기존 누적 로그는 키트의 자동 순환 전에 `.market/market-kit-before-v3-20261027T105244Z.log`에
하드 링크로 보존했으며 해당 경로는 커밋하지 않음
