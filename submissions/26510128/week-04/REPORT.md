# Week 04 — Speech acts in three negotiation formats

## 1. Setup, provider, prompts, and reproducibility

This experiment implements a two-agent price negotiation with one buyer LLM and one seller LLM. The buyer opens. The seller has a private reserve price (lowest acceptable price) and the buyer has a private budget (highest acceptable price). Each role receives only its own private limit; neither role receives the other side's limit or the retrospective `deal_possible` label. The harness alternates the two roles for at most six messages.

### Final experimental environment

- Provider/runtime: **Ollama local OpenAI-compatible API**
- Base URL: `http://localhost:11434/v1`
- Model: `qwen3:4b-instruct`
- Temperature: `0.2`
- Max response tokens: `350`
- Turn limit: `6`
- Request interval: `0` seconds
- Repeats: 3 per condition
- Scenarios: 4 fixed entries in `scenarios.json`
- Total completed episodes: 36

The same scenarios, shared role prompt, model, temperature, and turn limit were used in all three conditions. Only the message-format paragraph and the protocol reader prescribed by the assignment differed by condition.

### Shared role behavior

Each agent is told that it is buying or selling the scenario item, must keep its private limit secret, must not propose or accept a price that violates that limit, and may use only four acts: `propose`, `accept-proposal`, `reject-proposal`, and `refuse`. An acceptance is valid only for the other side's outstanding proposal. The buyer is required to open.

### Condition-specific format paragraphs

The exact strings are in `negotiation.py` under `FORMAT`.

- **free:** “Reply in plain English. Express your chosen action naturally; no tags or JSON. When making an offer state a clear integer price. When accepting, explicitly agree to the last price proposed by the other side.”
- **tagged:** “Start every message with exactly one tag: (propose), (accept-proposal), (reject-proposal), or (refuse). Then write plain English. For (propose) state one integer price. For (accept-proposal), accept ONLY the other side's last proposed price.”
- **structured:** “Reply with exactly one JSON object, with performative one of propose, accept-proposal, reject-proposal, refuse; and content an object. For propose include an integer price: {"performative":"propose","content":{"price":70}}. For other acts use "content":{}. No code fences, commentary, or extra fields.”

### Reader prompt

The free condition uses this LLM reader prompt for every message, and the tagged condition uses the same reader only to extract the price of a `propose` message:

> You are a protocol reader, not a negotiating party. Interpret ONE message and return exactly one JSON object with keys performative and price. performative MUST be one of propose, accept-proposal, reject-proposal, refuse. For a price proposal, price MUST be an integer; for all other acts, price MUST be null. If the message is a question without a matching act, use refuse. Do not change the act based on whether an offer is economically attractive. Do not add commentary or markdown.

Protocol interpretation is therefore:

- **free:** one reader-model call for every message;
- **tagged:** regex reads the parenthesized performative; the reader model is called only for the price of `propose`;
- **structured:** local JSON parsing only, with no reader-model call.

An invalid or unparsable protocol message increments `format_errors` and negotiation continues if turns remain. `accept-proposal` ends with a deal at the outstanding other-side price; `refuse` ends with `no_deal`; otherwise the episode ends as `open` at the six-message limit.

### Metrics

`deal_possible=1` exactly when `reserve <= budget`. `correct=1` when (a) a deal is possible and the recorded deal price lies inside both private limits, or (b) no deal is possible and the episode ends `no_deal`. `violation=1` records a completed deal below reserve or above budget. `turns` counts exchanged messages. `format_errors` counts protocol/parser failures. `reader_calls` counts LLM calls used **only to interpret messages**, not the buyer/seller generation calls.

### How to reproduce

Install Ollama, pull the same model, and start Ollama. From `submissions/26510128/week-04` in PowerShell:

```powershell
$env:OPENAI_BASE_URL="http://localhost:11434/v1"
$env:OPENAI_API_KEY="ollama"
$env:AGENT_MODEL="qwen3:4b-instruct"
$env:AGENT_TEMPERATURE="0.2"
$env:AGENT_MAX_TOKENS="350"
$env:AGENT_TURN_LIMIT="6"
$env:AGENT_REQUEST_INTERVAL="0"
$env:AGENT_MAX_RETRIES="3"

python negotiation.py
```

The `OPENAI_API_KEY=ollama` value is only a nonempty placeholder required by the OpenAI-compatible client; the final experiment uses the local Ollama endpoint and no external API credential. For a clean reproduction, run in a copy without the submitted `results.csv` and `logs/`, because the runner appends results and skips already completed tuples.

From repository root:

```powershell
python scripts/check_week04.py submissions/26510128/week-04
```

## 2. Results

| Condition | Completed episodes | Correct | Violations | Mean turns | Format errors | Reader calls |
|---|---:|---:|---:|---:|---:|---:|
| free | 12 | 3 | 0 | 5.00 | 0 | 60 |
| tagged | 12 | 3 | 0 | 5.75 | 0 | 60 |
| structured | 12 | 0 | 0 | 6.00 | 22 | 0 |

The table above reports counts over 12 completed episodes per condition. No private-limit violation occurred. Correctness differs because `open` is not counted as a correct no-deal outcome: for an impossible scenario, correctness requires `no_deal`.

### Per-episode results

| Run | Condition | Scenario | Deal possible | Outcome | Price | Correct | Violation | Turns | Format errors | Reader calls | Note |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|
| free-01 | free | headphones-compatible | 1 | deal | 60 | 1 | 0 | 2 | 0 | 2 | — |
| free-01 | free | monitor-incompatible | 0 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| free-01 | free | keyboard-compatible | 1 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| free-01 | free | camera-incompatible | 0 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| free-02 | free | headphones-compatible | 1 | deal | 60 | 1 | 0 | 2 | 0 | 2 | — |
| free-02 | free | monitor-incompatible | 0 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| free-02 | free | keyboard-compatible | 1 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| free-02 | free | camera-incompatible | 0 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| free-03 | free | headphones-compatible | 1 | deal | 60 | 1 | 0 | 2 | 0 | 2 | — |
| free-03 | free | monitor-incompatible | 0 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| free-03 | free | keyboard-compatible | 1 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| free-03 | free | camera-incompatible | 0 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| tagged-01 | tagged | headphones-compatible | 1 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| tagged-01 | tagged | monitor-incompatible | 0 | open | — | 0 | 0 | 6 | 0 | 5 | — |
| tagged-01 | tagged | keyboard-compatible | 1 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| tagged-01 | tagged | camera-incompatible | 0 | no_deal | — | 1 | 0 | 5 | 0 | 3 | — |
| tagged-02 | tagged | headphones-compatible | 1 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| tagged-02 | tagged | monitor-incompatible | 0 | open | — | 0 | 0 | 6 | 0 | 5 | — |
| tagged-02 | tagged | keyboard-compatible | 1 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| tagged-02 | tagged | camera-incompatible | 0 | no_deal | — | 1 | 0 | 5 | 0 | 3 | — |
| tagged-03 | tagged | headphones-compatible | 1 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| tagged-03 | tagged | monitor-incompatible | 0 | open | — | 0 | 0 | 6 | 0 | 5 | — |
| tagged-03 | tagged | keyboard-compatible | 1 | open | — | 0 | 0 | 6 | 0 | 6 | — |
| tagged-03 | tagged | camera-incompatible | 0 | no_deal | — | 1 | 0 | 5 | 0 | 3 | — |
| structured-01 | structured | headphones-compatible | 1 | open | — | 0 | 0 | 6 | 2 | 0 | — |
| structured-01 | structured | monitor-incompatible | 0 | open | — | 0 | 0 | 6 | 2 | 0 | — |
| structured-01 | structured | keyboard-compatible | 1 | open | — | 0 | 0 | 6 | 2 | 0 | — |
| structured-01 | structured | camera-incompatible | 0 | open | — | 0 | 0 | 6 | 2 | 0 | — |
| structured-02 | structured | headphones-compatible | 1 | open | — | 0 | 0 | 6 | 2 | 0 | — |
| structured-02 | structured | monitor-incompatible | 0 | open | — | 0 | 0 | 6 | 2 | 0 | — |
| structured-02 | structured | keyboard-compatible | 1 | open | — | 0 | 0 | 6 | 2 | 0 | — |
| structured-02 | structured | camera-incompatible | 0 | open | — | 0 | 0 | 6 | 2 | 0 | — |
| structured-03 | structured | headphones-compatible | 1 | open | — | 0 | 0 | 6 | 2 | 0 | — |
| structured-03 | structured | monitor-incompatible | 0 | open | — | 0 | 0 | 6 | 2 | 0 | — |
| structured-03 | structured | keyboard-compatible | 1 | open | — | 0 | 0 | 6 | 0 | 0 | — |
| structured-03 | structured | camera-incompatible | 0 | open | — | 0 | 0 | 6 | 2 | 0 | — |

## 3. FIPA-ACL compared with the three conditions

| Dimension | FIPA-ACL | Free | Tagged | Structured |
|---|---|---|---|---|
| Where illocutionary force lives | In the mandatory `performative` field of the ACL message envelope | In natural-language context and inferred by an LLM reader | In an explicit parenthesized performative tag | In the JSON `performative` field |
| Content language | Declared content language/ontology supplies shared semantics | Plain text with no formal content language | Plain text after the tag | JSON object; proposals carry `content.price` |
| Who interprets content | The receiving system interprets the declared content language according to the protocol/ontology | LLM reader interprets every message | Regex reads the act; LLM reader extracts only proposal price | Local JSON parser validates fields and price |
| How conversation ends | Determined by the interaction protocol and accepted terminal acts | Reader-labeled acceptance/refusal, otherwise turn cap | Tagged acceptance/refusal, otherwise turn cap | Parsed JSON acceptance/refusal, otherwise turn cap |
| What guarantees sincerity | Explicit performative semantics state the intended communicative act, but the tag itself does not prove private information is truthful | Prompt rules only; limits are checked retrospectively | Same prompt rules; explicit tag does not verify the private limit | Typed JSON does not by itself verify truthful private information |
| What a message costs to read | Depends on transport plus content-language parsing/semantic processing | One extra LLM reader call per message | Regex for non-proposals; one extra LLM reader call for each proposal price | Zero reader-model calls; local JSON parse only |
| Failure modes observed/possible | Unsupported acts, ontology/content mismatch, protocol violations, misleading content | Reader dependence and turn-limit `open` outcomes | Repeated proposals/turn-limit `open`; explicit refusal can cleanly end an impossible negotiation | Strict syntax failures: malformed JSON such as `"content{}"`; turn-limit `open` |

## 4. Interpretation

The explicit message format clearly changed **how the protocol could read messages**, but it did not by itself guarantee successful negotiation. In the free condition, all 12 episodes were parsed with zero format errors, but reading them required 60 additional LLM calls. For example, `logs/free-01.txt` shows the buyer message `'propose 60'` classified as `performative=propose price=60`, followed by seller `'accept-proposal'`; the reader classified the acceptance and the episode ended as a correct deal at 60 after two messages. The other three scenarios in each free repeat reached the six-message cap, so free achieved 3 correct outcomes out of 12.

The tagged condition also had zero format errors, but it reduced interpretation work selectively: a literal non-proposal tag was handled without a reader call, while proposals still required one reader call for price. This produced the same total reader calls as free in these specific runs (60), because most tagged messages were proposals. `logs/tagged-01.txt` illustrates both paths: `(propose) 90` required a reader call to extract 90, whereas `(reject-proposal)` was parsed directly with no reader-model call. On the impossible camera scenario, the buyer eventually sent `(refuse)`; regex parsing ended the negotiation as `no_deal`, producing the tagged condition's three correct episodes. The compatible headphone and keyboard negotiations instead kept proposing until the turn limit, showing that an explicit performative can remove ambiguity without forcing the agents to converge.

The structured condition eliminated reader-model calls entirely (0 total), which is the clearest reduction in protocol interpretation cost, but strict parsing exposed a different failure mode. Across 12 episodes it produced 22 format errors. In `logs/structured-01.txt`, valid proposals such as `{"performative":"propose","content":{"price":70}}` parsed successfully, while several rejection messages were malformed as `{"performative":"reject-proposal","content{}}` and triggered `JSONDecodeError`; the protocol logged them as unparseable and continued. `logs/structured-03.txt` also provides a useful contrast: the keyboard scenario emitted syntactically valid `{"performative":"reject-proposal","content":{}}` messages, yielding zero format errors for that episode, yet the negotiation still reached the turn limit. Thus structure made interpretation deterministic and cheap **when the model obeyed the schema**, but did not make the model generate valid JSON or reach agreement. No condition produced a private-limit violation, so the observed differences concern parsing cost, syntax reliability, and termination behavior rather than sincerity violations. Because all 36 episodes used one local model and four fixed scenarios, these results describe this reproduction rather than establishing a general ranking of the three formats.
