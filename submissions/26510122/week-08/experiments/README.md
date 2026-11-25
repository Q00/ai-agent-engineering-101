# Seller introduction experiment — November 3, 2026

Follow-up: [seller-copy combinations and fresh confirmation](SELLER_COPY_COMBINATIONS.md)
tests 10 descriptions over 96 calls in a pre-quote first-contact scenario.
That follow-up selects and applies a new local Agent Card description; the
older experiment below is retained as a separate historical comparison.

The primary goal is **getting a buyer to contact the shop**. The token-saving
introduction is a promising presentation candidate on that metric. Request
counts and token usage are secondary diagnostics; a reduction in negotiation
rounds is not required for the introduction to be useful. This small probe did
not establish live-market conversion or profit. Existing seller copy and buyer
policies were left in place; no live service was restarted and no market messages
were sent.

## What was compared

Six descriptions/opening replies are preserved in `seller_copy_variants.json`:
ordinary inventory, token saving, explicit purchase steps, quick trading, clear
terms/certificates, and minimal wording. The stocked product, price, grade,
certificate, warranty, reviews and firsthand-history conditions were identical.
The full messages differ in length and emphasis, so this does not isolate the
causal effect of the word “tokens.” The claims are not proven capability
differences between shops.

For each requested CLI model, six circular arrangements were
tested in both food and goods contexts: 12 choices per model. Every introduction
appeared in each position twice. Anonymous IDs were fixed to display positions.
Jobs were shuffled with a saved seed. Each call used a fresh temporary context.
The buyer's ordinary objective was useful surplus and valid settlement; the
selection prompt did not ask it to minimize token use.

**Visibility limitation:** our current `market_buyer_r3.choose` does not consume
Agent Card descriptions. Its card lookup happens after selection and only reads
the endpoint. Therefore this choice experiment measures what happens **if the
descriptions are visible**, not an improvement to our deployed selection flow.
Other agents' use of descriptions was not verified.

## Observed choices

| Introduction | gpt-5.6-luna, n=12 | gpt-6-luna, n=12 | Claude sonnet, n=12 |
| --- | ---: | ---: | ---: |
| Ordinary inventory | 1 | 0 | 1 |
| Token saving | 6 | 4 | 0 |
| Explicit purchase steps | 0 | 2 | 3 |
| Quick trading | 2 | 1 | 2 |
| Clear terms/certificates | 2 | 4 | 4 |
| Minimal wording | 1 | 1 | 2 |

Ten of twelve choices in **each of the three** model groups came from the first two displayed
positions. Rotation balances exposure, but this still shows strong position/ID
sensitivity and weak discrimination when economics are tied. These are small
descriptive samples, not independent real customers or statistical proof of an
advertising effect. The model groups should remain visible separately. Claude
frequently called the structured terms equivalent and its choice arbitrary.
Its zero token-saving selections are not evidence that it dislikes that wording.
Clear terms/certificates were its most frequently selected full description,
and explicit purchase steps came next. A practical next copy candidate can
combine concise negotiation with clear conditions and a concrete receipt path;
that new combination remains untested.

Two additional independent buyer-role sub-agents received different description
orders. Under ordinary buying priorities, one weakly preferred explicit steps;
the other explicitly used displayed order to break an economic tie. When
conversational overhead was explicitly prioritized, the first preferred minimal
wording, while the second retained its tie-break. Their inherited model identity
was not separately reported; these are two judges, not two additional confirmed
model families. Original answers and mappings are preserved in
`../logs/seller-copy-independent-judges-2026-11-03.json`.

## Actual buyer-code probe against an offline shop

The existing `buyer_r3.negotiate` ran twice for each opening variant using the
real `gpt-5.6-luna` follow-up model. All HTTP calls were intercepted by a local
MockTransport. Every mock shop opened at 900, received an initial 650 offer,
countered at 800, and issued a placeholder receipt only for an offer of at least
800. Buyer authorization was 850. No seller model or real checkout ran.

| Opening variant | Requests per negotiation | Buyer model calls per negotiation | Mock receipts, n=2 |
| --- | ---: | ---: | ---: |
| Ordinary inventory | 4 | 2 | 1 |
| Token saving | 4 | 2 | 0 |
| Explicit purchase steps | 4 | 2 | 2 |
| Quick trading | 4 | 2 | 1 |
| Clear terms/certificates | 4 | 2 | 1 |
| Minimal wording | 4 | 2 | 0 |

All twelve negotiations sent three numeric offers. The first offer is fixed
before the model reads the opening; the model sees that opening in its first
follow-up's recent history. The buyer already sends very short numeric messages,
and the mock seller's subsequent replies are fixed. This probe therefore cannot
measure savings in a real seller model's generation cost. Mock receipts contain
placeholder signatures and are **not** settlements or real cryptographic proof.

The two repetitions per copy are insufficient to rank agreement reliability.
In failed mock negotiations the last offer remained below the attainable 800
price, despite the 850 authorization. The current follow-up prompt does not
tell the model how many of its three permitted offers remain. That is a concrete
context omission and a useful separate improvement hypothesis, not an established
cause of each failure. Buyer policy was not changed by this experiment.

Actual CLI input/output usage, cached input and model replies were preserved.
Negotiation prompts averaged 534–557 serialized bytes, while reported CLI input
usage included roughly 12.5k–13.4k tokens of variable system overhead. Caching
also differed between calls. These raw usage counts do not identify a copy-only
cost effect or monetary saving. The invariant call count is the clearer result.

## Evidence and reproduction

The initial GPT comparison used **49 CLI call attempts: 48 completed and one timed out**. The first
`gpt-6-luna` goods/rotation-0 call exceeded 60 seconds. Its original failure is
preserved, and only that failed case was retried in a separate run with a
90-second timeout. No successful case was rerun or selected away. The aggregate
rejects duplicate successful cases rather than choosing a favorable result.
The later Claude availability attempt failed authentication, and all 12 choices
after login completed. Including both phases gives **62 CLI attempts, 60
completed model decisions, and two preserved failures**. That covers 36 shop
choices across three requested models and 12 mock negotiations with 24 follow-up
model calls. The separate sub-agent judgments are not included in those totals.

Run from the week-08 directory with existing CLI model access; output directories
must be new. The commands make real model calls but no marketplace requests.

```sh
.venv/bin/python experiments/seller_copy_probe.py \
  --output logs/seller-copy-new-run --workers 3
WEEK08_MODEL=gpt-6-luna .venv/bin/python experiments/seller_copy_probe.py \
  --phase selection --output logs/seller-copy-second-model-new-run --workers 2

# Optional: retry only a failed case, preserving its original run.
WEEK08_MODEL=gpt-6-luna WEEK08_MODEL_TIMEOUT=90 \
  .venv/bin/python experiments/seller_copy_probe.py --phase selection \
  --case selection:goods:0 --output logs/seller-copy-new-retry --workers 1
```

The initial preserved runs are `../logs/seller-copy-probe-2026-11-03/`,
`../logs/seller-copy-probe-gpt6-luna-2026-11-03/` and
`../logs/seller-copy-probe-gpt6-luna-retry-2026-11-03/`. Each includes a manifest,
full model prompts/answers/usage and per-case outcomes. The aggregate JSON is
`../logs/seller-copy-aggregate-2026-11-03.json`, generated with:

```sh
.venv/bin/python experiments/seller_copy_report.py \
  logs/seller-copy-probe-2026-11-03 \
  logs/seller-copy-probe-gpt6-luna-2026-11-03 \
  logs/seller-copy-probe-gpt6-luna-retry-2026-11-03 \
  --output logs/seller-copy-new-aggregate.json
```

The expanded aggregate preserving the Claude failure and completed comparison
is `../logs/seller-copy-aggregate-with-claude-2026-11-03.json`. Reproduce it by
adding `logs/seller-copy-claude-smoke-2026-11-03` and
`logs/seller-copy-claude-authenticated-2026-11-03` to the report command and using
a new output filename. Earlier aggregates and raw runs are not overwritten.

Keep token-saving wording as a test candidate alongside clear terms. A next
candidate could combine a brief-chat promise with concrete price/receipt steps;
that combination has not been evaluated here. Establishing real efficiency
requires observing replies, calls and successful checkouts in buyers that
actually see and act on these introductions.

## Claude selection backend

`seller_copy_probe.py --provider claude --model sonnet --phase selection` now
uses Claude Code for the same buyer instructions, description variants,
structured choice schema, food/goods conditions and position rotations. It runs
in a fresh temporary directory with safe mode, no built-in/MCP tools and no
session persistence. The `sonnet` alias is recorded as requested; resolved model
identifiers are saved from `modelUsage` when available. Vendor CLI system
wrappers differ, so this is not a perfectly controlled model-only comparison.

The local CLI is Claude Code 2.1.233. On November 3, `claude auth status` reported
`loggedIn=false`, and no Anthropic/third-party authentication was configured in
the environment. The single real smoke attempt returned **“Failed to
authenticate: OAuth session expired and could not be refreshed.”** It is
preserved in `../logs/seller-copy-claude-smoke-2026-11-03/`; at that point no
Claude buyer choice had completed. An auth failure or missing `structured_output`
cannot be counted as a preference. The full 61-test suite passes, including
those adapter checks.

After the user authenticated, the follow-up in
`../logs/seller-copy-claude-authenticated-2026-11-03/` completed all 12 choices
with no failures. All twelve actual user prompts matched the corresponding GPT
selection prompts exactly, and every description appeared in each position
twice. The requested alias was `sonnet`; each response's model usage included
`claude-sonnet-5`, and the CLI also accounted usage for
`claude-haiku-4-5-20251001`. Those additional usage entries are not treated as
separate Haiku buyer judgments. The CLI's internal wrapper/model routing is a
limit of cross-provider comparisons.

After authenticating the local CLI, run the contact-choice comparison only:

```sh
claude auth login
.venv/bin/python experiments/seller_copy_probe.py --provider claude --model sonnet \
  --phase selection --output logs/seller-copy-claude-authenticated-new-run --workers 2
```

The response adapter follows the official
[programmatic CLI documentation](https://code.claude.com/docs/en/headless#get-structured-output)
(`structured_output`, usage and failure handling) and the
[CLI reference](https://code.claude.com/docs/en/cli-reference)
for safe mode and authentication. The implementation preserves normal OAuth
authentication rather than using `--bare`, which does not read OAuth credentials.
