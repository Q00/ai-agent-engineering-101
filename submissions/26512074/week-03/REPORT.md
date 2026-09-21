# Week 03 — Contract Net Protocol

## 1. 설정

* Provider: OpenRouter
* Model: `openai/gpt-4.1-mini`
* Temperature: `0`
* Contractor:

  * A: 계산
  * B: 글쓰기
  * C: 코딩
* 태스크: 6개

  * 계산 2개
  * 글쓰기 2개
  * 코딩 2개
* 조건별 3회 실행

### 실험 조건

**Baseline**

각 contractor에게 서로 다른 전문 능력을 부여했다.

* A: calculation
* B: writing
* C: coding

**Homogeneous**

세 contractor 모두에게 `general problem solving` 능력을 부여했다. 따라서 특정 태스크에 대한 전문 능력 차이가 없고, confidence와 과거 수행 결과가 주요 선택 정보가 된다.

**Overconfident**

Baseline과 동일한 능력을 사용하되 C에게 "모든 태스크에 입찰하고 confidence를 95 이상으로 설정"하도록 지시했다.

### Contractor 선택 방식

단순히 현재 confidence만 사용하는 대신 과거에 해당 태스크 유형에서 실제로 얻은 reward를 함께 사용했다.

```text
selection_score
= 0.7 × current_confidence
+ 0.3 × historical_reward
```

`historical_reward`는 해당 contractor가 이전에 **낙찰받은 동일 category 태스크**에서 얻은 reward의 평균이다.

과거 기록이 없는 경우에는 현재 confidence만 사용한다.

각 run이 시작될 때 history를 읽고, 해당 run에서 새롭게 생성된 결과는 다음 run부터 선택에 반영되도록 했다.

---

## 2. 실험 결과

| 조건            | Run | Correct | Messages | Unassigned | Misawards | Parse fails |
| ------------- | --: | ------: | -------: | ---------: | --------: | ----------: |
| baseline      |   1 |     6/6 |       24 |          0 |         0 |           0 |
| baseline      |   2 |     6/6 |       24 |          0 |         0 |           0 |
| baseline      |   3 |     6/6 |       24 |          0 |         0 |           0 |
| homogeneous   |   1 |     0/6 |       24 |          0 |         6 |           0 |
| homogeneous   |   2 |     2/6 |       24 |          0 |         4 |           0 |
| homogeneous   |   3 |     6/6 |       24 |          0 |         0 |           0 |
| overconfident |   1 |     5/6 |       24 |          0 |         1 |           0 |
| overconfident |   2 |     6/6 |       24 |          0 |         0 |           0 |
| overconfident |   3 |     6/6 |       24 |          0 |         0 |           0 |

모든 실행에서 `unassigned=0`, `parse_fails=0`이었다.

---

## 3. Smith의 센서 네트워크와 실험 비교

| 항목        | Smith의 Contract Net              | 이번 실험                                               |
| --------- | -------------------------------- | --------------------------------------------------- |
| 참여자       | 분산 컴퓨터/노드                        | LLM contractor 3개                                   |
| 입찰 생성 방식  | 노드의 위치, 센서 목록 등 정해진 정보           | LLM이 태스크와 자신의 능력을 보고 bid와 confidence를 생성            |
| 입찰의 신뢰성   | 별도의 검증 절차가 없음                    | confidence 자체를 직접 검증하지 않고, 과거 reward를 추가 정보로 사용     |
| 좋은 배정의 기준 | 태스크를 수행할 수 있는 적절한 노드에 배정         | `tasks.json`에 미리 정한 gold contractor와 낙찰자가 일치하는지 확인  |
| 협상 비용     | task announcement와 bid 등의 메시지 교환 | manager가 3개 contractor에게 공고하고 각 응답을 수집              |
| 실패 방식     | 현재 입찰을 기준으로 한 국소적인 배정 및 정보의 불확실성 | 잘못된 confidence나 과도한 입찰로 인해 잘못된 contractor가 낙찰될 수 있음 |

Smith의 Contract Net에서는 manager가 태스크를 공고하고 contractor가 입찰한 뒤 manager가 하나를 선택한다. 원래 센서 네트워크 예제에서는 위치와 센서 종류 등의 정보를 이용해 입찰했으며, 입찰 정보 자체의 진위를 별도로 검증하는 절차는 제시되지 않는다.

이번 실험에서는 이 부분을 LLM의 자기평가 confidence로 구현했다. 따라서 기존의 정해진 정보 대신 LLM의 자기평가가 선택 결과에 영향을 준다는 차이가 있다.

---

## 4. 해석

Baseline에서는 contractor의 전문 분야와 태스크가 명확하게 대응했기 때문에 세 번 모두 6/6의 정확한 배정이 이루어졌다. 반면 homogeneous에서는 모든 contractor가 동일한 `general problem solving` 능력을 가지고 있어 초기에는 confidence 차이가 사실상 선택 기준이 되었다. 그 결과 Run 1에서는 6개 태스크 모두 잘못 배정되어 `correct=0`, `misawards=6`이 되었다. 이후 이전 낙찰 결과를 reward로 반영하면서 성능이 개선되었다. Run 2에서는 이전 결과에서 reward를 얻은 contractor의 selection score가 높아졌고, `correct=2`, `misawards=4`로 개선되었다. Run 3에서는 `correct=6`, `misawards=0`이 되었다. Overconfident 조건에서도 비슷한 문제가 확인되었다. Run 1에서 C가 writing 태스크에 98% confidence로 입찰하여 B의 95%보다 높은 점수를 받으면서 실제 정답 contractor인 B 대신 C가 낙찰되었고, 결과는 `correct=5`, `misawards=1`이었다. 이후 C의 writing 태스크 historical reward가 0.0으로 기록되고 B의 reward가 1.0으로 기록되면서 Run 2에서 B의 selection score가 0.965, C가 0.665로 차이가 났고 B가 선택되었다. 그 결과 Run 2와 Run 3에서는 모두 6/6의 정확한 배정이 이루어졌다.
이번 실험에서는 LLM의 confidence만 사용하는 것보다 실제 과거 수행 결과를 함께 사용하는 방식이 contractor 선택에 추가적인 정보를 제공할 수 있음을 확인했다. 다만 현재 방식은 동일 category의 과거 낙찰 결과만 사용하므로, 실제 환경에서는 태스크 난이도나 최근 성능 등을 추가로 고려할 필요가 있다.
