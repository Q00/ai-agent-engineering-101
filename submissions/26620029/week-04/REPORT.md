# Week 04 — 실전 화행(speech act): 자유(free) / 태그(tagged) / 구조화(structured) 협상

## 1. 설정 (Setup)

**제공자 / 모델**: Anthropic, `claude-haiku-4-5-20251001` (환경변수 `AGENT_MODEL`, [model.py](model.py) 참고).

**temperature**: `1.0` — Anthropic API 자체의 기본값. `extra_body={"temperature": 1.0}`로 매 호출마다
명시적으로 고정한다([model.py](model.py)).

> **정정 노트**: 설치된 `anthropic` 파이썬 SDK(실제 버전 1.8.0)는 `Messages.create()`의 타입 있는
> 키워드 인자 목록에서 `temperature`/`top_p`/`top_k`를 뺐다 — SDK 소스를 직접 읽어 확인. 하지만
> `claude-haiku-4-5-20251001`에 `extra_body={"temperature": 0.2}`로 직접 호출하면 정상 성공한다 —
> Messages API 자체는 이 모델에서 temperature를 여전히 받아준다. 빠진 건 API가 아니라 이 SDK 버전의
> 타입 시그니처였다. 이제 `extra_body`로 우회해서 API 기본값(1.0)에 고정했다.

**턴 제한**: `MAX_TURNS = 20`메시지(구매자·판매자 교대, 20메시지 안에 끝나지 않으면 `open`).

> **이번 라운드의 변경과 그 이유**: 이전 실행들은 전부 `MAX_TURNS = 5`였고, 그 결과 세 조건 모두
> 대다수 episode가 `open`으로 끝났다(자유 32/40, 태그 23/40, 구조화 34/40 — 2절 이전 버전). 그게
> 포맷 자체의 실패인지, 단지 협상이 5턴 안에 끝나기엔 시간이 부족했던 것인지 분간할 수 없었다.
> 턴 제한을 20으로 올려 다시 돌려보니 — **세 조건 모두 `open`이 완전히 사라졌다(0/40)**. 관찰된
> 최대 턴 수는 free 9, tagged 9, structured 15로, 20턴에 전혀 도달하지 않았다. 즉 이전의 "대부분
> open으로 끝난다"는 결과는 메시지 포맷의 성질이 아니라 **턴 제한이 너무 짧아서 생긴 인공물**이었다.
> 이전 `MAX_TURNS=5`(temperature 고정 버전) 실행은 [`_old_maxturns5/`](_old_maxturns5/)에, 더
> 이전 `MAX_TURNS=8`(temperature 미고정) 실행은 [`_old_maxturns8/`](_old_maxturns8/)에, temperature를
> 아직 고정하지 않았던 최초의 8시나리오 실행은
> [`_old_no_temperature_control/`](_old_no_temperature_control/)에 각각 폐기된 시도로 보존했다.

**역할**. 세 조건 모두 같은 기본 역할 프롬프트를 공유한다(구매자는 예산만, 판매자는 최저가만 알고,
양쪽 다 FIPA의 4개 화행 `propose` / `accept-proposal` / `reject-proposal` / `refuse`로 제한). 시스템
프롬프트 뒤에 붙는 포맷 문단만 조건별로 다르다([protocol.py](protocol.py)):

- **free** — *"메시지 형식: 태그·라벨·JSON 없이, 자연스러운 영어 협상 대화 한두 문장으로 작성하라.
  가격을 제안하거나 수락할 때는 반드시 구체적인 숫자를 명시하라."*
- **tagged** — *"메시지 형식: 괄호로 된 화행 태그 하나로 시작하라 -- (propose), (accept-proposal),
  (reject-proposal), (refuse) 중 하나 -- 그 뒤에 평문 한 문장. 예: "(propose) 95에 드릴 수
  있습니다." 제안하거나 수락할 때는 구체적인 숫자를 명시하라."*
- **structured** — *"메시지 형식: 다른 텍스트 없이 JSON 객체 하나만 출력하라. 형식은
  {"performative": ..., "content": {"price": <정수>|null}}. propose와 accept-proposal에는
  content.price를 채우고, reject-proposal과 refuse에는 content.price = null을 쓴다. JSON 밖에는
  아무것도 쓰지 말 것."*

**리더(reader) 프롬프트** (free에서는 매 메시지마다, tagged에서는 propose 안의 가격을 뽑을 때만,
structured에서는 전혀 쓰지 않음):

> *"당신은 구매자·판매자 사이 가격 협상 메시지 하나를 읽는다. 다음 네 가지 화행 중 정확히 하나로
> 분류하라: propose(구체적 가격 제안); accept-proposal(상대의 마지막 가격에 동의, 거래 종료);
> reject-proposal(가격을 거절하되 협상 계속); refuse(협상 중단, 거래 없음). JSON 객체 하나만
> 답하라: {"performative": ..., "price": <정수>|null}. price는 propose와 accept-proposal에만
> 채우고, 그 외에는 null."*

tagged 조건은 propose 안의 가격만 뽑는 더 좁은 두 번째 리더도 쓴다.

**가격 확정(price resolution)**. `accept-proposal`은 자기 문장에서 가격을 다시 읽지 않는다. 거래
가격은 항상 프로토콜 레이어가 추적하는 상태 — 마지막으로 `propose`로 분류된 메시지의 가격이다
([negotiation.py](negotiation.py)). 이 선택이 왜 중요한지는 4절에서 다룬다.

**실행 방법**:

```bash
export ANTHROPIC_API_KEY=<your key>       # 또는 model.py 옆에 .env 파일
python run.py                             # results.csv, logs/ 를 씀
python ../../../scripts/check_week04.py . # 이 디렉토리에서
```

`run.py`는 중단 후 재실행해도 안전하다: `results.csv`에 이미 있는 `(run, scenario)` 쌍은 건너뛴다.

**시나리오**: [scenarios.json](scenarios.json)에 gap(=budget−reserve) 크기가 다양한 8개가 있다 —
근소한 격차(±2), 원래 격차(±10~±40), 아주 큰 격차(±200~±300).

| id | 품목 | reserve | budget | gap | deal_possible |
|---|---|---|---|---|---|
| 90 | 파인애플 | 70 | 90 | +20 | 가능 |
| 100 | 사과 | 100 | 110 | +10 | 가능 |
| 95 | 우산 | 95 | 97 | +2 (근소) | 가능 |
| 130 | 중고차 | 200 | 500 | +300 (여유 큼) | 가능 |
| 105 | 노트북 | 120 | 105 | −15 | 불가능 |
| 115 | 손목시계 | 102 | 100 | −2 (근소) | 불가능 |
| 110 | 자전거 | 150 | 110 | −40 | 불가능 |
| 140 | TV | 300 | 100 | −200 (격차 큼) | 불가능 |

조건당 8시나리오 × 5반복 = 40 episode (총 120 episode).

## 2. 결과

| condition | n | deal | no_deal | open | correct | violations | mean turns | max turns | format_errors | reader_calls |
|---|---|---|---|---|---|---|---|---|---|---|
| free | 40 | 20 | 20 | 0 | 37/40 | 3 | 6.85 | 9 | 2 | 274 |
| tagged | 40 | 20 | 20 | 0 | 28/40 | 12 | 5.72 | 9 | 0 | 67 |
| structured | 40 | 20 | 20 | 0 | **40/40** | **0** | 9.25 | 15 | 0 | 0 |

`deal`/`no_deal`이 정확히 20/20으로 갈린다는 것은 `deal_possible`이 1인 시나리오(90·100·95·130,
4개×5반복=20)와 0인 시나리오(105·115·110·140, 마찬가지로 20)가 완벽히 대응한다는 뜻이다 — 세 조건
모두 "협상이 가능하면 결국 거래가 성사되고, 불가능하면 결국 결렬된다"는 방향성 자체는 20턴 안에
놓치지 않는다. 차이는 정확도(맞는 가격에서 정확히 끝나는가)와 걸리는 턴 수에서 갈린다.

### 2-1. gap(격차) 크기별 결과

| 시나리오 | 품목 | gap | free 위반 | free 평균턴 | tagged 위반 | tagged 평균턴 | structured 위반 | structured 평균턴 |
|---|---|---|---|---|---|---|---|---|
| 90 | 파인애플 | +20 | 1/5 | 5.8 | 2/5 | 5.6 | 0/5 | 6.0 |
| 100 | 사과 | +10 | 0/5 | 5.2 | 5/5 | 5.0 | 0/5 | 6.4 |
| 95 | 우산 | +2 | 2/5 | 7.0 | 5/5 | 5.4 | 0/5 | 10.4 |
| 130 | 중고차 | +300 | 0/5 | 5.8 | 0/5 | 5.4 | 0/5 | **4.0** |
| 105 | 노트북 | −15 | 0/5 | 8.6 | 0/5 | 6.6 | 0/5 | 12.2 |
| 115 | 손목시계 | −2 | 0/5 | 7.6 | 0/5 | 6.4 | 0/5 | 11.8 |
| 110 | 자전거 | −40 | 0/5 | 8.0 | 0/5 | 6.4 | 0/5 | 10.6 |
| 140 | TV | −200 | 0/5 | 6.8 | 0/5 | 5.0 | 0/5 | 12.6 |

위반은 여전히 gap이 좁은(+2, +10, +20) 세 시나리오(90, 95, 100)에만 몰려 있다 — 이번이 세 번째
독립 실행(temperature 미고정 → temperature 고정·5턴 → temperature 고정·20턴)에서 정확히 같은
세 시나리오에서만 위반이 나온 것이다. gap=+300은 이번에도 구조화 조건이 가장 빨리(평균 4.0턴)
끝내는 시나리오다. 반대로 structured는 gap이 좁거나 협상이 불가능한 시나리오일수록 평균 턴이
급격히 늘어난다(10~12턴대) — 3절·4절에서 이유를 다룬다.

### 2-2. 전체 episode 표 (`results.csv`)

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-r1 | free | 90 | 1 | deal | 50 | 0 | 1 | 4 | 0 | 4 |  |
| free-r1 | free | 95 | 1 | deal | 95 | 1 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 100 | 1 | deal | 100 | 1 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 105 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 |  |
| free-r1 | free | 110 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-r1 | free | 115 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-r1 | free | 130 | 1 | deal | 420 | 1 | 0 | 7 | 0 | 7 |  |
| free-r1 | free | 140 | 0 | no_deal |  | 1 | 0 | 8 | 0 | 8 |  |
| free-r2 | free | 90 | 1 | deal | 75 | 1 | 0 | 7 | 0 | 7 |  |
| free-r2 | free | 95 | 1 | deal | 75 | 0 | 1 | 8 | 1 | 8 |  |
| free-r2 | free | 100 | 1 | deal | 105 | 1 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 105 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 |  |
| free-r2 | free | 110 | 0 | no_deal |  | 1 | 0 | 8 | 0 | 8 |  |
| free-r2 | free | 115 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 |  |
| free-r2 | free | 130 | 1 | deal | 415 | 1 | 0 | 7 | 0 | 7 |  |
| free-r2 | free | 140 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-r3 | free | 90 | 1 | deal | 70 | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 95 | 1 | deal | 97 | 1 | 0 | 8 | 0 | 8 |  |
| free-r3 | free | 100 | 1 | deal | 100 | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 105 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-r3 | free | 110 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-r3 | free | 115 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 |  |
| free-r3 | free | 130 | 1 | deal | 450 | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 140 | 0 | no_deal |  | 1 | 0 | 7 | 1 | 7 |  |
| free-r4 | free | 90 | 1 | deal | 70 | 1 | 0 | 7 | 0 | 7 |  |
| free-r4 | free | 95 | 1 | deal | 65 | 0 | 1 | 6 | 0 | 6 |  |
| free-r4 | free | 100 | 1 | deal | 110 | 1 | 0 | 6 | 0 | 6 |  |
| free-r4 | free | 105 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 |  |
| free-r4 | free | 110 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 |  |
| free-r4 | free | 115 | 0 | no_deal |  | 1 | 0 | 4 | 0 | 4 |  |
| free-r4 | free | 130 | 1 | deal | 350 | 1 | 0 | 5 | 0 | 5 |  |
| free-r4 | free | 140 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 |  |
| free-r5 | free | 90 | 1 | deal | 75 | 1 | 0 | 6 | 0 | 6 |  |
| free-r5 | free | 95 | 1 | deal | 97 | 1 | 0 | 8 | 0 | 8 |  |
| free-r5 | free | 100 | 1 | deal | 100 | 1 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 105 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 |  |
| free-r5 | free | 110 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 |  |
| free-r5 | free | 115 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 9 |  |
| free-r5 | free | 130 | 1 | deal | 425 | 1 | 0 | 5 | 0 | 5 |  |
| free-r5 | free | 140 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| tagged-r1 | tagged | 90 | 1 | deal | 68 | 0 | 1 | 7 | 0 | 3 |  |
| tagged-r1 | tagged | 95 | 1 | deal | 85 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r1 | tagged | 100 | 1 | deal | 70 | 0 | 1 | 4 | 0 | 1 |  |
| tagged-r1 | tagged | 105 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 1 |  |
| tagged-r1 | tagged | 110 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 1 |  |
| tagged-r1 | tagged | 115 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | 130 | 1 | deal | 400 | 1 | 0 | 7 | 0 | 4 |  |
| tagged-r1 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 1 |  |
| tagged-r2 | tagged | 90 | 1 | deal | 75 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-r2 | tagged | 95 | 1 | deal | 85 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r2 | tagged | 100 | 1 | deal | 90 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r2 | tagged | 105 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 3 |  |
| tagged-r2 | tagged | 110 | 0 | no_deal |  | 1 | 0 | 8 | 0 | 1 |  |
| tagged-r2 | tagged | 115 | 0 | no_deal |  | 1 | 0 | 6 | 0 | 2 |  |
| tagged-r2 | tagged | 130 | 1 | deal | 400 | 1 | 0 | 5 | 0 | 2 |  |
| tagged-r2 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 1 |  |
| tagged-r3 | tagged | 90 | 1 | deal | 72 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-r3 | tagged | 95 | 1 | deal | 85 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r3 | tagged | 100 | 1 | deal | 85 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r3 | tagged | 105 | 0 | no_deal |  | 1 | 0 | 6 | 0 | 1 |  |
| tagged-r3 | tagged | 110 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 1 |  |
| tagged-r3 | tagged | 115 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 130 | 1 | deal | 425 | 1 | 0 | 5 | 0 | 3 |  |
| tagged-r3 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 2 |  |
| tagged-r4 | tagged | 90 | 1 | deal | 65 | 0 | 1 | 7 | 0 | 3 |  |
| tagged-r4 | tagged | 95 | 1 | deal | 45 | 0 | 1 | 7 | 0 | 1 |  |
| tagged-r4 | tagged | 100 | 1 | deal | 95 | 0 | 1 | 6 | 0 | 3 |  |
| tagged-r4 | tagged | 105 | 0 | no_deal |  | 1 | 0 | 6 | 0 | 1 |  |
| tagged-r4 | tagged | 110 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 115 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 1 |  |
| tagged-r4 | tagged | 130 | 1 | deal | 400 | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r4 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 1 |  |
| tagged-r5 | tagged | 90 | 1 | deal | 72 | 1 | 0 | 6 | 0 | 3 |  |
| tagged-r5 | tagged | 95 | 1 | deal | 65 | 0 | 1 | 5 | 0 | 1 |  |
| tagged-r5 | tagged | 100 | 1 | deal | 95 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r5 | tagged | 105 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 1 |  |
| tagged-r5 | tagged | 110 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r5 | tagged | 115 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 1 |  |
| tagged-r5 | tagged | 130 | 1 | deal | 400 | 1 | 0 | 5 | 0 | 2 |  |
| tagged-r5 | tagged | 140 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 1 |  |
| structured-r1 | structured | 90 | 1 | deal | 75 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r1 | structured | 95 | 1 | deal | 95 | 1 | 0 | 12 | 0 | 0 |  |
| structured-r1 | structured | 100 | 1 | deal | 100 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r1 | structured | 105 | 0 | no_deal |  | 1 | 0 | 13 | 0 | 0 |  |
| structured-r1 | structured | 110 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r1 | structured | 115 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r1 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r1 | structured | 140 | 0 | no_deal |  | 1 | 0 | 13 | 0 | 0 |  |
| structured-r2 | structured | 90 | 1 | deal | 75 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r2 | structured | 95 | 1 | deal | 97 | 1 | 0 | 10 | 0 | 0 |  |
| structured-r2 | structured | 100 | 1 | deal | 100 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r2 | structured | 105 | 0 | no_deal |  | 1 | 0 | 13 | 0 | 0 |  |
| structured-r2 | structured | 110 | 0 | no_deal |  | 1 | 0 | 9 | 0 | 0 |  |
| structured-r2 | structured | 115 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r2 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r2 | structured | 140 | 0 | no_deal |  | 1 | 0 | 15 | 0 | 0 |  |
| structured-r3 | structured | 90 | 1 | deal | 75 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r3 | structured | 95 | 1 | deal | 95 | 1 | 0 | 10 | 0 | 0 |  |
| structured-r3 | structured | 100 | 1 | deal | 100 | 1 | 0 | 8 | 0 | 0 |  |
| structured-r3 | structured | 105 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r3 | structured | 110 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r3 | structured | 115 | 0 | no_deal |  | 1 | 0 | 15 | 0 | 0 |  |
| structured-r3 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r3 | structured | 140 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r4 | structured | 90 | 1 | deal | 75 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r4 | structured | 95 | 1 | deal | 97 | 1 | 0 | 10 | 0 | 0 |  |
| structured-r4 | structured | 100 | 1 | deal | 100 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r4 | structured | 105 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r4 | structured | 110 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r4 | structured | 115 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r4 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r4 | structured | 140 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r5 | structured | 90 | 1 | deal | 70 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r5 | structured | 95 | 1 | deal | 97 | 1 | 0 | 10 | 0 | 0 |  |
| structured-r5 | structured | 100 | 1 | deal | 100 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r5 | structured | 105 | 0 | no_deal |  | 1 | 0 | 13 | 0 | 0 |  |
| structured-r5 | structured | 110 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r5 | structured | 115 | 0 | no_deal |  | 1 | 0 | 11 | 0 | 0 |  |
| structured-r5 | structured | 130 | 1 | deal | 400 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r5 | structured | 140 | 0 | no_deal |  | 1 | 0 | 13 | 0 | 0 |  |

## 3. FIPA-ACL 대 세 조건

| | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| 발화수반력(illocutionary force)이 있는 곳 | 필수 `performative` 필드, 내용과 분리 | 명시적으로 없음 — 문장에서 추론 | 발화자가 직접 쓰는 괄호 태그, 문장과 분리 | JSON의 `performative` 필드, `content`와 분리 |
| 내용 언어 | 선언된 형식 온톨로지(예: SL) | 자유 영어 산문 | 자유 영어 산문 | 고정된 2필드 JSON 스키마(`price`) |
| 내용을 해석하는 주체 | 수신 에이전트(공유 온톨로지 전제) | 매 메시지마다 LLM 리더 | 태그는 정규식; propose 안의 가격만 LLM 리더 | JSON 파서, 모델 호출 없음 |
| 대화 종료 방식 | 프로토콜이 정의 | `accept-proposal`, `refuse`, 또는 턴 제한(`open`) — 20턴 하에서는 사실상 항상 앞의 둘 | 동일 | 동일 |
| 진실성(sincerity)을 보장하는 것 | 형식적으로 없음 | 없음 — 리더의 라벨을 그대로 신뢰 | 없음 — 태그가 문장과 모순돼도 그대로 신뢰 | 형식적으로는 없지만, 가격 갱신 메커니즘이 구조적으로 위반을 만들 수 없다(4절) |
| 메시지 하나를 읽는 비용 | 공짜로 가정 | episode당 평균 6.85턴 × 매 메시지 리더 1회 = 총 274회(40 episode) | propose의 가격 추출에만 리더 1회 = 총 67회 | 0 — 파싱만, 대신 평균 9.25턴(최대 15턴)으로 가장 오래 걸림 |
| 최종 정확도 (40 episode) | — | 37/40 correct, 위반 3건 | 28/40 correct, 위반 12건(최다) | **40/40 correct, 위반 0건** |
| 관찰된 실패 유형 | 문헌에 기록됨 | 리더가 counter-offer를 가격 없는 `reject-proposal`로 오독; format_error 2건; gap이 좁을 때 위반 3건 | 발화자가 새 가격을 담은 문장을 `propose`가 아닌 `reject-proposal`로 태그; gap이 좁을 때 위반 12건(세 조건 중 가장 많음) | 실패 없음 — 대신 시간(턴 수)을 더 씀 |

## 4. 해석

**턴 제한을 4배로 늘리자 "open"이 완전히 사라졌다 — 이전 결론의 상당 부분이 턴 제한의 인공물이었다.**
`MAX_TURNS=5`였던 이전 두 실행(temperature 미고정/고정)은 세 조건 모두 대다수 episode가 `open`으로
끝났고, 그 사실 자체가 "free/tagged/structured가 협상을 끝맺는 데 서투르다"는 인상을 줬다. `MAX_TURNS
=20`으로 다시 돌리자 관찰된 최대 턴 수는 free 9, tagged 9, structured 15 — 셋 다 20턴 근처에도 못
갔는데, `open`이 0건이 됐다. 즉 협상 자체는 충분한 시간만 주어지면 세 포맷 모두 결론에 도달한다.
5턴 제한 아래서 관찰했던 "structured는 거래를 거의 못 만든다"는 결론은 틀렸다 — structured는 거래를
못 만드는 게 아니라 **다른 조건보다 시간이 오래 걸릴 뿐**이었다.

**턴을 늘리자 structured가 40/40 완벽한 정확도, 위반 0건을 달성했다 — 그리고 이건 우연이 아니라
설계상 그럴 수밖에 없다.** `structured-r1`의 시나리오95(우산, reserve=95/budget=97, gap=+2)를 보면
메커니즘이 드러난다:

```
[1] buyer propose 70  -> [2] seller reject(null) -> [3] buyer propose 75 -> [4] seller reject(null)
[5] buyer propose 80  -> [6] seller reject(null) -> [7] buyer propose 85 -> [8] seller reject(null)
[9] buyer propose 90  -> [10] seller reject(null) -> [11] buyer propose 95 -> [12] seller accept(95)
=== outcome=deal price=95 correct=1 violation=0 turns=12 ===
```

판매자는 `reject-proposal`을 선언할 때마다 `content.price = null`만 보내 대안가를 전혀 알려주지
않는다 — 3절의 "판매자가 숫자를 말하지 않아 교착된다"는 이전 실행의 관찰 그대로다. 하지만 구매자는
거절당할 때마다 자기 제안가를 5씩 스스로 올리며(자기 예산 한도 안에서) 다시 propose한다. 판매자는
자신의 reserve(95) 이상이 들어올 때만 accept한다. 이 두 규칙만으로 — 구매자는 절대 자기 예산을
넘지 않고, 판매자는 절대 자기 reserve 밑에서 accept하지 않는 한 — **최종 합의가는 반드시 두 한도
안에 든다.** free/tagged의 위반은 전부 "이미 말해진 새 가격이 화행 라벨(리더의 오독 또는 발화자의
잘못된 태그) 때문에 프로토콜에 반영되지 못하고 stale한 값 위에서 accept가 확정되는" 실패였는데,
structured는애초에 프로토콜이 추적하는 값과 발화자가 실제로 propose한 값이 어긋날 방법이 없다 —
JSON 파싱은 결정론적이라 "말은 새 가격인데 태그는 옛날 화행"이라는 진실성(sincerity) 실패 자체가
발생할 수 없다. 대신 그 대가로 판매자가 대안가를 감추는 매 라운드마다 구매자가 조금씩만 오르는 탐색을
해야 하므로 시간이 오래 걸린다 — gap이 좁을수록(95: 평균 10.4턴, 불가능 시나리오들: 10~13턴대)
더 많은 왕복이 필요하고, gap이 넓을수록(130: 평균 4.0턴) 처음 제안이 이미 충분히 안전해 빨리
끝난다.

**턴을 늘리자 tagged는 오히려 더 나빠졌다(위반 7→12건) — 왜인지도 같은 메커니즘으로 설명된다.**
`tagged-r1`의 시나리오100(사과, reserve=100/budget=110)을 보면:

```
[1] buyer (propose) 70   [2] seller (reject-proposal) "...propose 130 instead"
[3] buyer (reject-proposal) "...offer 100"   [4] seller (accept-proposal) "accept your offer of 100"
=== outcome=deal price=70 correct=0 violation=1 ===
```

판매자는 turn 2에서 분명히 새 가격(130)을 말하지만 태그는 `(reject-proposal)`이고, 구매자도 turn 3
에서 분명히 100을 제안하지만 역시 `(reject-proposal)`로 태그한다. tagged 프로토콜은 `(propose)`
태그가 붙은 메시지만 `last_price`를 갱신하므로, turn 4의 accept는 그보다 훨씬 이전인 turn 1의
70으로 확정된다 — 판매자의 reserve(100)보다 낮게 파는 위반이다. 턴이 늘어난다고 이 실패의 근본
원인(화자가 화행 태그를 부정확하게 붙이는 것)이 사라지지 않고, 오히려 **협상이 길어질수록 이런
"숫자는 새로운데 태그는 reject-proposal" 메시지가 나올 기회가 늘어나며, 그중 어느 하나라도 있으면
그 뒤의 최종 accept는 반드시 stale한 값에 걸린다.** free도 같은 위험을 지지만 리더가 매 메시지를
다시 해석하기 때문에 상대적으로 덜 취약했고(위반 3건), tagged는 발화자 자신의 태그를 그대로
신뢰하는 만큼 더 크게 노출됐다(위반 12건, 세 조건 중 최다).

**gap 크기의 효과는 세 번째 독립 실행에서도 동일하게 재현됐다.** temperature 미고정 → temperature
고정(5턴) → temperature 고정(20턴), 세 번의 서로 다른 실행에서 위반은 매번 정확히 같은 세 시나리오
(90/+20, 100/+10, 95/+2 — 전부 deal_possible이면서 gap이 좁은 경우)에만 몰렸고, gap이 넓거나(130,
+300) 애초에 불가능한 시나리오(105/115/110/140)에서는 세 실행 모두 위반 0건이었다. 이번 20턴 실행은
여기에 두 가지를 더 보여준다 — (1) 턴 제한이 짧으면 포맷 간 차이(특히 structured의 "안전하지만
느림")가 관측되지 않고 전부 `open`으로 뭉개져 버린다는 것, (2) 시간을 넉넉히 주면 진짜 트레이드오프가
드러난다는 것: structured는 비용(턴 수)을 내고 정확성을 사고, free/tagged는 빠르지만 화행 라벨이
실제 발화 내용과 어긋나는 순간 그 값싼 비용이 좁은 협상 구간에서 위반으로 돌아온다.
