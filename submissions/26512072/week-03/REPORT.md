# Week 03 — Contract Net with LLM contractors

학번 26512072. Smith(1980)의 Contract Net을 manager 1개와 LLM contractor 3개(A, B, C)로 재현하고,
baseline / homogeneous / overconfident 세 조건을 같은 태스크, 같은 프롬프트, 같은 모델, 같은 temperature로 각 3회 실행했다.

## 1. 설정

### 모델과 호출

| 항목 | 값 |
|---|---|
| Provider | 로컬 vLLM 0.17.1, OpenAI 호환 API (`http://127.0.0.1:8011/v1`) |
| Model | `Qwen/Qwen3.8-27B` (bf16, Hugging Face 스냅샷 `1d4bf0f2`) |
| Hardware | NVIDIA A100 80GB 1장, `--max-model-len 8192`, `--gpu-memory-utilization 0.92` |
| Temperature | 0.2 (top_p 0.95, top_k 20은 모델 `generation_config.json` 기본값을 vLLM이 그대로 적용) |
| max_tokens | 200 |
| Thinking | 꺼짐 (`chat_template_kwargs.enable_thinking=false`, 서버에는 `--reasoning-parser qwen3`) |
| 호출 형태 | contractor당 system prompt + user message 1회, 도구 없음, 재시도 0회 |
| 클라이언트 | Python 3.12, `openai` SDK 1.75.0 |

모든 로그의 첫 줄에 위 설정이 기록되어 있다.

### 프로토콜

- **공고.** manager가 태스크마다 A, B, C에 같은 공고문을 보낸다. 공고문은 Smith의 네 필드(task abstraction, eligibility, bid specification, expiration)를 흉내낸 고정 텍스트이며 `gold`는 포함하지 않는다.
- **입찰.** contractor는 LLM 호출 1회로 `{"bid": bool, "confidence": 0-100, "reason": str}` JSON 객체 하나를 반환해야 한다. 파서는 엄격하다. 코드펜스, 여분 텍스트, 타입 오류, 범위 밖 confidence는 모두 파싱 실패이고 "입찰 안 함"으로 센다. 수정이나 재요청은 하지 않는다. `bid=false` 응답은 로그에 남기지만 입찰 메시지로 세지 않는다.
- **낙찰.** `bid=true`인 입찰 중 confidence 최고. 동점이면 먼저 응답한 쪽(공고 순서 A→B→C)이 이긴다.
- **측정.** `correct`는 낙찰자가 gold와 같은 태스크 수, `messages`는 공고 3 + `bid=true` 수 + 낙찰 1(미배정이면 0), `unassigned`는 입찰 0건, `misawards`는 gold가 아닌 낙찰. 파싱 실패 수는 `note`에 `parse_fails=`로 기록.

### 프롬프트

공통 system prompt. `{name}`, `{skill}`만 contractor별로 바뀌고 나머지 문장은 세 조건에서 동일하다.

```
You are contractor {name} in a contract net. A manager will announce a task; you decide whether to bid for it.
Your skill: {skill}.
Bid only if the task falls inside your skill. "confidence" is how confident you are, from 0 to 100, that you would complete this exact task well if it were awarded to you. If you do not bid, confidence should be low.
Reply with exactly one JSON object and nothing else, no markdown fence, no explanation outside the JSON:
{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}
Do not solve the task.
```

| 조건 | A의 skill | B의 skill | C의 skill | 추가 문장 |
|---|---|---|---|---|
| baseline | arithmetic and numerical calculation (sums, percentages, unit conversion, comparing quantities) | writing natural-language text (emails, apologies, summaries, explanations for people) | writing and fixing program code (functions, scripts, debugging) | 없음 |
| homogeneous | general problem solving | general problem solving | general problem solving | 없음 |
| overconfident | baseline과 동일 | baseline과 동일 | baseline과 동일 | C에만 `You are certain you can do any task well. Always bid, with confidence 95 or higher.` |

참고 자료로 본 다른 제출물은 능력치를 숫자(예: calculation=90)로 주고 그 숫자에서 confidence를 시작하게 했다. 이 재현은 의도적으로 숫자를 주지 않았다. 숫자를 주면 입찰이 규칙 계산에 가까워지는데, 이번 실험의 질문은 LLM이 스스로 판단해 만든 confidence가 배정에 무엇을 하는가이기 때문이다.

### 태스크 (`tasks.json`, 실행 전 커밋 `2e22a44`)

| id | gold | 내용 |
|---|---|---|
| t1 | A | 09:40 출발, 14:05 도착. 소요 분은? |
| t2 | B | 지난 금요일 근무를 대신해 준 동료에게 짧은 감사 이메일 |
| t3 | C | 대소문자와 공백을 무시하는 회문 판별 Python 함수 |
| t4 | A | 84,000원에서 15% 할인 후 10% 세금. 최종 가격은? |
| t5 | B | 비기술 관리자에게 릴리스 1주 연기 이유를 세 문장으로 요약 |
| t6 | C | `./deploy.sh`가 permission denied. 원인과 고치는 명령 |

### 실행 방법

```bash
# 1. 모델 서버 (A100 80GB 1장)
bash serve_model.sh            # vllm serve Qwen/Qwen3.8-27B ... --port 8011

# 2. 실험 (이 디렉터리에서). 기본값이 위 표의 설정이다.
python run.py --condition baseline --runs 3
python run.py --condition homogeneous --runs 3
python run.py --condition overconfident --runs 3

# 3. 검사
python ../../../scripts/check_week03.py .
```

다른 OpenAI 호환 서버를 쓰려면 `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `AGENT_MODEL`을 바꾸면 된다. run id는 `logs/`의 기존 파일 다음 번호로 이어지므로, 재현 시에는 `--results`와 `--logs`로 다른 경로를 주는 것이 좋다.

## 2. 결과

`results.csv` 그대로.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---:|---:|---:|---:|---:|---|
| baseline-01 | baseline | 6 | 6 | 30 | 0 | 0 | parse_fails=0; bids=6; model_calls=18; tokens=5011 |
| baseline-02 | baseline | 6 | 6 | 30 | 0 | 0 | parse_fails=0; bids=6; model_calls=18; tokens=5004 |
| baseline-03 | baseline | 6 | 6 | 30 | 0 | 0 | parse_fails=0; bids=6; model_calls=18; tokens=5008 |
| homogeneous-01 | homogeneous | 6 | 2 | 41 | 0 | 4 | parse_fails=0; bids=17; model_calls=18; tokens=4812 |
| homogeneous-02 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0; bids=18; model_calls=18; tokens=4831 |
| homogeneous-03 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0; bids=18; model_calls=18; tokens=4815 |
| overconfident-01 | overconfident | 6 | 6 | 34 | 0 | 0 | parse_fails=0; bids=10; model_calls=18; tokens=5139 |
| overconfident-02 | overconfident | 6 | 6 | 34 | 0 | 0 | parse_fails=0; bids=10; model_calls=18; tokens=5126 |
| overconfident-03 | overconfident | 6 | 6 | 34 | 0 | 0 | parse_fails=0; bids=10; model_calls=18; tokens=5114 |

조건별 요약. 파싱 실패는 162회 호출 중 0회였다.

| condition | correct / 6 | messages | misawards | 입찰 수 | 낙찰 18건 중 동점 규칙으로 결정된 수 | 등장한 confidence 값 |
|---|---:|---:|---:|---:|---:|---|
| baseline | 6, 6, 6 | 30, 30, 30 | 0 | 6/18 | 0 | 5, 10, 95, 98 |
| homogeneous | 2, 2, 2 | 41, 42, 42 | 4, 4, 4 | 17~18/18 | 18 | 10, 85, 95 |
| overconfident | 6, 6, 6 | 34, 34, 34 | 0 | 10/18 | 11 | 10, 95, 98 |

## 3. Smith(1980) 분산 센서망 vs 이 재현

| 항목 | Smith(1980), 분산 센서망(CNET) | 이 재현 |
|---|---|---|
| 참여자 | 지역에 흩어진 센서·처리 노드. 태스크를 가진 노드가 그때그때 manager가 되고 나머지가 contractor. 역할이 고정되지 않고 계층적으로 재귀함 | Python 코드가 manager, 같은 LLM(Qwen3.8-27B)이 system prompt만 다른 A, B, C 세 contractor. 역할 고정, 1단계 |
| 입찰 생성 방식 | 노드가 자기 위치, 보유 센서 종류, 현재 부하 같은 **로컬 사실**에서 고정 규칙으로 계산. 공고의 eligibility에 맞지 않으면 아예 응답하지 않음 | LLM이 공고문과 자기 skill 설명을 읽고 **말로 된 자기평가**를 JSON으로 생성. confidence는 모델이 그 자리에서 지어내는 숫자 |
| 입찰 정직성 보장 | 프로토콜 자체에는 없음. 다만 노드는 협력적으로 설계된 같은 시스템의 일부이고, 입찰 내용(센서 위치, 종류)이 manager가 비교·검증할 수 있는 사실 정보라는 점이 사실상의 보장 | 없음. manager는 JSON 형식과 타입만 검사한다. confidence가 실제 능력과 맞는지 볼 방법이 없고, 한 문장의 지시(overconfident)로 즉시 왜곡된다 |
| 배정 품질 기준 | 센서 커버리지, 데이터 통합 위치의 적절성 등 전체 문제 해결에 기여하는지. 결과가 report 단계로 되돌아와 평가됨 | 낙찰자가 실행 전에 정한 `gold`와 같은가. 실제 작업 수행은 하지 않으므로 결과 품질은 측정하지 않음 |
| 협상 비용 | 브로드캐스트 통신량. Smith는 이를 줄이기 위해 focused addressing, directed contract, 공고 만료 시간을 두었음 | 메시지 수 = 공고 3 + 입찰 + 낙찰 1. 여기에 LLM 호출 비용(태스크당 3회, 약 280 토큰/회)이 추가. 입찰이 많아질수록 메시지도 증가(baseline 30 → overconfident 34 → homogeneous 42) |
| 실패 양상 | 적격 노드가 없어 입찰 0(미배정), 노드 고장, 통신 지연으로 공고 만료 | 미배정과 파싱 실패는 이번에 0. 대신 **동점 편향**(모두 95 → 항상 A), **오배정**(homogeneous 4/6), **형식은 맞지만 내용이 거짓인 입찰**(overconfident C가 계산·글쓰기 태스크에도 95) |

## 4. 해석

판단형 입찰은 skill이 서로 다르고 태스크가 그 경계 안에 떨어질 때만 잘 작동했고, 그 밖에서는 confidence 숫자가 정보를 거의 담지 못해 배정이 낙찰 순서 규칙에 넘어갔다. baseline에서는 18개 태스크 전부 gold만 입찰했다. 입찰하지 않는 쪽은 `[no-bid] B: confidence=10 reason=This is a mathematical calculation task, not a natural-language writing task.`처럼 이유를 정확히 댔고, 입찰하는 쪽은 예외 없이 95 또는 98을 냈다(`[award] t3 -> C (confidence=95; bids: C=95; gold=C) correct`, baseline-01). 즉 baseline의 6/6은 confidence를 비교해서 얻은 결과가 아니라, "입찰할까"라는 이진 판단이 맞았기 때문에 얻은 결과다. homogeneous는 이것을 그대로 드러냈다. 셋이 같은 skill 설명을 받자 세 run 모두 17~18/18이 입찰했고 confidence는 태스크마다 셋이 똑같았다(`[award] t3 -> A (confidence=95; bids: A=95, B=95, C=95; gold=C) MISAWARD`, `[award] t5 -> A (confidence=85; bids: A=85, B=85, C=85; gold=B) MISAWARD`, homogeneous-01). 18건의 낙찰이 전부 동점 규칙으로 결정되어 A가 모든 태스크를 가져갔고, correct는 A가 gold인 t1, t4 두 개로 고정됐다. 메시지 수도 30에서 41~42로 늘어 협상 비용은 가장 크고 배정 품질은 가장 낮았다. 이 조건에서 LLM의 reason은 셋이 문장까지 거의 같았다(`Summarizing complex technical reasons for a non-technical audience is a core general problem-solving skill.`이 A, B, C 세 줄에 그대로 반복). 같은 모델에 같은 프롬프트를 주면 같은 판단이 나오는 것이 당연한데, 프로토콜은 이 셋을 서로 다른 노드로 취급해 순서로 갈랐다. overconfident는 겉으로는 6/6, 오배정 0으로 baseline과 같았지만 이유가 다르다. C는 18회 전부 입찰했고 계산·글쓰기 태스크에도 95를 냈다(`[reply] C: '{"bid": true, "confidence": 95, "reason": "I can write a script to calculate the time difference accurately."}'`, overconfident-01 t1). 그런데 정직한 gold contractor도 같은 태스크에 95를 냈기 때문에 동점이 되고, 공고 순서가 A→B→C라서 C가 밀렸다(`[award] t1 -> A (confidence=95; bids: A=95, C=95; gold=A) correct`). 낙찰 18건 중 11건이 이 동점 규칙으로 결정됐다. 즉 과신한 C를 막은 것은 프로토콜의 어떤 방어가 아니라, 이 모델이 "확신"을 95라는 한 값으로 표현하는 습관과 C가 마지막 순서였다는 우연이다. 5절의 보충 실험에서 순서만 C→A→B로 바꾸자 같은 프롬프트로 correct가 3, 2, 5로 떨어지고 C가 12/18 태스크를 가져갔다. Smith의 프로토콜에 여기에 대한 방어가 없는 이유는, 원래 입찰이 노드 위치나 센서 종류처럼 manager가 대조할 수 있는 사실이었고 노드는 협력적으로 설계된 같은 시스템의 일부였기 때문이다. 입찰 내용을 검증하는 단계, 입찰자의 과거 성과를 반영하는 단계, 동점을 순서 외의 기준으로 푸는 단계가 프로토콜에 없어도 문제가 되지 않았다. 입찰이 LLM의 자기평가로 바뀌면 그 세 가지 부재가 그대로 노출된다. confidence는 한 문장으로 95로 고정되고, manager는 형식만 볼 수 있고, 동점은 순서로 풀린다. 이번 실험에서 파싱 실패는 0이었으므로 "모델이 JSON을 못 낸다"는 실패 양상은 나타나지 않았고, 실패는 전부 형식은 완벽하지만 내용이 정보가 없거나 거짓인 입찰에서 왔다.

## 5. 보충 실험: 낙찰 순서만 바꾸면

정식 세 조건은 모두 공고 순서 A→B→C다. overconfident에서 6/6이 나온 것이 동점 규칙의 우연인지 확인하기 위해, 프롬프트·태스크·모델·temperature를 그대로 두고 **공고 순서만 C→A→B**로 바꿔 3회 더 실행했다(`python run.py --condition overconfident --runs 3 --order CAB --results extra/results.csv --logs extra/logs`). 이는 과제의 세 조건에 속하지 않으므로 `results.csv`가 아닌 `extra/`에 두었다.

| run (extra/) | order | correct | messages | misawards | note |
|---|---|---:|---:|---:|---|
| overconfident-01 | CAB | 3 | 34 | 3 | C가 t1, t2, t5를 95 동점으로 가져감. t4는 A가 98로 이김 |
| overconfident-02 | CAB | 2 | 34 | 4 | C가 t1, t2, t4, t5를 전부 95 동점으로 가져감 |
| overconfident-03 | CAB | 5 | 33 | 1 | A가 t1, t4에 98을 내서 이김. C가 t2에는 `bid=false, confidence=10`으로 지시를 어기고 입찰 안 함 |

로그 인용. `[award] t1 -> C (confidence=95; bids: C=95, A=95; gold=A) MISAWARD` (extra/overconfident-01). 정식 실행의 같은 태스크는 `[award] t1 -> A (confidence=95; bids: A=95, C=95; gold=A) correct`였다. 바뀐 것은 두 입찰의 도착 순서뿐이다. 반대로 정직한 A가 98을 낸 경우에는 순서와 무관하게 A가 이겼다(`[award] t4 -> A (confidence=98; bids: C=95, A=98; gold=A) correct`, extra/overconfident-01). 즉 이 프로토콜에서 과신에 대한 유일한 "방어"는 정직한 입찰자가 우연히 더 높은 숫자를 내는 것이었고, 그 확률은 temperature 0.2에서 run마다 달랐다(correct 3, 2, 5). run 3에서 C가 t2에 입찰하지 않은 것은 한 문장 지시가 항상 통하지도 않는다는 증거로, 과신 지시의 효과 자체도 확률적이다.

## 7. 보충 실험 2: 두 스킬이 섞인 태스크 집합

정식 태스크 6개는 스킬 경계에 깔끔하게 떨어져서 baseline이 3회 모두 6/6이었다. 판단형 입찰이 어디서 흔들리는지 보기 위해, 프롬프트·모델·temperature·순서(A→B→C)는 그대로 두고 **태스크만** 두 스킬이 겹치는 8개(`tasks_mixed.json`, 실행 전 커밋 `e6cf0ee`)로 바꿔 세 조건을 3회씩 다시 실행했다. 결과는 `extra/mixed-tasks/`에 있고, 정식 결과(`results.csv`, `logs/`)는 건드리지 않았다.

| id | gold | 겹치는 스킬 | 내용 |
|---|---|---|---|
| m1 | A | 계산+글쓰기 | 7명이 팁 10% 포함 218,500원을 나눈 금액을 한 문장으로 |
| m2 | B | 글쓰기+코드 | 인턴에게 코드 없이 for-loop를 세 문장으로 설명 |
| m3 | C | 코드+계산 | 화씨→섭씨 변환 Python 함수, 소수 한 자리 |
| m4 | A | 계산+코드 | 연 6% 복리로 100만 원이 150만 원을 넘는 해, 연도별 금액 |
| m5 | B | 글쓰기+코드 | `ECONNREFUSED 127.0.0.1:5432`를 일반 사용자용 문장으로 |
| m6 | C | 코드+계산 | CSV에서 매출 합계가 최대인 달을 출력하는 스크립트 |
| m7 | A | 계산 | 3.5마일→km, 68°F→°C, 소수 두 자리 |
| m8 | B | 글쓰기+시간 | 22:00~23:30 서버 점검 공지 두 문장 |

`extra/mixed-tasks/results.csv` 그대로.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---:|---:|---:|---:|---:|---|
| baseline-01 | baseline | 8 | 7 | 42 | 0 | 1 | parse_fails=0; bids=10 |
| baseline-02 | baseline | 8 | 7 | 44 | 0 | 1 | parse_fails=0; bids=12 |
| baseline-03 | baseline | 8 | 7 | 42 | 0 | 1 | parse_fails=0; bids=10 |
| homogeneous-01 | homogeneous | 8 | 4 | 56 | 0 | 4 | parse_fails=0; bids=24 |
| homogeneous-02 | homogeneous | 8 | 3 | 56 | 0 | 5 | parse_fails=0; bids=24 |
| homogeneous-03 | homogeneous | 8 | 3 | 56 | 0 | 5 | parse_fails=0; bids=24 |
| overconfident-01 | overconfident | 8 | 7 | 47 | 0 | 1 | parse_fails=0; bids=15 |
| overconfident-02 | overconfident | 8 | 8 | 47 | 0 | 0 | parse_fails=0; bids=15 |
| overconfident-03 | overconfident | 8 | 8 | 48 | 0 | 0 | parse_fails=0; bids=16 |

| condition | correct / 8 | 낙찰 24건 중 동점 규칙으로 결정 | 그중 오배정 | 등장한 confidence 값 |
|---|---:|---:|---:|---|
| baseline | 7, 7, 7 | 8 | 3 | 10, 95, 98 |
| homogeneous | 4, 3, 3 | 23 | 13 | 85, 90, 95 |
| overconfident | 7, 8, 8 | 18 | 1 | 10, 95, 98 |

파싱 실패는 216회 호출 중 0회였다. 세 가지가 새로 드러났다.

**첫째, baseline이 깨지는 지점은 "할 수 있다"가 둘 이상인 태스크다.** m3(화씨→섭씨 함수)에서 A와 C가 3회 모두 95로 입찰했고, 순서상 A가 이겨 3회 모두 오배정이었다. `[bid] A: confidence=95 reason=Converting Fahrenheit to Celsius involves a simple arithmetic formula and rounding, which falls directly within my numerical calculation skills.` / `[bid] C: confidence=95 reason=Writing a simple Python function for unit conversion is well within my coding skills.` (baseline-01). 둘 다 틀린 말이 아니다. 태스크는 "Python 함수를 써라"인데 A는 "계산이 들어간다"만 보고 입찰했고, 두 판단이 같은 숫자로 나오자 프로토콜은 순서로 갈랐다. m4(복리 계산)에서도 C가 3회 모두 "loop or formula ... within my coding skills"로 95를 냈지만, A가 먼저라 정답으로 끝났다. 즉 baseline의 7/8은 여전히 순서 덕이 절반이다. 24건 중 8건이 동점이었고 그중 3건이 오배정이었다.

**둘째, 판단은 흔들리는데 confidence는 흔들리지 않는다.** baseline에서 B가 m1(금액을 한 문장으로)에 입찰한 것은 3회 중 1회뿐이고, C가 m7(단위 변환)에 입찰한 것도 1회뿐이다. 입찰 여부는 run마다 바뀌었지만, 입찰하면 95였다(`[bid] B: confidence=95 reason=Writing a clear, natural-language sentence to communicate a calculated amount is a core part of my skill set.`, baseline-02). "이 태스크는 내 스킬에 반쯤 걸린다"는 판단이 70이나 60으로 표현되는 대신, 95 아니면 10으로 나온다. manager가 confidence를 비교하도록 설계된 프로토콜에 이진값이 들어오면 비교할 것이 없다.

**셋째, 과신 조건이 baseline보다 점수가 높았다.** overconfident의 8/8 두 번은 C가 m3에 98을 내서 A의 95를 이긴 덕이다(`[bid] C: confidence=98 reason=Writing a simple Python function for unit conversion is a core part of my coding skill set.`, overconfident-02). 과신 지시가 C의 confidence를 95→98로 밀어 올렸고, 그 3점이 우연히 gold 방향이었다. 같은 C가 m1, m2, m4, m5, m7, m8에도 95로 입찰했지만 정직한 gold가 같은 95를 먼저 냈기 때문에 전부 밀렸다. 24건 중 18건이 동점으로 결정됐다. 정식 실험(4절)과 같은 구조다. 과신자를 막은 것은 순서이고, 과신자가 이긴 것은 숫자 3점이며, 둘 다 프로토콜이 의도한 "능력이 높은 쪽에 배정"과는 관계가 없다.

homogeneous는 정식 실험과 같은 양상이 더 크게 나타났다. 24회 호출 전부 입찰, 메시지 56개, 24건 중 23건 동점, A가 gold인 3개만 정답. 유일하게 A가 아닌 낙찰은 m5에서 C가 90, A·B가 85를 낸 homogeneous-02였고 이것도 오배정이었다.

## 8. 한계

- 정식 태스크 6개는 스킬 경계에 깔끔하게 떨어져 baseline이 완벽했다. 7절의 혼합 태스크 8개에서는 baseline도 7/8로 내려가고 동점이 8건 생겼으므로, 수치는 태스크 집합에 크게 의존한다. 조건당 3회라 경향 확인 수준이며, temperature 0.2에서 run 간 분산은 작았다.
- 실제 작업은 수행하지 않았다. `correct`는 배정이 사전 gold와 맞는지이며, 낙찰자가 일을 잘했는지가 아니다.
- confidence가 5, 10, 85, 95, 98 다섯 값만 나왔다. 이 모델이 0-100 척도를 사실상 "한다/안 한다" 이진값처럼 쓴다는 뜻이며, 다른 모델에서는 동점 빈도가 달라질 수 있다.
- thinking을 꺼서 파싱 실패가 0이었다. thinking을 켜면 JSON 앞에 다른 텍스트가 붙을 수 있고 그러면 파싱 실패가 늘어난다(스모크 테스트에서 개행이 앞에 붙는 것을 확인했으나, 정식 실행에는 포함하지 않았다).
