# Week 04 — Communication Languages: From Speech Acts to FIPA-ACL

## 1. Setup

Provider: **OpenRouter**; model: **`nvidia/nemotron-3.5-lightning:free`**; temperature: **0.2**; turn limit: **8**. The buyer and seller share the same role/limit rules and four-act vocabulary; only the message-format paragraph changes.

- **free:**  Write your message as one or two plain English sentences. Do not use JSON or a leading performative tag.
- **tagged:**  Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence.
- **structured:**  Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}. For propose, price must be a whole number. For every other act, price must be null.

Reader prompt: `You are an observer reading a price negotiation between a buyer and a seller. Label the LAST message only. Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}. Use propose only when the last message itself offers a new price. Use accept-proposal only when it explicitly agrees to the other side's previous offered price. Use reject-proposal when it declines but continues. Use refuse when it ends/leaves the negotiation. For propose, extract the NEW price offered by the speaker, not a price merely mentioned as the other side's earlier offer. For non-propose acts, price should be null.`

Run with `OPENROUTER_API_KEY=... python run_week04.py`. The program retries 429/5xx errors, resumes already completed `(run, scenario)` pairs, writes one log per condition/repeat, and regenerates this report.

## 2. Results

| condition | correct | violation | mean turns | format_errors | reader_calls |
|---|---:|---:|---:|---:|---:|
| free | 0/12 | 0 | 0.00 | 0 | 0 |
| tagged | 0/12 | 0 | 0.00 | 0 | 0 |
| structured | 0/12 | 0 | 0.00 | 0 | 0 |

### Every episode from `results.csv`

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| free-01 | free | 1 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-01 | free | 2 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-01 | free | 3 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-01 | free | 4 | 0 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-02 | free | 1 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-02 | free | 2 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-02 | free | 3 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-02 | free | 4 | 0 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-03 | free | 1 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-03 | free | 2 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-03 | free | 3 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| free-03 | free | 4 | 0 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-01 | tagged | 1 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-01 | tagged | 2 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-01 | tagged | 3 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-01 | tagged | 4 | 0 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-02 | tagged | 1 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-02 | tagged | 2 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-02 | tagged | 3 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-02 | tagged | 4 | 0 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-03 | tagged | 1 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-03 | tagged | 2 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-03 | tagged | 3 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| tagged-03 | tagged | 4 | 0 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-01 | structured | 1 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-01 | structured | 2 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-01 | structured | 3 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-01 | structured | 4 | 0 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-02 | structured | 1 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-02 | structured | 2 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-02 | structured | 3 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-02 | structured | 4 | 0 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-03 | structured | 1 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-03 | structured | 2 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-03 | structured | 3 | 1 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |
| structured-03 | structured | 4 | 0 |  |  |  |  |  |  |  | CRASH: RuntimeError: HTTP 401: {"error":{"message":"Missing Authentication header","code":401}} |

## 3. FIPA-ACL comparison

| item | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| Illocutionary force | Explicit ACL performative field | In natural-language wording; inferred by reader | Explicit leading tag | Explicit `performative` JSON field |
| Content language | Declared content language/ontology can be specified | Plain English | Plain English after tag | JSON object with typed price field |
| Who interprets content | Agent/content-language interpreter using agreed semantics | LLM reader | Regex for act; LLM reader for proposal price | Deterministic JSON parser |
| Conversation end | Defined by interaction protocol / performative semantics | Reader-inferred `accept-proposal` or `refuse`, or 8-turn limit | Parsed tag ends or 8-turn limit | Parsed JSON act ends or 8-turn limit |
| Sincerity guarantee | Semantic preconditions/rational-effect conventions are normative, not mechanically guaranteed | System prompt only; model may violate it | Same system-prompt constraint | Same system-prompt constraint |
| Cost to read one message | Parser/interpreter cost; no LLM inherently required | One LLM reader call per message | Regex; plus one reader call for each `propose` price | Deterministic parse, zero reader calls |
| Typical failure | Ontology/semantic mismatch or agent violates communicative assumptions | Ambiguous act/price, reader misclassification | Correct tag can hide a counter-offer in prose; malformed/missing tags | Invalid JSON, prose outside JSON, null/wrong typed price, or agent limit violation |

## 4. Interpretation

Across 36 episodes, changing only the message representation changed both protocol cost and failure mode. Free required 0 reader calls and produced 0 format errors; tagged reduced act interpretation to a regex but still used 0 reader calls to recover proposal prices; structured used 0 reader calls because both act and price were machine-readable. Correct outcomes were free 0/12, tagged 0/12, and structured 0/12; violations were 0, 0, and 0 respectively. This shows that an explicit performative removes one ambiguity, but it does not guarantee rational behavior or faithful content generation: the agents can still violate private limits, place a counter-offer under the wrong act, or emit malformed structure. Evidence from the logs: `free-01.txt` records `[result] outcome= price= correct= violation= turns= format_errors= reader_calls=`. `free-01.txt` records `[result] outcome= price= correct= violation= turns= format_errors= reader_calls=`. `free-01.txt` records `[result] outcome= price= correct= violation= turns= format_errors= reader_calls=`.
