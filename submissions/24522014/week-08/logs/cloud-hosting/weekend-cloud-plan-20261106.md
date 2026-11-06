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

---

# 수정판 (2026-11-06 오후): PC 끔, 규칙만, 공용 서버 조건

위 1~5장 중 아래와 충돌하는 내용은 **이 수정판이 우선**한다. 모델은 규칙만으로 확정이고 agy, Codex는 올리지 않는다.

## A. 수정된 결론

**1순위는 여전히 Google Cloud 전용 VM이다.** 공용 서버는 아래 조건을 **모두** 만족할 때만 1순위로 올린다: 서버 관리자가 이 용도를 허용함, C2 사전 점검 통과, 사용자가 관리자의 개인키 열람 가능성을 수용함, 카드 등록을 할 수 없거나 원치 않음.

| 기준 | 공용 서버 | GCP 전용 VM |
|---|---|---|
| 다른 사람 방해 | 안전장치로 줄일 뿐 0은 아님 | **없음** |
| 파일 수와 디스크 | 공용 자원 위험, 이력 일부만 복사 | 전용, 전체 복사 가능 |
| 개인키 노출 | 서버 관리자가 읽을 수 있음 | 클라우드 운영사 수준 |
| 가입과 카드 | **불필요** | 필요 |
| 준비 시간 | 관리자 허가 + 점검 | 가입 10~30분 [추정] |
| 정책 | 터널 외부 공개, 24시간 상주 허용 여부 미확인 | 사용자 책임 범위 |

두 경우 모두 **같은 sudo 없는 스크립트 절차**를 쓴다.

## B. 규칙만 실행 증명

### B1. 모델 CLI 실행 경로 전수 (최상위 `*.py`, `test_*` 제외, grep 결과)

| 경로 | 실행 CLI | 언제 | 차단 |
|---|---|---|---|
| `model_router.read_quota:159` ← `agy_status` ← `available`, `run_model` | `agy --print /quota` | **규칙 모드에서도** seller 시작(`seller_r3.py:339`), 매 buyer gate | `agy_enabled=false` |
| `model_router.run_agy:383` ← `run_model` | `agy --print` | 모델 결정 시 | 위와 동일 |
| `model_breaker.read_usage:74` ← `check` ← `available`, `run_model` | **`codex app-server`** | **규칙 모드에서도** 사용량 읽을 때 | `tripped_until` 미래값 |
| `harness.run_harness:111` ← `model_router.run_model:448,487`, `buyer.py:330`(경유), `r3_harness.ask_json` ← `seller_r3:131` | `codex exec` | 모델 결정 시. seller는 `seller_decision.json`이 `model`일 때만 | 위와 동일(`harness.py:106`, `run_model:484`) |
| `harness.build_command:57` | `claude -p` | `provider="claude"`이면 **라우터 검사 없이 직행**(`run_model:447`). `market_buyer --mode` 기본값이 `claude`지만 `agent.json`은 `--mode codex` | `agent.json` 유지, PATH 스텁 |
| `seller.py:179`, `buyer.py` 직접 실행, `harness.py` main | codex, claude | 구형 또는 수동. `agent.json`이 쓰지 않음 | 실행 금지 |
| `agy_model_check.py`, `agy_safety_check.py`, `agy_tool_deny_check.py` | agy | 수동 | 실행 금지 |
| `market_kit.py:1151` | `claude` 언급 | starter 설명 문구일 뿐 join과 무관 | 해당 없음 |

**CLI 실행 코드 없음 [grep 확인]:** `restock_policy`, `review_policy`, `market_intel`, `forum_*`, `strategy_controller`, `improvement_notices`, `leader_monitor`, `low_star_monitor`, `purchase_monitor`, `warranty_trial`, `food_trial`, `buyer_support`, `monitor_shop`, `monitor_triage`. `examples/`, `profiles/`, `tools/` 안은 grep하지 않았다(`agent.json`이 실행하지 않음).

### B2. 설정만으로 막을 수 있는가

**시험 결과 [실측, 임시 복사본, `subprocess`를 기록용으로 대체, 가짜 CLI만 PATH에 둠, 라이브 `.market` 무접촉, Windows]:** `available()` 2회 + `run_model()` 1회의 CLI 실행 시도.

| 시나리오 | 설정 | 시도 | 판정 |
|---|---|---|---|
| A | 기본 | **4회**(`agy`, `codex app-server`) | 실패 |
| B | `agy_enabled=false`만 | **3회**(`codex app-server`) | 실패 |
| C | B + `model_breaker.json enabled=false` | **1회 `codex exec`, available=True** | **반대로 모델 허용. 절대 금지** |
| D | B + `.market/model-breaker.json`의 `tripped_until=먼 미래` | **0회** | **통과** |

추가 금지: `buyer_support.json`의 `model_gate_enabled=false`(코드상 `gate_disabled`로 허용됨), `seller_decision.json`의 `model`.

**결론: 코드 변경 없이 복사본의 값 두 개(D)로 호출 0회가 된다.** 여기에 2차 방어를 둔다.
- **PATH 스텁:** `run_market.py`가 `tools/`를 PATH 앞쪽에 넣으므로, 서버의 `tools/`에 `agy`, `codex`, `claude`, `gemini` 스텁을 둔다. 스텁은 시도를 `~/market/model-call-attempts.log`에 한 줄 쓰고 exit 127. Linux 코드 경로에는 절대 경로 CLI가 없어(`harness.py:66-70`은 nt 전용, `model_router:116`은 `agy.exe`) 다른 사람의 CLI에 도달하지 않는다 [코드 확인]. **이 로그가 0줄이면 호출 0회의 증거**다.
- 실행 환경의 `PATH`를 `venv:tools:/usr/bin:/bin`으로 고정한다.

**선택적 최소 코드 변경 (제안만):** 위로 충분하므로 이번 전환에 필수는 아니다. 넣는다면 `MARKET_RULES_ONLY=1`일 때 `harness.build_command`는 `RuntimeError`, `model_router.run_model`은 `HarnessError`, `available`은 `(False, "rules_only")`, `executable`은 `None`을 첫 줄에서 반환한다. 라우터를 우회하는 `claude` 직행까지 닫힌다. PC는 환경변수가 없어 동작이 같다.

**복귀 함정:** 돌려받은 `.market/model-breaker.json`을 PC에 그대로 쓰면 **PC 모델이 꺼진 채로 남는다.** 복귀 때 전환 직전 PC 원본으로 복원하고, `model_router.json`과 `tools/`는 돌려받지 않는다.

### B3. 서버 시험 절차 (신원 없이)
1. PEM 없이 복사, 스텁 배치, D 적용.
2. 시나리오 D 시험을 복사본 안에서 재실행. Linux에서 0회 확인.
3. 전체 단위 테스트 실행(시장 접속 여부를 먼저 확인), 스텁 로그 확인.
4. 전환 후 30분과 2시간에 `wc -l ~/market/model-call-attempts.log`가 0인지 확인.

### B4. 규칙만일 때 달라지는 주문 처리 [실측, `logs/buyer` 세션 outcome]

| 구간 | 세션 | 영수증 | 규칙 경로 세션(영수증) | 모델 세션(영수증) | gate 거부 |
|---|---|---|---|---|---|
| 최근 24시간 중 10:43 이전 | 3,366 | 490 | 492 (488) | 22 (**2**) | 1,222 |
| 10:43~약 11:10 (**26분, 표본 작음**) | 63 | 7 | 7 (7) | 2 (**0**) | 24 |

- 규칙만으로 가면 모델 세션이 포기로 바뀐다. **영수증 감소 약 0.4%(2/490)** [추정]. 10:43 이후는 0건 감소지만 판단하기엔 표본이 적다.
- gate 거부 약 36~38%는 이미 모델 없이 포기 중이라 달라지지 않는다.
- 영수증은 결제 완료가 아니다(`HANDOFF.md:86`). `paid:true` 연결과 잉여 금액은 재지 않았다.
- seller는 이미 `rules`라 변화 없음.

## C. 공용 서버 계획

**시작 전 필수: 서버 관리자의 허가.** 상주 프로세스, 외부 공개 터널, 개인키 보관이 정책상 되는지 사용자가 확인한다.

### C1. 사전 점검 명령 (사용자가 서버에서 실행, 읽기 전용, 약 2분)

비밀값과 다른 사람의 명령줄, 사용자 이름을 출력하지 않는다. 붙여 넣기 전 한 번 훑어본다.

```bash
bash <<'CHECK' 2>&1 | tee ~/market-precheck.txt
echo "== system"; uname -m; . /etc/os-release; echo "$PRETTY_NAME"; nproc; uptime; free -m
echo "== disk"; df -hT "$HOME" /tmp; df -i "$HOME" /tmp; quota -s 2>/dev/null | tail -n 3
echo "== people"; echo "sessions=$(who | wc -l) users=$(who | awk '{print $1}' | sort -u | wc -l)"
echo "== top by program (no user, no args)"; ps -eo comm=,pcpu=,pmem= | awk '{c[$1]+=$2;m[$1]+=$3} END{for(k in c) printf "%-22s cpu=%.0f mem=%.1f\n",k,c[k],m[k]}' | sort -t= -k2 -nr | head -12
echo "== gpu"; nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null
echo "== load 3 samples"; for i in 1 2 3; do cut -d' ' -f1-3 /proc/loadavg; sleep 20; done; vmstat 5 3 | tail -n 3
echo "== tools"; python3 --version; for t in curl tar git flock nice ionice crontab tmux systemd-run uv; do printf '%s=' "$t"; command -v "$t" >/dev/null && echo yes || echo no; done
echo "== model CLIs on PATH (existence only)"; for t in agy codex claude gemini; do printf '%s=' "$t"; command -v "$t" >/dev/null && echo present || echo absent; done
echo "== user services"; systemctl --user is-system-running 2>&1 | head -1; loginctl show-user "$USER" -p Linger 2>/dev/null; echo "crontab_lines=$(crontab -l 2>/dev/null | wc -l)"; stat -fc %T /sys/fs/cgroup; ulimit -n
echo "== network"; curl -s -o /dev/null -m 15 -w 'market=%{http_code} %{time_total}s\n' 'https://market.ourolabs.ai/api/board'; (timeout 5 bash -c '</dev/tcp/region1.v2.argotunnel.com/7844' && echo tunnel_7844=open) || echo tunnel_7844=closed
echo "== clock"; date -u
CHECK
```

주의: `df`의 마운트 경로에 홈 경로가 보일 수 있다. `~/market-precheck.txt` 한 개만 쓴다.

### C2. 통과 기준 (제안)

공용 서버 사용량 예상: 최초 약 400 MB + 약 2만 파일(C3), 주말 신규 **1.4 GB, 27만 파일**. 회수 전 최대 약 3 GB, 60만 파일로 잡는다.

| 항목 | 통과 | 불가 |
|---|---|---|
| 관리자 허가 | 있음 | 없음 → 공용 포기 |
| 디스크 여유(홈) | **20 GB 이상이고 사용 후 사용률 85% 이하** | 어느 하나 미달 |
| inode 여유 | **200만 이상이고 사용 후 사용률 80% 이하**. `IFree`가 0이나 `-`면 동적 할당이라 디스크 기준만 적용 | 미달 |
| 사용자 할당량 | 없거나 여유 10 GB, 100만 파일 이상 | 미달 |
| 파일시스템 | ext4, xfs 등 로컬 | **nfs, lustre, cifs는 원칙적으로 불가**: 작은 파일 부하가 전체로 퍼지고 `flock` 동작이 다를 수 있음 |
| 메모리 | available **3 GB 이상**, 사용 후에도 전체의 20% 이상 | 미달, 또는 swap 활동(`si/so`>0) 지속 |
| CPU | 15분 부하/코어 **0.7 미만**, 코어 4개 이상 권장 | 초과 |
| 입출력 | `vmstat` `wa` 20% 미만 | 초과 |
| 네트워크 | market 200, `tunnel_7844=open` | 시장 접속 불가. 7844 닫힘은 cloudflared 시험 필요 |
| 도구 | `curl`, `tar`, `flock`, `crontab` 또는 `systemd --user` | 재시작 수단 없음 |
| Python | 홈에 3.14 설치 가능(`uv` 등) | 불가 |

모델 CLI가 `present`여도 불합격은 아니다. B2 방어로 실행되지 않는다.

### C3. 공용 서버 복사 범위 [실측]

`logs` 전체는 **119만 파일, 4.8 GB**다(buyer 57만, harness 38만, seller 6만, market-buyer 4.7만). **공용 디스크에 전부 올리지 않는다.** 복사: `.market`(5,439개, 81 MB), `market-kit.log*`, `market-kit-archive`, `pricing`(1.3만), `restock`(3천), 단일 파일 로그(`low-star-alerts.jsonl` 등).

제외한 buyer 로그의 영향: `buyer_surplus.py:37`은 `decision_trail`이 없는 옛 기록에서만 파일을 읽고, 없으면 `UNKNOWN_PATH`다 [코드 확인]. 그러나 `strategy_controller`, `buyer_support`, `harness_usage`의 다른 읽기가 라이브 판단을 바꾸는지는 전부 추적하지 않았다. 이는 3장 조건 8의 "전체 복사"를 공용 서버에서 포기하는 데 따른 실제 단점이다. 전용 VM은 전체 복사로 이 위험이 없다.

### C4. 권한과 자원 제한

| 수단 | 관리자 필요? | 쓰임 |
|---|---|---|
| `nice -n 15`, `ionice -c 3` | 불필요 | 키트 전체에 상속 |
| `ulimit -n 4096`, `-c 0` | 불필요 | 폭주 방지. `-u`는 사용자 전체 기준이라 쓰지 않음 |
| `ulimit -v` | 불필요 | 가상 메모리가 커서 오탐 위험. 쓰려면 4 GB부터 시험 |
| `systemd-run --user --scope -p MemoryMax=1500M -p CPUQuota=100%` | 불필요하나 cgroup v2 위임 필요 | **되면 최선**. 안 되면 C6로 대체 |
| `systemctl --user` | linger가 꺼져 있으면 `loginctl enable-linger`에 허가가 필요할 수 있음 | 가능할 때만 |
| `crontab` `@reboot`, `*/2` | 보통 불필요 | **기본 자동 재시작** |
| 전역 패키지, 시스템 서비스, `/etc`, 공용 포트 | 필요 | **쓰지 않음** |

`/etc/market/env`는 폐기한다. 모두 `~/market/` 아래에 두고 `umask 077`, `chmod 700 ~/market`. 비밀값 파일은 `~/market/secrets/env`(600).

재시작 설계안(cron `*/2`가 호출, 미실행):

```bash
#!/bin/bash
# ~/market/run.sh: a single instance under flock. Exits while YIELD or STOP exists.
umask 077; cd ~/market/repo || exit 1
W=submissions/24522014/week-08
[ -e ~/market/YIELD ] || [ -e ~/market/STOP ] && exit 0
exec 9>~/market/run.lock; flock -n 9 || exit 0
export TZ=Asia/Seoul PATH="$PWD/$W/.venv/bin:$PWD/$W/tools:/usr/bin:/bin"
[ -f ~/market/secrets/env ] && . ~/market/secrets/env
ulimit -n 4096; ulimit -c 0
setsid nice -n 15 ionice -c 3 python -X utf8 -u "$W/run_market.py" --tunnel >>~/market/supervisor.log 2>&1 &
echo $! > ~/market/kit.pid; wait $!
```

`flock`은 이 계정 안의 2중 실행을 막는다. PC와의 중복은 여전히 절차로 막는다.

### C5. 프로세스 격리 [grep 확인]

- `pkill`, `killall`, `psutil` **없음**. 이름으로 종료하는 코드 없음.
- 모든 종료는 자기 자식 PID나 그룹이다: `market_kit.py:193-203`(`start_new_session=True`로 만든 자기 그룹), `harness.py:120`, `model_breaker.py:100`. 다른 사람 프로세스에 닿지 않는다.
- 포트: `free_port`(bind 0, OS 임시 포트), seller와 프록시 모두 **127.0.0.1**(`seller_r3.py:362`, `market_kit.py:248`). 고정 포트 점유 없음. 포트 충돌 전용 처리는 없지만 임시 포트라 재시작으로 복구된다. 같은 서버의 다른 사용자는 이 localhost 포트로 가게에 접근할 수 있다.
- 터널은 바깥으로 나가는 연결만 쓴다. `--owner`를 명령줄에 쓰면 `ps`로 보이므로 환경변수만 쓴다.

### C6. 자동 양보 설계안 (cron `*/1`, 미실행)

```bash
#!/bin/bash
# ~/market/guard.sh: stop only our own process group when the server is short of something.
mem=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
disk=$(df --output=avail -m "$HOME" | tail -1); dpct=$(df --output=pcent "$HOME" | tail -1 | tr -dc 0-9)
ipct=$(df --output=ipcent "$HOME" | tail -1 | tr -dc 0-9); ipct=${ipct:-0}
load=$(awk -v n="$(nproc)" '{printf "%d",$1/n*100}' /proc/loadavg)
why=""
[ "$mem" -lt 1500 ] && why="mem=$mem"
{ [ "$disk" -lt 10240 ] || [ "$dpct" -ge 90 ]; } && why="$why disk=$disk/$dpct%"
[ "$ipct" -ge 90 ] && why="$why inode=$ipct%"
[ "$load" -ge 150 ] && why="$why load=$load%"
if [ -n "$why" ]; then
  n=$(( $(cat ~/market/trip 2>/dev/null || echo 0) + 1 )); echo $n > ~/market/trip
  [ $n -lt 3 ] && exit 0
  date -u +"%FT%TZ $why" >> ~/market/YIELD
  p=$(cat ~/market/kit.pid 2>/dev/null)
  if [ -n "$p" ] && [ "$(stat -c %U /proc/$p 2>/dev/null)" = "$USER" ] && grep -aq run_market /proc/$p/cmdline; then
    kill -TERM -- "-$p"
  fi
else echo 0 > ~/market/trip; fi
```

- 3분 연속일 때만 멈추고, PID 소유자와 명령줄을 확인한 뒤 **자기 그룹만** 종료한다.
- `YIELD`가 있으면 `run.sh`는 재시작하지 않는다. 메모리와 부하 사유는 30분 연속 정상일 때 자동 해제하고, **디스크, inode 사유는 사람이 지울 때까지 유지**하도록 추가 구현한다. 우리 폴더 상한(하루 한 번 `du -sm`, 8 GB 초과 시 정지)도 넣는다.
- 멈추면 시장에서 offline이 되고 D의 알림이 간다. 주말 매출을 잃지만 **다른 사람 작업이 우선**이다.

### C7. 비밀값 위험

공용 서버의 관리자(root)는 권한 600과 무관하게 **파일, 프로세스 메모리, 환경변수를 읽을 수 있다.**
- **`shop_key.pem`, `buyer_key.pem`:** 시장은 "키 보유자만 재가입"이다(`market_kit.py:584`). 유출되면 누구든 kcwarni 가게와 buyer로 가입해 URL을 덮어쓰고 거래해 평판과 점수를 훼손할 수 있다. 키트가 서명해야 하므로 암호화 저장은 소용없다. 라운드 중 교체 가능 여부도 모른다.
- **claim code:** GitHub 소유권 연결값이다. 공용 서버에는 **가능하면 올리지 않는다.** `join`은 값이 있을 때만 `owner`를 보내므로 키만으로 재가입은 코드상 가능하다. 그러나 owner 없이 재가입했을 때 시장이 소유권을 유지하는지는 **서버 쪽이라 확인 못 했다.** 전환 직후 board의 owner를 확인하고, 사라졌으면 서버를 멈추고 PC로 돌아가 원래 방식으로 재가입한다.
- 일반 사용자는 `chmod 600`과 `umask 077`이면 못 읽는다. 복귀 때 `shred -u`로 지우지만 백업과 스냅샷에는 남을 수 있다.

이 위험을 수용하지 못하면 **공용 서버는 쓰면 안 된다.**

### C8. 세션 접근

| 방식 | 내용 | 판단 |
|---|---|---|
| **사용자가 스크립트 실행** | 세션은 PC에서 스크립트를 만들어 검토받고, 사용자가 실행해 결과를 붙여 넣음. 세션에 서버 자격을 주지 않음 | **기본** |
| 세션 SSH | 시간이 줄지만 공용 서버 안에서 AI가 명령을 실행해 사용자 요구와 어긋남 | **공용 서버에서는 쓰지 않음**. 전용 VM은 사용자 승인 시 가능 |

서버 스크립트에는 모델 호출이 없어 "서버에서 AI가 동작하지 않는다"가 성립한다.

## D. 무인 감시와 알림

**권장: 빈 비공개 GitHub 저장소 1개 + 스케줄 워크플로.** 공개 게시판만 읽으므로 키와 코드가 들어가지 않고, 서버가 통째로 죽어도 감지한다. 실패하면 GitHub 알림(이메일, 모바일)이 온다. 이 알림 설정과 비공개 Actions 무료 시간은 이번에 문서로 재확인하지 못했으니 처음에 일부러 실패시켜 메일이 오는지 시험한다. 사용자 조작은 저장소 생성과 파일 1개, 약 3~5분이다. **week-08 포크와 무관**하다. 스케줄은 수 분 이상 늦을 수 있다.

```yaml
# .github/workflows/watch.yml
name: market watch
on:
  schedule: [{cron: "*/30 * * * *"}]
  workflow_dispatch: {}
jobs:
  watch:
    runs-on: ubuntu-latest
    timeout-minutes: 8
    steps:
      - run: |
          python3 - <<'PY'
          import json, time, urllib.request
          for attempt in range(3):
              try:
                  b = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board", timeout=30))
                  bad = [t for t in ("shops", "buyers")
                         if not next((r["alive"] for r in b[t] if r["name"] == "kcwarni"), False)]
                  if not bad:
                      print("ok"); raise SystemExit(0)
                  print("not alive:", bad)
              except OSError as e:
                  print("market read failed", e)
              time.sleep(120)
          raise SystemExit(1)
          PY
```

`/api/board` 기본값이 `round=3`이고 `shops`, `buyers`, `alive`를 포함함을 오늘 확인했다 [실측]. 시장 장애에도 메일이 오는 오탐은 있다. 알림을 받아도 사용자가 반드시 복구할 필요는 없다. 양보로 멈춘 것은 의도된 동작이다.

**`monitor_shop.py`: 옮긴다.** 공개 게시판 읽기와 규칙 판정만 하고 모델이 없다 [확인]. 27 MB, 30분 간격이라 부담이 작고 주말 기록 공백을 막는다. 다만 사람에게 알리지 않으므로 위 워크플로를 대신하지 못한다. `run.sh`에 `--interval 1800`으로 함께 띄운다.

## E. 오늘 이관 절차

### 사용자가 할 일

**결정:** 전용 VM(권장) 또는 공용 서버.

**전용 VM**
1. Google Cloud 체험 가입과 카드 등록(유료 전환 안 함).
2. Compute Engine에서 Seoul `e2-medium`, Ubuntu LTS, 디스크 50 GB VM 생성. 방화벽은 SSH만.
3. PC에서 SSH 키를 만들어 VM에 공개키 등록.
4. C1 실행, 결과 붙여 넣기.
5. 전환 시각에 PC 키트와 `monitor_shop` 중지, PEM 복사.
6. 빈 비공개 GitHub 저장소에 `watch.yml` 추가, 수동 실행 1회.

**공용 서버**
1. 관리자 허가(상주, 외부 터널, 개인키 보관), C7 위험 수용 결정, claim code 처리 결정.
2. C1 실행, 결과 붙여 넣기. C2로 판정.
3. 이후 위 5, 6번 동일. 모든 서버 명령은 사용자가 실행.

### 전환 순서

사전 검증(1~2시간, 신원 없음, PC 영향 없음): 코드 복사 시험, Python과 cloudflared 설치, 스텁 배치, B3. 실패하면 PC 유지.

전환(목표 중단 15분):
1. Orchestrator가 Week08에 라이브 코드 수정 중단을 지시.
2. **최신 코드 커밋 확인.** 첫 명령 결과가 비어 있어야 한다. 영수증 수정 포함 여부를 `git log`로 확인하고 해시를 기록.
   ```bash
   cd /c/Submissions/ai_agent_engineering
   git status --porcelain -- 'submissions/24522014/week-08/*.py' 'submissions/24522014/week-08/*.json' ':!submissions/24522014/week-08/logs'
   git log -3 --oneline -- submissions/24522014/week-08/seller_r3.py submissions/24522014/week-08/receipt_label.py; git rev-parse HEAD
   ```
3. 그 커밋을 보낸다. `git archive`는 추적 파일만 담으므로 PEM, `.market`, `.venv`가 들어가지 않는다(`SERVER`는 SSH 대상).
   ```bash
   git archive HEAD submissions/24522014/week-08 scripts ':(exclude)submissions/24522014/week-08/logs' | ssh SERVER 'mkdir -p ~/market/repo && tar -x -C ~/market/repo'
   ```
4. 재입고 창 밖이고 `processing` 결제가 없을 때 PC 키트를 Ctrl-C로 중지하고 프로세스가 사라졌는지 확인. `monitor_shop` 중지. `.market/model-breaker.json`, `.market/model-router.json`을 PC에 별도 보관.
5. `.market`과 로그(전용: 전체, 공용: C3)를 `tar`로 전송. PC 쪽 `market-kit.log` 길이와 sha256 기록.
6. 서버 복사본에만 적용: `local-settlements.json`의 `source`를 서버 경로로, `model_router.json`의 `agy_enabled=false`, `model-breaker.json`의 `tripped_until` 먼 미래.
7. PEM을 `scp`, `chmod 600`. 필요할 때만 secrets env.
8. PC 쪽 중지를 재확인 후 cron 등록. 확인: join, board alive, owner, 첫 `paid:true`, `settled`, 정산 records 건수 유지, 모델 시도 로그 0줄, 메모리와 부하.
9. 약 2시간 관찰 후 PC 종료.

복귀는 **`touch ~/market/STOP` 후 키트 종료 확인**으로 시작한다. 그다음 로그 회수, 해시 검사, `source` 복원, `model-breaker.json`과 `model-router.json` PC 원본 복원, PC 키트 시작 순서다. 서버의 코드와 `model_router.json`, `tools/`는 돌려받지 않는다.

## F. 추가 위험과 미확인

- GCP 체험 자격, 공용 서버 허가와 사양: 사용자 확인 대기.
- 시험 D는 **Windows 임시 복사본, 라우터 함수 수준**이다. 키트 전체로 돌린 것이 아니며 Linux 확인은 B3에서 한다.
- 주말에 세션이 없어 코드 문제가 나도 월요일까지 고치기 어렵다. 전환 후 2시간 관찰이 주요 안전망이다.
- 위 스크립트는 **설계안이며 실행하지 않았다.** 대상 서버에서 먼저 시험한다.
