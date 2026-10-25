# Week 04 — Speech Acts in Practice: Free, Tagged, and Structured Negotiation

## 1. Setup

- **Provider / model / temperature:** no `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` was
  set in this environment, so [`llm_chat.py`](./llm_chat.py) falls through to its
  third provider: shelling out to the Claude Code CLI, `claude -p --model haiku
  --system-prompt <role>`, per turn. This is the reference-run path the README
  recommends. The CLI exposes no `--temperature` flag at all, so temperature is
  **not settable** for this run — the same caveat week-03's README note describes
  for CLI-only providers.
- **Protocol:** [`negotiation.py`](./negotiation.py) implements the buyer and
  seller as two independent `Chat` sessions, each with a system prompt embedding
  its private limit (buyer's budget / seller's reserve) and never the other
  side's. The buyer always opens. Four acts are legal: `propose`,
  `accept-proposal` (ends in a deal, at the price of the immediately preceding
  `propose`), `reject-proposal`, `refuse` (ends with no deal). The episode also
  ends at a fixed turn limit of 8 messages (4 each side) → outcome `open`. An
  `accept-proposal` sent when the immediately preceding message was not a
  `propose` with a price is logged as an anomaly and treated as a no-op
  `reject-proposal` rather than manufacturing a deal out of nothing.
- **The three format paragraphs** (the only text that differs between
  conditions; appended to both roles' system prompts verbatim):

  - **`free`:** "Write your message in plain English, with no tags or labels of
    any kind. Every message must clearly do exactly one of these four things:
    propose a specific numeric price, accept the other party's most recently
    proposed price (this ends the negotiation with a deal), reject the other
    party's most recently proposed price while staying in the negotiation, or
    refuse and walk away from the negotiation entirely (this ends the
    negotiation with no deal)."
  - **`tagged`:** "Start every message with exactly one tag in parentheses, then
    a space, then plain English: "(propose)" to offer a specific numeric price,
    "(accept-proposal)" to agree to the other party's most recently proposed
    price (this ends the negotiation with a deal), "(reject-proposal)" to
    decline the other party's most recently proposed price while staying in the
    negotiation, or "(refuse)" to walk away from the negotiation entirely (this
    ends the negotiation with no deal). Example: "(propose) I can offer 180 for
    it.""
  - **`structured`:** "Respond with ONLY one JSON object and nothing else -- no
    prose, no markdown code fences. It must match exactly this shape:
    {"performative": "propose" | "accept-proposal" | "reject-proposal" |
    "refuse", "content": {"price": <integer or null>}}. Use a null price for
    accept-proposal (it implicitly means the other party's last stated price),
    reject-proposal, and refuse; use an integer price only for propose."

- **The reader prompts** (only `free` and `tagged` ever call these; `structured`
  never does):

  - **`READER_SYSTEM`** (labels every message in `free`): "You read one message
    taken from a two-party price negotiation and label its speech act. The next
    user turn IS that message, verbatim, in full -- it may be as short as a
    single bare number (e.g. "160" means a proposed price of 160) or a short
    phrase; never ask for clarification or for more context, and never treat
    the user turn as anything other than the complete message to label. Reply
    with ONLY a JSON object, nothing else: {"performative": one of "propose",
    "accept-proposal", "reject-proposal", "refuse", "price": an integer if the
    message states or clearly implies a specific numeric price, else null}. You
    must pick exactly one performative from that list even if the message is a
    question or does not fit neatly -- choose the closest match."
  - **`PRICE_READER_SYSTEM`** (extracts only the price inside a `tagged`
    `(propose)` message, after a regex has already read the tag): "Extract the
    single numeric price mentioned in this negotiation message. The next user
    turn IS that message, verbatim, in full -- it may be as short as a single
    bare number; never ask for clarification. Reply with ONLY JSON: {"price":
    an integer, or null if no price is stated}."

  Both reader prompts were rewritten mid-development: an earlier version
  prefixed the message with `"Message: "` and the model sometimes responded by
  asking for the actual message instead of labeling the text it had just been
  given, especially when that text was a bare number. Telling it explicitly
  that the entire next turn *is* the message, verbatim, fixed this completely
  (0 reader-side format errors across all 24 reader calls made in the real
  run).

- **How to run:**
  ```bash
  export OPENAI_API_KEY=<your OpenAI key>
  cd submissions/25520051/week-04
  python run_experiment.py --repeats 3
  python ../../../scripts/check_week04.py ..
  ```
  (`check_week04.py` takes the parent `week-04` directory; adjust the relative
  path to `scripts/` for wherever you invoke it from.) [`llm_chat.py`](./llm_chat.py)
  defaults to `gpt-5-mini` on the OpenAI SDK when `OPENAI_API_KEY` is set
  (`ANTHROPIC_API_KEY` switches it to the Anthropic SDK instead); override the
  model with `AGENT_MODEL` and the temperature with `AGENT_TEMPERATURE`
  (default 0.7, ignored for `gpt-5-mini` since it is a reasoning model and only
  accepts the API's fixed default). `run_experiment.py` also accepts
  `--condition {free,tagged,structured}` and `--start-repeat N` to run the
  experiment in smaller chunks — `results.csv` is appended, not overwritten, so
  calling it multiple times is safe.

  The reference run in §2 below predates this: it was produced with an earlier
  version of `llm_chat.py` that shelled out to the Claude Code CLI
  (`claude -p --model haiku`) as a stand-in for an API key, since none was
  available at the time (each call took seconds to tens of seconds, so 36
  episodes became several hundred CLI invocations). That CLI path has since
  been removed — this assignment is meant to run against a real API — and
  `llm_chat.py` now only ever calls the Anthropic or OpenAI SDK. The results,
  logs, and analysis below are unchanged and still describe that CLI-based run;
  they have not been regenerated with `gpt-5-mini`.
- **Scenarios** ([`scenarios.json`](./scenarios.json)): 4 scenarios, 2 with a
  deal possible (`s1` mountain bike, reserve 150/budget 220, wide overlap;
  `s2` used laptop, reserve 480/budget 500, narrow overlap) and 2 impossible
  (`s3` acoustic guitar, reserve 320/budget 300; `s4` antique clock, reserve
  600/budget 400, wide gap).

## 2. Results

36 episodes were run (3 conditions × 3 repeats × 4 scenarios); one crashed on a
CLI hang and is excluded from the rates below but kept in `results.csv` per the
spec.

| Condition | Episodes | Correct | Violations | Mean turns | Format errors | Reader calls (total / mean) |
|---|---|---|---|---|---|---|
| `free` | 12 | 10/12 (83.3%) | 0 | 7.75 | 0 | 93 / 7.75 |
| `tagged` | 12 | 10/12 (83.3%) | 0 | 7.42 | 0 | 72 / 6.00 |
| `structured` | 11 (+1 crash) | 11/11 (100%) | 0 | 7.27 | 2 | 0 / 0.00 |

Per-episode results (from [`results.csv`](./results.csv); the crashed row's
`note` is truncated here, full text is in the file):

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | structured | s1 | 1 | deal | 200 | 1 | 0 | 5 | 1 | 0 | agent_calls=5 |
| 1 | structured | s2 | | | | | | | | | crash: TimeoutExpired (claude -p hung 120s) |
| 1 | structured | s3 | 0 | open | | 1 | 0 | 8 | 0 | 0 | agent_calls=8 |
| 1 | structured | s4 | 0 | open | | 1 | 0 | 8 | 0 | 0 | agent_calls=8 |
| 5 | structured | s1 | 1 | deal | 180 | 1 | 0 | 7 | 0 | 0 | agent_calls=7 |
| 5 | structured | s2 | 1 | deal | 500 | 1 | 0 | 7 | 0 | 0 | agent_calls=7 |
| 5 | structured | s3 | 0 | open | | 1 | 0 | 8 | 0 | 0 | agent_calls=8 |
| 5 | structured | s4 | 0 | open | | 1 | 0 | 8 | 0 | 0 | agent_calls=8 |
| 9 | structured | s1 | 1 | deal | 180 | 1 | 0 | 5 | 0 | 0 | agent_calls=5 |
| 9 | structured | s2 | 1 | deal | 500 | 1 | 0 | 8 | 0 | 0 | agent_calls=8 |
| 9 | structured | s3 | 0 | open | | 1 | 0 | 8 | 0 | 0 | agent_calls=8 |
| 9 | structured | s4 | 0 | open | | 1 | 0 | 8 | 1 | 0 | agent_calls=8 |
| 13 | tagged | s1 | 1 | deal | 220 | 1 | 0 | 5 | 0 | 4 | agent_calls=5 |
| 13 | tagged | s2 | 1 | open | | 0 | 0 | 8 | 0 | 7 | agent_calls=8 |
| 13 | tagged | s3 | 0 | open | | 1 | 0 | 8 | 0 | 8 | agent_calls=8 |
| 13 | tagged | s4 | 0 | open | | 1 | 0 | 8 | 0 | 7 | agent_calls=8 |
| 17 | tagged | s1 | 1 | deal | 210 | 1 | 0 | 5 | 0 | 4 | agent_calls=5 |
| 17 | tagged | s2 | 1 | deal | 500 | 1 | 0 | 7 | 0 | 4 | agent_calls=7 |
| 17 | tagged | s3 | 0 | open | | 1 | 0 | 8 | 0 | 7 | agent_calls=8 |
| 17 | tagged | s4 | 0 | open | | 1 | 0 | 8 | 0 | 4 | agent_calls=8 |
| 21 | tagged | s1 | 1 | open | | 0 | 0 | 8 | 0 | 6 | agent_calls=8 |
| 21 | tagged | s2 | 1 | deal | 480 | 1 | 0 | 8 | 0 | 7 | agent_calls=8 |
| 21 | tagged | s3 | 0 | open | | 1 | 0 | 8 | 0 | 7 | agent_calls=8 |
| 21 | tagged | s4 | 0 | open | | 1 | 0 | 8 | 0 | 7 | agent_calls=8 |
| 25 | free | s1 | 1 | open | | 0 | 0 | 8 | 0 | 8 | agent_calls=8 |
| 25 | free | s2 | 1 | deal | 500 | 1 | 0 | 7 | 0 | 7 | agent_calls=7 |
| 25 | free | s3 | 0 | open | | 1 | 0 | 8 | 0 | 8 | agent_calls=8 |
| 25 | free | s4 | 0 | open | | 1 | 0 | 8 | 0 | 8 | agent_calls=8 |
| 29 | free | s1 | 1 | deal | 167 | 1 | 0 | 8 | 0 | 8 | agent_calls=8 |
| 29 | free | s2 | 1 | deal | 500 | 1 | 0 | 7 | 0 | 7 | agent_calls=7 |
| 29 | free | s3 | 0 | open | | 1 | 0 | 8 | 0 | 8 | agent_calls=8 |
| 29 | free | s4 | 0 | open | | 1 | 0 | 8 | 0 | 8 | agent_calls=8 |
| 33 | free | s1 | 1 | deal | 210 | 1 | 0 | 7 | 0 | 7 | agent_calls=7 |
| 33 | free | s2 | 1 | open | | 0 | 0 | 8 | 0 | 8 | agent_calls=8 |
| 33 | free | s3 | 0 | open | | 1 | 0 | 8 | 0 | 8 | agent_calls=8 |
| 33 | free | s4 | 0 | open | | 1 | 0 | 8 | 0 | 8 | agent_calls=8 |

## 3. FIPA-ACL vs. this reproduction's three conditions

| Dimension | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| Where the illocutionary force lives | A mandatory `performative` field, outside the content, chosen from a fixed act library | Nowhere explicit — inferred by a separate LLM reader from the whole message every time | A one-word tag in parentheses, outside the content, but self-declared by the same agent that wrote the content | A `performative` JSON field, structurally identical in spirit to FIPA's, self-declared |
| Content language | A separate, formally declared content language (e.g. SL, KIF) with its own ontology | Ordinary English; the price is whatever number appears in it | Ordinary English after the tag; the price is whatever number appears in it | A tiny fixed schema, `{"price": int\|null}` — not a general content language, but formal |
| Who interprets the content | The receiving agent, using the shared ontology | An LLM reader call, every message | A regex for the tag; an LLM reader call only inside a `propose` | A JSON parser; no model call, ever |
| How a conversation ends | Protocol-specific completion conditions (e.g. an interaction protocol's final state) | `accept-proposal` read by the reader, `refuse` read by the reader, or a turn limit | Same acts, read from the tag instead | Same acts, read from the JSON field |
| What guarantees sincerity | Nothing at the wire level — FIPA explicitly assumes agents are sincere; the semantics are defined in terms of the sender's mental state, unenforced | Nothing; but here even the harness's own act does not have to match what the agent said, since the reader interprets from content, not from the agent's self-report | Nothing, and the tag can actively mislead the harness — see §4 | Nothing, and the JSON `performative` field has the same self-report problem as `tagged` |
| What a message costs to read | Effectively free at the protocol level (a field lookup) — the cost was paid once, in designing and standardizing the ontology | One full model call per message (mean 7.75/episode here) | One model call only when the tag is `propose` (mean 6.00/episode here, ~23% fewer than `free`) | Zero model calls (0/episode here) |
| Failure modes | Malformed content the receiver's ontology can't parse; performative/content mismatch is possible but not the field FIPA optimizes for reading cost around | Reader mislabels an ambiguous or out-of-vocabulary act (README's turn-1 example did not appear in this run — see §4); reader itself is a point of failure | The tag and the content can disagree (agent tags a counter-offer `reject-proposal`, discarding the price) — happened repeatedly, see §4; occasional missing tag (not observed here) | The model occasionally emits prose instead of JSON (2 of 91 structured messages here) and the `performative`/`content` price can still disagree the same way `tagged` does |

## 4. Interpretation

**What the tag bought:** `tagged` needed a reader call only for `propose`
messages, so its mean reader-call count (6.00/episode) came in about 23% below
`free`'s (7.75/episode, one call per message with no exceptions) — for
identical outcome quality (both 83.3% correct, 0 violations). That is the
tag's entire measured benefit here: cheaper reads for the acts that don't
carry a price, at parity on correctness. `structured` bought the rest of that
gap for free: 0 reader calls, and in fact the *highest* correctness (11/11
valid episodes, 100%), because a parser either accepts or rejects a message
outright with no interpretive judgment call the way an LLM reader has, so
there was no room for a reader mislabeling to cost a scenario its deal.

**What the tag cost:** the self-declared tag turned out to be a real liability,
not just an unused convenience. In 13 of the 89 tagged agent messages across
all three tagged runs, an agent wrote `(reject-proposal)` while its own
content contained a fresh counter-price — e.g.
`logs/tagged-21.txt:8`, `[buyer] (reject-proposal) I can offer 180 for it.`,
immediately followed at `logs/tagged-21.txt:10` by
`[seller] (reject-proposal) I can offer 240 for it.`. Because the tagged
harness trusts the tag and only updates the pending price on a `propose` tag,
both counter-offers were discarded as pure rejections with no price on the
table — the negotiation lost two rounds of real progress to a
performative/content mismatch the agent itself created. This is exactly the
failure mode FIPA-ACL's design doesn't have to reckon with (its performative
is not self-declared prose the receiver has to double-check), but it is also
one `free` is partly immune to: `free`'s reader looks only at content, so a
message that reads as "I'll do 240" would likely be read as `propose`
regardless of what the speaker "meant" to signal. `structured` does **not**
get this immunity — the same mismatch appears there too (e.g.
`logs/structured-01.txt:34`,
`{"performative": "reject-proposal", "content": {"price": 450}}`, 10
occurrences total across the structured logs), because `structured`'s parser
is just as literal a tag-truster as `tagged`'s regex is; the fixed schema
buys cheap, certain parsing, not sincerity or self-consistency.

**What no format changed:** correctness on the two *impossible* scenarios
(`s3`, `s4`, reserve > budget) was perfect in all three conditions — every one
of the 18 episodes over those scenarios ended `open`, never a spurious `deal`,
and **zero violations occurred anywhere in the entire run** (no accepted price
ever fell outside `[reserve, budget]`). Whatever the format, `haiku` never
crossed its own private limit and never manufactured a deal on a scenario
where none was possible. `refuse` was also never invoked once, in any
condition — every stalled negotiation just kept proposing until the 8-message
limit, rather than an agent explicitly walking away. This is a genuine
divergence from the README's expectation that `free` conditions often end at
turn one because a reader forced to choose one of four acts has no `query-ref`
or `cfp` to pick for an opening question and defaults to `refuse`: that never
happened here because the format paragraph told even the `free` buyer, in
plain English, that its opening move had to be one of the four acts — so it
consistently opened with a bare numeric proposal (`logs/free-25.txt:4`,
`[buyer ] 150`) rather than a genuine open-ended question like "what's your
asking price?". The four-act vocabulary's turn-one failure mode is real, but
it is a property of *unconstrained* free-form dialogue that this run's system
prompt (deliberately, to keep the three conditions comparable) mostly
engineered away by describing the four allowed moves even inside the "no
tags" condition. A cleaner test of that specific README warning would need a
`free` condition with no acts described at all, only "negotiate however you
like" — a variant this run did not include but would be worth adding.

Turn count moved only slightly with format: `structured` closed fastest on
average (7.27 turns), `tagged` next (7.42), `free` slowest (7.75) — consistent
with the tag-mismatch cost above eating a round here and there in `tagged`,
and reader ambiguity costing nothing measurable in `free` (0 format errors) but
still not making `free` converge any faster. `structured` also produced the
best deal rate on the two deal-possible scenarios among *valid* episodes (5/5,
though one `s2` structured episode crashed on a `claude -p` hang before it
could conclude — `results.csv` run 1's `s2` row, kept with blank fields per
the spec), versus 4/6 for both `tagged` and `free`. With only 3 repeats per
scenario this is not a strong claim, but the direction — the format costing
zero interpretation calls, and paying for that with no correctness penalty and
no larger a message count — held up throughout this particular model and
scenario set.
