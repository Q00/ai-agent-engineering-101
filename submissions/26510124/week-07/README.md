# Week 07 — dyishappy / 26510124

서명 영수증을 발행하는 A2A 판매자와, 후기·거래 기억으로 가게를 고르는 구매자다.
협상과 선택은 Claude Opus 5.5가 담당하고 Python은 예산·reserve·영수증을 검사한다.
현재 시장 실행은 중지되어 있고 상시 서버 이전은 아직 하지 않았다.
Round 2 인정 구매 17건은 확인했지만 가동 중이라는 뜻은 아니다.
제출 준비 보고서는 REPORT.md, 과거 설명은 README-HISTORY.md / REPORT-HISTORY.md다.

## 설치

전체 수업 저장소를 같은 구조로 준비한다. 공개 실행기가 공식 scripts/market_kit.py를
읽으므로 week-07만 복사해서는 동작하지 않는다. Python 3.10 이상이 필요하고 기존은 3.12다.

```bash
cd submissions/26510124/week-07
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
claude --version
claude auth status
```

서버에도 Claude 하네스를 실행할 인증이 필요하다. 인증은 서버에서 처리한다.
기존 shop_key.pem과 buyer_key.pem을 안전하게 이전하되 Git에 넣지 않는다.

## 구성

| 파일 | 역할 |
|---|---|
| seller_agent.py, receipt_signing.py | 판매자와 Ed25519 영수증 서명 |
| buyer_agent.py | Round 0 / 6주차 구매자 |
| market_buyer.py | 후기·이름 기반 기억·선택 이유·마지막 JSON 결과 |
| harness.py, prompts/ | Claude 호출, 출력 검증, 원본 로그 |
| shop-round2.json | 현재 판매 문구와 성사 우선 전략 |
| agent.json | seller / buyer / market_buyer 명령 |
| data/market-memory.jsonl | 실제 시장 거래 기억 |
| public_market.py, run_public_market.py | 공개 프록시와 기존 터널 갱신 실행기 |

agent-round2.json은 준비 단계부터 보존한 설정이며 현재 agent.json과 같다.
6주차 카탈로그 코드·실험 로그는 이력 보존용이며 7주차 챌린지와 구별한다.

## 검증

week-07 디렉터리에서:

```bash
.venv/bin/python -m unittest -v test_harness test_seller_opening test_seller test_buyer test_market_buyer test_receipt_signing test_public_market test_shop_marketing
.venv/bin/python verify_round2.py
```

두 번째는 실제 하네스를 호출한다. 설정·키를 .market/의 새 격리 디렉터리에 복사하고
공식 시장 테스트를 실행한다. 라이브 기억은 건드리지 않는다. 키를 제외한 소스·해시·
원본 출력은 새 logs/round2-check-*에 보존한다. 실패한 실행도 남긴다.
agent.json과 agent-round2.json이 달라지면 준비 설정 결과를 현재 설정 결과로 사용하지 않는다.

공식 명령은 저장소 루트에서 다음과 같다. 시장 테스트는 결과 파일을 생성하므로
기존 로그를 보존한 격리 디렉터리에서 실행하는 것을 권장한다.

```bash
submissions/26510124/week-07/.venv/bin/python scripts/market_test_week07.py submissions/26510124/week-07
submissions/26510124/week-07/.venv/bin/python scripts/check_week07.py submissions/26510124/week-07 --github dyishappy
```

macOS 인증서 오류가 있으면 해당 Python의 certifi 인증서 경로를 SSL_CERT_FILE에
지정한다. 인증서 검증 자체를 끄지 않는다.

## 시장 실행

https://market.ourolabs.ai/join 에서 GitHub를 연결하고 claim code를 실행 환경의
MARKET_OWNER로 제공한다. 값을 커밋하지 않는다. 서버 인증·네트워크를 확인한 뒤
저장소 루트에서 실행한다.

```bash
submissions/26510124/week-07/.venv/bin/python scripts/market_kit.py join --dir submissions/26510124/week-07 --market https://market.ourolabs.ai --name dyishappy --tunnel
```

--tunnel에는 cloudflared가 필요하다. 판매자를 별도 배포했다면 공식 안내대로
--deployed <shop URL>을 쓴다. 구매자를 구동하는 키트도 계속 실행되어야 한다.
이미 가동하는 키트가 있다면 중복 실행하지 않는다. 서버의 프로세스 관리·재시작
설정은 환경을 확인한 후 결정한다. 아직 설정했다고 주장하지 않는다.

기존 Mac용 대체 실행기는 week-07에서 다음과 같이 실행한다.
기존 Serveo 키 .market/serveo_key.pem도 필요하다.

```bash
.venv/bin/python run_public_market.py --name dyishappy
.venv/bin/python check_public_shop.py --verify-signature
```

## 챌린지와 제출

보존된 대시보드·실행 로그의 분석 재현:

```bash
.venv/bin/python collect_submission_evidence.py --snapshot logs/preflight-market-20261026T224624
```

logs/challenge-1/ 및 logs/challenge-2/ 아래 새 폴더를 만들고 출처 해시와 한계를
기록한다. 원본은 수정하지 않는다. 가격 변경 직전 Round 1 거래가 0건이므로
평균 거래가는 정의할 수 없다. 부정직 사례는 Maple Home의 합의 185 / 영수증 210이다.

제출물은 코드, agent.json, 최신 공식 test-report.json, 시장 테스트·실거래·
두 챌린지 로그, 여섯 항목을 설명한 REPORT.md다.
마감까지 판매자와 구매자가 GitHub 이름으로 가동되어야 한다. 아직 PR은 열지 않았다.
