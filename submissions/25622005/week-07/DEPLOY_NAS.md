# Week 7 NAS 수동 배포

판매 에이전트, 구매 에이전트, 수업의 마켓 키트, Cloudflare Quick Tunnel을
`market` 컨테이너 하나에서 실행한다. 기존 `market_runtime.py`가 터널과 키트를
감시하고, 터널 주소가 바뀌면 같은 이름·키로 마켓에 다시 등록한다.
판매 판단은 Python 규칙이며 일반 구매 협상에는 Claude Code의 `haiku`를 사용한다.

```text
다른 구매자 → 새 trycloudflare.com 주소 → NAS 컨테이너의 키트 프록시 → 판매자
마켓 → 키트의 주문 조회 → 구매자 프로세스 → 다른 판매자 → 결과를 마켓에 제출
```

## 준비 조건과 파일

- Docker Engine과 Docker Compose V2 이상. Linux AMD64 또는 ARM64 NAS를 대상으로 한다.
- NAS가 인터넷의 마켓·Claude·Cloudflare에 접속할 수 있어야 한다.
- Cloudflare 계정·개인 도메인·NAS의 인바운드 포트 공개는 필요하지 않다.
- Claude Code를 사용할 계정과 NAS에서의 별도 로그인이 필요하다.
- 기존 `shop_key.pem`, `buyer_key.pem`을 가져와 같은 마켓 신원을 유지한다.

NAS에는 이 저장소의 디렉터리 구조를 유지해야 한다. 빌드 컨텍스트가 저장소
루트이며 수업의 `scripts/market_kit.py`도 이미지에 포함하기 때문이다.
이번 Docker 관련 변경은 커밋하지 않았으므로 `git pull`만으로 전달되지 않는다.
`Dockerfile`, `Dockerfile.dockerignore`, `compose.yaml`, `docker/` 및 최신 Week 7
소스 파일을 NAS의 같은 경로에 직접 복사한다.

모든 아래 NAS 명령은 다음 디렉터리에서 실행한다.

```sh
cd /NAS에_복사한_저장소/submissions/25622005/week-07
```

현재 Dockerfile의 Claude Code는 N40L 호환을 위해 **2.1.112의 Node.js CLI**로
고정했다. 이 값은 모델 버전이 아니라 CLI 버전이며 모델 설정은 `haiku`다.
자세한 원인과 새 AMD64 이미지 빌드 절차는 아래 N40L 항목을 따른다.

이미지에는 소스와 실행 도구만 들어간다. Dockerfile 전용 제외 목록은
키·claim code·Claude 인증·거래 기록·로그·다른 학생의 제출물을 빌드에서 제외한다.

| NAS 호스트 경로 | 컨테이너 경로 | 보존 내용 |
|---|---|---|
| `.market/nas-data/` | `/data` | 서명키, claim code, 거래 기억, 로그, 런타임 상태 |
| `.market/nas-claude/` | `/home/node` | NAS에서 로그인한 Claude Code 인증과 사용자 설정 |

시작할 때 이미지의 Python 소스와 컨테이너용 `agent.json`을 `/data`에 갱신한다.
기존 키·기억·로그는 덮어쓰지 않는다. `/data`에서 코드를 직접 수정하는 대신
저장소 소스를 수정하고 이미지를 다시 빌드한다.
첫 진입 시 두 전용 마운트의 소유권을 지정한 UID/GID(기본 1000)로 맞춘 뒤,
실제 런타임과 Claude Code는 `node` 사용자로 실행한다.
일반 NAS 공유 폴더 전체를 이 두 경로에 마운트하지 않는다.

## 1. 이미지 빌드와 Claude 로그인

NAS의 일반 사용자로 실행한다. NAS 사용자 번호가 1000이 아니라면 아래 두 값을
현재 사용자에 맞춰 둔다. 이후 새 SSH 세션에서도 같은 값을 설정한다.
항상 적용하려면 이 두 설정만 로컬 `.env`에 보관해도 된다. `.env`는 Git 제외 대상이다.

```sh
export NAS_UID="$(id -u)"
export NAS_GID="$(id -g)"
umask 077
mkdir -p .market/nas-data/.market .market/nas-data/logs .market/nas-claude
docker compose config --quiet
docker compose build market
docker compose run --rm -it market claude
docker compose run --rm market claude auth status
```

테마·계정 선택 후 터미널에서 안내한 URL을 PC 브라우저에서 열어 인증한다.
이미 Claude 대화 화면이면 `/login`으로 인증 화면을 연다. 브라우저에서 코드를
발급받으면 **같은 실행 세션**의 `Paste code here if prompted >` 입력창에
코드 전체를 붙여 넣고 Enter를 누른다. 입력은 별표로 보일 수 있다.
쉘의 `$` 프롬프트가 돌아올 때까지 기다리는 절차가 아니다.
성공 후 Claude를 종료하고 위 `claude auth status` 명령으로 상태를 확인한다.

호환용 CLI 2.1.112의 `claude auth login`은 수동 코드 입력 처리가 연결돼 있지
않아, NAS·컨테이너에서 URL만 표시하고 자동 콜백을 기다릴 수 있다.
이 경우 Ctrl+C로 중단하고 위 대화형 명령으로 시작한다. 이전 실행에서 발급받은
코드는 재사용하지 않고 새 화면의 URL로 다시 발급받는다.
이 절차는 설치된 CLI의 대화형 입력 기능을 이용하므로 이미지를 다시 빌드할 필요는 없다.

인증 결과는 컨테이너의 `/home/node/.claude/.credentials.json`에 자동 저장되고
호스트의 `.market/nas-claude/.claude/.credentials.json`으로 유지된다.
코드를 이 파일이나 `.env`에 직접 기록하지 않는다.
Mac의 인증 파일을 이미지에 넣거나 Mac 키체인을 복사할 필요가 없다.
`run`의 로그인·검사 명령은 마켓에 등록하거나 주문을 요청하지 않는다.
브라우저 콜백이 도달하지 않는 SSH·컨테이너 환경의 수동 입력 절차는
[공식 인증 안내](https://code.claude.com/docs/en/authentication#log-in-to-claude-code)도 참고한다.

Claude 계정에서 모델 호출까지 확인하려면 다음을 수동 실행할 수 있다.
이는 실제 모델 사용량이 발생하는 별도 확인이다.

```sh
docker compose run --rm market claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence 'Reply with OK only.'
```

## 2. Mac에서 NAS로 운영 상태 이전

이미지 빌드와 NAS의 Claude 로그인은 기존 Mac 운영 중에도 준비할 수 있다.
최종 상태 복사 직전에는 Mac의 실행을 중지한다. 같은 이름·키의 Mac과 NAS를
동시에 운영하면 등록 주소가 서로 바뀌거나 주문·기억이 나뉠 수 있다.

Mac의 Week 7 디렉터리에서, PID의 명령이 본인의 `market_runtime.py`인지 확인한다.

```sh
ps -p "$(cat .market/runtime.pid)" -o pid=,command=
```

확인 후 해당 감독 프로세스를 정상 종료한다.

```sh
kill -TERM "$(cat .market/runtime.pid)"
```

이후 SSH/SCP 등으로 다음 파일을 **NAS의 지정 위치에 별도로** 복사한다.
키와 인증은 저장소나 이미지에 포함하지 않는다.

| Mac의 Week 7 기준 원본 | NAS의 Week 7 기준 목적지 |
|---|---|
| `shop_key.pem` | `.market/nas-data/shop_key.pem` |
| `buyer_key.pem` | `.market/nas-data/buyer_key.pem` |
| `buyer-memory.json` | `.market/nas-data/buyer-memory.json` |
| `seller-memory.jsonl` | `.market/nas-data/seller-memory.jsonl` |
| `.market/market-owner` | `.market/nas-data/.market/market-owner` |
| `logs/market-kit.log` | `.market/nas-data/logs/market-kit.log` |

그 밖의 원본 운영 로그도 필요하면 같은 `logs/` 목적지에 함께 보존한다.
Mac의 `.market/*.pid`, 다운로드한 Mac용 실행 파일, 오래된 터널 설정은 복사하지 않는다.
키가 없으면 컨테이너는 새 키를 자동 생성하지 않고 중지한다.

NAS에서 파일 권한을 제한한다.

```sh
chmod 600 .market/nas-data/shop_key.pem .market/nas-data/buyer_key.pem
chmod 600 .market/nas-data/.market/market-owner
```

claim code를 복사하지 않았다면 아래 명령으로 숨김 입력한다. 실제 코드를
Compose YAML, 명령행 인수, 문서에 쓰지 않는다.

```sh
docker compose run --rm market python configure_market_owner.py
```

## 3. NAS에서 시작하고 마켓 확인

```sh
docker compose up -d market
docker compose ps
docker compose logs -f --tail 100 market
```

`[runtime] public URL changed to https://...trycloudflare.com`은 새 NAS 주소다.
기존 Week 6 터널 주소를 재사용하는 구성은 아니다. 새 주소와 기존 `psyoung82`
이름·서명키로 자동 등록하므로 별도의 웹 등록 버튼은 필요하지 않다.

상세 주문·등록·검증 로그는 호스트의 `.market/nas-data/logs/`에 남는다.

```sh
docker compose exec --user node market tail -f /data/logs/market-runtime-kit.log
```

다음을 확인한다.

1. `shop registered as psyoung82`, `buyer registered as psyoung82` 로그.
2. 새 공개 주소의 `/.well-known/agent-card.json`이 버전 `1.11.0`으로 응답.
3. `/.well-known/quality-guarantee.json`에 가격 105·185·270과 서명 공개키 표시.
4. 마켓에서 양쪽 온라인, 등록 GitHub 계정이 본인인지 확인.
5. 실제 배정 주문 후 `[market] ledger #... verified True`와 구매 거래 증가 확인.

Compose의 `healthy`는 로컬 감독·키트 프로세스와 판매 카드의 응답 확인이다.
외부 터널이나 마켓 인증의 정상 여부까지 증명하지 않는다. Docker는 `unhealthy`
표시만으로 컨테이너를 재시작하지 않는다. 프로세스 종료는 `restart: unless-stopped`,
터널·키트 장애는 기존 Python 감독 프로그램이 처리한다.

## 조회·갱신·로그 제출

시간당 이익·절약액은 다음으로 확인한다. 로컬 기억의 계산이며 시장의 최종 집계와 다를 수 있다.

```sh
docker compose exec --user node market python strategy_metrics.py --dir /data --hours 1
```

모델이나 마켓을 호출하지 않는 회귀 검사는 서비스를 시작하기 전에 실행할 수 있다.

```sh
docker compose run --rm market python -m unittest discover -s /data/tests -p 'test_*.py'
```

소스 변경 후에는 다시 빌드하고 컨테이너를 교체한다. 같은 마운트의 신원과 기록은 유지된다.

```sh
docker compose build market
docker compose up -d market
```

정지·재개는 다음과 같다.

```sh
docker compose stop market
docker compose start market
```

NAS가 재부팅되면 Docker 서비스가 자동 시작되도록 NAS에서도 설정한다.
로그는 호스트의 `.market/nas-data/logs/market-kit.log`에 계속 쌓인다.
제출 시에는 NAS 서비스를 정지한 뒤 이 파일을 제출 경로의
`logs/market-kit.log`로 복사하고, 기존 로그는 편집하지 않는다.
키·claim code·`.market/nas-claude/`는 제출하지 않는다.

Cloudflare Quick Tunnel은 주소가 바뀌고 가동률 보장이 없다. 감독 프로그램이
재등록을 시도하지만 인터넷 단절이나 인증 만료까지 해결하지는 못한다.
근거: [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
Claude 설치·인증은 [공식 설치 안내](https://code.claude.com/docs/en/setup)를 참고한다.

이 구성은 수동 배포 준비물이다. 작성 과정에서 NAS 배포, 기존 Mac 서비스 정지,
마켓 등록 전환은 수행하지 않는다. 컨테이너용 실행 경로가 기존 Mac의 `agent.json`과
다르므로 과거 `test-report.json`을 NAS 경로의 공식 전체 테스트 증거로 취급하지 않는다.

## AMD Turion II Neo N40L: Illegal instruction 대응

사용자가 제공한 N40L의 CPU flags에는 `avx`, `avx2`, `sse4_1`, `sse4_2`가 없다.
`sse4a`는 별도의 AMD 기능이며 SSE4.2를 의미하지 않는다. 처음 준비한
Claude Code 2.1.263은 npm 설치여도 내부의 네이티브 실행 파일을 사용한다.
이 CPU에서 `Illegal instruction (core dumped)`가 발생하는 경우 로그인보다
실행 파일의 CPU 호환성을 먼저 해결해야 한다. `-it`나 UID 변경으로 CPU의
명령어 지원이 추가되지는 않는다.

수정한 Dockerfile은 공식 npm 패키지의 2.1.112를 설치하고 `cli.js`의 존재를
빌드 중 검사한다. `claude`는 이 JavaScript 파일을 Node.js로 실행한다.
선택적 네이티브 이미지 처리 모듈은 텍스트 협상에 필요하지 않아 설치에서 제외한다.
자동 업데이트도 계속 꺼 두어 네이티브 CLI로 바뀌지 않게 한다.
이 호환 조치는 오래된 CLI에 의존하므로 실제 NAS의 로그인과 Haiku 호출은
사용자가 따로 확인해야 한다. 서버가 구버전 클라이언트를 거부하면 이 방법으로
운영을 완료할 수 없으며, 지원되는 머신에서 구매 하네스를 실행하는 등의 재구성이 필요하다.

근거:
[공식 npm 설치 문서](https://code.claude.com/docs/en/setup#install-with-npm),
[공식 저장소의 비 AVX CPU 장애 보고와 2.1.112 우회 사례](https://github.com/anthropics/claude-code/issues/51976).

수정한 파일을 사용해 **저장소 루트에서** 새 태그로 AMD64 이미지를 빌드한다.
루트에 별도로 복사한 예전 Dockerfile을 사용하지 않도록 `-f` 경로를 지정한다.

```sh
docker buildx build --platform linux/amd64 --load \
  -f submissions/25622005/week-07/Dockerfile \
  --build-arg CLAUDE_CODE_VERSION=2.1.112 \
  -t kwimp/ai-agent-engineering-101:week07-agent-n40l .
```

사용자가 Docker Hub로 전달할 경우 직접 게시한다.

```sh
docker push kwimp/ai-agent-engineering-101:week07-agent-n40l
```

NAS의 이미지 전용 Compose에서는 서비스의 이미지와 플랫폼을 다음처럼 맞춘다.
기존 볼륨·환경·healthcheck 설정은 계속 사용한다.

```yaml
services:
  market:
    image: kwimp/ai-agent-engineering-101:week07-agent-n40l
    platform: linux/amd64
```

새 이미지가 실제로 들어왔는지 확인한 뒤 로그인한다.

```sh
docker compose pull market
docker compose run --rm market node --version
docker compose run --rm market claude --version
docker compose run --rm -it market claude
docker compose run --rm market claude auth status
```

`claude --version`은 `2.1.112`를 출력해야 한다. 로그인 후에는 1절의 Haiku
한 줄 응답 검사를 수동 실행하고, 성공하면 `docker compose up -d market`으로 시작한다.
로그인과 모델 요청 검증 없이 CPU 호환 수정만으로 NAS 배포가 완료됐다고 판단하지 않는다.

## 작성 시 검증 결과

### 컨테이너 시작 시 `IndexError: 3` 수정

처음 배포 이미지에는 `/data/market_runtime.py`에서
`Path(__file__).resolve().parents[3]`을 바로 읽는 오류가 있었다.
컨테이너 경로에는 그 깊이의 부모가 없으며, `--kit`을 명시해도 argparse의
기본값 식이 먼저 계산돼 실행 전에 종료됐다. 앞선 판매·CLI 검사는 실제 감독
프로그램의 시작 경로를 실행하지 않아 이 오류를 발견하지 못했다.

수정본은 인수 해석 뒤에만 기본 경로를 탐색하고, 명시한 `--kit`을 우선한다.
저장소의 상위 경로에서 키트를 찾거나 컨테이너의 `/opt/market/market_kit.py`를
사용하며, 파일이 없으면 명확한 인수 오류를 반환한다.

NAS에 이미지를 전달할 때는 저장소 루트에서 수정한 Dockerfile 경로를 지정한다.

```sh
docker buildx build --platform linux/amd64 \
  -f submissions/25622005/week-07/Dockerfile \
  -t kwimp/ai-agent-engineering-101:week07-agent3 --push .
```

NAS Compose의 `image`를 `kwimp/ai-agent-engineering-101:week07-agent3`으로 바꾼다.
두 볼륨 경로를 유지하고 실행한다.

```sh
docker compose pull market
docker compose run --rm market python /data/market_runtime.py --help
docker compose up -d --force-recreate market
docker compose logs -f --tail 100 market
```

`--help` 단계는 로그인이나 마켓 등록 없이 기존 충돌 지점을 확인한다.
같은 Claude 인증 볼륨을 사용하면 이 코드 수정 때문에 재로그인할 필요는 없다.
`/data`의 소스는 시작 시 이미지에서 갱신되므로 `/data/market_runtime.py`만
직접 수정한 상태로 재시작하면 이전 이미지의 파일로 덮어써진다.

검증은 `logs/container-startup-20261024-1/`에 보존했다. 기존 이미지에서 실패를
재현하고 수정한 AMD64 이미지의 경로 회귀 검사 포함 69개를 통과했다.
추가로 실제 런처에서 감독 프로그램으로 넘어가 cloudflared를 시작하고
SIGTERM으로 정상 종료하는 경로를 확인했다. 이 검사는 네트워크를 차단하고
로그인 상태 조회만 성공 픽스처로 대체했으며 합성 키·claim을 사용했다.
따라서 실제 OAuth·마켓 등록·모델 요청의 완료를 의미하지 않는다.

2026-10-24, 로컬 Docker의 Linux ARM64 이미지에서 다음을 확인했다.
AMD64 NAS에서는 해당 NAS에서 빌드·실행을 확인해야 한다.

- Compose 설정 검사와 실제 Docker 이미지 빌드 성공.
- 최종 컨테이너의 기존 회귀 검사 64개 성공.
- 네트워크를 끈 임시 컨테이너에서 세 상품의 실제 로컬 A2A 거래,
  공개 가격·서명키 일치, 서명 검증 및 변조 거부 확인.
- 소스 갱신 시 키·기억·claim code·기존 로그 보존 확인.
- UID 1026/GID 100으로 컨테이너를 교체해도 데이터·소유권 보존 확인.
- 기존 키가 없으면 마켓 시작을 거부하며, UID 0으로 워크로드를 실행하지 않음.
- 공식 제출 구조 검사 성공. 여기서 확인하는 과거 공식 25회 테스트는
  NAS용 명령 경로로 새로 실행한 전체 모델 테스트가 아니다.

원본 증거는 `logs/docker-nas-20261024-1/`에 있다. 최초 테스트의 읽기 전용 경로 오류와,
CA 인증서까지 개인키로 오인한 검증 코드의 실패도 보존했다. 수정 후 결과는
`container-unit-tests-final.log`, `container-integration-final.log`,
`ownership-and-restart.json`에 남겼다. 실제 마켓 등록이나 모델 요청은
이 컨테이너 검증에서 수행하지 않았다. 변경 파일은 커밋하지 않았다.

N40L 정보 수신 후에는 수정한 `week07-agent:n40l`의 Linux AMD64 이미지로
회귀 검사 64개와 세 상품의 실제 로컬 A2A·서명 검사를 다시 통과했다.
별도의 `qemu-x86_64 -cpu phenom` 검증에서도 Node.js 기반 CLI의 버전 출력과
`auth status --help`가 정상 실행됐고, 현재 하네스 옵션이 제공됨을 확인했다.
SIGILL을 로그인 미완료로 오해하지 않도록 시작 오류도 구분했다.
근거는 `logs/nas-cpu-20261024-1/validation.json`과 같은 폴더의 원본 로그다.
이 에뮬레이션은 실제 NAS의 모든 하드웨어·운영체제 조건을 재현하지 않는다.
실제 NAS 접근, OAuth 로그인, 모델 호출, Docker Hub 게시, 마켓 전환은 수행하지 않았다.
