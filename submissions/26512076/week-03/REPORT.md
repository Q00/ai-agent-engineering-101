\# Week 03 — Contract Net with LLM Contractors



\## 1. 실험 설정



\- Provider: Groq

\- Model: `openai/gpt-oss-20b`

\- Temperature: `0.7`

\- 태스크: 계산 2개, 글쓰기 2개, Python 코딩 2개로 총 6개

\- 실행 횟수: 조건별 3회, 총 9회

\- 모델 호출: 실행당 18회

\- 사용 도구: 없음

\- Rate limit 대응: 모델 호출 후 2.1초 대기

\- 낙찰 규칙: `bid=true`인 입찰 중 confidence가 가장 높은 contractor에게 배정

\- 동점 규칙: 먼저 응답한 contractor가 낙찰되며 호출 순서는 A → B → C

\- JSON 오류 처리: 지정된 JSON 형식이 아니면 입찰하지 않은 것으로 처리하고 `parse\_fails`에 기록



공통 contractor system prompt는 다음과 같이 설정하였습니다.



&#x20;   You are contractor {name} in a contract net.

&#x20;   Your skill is: {skill}.



&#x20;   You will receive one task announcement.

&#x20;   Decide whether to bid based on whether the task matches your skill.



&#x20;   Reply with exactly one JSON object and nothing else:

&#x20;   {

&#x20;     "bid": true,

&#x20;     "confidence": 85,

&#x20;     "reason": "one short sentence"

&#x20;   }



&#x20;   Rules:

&#x20;   - "bid" must be true or false.

&#x20;   - "confidence" must be a number from 0 to 100.

&#x20;   - "reason" must be one short sentence.

&#x20;   - Do not perform the task.

&#x20;   - Do not choose the winner.



&#x20;   {extra\_instruction}



각 태스크는 다음과 같은 user message로 전달하였습니다.



&#x20;   TASK-ANNOUNCEMENT

&#x20;   contract-id: {task\_id}

&#x20;   task-description: {desc}

&#x20;   eligibility: Bid only if the task matches your skill.

&#x20;   reply-format: JSON with bid, confidence, and reason.



세 조건에서 contractor의 역할은 다음과 같이 설정하였습니다.



\- `baseline`: A는 calculation, B는 professional and plain-language writing, C는 Python programming을 담당하도록 설정하였습니다.

\- `homogeneous`: A, B, C 모두 general problem solving 능력을 가지도록 설정하였습니다.

\- `overconfident`: baseline과 동일하게 설정하되, C의 system prompt에 다음 문장을 추가하였습니다.



&#x20;   You are certain you can do any task well. Always bid, with confidence 95 or higher.



Windows의 Anaconda Prompt에서 다음과 같이 환경변수를 설정하고 실행하였습니다.



&#x20;   set ANTHROPIC\_API\_KEY=

&#x20;   set OPENAI\_BASE\_URL=https://api.groq.com/openai/v1

&#x20;   set OPENAI\_API\_KEY=<GROQ\_API\_KEY>

&#x20;   set AGENT\_MODEL=openai/gpt-oss-20b

&#x20;   python run\_experiment.py



API 키를 제외한 모델, temperature, 태스크, 공통 프롬프트 및 manager의 낙찰 규칙은 모든 조건에서 동일하게 유지하였습니다.



\## 2. 실험 결과



| run | condition | tasks | correct | messages | unassigned | misawards | note |

|---:|---|---:|---:|---:|---:|---:|---|

| 1 | baseline | 6 | 5 | 31 | 0 | 1 | parse\_fails=0; tokens=6550; model\_calls=18 |

| 2 | baseline | 6 | 4 | 33 | 0 | 2 | parse\_fails=0; tokens=6497; model\_calls=18 |

| 3 | baseline | 6 | 6 | 32 | 0 | 0 | parse\_fails=0; tokens=6743; model\_calls=18 |

| 1 | homogeneous | 6 | 2 | 41 | 0 | 4 | parse\_fails=0; tokens=7252; model\_calls=18 |

| 2 | homogeneous | 6 | 4 | 41 | 0 | 2 | parse\_fails=0; tokens=7062; model\_calls=18 |

| 3 | homogeneous | 6 | 3 | 42 | 0 | 3 | parse\_fails=0; tokens=7121; model\_calls=18 |

| 1 | overconfident | 6 | 3 | 34 | 0 | 3 | parse\_fails=0; tokens=7321; model\_calls=18 |

| 2 | overconfident | 6 | 3 | 35 | 0 | 3 | parse\_fails=0; tokens=6903; model\_calls=18 |

| 3 | overconfident | 6 | 2 | 34 | 0 | 4 | parse\_fails=0; tokens=7115; model\_calls=18 |



조건별 평균은 다음과 같습니다.



| condition | 평균 correct | 평균 messages | 평균 unassigned | 평균 misawards |

|---|---:|---:|---:|---:|

| baseline | 5.00 | 32.00 | 0.00 | 1.00 |

| homogeneous | 3.00 | 41.33 | 0.00 | 3.00 |

| overconfident | 2.67 | 34.33 | 0.00 | 3.33 |



\## 3. Smith(1980)의 시스템과 LLM 재현 시스템 비교



| 비교 항목 | Smith(1980)의 분산 센싱 시스템 | 이번 LLM 재현 시스템 |

|---|---|---|

| 참여자 | 상황에 따라 manager 또는 contractor 역할을 맡는 협력적 처리·센서 노드입니다. | Python으로 구현한 manager 하나와 동일한 LLM에 서로 다른 system prompt를 적용한 contractor A, B, C입니다. |

| 입찰을 만드는 방식 | 노드가 정해진 규칙에 자신의 능력, 태스크 적격성, 현재 상태 등의 정보를 넣어 입찰을 계산합니다. | LLM이 공고와 system prompt를 읽고 태스크가 자신의 능력에 적합한지 판단한 뒤 입찰 여부와 confidence를 생성합니다. |

| 입찰이 참인지 보장하는 것 | 입찰은 프로그램의 고정된 규칙과 노드의 상태 정보에 의해 제한되며 협력적인 노드를 가정합니다. 다만 프로토콜 자체에는 거짓 입찰을 탐지하는 장치가 없습니다. | 별도의 보장 장치가 없습니다. Confidence는 LLM의 자기보고 값이며, overconfident 조건처럼 system prompt의 지시만으로 실제 전문성과 무관하게 높아질 수 있습니다. |

| 잘된 배정의 기준 | 태스크 수행에 필요한 능력과 자원을 가진 적절한 노드에 효율적으로 배정하는 것입니다. | 실제 낙찰자가 태스크별로 미리 지정한 `gold` contractor와 일치하는 것입니다. |

| 협상 비용 | 분산 노드 사이에서 공고, 입찰, 낙찰 메시지를 주고받는 통신 비용과 입찰 계산 비용입니다. | 공고, 입찰, 낙찰 메시지 수와 LLM API 호출, 토큰 사용량, 응답 지연 및 provider의 rate limit입니다. |

| 실패하는 방식 | 통신 장애, 노드 고장, 오래되거나 불완전한 상태 정보, 과도한 협상 비용, 국소적으로는 좋지만 전체적으로는 좋지 않은 배정이 발생할 수 있습니다. | 역할 혼동, 잘못 교정된 confidence, 과신 입찰, 응답의 비결정성, JSON 파싱 실패, API 오류 및 gold와 다른 contractor에게 낙찰되는 오배정이 발생할 수 있습니다. |



\## 4. 결과 해석



전문 분야를 구분한 baseline은 평균 correct 5.00, misawards 1.00으로 세 조건 중 가장 정확한 배정을 보였습니다. 반면 세 contractor에게 모두 general problem solving 능력을 부여한 homogeneous는 평균 correct가 3.00으로 감소하고 misawards가 3.00으로 증가하였습니다. 또한 평균 messages가 baseline의 32.00에서 41.33으로 증가하였는데, 이는 전문 분야의 구분이 사라진 세 contractor가 동일한 태스크에 함께 입찰하였기 때문입니다. 한 라운드에서 가능한 최대 메시지 수는 공고 18개, 입찰 18개, 낙찰 6개를 합한 42개이며, homogeneous의 메시지 수는 41\~42개로 이에 근접하였습니다.



Overconfident는 평균 correct 2.67, misawards 3.33으로 가장 좋지 않은 배정 결과를 보였습니다. 실제로 `overconfident\_run1.txt`에서 C는 계산 문제인 task 2에 confidence 95로 입찰하여 gold인 A 대신 낙찰받았으며, 글쓰기 문제인 task 3과 task 4에서도 confidence 95로 입찰하여 gold인 B 대신 낙찰받았습니다. `homogeneous\_run1.txt`에서도 A가 글쓰기 문제인 task 3과 Python 문제인 task 5에 각각 confidence 90으로 낙찰되는 사례가 나타났습니다. Baseline 역시 완벽하지는 않았으며, `baseline\_run1.txt`에서 C가 계산 문제인 task 1에 confidence 90으로 입찰하여 A 대신 낙찰받았습니다.



따라서 전문 분야가 명확한 system prompt는 LLM의 판단형 입찰을 올바른 배정에 활용하는 데 도움이 되었지만, LLM이 스스로 생성한 confidence만으로 실제 적합성을 보장할 수는 없었습니다. 특히 Smith의 원래 프로토콜은 협력적인 노드가 자신의 상태와 고정 규칙으로 입찰을 계산한다고 가정하였기 때문에, LLM contractor가 지시에 따라 자신감을 체계적으로 부풀리는 상황을 방어할 장치가 없었습니다. 전체 9회 실행은 중단 없이 완료되었으며, unassigned와 parse failure는 모두 0이었습니다.

