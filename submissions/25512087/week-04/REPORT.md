# Week 04 — Speech Acts in Practice

## 1. Setup

This experiment uses one buyer and one seller to negotiate four items. The buyer has a private maximum budget and the seller has a private minimum reserve price. The buyer opens, and the conversation alternates until an `accept-proposal`, `refuse`, or the fixed turn limit ends the episode. A deal uses the last proposed price. A deal is correct only when `reserve <= price <= budget`; a no-deal is correct when the private limits do not overlap.

The same scenarios, role prompts, model, temperature (`0`), and turn limit (`8`) are used in all conditions. Only the message-format paragraph and protocol reader change:

- `free`: agents speak plain English. A model reader labels each message with one of the four allowed performatives and extracts a proposed price.
- `tagged`: agents put one performative tag such as `(propose)` before plain English. A regular expression reads the act, and the model reader is used only to extract a price from a tagged proposal.
- `structured`: agents return only `{"performative": "...", "content": {"price": ...}}`. A local JSON parser reads the message, so no reader model call is needed.

The runner uses `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `AGENT_MODEL` from the environment. It records every message, reader call, parse result, episode result, provider, model, temperature, and turn limit in JSONL logs. It resumes safely by skipping completed `(condition, scenario, repeat)` pairs.

Run from this directory:

```bash
export OPENAI_BASE_URL=https://api.openai.com/v1
export OPENAI_API_KEY=<your key>
export AGENT_MODEL=gpt-5.6-luna
python3 run_experiment.py --runs 3 --turn-limit 8
```

## 2. Results

The implementation and data contract are ready. The nine run files and result rows are generated after the API command above is executed; no measurements are invented in this draft.

| condition | correct | violations | mean turns | format errors | reader calls |
|---|---:|---:|---:|---:|---:|
| free | pending | pending | pending | pending | pending |
| tagged | pending | pending | pending | pending | pending |
| structured | pending | pending | pending | pending | pending |

## 3. FIPA-ACL compared with the three conditions

| Dimension | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| Where illocutionary force lives | Mandatory `performative` field | In plain-language context, recovered by a reader model | Explicit tag, recovered by regex | Explicit JSON field |
| Content language | Declared content language and ontology | Natural language | Natural language after the tag | JSON schema |
| Who interprets content | Receiver under shared ontology | LLM reader | Regex for act plus LLM reader for proposal price | Deterministic parser |
| Conversation ending | Protocol-specific acts and deadlines | Four-act harness plus turn limit | Same | Same |
| Sincerity guarantee | FP/RE describe beliefs but do not expose them | No independent guarantee | No independent guarantee | No independent guarantee; syntax is verifiable |
| Cost to read one message | Formal interpretation by receiver | One extra model reader call | Reader call only for proposal price | No reader call |
| Failure modes | Ontology mismatch and unverifiable mental states | Misread act, question treated as refusal, malformed reader JSON | Missing/wrong tag and malformed price extraction | Invalid JSON, missing price, unsupported act |

## 4. Interpretation

This section will be completed from the nine JSONL logs after the API run. The comparison will identify whether explicit tags reduce act-reading errors, whether structured messages eliminate reader calls at the cost of natural-language flexibility, and whether any format accepts a price outside a private limit. A free-format episode ending on a buyer question is treated as evidence about the four-act vocabulary, not silently corrected. The analysis will cite concrete `message`, `reader`, `parsed`, `parse_error`, and `episode_result` lines from the committed logs.
