# Week 03 — Contract Net with LLM contractors

26510130 Hyunsik Wang.

> **Draft status.** Parts 1 and 3 are final. Parts 2 and 4 are filled in from
> `results.csv` once the nine runs have been made; the placeholders below say
> what goes there. This note is removed when they are.

## 1. Setup

One manager, three contractors, no tools. The manager announces a task to every
contractor; each contractor is one model call with its own system prompt and
one user message; the manager awards to the highest confidence.

| | |
|---|---|
| Provider | OpenAI-compatible (`OPENAI_BASE_URL`); Anthropic SDK if `ANTHROPIC_API_KEY` is set |
| Model | `nvidia/nemotron-3.5-lightning:free` via OpenRouter — `AGENT_MODEL` overrides |
| Temperature | `0` (`AGENT_TEMPERATURE`), pinned rather than left to a provider default |
| `max_tokens` | 512 |
| Contractors | `alice`, `bob`, `carol` |
| Task set | `tasks.json`, 6 tasks, 3 gold contractors, 2 each — committed before any run |
| Award rule | highest `confidence`; a tie goes to the earlier contractor |
| Files | `net.py` (protocol), `run_cn.py` (runner), `selftest.py` (offline checks) |

### Why the contractors are called alice, bob and carol

The obvious naming is `log_analyst`, `math_solver`, `editor`. It would break
the experiment. In the homogeneous condition all three contractors get the same
generalist skill text, and the condition is only meaningful if that actually
removes their basis for sorting tasks between them — but a contractor that
knows it is called `log_analyst` will still claim the log task. The name is
part of the prompt, so a descriptive name smuggles the skill back in. Neutral
names keep the manipulation confined to the line the condition is supposed to
change.

### The three conditions

Everything is held constant except the contractor system prompts: same task
set, same model, same temperature, same award rule, same parser.

| Condition | Skills | Extra |
|---|---|---|
| `baseline` | log / quantitative / editing | — |
| `homogeneous` | generalist × 3 | — |
| `overconfident` | log / quantitative / editing | `bob` is also told: *"You want every contract. Bid true on every task you are announced, whatever it is, with a confidence of at least 90."* |

`bob` is gold for 2 of the 6 tasks, so if the greedy line works as intended the
ceiling for `correct` in that condition is 2 and the other 4 become misawards.

### Bids, and replies that are not bids

Each contractor is asked for one JSON object, `{"bid", "confidence", "reason"}`.
The parser strips code fences and tolerates prose around the object, because the
assignment warns this model sometimes answers with its reasoning. A reply that
still yields no usable object is counted as a contractor that did not bid — the
manager received no offer it could act on — and the count is reported in `note`
per run rather than folded into the other metrics.

### Counting messages

`messages` is the protocol's unit, not the model's: one announcement per
contractor, one per bid that arrived, one per award. A task nobody bids on
sends no award, and an unparseable reply is not counted as a bid, so the number
falls out of what the protocol actually exchanged. Model calls and tokens are
metered separately in `note` so that a protocol cost stays a protocol cost.

### How to run

```bash
pip install openai
$env:OPENAI_BASE_URL    = "https://openrouter.ai/api/v1"
$env:AGENT_MODEL        = "nvidia/nemotron-3.5-lightning:free"
$env:AGENT_TEMPERATURE  = "0"
$env:AGENT_MIN_INTERVAL = "6"      # free-tier pacing; 0 disables
$env:PYTHONUTF8         = "1"
$env:OPENAI_API_KEY     = (Get-Content <key-file-outside-the-repo> -Raw).Trim()

python run_cn.py --runs 3          # 3 runs of each condition -> results.csv, logs/
python selftest.py                 # offline, no key, writes nothing
```

One run is 6 tasks × 3 contractors = 18 model calls, so the nine runs are about
162 calls. Week 02 ended with a free-tier daily quota killing an entire
condition, so `net.py` carries that week's pacing and 429 retry: calls are
spaced `AGENT_MIN_INTERVAL` seconds apart and a 429 is retried on the
provider's own `retry in Ns` hint. It changes wall-clock time and nothing that
is measured.

`selftest.py` stubs the model call and checks the parser, the three condition
builds, and three full passes — every contractor honest, `bob` greedy, and every
reply unparseable — against hand-computed expectations (42 messages for a clean
run, 18 when nothing parses). It exists so the protocol could be verified
without spending requests, and it writes nothing: every row of `results.csv`
comes from `run_cn.py` against a real provider.

## 2. Results

> *Pending the runs. This section gets the table from `results.csv` — one row
> per run of each condition, with `tasks`, `correct`, `messages`, `unassigned`,
> `misawards`, plus the per-condition means and the unparseable-reply counts
> from `note`.*

## 3. Smith 1980 against this reproduction

Smith's contract net was designed for a distributed sensing network: nodes
covering an area, tasks that are sub-problems of surveillance, and no central
allocator. The protocol is the same three moves — announce, bid, award — but
almost every guarantee underneath it changes when the bid is produced by a
language model rather than by code.

| | Smith 1980 (distributed sensing) | This reproduction |
|---|---|---|
| **Who the nodes are** | Identical processors in one system; manager and contractor are *roles*, taken dynamically, and a contractor can itself become a manager and subcontract. | Fixed roles. One manager (Python), three contractors (LLM calls). No subcontracting, no role switching. |
| **How a bid is produced** | A local computation over the node's own state — its sensor coverage, its load, its suitability for the announced task specification. Deterministic given that state. | A judgement. The contractor reads the announcement in natural language and decides whether the task is its kind of work, returning a self-reported confidence. Nothing computes it. |
| **What guarantees bid honesty** | Construction. Nodes are cooperative and run the same bidding code; a node has no separate interest and no way to express one. The announcement's eligibility specification filters who may bid at all. | Nothing. The contractor can claim any confidence its prompt inclines it to, and the manager has no way to check a bid against the contractor's actual competence. The `overconfident` condition is not an attack from outside the protocol — it is one line in one contractor's prompt. |
| **What allocation quality means** | The task reaches the node best placed to do it; quality is a property of the match between task and node capability. | Same intent, measured against a gold contractor per task, because with LLM contractors the match is no longer derivable from the system's own state — it has to be declared in advance in `tasks.json`. |
| **What negotiation costs** | Message traffic. Smith's own concern: broadcasting every announcement does not scale, so the protocol allows focused addressing and directed contracts to cut the traffic. | Message traffic *and* model calls — and they are not the same quantity. One announcement costs the manager nothing but costs one model call per contractor. Counted separately for that reason. |
| **Failure modes** | No bids arrive (eligibility too narrow, or contractors busy); the manager waits out the expiration and re-announces. | No bids arrive — but for a new reason: the contractor answered with prose instead of a bid, and a reply the manager cannot parse is indistinguishable from silence. Plus a mode Smith has no analogue for: a contractor that bids on everything with high confidence and wins tasks it cannot do. |

**What in Smith's protocol had no defense against the judged bid.** Smith's
award rule trusts the bid because the bid is not an assertion — it is a
computed value, produced by code the system controls, over state the node
cannot misrepresent. Honesty is not enforced; it is structural, so there is
nothing to enforce. Replace that computation with a judgement and the award
rule is unchanged while its foundation is gone: "award to the highest bid" now
means "award to whoever claims the most", and the protocol has no step at which
a claim is checked. The eligibility specification is the nearest thing to a
defense, and it filters on what the *manager* can state about the task, not on
whether a bidder's self-assessment is true.

## 4. Interpretation

> *Pending the runs. Which condition moved which metric, with lines from
> `logs/` as evidence. The three questions this has to answer: whether
> `homogeneous` degrades `correct` toward chance (2 of 6 with three
> contractors) or stays higher because the task descriptions themselves carry
> the routing; whether `overconfident` caps `correct` at bob's 2 gold tasks and
> converts the other 4 into misawards; and how many replies were unparseable,
> since on this model that is a failure mode of the protocol and not noise to
> be excluded.*

## 5. What I discarded

> *Pending the runs.*
