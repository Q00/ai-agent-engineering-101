# HW 상시 시장: 현재 PC 운영

사용자는 추가 유료 서버를 만들지 않고 현재 Windows PC를 켜 두기로 선택했다. OCI와 C-SERVER는 이번 과업에서 제외한다. 이 운영 구성에 서버 구독은 없으며 모델 사용량과 PC 전력 사용은 별개다.

## 실제 실행 위치

seller, buyer, 공식 시장 키트, cloudflared 모두 현재 PC에서 실행된다. Cloudflare Quick Tunnel은 PC의 서버를 공개 URL로 연결한다. PC가 종료되거나 절전·최대절전하면 운영이 중단된다. 화면만 끄는 것은 가능하다. 전원과 인터넷을 유지하고 노트북이라면 덮개를 닫을 때 절전되는 설정에 유의한다.

현재 실행 정보는 `.market/process.json`에 있다. 문서에 적힌 과거 PID·URL 대신 이 파일과 시장의 최신 등록을 확인한다. `run_market.py`가 Windows 자동 절전 방지를 요청하며 전역 전원 설정을 바꾸지는 않는다. 수동 종료나 강제 절전을 차단하는 기능은 아니다.

## 상태 점검

저장소 루트의 PowerShell에서 실행한다.

```powershell
& ./submissions/24522014/week-07/.venv/Scripts/python.exe -X utf8 ./submissions/24522014/week-07/check_always_on.py
```

이 명령은 모델 호출이나 재시작 없이 로컬 키트 프로세스의 명령행, 공개 카드, GitHub 소유권, R2 가게·buyer 활성 상태와 유효 구매 1건 이상을 확인한다. 스냅샷을 매번 새로운 `logs/hw-always-on/<UTC시각>/`에 저장한다. 제한된 실행 환경에서 타 프로세스 명령행을 읽지 못하면 로컬 확인만 실패할 수 있으므로 일반 사용자 터미널에서 다시 확인한다. 확인 시점의 상태이며 기한까지의 연속 가동 보장은 아니다.

## 중단 시 복구

먼저 기존 키트가 종료됐는지 확인한다. 동일 이름으로 키트를 두 개 동시에 실행하지 않는다. `shop_key.pem`, `buyer_key.pem`, `.market/buyer-memory.jsonl`을 보존한다. 기존 키를 지우거나 새로 생성하지 않는다. 시장 owner claim code는 기존처럼 프로세스 환경변수 `MARKET_OWNER`로 제공하며 코드·문서·로그에 기록하지 않는다.

```powershell
& ./submissions/24522014/week-07/.venv/Scripts/python.exe -X utf8 -u ./submissions/24522014/week-07/run_market.py --tunnel
```

공식 키트가 같은 이름과 키로 재등록한다. Quick Tunnel URL은 재시작 때 바뀔 수 있다. `shop registered as kcwarni`, `buyer registered as kcwarni`, 이후 `verified True`인 실제 결과를 확인한다. 키트가 `logs/market-kit.log`에 원본 운영 로그를 추가한다. 현재 구성은 재부팅 후 자동 시작이나 장애 시 자동 재시작을 설정하지 않았다.

## 제출 상태

공식 거래 테스트는 기존 25/25 PASS이며 이후 변경한 이벤트 안내는 로컬 프로토콜 검사로 확인했다. 현재 PC에서의 운영 증거와 제출 구조 검사를 추가한다. 챌린지 2의 실제 부정행위 확인과 회피 증명은 아직 미완료이며 구조 검사 통과만으로 과제 전체가 완료되었다고 보지 않는다.
