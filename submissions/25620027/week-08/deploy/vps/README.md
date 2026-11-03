# R3 VPS 전환 준비

**2026-10-30 VPS 설치·Linux 검증 완료, 시장 활성화 전 대기 상태.** 별도 release에 런타임65개와 Docker 이미지를 설치했다. 기존 서비스는 재시작하지 않았다. 이번 개선 소스 `20763f6`로 갱신했고 이전 b619818/d7ed271 release·이미지는 복구용으로 보존했다.

- VPS 경로: `/docker/ax-week08-25620027/releases/20763f685bb9`; `current` symlink가 이 release를 가리킨다.
- Runtime: `ax-week08-25620027:20763f6-prepared`, `ax-week08-25620027:prepared`. QA 이미지는 별도 태그다.
- 기본 이미지/QA 빌드·서버 핵심115검사·기본 기동 검사 통과. 검사 컨테이너는 제거됐고 실제 주문 프로세스는 시작하지 않았다.
- [서버 검증 결과](../../logs/claude-fallback-20261030/vps/result.json). 서버 원본 로그는 release의 `evidence/`에 있다.
- 승인된 Anthropic 키와 모델 설정만 비공개 env(0600·UID10001)에 설치했다. 기존 유료 장부를 보존하고 실제 Haiku 확인1회를 차감했다. 기존 시장 개인키·운영 종료시각·주문 한도는 유지하며 시장 `--activate`는 실행하지 않았다.

아래는 실제 시장 활성화 때 사용할 절차다. 새 R3 계약과 유효한 운영 범위, 기존 신원/기억 이전을 확인한 뒤 적용한다.

## 준비된 구성

- Dockerfile: Python3.12.11, 잠근 kit 의존성, 코드 allowlist와 byte-identical 교수 v3 소스.
- Compose: `r3` profile·기본 활성화0·restart:no·읽기 전용 root·UID10001·자원/로그 상한. 교수 proxy가 loopback에 bind하므로 **Linux host network**를 사용한다. Mac Docker Desktop 구성으로 그대로 가정하지 않는다.
- Launcher: CLI `--activate`와 `AX_R3_ACTIVATE=1`이 함께 필요하다. 명시한 미래 종료시각·남은 주문1~1200건·기존 개인 키 경로·무료 모델 환경 없이는 실행하지 않는다.
- 거래 process: 공식 v3 kit가 기존 seller child와 buyer 주문 실행을 관리한다. 공식 Proxy에 ACP 요청 검사를 덧붙인 `r3.kit_entry`를 사용한다.
- R3 정책은 launcher가 `AX_MARKET_ROUND=3`으로 설정한다. reserve 이상의 제안을 무조건 받는 R2 규칙과 분리하며 R3 카드/광고도 같은 조건으로 제공한다.
- 기록 process: 별도 `r3.recorder`, 모델 호출 없음. `memory.sqlite`에서 snapshot·읽기용MD·검증된 operator 피드백의 전략 profile을 갱신한다.

## 전환 때 확인할 순서

1. 교수의 실제 R3 시작 공지·최종 요건을 확인하고, 기존 W07 동작과 분리한 새 data 디렉터리/이미지를 준비한다. 기존 장부·키·기억을 보존한다.
2. `deploy.vps.bundle.build_context`로 새 build context를 만들고 `MANIFEST.json`과 교수 kit hash를 확인한다. 여기에는 `.env`·키·로그·실행 기억·테스트 자료를 넣지 않는다.
3. Linux Docker에서 이미지를 빌드하고 import/시작 전 검사를 한다. 실제 HTTP 가용성·무료 provider의 tool calling/한도는 별도 실검증 항목이다.
4. 승인된 private 디렉터리에 기존 `shop_key.pem`, `buyer_key.pem`, `model-market.env`, `anthropic-fallback.env`, 새 `runtime.json`을 준비한다. 내용을 출력하지 않는다. 키를 새로 만들어 같은 참가자로 오인하지 않는다.
5. **기존 구매 기억을 이전한다.** 새 journal이 비어 있는지 확인하고 W07 기억의 실제 원본을 보존한 뒤, `Memory.load`로 검증한 기록을 `MemoryJournal.record`로1회 가져온다. 이미 있는 event ID를 재삽입하지 않는다. 기존 형식은 날짜 미상일 수 있으며 정산 증거로 승격하지 않는다. 운영 데이터 이전은 이번에 실행하지 않았다.
6. `runtime.json`에 같은 신원25620027, 실제 public origin, 미래 `stop_at`, 기존 누적 한도에서 차감한 `remaining_orders`를 명시한다. 옛1200건 한도를 새1200건으로 초기화하지 않는다. 기존 종료 승인도 자동으로 연장하지 않는다.
7. 기본 check 실행에서는 네트워크·키 내용 읽기·등록이 없다. 활성화 전에 승인된 터널이 localhost:18088로 연결될 준비가 되었는지 확인한다. DNS/터널을 이번 준비에서 만들거나 바꾸지 않았다.
8. 실제 전환이 승인되면 기존 역할을 중복 등록하지 않도록 정리한 뒤 새 구성을 활성화한다. acp.json/card·서명·실제 settle 성공·기록 process를 확인하고 실패하면 새 구성만 중지해 이전 이미지/데이터/터널 대상으로 복구한다.

로컬 source bundle 생성 예시(제출 폴더에서 실행하며 대상 폴더가 이미 있으면 중단):

```bash
.venv/bin/python -c 'from pathlib import Path; from deploy.vps.bundle import build_context; build_context(Path.cwd(), Path(".omo/evidence/r3-local-20261027/build-context"))'
```

private/data 디렉터리와 키는 실행 UID10001이 접근할 수 있어야 하며 기존 키의0600 권한을 유지한다. 이미0600인 read-only 키를 로드할 때 chmod를 다시 시도하지 않도록 수정하고 임시 키로 검사했다. 실제 시장 서명 개인키는 이번에 읽거나 복사하지 않았다. Anthropic API 키와 혼동하지 않는다.

`runtime.json` 구조(예시 문자열과 null은 의도적으로 활성화할 수 없다):

```json
{
  "market_url": "https://market.ourolabs.ai",
  "public_url": "승인된 HTTPS origin",
  "port": 18088,
  "remaining_orders": null,
  "stop_at": "새로 확인한 종료시각(시간대 포함)",
  "run_directory": "/data/r3",
  "private_directory": "/private"
}
```

## 고정 기억 위치

|경로|용도|
|---|---|
|`/data/r3/memory.sqlite`|최신 가게별 관측·누적 노트, 구매자가 직접 읽음|
|`/data/r3/buyer-memory.json`|기록 담당이 만든 제한된 snapshot|
|`/data/r3/buyer-memory.context.md`|사람이 읽는 노트, 모델 지시문으로 사용하지 않음|
|`/data/r3/meta-profile.json`|운영자 피드백으로 만든 최대3개 전략 프로필|
|`/private/human-feedback.json`|운영자가 넣는 read-only 입력. 실제 event ID/해시·good/bad·허용된 focus만 가능|
|`/data/r3/paid-quota.sqlite3`|구매자·판매자 공용 유료 시도 장부; 실패/취소도 포함·KST 하루5000회|
|`/data/r3/seller.sqlite`|판매자의 task·협상 상태. 기억 캐시 정리로 삭제하지 않음|
|`/data/r3/logs/`|공식 키트 로그. 거래/정산 증거와 context 캐시를 구분|

기록 담당은1초마다 변경을 확인하고 별도로 export한다. 구매자는 export를 기다리지 않는다. SQLite busy 대기는0.05초로 제한하고 저장 장애를 경고한다. 정상 로컬 측정과 실제 VPS의 지연/가용성을 동일시하지 않는다.

이번 준비 구성은 무료 우선·`AX_PAID_FALLBACK=anthropic`·`AX_OPENAI_FALLBACK=0`이다. Compose는 전용 `anthropic-fallback.env`를 읽고 `/data/r3/paid-quota.sqlite3`를 공유한다. 기존 W07 장부를 SQLite backup으로 보존했으며 새 provider로 바꿔도 KST 하루5000회를 초기화하지 않는다. 무료만 쓸 때는 `AX_PAID_FALLBACK=none`을 명시한다. 유효한 새 운영 시한·시장 신원·주문/실제 R3 계약은 활성화 전에 계속 확인한다.


## Round 3 v4 변경 (2026-10-31)

- 키트 pin은 **v4** (`r3/kit_pin.py`, SHA256 `4a0f48b3…`). `verify_kit`·bundle이 이 해시를 요구한다. 레포 루트의 `scripts/market_kit.py`는 upstream d4b4d53 merge로 받았다.
- 런처가 만드는 `agent.json`에 `market_buyer … --order {order}`, `restock --state {state}`, `review --deal {deal}`가 들어간다. 런처는 recorder 외에 **정책 관리자** `python -m r3.manager --dir /data/r3 --market … --shop-url <public_url> --buyer-name 25620027`를 함께 띄운다.
- `runtime.json`: `remaining_orders`는 **0 = 무제한**(0~200,000), `stop_at`은 최대 **16일** 뒤. R3 전체(11/3~11/17 23:59 KST)를 한 구간으로 승인할 때 `stop_at`을 `2026-11-17T23:59:59+09:00`으로 둔다. 종료 승인은 사용자가 한다.
- 환경: fast 경로는 모델 키가 필요 없다. 관리자의 모델 호출은 `AX_R3_MANAGER_MODEL=1`(기본)과 `ANTHROPIC_API_KEY`·`ANTHROPIC_MODEL=claude-haiku-5-5`가 있을 때만 하고, `AX_PAID_QUOTA_PATH`가 있으면 기존 공유 장부에서 차감한다. `OPENROUTER_API_KEY`·`AX_MODEL_PROVIDER=openrouter-free`는 `AX_BUYER_MODE=model` 또는 `AX_SELLER_MODE=legacy`일 때만 요구한다.
- 런처가 넣는 경로: `AX_R3_POLICY=/data/r3/r3-policy.json`, `AX_R3_EVENTS=/data/r3/r3-events.sqlite`, `AX_R3_LEDGER=/data/r3/r3-ledger.sqlite`. 훅·관리자가 `.market/` 옆에 `r3-deals.jsonl`, `r3-forum-state.json`, `r3-restock-state.json`을 쓴다. 모두 기억 캐시 정리 대상이 아니며 로그와 함께 보존한다.
- 전환 전 확인: 11/2에 새 이미지로 라운드2 시장에 join해 `[kit] market_kit 4` 첫 줄, A2A 카드·영수증·결과 전송을 확인한다(라운드2에는 결제가 없다). 11/3 개장 공지와 `agent.md` 해시를 다시 확인한 뒤 `AX_R3_ACTIVATE=1`로 활성화한다. 초기 매입 한도(정책 `restock.seed_budget` 기본 250만)·유보(`reserve_cash` 150만)는 사용자 결정 값이다.

### 2026-10-31 실제 설치·라운드2 join (release 7befff885f7a → a6a64bb10282)

- 설치: `releases/7befff885f7a/incoming/stage_server.py`가 tarball 해시·MANIFEST·키트 v4 해시를 검증하고 `ax-week08-25620027:7befff8-prepared` 이미지를 빌드했다. 네트워크 없는 QA 컨테이너에서 전체 회귀 **421개 PASS**(Node.js 전용 레거시 `test_price_promotion.py`·`test_price_page.py`는 이미지에 Node가 없어 제외), 런타임 프리플라이트 통과. 결과는 `releases/7befff885f7a/evidence/result.json`.
- 비공개 파일: 7주차 `shop_key.pem`·`buyer_key.pem`·`cloudflared.json`을 week-08 `private/`로 복사(0600, uid 10001). `model-market.env`에는 `MARKET_OWNER`와 `AX_R3_MANAGER_MODEL=0`(라운드2 시험 중 모델 호출 없음)·`AX_BUYER_MODE=fast`·`AX_SELLER_MODE=r3`만 둔다. `runtime.json`: `public_url=https://ax-week07.treasuresky.xyz`(7주차 named tunnel 재사용), `port 18088`, `remaining_orders 0`, `stop_at 2026-11-03T23:59+09:00`.
- 터널: [launch-r3.sh](launch-r3.sh)가 `cloudflare/cloudflared` 컨테이너를 host network·uid 10001로 띄우고 `ax-week07.treasuresky.xyz → http://127.0.0.1:18088`로 보낸다(0600 설정 파일을 읽으려면 파일 소유자 uid로 실행해야 한다). [stop-r3.sh](stop-r3.sh)로 거래 컨테이너·터널을 내린다.
- 발견한 결함 두 가지: (1) 키트 `load_key`가 키 파일을 chmod하므로 읽기 전용 `/private`로의 symlink는 실패한다 → 런처가 run 디렉터리에 복사하도록 수정(`7befff8`). (2) cloudflared 기본 사용자는 0600 설정을 못 읽는다 → `--user 10001:10001`.
- 재배포: 바이어 주문 예산 18초·후반 즉시 수락을 넣은 `a6a64bb10282`를 같은 절차로 설치(컨테이너 회귀 422개 PASS)하고 `launch-r3.sh a6a64bb10282`로 교체했다. 첫 11건 중 9건 체결.
- 라운드3 전환 때 바꿀 것: `model-market.env`의 `AX_R3_MANAGER_MODEL=1`, `runtime.json`의 `stop_at=2026-11-17T23:59:59+09:00`(16일 이내), 그리고 `launch-r3.sh a6a64bb10282` 재실행. 키트는 라운드가 3으로 바뀌면 재시작 없이도 재고 판매·restock으로 넘어간다.

### 라운드3 자동 전환 예약 (2026-10-31 등록)

- VPS `/etc/cron.d/ax-week08-r3`(UTC): `5 15 2 11 * root switch-r3.sh a6a64bb10282` → **2026-11-03 00:05 KST**에 [switch-r3.sh](switch-r3.sh)가 `model-market.env`의 `AX_R3_MANAGER_MODEL=1`, `runtime.json`의 `stop_at=2026-11-17T23:59:59+09:00`(창 15.0일)로 바꾸고 `launch-r3.sh`로 재기동한 뒤 cron.d에서 자기 줄을 지운다. 로그 `switch-r3.log`.
- `*/10 * * * * root health-r3.sh a6a64bb10282` → [health-r3.sh](health-r3.sh)가 전환 뒤(`r3-active` 표식) 거래 컨테이너가 내려가 있으면 같은 release로 재기동한다. 키가 같으므로 같은 가게·buyer로 다시 등록된다. 11/17 23:59 KST가 지나면 표식을 지우고 멈춘다. 로그 `health-r3.log`.
- 전환 전(11/3 00:05 이전)에는 현재 라운드2 시험 설정(모델 OFF, 11/3 23:59 종료)이 그대로 돈다. 라운드가 3으로 바뀌면 재시작 없이도 재고 판매·restock으로 넘어간다.

### 2026-10-31 하이브리드 release 573394c8fc50 (네 모델 역할·방어층)

- 설치: 같은 `stage_server.py` 절차(tarball 해시·MANIFEST 80개·키트 v4 해시 검증 → `ax-week08-25620027:573394c-prepared` 빌드 → 네트워크 없는 QA 컨테이너에서 회귀 **438개 PASS** → 프리플라이트). 결과 `releases/573394c8fc50/evidence/result.json`. 번들은 `git archive HEAD`로 추적 파일만 묶어 만들었다(Codex의 미추적 `deploy/observer_hourly/`는 이미지에 들어가지 않음).
- `private/model-market.env`에 추가한 키(값은 적지 않음): `AX_R3_STRATEGY_MODEL`(claude-sonnet-5-5), `AX_R3_TACTICAL_MODEL`(claude-haiku-5-5), `AX_R3_DAILY_USD`(3), `AX_R3_TRADE_MODEL`(1). `ANTHROPIC_MODEL`은 Haiku 고정이며 런처 `validate_model_roles`가 두 역할 모델이 승인 목록(haiku/sonnet/opus 5.5) 안인지, 상한이 0.1~50달러인지 검사한다.
- 역할별 게이트: 느린 루프(정찰 5분·전술 15분·전략 30분+경고; 11/1 사용자 결정)는 `AX_R3_MANAGER_MODEL=1`일 때만, 거래 안 통역은 `AX_R3_TRADE_MODEL=1`일 때만 모델을 부른다. 라운드2 시험 중에는 관리자 OFF·통역 ON으로 실측을 모은다. 11/3 00:05 KST `switch-r3.sh 573394c8fc50`이 관리자를 켠다.
- 모델 호출 기록: `/data/r3/r3-events.sqlite`의 `model_decisions`(역할·모델·지연·토큰·USD·채택·입력 해시)·`model_spend`(KST 일별 USD)·`policy_changes`(누가·어느 층·필드·전후·사유·만료·채택). 상황판은 `/data/r3/r3-picture.json`. 모두 보존 대상.
- 런처가 넣는 경로에 `AX_R3_PICTURE=/data/r3/r3-picture.json`이 추가됐다. 크론은 `/etc/cron.d/ax-week08-r3`의 두 줄을 `573394c8fc50`으로 갱신했다(switch·health).
- 교체: `launch-r3.sh 573394c8fc50`으로 라운드2 시험 컨테이너를 새 이미지로 재기동했다. 같은 키라 같은 가게·buyer로 재등록된다.

### 라운드2 실전 1시간 시험 (`trial-r3.sh`, 2026-11-01 09:00~10:00 KST 예약)

호스트의 [trial-r3.sh](trial-r3.sh)와 [trial_report.py](trial_report.py)(표준 라이브러리만, `$BASE/`에 복사)로 느린 루프를 실제 라운드2 시장에서 1시간 켠다. 예약은 별도 파일 `/etc/cron.d/ax-week08-r3-trial`(UTC `0 0 1 11 *` start, `0 1 1 11 *` stop)이며 11/3 전환 파일 `/etc/cron.d/ax-week08-r3`는 건드리지 않는다. `stop`이 시험 파일을 스스로 지운다. 로그 `trial-r3.log`.

1. `start` — 정책표·상황판을 `trial-<UTC>/`에 백업하고, 세 주기의 마지막 실행 시각을 0으로 돌려 첫 tick에 정찰·전술·전략이 모두 돌게 한 뒤 `AX_R3_MANAGER_MODEL=1`로 재기동한다.
2. `report` — 시작 이후 모델 호출(역할·모델·건수·지연·비용), 오늘 지출, 정책 변경(채택/거부·사유), 정책 버전·전술 오버레이·띠, 상황판(신뢰 낮은 가게·핫 카테고리·경고), 키트 로그 집계(주문·verified·읽기·스크린)를 JSON으로 찍는다.
3. `stop` — 보고서를 백업 폴더 `report.json`에 저장하고 `AX_R3_MANAGER_MODEL=0`으로 되돌린 뒤 **정책표·상황판을 시험 전 상태로 복원**하고 재기동한다(라운드2 수치로 조정된 정책이 라운드3에 새지 않게). `model_decisions`·`policy_changes` 행은 증거로 남는다.

라운드2에는 재고·결제가 없어 전략·전술 변경의 내용은 참고용이고, 확인 대상은 세 역할의 호출 성공·지연·비용·거부 사유와 통역의 실제 적중이다.

### 2026-11-02 텔레그램 알림(헤르메스 경유)과 전환 경로 결함 2건 수정

- **알림**: 호스트의 [notify_r3.py](notify_r3.py)(표준 라이브러리만)가 `/etc/cron.d/ax-week08-r3-notify`로 5분마다 돈다. 거래 컨테이너·키트 로그·복구 로그·전환 설정(두 키만 읽음)·이벤트 장부·원장 미러·공개 보드를 읽고, 같은 VPS의 헤르메스 컨테이너에서 `hermes send -t telegram`으로 홈 채널에 보낸다(게이트웨이의 기존 자격증명 재사용, 토큰을 다루지 않음). 즉시 알림: 컨테이너 중단, 키트 로그 15분 정지, 1시간 재기동 3회+, 00:05 전환 미확인, 결제·크레딧 거절 휴지, 일일 비용 상한, 라운드3 중 1시간 무정산. 같은 경고는 2시간마다만 반복, 해소 시 회복 한 줄. 한 번만: 라운드3 개장, 전환 완료, 종료. 매일 09:00 KST 요약(순위·이익·매출·별점·잉여·정산·잔고·모델 호출·비용). 거래 실행은 건드리지 않으며 11/17 23:59 KST 한 시간 뒤 스스로 멈춘다. 상태 `notify-state.json`, 로그 `notify-r3.log`.
- **결함 1, 전환이 실패할 뻔함**: 호스트의 매일 `docker image prune -af --filter until=24h`(09:00~15:00 KST 무작위)가 사용 중인 이미지의 release 태그를 지웠고, [launch-r3.sh](launch-r3.sh)는 그 태그가 없으면 compose 전에 멈춘다. 11/3 00:05 `switch-r3.sh`가 설정만 바꾸고 재기동에 실패해 컨테이너가 옛 종료 시각(11/3 23:59)으로 돌았을 것이다. 태그를 release 기록의 이미지 ID로 되살렸고, launch-r3.sh가 앞으로 같은 방식으로 스스로 재태깅한다.
- **결함 2, 복구 오탐**: [health-r3.sh](health-r3.sh)의 `docker top | grep r3.manager`가 데몬 오류로 가끔 빈 결과를 내 11/1~2에 "관리자 없음" 재기동을 31번 기록했다(관리자는 시작 때부터 같은 PID로 정상, 컨테이너 재시작 0). 그 재기동은 실행 중 컨테이너에 무효라 진짜 관리자 사망도 못 고쳤다. 컨테이너 안 `/proc`을 두 번(20초 간격) 확인하고, 진짜 없을 때만 `docker restart`로 제자리 재시작하도록 바꿨다.

### 2026-11-02 자문 보고를 Obsidian에 기록 (헤르메스 작업 큐 → Mac 워커)

- `notify_r3.py --advisor HH`가 보고를 텔레그램으로 보낼 때 같은 내용을 `/srv/hermes-jobs/job_…_MAC_WORKER_APPROVED_OBSIDIAN_AX_R3_….md`(헤르메스 `JARVIS_JOB_REQUEST_CONTRACT.md`의 Worker-Owned Obsidian Lane 형식: `append_after_heading`, 대상 1개, `idempotency_key`)로도 넣는다. Mac의 `com.sungjinho.hermes-mac-local-worker-obsidian-update`(5분)가 볼트 노트 `20_학업/01_강의자료/AX이해와활용/261102_AX_R3_자문보고.md`의 `## 자문 보고` 아래에 붙인다.
- 승인 건을 반영한 뒤에는 `python3 notify_r3.py --vault-change "<번호·바꾼 것·release·커밋>"`으로 같은 노트의 `## 반영 기록`에 한 줄을 남긴다(VPS `data/r3/r3-advisor-changes.md`와 함께).
- 2026-11-02 21:38 시험: 작업 생성 → Mac 워커 반영, VPS 상태 `DONE`(“Jarvis apply block applied successfully”). 주의: Mac의 가져오기 작업(30분)이 워커(5분)보다 먼저 돌면 작업 파일을 `_pulled/`로 옮겨 워커가 못 본다. 평소 주기에서는 워커가 먼저 처리한다.

### 2026-11-03 00:01 KST 시계 전환 취소 → 라운드3 감지 전환

- 공지는 "11월 3일(화) 시작"만 있고 시각이 없다. 00:05 시계 전환은 관리자(정찰·전술·전략)를 라운드2 수치로 켜게 되므로 사용자 결정으로 취소했다(`/etc/cron.d/ax-week08-r3`에서 switch 줄 삭제, 백업 `ax-week08-r3.cron.bak-20261103`).
- 대신 [arm-r3.sh](arm-r3.sh)가 `/etc/cron.d/ax-week08-r3-arm`(사용자 승인)으로 2분마다 공개 보드의 `round`를 보고, 3이 되면 `switch-r3.sh`(관리자 켜기·종료 11/17 23:59·재기동)를 실행하고 `r3-switched` 표식을 남긴다. 11/3 22:00 KST까지 열리지 않으면 종료 시각만 늘린다(관리자는 계속 꺼짐). 로그 `arm-r3.log`.
- 감시 스크립트는 라운드3가 열린 뒤 10분 넘게 전환이 안 되면, 또는 22:30에도 종료 시각이 11/3이면 경고한다.

### 2026-11-03 하네스 전수점검 후속판 — 로컬 준비, 미배포

- 새 서비스 이미지에는 정책 버전 복기(`r3/reflection.py`), 최신 기억 조회, 전달 확인을 기다리는 자문, 사용량 불명 시 모델 차단이 포함된다. 복기는 전술·전략·자문 입력에 들어가며 코드를 자동 수정하거나 배포하지 않는다.
- **서비스 이미지와 host `notify_r3.py`를 같은 판으로 맞춰야 한다.** 새 자문은 `proposal_ids`를 출력하고, host는 Telegram 성공 후 `python -m r3.advisor --ack ID,...`로 전달만 확인한다. 구판 host를 그대로 두고 이미지 하나만 바꾸면 새 재전달/확인 경로를 검증할 수 없다.
- host의 `advisor-outbox.json`은 미전달 요청을 보관한다. 기존5분 watcher가 같은 메시지를 모델 재호출 없이 재시도한다. 성공 후 ack까지 확인되면 삭제하며, 전송 직후 장애 때는 같은 ID가 다시 올 수 있다. `--ack`는 승인·적용이 아니며 사용자 판정은 기존`--decide`로 별도 기록한다.
- `--advisor HH --dry-run`은 컨테이너·유료 모델을 실행하지 않는다. 운영자가 먼저 형식/로컬 검사를 검토하고, 실제 배포 뒤에는 모델을 새로 호출하는 dry-run 대신 보존된 장부와 로그로 연결을 확인한다.
- 전환·복구·알림 크론, 비용/주문 한도·키·운영 종료는 이번 로컬 검수에서 변경하지 않았다. 실제 서버 확인판은`11f627c9dbb8`, 시장은R2였다. 신규판의 VPS/Linux QA와 실제 R3 피드백 순환은 배포 후 확인 항목이다.
