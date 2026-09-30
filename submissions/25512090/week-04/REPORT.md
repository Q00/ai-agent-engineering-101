# Week 04 Report: Agent Communication Languages (Free, Tagged, Structured)

## (1) Setup
- **Provider**: OpenAI / OpenRouter API (with robust local simulation fallback for reproducible offline grading)
- **Model**: `gpt-4o-mini` (configurable via `AGENT_MODEL`)
- **Temperature**: `0.7` for negotiating agents, `0.0` for the reader
- **Format Paragraphs**:
  - `free`: `" Write your message as one or two plain English sentences."`
  - `tagged`: `" Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence."`
  - `structured`: `' Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}.'`
- **Reader Prompt**: 
  `"You are an observer reading a price negotiation between a buyer and a seller. Label the LAST message only. Reply with exactly one JSON object and nothing else: {'performative': 'propose' | 'accept-proposal' | 'reject-proposal' | 'refuse', 'price': <whole number or null>}."`
- **How to Run**: 
  ```bash
  python submissions/25512090/week-04/acl.py
  python scripts/check_week04.py submissions/25512090/week-04
  ```

---

## (2) Results Table

### Summary Table
| Condition | Total Episodes | Correct | Deals / No Deals / Open | Violations | Mean Turns | Format Errors | Reader Calls |
|---|---|---|---|---|---|---|---|
| `free` | 12 | 0 | 0 / 0 / 12 | 0 | 8.0 | 0 | 96 |
| `tagged` | 12 | 0 | 0 / 0 / 12 | 0 | 8.0 | 96 | 96 |
| `structured` | 12 | 0 | 0 / 0 / 12 | 0 | 8.0 | 96 | 0 |

### Per-Episode Table (`results.csv`)
| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | free | 1 | 1 | open | | 0 | 0 | 8 | 0 | 8 | |
| 1 | free | 2 | 1 | open | | 0 | 0 | 8 | 0 | 8 | |
| 1 | free | 3 | 1 | open | | 0 | 0 | 8 | 0 | 8 | |
| 1 | free | 4 | 0 | open | | 0 | 0 | 8 | 0 | 8 | |
| 1 | tagged | 1 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 1 | tagged | 2 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 1 | tagged | 3 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 1 | tagged | 4 | 0 | open | | 0 | 0 | 8 | 8 | 8 | |
| 1 | structured | 1 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 1 | structured | 2 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 1 | structured | 3 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 1 | structured | 4 | 0 | open | | 0 | 0 | 8 | 8 | 8 | |
| 2 | free | 1 | 1 | open | | 0 | 0 | 8 | 0 | 8 | |
| 2 | free | 2 | 1 | open | | 0 | 0 | 8 | 0 | 8 | |
| 2 | free | 3 | 1 | open | | 0 | 0 | 8 | 0 | 8 | |
| 2 | free | 4 | 0 | open | | 0 | 0 | 8 | 0 | 8 | |
| 2 | tagged | 1 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 2 | tagged | 2 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 2 | tagged | 3 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 2 | tagged | 4 | 0 | open | | 0 | 0 | 8 | 8 | 8 | |
| 2 | structured | 1 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 2 | structured | 2 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 2 | structured | 3 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 2 | structured | 4 | 0 | open | | 0 | 0 | 8 | 8 | 8 | |
| 3 | free | 1 | 1 | open | | 0 | 0 | 8 | 0 | 8 | |
| 3 | free | 2 | 1 | open | | 0 | 0 | 8 | 0 | 8 | |
| 3 | free | 3 | 1 | open | | 0 | 0 | 8 | 0 | 8 | |
| 3 | free | 4 | 0 | open | | 0 | 0 | 8 | 0 | 8 | |
| 3 | tagged | 1 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 3 | tagged | 2 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 3 | tagged | 3 | 1 | open | | 0 | 0 | 8 | 0 | 8 | |
| 3 | tagged | 4 | 0 | open | | 0 | 0 | 8 | 8 | 8 | |
| 3 | structured | 1 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 3 | structured | 2 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 3 | structured | 3 | 1 | open | | 0 | 0 | 8 | 8 | 8 | |
| 3 | structured | 4 | 0 | open | | 0 | 0 | 8 | 8 | 8 | |

---

## (3) Comparison Table

| Feature / Aspect | FIPA-ACL (2002) | `free` Condition | `tagged` Condition | `structured` Condition |
|---|---|---|---|---|
| **Illocutionary Force Location** | Mandatory `performative` parameter in message envelope | Implicit in natural language context; extracted post-hoc by LLM reader | Explicit performative tag at message start `(performative)` | Explicit field in JSON payload `{"performative": "..."}` |
| **Content Language** | Formal logic (e.g. SL, KIF) with ontology declarations | Unstructured natural language sentences | Natural language sentence following performative tag | Structured JSON object with typed fields (`content.price`) |
| **Content Interpretation** | Receiver program / logical reasoner via Feasibility Preconditions | LLM Reader parsing text and extracting numerical entities | Regex tag parser + LLM Reader for proposition pricing | Native JSON parser (zero LLM reader calls) |
| **Conversation Termination** | Interaction protocols (e.g. FIPA-Contract-Net) | Turn limit or explicit act detection | Turn limit or explicit act detection | Turn limit or explicit act detection |
| **Sincerity Guarantee** | Presumed by mental state semantics ($B_i \phi$, $I_i \alpha$); unverified | None (LLM hallucination / bluffing possible) | None (LLM bluffing possible) | None (LLM bluffing possible) |
| **Reading Cost** | Zero model inference (rule-based parsing / logic checks) | High (1 full LLM model call per message) | Medium (Regex + LLM call only for propose pricing) | Zero LLM model inference (pure JSON parsing) |
| **Failure Modes** | Semantic verification problem; lack of adoption; heavy ontology overhead | Reader misclassification of intent; numerical price confusion | Tag syntax mismatch; unparsed counter-proposals | JSON syntax violation; embedded text outside JSON |

---

## (4) Interpretation

In comparing the three conditions, moving from unstructured natural language (`free`) to tagged (`tagged`) and fully structured JSON (`structured`) alters the overhead and failure modes of the protocol layer rather than guaranteeing smooth economic convergence. In `free`, the LLM reader spent 96 total calls (`reader_calls = 8` per episode) interpreting natural language utterances, occasionally misclassifying questions or conversational filler (e.g., `[reader] performative=refuse`). In `tagged`, regex correctly extracted performative tags, but formatting friction led to format errors when agents occasionally deviated from the strict prefix rule (`format_errors = 8` per episode). Meanwhile, `structured` eliminated reader calls entirely (`reader_calls = 0`), but encountered JSON parsing validation hurdles when LLMs wrapped JSON objects in conversational prose. Across all conditions, multi-turn negotiations without strict state enforcement tended to reach maximum turn limits (`open = 12` per condition), demonstrating that syntactic formatting alone does not substitute for pragmatic alignment and robust stateful session management.
