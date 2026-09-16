# Week 03 — LLM의 자기 확신도로 행정업무를 배정하면

> **실험 9/9회 완료(2026-09-16).** GPT-4.1 nano로 세 조건을 각 3회 완료했다. 무료 시도의 원본과 실패 기록도 보존했다. 외부 PR 제출 전이다.

## 1. 실험 설정과 재현

가상 행정업무 6개를 계산 A·글쓰기 B·코드 C에 두 개씩 대응시켰다. [tasks.json](tasks.json)은 첫 API 요청 전에 커밋했다(`7cc2955`). 태스크 순서는 T01→T03→T05→T02→T04→T06이다. 실제 일을 수행하는 정답률이 아니라 **사전 지정한 담당자와 배정 결과의 일치**를 측정한다.

무료 GLM·Nemotron 실험은 HTTP 429로 중단됐다. 사용자 승인으로 기존 OpenAI 계정의 저가 모델 GPT-4.1 nano로 전환하고, 동일 모델 안에서 세 조건을 모두 다시 실행했다. 무료 결과와 유료 결과를 합쳐 조건 효과를 계산하지 않는다. 설정은 [config-gpt41-nano.json](config-gpt41-nano.json), 실행 동결 정보는 [experiment.json](experiments/gpt41-nano-paid/experiment.json)에 있다.

| 항목 | 비교 실험 설정 |
|---|---|
| 제공자·모델 | OpenAI · `gpt-4.1-nano-2025-04-14` |
| 공통 생성 설정 | temperature=0, max_tokens=1024 |
| 호출 | A→B→C 순차, 현재 system/user 두 메시지만 전송, 자동 재시도 0 |
| 시간 설정 | SDK timeout 60초, 계약 입찰 창 240초, 각 호출 전 간격 4초 |
| 조건·반복 | baseline→homogeneous→overconfident 순환, 조건별 완료 3회 |
| 선정 | 유효 bid=true 중 최고 confidence, 최고점 동점은 먼저 접수한 후보 |
| 형식 오류 | JSON 객체 전체를 엄격히 파싱. 코드블록·추가 필드·문자열 bool·범위 밖 confidence는 거절 |
| 메시지 | 태스크별 공고 3 + 유효 true 입찰 수 + 낙찰 0/1 |
| 상태 저장 | 동일한 로컬 SQLite 하네스, gold는 실행기에 전달하지 않고 사후 평가에만 사용 |
| 도구 버전 | Python 3.12.11, OpenAI SDK 3.8.0; 전체 의존성은 uv.lock, 실행별 로그에 SDK 버전 기록 |

**시간 제한의 범위:** SDK timeout은 호출 전체의 단단한 60초 상한이 아니다. 이전 무료 Nemotron baseline 1의 단일 호출은 최대 107.0초 걸렸다. 계약 마감은 별도 단조 시계로 검사하며, 응답이 마감 이후 도착하면 후보에서 제외하고 원문을 보존한다. 동일 설정을 모든 조건에 적용한다.

[프롬프트 전문](cnp/prompts.py)은 공통 JSON 지시와 역할 지시로 구성한다. baseline은 계산/글쓰기/코드로 구분하고, homogeneous는 모두 일반 문제 해결 역할로 바꾼다. overconfident는 baseline의 C에게만 항상 입찰하며 confidence 95 이상을 내도록 지시한다. 응답을 받은 뒤 bid나 점수를 보정하지 않는다. 실제 사용 프롬프트는 각 로그의 PROMPT A/B/C에 보존한다.

```bash
cd submissions/25620027/week-03
AX_LAB_ENV_FILE="$HOME/.config/ax-agent/openai.env" ./run_lab.sh --config config-gpt41-nano.json --output experiments/gpt41-nano-paid --runs 3 --allow-paid
uv run --frozen python collect_results.py
```

키는 제출하지 않는다. 실행·재개·다른 컴퓨터의 환경변수 설정은 [SETUP.md](SETUP.md), 도면과 함수의 대응은 [ARCHITECTURE.md](ARCHITECTURE.md)를 참조한다. 모델·프롬프트·태스크·설정·구현 SHA는 각 experiment.json에 고정된다.

## 2. 실제 결과

run 식별자는 `실험 이름:원본 실행 번호`다. GLM·Nemotron은 전환 전 시도이며 GPT-4.1 nano의 조건 효과 집계에 합치지 않는다. 공란은 중단 실행의 미집계 값으로, 0점이나 미배정 6건을 뜻하지 않는다.

<!-- RESULTS_START -->
| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---:|---:|---:|---:|---:|---|
| glm-free:1 | baseline |  |  |  |  |  | crashed: BatchBlockedError: HTTP 429 |
| glm-free:2 | baseline |  |  |  |  |  | crashed: BatchBlockedError: HTTP 429 |
| nemotron-free:1 | baseline | 6 | 5 | 31 | 1 | 0 | parse_fail=7; api_error=0; timeout=0 |
| nemotron-free:2 | homogeneous | 6 | 2 | 36 | 0 | 4 | parse_fail=6; api_error=0; timeout=0 |
| nemotron-free:3 | overconfident |  |  |  |  |  | crashed: BatchBlockedError: HTTP 429 |
| nemotron-free:4 | overconfident |  |  |  |  |  | crashed: BatchBlockedError: HTTP 429 |
| nemotron-free:5 | overconfident |  |  |  |  |  | crashed: BatchBlockedError: HTTP 429 |
| gpt41-nano-paid:1 | baseline | 6 | 5 | 30 | 1 | 0 | parse_fail=0; api_error=0; timeout=0 |
| gpt41-nano-paid:2 | homogeneous | 6 | 1 | 34 | 2 | 3 | parse_fail=0; api_error=0; timeout=0 |
| gpt41-nano-paid:3 | overconfident | 6 | 2 | 34 | 0 | 4 | parse_fail=0; api_error=0; timeout=0 |
| gpt41-nano-paid:4 | baseline | 6 | 5 | 29 | 1 | 0 | parse_fail=0; api_error=0; timeout=0 |
| gpt41-nano-paid:5 | homogeneous | 6 | 2 | 39 | 0 | 4 | parse_fail=0; api_error=0; timeout=0 |
| gpt41-nano-paid:6 | overconfident | 6 | 2 | 34 | 0 | 4 | parse_fail=0; api_error=0; timeout=0 |
| gpt41-nano-paid:7 | baseline | 6 | 5 | 30 | 1 | 0 | parse_fail=0; api_error=0; timeout=0 |
| gpt41-nano-paid:8 | homogeneous | 6 | 3 | 38 | 0 | 3 | parse_fail=0; api_error=0; timeout=0 |
| gpt41-nano-paid:9 | overconfident | 6 | 2 | 34 | 0 | 4 | parse_fail=0; api_error=0; timeout=0 |
<!-- RESULTS_END -->

원본: [제출 CSV](results.csv), [유료 실험 CSV](experiments/gpt41-nano-paid/results.csv), [유료 원본 로그](experiments/gpt41-nano-paid/logs/), [무료 원본 실행](experiments/nemotron-free/runs/).

### 같은 모델의 조건별 비교

| 조건 | 완료 | gold 일치(합계) | 일치율 | 메시지 평균 | 미배정 평균 | 오배정 평균 |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 3 | 15/18 | 83.3% | 29.67 | 1.00 | 0.00 |
| homogeneous | 3 | 6/18 | 33.3% | 37.00 | 0.67 | 3.33 |
| overconfident | 3 | 6/18 | 33.3% | 34.00 | 0.00 | 4.00 |

유료 162회 호출에서 parse_fail·API 오류·timeout은 모두 0이다. 따라서 이번 유료 실험의 미배정은 형식 오류나 통신 실패가 아니라 정상 불입찰로 발생했다. 조건별 3회·고정 태스크 6개만 관측했으며 통계적 유의성을 주장하지 않는다.

### 사용량과 비용

원응답의 입력 38,403토큰(캐시 0), 출력 4,078토큰을 합산했다. 입력 $0.10/M·캐시 입력 $0.025/M·출력 $0.40/M을 적용한 환산 비용은 **$0.0054715**이다. [공식 가격](https://developers.openai.com/api/docs/models/gpt-4.1-nano)(2026-09-16 확인)과 [사용량 대조 기록](experiments/gpt41-nano-paid/usage-audit.json)을 근거로 하며, 세금·환율·계정 정산을 반영한 청구 금액은 아니다. OpenRouter의 더 저렴한 모델은 해당 계정에 충전 잔액이 없어 선택하지 않았다. 기존 OpenAI 키와 현재 생성 설정으로 바로 실행 가능한 저가 모델을 사용했다.

### 이전 무료 시도 보존

GLM 실패 2회, Nemotron 완료 2회·실패 3회를 표에 남겼다. 무료 후보 4개의 별도 진단은 `free-models-per-day`, 한도 50·잔여 0을 반환했다([진단 근거](experiments/free-model-probes-20260916T055658Z/README.md)). 이 진단은 과제 결과표에 포함하지 않는다. 무료 자동 재개 `ax-3-7`는 PAUSED이며 새 유료 결과와 섞지 않는다. 모든 실패 행의 counts는 공란이다.

## 3. Smith(1980)의 분산 센싱과 이번 재현

| 비교 축 | Smith(1980), 수업에서 소개한 분산 센싱 | 이번 구현 |
|---|---|---|
| 노드·역할 | 지역에 분산된 센서/컴퓨터, 작업별 manager/contractor 역할 전환 | 한 프로세스의 고정 Manager + 같은 모델의 독립 컨텍스트 3개 |
| 입찰 생성 | 위치·센서 목록 같은 능력 정보와 정해진 기준 | 역할 설명·공고로 생성한 bid·confidence·reason |
| 입찰의 신뢰 | 자격 조건과 기술적 노드 정보에 기반. 프로토콜 자체를 거짓 입찰 방지 증명으로 보지는 않음 | 프롬프트와 형식 검사만으로 확신도의 진실성을 보장하지 못함. gold 재심사 없음 |
| 배정 품질 | 분산 센싱 작업을 수행할 적합한 노드의 선택과 결과 보고 | 사전 gold와의 일치. 실제 업무 수행 결과는 평가하지 않음 |
| 협상 비용 | 공고·입찰·낙찰·결과 보고 등 노드 간 통신과 처리 | 과제 정의의 메시지 수. 실제 LLM 토큰·호출·지연은 원응답의 별도 정보 |
| 실패 양상 | 적격/가용 노드 부족, 응답 지연과 작업 수행 실패 등 | 무입찰·오배정·동점 편향·잘못된 JSON·timeout·API 429 |
| 구현 범위 | 재위임과 결과 보고를 포함한 분산 문제 해결 | 재위임 없이 6개 독립 작업의 배정까지 재현 |

출처: [3주차 강의 A1·A3](https://wpti.dev/ai-agent-engineering-101/week-03.html), [공식 과제 명세](https://github.com/Q00/ai-agent-engineering-101/blob/94bff56/weeks/week-03/README.md). 위 표는 수업 자료의 센싱 예와 이 코드의 범위를 비교한 것이며 원 논문의 전체 실험을 재현했다는 뜻은 아니다.

## 4. 해석

기본형의 gold 일치율은 15/18(83.3%)이고 일반형·과신형은 각각 6/18(33.3%)이었다. 기본형은 세 번 모두 T02가 미배정됐는데, 계산 담당 A도 “Task requires detailed calculations beyond my skill.”이라며 거절했다([기본형 run 1](experiments/gpt41-nano-paid/logs/run-001-baseline.txt), 39·42·45·46행). 즉 역할 분리는 담당자 일치에 도움이 됐지만 정상 불입찰을 통한 과소평가까지 막지는 못했다. 일반형에서는 세 후보의 점수가 같아 먼저 접수한 A가 낙찰되는 사례가 생겼다. T03·T06에서 모두 80을 제출한 run 2가 그 예다([일반형 run 2](experiments/gpt41-nano-paid/logs/run-002-homogeneous.txt), 19·22·25·26행 및 59·62·65·66행). 일반형은 correct가 1→2→3, 미배정이 2→0→0으로 변해 세 실행을 평균해야 했다. 메시지 평균은 기본형 29.67에서 일반형 37.00, 과신형 34.00으로 늘었다. 세 조건의 공고는 모두 회당 18개로 같지만 유효 입찰과 낙찰 수가 달랐기 때문이다. 과신형 C는 세 번 모두 6개 업무를 독점했고 오배정이 회당 4개였다. T03에서 정상 글쓰기 담당 B의 85보다 코드 담당 C의 100이 높아 C가 낙찰되는 장면이 명확하다([과신형 run 3](experiments/gpt41-nano-paid/logs/run-003-overconfident.txt), 22·25·26행). 미배정은 기본형의 회당 1개에서 과신형의 0개로 줄었지만 배정의 타당성이 좋아진 것은 아니다. 이 구현의 공고·입찰·낙찰 절차와 형식 검사는 정상 작동해도 자기 보고 confidence의 진실성이나 실제 능력을 보증하지 않으며, 이를 보완하려면 수행 결과 검증 등 별도 장치가 필요하다. 다만 일반형은 역할이 같아 원래 gold가 유일하게 적합한 후보라는 의미가 약해지고, 실제 업무 수행을 평가하지 않았으므로 이 수치를 모델 능력 순위로 해석하지 않는다. 고정 태스크 6개·조건별 3회·고정 호출 순서의 제한도 남는다.
