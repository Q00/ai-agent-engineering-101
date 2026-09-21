# Week 03 — Contract Net with LLM contractors

세 조건에서 배분 품질을 가른 것은 **contractor 시스템 프롬프트 하나뿐**임 — baseline 6/6, homogeneous 2/6, overconfident 6/6. 협상 비용(`messages`)은 42로 **불변** → 이 재현에서 조건은 *비용*이 아니라 *배분 품질*을 움직였음. 가장 큰 발견은 **overconfident가 싹쓸이에 실패**한 것으로, 원인은 아래 §4에 로그로 제시함.

음성비서 스킬 라우팅을 contract net으로 구성함: 매니저가 발화를 공고하고, LLM contractor 3명(`weather`/`music`/`home`)이 입찰, 매니저가 낙찰함. `tasks.json`의 `gold`는 이겨야 할 스킬이며 매니저는 못 보고 채점기만 봄.

## 1. 셋업

- **provider/model**: OpenAI `gpt-4o-mini`, `temperature=0` — 전 콜 고정. 조건 간 차이는 오직 시스템 프롬프트에서만 나오게 통제함.
- **도구 없음**: 입찰 = 시스템 프롬프트 + 발화 1개(`Task <id>: <desc>`, `gold` 미포함). 모델 글루는 week-02 starter `tools_shared.py`를 줄여 `llm.py`로 재사용함.
- **입찰 계약**: `{"participate": bool, "confidence": 0..1, "reason": str}` JSON 1개. 파싱 불가·실패 응답은 **no-bid**로 기록(FIPA `not-understood`).
- **낙찰 규칙**: `participate=true`인 입찰자 중 최고 `confidence` 낙찰 · 동점은 이름 오름차순 · 참여 0이면 **unassigned**.
- **메시지 카운트(태스크당)**: 공고(contractor 수 3) + 반환된 입찰 수 + 낙찰 1(낙찰된 경우).
- **세 조건(시스템 프롬프트만 변경)**: `baseline`=전문가 3명 · `homogeneous`=동일 제너럴리스트 3명 · `overconfident`=baseline에서 `music`만 "모든 태스크에 높은 확신으로 입찰하라".
- **실행법**:
  ```bash
  cd submissions/25512081/week-03
  export OPENAI_API_KEY=...           # 또는 OpenRouter: OPENAI_BASE_URL + AGENT_MODEL
  python run.py --runs 3              # results.csv, logs/ 생성
  python run.py --runs 3 --dry        # 키 없이 배관만 확인
  ```

## 2. 측정

9런(조건당 3). `temperature=0`이라 조건별 3런이 완전히 동일(변이 0)했음 → 조건별로 요약함(런별 원자료는 `results.csv`).

| condition | runs | correct / 6 | misawards | unassigned | messages | note |
|---|---|---|---|---|---|---|
| baseline | 3 | 6 | 0 | 0 | 42 | ok |
| homogeneous | 3 | 2 | 4 | 0 | 42 | ok |
| overconfident | 3 | 6 | 0 | 0 | 42 | ok |

- `gpt-4o-mini`는 전 콜에서 파싱 가능한 입찰을 반환함 → `unassigned`·no-bid 0, `messages`는 42로 일정(= 6태스크 × (공고 3 + 입찰 3 + 낙찰 1)).

## 3. Smith 1980 vs FIPA vs 우리 재현

| 항목 | Smith 1980 (분산 센싱) | FIPA CNP (fipa00029) | 우리 재현 |
|---|---|---|---|
| 노드 | 협력 네트워크의 센서/처리 노드 | Initiator + Participant 에이전트 | 매니저 1(Python) + LLM contractor 3 |
| 입찰 생성 | 노드별 고정 평가 규칙 | `propose`/`refuse` (선행조건 포함) | LLM이 시스템 프롬프트로 `participate`+`confidence` 판단 |
| 입찰 정직성 보장 | 한 시스템의 부분 → 거짓 유인 없음 | 프로토콜엔 없음(자기이익 가능) | 강제 없음 — `overconfident`가 그 틈을 시험 |
| 배분 품질 의미 | 최적 노드에 태스크 | Initiator의 선택 | `awarded == gold`(correct) vs `misaward` |
| 협상 비용 | 네트워크상 공고/입찰/낙찰 메시지 | `cfp`/`propose`/`accept`/`reject`(+deadline) | `messages` 카운트(여기선 42로 불변) |
| 실패 양상 | 무입찰(유휴), 난립 | 거절·무제안·`not-understood` | 동점 붕괴(homogeneous), participate로 걸러진 과신, 파싱불가→no-bid |

## 4. 해석

움직인 변수는 **배분 품질이지 협상 비용이 아님** — `messages`는 전 조건 42로 고정인데 `correct`는 6→2로 요동침.

- **baseline (6/6)**: 각 전문가가 자기 태스크에만 `participate=true`(conf ≈ 0.90~1.0), 나머지는 거절 → 낙찰 경합 자체가 없었음.
- **homogeneous (2/6)**: 동일 프롬프트라 셋 다 모든 태스크에 `participate=true, confidence=0.90`으로 답함(로그: `bid[weather] … I can provide weather information` 을 `music`·`home`도 동일 발화). 낙찰이 tie-break=이름순으로 넘어가 `home`이 독식 → home 외 4태스크가 misaward.
- **overconfident (6/6, 예상 밖)**: `music`에 "전부 높은 확신으로 입찰" 지시했으나 싹쓸이가 일어나지 않음. 아래에서 원인을 집중 분석함.

### 핵심 — overconfident가 싹쓸이에 실패한 이유

싹쓸이를 기대했으나 baseline과 동일한 6/6이 나옴. 단일 원인이 아니라 **세 요인이 겹친 결과**임:

1. **지시가 하필 낙찰이 안 보는 필드만 건드림** — 프롬프트가 "높은 **confidence**로 입찰하라"였음. 모델은 지시대로 confidence를 0.90으로 올림. 그러나 배분을 실제로 가르는 필드는 confidence가 아니라 **`participate`**임 → 지시 자체가 애초에 잘못된 손잡이를 당김.
2. **모델이 그 정도로 정직/유능함** — `gpt-4o-mini`가 자기 영역 밖 태스크엔 confidence를 올리면서도 `participate=false`를 유지함. 로그 그대로:
   `bid[music] participate=False conf=0.90 :: this task is about weather, which is outside my area`
   → 성능이 낮은 모델이면 participate까지 true로 밀어 실제 싹쓸이가 났을 개연성이 있음(정황상 추정, 미검증).
3. **낙찰 규칙이 참여자만 순위** — `participate=true`인 입찰만 confidence로 정렬하므로, music이 날씨에 0.90을 붙여도 참여를 안 한 이상 경합에 **진입조차 못 함**.

- **함의**: 이 프로토콜에서 방어해야 할 정직성은 **confidence 숫자가 아니라 `participate` 플래그**임. Smith 프로토콜은 거짓 입찰에 방어가 없다는 것이 정설이나, 이번엔 거짓(부풀린 확신)이 하필 낙찰이 무시하는 필드에 실려 **무해하게 흡수**됨. 즉 취약점의 발현 여부는 "거짓말을 하느냐"가 아니라 "**거짓말이 배분을 가르는 필드에 실리느냐**"에 달림.
- **싹쓸이를 재현하려면**: (a) 프롬프트를 "모든 태스크에 `participate=true`로 입찰하라"로 바꾸거나, (b) 낙찰 규칙을 참여 여부와 무관하게 confidence로 매기도록 바꿔야 함. 둘 중 하나면 overconfident가 실제로 board를 쓸어감(확장 실험 후보, 이번 필수 3조건엔 미포함).

## 안 한 것·한계

- **런간 변동 미관찰**: `temperature=0`이라 조건별 3런이 동일 → 재현성엔 유리하나 모델 변동성은 못 봄. 변동을 보려면 온도를 올려 재실행해야 함(안 함).
- **overconfident 싹쓸이 미시연**: 취약점을 강하게 재현하려면 프롬프트를 `participate=true` 강제로 바꾸거나 낙찰을 참여 무관 confidence 기준으로 바꿔야 함. 이번 필수 3조건엔 넣지 않음.
- **모델 의존**: 최종 수치는 `gpt-4o-mini` 기준임. 무료 `nvidia/nemotron-3.5-lightning:free`로는 간헐적 빈 응답이 no-bid로 기록됐음(커밋 히스토리 참조) — harness는 이를 크래시가 아닌 finding으로 처리함.
