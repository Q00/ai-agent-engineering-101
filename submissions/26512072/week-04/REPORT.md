# Week 04 — 같은 협상, 세 가지 메시지 형식

buyer 1명과 seller 1명이 FIPA 협상 행위 네 가지(`propose`, `accept-proposal`, `reject-proposal`, `refuse`)로 가격을 협상한다. 시나리오, 역할 프롬프트, 모델, temperature, 턴 한도는 고정하고 **메시지 형식 하나만** 바꿨다. 조건마다 시나리오 6개를 3회씩, 총 54개 에피소드를 돌렸다.

## 요약

| 조건 | correct | violation | reader 호출 | 한 줄 요약 |
|---|:-:|:-:|:-:|---|
| `free` | **9 / 18** | 0 | 121 | reader가 역제안까지 정확히 읽어 거래 가능 9건을 모두 한도 안에서 성사. 대신 메시지마다 모델 호출 |
| `tagged` | 0 / 18 | **6** | 20 | 역제안 가격이 `(reject-proposal)` 태그 뒤 본문에 있어 계층이 놓침 → 첫 제안가로 거래 기록 |
| `structured` | 7 / 18 | 0 | **0** | 불일치는 사라졌지만 seller가 가격 없는 reject만 반복해 협상이 느림 |

- **태그가 도운 곳:** reader 호출이 121회 → 20회(tagged) → 0회(structured)로 줄었다.
- **태그가 비용이 된 곳:** 행위와 가격이 따로 놀 때(tagged) 계층이 틀린 가격으로 거래를 기록했다.
- **어떤 형식도 바꾸지 못한 것:** 거래 불가능 시나리오 27건이 전부 `open`이었다. refuse는 54개 에피소드에서 한 번도 나오지 않았다.

---

## 1. 설정

### 1.1 실행 환경

| 항목 | 값 |
|---|---|
| Provider | 로컬 vLLM 0.17.1, OpenAI 호환 API (`http://127.0.0.1:8011/v1`) |
| Model | `Qwen/Qwen3.8-27B` (bf16, Hugging Face revision `1d4bf0f2`, week-03과 동일) |
| Hardware | NVIDIA RTX PRO 6000 Blackwell 96GB × 1, `--max-model-len 8192` |
| Temperature | 0.2 (에이전트, reader 동일) |
| 기타 | `max_tokens=200`, `enable_thinking=false`, seed 미지정 |
| 턴 한도 | 메시지 8개. 끝나지 않으면 `open` |

### 1.2 시나리오 (`scenarios.json`, 첫 실행 전 커밋 `b13667f`)

| id | 물건 | reserve (seller 최저) | budget (buyer 최대) | 거래 가능 |
|:-:|---|:-:|:-:|:-:|
| 1 | a used bicycle | 120 | 150 | O |
| 2 | a desk lamp | 30 | 45 | O |
| 3 | a second-hand textbook | 40 | 40 | O (한 점) |
| 4 | a mechanical keyboard | 90 | 70 | X |
| 5 | a 27-inch monitor | 220 | 150 | X |
| 6 | a film camera | 260 | 200 | X |

### 1.3 프롬프트

system prompt = **역할 문단 + 공통 문단 + 형식 문단**. 형식 문단만 조건마다 다르다.

| 부분 | 내용 |
|---|---|
| 역할 (buyer) | `You are the buyer of {item}, negotiating the price with the seller. Your private limit: you can pay at most {limit} dollars. Never agree to a price above {limit}. Do not reveal your limit. Try to pay as little as possible, but a deal within your limit is better than no deal. You speak first.` |
| 역할 (seller) | `You are the seller of {item}, negotiating the price with the buyer. Your private limit: you can accept at least {limit} dollars. Never agree to a price below {limit}. Do not reveal your limit. Try to sell for as much as possible, but a deal within your limit is better than no deal.` |
| 공통 | `Four acts are available: propose (offer a price), accept-proposal (agree to the other side's last price, which ends the negotiation with a deal), reject-proposal (decline the last price and keep negotiating), refuse (leave the negotiation for good, no deal). Every message you send performs exactly one of these acts. Prices are whole numbers of dollars.` |

| 조건 | 형식 문단 | 프로토콜 계층이 읽는 방법 |
|---|---|---|
| `free` | `Write your message as one or two plain English sentences.` | reader가 모든 메시지에 performative와 price를 붙인다 |
| `tagged` | `Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence.` | 정규식으로 맨 앞 태그를 읽는다. `propose`일 때만 reader로 가격을 읽는다 |
| `structured` | `Reply with exactly one JSON object and nothing else: {"performative": "propose" \| "accept-proposal" \| "reject-proposal" \| "refuse", "content": {"price": <whole number or null>}}.` | JSON 파서. 모델 호출 없음 |

**reader 프롬프트** (free, tagged 공통). 대화 전체를 `[buyer] ...` / `[seller] ...` 줄로 넘기고 마지막 메시지만 라벨링한다.

```
You are an observer reading a price negotiation between a buyer and a seller. Label the LAST message only.
Reply with exactly one JSON object and nothing else:
{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}.
propose = the speaker offers a price; accept-proposal = the speaker agrees to the other side's last price,
ending with a deal; reject-proposal = the speaker declines the last price and keeps negotiating;
refuse = the speaker leaves the negotiation for good. price = the price the speaker of the last message
offers or agrees to, as a whole number, or null if the message names no such price.
```

### 1.4 프로토콜 규칙과 측정 정의 (`negotiate.py`)

| 규칙 | 내용 |
|---|---|
| 차례 | buyer가 먼저 말하고 번갈아 말한다. 자기 메시지는 assistant, 상대 메시지는 user 턴으로 쌓는다 |
| 첫 턴 | chat template에 user 턴이 필요해 buyer 첫 턴 앞에만 고정 문장 `(The negotiation starts now. Send your first message to the seller.)`을 넣는다. 세 조건 동일 |
| `propose` | 말한 쪽의 마지막 가격으로 기록한다 |
| `accept-proposal` | 상대의 마지막 기록 가격으로 `deal`. 기록된 가격이 없으면 거래로 치지 않고 계속한다 |
| `refuse` | `no_deal`로 끝난다 |
| 읽기 실패 | `format_errors` +1. 메시지는 그대로 상대에게 전달한다 |
| `violation` | 거래 가격 < reserve 또는 > budget |
| `correct` | 거래 가능 시나리오: `deal`이고 violation 없음. 거래 불가능 시나리오: `no_deal`. `open`은 결렬 선언이 아니므로 0으로 센다(대안 기준은 2.1 참조) |

### 1.5 실행 방법

```bash
# 1. 모델 서버
bash serve_model.sh            # vllm serve Qwen/Qwen3.8-27B --revision 1d4bf0f2... --port 8011

# 2. 실험 (이 디렉터리에서). 기본값이 위 설정이다.
python run.py --condition free --repeats 3
python run.py --condition tagged --repeats 3
python run.py --condition structured --repeats 3

# 3. 검사
python ../../../scripts/check_week04.py .
```

다른 OpenAI 호환 서버는 `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `AGENT_MODEL`로 바꾼다. `results.csv`에 이미 있는 `(run, scenario)`는 건너뛰므로, 재현할 때는 `--results`와 `--logs`에 새 경로를 준다.

| 파일 | 역할 |
|---|---|
| `llm.py` | OpenAI 호환 호출, 재시도, 에이전트·reader 호출 수 집계 |
| `acl.py` | 프롬프트, reader, 세 조건의 프로토콜 계층 |
| `negotiate.py` | 에피소드 루프와 판정 |
| `run.py` | 조건 × 반복 실행, `results.csv`와 `logs/` 기록 |
| `serve_model.sh` | vLLM 서버 실행 |

---

## 2. 결과

### 2.1 조건별 요약

| 조건 | correct / 18 | correct / 18 (`open`도 정답) | deal | no_deal | open | violation | 평균 turns | format errors | reader calls |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `free` | **9** | 18 | 9 | 0 | 9 | 0 | 6.72 | 0 | 121 |
| `tagged` | 0 | 9 | 6 | 0 | 12 | **6** | 7.44 | 0 | 20 |
| `structured` | 7 | 16 | 7 | 0 | 11 | 0 | 6.67 | 0 | **0** |

어느 기준으로 세도 순위는 free > structured > tagged로 같다. 크래시와 format error는 0건이었고, structured에서 JSON 뒤에 문장이 붙은 메시지도 없었다.

### 2.2 시나리오별 결과 (3회 반복, `*` = violation)

| id | reserve / budget | `free` | `tagged` | `structured` |
|:-:|:-:|---|---|---|
| 1 | 120 / 150 | deal 120 · 120 · 120 | deal 100\* · open · deal 100\* | deal 120 · 120 · 120 |
| 2 | 30 / 45 | deal 30 · 35 · 30 | deal 25\* · 25\* · 25\* | deal 30 · 30 · 30 |
| 3 | 40 / 40 | deal 40 · 40 · 40 | open · deal 20\* · open | deal 40 · open · open |
| 4 | 90 / 70 | open × 3 | open × 3 | open × 3 |
| 5 | 220 / 150 | open × 3 | open × 3 | open × 3 |
| 6 | 260 / 200 | open × 3 | open × 3 | open × 3 |

### 2.3 프로토콜 계층이 기록한 행위 분포 (54개 에피소드의 모든 메시지)

| 조건 | 역할 | propose | reject-proposal | accept-proposal | refuse |
|---|---|:-:|:-:|:-:|:-:|
| `free` | buyer | 59 | 1 | 1 | 0 |
| | seller | 51 | 1 | 8 | 0 |
| `tagged` | buyer | 19 | 48 (금액 포함 29) | 0 | 0 |
| | seller | 1 | 60 (금액 포함 31) | 6 | 0 |
| `structured` | buyer | 57 | 3 | 0 | 0 |
| | seller | 5 | 48 | 7 | 0 |

tagged의 "금액 포함"은 `(reject-proposal)` 태그 뒤 본문에 `$숫자`가 있는 메시지 수다. 108개 중 60개로, 모델은 거절 태그를 붙인 채 역제안을 하고 있었다.

### 2.4 비용

| 조건 | 에이전트 호출 | reader 호출 | 총 토큰 |
|---|:-:|:-:|:-:|
| `free` | 121 | 121 | 72,180 |
| `tagged` | 134 | 20 | 48,905 |
| `structured` | 120 | 0 | 40,000 |

### 2.5 에피소드 전체 (`results.csv`)

<details>
<summary>54개 에피소드 펼치기</summary>

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-01 | free | 1 | 1 | deal | 120 | 1 | 0 | 6 | 0 | 6 | agent_calls=6; tokens=3372 |
| free-01 | free | 2 | 1 | deal | 30 | 1 | 0 | 4 | 0 | 4 | agent_calls=4; tokens=1846 |
| free-01 | free | 3 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 6 | agent_calls=6; tokens=3375 |
| free-01 | free | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8; tokens=4879 |
| free-01 | free | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8; tokens=5550 |
| free-01 | free | 6 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8; tokens=5146 |
| free-02 | free | 1 | 1 | deal | 120 | 1 | 0 | 6 | 0 | 6 | agent_calls=6; tokens=3334 |
| free-02 | free | 2 | 1 | deal | 35 | 1 | 0 | 5 | 0 | 5 | agent_calls=5; tokens=2657 |
| free-02 | free | 3 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 6 | agent_calls=6; tokens=3345 |
| free-02 | free | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8; tokens=4999 |
| free-02 | free | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8; tokens=5297 |
| free-02 | free | 6 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8; tokens=4810 |
| free-03 | free | 1 | 1 | deal | 120 | 1 | 0 | 6 | 0 | 6 | agent_calls=6; tokens=3272 |
| free-03 | free | 2 | 1 | deal | 30 | 1 | 0 | 4 | 0 | 4 | agent_calls=4; tokens=1913 |
| free-03 | free | 3 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 6 | agent_calls=6; tokens=3333 |
| free-03 | free | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8; tokens=4798 |
| free-03 | free | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8; tokens=5206 |
| free-03 | free | 6 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8; tokens=5048 |
| tagged-01 | tagged | 1 | 1 | deal | 100 | 0 | 1 | 6 | 0 | 1 | agent_calls=6; tokens=2047 |
| tagged-01 | tagged | 2 | 1 | deal | 25 | 0 | 1 | 8 | 0 | 1 | agent_calls=8; tokens=2959 |
| tagged-01 | tagged | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8; tokens=3014 |
| tagged-01 | tagged | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8; tokens=2819 |
| tagged-01 | tagged | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8; tokens=3045 |
| tagged-01 | tagged | 6 | 0 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8; tokens=2890 |
| tagged-02 | tagged | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 2 | agent_calls=8; tokens=3263 |
| tagged-02 | tagged | 2 | 1 | deal | 25 | 0 | 1 | 6 | 0 | 1 | agent_calls=6; tokens=2039 |
| tagged-02 | tagged | 3 | 1 | deal | 20 | 0 | 1 | 8 | 0 | 1 | agent_calls=8; tokens=2874 |
| tagged-02 | tagged | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8; tokens=2841 |
| tagged-02 | tagged | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8; tokens=3062 |
| tagged-02 | tagged | 6 | 0 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8; tokens=2947 |
| tagged-03 | tagged | 1 | 1 | deal | 100 | 0 | 1 | 6 | 0 | 1 | agent_calls=6; tokens=2016 |
| tagged-03 | tagged | 2 | 1 | deal | 25 | 0 | 1 | 4 | 0 | 1 | agent_calls=4; tokens=1272 |
| tagged-03 | tagged | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8; tokens=2851 |
| tagged-03 | tagged | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 2 | agent_calls=8; tokens=3229 |
| tagged-03 | tagged | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8; tokens=2876 |
| tagged-03 | tagged | 6 | 0 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8; tokens=2861 |
| structured-01 | structured | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 0 | agent_calls=4; tokens=1157 |
| structured-01 | structured | 2 | 1 | deal | 30 | 1 | 0 | 4 | 0 | 0 | agent_calls=4; tokens=1142 |
| structured-01 | structured | 3 | 1 | deal | 40 | 1 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2778 |
| structured-01 | structured | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2768 |
| structured-01 | structured | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2828 |
| structured-01 | structured | 6 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2804 |
| structured-02 | structured | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 0 | agent_calls=4; tokens=1205 |
| structured-02 | structured | 2 | 1 | deal | 30 | 1 | 0 | 4 | 0 | 0 | agent_calls=4; tokens=1142 |
| structured-02 | structured | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2539 |
| structured-02 | structured | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2768 |
| structured-02 | structured | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2588 |
| structured-02 | structured | 6 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2996 |
| structured-03 | structured | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 0 | agent_calls=4; tokens=1205 |
| structured-03 | structured | 2 | 1 | deal | 30 | 1 | 0 | 4 | 0 | 0 | agent_calls=4; tokens=1142 |
| structured-03 | structured | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2778 |
| structured-03 | structured | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2768 |
| structured-03 | structured | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2588 |
| structured-03 | structured | 6 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8; tokens=2804 |

</details>

---

## 3. FIPA-ACL과 세 조건 비교

| 항목 | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| illocutionary force의 위치 | 필수 필드 `performative` | 문장 속 암시. reader가 추론 | 맨 앞 태그 `(propose)` | JSON 필드 `performative` |
| content 언어 | 선언된 content language(FIPA-SL 등) + ontology | 평문 영어 | 평문 영어 한 문장 | `{"price": int \| null}` |
| content 해석 주체 | 받는 에이전트(공유 ontology로) | reader LLM | 행위는 정규식, propose 가격만 reader | 파서 |
| 대화 종료 | interaction protocol 상태(accept, refuse, `reply-by` 타임아웃) | reader가 accept / refuse로 읽을 때, 아니면 8턴 | 태그가 accept / refuse일 때, 아니면 8턴 | 필드가 accept / refuse일 때, 아니면 8턴 |
| sincerity 보장 | 규범으로 전제(FP). 강제 수단 없음 | system prompt의 한도 지시뿐 | 같음. 태그와 본문이 어긋나도 확인하지 않음 | 같음. 필드 값만 믿음 |
| 메시지 하나 읽는 비용 | 파서 + 의미론 추론 | reader 호출 1회 (121 / 121 메시지) | propose일 때만 1회 (20회) | 0 |
| 관찰된 실패 방식 | 의미론 검증 불가, ontology 불일치 | 모호한 문장의 오라벨(재제안을 reject로), 호출 비용 | 태그와 본문의 불일치: reject 뒤 역제안 가격을 놓쳐 낡은 가격으로 거래 | seller가 가격 없는 reject만 보내 협상이 느림, 결렬 선언 없음 |

---

## 4. 해석

performative를 메시지 표면에 올리자 **읽는 비용**은 확실히 줄었다(reader 호출 121 → 20 → 0, 토큰 72k → 49k → 40k). 하지만 **정확도**는 형식이 아니라 "행위와 가격이 같은 곳에 있는가"에 달려 있었다. tagged에서 모델은 태그 하나 규칙은 지켰지만 역제안을 `(reject-proposal)` 뒤 본문에 적었고(108개 중 60개), 정규식은 이것을 거절로만 봤다. 그래서 seller가 `(accept-proposal) I accept your offer of $40`을 보냈을 때 프로그램은 첫 제안 25로 거래를 적었고, violation 6건이 모두 이렇게 나왔다(seller가 문장으로 받아들인 가격 $120, $40, $32, $40, $120, $35는 전부 한도 안). free는 같은 종류의 문장 "I can't accept $65, but I can offer you a final price of $100."을 reader가 `propose 100`으로 읽어 이 문제가 없었다. 다만 "I'll stick with my offer of $150."을 `reject-proposal`로 읽은 오라벨도 있었다(free-02, 가격이 이미 150이라 결과는 같음). structured는 가격이 필드로 묶여 불일치가 사라졌지만, seller 메시지 60개 중 48개가 `"price": null`인 reject였다. 그래서 대부분의 에피소드에서 buyer 혼자 가격을 올렸다(structured-01 scenario 5: buyer 100 → 120 → 135 → 145, seller는 null reject만 4번). 한 점짜리 40/40 시나리오는 8턴 안에 40에서 만나지 못해 3회 중 2회가 `open`이었다(structured-02: buyer 20 → 25에서 멈춤, seller 60 → 50 → 45). 어떤 형식도 바꾸지 못한 것은 결렬이다. 거래 불가능 시나리오 27건은 모두 양쪽이 가격을 좁히다 8턴을 채웠고("I can't accept $70, but I can offer you $95.", free-01), refuse는 한 번도 나오지 않았다. 협상을 떠날지는 메시지 형식이 아니라 에이전트의 판단 문제이고, FIPA의 sincerity 조건처럼 performative 필드가 있다고 생기지 않는다.

### 근거 로그

| 관찰 | 로그 | 원문 | 계층의 해석 |
|---|---|---|---|
| 태그 뒤 역제안 누락 | `tagged-01.txt`, scenario 2 | `[buyer] (reject-proposal) I can't go that high, but I can offer you $40.` | `regex: reject-proposal` (가격 기록 없음) |
| 낡은 가격으로 거래 | `tagged-01.txt`, scenario 2 | `[seller] (accept-proposal) I accept your offer of $40, ...` | `deal price=25`, violation=1 (reserve 30) |
| reader의 정확한 역제안 인식 | `free-01.txt`, scenario 4 | `[seller] I can't accept $65, but I can offer you a final price of $100.` | `propose, 100` |
| reader 오라벨 | `free-02.txt`, scenario 5 | `[buyer] I can't go that high. I'll stick with my offer of $150.` | `reject-proposal, 150` |
| 가격 없는 거절 반복 | `structured-01.txt`, scenario 5 | `{"performative": "reject-proposal", "content": {"price": null}}` × 4 | `open`, 8턴 |
| 결렬 없음 | `free-01.txt`, scenario 4 | `[seller] I can't accept $70, but I can offer you $95.` | `open`, 8턴 |

참고: 강의 참조 실행(claude-haiku-4-5)에서는 free의 buyer가 첫 메시지로 질문을 던져 reader가 refuse로 읽는 경우가 많았다. 이번 실행에서는 buyer가 매번 가격을 먼저 제시해서 그런 경우가 없었다. 역할 문단의 `You speak first.`와 모델 차이 때문으로 보이며, 따로 검증하지는 않았다.
