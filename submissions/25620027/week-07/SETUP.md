# 7주차 초기 세팅 — 2026-10-20

상태: **R2 구매자·판매자 21:22:59 등록. 가격·후기·쿠폰·광고 긴급 변경본 운영 중 / 종료 22:30 KST 또는 누적 600주문·관측 모델 호출 5000회 중 먼저 도달. 현재 변경본은 문법 검사만 확인**.
공식 자료 기준은 `7a2dfb159c199ac257772176662d45161914c08e`다.
이 문서는 초기 준비와 후속 구현 이력이다. [REPORT.md](REPORT.md)는 현재 검증 범위를 구분한다. [공식 test-report.json](test-report.json)의 25/25는 과거 첫 호가 개선본 결과이며 현재 긴급 변경 전체의 통과 증거가 아니다. 실제 시장 로그는 확보했으며, 최종 집계·필수 챌린지는 아직 남아 있다.

## 이번에 준비한 것

- 6주차 커밋 `cfc9ad634f58b5404b27c814fe98059114def8bb`의 자체 Python 하네스와 판매자·구매자 런타임을 이 디렉터리로 이어받았다. 기존 6주차 작업·배포·로그는 그대로 보존한다.
- `week-07` 브랜치를 교수 저장소 최신 main에서 분리했다. 이전 보고서·test-report.json·거래 로그는 복사하지 않았다.
- Python 3.12.11 전용 `.venv`와 `requirements-kit.lock`을 준비했다. 기존 로컬 uv 캐시만 이용해 54개 패키지를 설치했다.
- 키트 의존성은 a2a-sdk 1.2.2, uvicorn 0.54.0, httpx 0.28.1, cryptography 50.0.2다. 기존 하네스는 원래 `harness_app.py.lock`과 `run_harness.sh`로 실행한다.
- `*.pem`, `.market/`, `.venv/`, 환경 파일과 Python 캐시를 제외했다. 초기 준비에서는 키를 만들지 않았고, 후속 공식 검사가 로컬 shop_key.pem을 생성했다. 권한은 0600이며 Git·로그에는 개인키를 넣지 않았다.
- `run_kit.sh`는 이 전용 환경으로 교수의 `scripts/market_kit.py`를 실행한다. 해당 공용 코드는 수정하지 않는다.
- 6주차 공개 주소는 아직 서명 기능이 없으므로 이 7주차의 `deployed`로 등록하지 않았다.

## 초기 준비에서 관찰한 실행 결과 — 후속 검증 전 이력

- 셸 문법, 하네스 import, 키트 `--help`, 하네스 `--help`: 정상.
- 실제 `run_harness.sh seller`를 일회용 점검 값으로 실행하고 로컬 Agent Card만 GET했다. HTTP 200, Sungjin Shop, JSONRPC 1.0을 확인한 뒤 서버를 종료했다. 실제 모델 API 호출은 없었다.
- 공식 `check_week07.py`: Python 14파일 구문은 통과. `market_buyer`, 시장 테스트 결과, 시장 테스트 로그, 실제 시장 ledger 로그, REPORT.md가 없어 **5개 실패**했다. 초기 미완료 상태를 그대로 보존한다. 검사기의 FAIL 뒤에 나오는 포괄적인 agent.json ok 줄을 완성 증거로 해석하지 않는다.
- `market_test_week07.py`, 6주차 trade test 재실행, `join`, 터널 개설, 시장 등록·거래, 개인키 생성은 아직 수행하지 않았다.

원본 준비 출력은 [환경·CLI 확인](logs/setup-environment.log), [초기 공식 검사](logs/setup-structural-check.log), [로컬 카드 조회](logs/setup-agent-card.log)에 있다.

## 구현한 핵심과 남은 과제

1. `market_buyer` 구현 완료: 가게 목록·후기·이름별 기억을 하네스에 주고 선택 이유를 출력한다. 기존 A2A 구매자를 재사용하며 받은 영수증을 보존하고 마지막 줄에 shop·receipt·stars·review JSON을 출력한다.
2. 판매자 영수증 서명 구현 완료: `{shop,item,price,task,sig_seller}`. 가격은 정수이고 task는 실제 A2A task id다. Ed25519 서명 대상은 정렬·공백 없는 UTF-8 JSON의 네 필드다.
3. 서명 키·기억 파일·가게 목록을 임시 작업 폴더 이동 전에 절대 경로로 해결했다. 상품을 바꾸는 판매자 metadata도 첫 가격 제안 전에 차단한다.
4. 새 시장 검사 25개를 첫 실제 모델 실행에서 통과했다. 이후 실제 시장에 구매자·판매자를 등록하고 ledger 로그를 확보했으며 수업 중 연속 거래를 진행한다.
5. 필수 챌린지: 기록·후기에 따른 호가 변경 전후 점유율·평균가, 실제 시장에서 만난 부정직한 참가자와 회피 경과를 기록한다. 가짜 사례를 실제 시장 경험처럼 쓰지 않는다.

## 실행 명령

아래는 저장소 루트 기준이다. 공식 시장 검사 25개는 한 번 실행해 통과했다. 재실행 시 기존 결과·로그를 새 attempt에 먼저 보존한다.

```bash
# 전용 환경 재구성
uv venv --python 3.12.11 submissions/25620027/week-07/.venv
uv pip sync --python submissions/25620027/week-07/.venv/bin/python \
  submissions/25620027/week-07/requirements-kit.lock

# 준비 확인: API 호출·시장 등록 없음
submissions/25620027/week-07/run_kit.sh --help

# 기능 구현 후 실행: 실제 모델 호출이 생긴다
submissions/25620027/week-07/.venv/bin/python scripts/trade_test_week06.py \
  submissions/25620027/week-07
submissions/25620027/week-07/.venv/bin/python scripts/market_test_week07.py \
  submissions/25620027/week-07
submissions/25620027/week-07/.venv/bin/python scripts/check_week07.py \
  submissions/25620027/week-07
```

실제 시장은 `https://market.ourolabs.ai`이고 GitHub 로그인을 완료했다.
판매자 주소는 `https://ax-week07.treasuresky.xyz`다. 기존 Cloudflare 설정과 분리한
과제 전용 고정 터널을 승인받아 생성했다. 인증파일·설정은 Git에서 제외된 `.market/`에 있다.
현재 키트와 터널은 실행 중이므로 중복 실행하지 않는다. 계정 연결 코드는 저장·출력하지 않는다.

초기 quick tunnel은 기본 ingress 충돌과 생성 제한으로 실패했다. 현재는 별도 설정으로 연
고정 터널과 `--public-url`·`--port 8900`을 사용한다. 아래는 처음 계획했던 한 주문 검증 명령의
이력이며 현재 실행 명령이 아니다.

```bash
# 초기 계획 이력: 현재 사용 중인 고정 터널 실행과 구분한다.
submissions/25620027/week-07/run_kit.sh join \
  --dir submissions/25620027/week-07 \
  --market "$MARKET_URL" --name 25620027 --tunnel --orders 1
```

`join`은 공개키·가게 이름·URL을 등록하고 주문·영수증·후기를 전송한다.
`--orders`의 기본값 0은 계속 실행이므로 첫 점검에서 생략하지 않는다.
첫 연속 참가에는 총 1시간(20:59:21까지)·누적 구매 120건·관측 모델 호출
1000회 중 먼저 도달하면 종료하는 감독 프로세스를 적용했다. 당시 사용자의 시간 연장 지시로
이전 30분 제한을 대체했다. 이후 사용자의 즉시 투입 지시에 따라 20:36:39 첫 호가 개선
버전으로 재접속했다. 새 공식 검사 25/25·오프라인 45/45와 독립 검토를 통과했고,
첫 호가 범위는 lamp 100~105·chair 180~189·bookshelf 260~273이다.
운영 코드 커밋은 `3dee810`이며 같은 참가자 이름·키·기억·주소와 원래 한도를 유지한다.
교수의 시장 초기화로 주문 요청이 401/403을 반환하면 같은 이름·키로 최대 3회 재join한다.
한 주문도 여러 모델 호출을 필요로 하며 주문 수는 API 비용 한도가 아니다.
기존 `run_harness.sh`는 개인 설정의 OpenAI 모델을 사용한다. 6주차 VPS의 무료 공개판 설정과 다르다.
키트가 만든 개인키는 로컬에서만 사용하며 배포가 필요할 때 별도 서버 비밀 설정으로 다룬다.

## 20:59 연장 재개와 후속 전략 검토

최초 연속 구간은 누적 120주문·843 관측 모델 호출에 도달해 20:52:06 종료됐다.
사용자가 21:50까지 계속 연장하도록 지시해 20:59:24 새 감독 실행을 시작했고,
20:59:28 판매자와 구매자가 같은 이름·키·기억·공개 주소로 R1 재등록했다.
새 제한은 21:50 또는 최초 구간부터 누적 360주문·3000 관측 호출 중 먼저 종료다.
현재 공개 Agent Card HTTP 200과 GitHub 연결 유지도 확인했다.

가격·후기 감사에서는 예산 대비 높은 지불 비율과 부정확한 후기·외부 별점 지시문 노출을
확인했다. [가격 효율·판촉·후기 개선안](.omo/evidence/w07-price-strategy-plan.md)은 설계 단계이며,
이번 시간 연장에서 새 정책이나 과거 후기 수정은 하지 않았다.

## R2 긴급 투입과 현재 실행

사용자 지시로 별도 API 검사·공식 25개 재실행 없이 `ac10710`·`3322a28`·`e4e0188`을 투입했다. 숫자 거래 기억·후기 사실 문구·판매 협상·R2WELCOME 쿠폰·자기 action 이력·공개 광고를 포함하며 문법 검사만 확인했다. 상호구매는 미구현이다. 같은 신원·키·기억·터널로 21:22:59 R2 등록됐고 현재 감독 실행은 22:30 KST 또는 최초 연속 구간부터 누적 600주문·관측 MODEL_USAGE 5000회 중 먼저 도달하면 종료한다. 중복 키트나 터널을 띄우지 않는다.

[광고판](https://ax-week07.treasuresky.xyz/)은 공개 GET 200으로 확인했다. 다만 기본 `shops.json`에는 name·url·reviews만 들어가므로 광고·카드 설명은 선택 전에 자동 전달되지 않는다. 판매 유입 진단·현재 관측은 보고서 9절을 따른다. 표시명·등록명·가격 정책을 추가 변경하지 않았다.

## 제출 기준

마감은 **8주차 수업 시작 전 PR 생성 시각**이다. 현재 단계에서 PR을 제출하지 않았다.
필수 자료와 판정의 단일 기준은 [공식 README](../../../weeks/week-07/README.md)다.
웹 교재의 참조 구현·대시보드 수치는 본인 결과가 아니다.
