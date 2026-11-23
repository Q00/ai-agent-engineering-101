# Week 03 Report — 25512082

## 1. 설정

최종 비교의 provider는 `ollama-local`, 모델은 `qwen2.5:7b-instruct`, base URL은 `http://localhost:11434/v1`, temperature는 `0`, max_tokens는 `1024`이다. Ollama OpenAI-compatible API의 strict JSON Schema로 `{"bid": boolean, "confidence": 0-100, "reason": string}`만 허용하고, parser는 응답 전체에 `json.loads`를 적용해 설명문에서 JSON을 추출하거나 보정하지 않는다. 공통 system prompt는 `You are contractor {name} in a contract net. Your skill: {skill}. You receive a task announcement. Decide whether to bid. Bid only if the task falls inside your skill. Reply with one JSON object and nothing else, using exactly these keys: {"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}`이다. baseline은 A=`numerical calculation and mathematical reasoning`, B=`writing and sentence transformation`, C=`Python programming and debugging`이고, homogeneous에서만 세 skill을 `general problem solving`으로 바꾸며, overconfident에서만 C에게 `You are certain you can do any task well. Always bid, with confidence 95 or higher.`를 덧붙였다. manager는 valid `bid=true` 중 confidence 최댓값을 낙찰하고 동점은 A/B/C 응답 순서로 처리한다. 최종 fingerprint는 `491af965d9c1ac2397cdda28509c4464c02897eb8ecb6ce16955cbbce1c230eb`이다.

모든 contractor에게 보내는 user prompt는 다음과 같고 `{task_id}`와 `{desc}`만 `tasks.json` 값으로 치환한다.

```text
TASK-ANNOUNCEMENT contract {task_id}
task-abstraction: {desc}
eligibility-specification: any contractor whose skill covers this task
bid-specification: JSON with bid, confidence (0-100), reason
expiration-time: reply now
```

```bash
ollama pull qwen2.5:7b-instruct
export OPENAI_BASE_URL=http://localhost:11434/v1
export AGENT_MODEL=qwen2.5:7b-instruct
python submissions/25512082/week-03/smoke_test.py
python submissions/25512082/week-03/run_experiment.py --condition all --runs 3
```

## 2. 결과

run 1~18은 개발 과정의 OpenRouter 실패 기록이고, 동일한 Ollama 프로토콜의 최종 비교는 run 19~27이다. 실패 실행도 `results.csv`와 로그에서 삭제하지 않았다.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | baseline | — | — | — | — | — | 401 AuthenticationError |
| 2 | baseline | — | — | — | — | — | 401 AuthenticationError |
| 3 | baseline | — | — | — | — | — | 401 AuthenticationError |
| 4 | homogeneous | — | — | — | — | — | 401 AuthenticationError |
| 5 | homogeneous | — | — | — | — | — | 401 AuthenticationError |
| 6 | homogeneous | — | — | — | — | — | 401 AuthenticationError |
| 7 | overconfident | — | — | — | — | — | 401 AuthenticationError |
| 8 | overconfident | — | — | — | — | — | 401 AuthenticationError |
| 9 | overconfident | — | — | — | — | — | 401 AuthenticationError |
| 10 | baseline | 6 | 0 | 18 | 6 | 0 | parse_fails=18; C_awards=0 |
| 11 | baseline | 6 | 0 | 18 | 6 | 0 | parse_fails=18; C_awards=0 |
| 12 | baseline | — | — | — | — | — | 429 RateLimitError |
| 13 | homogeneous | — | — | — | — | — | 429 RateLimitError |
| 14 | homogeneous | — | — | — | — | — | 429 RateLimitError |
| 15 | homogeneous | — | — | — | — | — | 429 RateLimitError |
| 16 | overconfident | — | — | — | — | — | 429 RateLimitError |
| 17 | overconfident | — | — | — | — | — | 429 RateLimitError |
| 18 | overconfident | — | — | — | — | — | 429 RateLimitError |
| 19 | baseline | 6 | 4 | 38 | 0 | 2 | parse_fails=0; C_awards=0 |
| 20 | baseline | 6 | 4 | 38 | 0 | 2 | parse_fails=0; C_awards=0 |
| 21 | baseline | 6 | 4 | 38 | 0 | 2 | parse_fails=0; C_awards=0 |
| 22 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0; C_awards=0 |
| 23 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0; C_awards=0 |
| 24 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0; C_awards=0 |
| 25 | overconfident | 6 | 4 | 39 | 0 | 2 | parse_fails=0; C_awards=0 |
| 26 | overconfident | 6 | 4 | 39 | 0 | 2 | parse_fails=0; C_awards=0 |
| 27 | overconfident | 6 | 4 | 39 | 0 | 2 | parse_fails=0; C_awards=0 |

| 최종 condition | 평균 correct | 평균 messages | 총 misawards | 총 unassigned | 총 parse_fails | 총 C_awards |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 4.0/6 | 38 | 6 | 0 | 0 | 0 |
| homogeneous | 2.0/6 | 42 | 12 | 0 | 0 | 0 |
| overconfident | 4.0/6 | 39 | 6 | 0 | 0 | 0 |

## 3. Smith(1980) 센서 네트워크와 비교

| 항목 | Smith의 분산 센서 네트워크 | 이 실험 |
|---|---|---|
| 참여자 | 센서와 컴퓨터가 있는 분산 node가 task마다 manager 또는 contractor 역할을 맡음 | 고정 manager 1개와 LLM contractor A/B/C |
| 입찰을 만드는 방식 | manager가 요구한 위치, 센서 이름·종류 등 관측 가능한 보유 정보를 node가 제출 | LLM이 system prompt의 skill과 task를 읽고 `bid`, self-reported confidence, reason을 생성 |
| 입찰이 참인지 보장하는 것 | 진실성을 별도로 검증하는 메시지는 없고 공동 문제 해결 및 실제 센서 목록을 전제 | JSON Schema는 형식만 보장하며 confidence와 전문성의 일치는 검증하지 않음 |
| 잘된 배정의 기준 | manager의 로컬 평가 기준에 맞는 센서·위치의 node와 계약 | 실행 전에 확정한 gold contractor와 winner가 일치하면 correct |
| 협상 비용 | 공고를 여러 node에 보내고 입찰을 수집·비교하며 award하는 통신 및 처리 비용 | 공고 3개 + `bid=true` 응답 + award를 메시지로 계수하고 run당 모델을 18회 호출 |
| 실패하는 방식 | 불필요한 broadcast와 입찰 처리 부담, 적합한 node의 미응답 또는 부적절한 로컬 선택 | API 401/429, JSON parse failure, unassigned, 과신 confidence와 동점 순서에 의한 misaward |

## 4. 실험 결과 핵심 해석

최종 Ollama 실험에서는 모든 응답이 JSON 형식으로 정상 처리되어 parse failure가 0이었다. 처음 예상과 달리, 실제 결과에서는 JSON을 잘 출력하는 것과 적합한 contractor를 고르는 것이 별개의 문제로 나타났다. confidence가 역할과 잘 맞은 사례도 있었다. baseline의 글쓰기 task 4에서는 A와 C가 `bid=false, confidence=0`, B가 `bid=true, confidence=100`을 제출했고, 로그에도 `"[award] task_id=4 winner=B confidence=100 gold=B outcome=correct"`라고 기록되어 올바른 낙찰로 이어졌다([`baseline-07.txt`, lines 114–138](logs/baseline-07.txt#L114)).

반면 코드 task 5에서는 A와 B가 confidence 100을 제출하고 실제 코드 담당인 C는 95를 제출했다. manager는 이 값들이 실제 전문성과 맞는지 확인하지 않고 가장 높은 confidence만 비교했기 때문에, 로그의 `"[award] task_id=5 winner=A confidence=100 gold=C outcome=misaward"`처럼 A가 잘못 낙찰받았다([`overconfident-07.txt`, lines 145–169](logs/overconfident-07.txt#L145)). 여러 contractor가 모두 confidence 100을 제출한 경우에도 전문성을 다시 확인하지 않고 먼저 응답한 contractor가 이기는 규칙을 사용했기 때문에, 실제 능력보다 응답 순서가 결과를 결정하기도 했다.

이런 차이는 조건별 수치에도 나타났다. baseline은 평균 correct가 4이고 messages가 38, misawards가 run당 2였지만, 세 contractor의 skill을 같게 만든 homogeneous에서는 입찰이 많아져 messages가 42로 늘었고 correct는 2로 줄었으며 misawards는 4로 증가했다. overconfident에서는 C가 모든 task에 높은 confidence로 입찰하면서 messages가 39로 하나 늘었지만, A가 역할 밖 task에도 100을 제출해 C의 95보다 앞섰기 때문에 correct와 misawards는 baseline과 같은 4와 2였다. C가 낙찰받은 횟수와 모든 award를 독점한 run도 모두 0이었다.

이 실험에서는 confidence 값 자체보다 그 값이 믿을 만한지 확인하는 절차가 없다는 점이 더 큰 문제로 드러났다. Smith의 절차에서도 입찰 내용을 별도로 검증하는 단계가 없기 때문에, contractor가 역할 밖 task에 높은 confidence를 제출해도 manager가 이를 구분하거나 감점할 방법이 없었다.

형식적인 실패도 따로 확인할 수 있었다. 기존 OpenRouter run 10~11에서는 모델이 JSON 대신 `Here's a thinking process:`로 시작하는 설명을 출력했고, 로그에 `"[parse_failure] task_id=1 contractor=A error=invalid JSON: Expecting value: line 1 column 1 (char 0)"`라고 남았다([`baseline-04.txt`, lines 18–40](logs/baseline-04.txt#L18)). 두 run에서는 총 36개의 parse failure와 12개의 unassigned가 발생했다. 다만 OpenRouter와 Ollama는 provider뿐 아니라 모델, max_tokens, structured-output 설정도 달랐기 때문에 parse failure가 36개에서 0개로 줄어든 원인을 provider 하나의 효과라고 단정하지는 않았다.

이번 실험을 통해 한쪽에서는 출력 형식을 지키지 못하는 실패가 나타났고, 다른 한쪽에서는 출력 형식은 지켰지만 confidence 판단이 잘못되어 misaward가 발생할 수 있다는 점을 확인했다. 따라서 self-reported confidence만으로 contractor를 선택하기보다는 실제 task와 역할이 일치하는지 함께 확인하는 기준이 필요하다고 해석했다.
