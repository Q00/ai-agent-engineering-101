# 주말 클라우드 이관 조사와 계획 (2026-11-06)

조사와 계획만 했다. 가입, 업로드, 서버 생성, 키트 조작, 상태 변경은 하지 않았다. 웹 자료 확인일은 모두 2026-11-06이다.
표기: **[확인]** 이번에 코드, 프로세스, 공식 문서로 직접 확인함. **[추정]** 계산 또는 판단이며 실행해 보지 않음.

## 1. 결론

**이번 주말 방안: Google Cloud 무료 체험($300, 90일)의 Seoul `e2-medium`(2 vCPU, 4 GB, x86_64, 50 GB 디스크) 1대에 키트 전체를 옮긴다. 모델은 규칙만, 또는 규칙+Codex로 돌리고 agy는 올리지 않는다.**

이유:
- 체험 계정은 $300 한도 안에서 쓰이고, 사용자가 유료로 전환하지 않으면 과금되지 않는다. [확인]
- Oracle A1처럼 용량 부족으로 생성이 막힐 위험이 상대적으로 작고, PC와 같은 x86_64라 Arm 호환 변수가 없다. [추정]
- 필수 코드 수정이 없다. 이관 시 상태 파일 값 하나만 고치면 된다(3장 조건 8). [확인, Linux 실행은 미검증]

**먼저 사용자에게 물을 것:** 과거 문서에 "기존 OCI와 C-SERVER"가 나온다(`SALES-EXPERIMENT.md:19`, `OPERATIONS.md:3`). 이 서버가 아직 있고 메모리 2 GB 이상이면 가입이 필요 없으므로 **그 서버가 1순위**다. 사양과 생존 여부는 코드로 알 수 없다.

**안 될 경우의 대안, 순서대로:**
1. 체험 자격이 없거나 카드가 거절되면: AWS 새 계정 Free plan 크레딧으로 EC2 소형 1대. 또는 Oracle A1을 시도.
2. 오늘 저녁까지 VM 검증이 끝나지 않으면: **이번 주말은 PC를 켜 두고**, 클라우드는 다음 주말(11-14)부터 쓴다. 급하게 옮기다 신원 중복이나 정산 색인 초기화가 나는 위험이 주말 PC 가동보다 크다.

**Cloudflare만으로는 안 된다.** Tunnel은 입구일 뿐 실행 환경이 아니다(지금처럼 계속 사용). Workers와 Containers의 이유는 2장에 있다.

## 2. 후보 비교

| 후보 | 사양 | 진짜 무료인가 | 카드 | 초과 과금 위험 | 주말까지 가능? | 판정 | 출처 |
|---|---|---|---|---|---|---|---|
| Cloudflare Workers Free | 요청당 CPU 10 ms, 일 10만 요청 | 예 | 불필요 [추정] | 없음 | 불가 | **불가.** 상주 Python과 subprocess 훅을 돌릴 방법이 문서에 없고, CPU 한도도 맞지 않음. 강의도 "키트 자체가 켜진 기계에서 계속 돌아야 한다"고 함 | [limits](https://developers.cloudflare.com/workers/platform/limits/), `weeks/week-07/README.md:56` |
| Cloudflare Containers | lite 1/16 vCPU~ | **아니오.** Free는 표에서 N/A, Workers Paid 전용 | 필요 | 있음 | - | **불가.** 요청이 와야 시작하고 유휴 시 잠드는 모델이라 주문 폴링 키트와 맞지 않음. 영구 디스크도 없음 [추정] | [pricing](https://developers.cloudflare.com/containers/pricing/) |
| Cloudflare Quick Tunnel | 동시 200요청, 가동 보장 없음, URL 변동 | 예 | 불필요 | 없음 | 지금 사용 중 | **입구로 유지.** 키트 watchdog이 대응함 | [docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) |
| **GCP 체험 e2-medium Seoul** | 2 vCPU(공유), 4 GB | 90일, $300 크레딧 안에서 무료. 예상 11일 사용 약 $15 이내 [추정, 가격표 본문 확인 실패] | 필요(0~$1 임시 승인 가능) | 체험 계정은 $300 상한. 유료 전환 전에는 무과금 | 가입 10~30분 [추정] | **1순위(가입 필요 시).** 자격 조건: 과거 GCP 유료 사용이나 체험 이력 없음 | [free](https://docs.cloud.google.com/free/docs/free-cloud-features) |
| GCP 평생 무료 e2-micro | 1 GB, 미국 3개 리전, 이그레스 1 GB/월 | 예 | 결제 계정 필요 | **있음** | 가능 | **부적합.** 메모리 빠듯, 이그레스가 하루도 못 감 | 위와 동일 |
| Oracle Always Free A1 | 합계 2 OCPU, 12 GB, 200 GB, 10 TB/월 (Arm) | 예, 사양 최고 | 필요(가상/선불 불가, 2차 자료) | Always Free만 쓰면 없음 | **불확실.** "out of host capacity" 공식 안내 있음. 커뮤니티 보고 다수 | **2순위.** 홈 리전은 가입 시 정함(변경 가능 여부 공식 FAQ 미확인, 403). 문서의 "Chuncheon 제외" 표기를 보면 Seoul 또는 Tokyo를 고를 것. 7일 유휴 회수 대응 필요(조건 10) | [always free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) |
| Oracle E2.1.Micro | 1/8 OCPU, 1 GB | 예 | 필요 | 없음 | 가능성 높음 | **부적합.** 1/8 OCPU로 10초 간격 훅 처리와 피크 436 MB+훅을 감당하기 어려움 [추정] | 위와 동일 |
| AWS 새 계정 Free plan | $100 크레딧(최대 $200), 6개월 | 예, 플랜 유지 시 무과금 | 공식 문구 상충("대부분 불필요" vs 부적격 시 필요) | Free plan 없음. Paid plan 있음 | 가능 [추정] | **3순위.** 허용 EC2 타입을 문서에서 확인 못 함. 가입 화면에서 확인 필요 | [billing](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html), [start](https://aws.amazon.com/start-here/) |
| 유료: Hetzner CPX12 | 1 vCPU, 2 GB, 40 GB | €11.99/월, €0.0192/시간. 11일 약 €5, SIN1은 더 비쌈 | 필요 | 월 상한 있음 | **확인 시점에 주문 버튼 "currently unavailable"** | 대기 후보. 미국/유럽이라 시장과의 지연 시간 미확인 | [plans](https://www.hetzner.com/cloud/regular-performance/) |
| 대조군: PC 켜 둠 | 24스레드, 64 GB | 전기료만 | - | 없음 | **즉시** | 변경 위험 0. 사용자 요구(PC 끄기)는 불충족 | 아래 참고 |

대조군 메모 [확인]: 데스크톱(배터리 0개)이고 마지막 부팅은 11-03 07:00, `run_market.py`가 이미 자동 절전을 막는다. 남은 위험은 Windows 업데이트 재부팅, 정전, 수동 종료, 자동 시작 미설정이다. `powercfg` 절전 값은 읽지 못했다.

## 3. 조건 10개 확인 결과

**1. 상주 실행.** [확인] `market_kit.cmd_join`의 주문 루프는 `POST /api/orders`를 계속 호출하고, 재입고 주기도 키트가 직접 돌린다. 시장이 깨워 주는 구조가 아니므로 **잠드는 환경은 불가**다. 일반 VM만 가능하다. 설명과 다른 점: 상주 프로세스에 `monitor_shop.py`(PID 29020, 11-03부터)도 있다. 키트 자식이 아니라 별도 실행이므로 PC를 끄면 멈춘다. 옮길지 결정이 필요하다.

**2. 외부 접속.** [확인] 그대로 동작한다. `open_tunnel`은 PATH의 `cloudflared`를 실행하고, watchdog은 60초 간격 3회 실패 시 새 터널로 다시 join한다. Linux용 cloudflared 설치만 필요하다. 계정과 고정 도메인은 불필요하다.

**3. 신원 하나만.** [확인] 키트에는 **중복 실행 방지 장치가 없다.** 양쪽을 띄우면 서로 URL을 덮어쓰고 재고와 주문이 갈라진다. 코드가 아니라 절차로 막아야 한다(4장). 또한 ACP 세션은 메모리에만 있어(`self.sessions`) 전환 순간의 진행 중 결제는 사라지며, `uncertain`으로 남은 건은 수동 확인이 필요하다.

**4. 모델 CLI.**
- 규칙 동작 [코드 확인, Linux 실행 미검증]: `seller_decision.json`은 `rules`라 판매는 원래 모델을 쓰지 않는다. `seller_r3.choose_move`는 모델 실패 시 규칙으로 돌아간다. buyer는 `buyer_decision`에서 규칙 경로가 먼저이고, gate 거부나 실패 시 `walk_away`로 끝나 프로세스는 멈추지 않는다. CLI가 없을 때도 `FileNotFoundError`(`OSError`)가 잡히고, agy가 없으면 `agy_quota_unreadable`로 건너뛴다.
- **모델 비중 [실측, 최근 24시간 buyer decision 3,174건]:** deterministic 983, quote_filter 903, model_gate 1,239, **model 44(1.4%)**, turn_limit 5. 규칙만으로 가면 44건이 포기로 바뀔 뿐, 흐름은 유지된다. 단 매출 영향은 측정하지 않았다.
- Codex [확인]: Linux에서 `codex login --device-auth`(베타, ChatGPT 보안 설정에서 먼저 켜야 함) 또는 `~/.codex/auth.json` 복사가 공식 방법이다. 다만 공식 문서는 자동화에 **API 키를 권장**하며, 구독 로그인 무인 실행의 허용 여부는 명시하지 않았다. 정기 토큰 갱신 때문에 두 기계 동시 사용은 피해야 한다 [추정]. PC에 `auth.json` 파일은 있다(내용 미열람).
- agy [확인 안 됨]: Linux 설치와 SSH 로그인 안내는 제3자 자료뿐이다. 약관(Section 6) 해석은 Google 포럼에서도 갈리고, 무인 고빈도 사용과 계정 정지 보고가 있다. 포럼 답변은 "공식 법적 해석이 아니다"라고 밝힌다. **서버 올리기는 구독 계정을 걸 만한 이득(1.4%)이 아니므로 비권장.** 약관 원문은 직접 읽지 못했다. [Google 포럼](https://discuss.ai.google.dev/t/is-google-ai-pro-antigravity-cli-automation-with-codex-permitted/187241), [Codex 인증](https://learn.chatgpt.com/docs/auth)
- 강의 메모: `weeks/week-06/README.md:100`은 `claude` 구독 로그인이 서버에서 무인으로 돌지 않는다고 적고, 서버 셀러는 다른 하네스나 모델을 써도 된다고 한다.

**5. 로그.** [실측]
- 전체 `logs/` 4.8 GB, `.market` 81 MB(5,439개).
- 최근 24시간 신규: **583 MB, 101,046개 파일**(`logs`+`.market`). 일회성 star-audit 106 MB가 포함됐을 수 있어 평소는 약 500 MB/일.
- `market-intel`: 11-05 332 MB/일 → 30분당 **약 6.9 MB로 설명과 일치**. `market-buyer` 72 MB/일. `buyer`와 `harness` 수십 MB/일.
- `market-kit.log`: 11-04 10:15 회전 후 48.6시간에 7.27 MB, **약 3.6 MB/일**. 20 MB 도달은 약 11-09~10 [추정]이므로 첫 주말 회전 가능성은 낮다.
- 주말 1회(약 64시간) 예상 약 1.4 GB, 약 27만 개 파일 [추정]. 파일 수가 많아 scp 대신 `tar` 스트림을 쓴다.
- **연속성 방법:** 이관 때 PC의 `market-kit.log`, `.log.1`, `logs/market-kit-archive/`를 mtime까지 보존해 복사한다(`tar`). 서버 키트가 같은 파일에 이어 쓰고, 복귀 때 통째로 돌려받는다. 돌려받은 파일의 앞부분이 PC 원본과 바이트 단위로 같은지(길이 N까지 sha256) 검사하면 무편집과 무단절을 증명할 수 있다. `.log.1`은 `.gitignore` 대상이고 커밋 대상은 `market-kit-archive`다. `log_archive`는 파일 이름을 mtime으로 만들므로 mtime 보존이 중요하다.

**6. 비밀값.** [확인] `shop_key.pem`, `buyer_key.pem`이 있고 week-08 최상위에 `.env`는 없다. claim code는 `MARKET_OWNER` 환경변수나 `--owner`로 들어간다. 전달: 코드 tar에서 PEM을 제외하고, 사용자 터미널에서 SSH 키로 `scp` 후 `chmod 600`. claim code는 사용자가 SSH로 들어가 `/etc/market/env`(600)에 직접 입력하고 systemd `EnvironmentFile`로 읽는다. 세션은 값을 보지 않는다. VM 메타데이터, startup script, GCS, 저장소는 쓰지 않는다. 종료 시 키 삭제 후 VM과 디스크를 삭제한다.

**7. 코드 전달.** [확인] 푸시 불필요. PC에서 `tar | ssh`로 작업 트리 그대로(미커밋 변경 포함) 보낸다. 제외: `.venv`, `tools`, `.harness-work`, `__pycache__`, `*.pem`, `.env`. 공식 `scripts/market_kit.py`도 필요하므로 경로를 `~/ai_agent_engineering/submissions/24522014/week-08`, `.../scripts/market_kit.py`로 맞춘다(`run_market.py`가 `parents[2]`를 씀). 서버에서 코드를 고쳤다면 복귀 때 `git bundle` 또는 패치로만 돌려받는다.

**8. Windows → Linux.** grep 결과 [확인, Linux 실행은 미검증]
- 수정 불필요(이미 분기 있음): `run_market.py:15`, `harness.py:65,112-120`, `strategy_controller.py:478-497`(잠금은 Linux에서 `flock`, 다른 모듈은 이 함수를 재사용), `market_kit.py:146-211`, `log_manifest.py:51`(`try`로 감쌈, 주석에 "elsewhere it does nothing"), `model_router.py:113-116`.
- **필수 조치 1건: `.market/local-settlements.json`의 `source`.** `local_settlements.py:67-79`는 저장 경로와 `str(source.resolve())`를 비교한다. 다르면 `same`과 `preserve`가 모두 거짓이라 **records, conflicting_ledgers, r3_anchor가 초기화되고 현재 로그만 다시 읽는다.** 11-04 이전 세대의 자기 정산 기록이 사라져 제어기 평가와 후기 연결에 영향을 준다. 대책: 이관과 복귀 때 복사본의 이 값 하나만 바꾼다(또는 `[Worker]Week08`이 코드에서 상대 경로로 비교하도록 수정). 복귀 시에도 필요하다.
- 영향 없음: `buyer_log_dir` Windows 경로 36,879건. 읽는 쪽이 `\\`를 `/`로 바꿔 이름만 쓴다.
- 작은 수정: `check_always_on.py:24`는 Linux에서 `local_alive=False`로 항상 실패한다. 서버에서는 공개 항목만 본다.
- 환경: `.venv`(Python 3.14.6)는 옮길 수 없다. 같은 버전을 설치하고 `requirements-lock.txt`로 재구성한다. 이 파일은 BOM으로 시작해 pip 동작을 먼저 확인한다. `TZ=Asia/Seoul`이 필요하다(키트 로그와 정산 파서가 시각 사용). 한글 폴더명은 서버에 생기지 않으며, PC 쪽 `tar`, `ssh` 인자에서만 주의하면 된다.
- 런타임 이력 의존: `buyer_surplus`, `buyer_funnel`, `strategy_controller`(`logs/pricing`), `harness_usage` 등이 과거 `logs/`를 읽는다. **로그를 빼고 옮기면 선택 동작이 달라질 수 있어 전체 `logs/`를 먼저 복사한다.**

**9. 자원.** [실측, 11-06 10:50경 60초 표본과 시작 후 평균]

| 프로세스 | 메모리 현재/피크 | CPU 평균(1코어 기준) |
|---|---|---|
| `run_market.py`(키트) | 117 / 194 MB | 0.7% |
| `seller_r3.py` | 96 / 158 MB | 2.1% |
| `cloudflared` | 36 / 40 MB | 0.1% |
| `monitor_shop.py` | 27 / 44 MB | 0% |

합계 현재 276 MB, 피크 436 MB. 짧은 훅은 표본에 잡히지 않았다. 최근 1시간 `logs/market-buyer` 151개로 훅은 초당 1개 미만이고, 라이브러리 import 0.14초. PC 전체 네트워크 평균 약 50 KB/s(모든 프로그램 포함, 30초 표본)로 상한 약 4 GB/일. 프로세스별 네트워크는 측정하지 못했다. Codex 실행은 PC에서 수백 MB이므로 동시 호출을 고려한다. 디스크는 11일 약 6 GB+기존 4.8 GB. **필요 사양 [추정]: 최소 2 GB, 권장 4 GB, 1 vCPU 이상, 디스크 30 GB 이상.** PC보다 코어가 훨씬 느리므로 첫 2시간 지연을 관찰한다.

**10. 무인 안정성.**
- 재시작 [추정]: 현재 자동 시작이 없다(`OPERATIONS.md:29`). systemd `Restart=always`, `RestartSec=30`, `KillSignal=SIGINT`로 해결한다. 키트 안의 watchdog이 seller와 터널을 처리한다(코드 확인).
- 알림 [추정]: 현재 푸시 알림이 없다(`HANDOFF.md:79`). 외부 감시용 cron을 시장 `/api/board`에서 kcwarni의 `alive`만 확인하도록 추가한다. 경로 선택은 사용자 결정이다. 이메일 또는 무료 dead-man 서비스 등을 쓸 수 있으며 비밀값은 보내지 않는다.
- 유휴 회수 [확인]: Oracle은 7일 동안 CPU p95, 네트워크, 메모리가 모두 20% 미만이면 회수할 수 있다. 12 GB를 쓰면 해당되기 쉬우므로 쓴다면 **1 OCPU, 2 GB**로 만들어 메모리 20%를 넘긴다 [추정]. GCP 체험은 90일 또는 크레딧 소진까지이고 유휴 회수 조건은 문서에서 보지 못했다. 체험 만료는 라운드 뒤다.
- 시장 규칙 [확인]: `weeks/week-07/README.md:55-56`이 VPS를 명시적으로 허용한다. 키를 서버에 비밀값으로 두는 것도 안내한다. 강의 `week-08.html`(upstream)에서 관련 금지 문구를 키워드로 찾지 못했으나 전문을 정독한 것은 아니다. 주최 측이 IP나 호스트 변경을 어떻게 보는지는 알 수 없다. 코드상으로는 같은 키의 재join으로 기록이 옮겨진다(`market_kit.py:1093` 주석).

## 4. 실행 계획

시간은 모두 추정이다.

**사용자가 직접 할 일 (15~40분)**
1. 기존 OCI/C-SERVER가 살아 있는지 답한다. 없으면 GCP 체험 가입과 카드 등록. 유료 전환 버튼은 누르지 않는다.
2. VM 생성은 세션에 맡길지 사용자가 할지 정한다. SSH 키는 사용자 PC에서 생성하고 비밀키는 PC에 둔다.
3. 키트 중지(Ctrl-C)와 PEM, claim code 전달은 사용자 터미널에서 한다.
4. 모델 정책 선택(규칙만 / 규칙+Codex).

**세션이 할 일, 사전 검증 (신원 없이, 1~2시간)**
1. VM 기본 구성: Python 3.14, cloudflared, TZ, systemd 유닛(비활성 상태 유지).
2. 코드 복사, 전체 `logs/`와 `.market`을 키트 실행 중에 1차 복사.
3. PEM 없이 서버에서 전체 단위 테스트를 실행하고, 결과를 새 로그로 저장. 테스트가 실제 시장에 접속하지 않는지 먼저 확인한다.
4. `local-settlements` 경로 변환을 **복사본에서** 시험. records 건수가 유지되는지 확인.
5. 실패하면 여기서 중단. PC는 그대로 돌아가므로 영향이 없다.

**금요일 전환 (목표 중단 15분)**
1. 재입고 창(20분마다 3분) 밖이고 진행 중 결제가 없는 시점을 고른다.
2. 사용자가 PC 키트를 중지. 해당 프로세스(키트, seller, cloudflared)가 없어졌는지 확인. `monitor_shop`은 별도 판단.
3. 차분만 다시 복사(`.market` 전체, `market-kit.log*`, 1차 이후 새 로그). PC 쪽 길이와 sha256 기록.
4. `local-settlements.source` 변환, PEM과 env 배치.
5. PC가 내려간 것을 재확인 후 서버에서 `systemctl enable --now market`.
6. 검증: `shop registered`, `buyer registered`, 공개 카드, board `alive`, 소유자 `kcwarni`, 첫 실제 `paid:true`와 `settled` 로그, `local-settlements` 건수.
7. PC에 `DO-NOT-START-KIT` 메모를 남기고, 주말 동안 PC에서 키트와 라이브 코드 수정을 하지 않는다.

**월요일 복귀 (11-09, 11-16 오전)**
1. 서버 `systemctl disable --now market`, 프로세스 부재 확인.
2. `tar` 스트림으로 새 로그와 `.market`을 내려받고, 파일 수와 크기, `market-kit.log` 앞부분 해시 비교.
3. `source` 값을 Windows 경로로 되돌린 뒤 PC 키트 시작. 위 6번과 같이 검증.
4. 로그 커밋은 경로를 지정해서. 11-17 후에는 서버의 PEM, env, `auth.json`을 지우고 VM과 디스크를 삭제.

**되돌리기:** 서버에서 시장 등록이나 첫 결제가 30분 안에 확인되지 않으면 서버를 끄고(`disable --now`) 그 시점 파일을 내려받아 PC로 복귀한다. 주말 중 장애로 복구가 안 되면 사용자가 PC를 켜고 **서버 중지를 확인한 후에만** 시작한다.

## 5. 위험과 아직 모르는 것

| 위험 | 수준 | 대응 |
|---|---|---|
| 신원 중복 실행 | 높음, 코드 차단 없음 | 전환 순서 고정, 항상 먼저 끄기 |
| 정산 색인 초기화 | 높음, 조치 안 하면 확정 | 조건 8의 값 변환, 건수 확인 |
| Linux 미검증 동작 | 중간 | 신원 없는 사전 테스트 |
| 주말 코드 변경 충돌 | 중간 | 주말 동결 또는 서버에만 반영 |
| Codex 구독 무인 사용 해석 | 중간 | 규칙만 우선, 사용자 판단 |
| 무인 장애를 아무도 모름 | 중간 | 외부 alive 감시 |
| 레이어 변경에 따른 성능 하락 | 낮음~중간 | 첫 2시간 주문 간격과 `order failed` 확인 |

**아직 모르는 것:** 기존 OCI/C-SERVER 상태, 사용자의 GCP 체험 자격, 카드 통과, 시장 서버와 Seoul VM 간 지연, e2-medium 정확한 가격(공식 표 본문을 읽지 못함), Python 3.14 패키지가 Linux에서 그대로 설치되는지, 시장 운영자의 호스트 변경 처리, Oracle 공식 FAQ(403), agy 약관 원문, 주말과 평일의 로그량 차이.

참고한 2차 자료: [Oracle free tier 커뮤니티 안내](https://guides.viren070.me/oracle), [fullmetalbrackets OCI 정리](https://fullmetalbrackets.com/blog/oci-free-tier-breakdown), [agy 설치 안내](https://pasqualepillitteri.it/en/news/3422/antigravity-cli-agy-install-migrate-gemini-cli).
