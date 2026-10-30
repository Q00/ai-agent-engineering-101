# R3 VPS 전환 준비

**2026-10-30 VPS 설치·Linux 검증 완료, 시장 활성화 전 대기 상태.** 별도 release에 런타임64개와 Docker 이미지를 설치했다. 기존 서비스는 재시작하지 않았다. 이번 개선 소스 `b619818`로 갱신했고 이전 d7ed271 release·이미지는 복구용으로 보존했다.

- VPS 경로: `/docker/ax-week08-25620027/releases/b619818b258f`; `current` symlink가 이 release를 가리킨다.
- Runtime: `ax-week08-25620027:b619818-prepared`, `ax-week08-25620027:prepared`. QA 이미지는 별도 태그다.
- 기본 이미지/QA 빌드·서버 핵심79검사·기본 기동 검사 통과. 검사 컨테이너는 제거됐고 실제 주문 프로세스는 시작하지 않았다.
- [서버 검증 결과](../../logs/reliability-20261030/vps/result.json). 서버 원본 로그는 release의 `evidence/`에 있다.
- API 키·개인키·환경 파일은 전송하지 않았다. 기존 승인 종료시각과 주문 한도를 임의로 새로 잡지 않았다. `/private`를 마운트하거나 `--activate`를 실행하지 않았다.

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
4. 승인된 private 디렉터리에 기존 `shop_key.pem`, `buyer_key.pem`, `model-market.env`, 새 `runtime.json`을 준비한다. 내용을 출력하지 않는다. 키를 새로 만들어 같은 참가자로 오인하지 않는다.
5. **기존 구매 기억을 이전한다.** 새 journal이 비어 있는지 확인하고 W07 기억의 실제 원본을 보존한 뒤, `Memory.load`로 검증한 기록을 `MemoryJournal.record`로1회 가져온다. 이미 있는 event ID를 재삽입하지 않는다. 기존 형식은 날짜 미상일 수 있으며 정산 증거로 승격하지 않는다. 운영 데이터 이전은 이번에 실행하지 않았다.
6. `runtime.json`에 같은 신원25620027, 실제 public origin, 미래 `stop_at`, 기존 누적 한도에서 차감한 `remaining_orders`를 명시한다. 옛1200건 한도를 새1200건으로 초기화하지 않는다. 기존 종료 승인도 자동으로 연장하지 않는다.
7. 기본 check 실행에서는 네트워크·키 내용 읽기·등록이 없다. 활성화 전에 승인된 터널이 localhost:18088로 연결될 준비가 되었는지 확인한다. DNS/터널을 이번 준비에서 만들거나 바꾸지 않았다.
8. 실제 전환이 승인되면 기존 역할을 중복 등록하지 않도록 정리한 뒤 새 구성을 활성화한다. acp.json/card·서명·실제 settle 성공·기록 process를 확인하고 실패하면 새 구성만 중지해 이전 이미지/데이터/터널 대상으로 복구한다.

로컬 source bundle 생성 예시(제출 폴더에서 실행하며 대상 폴더가 이미 있으면 중단):

```bash
.venv/bin/python -c 'from pathlib import Path; from deploy.vps.bundle import build_context; build_context(Path.cwd(), Path(".omo/evidence/r3-local-20261027/build-context"))'
```

private/data 디렉터리와 키는 실행 UID10001이 접근할 수 있어야 하며 기존 키의0600 권한을 유지한다. 이미0600인 read-only 키를 로드할 때 chmod를 다시 시도하지 않도록 수정하고 임시 키로 검사했다. 실제 운영 키는 이번에 읽거나 복사하지 않았다.

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
|`/data/r3/seller.sqlite`|판매자의 task·협상 상태. 기억 캐시 정리로 삭제하지 않음|
|`/data/r3/logs/`|공식 키트 로그. 거래/정산 증거와 context 캐시를 구분|

기록 담당은1초마다 변경을 확인하고 별도로 export한다. 구매자는 export를 기다리지 않는다. SQLite busy 대기는0.05초로 제한하고 저장 장애를 경고한다. 정상 로컬 측정과 실제 VPS의 지연/가용성을 동일시하지 않는다.

이번 준비 구성은 `openrouter-free`·유료 fallback OFF다. 기존 VPS의 유료 대체 설정은 변경하지 않았다. 유료 대체를 유지하는 실제 R3 전환은 지원 모델·기존 공유 일별 장부와 한도를 함께 확인해야 한다. 새 이미지가 별도 유료 한도를 만들지 않는다.
