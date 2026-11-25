# B1 local implementation candidate

Experiment defaults: `ENABLED=False`, `STUDY=""`, `START=END=0`. No future window
is configured. This module makes no market calls and changes no pricing,
floor, cash buffer, warranty, pool, limit, or quote-count settings.

`market_buyer.quote` records goods responses with the exact selected SKU,
response time, listing-read time and a fingerprint of the complete observed
SKU listing (including any revision/unit fields the listing provides).
Only whole-message explicit stock refusals become `SOLD_OUT`; no answer,
no-fit, warranty refusal, conditional statements and other-SKU refusals do not.
A later numeric ask or fit clears the mark. Inventory fingerprint changes
persist an invalidation, including a change back to an old quantity. Both the
response and listing snapshot expire after 300 seconds. A delayed response
from an older snapshot cannot undo a newer inventory invalidation.

This is evidence of the **observed public listing**, not proof of unchanged
physical seller inventory. An unobserved replenishment that leaves the public
listing identical cannot be detected. Historical terminal rows have neither
live snapshots nor these observations: they remain `UNKNOWN`. Historical
19/38 counts are not experimental eligibility or causal evidence.

`prefetch_pool` computes the existing fit/fresh order and rival partition once.
The candidate plan stably moves sold-out shops to the suffix **within each
fit/fresh × rival class**, filling exactly that class's existing index slots.
It retains the entire candidate set, fit/fresh slot counts, rival priority,
and the `k-1` prefetch quota. The chooser still sees the original `left` list.
Eligibility compares the same-input membership of the first **prefetch batch**
(the actual `start_quotes(early, ...)` call before the chooser returns).
Reordering within an otherwise identical concurrent batch is ineligible.
The later chosen-shop quote remains governed by the existing `best_quote`.

Source-enabled starting goods orders reserve ITT before the no-shops and other
early exits, including orders with no opportunity. The actual kit uses
`o-` plus twelve hex digits, not bare UUIDs. These complete keys receive stable
UUID5 allocation IDs; SHA256 uses the entire allocation UUID and study for a
fixed 50:50 probability. Bare full UUID order keys are supported without
truncation. UUID5 does not reconstruct a missing historical UUID. Hashing does
not guarantee exactly equal finite arm counts. Study/config changes, invalid
keys, or allocation failures use baseline.

Each order has immutable, atomic files under the local data directory:

- `assignment.json`: one durable allocation across concurrent retries.
- `decision.json`: full same-input baseline/treatment snapshot and intent.
- `attempt-<uuid>.json`: actual successful `Thread.start` boundary, phase,
  selected shop/SKU and time. This does not claim ACP/RPC success or payment.
- `terminal.json`: terminal state and kit hand-off, independently of attempts.

Publication uses a fully written/fsynced temporary file and exclusive hardlink,
then directory fsync; concurrent reservations cannot expose empty placeholders.
Observations prepare/fsync outside a short publication flock and sync the
directory after unlock. No filesystem work runs inside `quote_ctl`'s lock.
Shared admission permits four daemon IO workers, with a 150 ms caller deadline;
timed-out workers retain their slots until completion. Each read/record is at
most 256 KiB, decisions at most 256 candidates, audits at most 128 entries.
Oversize data fails to baseline without candidate truncation.

Reservation and decision IO failures cannot authorize treatment. A timed-out
reservation may subsequently appear on disk, but its caller stays baseline.
Previously durable ITT survives later decision/witness/terminal failures.
Missing attempts or terminal outcomes remain unknown, never inferred as zeros.
A witness failure **after a thread was started cannot undo that attempt**;
the terminal measurement reports witness failures, and the allocation is kept.
A total filesystem outage cannot provide a new durable denominator: the runtime
marks the order unreserved and follows baseline; existing terminal journaling
is a secondary record, not a forged ITT success.

Decision freshness is checked before and after journal IO and again inside
`start_quotes` immediately before the first prefetch dispatch. The runtime
rereads the complete local listing, fit/review ordering and rival inputs, then
the latest availability records. Both observation timestamps must still precede
their TTL at validation completion; a plan's age alone is insufficient. The live
clock is reread after reservation IO, so a reservation crossing END is retained
for audit but cannot authorize treatment.

A positive `proposed_reorder` in `decision.json` is a durable proposal with
`intended_reorder=null`, not confirmation of dispatch. It may finish after its
caller falls back. Actual thread-start witnesses record the final runtime
`intended_reorder` value and actual shop/SKU. Clearing or changing evidence can
therefore leave a proposal alongside baseline witnesses without falsely
claiming treatment. A fallback updates the shared early/full lists and chooser's
asked set to the actually dispatched baseline batch; quota and partitions stay
unchanged. An already started batch is not rewritten from later observations.

The recorded fixture in `testdata/b1_legacy_order.json` preserves real numeric
types, missing fields and the 34-to-20 candidate truncation. Identifiers are
anonymized with their original format retained and source SHA recorded. It is
a terminal fixture, not live snapshot evidence. Live tests use the repository's
listing and seller task schemas with fake external transport and real disk.

All work is local. Independent product review and formal security review are
required before any future activation; neither is established by these tests.
