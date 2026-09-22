# Week 04 — Speech acts in practice: free, tagged, and structured negotiation

A buyer and a seller negotiate the price of an item. Each is an LLM with its own
system prompt and a private limit (buyer: budget, seller: reserve). Four acts are
allowed — `propose`, `accept-proposal`, `reject-proposal`, `refuse` — and an
episode also ends at a fixed turn limit. The same negotiation runs in three
message formats; only the format paragraph of the system prompt and the protocol
layer that reads a message change.

## 1. Setup

- **Provider / model:** OpenAI `gpt-4o-mini`, `temperature=0`, `max_turns=8`.
- **Model glue:** `llm.py` (`call_model(system, messages, meter)`), generalised
  from the week-03 glue; the reader uses the same call. No tools.
- **Roles:** `acl.py` holds a shared `ROLE` + `COMMON` (the four acts); only the
  `FORMAT` paragraph changes per condition. The role prompt forbids crossing the
  private limit, so a violation would come from the reader, not the agent.
- **The three format paragraphs (only difference between conditions):**
  - `free`: "Write your message as one or two plain English sentences."
  - `tagged`: "Start your message with exactly one performative tag in
    parentheses, one of (propose), (accept-proposal), (reject-proposal),
    (refuse), then write one plain English sentence."
  - `structured`: 'Reply with exactly one JSON object and nothing else:
    `{"performative": ..., "content": {"price": <whole number or null>}}`.'
- **Reader prompt (`READER_SYSTEM`):** "You are an observer reading a price
  negotiation ... Label the LAST message only. Reply with exactly one JSON
  object ... `{"performative": ..., "price": <whole number or null>}`." Used on
  every message in `free`, and only for the price of a `propose` in `tagged`;
  `structured` never calls it.
- **How to run:**
  ```bash
  cd submissions/25512081/week-04
  export OPENAI_API_KEY=...            # or OpenRouter: OPENAI_BASE_URL + AGENT_MODEL
  python run.py --repeats 3            # writes results.csv and logs/
  ```

## 2. Results

15 episodes per condition (5 scenarios × 3 repeats). Full per-episode rows are in
`results.csv`; `reader_calls` is the total over the 15 episodes.

| condition | correct / 15 | violations | mean turns | format_errors | reader_calls | deal / no_deal / open |
|---|---|---|---|---|---|---|
| free | 7 | 0 | 8.0 | 0 | 120 | 1 / 0 / 14 |
| tagged | 8 | 0 | 6.8 | 0 | 27 | 5 / 3 / 7 |
| structured | 12 | 0 | 6.0 | 0 | 0 | 6 / 0 / 9 |

Per scenario (outcome across the 3 repeats; scenarios 1–3 have a deal possible,
4–5 do not):

| scenario (reserve/budget) | free | tagged | structured |
|---|---|---|---|
| 1 road bike (120/180) | open ×3 | deal ×2, open ×1 | deal@150 ×3 |
| 2 office chair (60/75) | deal ×1, open ×2 | deal-no-price ×3 | deal@60 ×3 |
| 3 film camera (200/200) | open ×3 | open ×1, no_deal ×2 | open ×3 |
| 4 electric guitar (300/240) | open ×3 | open ×3 | open ×3 |
| 5 graphics tablet (150/135) | open ×3 | open ×2, no_deal ×1 | open ×2, no_deal ×1 |

## 3. FIPA-ACL vs the three conditions

| Dimension | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| Where the illocutionary force lives | mandatory `performative` field | implicit in prose | explicit tag in parentheses | explicit JSON field |
| Content language | formal CL + declared ontology | natural language | natural language after the tag | JSON `{price}` |
| Who interprets the content | receiver via the CL parser | LLM reader, every message | regex for the act, LLM reader for a propose's price | plain parser, no model |
| How a conversation ends | protocol state machine | accept→deal / refuse→no_deal / turn limit→open (same all three) | same | same |
| What guarantees sincerity | assumed sincerity condition | nothing enforced (role prompt only asks) | nothing enforced | nothing enforced |
| Cost to read one message | parse | one model call (120 total) | regex + a model call only for a propose price (27) | zero model calls |
| Failure modes seen | ontology mismatch | non-convergence: 14/15 episodes hit the turn limit | `accept-proposal` after a price the layer never captured (scenario 2, "accept without a priced proposal") | exact-price standoff at the boundary (scenario 3, reserve = budget = 200) |

## 4. Interpretation

The metric the format moved most was the **cost of reading**, not correctness of behaviour: `reader_calls` fell from 120 (free, one reader call per message across all 8 turns of every episode) to 27 (tagged, a call only to price a `propose`) to 0 (structured, parsed with no model), while no condition recorded a single `violation` or `format_error` — with this model and these role prompts, the agents always kept their private limits and always emitted well-formed messages, so the reference run's reader-misread violation and its turn-one `refuse` did not appear here. What structure bought instead was **closing power**: structured closed both easy deals in 2–4 turns (scenario 1 at 150, scenario 2 at 60, e.g. `structured-1` scenario 1 reaching `outcome=deal price=150 turns=2`), tagged closed some but logged three "accept without a priced proposal" episodes on scenario 2 because a `reject-proposal` carries no price and the accept then landed with nothing to record, and free almost never closed at all (14 of 15 episodes ran to `open`, e.g. every `free-*` scenario-1 episode ending `outcome=open turns=8 reader_calls=8`) — the plain-English agents kept exchanging polite counteroffers past the turn limit. The one dimension no format changed was the impossible scenarios (4, 5) and the exact-boundary scenario (3): none of the three could turn `reserve = budget = 200` into a deal, all ending `open`, which is why structured's 12 correct still leaves scenario 3 at zero. So the explicit performative here did not make agents more honest (sincerity was never violated to begin with) — it made the message cheaper to read and the negotiation likelier to reach a recordable deal, and its residual cost surfaced only in tagged, where separating the act tag from the price left the accept step without a number.
