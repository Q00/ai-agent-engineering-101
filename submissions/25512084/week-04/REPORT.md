# Week 04 — Speech Acts in Negotiation

## 1. Setup

Provider: OpenRouter  
Model: `nvidia/nemotron-3-super-120b-a12b:free`  
Temperature: 0  
Turn limit: 6 messages

The experiment uses one LLM buyer and one LLM seller. The buyer has a private budget and the seller has a private reserve price. The buyer always opens the negotiation.

Four communicative acts are allowed: `propose`, `accept-proposal`, `reject-proposal`, and `refuse`.

The same scenarios, role prompts, model, temperature, and turn limit are used in all three conditions. Only the message-format instructions and the protocol reader change.

### Free condition

Format paragraph:

`Reply in plain natural English. Do not use explicit performative tags or JSON.`

Every free-form message is interpreted by an LLM reader. The reader identifies both the performative and the price.

### Tagged condition

Format paragraph:

`Start every message with exactly one performative in parentheses: (propose), (accept-proposal), (reject-proposal), or (refuse). After the tag, write a short natural-English message.`

The performative is read with a regular expression. The LLM reader is used only to extract a price from `propose` messages.

### Structured condition

Format paragraph:

`Reply with exactly one JSON object and nothing else: {"performative": "...", "content": {"price": integer_or_null}}.`

Structured messages are interpreted by a JSON parser. No LLM reader is needed.

### Reader prompt

The free-condition reader is instructed to classify each message into one of the four allowed performatives and extract a price if present. It returns:

`{"performative": "...", "price": integer_or_null}`

For the tagged condition, a smaller reader prompt is used only to extract the offered price from a `propose` message.

### How to run

Environment variables:

`OPENAI_BASE_URL=https://openrouter.ai/api/v1`

`AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free`

`OPENAI_API_KEY` is provided through the environment and is not committed.

Run:

`python run_experiment.py`

The runner also supports `--start-run` for recovery runs.

Initial runs 1–9 are retained in `results.csv` and `logs/` as process evidence. During these runs, empty OpenRouter responses were not handled correctly and caused many `TypeError` failures. The model wrapper was then changed to retry empty responses. Recovery runs 10–18 were performed with the corrected code and are used for the main comparison.

## 2. Results

### Recovery runs 10–18

| Condition | Episodes | Completed | Correct | Violations | Mean turns | Format errors | Reader calls | Crashes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| free | 12 | 12 | 12 | 0 | 3.42 | 0 | 41 | 0 |
| tagged | 12 | 11 | 6 | 0 | 4.36 | 0 | 38 | 1 |
| structured | 12 | 12 | 6 | 0 | 4.00 | 0 | 0 | 0 |

The recovery experiment contains 36 episodes: 12 per condition.

The free condition completed all 12 recovery episodes and all 12 outcomes were correct. It required 41 additional LLM reader calls.

The tagged condition completed 11 of 12 episodes. Six completed episodes were correct. One episode, run 13 scenario s4, crashed with `RuntimeError: model returned no usable choice`.

The structured condition completed all 12 episodes. Six were correct, and it required zero LLM reader calls.

No completed recovery episode violated the buyer's budget or the seller's reserve price.

### Per-episode results

The complete per-episode table from `results.csv` is included below.
| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | free | s1 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 1 | free | s2 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 1 | free | s3 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 1 | free | s4 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 2 | free | s1 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 2 | free | s2 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 2 | free | s3 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 2 | free | s4 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 3 | free | s1 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 3 | free | s2 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 3 | free | s3 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 3 | free | s4 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 4 | tagged | s1 | 1 | deal | 500 | 1 | 0 | 3 | 0 | 2 |  |
| 4 | tagged | s2 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 4 | tagged | s3 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 4 | tagged | s4 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 5 | tagged | s1 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 5 | tagged | s2 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 5 | tagged | s3 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 5 | tagged | s4 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 6 | tagged | s1 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 6 | tagged | s2 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 6 | tagged | s3 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 6 | tagged | s4 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 7 | structured | s1 | 1 | deal | 500 | 1 | 0 | 2 | 0 | 0 |  |
| 7 | structured | s2 | 1 | deal | 300 | 1 | 0 | 2 | 0 | 0 |  |
| 7 | structured | s3 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 7 | structured | s4 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 8 | structured | s1 | 1 | deal | 500 | 1 | 0 | 2 | 0 | 0 |  |
| 8 | structured | s2 | 1 | deal | 300 | 1 | 0 | 2 | 0 | 0 |  |
| 8 | structured | s3 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 8 | structured | s4 | 0 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 9 | structured | s1 | 1 |  |  |  |  |  |  |  | TypeError: 'NoneType' object is not subscriptable |
| 9 | structured | s2 | 1 | deal | 300 | 1 | 0 | 2 | 0 | 0 |  |
| 9 | structured | s3 | 0 |  |  |  |  |  |  |  | RateLimitError: OpenRouter free-model daily request limit reached |
| 9 | structured | s4 | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| 10 | free | s1 | 1 | deal | 500 | 1 | 0 | 2 | 0 | 2 |  |
| 10 | free | s2 | 1 | deal | 300 | 1 | 0 | 2 | 0 | 2 |  |
| 10 | free | s3 | 0 | no_deal |  | 1 | 0 | 4 | 0 | 4 |  |
| 10 | free | s4 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| 11 | free | s1 | 1 | deal | 500 | 1 | 0 | 2 | 0 | 2 |  |
| 11 | free | s2 | 1 | deal | 300 | 1 | 0 | 2 | 0 | 2 |  |
| 11 | free | s3 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| 11 | free | s4 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| 12 | free | s1 | 1 | deal | 500 | 1 | 0 | 2 | 0 | 2 |  |
| 12 | free | s2 | 1 | deal | 300 | 1 | 0 | 2 | 0 | 2 |  |
| 12 | free | s3 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| 12 | free | s4 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 |  |
| 13 | tagged | s1 | 1 | deal | 500 | 1 | 0 | 3 | 0 | 2 |  |
| 13 | tagged | s2 | 1 | deal | 300 | 1 | 0 | 3 | 0 | 2 |  |
| 13 | tagged | s3 | 0 | open |  | 0 | 0 | 6 | 0 | 4 |  |
| 13 | tagged | s4 | 0 |  |  |  |  |  |  |  | RuntimeError: model returned no usable choice |
| 14 | tagged | s1 | 1 | deal | 500 | 1 | 0 | 3 | 0 | 2 |  |
| 14 | tagged | s2 | 1 | deal | 300 | 1 | 0 | 3 | 0 | 2 |  |
| 14 | tagged | s3 | 0 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| 14 | tagged | s4 | 0 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| 15 | tagged | s1 | 1 | deal | 500 | 1 | 0 | 3 | 0 | 2 |  |
| 15 | tagged | s2 | 1 | deal | 300 | 1 | 0 | 3 | 0 | 2 |  |
| 15 | tagged | s3 | 0 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| 15 | tagged | s4 | 0 | open |  | 0 | 0 | 6 | 0 | 4 |  |
| 16 | structured | s1 | 1 | deal | 500 | 1 | 0 | 2 | 0 | 0 |  |
| 16 | structured | s2 | 1 | deal | 300 | 1 | 0 | 2 | 0 | 0 |  |
| 16 | structured | s3 | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| 16 | structured | s4 | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| 17 | structured | s1 | 1 | deal | 500 | 1 | 0 | 2 | 0 | 0 |  |
| 17 | structured | s2 | 1 | deal | 300 | 1 | 0 | 2 | 0 | 0 |  |
| 17 | structured | s3 | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| 17 | structured | s4 | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| 18 | structured | s1 | 1 | deal | 500 | 1 | 0 | 2 | 0 | 0 |  |
| 18 | structured | s2 | 1 | deal | 300 | 1 | 0 | 2 | 0 | 0 |  |
| 18 | structured | s3 | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| 18 | structured | s4 | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
 Initial failed runs are retained as required instead of being deleted.

## 3. Comparison with FIPA-ACL

| Item | FIPA-ACL | Free | Tagged | Structured |
|---|---|---|---|---|
| Where illocutionary force lives | Explicit mandatory `performative` field | Inferred from natural-language context by an LLM reader | Explicit parenthesized tag | Explicit JSON `performative` field |
| Content language | Formal content language with a declared ontology | Unrestricted natural English | Natural English after the tag | Fixed JSON structure with a price field |
| Who interprets the content | Protocol or agent using the declared representation | LLM reader interprets performative and price | Regex reads the act; LLM reader extracts proposal prices | Deterministic JSON parser |
| How conversation ends | Communicative acts define interaction state | Reader must infer acceptance or refusal; turn limit may produce `open` | Explicit tags identify acceptance or refusal; turn limit may produce `open` | Parsed field identifies acceptance or refusal; turn limit may produce `open` |
| What guarantees sincerity | A performative does not guarantee truthful content | No format-level guarantee | Tag states the intended act but does not guarantee consistent content | Schema guarantees structure, not truthful or rational behavior |
| Cost to read a message | Depends on protocol implementation | One additional LLM reader call for every message | Regex is cheap, but `propose` messages require an LLM price-reader call | No reader-model calls |
| Failure modes | Protocol/content mismatch remains possible | Reader errors, price extraction errors, model/API failures | Tag/content inconsistency, price-reader failures, model/API failures | Invalid JSON/schema, model/API failures, or reaching the turn limit |

## 4. Interpretation

The message format changed both the cost of interpretation and the behavior of the negotiation, but explicit structure did not guarantee a correct outcome.

In the free condition, all 12 recovery episodes produced correct outcomes with a mean of 3.42 turns. However, this required 41 reader calls. For example, in run 10 the buyer wrote `I'd like to propose $500 for the used laptop.` The reader converted this natural-language message into `performative=propose price=500`. The seller then wrote `I accept your proposal of $500. We have a deal.`, which the reader classified as `accept-proposal`. This shows that the LLM reader could recover the intended speech act from context, but every message required an additional model call.

In an impossible-deal free scenario, the buyer proposed `$120` for an office chair while the seller's reserve was `$250`. The seller rejected the proposal and eventually stated that $250 was its firm minimum. The reader classified the final response as `refuse`, allowing the episode to finish as `no_deal` rather than remaining open.

The tagged condition made the illocutionary force explicit, but the tag did not guarantee that the natural-language content was internally consistent. One seller message was `(propose) I can't accept $150—my lowest is $250. Would you consider meeting at $225?`. The `propose` tag was easy to parse, but the proposed price of $225 was below the seller's stated reserve of $250. No violating deal was completed, but this illustrates that an explicit performative does not guarantee sincerity or consistency.

Tagged messages also still required 38 LLM reader calls because prices inside `propose` messages had to be extracted from natural language. In one impossible-deal scenario, both agents kept proposing and rejecting until the six-turn limit, producing `open`. One tagged recovery episode also crashed because the model returned no usable response even after retries.

The structured condition removed the reader-model cost completely. Across the 12 recovery episodes, `reader_calls=0` and no format errors were observed. For example, `{"performative": "propose", "content": {"price": 500}}` could be interpreted directly by the protocol layer without another LLM call.

However, structure did not guarantee correct negotiation behavior. In impossible-deal structured scenarios, the agents often continued proposing or rejecting until the six-turn limit instead of explicitly leaving with `refuse`. Those episodes therefore ended as `open`, which is considered incorrect when no deal is possible. This shows that explicit structure reduces interpretation ambiguity and reader cost, while negotiation strategy and termination behavior remain separate problems.
