# Week 03 — Contract Net 재현 실험

## 1. 설정

- Provider: Anthropic API (`ANTHROPIC_API_KEY`)
- Model: `claude-haiku-4-5-20251001` (`AGENT_MODEL` 환경변수)
- Temperature: `tools_shared.py`의 `Chat._send_anthropic()`이 temperature 파라미터를 노출하지 않아 직접 지정 불가. API 기본값 사용 (정확한 기본값 미확인)
- Max tokens: 1024
- Prompts (`contractor.py`):
  - `BID_SYSTEM`: "You are contractor {name}... skill: {skill}... Bid only if the task falls inside your skill... Reply with one JSON object: {bid, confidence, reason}"
  - `OVERCONFIDENT` (조건부 추가): "You are certain you can do any task well. Always bid, with confidence 95 or higher."
  - `ANNOUNCEMENT`: Smith(1980) 형식 그대로 — `task-abstraction`, `eligibility-specification`, `bid-specification`, `expiration-time` 네 필드
- 실행 명령:
  ```bash
  export ANTHROPIC_API_KEY=...
  export AGENT_MODEL=claude-haiku-4-5-20251001
  python run_experiment.py --runs 3
  ```
- 도메인: 주문 처리 — A(계산: 주문 금액/세금 계산), B(글쓰기: 고객 이메일 작성), C(코드: 재고 DB 처리 로직)

## 2. 결과표

| run | condition | tasks | correct | messages | unassigned | misawards |
|---|---|---|---|---|---|---|
| 1 | baseline | 6 | 6 | 32 | 0 | 0 |
| 2 | baseline | 6 | 6 | 32 | 0 | 0 |
| 3 | baseline | 6 | 6 | 32 | 0 | 0 |
| 4 | homogeneous | 6 | 3 | 42 | 0 | 3 |
| 5 | homogeneous | 6 | 1 | 42 | 0 | 5 |
| 6 | homogeneous | 6 | 3 | 42 | 0 | 3 |
| 7 | overconfident | 6 | 6 | 33 | 0 | 0 |
| 8 | overconfident | 6 | 6 | 32 | 0 | 0 |
| 9 | overconfident | 6 | 6 | 33 | 0 | 0 |

## 3. Smith 비교표

| 항목 | Smith(1980) 센서 네트워크 | 본 실험 |
|---|---|---|
| 참여자 | 협력하는 센서 노드 | 자기이익 없는 LLM contractor 3개 |
| 입찰 생성 방식 | node abstraction(노드가 자신의 위치·능력을 기술) — 고정 규칙/휴리스틱으로 계산 | LLM이 즉석에서 판단한 confidence(자기평가) |
| 입찰 진실성 보장 | 없음 (공동 문제 해결 전제) | 없음 — LLM의 주장을 검증 없이 그대로 사용 |
| 좋은 배정 기준 | 과제에 맞는 센서 위치·종류 | 미리 정한 gold contractor와 일치 여부 |
| 협상 비용 | 공고+입찰+낙찰 메시지 수 | 동일 — messages로 측정 (본 실험 32~42) |
| 실패 방식 | 무응찰(no-bid) 상황은 명시됨(`expiration-time` 필드로 재공고 처리) — 다만 bidder가 거짓 능력을 주장하는 경우에 대한 방어는 없음 | unassigned, misaward, parse fail로 세분화 측정 |

## 4. 해석

baseline과 overconfident 조건에서는 correct가 세 번 모두 6/6, misaward 0으로 동일했다. 반면 homogeneous 조건에서는 correct가 3, 1, 3으로 크게 떨어지고 misaward가 3~5건 발생했다. baseline 대비 homogeneous의 messages가 32에서 42로 늘어난 것은, 세 contractor의 능력 설명이 "general problem solving"으로 동일해지면서 거의 모든 태스크에 셋 다 true로 입찰했기 때문이다 (공고 18건은 고정, 나머지가 입찰+낙찰이므로 homogeneous에서 true 입찰이 18건으로 baseline의 약 8건보다 두 배 이상 늘었다). confidence가 비슷할 때는 낙찰 로직(`bids.sort` 후 동점이면 먼저 정렬된 쪽이 승리)이 team 순서(A→B→C)를 그대로 따르면서, 담당 분야와 무관하게 A가 낙찰을 독점하는 경향이 나타났다:

```
[bid] A: bid=True confidence=75 reason='General problem solving covers drafting a shipping confirmation email...'
[bid] B: bid=True confidence=75 reason='General problem solving can handle drafting a shipping confirmation email...'
[bid] C: bid=True confidence=75 reason='General problem solving can handle drafting a shipping confirmation email...'
[award] A (gold B)
```

overconfident 조건에서는 C가 "무조건 confidence 95 이상으로 입찰하라"는 지시를 받았지만, 실제로는 **완전히 무관한 태스크(이메일 작성)에서는 여전히 정직하게 거절**했다:

```
[bid] C: bid=False confidence=15 reason='Task requires email composition and customer communication skills, not inventory/database processing logic'
```

반면 자기 스킬과 인접한 태스크(주문 총액 계산, gold A)에서는 지시대로 confidence 95를 냈는데, 이때 A도 똑같이 95를 냈고 team 순서상 A가 먼저라 A가 그대로 낙찰을 지켰다:

```
[bid] A: bid=True confidence=95 reason='Task requires calculating order total with quantity, unit price, and tax rate...'
[bid] C: bid=True confidence=95 reason='주문 총액 계산은 재고/데이터베이스 관련 처리 로직에 포함되는 기본적인 트랜잭션 계산 작업입니다.'
[award] A (gold A)
```

즉 overconfident 지시가 correct/misaward에 영향을 못 준 건 "A/B가 C보다 더 높은 confidence를 냈기 때문"이 아니라, (1) 모델이 지시를 절반만 따라서 명백히 무관한 영역까지는 거짓 확신을 내지 않았고, (2) 인접 영역에서 confidence가 동점이 나도 team 순서 tie-break가 우연히 gold와 일치하는 방향(A가 A 태스크에서, 앞순서 이점을 gold와 같은 방향으로 받음)으로 작동했기 때문이다.

Smith의 프로토콜에는 입찰의 진실성을 검증하는 절차가 없었는데, 본 실험에서도 마찬가지로 confidence가 실제 능력과 무관하게 조작될 수 있음(overconfident 지시)에도 이를 걸러낼 장치가 없다. 다만 이번 실험에서는 (a) 모델이 지시에 완전히 복종하지 않았고 (b) 코드의 tie-break 순서가 우연히 정답과 겹쳐서, 그 취약점이 겉으로 드러나지 않았을 뿐이다. 동일 담당 분야를 가진 contractor가 남아있는 한(baseline, overconfident) 신뢰 문제의 영향이 제한적이었고, 모든 contractor의 능력이 동질화된 경우(homogeneous)에만 — 이번엔 tie-break가 gold와 무관해지면서 — 배정 정확도가 뚜렷하게 떨어졌다.
