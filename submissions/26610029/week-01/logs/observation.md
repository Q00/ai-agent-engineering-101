# Observations — Week 01

## 1. Model selection: README example no longer free

README에 예시로 나온 `meta-llama/llama-3.3-70b-instruct:free`를 처음 시도했으나,
OpenRouter에서 더 이상 무료로 제공되지 않아 404 에러가 발생했다:

> Error code: 404 - 'This model is unavailable for free. The paid version is
> available now - use this slug instead: meta-llama/llama-3.3-70b-instruct'

대신 OpenRouter의 Free Models Router(`openrouter/free`)로 교체하여 해결함 —
요청 특성(tool calling 등)에 맞는 무료 모델을 자동으로 골라주는 라우터.
이후 `AGENT_MODEL=openrouter/free`로 실행.

## 2. Observation: non-deterministic tool calls under ambiguous instructions

동일한 프롬프트("read notes.txt and calculate the total amount to report")를 세 번 실행한 결과, calculator에 전달된 수식이 매번 달랐다:

| 실행 | calculator 수식 | 결과 | 해석 |
|---|---|---|---|
| run1 | 48000 + 9500 - 12000 | 45500 | 환급분을 차감 (맥락 추론) |
| run2 | 48000 + 9500 + 12000 | 69500 | 참석자 수 제외, 나머지 합산 |
| run3 | 4 + 48000 + 9500 + 12000 | 69504 | 지시문 문자 그대로 전부 합산 |

notes.txt의 "위의 숫자들을 모두 더하여"라는 지시가 참석자 수까지 포함하는지,
그리고 "환급받은 금액"을 총액에 더할지 뺄지가 명시되지 않아 생긴 차이로 보인다.
같은 모델·같은 프롬프트에서도 매 실행마다 다른 그럴듯한 해석을 택했으며,
이는 LLM의 확률적 생성 특성과 프롬프트의 모호성이 결합된 결과로 보인다.

세 실행의 원본 콘솔 출력은 `logs/run2.log`, `logs/run3.log`

## 3. Observation: calculator crashes on date-formatted input

## Observation: same prompt produces three different calculator strategies

같은 시나리오("마감일을 읽고 오늘 날짜를 확인한 뒤 남은 일수 계산")를
세 번 실행한 결과, calculator에 전달된 수식과 결과가 매번 달랐다:

| 실행 | calculator 수식 | 결과 | 비고 |
|---|---|---|---|
| run4 (첫 시도, logs/run4.log) | `(2026-09-20) - (2026-09-08)` | 크래시 | 날짜 형식을 그대로 전달 |
| run5 (logs/run5.log) | `20 - 8` | 12일 | 날짜 숫자만 추출해서 계산 |

run4 — 크래시 원인
: calculator는 `ast.parse` 기반의 순수 산술 파서라 날짜 형식을 이해하지 못한다. 모델이 `(2026-09-20) - (2026-09-08)`를 그대로 전달하자, 파이썬이 `09`, `20` 같은 0으로 시작하는 숫자를 정수 리터럴로 파싱하지 못해 `SyntaxError`가 발생했고 프로그램 전체가 죽었다. calculator의 description("Evaluate an arithmetic expression")이 "날짜는 지원하지 않는다"는 제약을 명시하지 않아, 모델이 도구의 실제 한계를 모른 채 잘못된 입력을 넣어 크래시를 유발한 것으로 보인다.

run5 — 성공, 그러나 우연
: 재실행 시 모델은 날짜에서 숫자만 추출해 `20 - 8`로 계산했고, 결과(12일)는 실제로 맞았다. 
하지만 이는 오늘(9월 8일)과 마감일(9월 20일)이 우연히 같은 달이었기 때문이다. calculator가 날짜 차이 계산 기능이 없는 이상, 마감일이 다른 달이었다면 이 "숫자만 빼기" 전략도 틀린 결과를 냈을 것이다.

**결론**
: 동일한 프롬프트에서도 모델은 매 실행마다 (a) 날짜 형식을 그대로 산술식에 넣거나, (b) 날짜 숫자만 추출해 빼는 등 서로 다른 전략을 시도했다. 두 전략 모두 근본적으로 "날짜 계산"이라는 작업에 맞지 않는 도구(calculator)를 억지로 사용한 결과이며, 결과의 정확성이 입력값의 우연한 조건(같은 달 여부)에 좌우된다는 한계를 보여준다.