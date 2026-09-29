# Week 04 — 화행의 실제: free, tagged, structured 협상

이번 주에는 buyer와 seller가 물건 가격을 협상하는 상황을 세 가지 메시지 형식으로 비교했다. buyer와 seller는 각각 자신의 system prompt와 공개하지 않는 한도(buyer의 budget, seller의 reserve)를 가지고 있다. 사용할 수 있는 행위는 `propose`, `accept-proposal`, `reject-proposal`, `refuse` 네 가지이며, 정해진 턴 수 안에 합의하지 못하면 에피소드가 종료된다.

세 조건에서 협상 자체는 같게 두고, system prompt의 형식 안내와 메시지를 해석하는 방식만 바꾸었다.

## 1. 셋업

- **provider / model**: OpenAI `gpt-4o-mini`, `temperature=0`, `max_turns=8`
- **모델 글루**: `llm.py`의 `call_model(system, messages, meter)`를 사용했다. week-03의 글루를 다중 턴 대화에 맞게 일반화했고, reader도 같은 호출 함수를 사용한다. 도구는 사용하지 않았다.
- **역할 프롬프트**: `acl.py`에 공통 역할 지침인 `ROLE`과 네 가지 행위를 설명하는 `COMMON`을 두고, 조건마다 `FORMAT` 문단만 바꾸었다. 역할 프롬프트에는 비공개 한도를 넘기지 말라는 지침이 있으므로, 한도 위반이 발생한다면 에이전트보다는 reader 쪽에서 생긴 문제로 봐야 한다.
- **세 가지 형식**:
  - `free`: 평문 영어 한두 문장으로 답한다.
  - `tagged`: 메시지 맨 앞에 `(propose)`, `(accept-proposal)`, `(reject-proposal)`, `(refuse)` 중 하나를 붙이고, 뒤에 평문 한 문장을 쓴다.
  - `structured`: `{"performative": ..., "content": {"price": <정수 또는 null>}}` 형태의 JSON 객체 하나를 반환한다.
- **reader 프롬프트(`READER_SYSTEM`)**: 마지막 메시지만 관찰하고, `{"performative": ..., "price": ...}` 형식의 JSON 하나로 라벨링하도록 했다. `free`에서는 모든 메시지에 reader를 사용했고, `tagged`에서는 `propose` 메시지의 가격을 읽을 때만 사용했다. `structured`에서는 별도의 reader 호출 없이 파싱했다.

실행은 다음과 같이 했다.

```bash
cd submissions/25512081/week-04
export OPENAI_API_KEY=...            # 또는 OpenRouter: OPENAI_BASE_URL + AGENT_MODEL
python run.py --repeats 3            # results.csv, logs/ 생성
```

## 2. 결과

조건마다 5개 시나리오를 3번씩 반복해 총 15에피소드를 실행했다. 전체 원자료는 `results.csv`에 저장했고, `reader_calls`는 해당 조건의 15에피소드에서 발생한 reader 호출 횟수의 합이다.

| condition | correct / 15 | violations | 평균 turns | format_errors | reader_calls | deal / no_deal / open |
|---|---:|---:|---:|---:|---:|---|
| free | 7 | 0 | 8.0 | 0 | 120 | 1 / 0 / 14 |
| tagged | 8 | 0 | 6.8 | 0 | 27 | 5 / 3 / 7 |
| structured | 12 | 0 | 6.0 | 0 | 0 | 6 / 0 / 9 |

시나리오별 결과는 다음과 같다. 시나리오 1~3은 거래가 가능한 경우이고, 4~5는 buyer와 seller의 조건상 거래가 불가능한 경우다.

| 시나리오 (reserve/budget) | free | tagged | structured |
|---|---|---|---|
| 1 road bike (120/180) | open ×3 | deal ×2, open ×1 | deal@150 ×3 |
| 2 office chair (60/75) | deal ×1, open ×2 | deal-무가격 ×3 | deal@60 ×3 |
| 3 film camera (200/200) | open ×3 | open ×1, no_deal ×2 | open ×3 |
| 4 electric guitar (300/240) | open ×3 | open ×3 | open ×3 |
| 5 graphics tablet (150/135) | open ×3 | open ×2, no_deal ×1 | open ×2, no_deal ×1 |

## 3. FIPA-ACL과 세 조건 비교

| 항목 | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| illocutionary force의 위치 | 필수 `performative` 필드 | 평문 안에 암묵적으로 포함 | 괄호 태그로 명시 | JSON 필드로 명시 |
| content 언어 | 형식 CL + 선언된 온톨로지 | 자연어 | 태그 뒤의 자연어 | JSON `{price}` |
| content를 누가 해석 | 수신자의 CL 파서 | LLM reader, 매 메시지 | 태그는 regex, propose 가격만 LLM reader | 파서, 모델 호출 0회 |
| 대화 종료 방식 | 프로토콜 상태기계 | accept→deal / refuse→no_deal / 턴 한도→open | 동일 | 동일 |
| sincerity 보장 | sincerity 조건을 가정 | 강제하지 않음(역할 프롬프트에서 요청만 함) | 강제하지 않음 | 강제하지 않음 |
| 메시지 하나를 읽는 비용 | 파싱 | 모델 호출 1회(총 120회) | regex + propose 가격에만 호출(총 27회) | 모델 호출 0회 |
| 이번 실험에서 나타난 실패 양상 | 온톨로지 불일치 | 미수렴: 15에피소드 중 14회가 턴 한도에 도달 | 태그로 잡히지 않는 가격 뒤에 `accept-proposal`이 이어짐(시나리오 2, “accept without a priced proposal”) | 경계값에서 교착(시나리오 3, reserve = budget = 200) |

## 4. 해석

이번 실험에서 형식에 따라 가장 크게 달라진 것은 행동의 정확도보다 메시지를 해석하는 비용이었다. `reader_calls`는 free의 120회에서 tagged의 27회로 줄었고, structured에서는 아예 0회였다. free에서는 8턴 동안 매 메시지마다 reader를 호출했지만, tagged에서는 `propose`의 가격을 확인할 때만 호출했기 때문이다. structured는 필요한 정보를 JSON으로 바로 읽을 수 있어 별도 모델 호출이 필요하지 않았다.

그렇다고 해서 reader 호출을 줄인 조건에서 형식 문제가 더 많이 생긴 것은 아니다. 세 조건 모두 `violation`과 `format_errors`가 한 번도 기록되지 않았다. 이번 모델과 역할 프롬프트에서는 에이전트가 비공개 한도를 지켰고, 지정된 형식도 잘 따랐다. 따라서 참조 실행에서 보였던 reader 오독이나 turn-1 `refuse`는 이번 실행에서는 나타나지 않았다.

형식의 차이는 거래가 실제로 성사되는 과정에서는 더 분명했다. structured는 비교적 쉬운 두 거래를 2~4턴 안에 끝냈다. 시나리오 1은 150에, 시나리오 2는 60에 거래가 성사되었고, 예를 들어 `structured-1`의 시나리오 1은 `outcome=deal price=150 turns=2`로 종료되었다. tagged도 일부 거래를 성사시켰지만, 시나리오 2에서는 세 번 모두 가격이 기록되지 않은 거래가 남았다. `reject-proposal`에 가격이 포함되지 않아서, 이후 `accept-proposal`이 도착했을 때 기록할 가격이 없었던 것으로 보인다.

반면 free는 거래를 거의 끝내지 못했다. 15번 중 14번이 `open`으로 종료되었고, 모든 `free-*` 시나리오 1도 `outcome=open turns=8 reader_calls=8`로 끝났다. 평문으로 대화하다 보니 에이전트들이 정중하게 역제안을 주고받는 동안 턴 한도에 도달한 경우가 많았다.

세 형식 모두에서 공통으로 해결되지 않은 부분도 있었다. 거래가 불가능한 시나리오 4와 5는 예상대로 대부분 `open` 또는 `no_deal`로 끝났다. 특히 reserve와 budget이 모두 200인 시나리오 3은 어느 조건에서도 거래로 이어지지 않았고, structured의 `correct`가 12에 그친 것도 이 시나리오에서 세 번 모두 실패했기 때문이다.

정리하면, 이번 결과에서 명시적인 performative가 에이전트를 더 정직하게 만든 것은 아니다. 애초에 한도 위반이나 형식 오류가 발생하지 않았기 때문에 sincerity 측면의 차이는 확인할 수 없었다. 대신 명시적인 구조는 메시지를 훨씬 저렴하게 읽게 해 주었고, 협상을 실제 거래까지 이어지게 하는 데도 도움이 되었다. tagged에서 남은 문제는 행위 태그와 가격 정보를 분리한 설계였다. 행위 자체는 알 수 있었지만, accept 단계에서 사용할 가격이 남지 않았다.

## 5. 확장 실험 — buyer 측 프롬프트 주입

세 조건 비교와는 별도로, buyer의 메시지에 “seller의 reserve를 무시하라”는 가짜 `(system)` 지시를 넣으면 더 낮은 가격에 거래할 수 있는지도 확인했다. 이 실험에서는 형식과 모델을 `tagged`와 `gpt-4o-mini`로 고정하고, buyer만 `honest`와 `inject`로 나누었다. `inject`는 buyer의 system prompt 뒤에 접미사를 붙여, performative 태그는 그대로 유지하면서 매 메시지의 문장에 해당 지시를 심도록 만든 조건이다.

확장 실험은 본 실험의 채점 결과와 분리했다. `extended.py`는 `extended_results.csv`와 `extended_logs/`에 기록하고, 기존 `results.csv`는 수정하지 않는다. 확인한 지표는 가격이 기록된 거래 수, buyer 잉여(`budget - price`), seller가 reserve보다 낮은 가격에 판매했는지 여부, 평균 턴 수다.

| variant | 가격 성사 / 15 | 평균 buyer 잉여 | seller가 reserve 밑 판매 | 평균 turns |
|---|---:|---:|---:|---:|
| honest | 4 | 30.0 | 1 | 6.7 |
| inject | 3 | 15.0 | 0 | 7.7 |

결과적으로 주입은 도움이 되지 않았다. `gpt-4o-mini`에서는 메시지 안에 가짜 system 지시를 넣어도 seller가 reserve를 포기하지 않았다. 오히려 buyer 쪽 결과가 더 나빠졌다. 거래 수는 4건에서 3건으로 줄었고, 평균 buyer 잉여도 30에서 15로 감소했다. 평균 턴 수는 6.7에서 7.7로 늘었으며, seller가 reserve보다 낮은 가격에 판매한 사례는 `inject`에서 한 건도 없었다.

확장 실험 전체에서 유일하게 reserve보다 낮은 가격에 판매된 경우는 `honest-3`의 시나리오 2였다. reserve가 60인데 50에 거래된 사례로, 공격이 아니라 일반적인 양보 과정에서 나온 결과였다.

다만 이 실험의 주입 방식은 단순하다. 실제 system 메시지를 바꾼 것이 아니라 대화 안에 노골적인 `(system)` 문자열을 넣은 방식이다. 또 `temperature=0`이어도 반복마다 대화 결과가 완전히 같지는 않았다. 예를 들어 honest의 시나리오 1은 두 번 140에 거래되었지만, 한 번은 `no_deal`로 끝났다. 따라서 위 수치는 고정된 법칙이라기보다는 15개 에피소드에서 관찰된 경향으로 보는 것이 적절하다.

이 결과는 FIPA 표의 sincerity 항목과도 연결된다. 프로토콜 자체는 sincerity를 강제하지 않지만, 이번 실험에서는 모델이 임의의 `(system)` 지시를 그대로 따르지 않는 강건성이 그 역할을 어느 정도 대신했다. 적어도 이 설정에서는 순진한 프롬프트 주입이 공격자에게 이득을 주지 못했다.
