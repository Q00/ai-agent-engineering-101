# Week 06 — A2A 거래 에이전트

판매자와 구매자가 A2A v1.0으로 흥정하고, 실제 Codex 하네스가 상품·가격·수락·거절을 판단하는 과제 구현입니다. 거래 조건 검증과 오프라인 전략 학습을 함께 구성했습니다. 판매자와 구매자는 독립된 역할이며, 구매자에게 판매자의 하한가를 무효화할 권한은 없습니다.

[시스템 구조](ARCHITECTURE.md), [코드 읽기 안내](CODE_WALKTHROUGH.md), [실행 근거와 과제 보고서](REPORT.md)를 함께 확인하세요. 검증 결과와 측정 범위는 실제 실행 로그를 인용하는 보고서에 기록했습니다.

2026-10-15 최종 검증은 **공식 21/21·두 필수 도전·구조 검사 통과**입니다. 20개 상점 장보기는 100/220/280, 총 600/600으로 세 품목을 한 번씩 샀고 예약·분쟁은 0입니다. 이전 실행의 지출 540보다 높으므로 장보기 비용 개선으로 해석하지 않습니다. 원문·전후 해시와 범위는 [최종 독립 감사](logs/improvements/20261015/final-active-independent-validation-audit-20261015T084150Z.json)와 보고서에서 확인할 수 있습니다. env2 후보는 미승격이며 기존 활성 정책을 유지합니다.

![호가 정규화·역할 목표 추가 이전 버전의 A2A 거래 시스템 아키텍처](docs/architecture-final.png)

위 정적 이미지는 호가 정규화와 역할별 가격 목표를 추가하기 전의 구조입니다. 현재 연결은 [시스템 구조의 Mermaid](ARCHITECTURE.md)를 기준으로 읽습니다. 관측 정규화·역할 목표·장보기 동시성·제한적 재선택과 거래 종료 경계를 개선했고, 단계 4에서는 합성 환경 v2로 새 전략 후보를 학습했습니다. 후보의 실제 거래 screen·probe를 마쳤지만 승격하지 않았으며 활성 정책 파일은 유지합니다. 전역 예산 재배분은 구현하지 않았습니다. 성능 판단과 후보 선택 근거는 측정 보고서에서 확인합니다.

## 실행 준비

Python 3.10 이상이 필요하며 3.12를 권장합니다. 현재 하네스의 프로세스 잠금은 POSIX 기반이므로 Linux, WSL 또는 macOS에서 실행합니다. 아래 명령은 저장소 루트에서 시작합니다.

```bash
cd submissions/26510358/week-06
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
codex login status
```

이미 만든 `~/a2a-lab/.venv`를 사용한다면 새 가상환경 대신 `source ~/a2a-lab/.venv/bin/activate` 후 의존성을 설치하면 됩니다. `uv`를 사용하는 경우 가상환경 생성과 설치 명령은 다음으로 바꿀 수 있습니다.

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

Codex CLI가 `PATH`에 있고 기존 로그인 상태여야 합니다. 로그인되어 있지 않다면 `codex login`으로 로그인합니다. 이 구현은 기존 Codex 인증을 사용하며 별도 API 키를 요구하지 않습니다. 호출 시 `--ignore-user-config`를 사용합니다.

기본 모델은 `gpt-6-sol`, reasoning effort는 `low`입니다. 정확한 CLI 인자는 [harness.py](harness.py)의 `command_for()`와 각 로그의 `harness_request.command`에 있습니다. 빈 임시 디렉터리에서 비대화형으로 실행하며 [구매자 스키마](buyer-decision.schema.json), [판매자 스키마](seller-decision.schema.json), 필요한 경우 [호가 관측 스키마](quote-observation.schema.json)를 사용합니다. 구매자의 제안 금액은 `price`에 기록하며 `offer`는 null입니다. `A2A_MODEL`로 모델을 바꾸면 `agent.json`의 설명과 실행 근거도 갱신해야 합니다.

## 한 가게와 거래

제출 디렉터리에서 판매자를 실행합니다.

```bash
python3 seller.py --port 9999
```

다른 터미널에서도 같은 가상환경을 활성화하고 같은 디렉터리로 이동한 뒤 구매자를 실행합니다.

```bash
python3 buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120
```

Agent Card 주소는 `http://127.0.0.1:9999/.well-known/agent-card.json`입니다. 기본 실행의 판단·요청·영수증 기록은 `logs/runtime/`에 쌓입니다. 판매자의 하한가와 구매자의 예산은 프롬프트와 코드에서 모두 검증하며, 코드는 모델이 낸 가격을 임의로 보정하지 않습니다.

구매자는 [quote_normalization.py](quote_normalization.py)에서 현재 응답의 metadata와 DataPart를 먼저 읽습니다. 지원하는 필드에 유일한 정상 숫자 호가가 있으면 관측용 모델 호출은 추가하지 않습니다. 텍스트만 있거나 숫자 호가를 바로 읽을 수 없는 형식이면 실제 `quote_observer` 하네스가 현재 호가·상품 ID·견적 ID를 추출합니다. 원문에 없는 숫자·ID는 검증에서 거부하며, 여러 숫자 중 마지막 값을 가격으로 선택하지 않습니다. 정규화한 `ask`를 **정책 선택 전에** 전달하고 최근 협상 이력에도 보존합니다. 구조화된 호가·상품·견적 ID가 서로 충돌하면 모델이 임의로 하나를 채택하지 않고 중단합니다.

현재 거래 단위는 credits로 상품 1개입니다. 구조화된 `currency`·`quantity`와 출처를 보존하고, 명시한 단위가 다르거나 잘못된 값이면 제안 전에 차단합니다. 단위가 없는 기존 과제 상대는 이 기본 규약으로 처리합니다. 최종 영수증에 다른 통화·수량이 명시되면 분쟁으로 기록하고 예약 금액을 유지합니다. 통화 환산이나 수량 추정은 하지 않습니다. 관측 호가가 불명확하면 수락은 차단하고, 예산 내 제안 또는 종료는 구매 하네스가 결정합니다.

통화·수량의 결정적 차단은 **지원하는 구조화 필드에만** 적용됩니다. 텍스트에만 `USD`나 여러 개 구매 조건이 있으면 관측 스키마가 이를 별도 단위 필드로 추출하지 않으므로 구매 모델의 의미 판단에 의존합니다. 또한 task metadata에 이전 호가가 남고 현재 메시지 호가와 다르면 충돌로 중단합니다. 이는 잘못된 견적을 임의로 채택하지 않는 처리이며 외부 형식의 호환 한계입니다.

역할 목표도 분리했습니다. 구매자는 예산을 지키면서 낮은 구매 가격과 성사 가능성을 함께 추구하고, 판매자는 하한가를 지키면서 높은 판매 가격과 성사 가능성을 함께 추구합니다. 예산은 지출 목표가 아니며 하한가는 목표 판매가나 생산원가가 아닙니다. 실제 이전 제안과 상대 반응을 보고 양보하되, 코드에 가격 계산 공식을 추가하거나 상대의 비공개 한도를 알려주지 않습니다.

`--item lamp`처럼 명시한 기본 상품 ID와 `lamp-0001` 같은 로컬 카탈로그 ID는 견적·모델 판단·영수증에서 그대로 유지되어야 합니다. 설명형 요청은 처음 견적의 상품 ID를 고정합니다. 외부 가게의 임의 SKU 형식을 미리 알고 있다면 `--expected-item-id SKU-LAMP_2026-A`로 정확한 ID를 지정할 수 있습니다. 이때 `--item`에는 상품 설명을 넣어도 됩니다. 해당 SKU는 실제 외부 가게가 제공하는 값이어야 합니다.

기본 로그 위치는 `A2A_TRACE_DIR`로 지정할 수 있습니다. 구매자의 `--log-dir`나 판매자 설정의 `log_dir`처럼 명시한 경로가 있으면 그 경로를 우선합니다. 각 프로세스의 `policy_loaded` 기록에는 실제 로드한 정책 버전과 해시가 남습니다. 공식 후보 검사의 추론 로그는 다른 작업과 섞이지 않는 새 디렉터리에 저장합니다.

추론 호출은 프로세스 간 FIFO 대기열로 차례를 지키며 완료 후 기본 5초 간격을 둡니다. 중앙 `inference_slot()`이 `A2A_INFERENCE_GAP_SECONDS`를 읽으며 값은 유한한 0 이상의 초 단위 숫자여야 합니다. 잘못된 설정은 모델 프로세스를 시작하기 전에 거부합니다. 예전 10초 조건을 재현하려면 모든 관련 프로세스를 시작하기 전에 `A2A_INFERENCE_GAP_SECONDS=10`을 지정합니다. `A2A_QUEUE_TRACE`에 새 JSONL 파일 경로를 지정하면 입장·종료·만료 기록도 남습니다. 병렬 장보기는 기본 2개 협상 스레드가 이 한 개의 추론 입장 슬롯을 공유합니다. 원래 3개 협상에서 공유 호출 대기와 여러 차례의 작은 양보가 쌓여 제한 시간에 도달한 실패를 반영한 설정이며, 각 스레드의 협상 판단은 계속 실제 하네스가 합니다.

`no_deal`과 실제 `TASK_STATE_FAILED`, `TASK_STATE_REJECTED`, `TASK_STATE_CANCELED` 중 하나를 함께 받고 해당 목록 키의 모든 원장 기록이 해제 상태일 때만 상점을 한 번 더 선택합니다. 지출 기록이 전혀 없는 경우도 재선택할 수 있습니다. 완료·분쟁·응답 유실·시간 초과·진행 중 상태와 모델의 단순 중단·회차 제한 종료는 재구매하지 않습니다. 재선택은 실제 카드와 현재 실패·원장 스냅샷을 읽는 하네스가 결정하고, 같은 상점이 다시 선택될 수도 있습니다.

실패·거절·취소 상태라도 `price` DataPart나 이름이 `receipt`인 artifact가 함께 있으면 `terminal_receipt_conflict` 분쟁으로 기록합니다. 가격이 잘못됐거나 영수증이 일부만 있어도 예약 금액을 유지하고 재구매를 막습니다. 영수증 증거가 없는 일반 실패와 진단 artifact는 정상 해제할 수 있습니다. 이미 받은 HTTP 응답은 deadline을 넘겨 처리하더라도 검증·파싱하므로 유효한 완료 영수증을 확정할 수 있습니다. 새 RPC는 시작 전에 deadline을 확인하고, 늦게 받은 견적에서 새 가격 제안도 하지 않습니다. 응답 유실·전송 시간 초과의 분쟁 처리는 유지합니다.

관측 호출도 같은 협상 deadline과 FIFO 대기열을 사용합니다. 간격을 바꾸어도 추론 입장 슬롯은 한 개이며, 기존 429 응답의 제한된 재시도·backoff 처리를 유지합니다. 간격 변경은 가격 판단이나 RL 가중치 갱신이 아닙니다. `quote_observation_started`, `quote_observation_finished`에는 남은 시간과 큐 대기를 포함한 경과 시간이 남고, `quote_normalized`에서 출처를 확인합니다. 최초 실제 검사에서 `130.`의 마침표를 소수점 연속으로 잘못 취급해 올바른 관측값을 거부한 실패가 있었습니다. 문장 마침표는 허용하면서 소수의 일부 값은 거부하도록 고쳤으며 [실패 원문](logs/improvements/20261015/stage-01-native-screen/r01-lamp-ours-buyer/buyer-trace/buyer-bdcd6501383d.jsonl)과 [수정 회귀 검사](logs/improvements/20261015/stage-01b-grounding-unit-XvGNwnPO/unittest.log)를 보존했습니다.

장보기 제안에서는 모델이 선택한 공개 태도 `negotiable` 또는 `firm`만 `negotiationStance` 메타데이터로 보낼 수 있습니다. 숫자 본문은 그대로이며 비공개 예산과 `reason`은 보내지 않습니다. 가격이 예산과 같다는 이유로 코드가 `firm`을 자동 설정하지 않습니다. 판매자는 이를 비신뢰 협상 정보로 참고하고 독립적으로 판단합니다. 일반 단일 구매는 이 메타데이터를 보내지 않습니다.

취소와 판매 게시가 겹치면 먼저 확정된 종료 상태를 유지합니다. 취소 확인 뒤 늦은 모델 응답은 버리고, 이미 판매가 완료됐다면 실제 HTTP 취소 요청에 `TASK_NOT_CANCELABLE`을 반환합니다. SDK V2의 오류 처리로 완료 상태가 실패로 바뀌었던 관측과 수정 검사는 별도 로그에 보존했습니다.

## 검사와 필수 도전

가상환경을 활성화한 상태에서 저장소 루트로 돌아가 공식 검사를 실행합니다. 명령과 작업 디렉터리는 [agent.json](agent.json)에 정의되어 있습니다.

```bash
python3 scripts/trade_test_week06.py submissions/26510358/week-06
python3 scripts/check_week06.py submissions/26510358/week-06
```

공식 거래 검사는 실제 하네스를 호출하고 `test-report.json`과 `logs/`를 기록합니다. 재실행 전 기존 보고서와 로그를 별도 시도 디렉터리에 보존해야 실패 근거가 남습니다. 구조 검사는 실제 모델 호출을 대신하지 않습니다.

아래 명령은 다시 제출 디렉터리에서 실행합니다.

```bash
python3 -m unittest discover -s tests -v
python3 run_challenges.py --challenge all
```

`run_challenges.py`는 먼저 전체 3,003개 상품을 가진 판매자로 설명형 검색을 검사하고, 이어 실제 전문점 20개의 Agent Card를 읽어 총예산으로 병렬 장보기를 실행합니다. `--challenge 1` 또는 `--challenge 2`로 나눠 실행할 수 있습니다. 로그·가게 설정·디렉터리·결과는 `logs/challenge-1/<실행 ID>/`, `logs/challenge-2/<실행 ID>/`에 저장됩니다. 서버는 실행 종료 시 정리합니다.

기존 가게 디렉터리로 장보기하려면 다음 형식을 사용합니다. 디렉터리는 실제로 읽은 `{id, url, card}` 배열, 구매 목록은 `{key, item}` 배열입니다. 개별 `budget`은 선택 사항이며, 없으면 실제 하네스가 전체 목록과 공개 카드를 보고 한 번에 예산을 배분합니다. 배분 합계를 총예산과 대조한 뒤 협상을 시작합니다. 전체 장보기 제한은 기본 600초, 동시 협상은 기본 2개, 개별 협상 상한은 기본 280초입니다. 새 시도는 전체 잔여 시간과 개별 상한 중 작은 값을 사용합니다. `--negotiation-timeout`은 유한한 양수이며 300초 미만이어야 합니다. 단일 `buyer.py`와 도전 1은 기본 240초를 유지합니다. 예시 파일명은 자신의 파일 경로로 바꿉니다.

```bash
python3 shopping.py --directory directory.json --list shopping-list.json --total-budget 600 --workers 2 --negotiation-timeout 280 --log-dir logs/shopping/new-run
```

`run_challenges.py --challenge 2 --workers 2 --negotiation-timeout 280`에도 같은 실행 한도를 적용할 수 있습니다. 상점 선택 직전과 협상 시도 직전에 원장의 실제 총액·예약·확정·가용 금액, 현재 목록 칸의 상태, 다른 확정 구매, 남은 전체 시간을 압축합니다. 구매자에게도 `coordination_context`로 이 시점의 스냅샷과 공유 호출 용량을 전달해, 목록 전체의 성사를 고려하며 의미 있게 협상을 진행하도록 합니다. 과거 대화 전체를 붙이거나 별도 요약 모델을 호출하지 않습니다. 스냅샷은 계속 갱신되는 실시간 화면이 아니며 예산 한도를 높이거나 판매자 판단을 무효화할 권한도 아닙니다.

## 다른 구현과 A2A 비교 재현

비교는 소스 고정, 실제 거래 실행, 저장된 결과 집계 순서입니다. 아래 명령은 제출 디렉터리에서 실행하며 `/tmp`의 새 작업 경로를 지정합니다. `benchmark_prepare.py`는 기록된 PR 296·295·292·291·290·289·288의 전체 커밋 SHA로 아카이브를 받고 우리 소스도 복사합니다. 준비만으로 에이전트를 실행하거나 패키지를 설치하지는 않습니다. 각 구현의 실행 의존성은 별도로 준비되어야 합니다. 비교용 OpenAI 호환 SDK가 필요한 경우 선택 의존성을 설치합니다. 기본 앱의 `requirements.txt`는 그대로 사용합니다.

```bash
python3 -m pip install -r requirements-benchmark.txt
A2A_BENCH_WORK="/tmp/a2a-week06-benchmark-NEW"
python3 benchmark_prepare.py \
  --out "$A2A_BENCH_WORK" --ours "$PWD" \
  --source-manifest logs/cross/20261015-comparison-w0mX68/manifest.json
```

| 옵션 | 비교하는 것 |
|---|---|
| `--mode native` | 원본 `agent.json` 명령과 제공자 환경을 사용한 실행. 해당 CLI 로그인·모델 서버·외부 서비스가 필요할 수 있음 |
| `--mode controlled` | 상대의 CLI 또는 호환 HTTP 추론을 실제 Codex `gpt-6-sol/low`로 연결한 동일 모델 실험. 원본 소스·프롬프트·가격 로직은 수정하지 않음 |
| `--kind fixed` | 참가자의 구매자·판매자를 각각 과제의 고정 상대와 거래시켜 역할별 동작을 측정 |
| `--kind cross` | 참가자 구매자 ↔ 우리 판매자, 우리 구매자 ↔ 참가자 판매자로 직접 거래하여 상호운용을 측정 |

먼저 우리 구현의 원래 실행을 고정 상대와 비교할 수 있습니다. 아래는 `lamp` 1회 예시이며, `--repeats`와 `--items lamp,chair,bookshelf`로 범위를 지정합니다. 실행마다 새 `--out` 경로를 사용합니다.

```bash
python3 benchmark_a2a.py \
  --participants "$A2A_BENCH_WORK/participants.json" \
  --kit ../../../scripts/trade_test_week06.py \
  --mode native --kind fixed --only ours \
  --roles buyer,seller --items lamp --repeats 1 \
  --out "$A2A_BENCH_WORK/native-fixed-ours"
```

동일 모델 실험은 별도 터미널에서 같은 가상환경·제출 디렉터리·작업 경로를 사용해 호환 HTTP 서버를 먼저 실행합니다. 위 `A2A_BENCH_WORK` 변수도 같은 값으로 지정합니다. 비교 서버와 CLI 상대 프로세스는 같은 `A2A_INFERENCE_GAP_SECONDS` 값을 사용해야 하며, 실제 간격은 queue 이벤트와 transport 요청 근거에 기록됩니다. CLI 호출을 치환하는 `benchmark_bin` 경로는 비교 실행기가 상대 프로세스에만 설정합니다. 서버는 사용 후 해당 터미널에서 종료합니다.

```bash
python3 benchmark_transport.py serve --port 9780 \
  --trace-dir "$A2A_BENCH_WORK/controlled-http-calls"
```

```bash
python3 benchmark_a2a.py \
  --participants "$A2A_BENCH_WORK/participants.json" \
  --kit ../../../scripts/trade_test_week06.py \
  --mode controlled --kind fixed \
  --only pr296,pr295,pr292,pr291,pr290,pr289,pr288 \
  --roles buyer,seller --items lamp --repeats 1 \
  --api-url http://127.0.0.1:9780/v1 \
  --out "$A2A_BENCH_WORK/controlled-fixed-peers"
```

같은 명령에서 `--kind cross`와 새 출력 경로를 지정하면 우리 양쪽 역할과 직접 거래합니다. 원래 제공자 환경을 모두 갖춘 경우 `--mode native`로도 실행할 수 있습니다. 원본 모델의 추론 능력·도구·파라미터·지연을 동일 모델 치환 결과에서 추정하지 않습니다. 지원하지 않는 도구 호출·스트리밍이나 실행 실패도 기록되며, 성공 거래로 바꾸어 집계하지 않습니다.

```bash
python3 benchmark_summary.py \
  "$A2A_BENCH_WORK/native-fixed-ours" \
  "$A2A_BENCH_WORK/controlled-fixed-peers" \
  --shared-http-trace "$A2A_BENCH_WORK/controlled-http-calls" \
  --out "$A2A_BENCH_WORK/comparison-summary.json"
```

집계는 native/controlled, fixed/cross, 실제 역할, 상품·예산·실행기 버전을 나눕니다. 미완료 checkpoint는 최종 성과에서 제외하고 누락값은 0으로 바꾸지 않습니다. 공유 HTTP 호출 수는 전체 관측치로만 기록하며 특정 거래에 임의 배분하지 않습니다. 모델 요청 수는 토큰 사용량이 아닙니다. 적은 반복의 가격이나 성사율만으로 모든 상대에 대한 협상 우열을 단정하지 않습니다.

## 오프라인 학습과 실제 평가

학습 대상은 `balanced`, `patient`, `direct` 중 어떤 전략 프롬프트를 선택할지입니다. Python 표준 라이브러리로 구현한 **표 기반 contextual bandit**을 사용하며, 합성 실험에서 관측한 보상으로 전략별 추정값을 갱신합니다. Codex 모델의 가중치를 학습하거나 SB3로 에이전트 전체를 훈련하는 구현은 아닙니다.

현재 학습 환경은 `negotiation-surrogate-v2`, 보상 버전은 `feasible-surplus-v2`입니다. v2에서는 예산 안의 호가도 `balanced/patient` 전략이 남은 턴에 따라 할인 제안으로 탐색할 수 있고, `direct`와 마지막 턴은 가능한 성사를 더 빨리 검토합니다. 역할별 전략 안내도 양보 속도·상대 반응·남은 시간을 구분하도록 갱신했습니다. 이 가격 계산은 합성 학습용 배우에만 있으며 실제 가격은 여전히 Codex가 결정합니다.

보상 계산기는 합성 환경에서 양쪽 한도를 알고 성사 가능한 폭 `budget - reserve`에 대한 자기 이익 비율과 성사 여부, 호출 비용을 평가합니다. 이 비율 계산은 **평가 전용**이며 정책 상태와 행동 함수에 상대의 비공개 한도를 넘기지 않습니다. 모의 상대는 자기 한도로 응답합니다. 구매자 특징은 자기 예산·공개 호가·단계·의심 신호, 판매자 특징은 자기 하한가·공개 제안·단계·의심 신호입니다. 합성 상대의 정해진 수락·양보·인내 규칙과 실제 LLM의 전략 반응은 다르므로 합성 보상 상승이 실제 협상 개선을 증명하지 않습니다.

다음 예시의 출력 경로는 실행마다 새 이름으로 바꿔 이전 근거를 보존합니다.

```bash
python3 train_policy.py --output policies/candidate-example.json --report logs/rl/training-example.json --seed 42 --episodes 5000 --holdout-episodes 800
python3 evaluate_policy.py --policy policies/candidate-example.json --output logs/rl/holdout-example.json --trace-dir logs/rl/holdout-example
python3 run_adversarial.py --policy policies/candidate-example.json --log-dir logs/adversarial/example
```

학습 보고서는 **합성 시뮬레이터에서만 측정한 결과**입니다. `evaluate_policy.py`는 학습에 쓰지 않은 입력으로 실제 하네스의 한 턴 판단을 검사합니다. 한 턴 검사만으로 거래 완료나 학습 전후 성능 개선을 주장하지 않습니다. `run_adversarial.py`는 코드 검증, 테스트용 HTTP 상대, 실제 하네스의 적대적 메시지 판단을 구분합니다. `--skip-actual-harness`를 쓰면 실제 모델 검증은 수행되지 않습니다.

정책 로드 우선순위는 **명시적 `--policy` → `A2A_POLICY_PATH` → `policies/active.json` → 학습 전 기본 정책**입니다. 실행 시작 시 한 번 읽어 고정하므로 거래 중 파일 변경으로 정책이 바뀌지 않습니다. 학습기는 후보만 생성하며 활성 정책을 자동 교체하지 않습니다.

단계 1–3b에서는 기존 활성 정책을 유지했고, 단계 4에서는 seed 42·5,000회 갱신으로 [새 후보](policies/candidate-env2-20261015.json) `synthetic-bandit-env2-s42-n5000`를 만들었습니다. [학습 시점 보고서](logs/improvements/20261015/stage-04-training-report.json)의 `actual_harness_evaluated=false`는 그대로 보존하고, 이후 실제 고정 상대 screen과 PR296 상대 probe를 별도 기록했습니다. screen에서 관측 우위가 없었고 probe는 미체결이어서 [기존 활성 정책 유지](logs/improvements/20261015/stage-04-opponent-probe-decision.json)를 선택했습니다. 후보 전용 holdout·공격·공식 검사 gate는 완료하지 않았으며 새 후보를 기본 런타임에 배포하지 않았습니다. 기본 `active.json`은 기존 `synthetic-bandit-s42-n5000`입니다. 활성 파일과 Q값을 유지해도 공통 전략 문구가 바뀌면 실행 행동이 달라질 수 있으므로 정책 해시와 소스 버전을 함께 비교합니다. 실제 실패 로그에서 자동 재학습하는 경로와 전역 예산 재배분은 없습니다.

후보를 공식 검사에 적용하려면 저장소 루트에서 다음과 같이 해당 실행에만 환경변수를 지정합니다. 아래는 개발 후보 `policies/candidate.json`을 검사하는 예시이며, 다시 실행할 때는 새 추론 로그 경로를 사용합니다.

```bash
A2A_POLICY_PATH="$PWD/submissions/26510358/week-06/policies/candidate.json" \
A2A_TRACE_DIR="$PWD/submissions/26510358/week-06/logs/attempts/new-run/runtime" \
python3 scripts/trade_test_week06.py submissions/26510358/week-06
```

활성 정책으로 승격하는 [policy.py](policy.py)의 `promote_policy()`는 후보 파일의 해시·버전, 독립된 실제 하네스 평가, 코드·적대적 검사, 현재 공식 검사 전체 결과와 명령·검사 파일 해시를 확인합니다. 근거가 부족하면 승격을 거절합니다. 합성 학습 결과만으로 활성화하면 안 됩니다.

[promote_policy.py](promote_policy.py)는 이 근거를 실제 보고서와 원문 CLI 출력에서 조합하는 실행 도구입니다. 제출 디렉터리에서 아래처럼 먼저 점검합니다. 적대적 보고서 파일명은 `run_adversarial.py`가 출력한 실제 경로로 바꾸며, 모든 보고서와 추론 로그가 같은 후보·현재 모델을 사용해야 합니다. 파일이 아직 없거나 결과가 실패했다면 점검도 거절됩니다.

```bash
A2A_ADVERSARIAL_REPORT="logs/adversarial/final-02/adversarial-report-6939005bcf62.json"
python3 promote_policy.py \
  --candidate policies/candidate.json \
  --holdout-report logs/rl/holdout-04.json \
  --adversarial-report "$A2A_ADVERSARIAL_REPORT" \
  --trade-report test-report.json \
  --official-trace-dir logs/attempts/05/runtime \
  --evidence-output logs/rl/promotion-verify-new.json \
  --target policies/active.json \
  --verify-only
```

위 경로는 기존 실행 파일을 이용한 호출 형식 예시입니다. 소스가 바뀐 현재 구현을 검증하려면 해당 소스로 새 실제 평가·공식 검사 근거를 만들어 경로를 바꿔야 합니다. `--verify-only`는 검증된 근거만 저장하고 활성 정책을 변경하지 않습니다. 실제 활성화는 같은 명령에서 `--verify-only`를 제거하고 `--evidence-output`에 새 경로를 지정해 실행합니다. 기존 근거 파일을 덮어쓰지 않습니다. 점검은 로컬 로그의 완전성과 일관성을 확인하며 외부 서비스의 암호학적 실행 인증이나 RL 성능 개선을 증명하지 않습니다.

## 해석할 때 지킬 범위

영수증 검사는 합의 가격·상품·예산·task ID와 명시된 통화·수량의 불일치를 확인합니다. 명령 주입 신호나 높은 호가만으로 판매자를 사기꾼으로 판정하지 않습니다. 서명 검증·실물 배송·실제 결제나 환불은 구현 범위 밖이며 영수증으로 미배송을 증명할 수 없습니다.

구매 원장의 잠금은 **한 프로세스 안의 여러 스레드**를 조정합니다. JSON 저장은 원자적으로 교체하지만 서로 다른 구매 프로세스 사이의 동시 거래를 조정하는 데이터베이스는 아닙니다. 응답 유실은 거래 완료 가능성을 남기므로 분쟁 상태로 기록하고 예약 금액을 유지합니다.

설계와 코드 작성에 Codex 개발 보조를 사용했습니다. 제출자가 직접 검토하고 설명할 수 있도록 모듈별 책임과 실제 실행 흐름을 [CODE_WALKTHROUGH.md](CODE_WALKTHROUGH.md)에 정리했습니다.
