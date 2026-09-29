\# Week 04 Report — Speech Acts in Practice



\## 1. Setup



\### Provider and model



\- Provider: Groq through its OpenAI-compatible API

\- Model: `openai/gpt-oss-120b`

\- Temperature: `0.0`

\- Reasoning effort: `low`

\- Turn limit: `4`

\- Scenarios: 4

\- Repeats: 3 per condition

\- Total completed episodes: 36



The same model, temperature, scenarios, role prompts, and turn limit were used for all three conditions. Only the message-format instruction and message-reading method changed.



\### Free condition



Agents communicated using plain English without explicit tags or JSON. Every message was interpreted by an LLM reader, which classified the message as `propose`, `accept-proposal`, `reject-proposal`, or `refuse`, and extracted a price when appropriate.



\### Tagged condition



Each message started with one explicit performative tag, such as `(propose)` or `(accept-proposal)`, followed by plain English. The performative was extracted by regular expression. The LLM reader was used only to extract the price from `propose` messages.



\### Structured condition



Each message used a JSON object such as:



{"performative":"propose","content":{"price":45}}



The program parsed the performative and price directly, so no LLM reader was required.



\### Reader prompt



The reader classified messages into exactly one of the four allowed performatives and extracted a proposed price when applicable. It returned the result in machine-readable JSON.



\## 2. Results



\### Summary by condition



| Condition | Episodes | Correct | Violations | Mean turns | Format errors | Reader calls |

|---|---:|---:|---:|---:|---:|---:|

| structured | 12 | 11 | 0 | 3.58 | 0 | 0 |

| tagged | 12 | 12 | 0 | 3.50 | 0 | 33 |

| free | 12 | 12 | 0 | 3.33 | 0 | 40 |



There were no private-limit violations and no format errors in any condition. Free and tagged communication produced correct outcomes in all 12 episodes. Structured communication produced 11 correct outcomes out of 12.



\## 3. Comparison with FIPA-ACL



| Aspect | FIPA-ACL | Free | Tagged | Structured |

|---|---|---|---|---|

| Illocutionary force | Explicit performatives identify communicative acts | Inferred from natural language by an LLM reader | Explicit performative tag | Explicit JSON field |

| Content language | Communicative act and content are separated | Natural-language content | Tag plus natural-language content | Structured JSON content |

| Interpreter | ACL-aware interpreter | LLM reader for every message | Regex for act and LLM reader for proposal price | Deterministic JSON parser |

| Conversation end | Normally controlled by an interaction protocol | Communicative act or turn limit | Communicative act or turn limit | Communicative act or turn limit |

| Sincerity / semantics | Semantic conditions are associated with communicative acts | Depends on model behavior and reader interpretation | Explicit tag does not guarantee matching behavior | Valid JSON does not guarantee good negotiation behavior |

| Interpretation cost | Depends on ACL implementation | 40 reader calls | 33 reader calls | 0 reader calls |

| Possible failures | Protocol or semantic mismatch | Classification or price-extraction errors | Tag/content mismatch or extraction errors | Correct structure but poor negotiation action |



\## 4. Interpretation



The results show that more explicit message structure reduced interpretation cost, but did not automatically guarantee a correct negotiation outcome. Structured communication required zero LLM reader calls and produced no format errors, but it achieved 11 correct outcomes out of 12. Free and tagged communication both achieved 12 correct outcomes, although they required 40 and 33 reader calls respectively.



The clearest failure occurred in structured run 9, scenario s2. The seller reserve was 35 and the buyer budget was 50, so a deal was possible. The buyer first proposed 30, the seller rejected it, and the buyer then proposed 45. Although 45 was inside the feasible range, the seller responded with a proposal of 55. The four-turn limit was then reached, producing `outcome=open` and `correct=0`.



This episode shows that syntactic reliability and negotiation quality are different issues. The structured JSON was parsed correctly, but the agents still failed to reach a feasible agreement. In this experiment, structured communication had the lowest interpretation cost, while free and tagged communication produced slightly better outcome correctness.


## How the experiment was run

The experiment was executed with `python run.py --condition <condition> --repeat <repeat>`. Each of the three conditions (free, tagged, and structured) was run three times, and every repeat included all four scenarios.

## Per-episode results

| Run | Condition | Scenario | Deal possible | Outcome | Price | Correct | Violation | Turns | Format errors | Reader calls |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | free | s1 | 1 | deal | 65 | 1 | 0 | 3 | 0 | 3 |
| 1 | free | s2 | 1 | deal | 38 | 1 | 0 | 3 | 0 | 3 |
| 1 | free | s3 | 0 | open |  | 1 | 0 | 4 | 0 | 4 |
| 1 | free | s4 | 0 | open |  | 1 | 0 | 4 | 0 | 4 |
| 2 | free | s1 | 1 | deal | 70 | 1 | 0 | 2 | 0 | 2 |
| 2 | free | s2 | 1 | deal | 38 | 1 | 0 | 3 | 0 | 3 |
| 2 | free | s3 | 0 | open |  | 1 | 0 | 4 | 0 | 4 |
| 2 | free | s4 | 0 | open |  | 1 | 0 | 4 | 0 | 4 |
| 3 | free | s1 | 1 | deal | 70 | 1 | 0 | 2 | 0 | 2 |
| 3 | free | s2 | 1 | deal | 38 | 1 | 0 | 3 | 0 | 3 |
| 3 | free | s3 | 0 | open |  | 1 | 0 | 4 | 0 | 4 |
| 3 | free | s4 | 0 | open |  | 1 | 0 | 4 | 0 | 4 |
| 4 | tagged | s1 | 1 | deal | 70 | 1 | 0 | 2 | 0 | 1 |
| 4 | tagged | s2 | 1 | deal | 45 | 1 | 0 | 4 | 0 | 3 |
| 4 | tagged | s3 | 0 | open |  | 1 | 0 | 4 | 0 | 4 |
| 4 | tagged | s4 | 0 | open |  | 1 | 0 | 4 | 0 | 3 |
| 5 | tagged | s1 | 1 | deal | 70 | 1 | 0 | 2 | 0 | 1 |
| 5 | tagged | s2 | 1 | deal | 45 | 1 | 0 | 4 | 0 | 3 |
| 5 | tagged | s3 | 0 | open |  | 1 | 0 | 4 | 0 | 4 |
| 5 | tagged | s4 | 0 | open |  | 1 | 0 | 4 | 0 | 3 |
| 6 | tagged | s1 | 1 | deal | 70 | 1 | 0 | 2 | 0 | 1 |
| 6 | tagged | s2 | 1 | deal | 45 | 1 | 0 | 4 | 0 | 3 |
| 6 | tagged | s3 | 0 | open |  | 1 | 0 | 4 | 0 | 4 |
| 6 | tagged | s4 | 0 | open |  | 1 | 0 | 4 | 0 | 3 |
| 7 | structured | s1 | 1 | deal | 70 | 1 | 0 | 2 | 0 | 0 |
| 7 | structured | s2 | 1 | deal | 45 | 1 | 0 | 4 | 0 | 0 |
| 7 | structured | s3 | 0 | open |  | 1 | 0 | 4 | 0 | 0 |
| 7 | structured | s4 | 0 | open |  | 1 | 0 | 4 | 0 | 0 |
| 8 | structured | s1 | 1 | deal | 70 | 1 | 0 | 3 | 0 | 0 |
| 8 | structured | s2 | 1 | deal | 45 | 1 | 0 | 4 | 0 | 0 |
| 8 | structured | s3 | 0 | open |  | 1 | 0 | 4 | 0 | 0 |
| 8 | structured | s4 | 0 | open |  | 1 | 0 | 4 | 0 | 0 |
| 9 | structured | s1 | 1 | deal | 70 | 1 | 0 | 2 | 0 | 0 |
| 9 | structured | s2 | 1 | open |  | 0 | 0 | 4 | 0 | 0 |
| 9 | structured | s3 | 0 | open |  | 1 | 0 | 4 | 0 | 0 |
| 9 | structured | s4 | 0 | open |  | 1 | 0 | 4 | 0 | 0 |
