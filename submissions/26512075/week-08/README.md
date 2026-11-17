# Week 08 — 웹앱 서비스 배포를 고려한 거래 에이전트

26512075 / helenjeon101

이 프로젝트의 아키텍처 전략은 **실제 웹앱 서비스 배포를 염두에 둔 역할 분리, 입력 검증, 상태 보존, 결과 피드백**이다. 시장의 매매 전략과 서비스 설계 원칙을 구분한다. 현재 거래 실행은 로컬 시장 키트가 담당하며, 웹 배포용 ASGI 진입점도 제공한다.

## 아키텍처 요약

| 구성 | 역할 |
|---|---|
| TypeScript Buyer (`buyer/src/`) | 주문에 맞는 상점 선택, 협상 요청, 예산·영수증 검증, 제한된 대체 상점 시도 |
| Python 하네스 (`harness/`) | 컨텍스트를 모델에 전달하고 구조화된 행동·가격을 반환 |
| Python Seller (`seller/seller_r3.py`) | 상품·가격·재고 확인, 협상 처리, 서명 영수증 발행 |
| 시장 키트 + `run_market.py` | 인증·시장 통신·결제·정산, 게시판 분석과 재입고 검증 연결 |
| Redis (`storage.py`) | task·협상 상태 저장, task 버전 충돌 검사, 기본 TTL 30일 |
| SQLite·JSON/JSONL | 게시판·광고 근거, Buyer 기억, 정산 결과와 실행 이력 보존 |

흐름은 `주문 → Buyer 선택 → 모델 협상안 → 코드 검증 → 키트 결제·정산 → 다음 주문의 기억`이다. 모델의 제안과 실제 실행을 분리하고, 협상 합의와 결제 완료도 구분한다.

Redis에는 재고 예약까지 저장하지 않는다. 예약은 seller 메모리이며, Redis·재고·결제를 묶는 전역 트랜잭션은 없다. **ASGI 서버만 실행한다고 시장 결제 경로 전체가 배포되는 것은 아니다.**

상세 설명:  **11/17 운영 로그에서는 Redis 선택과 실제 저장·조회 성공이 확인됐다.** 재시작 전후 동일 상태의 복구 검증과는 구분한다.

## 설치와 환경 설정

아래 명령은 저장소 루트에서 시작한다. Python과 Node.js/npm이 필요하며, 공개 터널 실행에는 `cloudflared`가 필요하다.

```bash
cd submissions/26512075/week-08
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm ci
mkdir -p logs
```

| 설정 | 용도 |
|---|---|
| `HARNESS_BACKEND=codex` | 기본 모델 실행 방식. 인증된 Codex CLI 필요 |
| `CODEX_MODEL` | 기본값 `gpt-6.1-terra` |
| `HARNESS_BACKEND=openai` | API 실행 방식. `OPENAI_API_KEY`, `OPENAI_MODEL`을 환경변수로 설정 |
| `REDIS_URL` | Redis 연결. 미설정 시 메모리 저장소 사용; Upstash는 TLS 연결 URL 사용 |
| `TASK_TTL_SECONDS` | 상태 보존 기간. 기본값 `2592000` |
| `SHOP_PRIVATE_KEY_PEM` | 기존 상점의 서명 키. 미설정 시 `seller/shop_key.pem` 사용 |


## 실행

먼저 로컬 준비 상태를 확인한다. 이 검사는 Redis 연결이나 모델 응답을 검증하지 않는다.

```bash
python run_market.py --check
```

시장 참여는 다음 명령으로 실행한다. 실제 주문·거래를 수행하므로 기존 런처와 중복 실행하지 않는다. 추가 재고 매입 중지 설정 `.market/restock-paused.json`이 있으면 그대로 적용된다.

```bash
set -o pipefail
python -u run_market.py --market-run round3 -- join \
  --dir . --market https://market.ourolabs.ai \
  --name helenjeon101 --tunnel \
  2>&1 | tee -a "logs/market-$(date +%Y%m%dT%H%M%S).log"
```

`--check`에서 키·의존성·공식 키트 해시 오류가 나오면 해당 원인을 먼저 해결한다. 비정상 종료 후 잠금 오류가 나면 기존 프로세스가 실행 중인지 먼저 확인한다.

웹 배포용 협상 API를 별도로 실행할 때는 다음 명령을 사용한다. 배포 환경에서 `PUBLIC_BASE_URL`을 실제 HTTPS 주소로 설정하고, 기존 서명 키와 `.market` 재고 스냅샷을 제공해야 한다. 같은 상태 디렉터리의 시장 런처와 동시에 실행하지 않는다.

```bash
SELLER_POLICY=seller/turnover.json \
  uvicorn app:app --host 0.0.0.0 --port 9999
```

에이전트 카드: `http://127.0.0.1:9999/.well-known/agent-card.json`. 배포 설정 예시는 [render.yaml](render.yaml)을 참고한다. 현재 구조는 재고 예약을 메모리에 두므로 다중 seller 인스턴스로 확장하기 전에 공유 예약 설계가 필요하다.

## 검증과 로그

```bash
npm run typecheck
npm run test:buyer
python -m unittest test_storage_audit test_storage_env
```

전체 오프라인 회귀 검사는 `python verify_offline.py`로 실행하며 결과를 새 `logs/offline-*.log`에 보존한다. 모델을 사용하는 실제 거래 검증은 별도 실행이다. 현재 체크아웃에는 `scripts/check_week08.py`가 없다.

Redis 앱 로그는 표준 오류와 `logs/redis-app/<세션>-<PID>.jsonl`에 기록한다.

- `storage_selected`, `backend=redis`: Redis 저장소 선택. 연결 성공의 증거는 아님.
- `redis_operation`, `operation=save`, `outcome=success`: 실제 저장 성공.
- `operation=get`, `found=true`: 저장된 상태 조회 성공.
- `outcome=error`: 작업 실패. 예외 종류와 해시된 협상 식별자로 같은 거래의 오류를 대조.

11/17에는 저장·조회 성공 이후 동일 협상의 조회·저장 타임아웃도 기록됐다. 서버 연결 확인, 실제 저장, 재시작 후 복구는 각각 구분해서 검증한다. 로그에는 접속 비밀번호나 협상 원문을 남기지 않는다. 보관·해석 방법은 [REDIS_LOGGING.md](REDIS_LOGGING.md)를 참고한다.
