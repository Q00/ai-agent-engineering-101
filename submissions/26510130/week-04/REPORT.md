# Week 04 — Speech acts in practice: free, tagged, and structured negotiation

26510130 Hyunsik Wang.

> **Draft.** Parts 1 and 3 are final. Parts 2 and 4 are filled from
> `results.csv` once the runs finish; the placeholders say what goes in them.
> This note goes when they do.

## 1. Setup

A buyer and a seller, each one LLM with its own system prompt, negotiating a
price. The seller alone knows its reserve, the buyer alone its budget — neither
limit appears in the other's prompt or in the transcript. The buyer opens. The
episode ends on `accept-proposal` (deal), `refuse` (no deal), or the turn limit
(`open`).

| | |
|---|---|
| Provider | Claude Code CLI, `claude -p` |
| Model | `haiku` → `claude-haiku-4-5-20251001` |
| Temperature | **not settable through this CLI.** Recorded as such, not assumed to be a default |
| Turn limit | 12 messages per episode (see §5 — the first pass ran at 6) |
| Scenarios | `scenarios.json`, 4 — two with `reserve ≤ budget`, two without. Committed before any run |
| Repeats | 3 per scenario per condition |
| Files | `acl.py` (model call, meter, readers), `negotiate.py` (agents, episode), `run_neg.py` (runner), `selftest.py` (offline checks) |

### Why the CLI and not an HTTP API

The assignment's own reference run went through `claude -p` for the reason that
applies here too: a free-tier OpenRouter key stops at 50 requests a day and one
pass of this experiment needs several hundred. `--system-prompt` replaces Claude
Code's own prompt, so the process answers as the negotiating agent rather than
as a coding assistant, and every tool is denied so it cannot go and *do*
something instead of replying.

Two consequences, both recorded rather than papered over:

- **Temperature is not exposed.** There is no flag for it. Week 03 pinned
  temperature to 0; this week cannot, and the report says so instead of
  claiming a value it did not set.
- **Token counts include the CLI's own context.** Each call is a fresh process
  that re-primes Claude Code's context, so `cache_creation_input_tokens` runs
  to roughly 19,000 against a ten-token prompt. `Meter` keeps that in
  `cli_overhead_tokens`, separate from the agents' own `input+output`.
  Attributing the harness's startup cost to the negotiation would make the
  three conditions look like they differ in ways they do not.

### The three conditions

Held constant: both role prompts, the scenarios, the model, the turn limit, the
scoring. Exactly two things change — the format paragraph appended to each
agent's system prompt, and the code that reads a message.

**`free`**
> Write your message in plain English, one or two sentences. Do not use tags,
> labels or JSON. Say what you mean in ordinary words.

Read by an LLM labeller: the whole message goes to a reader call that returns
`{"performative": ..., "price": ...}`.

**`tagged`**
> Begin every message with exactly one performative in parentheses, then plain
> English. The performatives are (propose), (accept-proposal),
> (reject-proposal) and (refuse). Example: (propose) I can go to 40000 for this.

Read by regex for the tag. A reader call is spent only when the message is a
`propose` whose price cannot be found in the text.

**`structured`**
> Reply with exactly one JSON object and nothing else:
> `{"performative": "<propose|accept-proposal|reject-proposal|refuse>",
> "content": {"price": <integer or null>}}`. No prose, no code fences, no text
> outside the object.

Read by a parser. No model call.

### The reader prompt (`free`, and `tagged`'s price fallback)

> You label one message from a price negotiation. The only speech acts
> available are: propose (offers a price), accept-proposal (agrees to the other
> side's last price), reject-proposal (declines but keeps negotiating), refuse
> (leaves, no deal). Reply with exactly one JSON object and nothing else:
> `{"performative": "<one of the four>", "price": <integer or null>}`. price is
> the number the message itself offers, null if it offers none. No prose, no
> code fences.

### Scoring

`correct` is 1 when a deal happened exactly where one was possible and its
price sits inside both private limits, or when the pair correctly walked away
from a scenario where `reserve > budget`. `violation` is 1 when a deal closed
below the seller's reserve or above the buyer's budget. **`open` is never
scored correct** — running out of turns is not the same as deciding there is no
deal, and counting it as correct would reward the turn limit for the agents'
failure to reach one.

### How to run

```bash
claude auth login            # the CLI carries the credentials; no API key
python run_neg.py --repeats 3 --max-turns 12
python selftest.py           # offline, no model call, writes nothing
```

The runner resumes: run numbers are fixed by `(condition, repeat)`, rows
already in `results.csv` are skipped and log files append, so an interrupted
pass continues instead of paying for finished episodes twice. `selftest.py`
stubs the model call and checks price parsing, all three readers, the
deal/no-deal/open/violation paths, unreadable messages, an accept with no
standing proposal, the reader-call split across conditions, and that neither
side's private limit reaches the other's prompt — 32 checks, no model call
spent. Every row in `results.csv` came from `run_neg.py` against the real CLI.

## 2. Results

> *Pending the runs.*

## 3. FIPA-ACL against the three conditions

FIPA-ACL (2002) made the illocutionary force a mandatory field: every message
declares its `performative`, and the content sits in a separate content
language with a declared ontology, so that a receiving agent parses force and
content without interpreting either. The three conditions here take that design
apart one layer at a time — `structured` keeps both halves, `tagged` keeps the
declared force and drops the content language, `free` drops both.

| | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| **Where the illocutionary force lives** | A mandatory `performative` slot in the message envelope, drawn from the Communicative Act Library. It is metadata: never inferred, always declared. | Nowhere in the message. It exists only as the reader's label, produced after the fact by a second model. | A tag the sender writes in parentheses. Declared, but inside the message body rather than an envelope, so nothing structurally prevents its absence. | A `performative` key in the JSON object — the closest of the three to FIPA's design. |
| **What the content language is** | A formal content language (SL, KIF) over a declared ontology. Content is a term to be evaluated, not a sentence to be read. | English. No content language at all; the price is whatever a number in the prose turns out to mean. | English, with one tag bolted on the front. The price still lives in prose. | JSON with a fixed shape, `{"price": int\|null}`. An ontology of one field — narrow, but declared and machine-checkable. |
| **Who interprets the content** | The receiving agent, mechanically, because the content language fixes the meaning. | A third party: an LLM reader that neither side sees, whose labels can differ from what the sender meant. | The regex for the force, and the reader for a price it cannot find in the text. Interpretation is split between code and model. | A parser. Nobody interprets — the sender's own structure is taken as given. |
| **How a conversation ends** | By a terminal performative in a defined interaction protocol (FIPA-Request, Contract Net), with a protocol state machine that says which acts may follow which. | When the *reader* labels something `accept-proposal` or `refuse`. The end is a reading, not an act. | When the tag says so. | When the JSON says so. |
| **What guarantees sincerity** | Nothing enforceable. FIPA states sincerity as a *feasibility precondition* — an agent must believe what it asserts — and leaves it as a normative assumption about cooperative agents, unverifiable by the receiver. | Nothing, and now the force is a guess as well, so an insincere message and a misread one are indistinguishable from the outside. | Nothing. The tag is as unverifiable as the assertion under it: an agent can write `(accept-proposal)` over any text. | Nothing. A well-formed object is not a true one. The private limits are enforced by the prompt only, which is why `violation` is a measured column and not an impossible state. |
| **What a message costs to read** | One parse. The point of the envelope: no inference needed. | One model call per message, plus its latency and its own error rate. | A regex, and a model call only for a price the text does not hand over. | One parse. Free, and the only condition where reading cannot itself be wrong. |
| **Which failure modes appear** | Ontology mismatch between agents; a message whose performative does not fit the protocol's state machine. | The reader picks the wrong act — and structurally must, when the message is not one of the four. A buyer opening with "what are you asking for it?" is a `query-ref` in FIPA's library, which this four-act vocabulary does not contain, so the reader has to force it into propose/reject/refuse. Also: a counter-offer read as an acceptance. | A missing or misspelled tag, which leaves the message unreadable even though the words are perfectly clear. The tag can also contradict the text under it. | Malformed or fenced JSON; a well-formed object whose `price` is null where a number was meant. The failure is loud and local rather than silent. |

Two things this table makes visible that the metrics alone do not.

**The performative tag does not buy sincerity — it buys parseability, and that
is all FIPA ever claimed.** Sincerity in FIPA is a feasibility precondition on
the sender, not a property the receiver can check; the envelope makes the
*force* unambiguous and leaves the *truth* exactly as unverifiable as it was.
So the question "what does the explicit tag buy" cannot be answered in
violations, only in reading cost and reading error.

**`free` does not remove the performative — it relocates it into a second
model call.** The force still has to be determined before the harness can act,
so the choice is not "declared force versus no force" but "declared by the
sender versus inferred by a third party", and the inference has a cost and an
error rate that the declaration does not. That relocation is what §2's
`reader_calls` column measures and what §4 reads the logs for.

## 4. Interpretation

> *Pending the runs.*

## 5. What I discarded, and what I would change

- **The first pass, at a turn limit of 6**, which ended 31 of 36 episodes at
  the limit: three deals, no refusals at all, and `correct` at 3 of 36. With
  almost every episode `open`, the outcome metrics could not separate the three
  conditions. The limit is a harness parameter applied identically to all
  three, so raising it favours none of them — but it changed the dataset from
  degenerate to usable, and that is worth stating plainly rather than quietly
  re-running. Those 36 rows are kept in `results.csv` with `max_turns=6` in
  `note`, and their logs under `logs/t6-*`, so the comparison between the two
  limits is in the data rather than in this sentence.
- **Letting `reject-proposal` carry a counter-offer.** The first live episode
  had both sides haggling inside `reject-proposal` messages — "no thanks, how
  about 150000" — which the protocol reads as *no price on the table*, so
  nothing could ever be accepted. The role prompts now say a counter-offer is a
  `propose`, identically in all three conditions. That the four-act vocabulary
  has no room for "decline and counter" is a finding about the vocabulary, not
  a bug in the harness, and it is the same gap as the missing `query-ref` in
  §3's last row.
