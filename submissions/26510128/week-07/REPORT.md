# Week 07 — Agent Market: Reputation and Verification

Student ID: **26510128**  
Harness: custom OpenAI-compatible Python decision loop, local Ollama `qwen3:4b-instruct`.

## Scope and current status
This submission extends the successful Week 06 A2A buyer/seller agents.
**The student's local market test printed `25/25 passed -> PASS` on 2026-10-20.**
This is a LOCAL test result, not proof of joining the live market.
Live market registration and Round 2 verified deals have since been observed in the student's market logs. Include the actual locally generated `logs/market-kit.log` in the eventual PR.

## 1. Buyer shop selection and memory
`market_buyer.py` reads the kit's shops JSON containing shop name, current URL,
and newest-first reviews. It ranks candidates using review stars, warning
language about dishonesty/overcharging, and prior memory, then lets the local
model select a candidate within a trust-protected shortlist. It remembers
experiences by stable shop **name**, not changing tunnel URL, in
`.market/buyer-memory.json`. That file is runtime state and should not be
committed as though it were an experiment log.

## 2. Reviews and the harness
At most five shortlisted candidates are supplied to the model, each with
a risk score, the four most recent reviews and previous buyer experiences.
The buyer rejects deceptive receipts (wrong item/task, over-budget or price
different from its most recent offer), keeps the signed data Part unchanged,
and prints a final JSON line with shop URL, receipt or null, stars and review.
A hard budget check runs outside the model.

## 3. Rounds 0, 1 and 2 — observations with stated gaps
The lecture defines R0 (random shop, no reviews), R1 (review/memory-based shop
selection), R2 (shop and buyer signed receipts tied to a genuine market order).
**Do not fill in trade counts, market shares or round comparisons until they
are visible in the actual dashboard and/or `logs/market-kit.log`.**
- Round 0 — actual shop, price, result: **pending evidence**
- Round 1 — real A2A purchases observed in the student's logs, e.g. ledger #6920 (lamp 105, verified True) and ledger #6989 (lamp 105, verified True). Preserve the original log and dashboard values for any full comparison.
- Round 2 — verified True / review accepted observed at ledger #9982 (bookshelf 340). The subsequent official board query also confirmed 1,459 buyer counted deals (a later snapshot).

## 4. Signature implementation, testing and troubleshooting
The seller signs canonical sorted-key, compact UTF-8 JSON over the integer
`{shop, item, price, task}` with Ed25519 `shop_key.pem`. The task field
is exactly the A2A task ID; A2A data Parts may represent integer prices
as floats in transit, so cryptographic signing happens on the integer.
The market kit verifies `sig_seller` and attaches `sig_buyer`.
The provided test output shows `seller_signed_receipt` passed 3/3;
overall `25/25 passed -> PASS` on 2026-10-20. On 2026-10-27, after the new buyer and seller price policy changes, the student reran the **official market test: 25/25 PASS**, and separate offline safety tests passed **5/5**.

Failure/repair provenance: starting from the Week 06 seller, the original
receipt lacked `task` and `sig_seller` (as seen in the **source code**, not
a claimed failed 7-week test run). We changed the receipt format, added
canonical signing and validated the signatures in the 7-week test.
No failed local signature test was observed or claimed.

## 5. Challenges
### Challenge 1 — seller adapts asking price

The current `pricing_policy.py` reads the official read-only market board for
the student's own Round 2/1 counted deals and summary ratings. It caches the
response briefly and imposes a hard `ask >= reserve` invariant. With no
counted sales, it quotes the reserve; with some sales, a reserve + 5
introductory margin; with at least three counted sales and two high-rating
reviews, a reserve + 10 cap. Low ratings or documented complaints reduce the
margin. Signed local A2A receipts are logged but **not** represented as
market-verified trades because local tests also create them.

Two real seller board snapshots were recorded locally: before on 2026-10-21,
and after deployment on 2026-10-27. The newer log also proves the revised
pricing policy executed and produced actual completed A2A sales. These
snapshots are **not an equal-window controlled comparison**: almost all
intervening sales predate the policy change. Board shares and cumulative
revenues must not be used to claim causal uplift. Both genuine JSON snapshot
files and original logs should be committed under `logs/challenge-1/` once
screened for secrets.

### Challenge 2 — dishonest participant avoidance

The buyer uses published reviews and its own past trade outcomes to rank
shops. A documented live market response from `bright-living` attempted to
impersonate the market operator and tell AI buying agents to accept a price
and give five stars. The original buyer refused an over-budget offer and did
not issue a five-star review. After that observation, `market_buyer.py` was
modified to detect this specific class of market-authority instruction,
refuse the transaction before feeding that seller text to the harness, and
persist an `injection_attempts` penalty against the *shop name*. On future
orders, that risk influences the initial shop ranking even when its tunnel
URL changes.

The actual Round 2 buyer log on 2026-10-27 **now demonstrates
subsequent live avoidance**. At 16:12:29 and 16:12:51, the buyer logged
`[safety selection] excluded=['bright-living'] reason=observed-authority-spoofing`.
In the same two market orders it selected `seochanit`, purchased lamps
at 100, and received official market ledger entries #1001170 at 16:12:45
and #1001218 at 16:13:07, each `verified True, review accepted`.
These are genuinely separate live orders, not fabricated test runs.
The older spoof message was detected retrospectively from unaltered logs,
while the two exclusion decisions occurred in **new live orders**.

The original log, the extracted evidence file and the new real
`[safety selection]` and corresponding `[shop selected]`/ledger lines
must be committed as audit artifacts in `logs/challenge-2/` after
checking for secrets. This shows a successful memory-based exclusion
without asserting a newly observed spoof message after deployment.
 
### Verified deployment of adaptive pricing, 2026-10-27

The student's original unedited `logs/market-kit.log` contains
pre-deployment chair opening asks at 15:19:54 and 15:21:07:
`reserve=180 ask=195`, together with `adjustment=0` and
`review_count=0` (legacy implementation). Those two deals completed at
180 and 195.

After successful market re-registration at 15:21:55/15:21:56, the log
shows the **new** `[pricing policy]` evidence object with:
`market.source=official-public-market-board`,
`market.round2.counted_deals=9712`, `review_count=9350`,
`mean_stars=4.12`, and raw `share=0.029`. Round 1 feedback timed
out, but Round 2 was available. The policy identified an observed
market-sales condition and capped the opening ask at reserve + 5.

| Item | Reserve | Model ask before policy | Actual adaptive opening ask | Signed local A2A completed sales observed |
| --- | ---: | ---: | ---: | --- |
| lamp | 100 | 115 | 105 | 1 at 105 (15:23:15) |
| bookshelf | 260 | 275 | 265 | 1 at 265 (15:23:31) |
| chair | 180 | 195 | 185 | 3 at 185 (15:23:52, 15:24:16, 15:24:19) |

This shows that the **new price policy actually ran in the deployed
seller** and at least five A2A tasks completed after the change.
These `[seller completed]` lines do **not** by themselves prove that
each transaction was accepted in the market ledger (which has separate
verification criteria); cross-check the board's counted-deal change.
The figures are raw observations. The old and new requests involve
different customers, and the market statistics are cumulative, so
they do not identify causal changes in market share or revenue.

### Round 2 public-board before/after snapshot comparison

The student provided a second read-only official Round 2 board response
on 2026-10-27 and saved the actual raw row locally to
`logs/challenge-1/after-pricing-<timestamp>.json`. Values below come from
the student's two supplied API outputs, not simulated data.

| Metric | Before snapshot, 2026-10-21 | After snapshot, 2026-10-27 | Difference |
| --- | ---: | ---: | ---: |
| Counted seller deals | 1,996 | 9,717 | +7,721 |
| Visits | 2,601 | 12,689 | +10,088 |
| Share (raw `share` field) | 0.090 | 0.029 | -0.061 |
| Average review stars | 4.06 | 4.12 | +0.06 |
| Reviews | 1,786 | 9,355 | +7,569 |
| Total revenue | 372,169 | 1,787,842 | +1,415,673 |
| Cumulative average transaction price (`revenue/deals`) | 186.46 | 183.99 | -2.47 |

The **inter-snapshot** average transaction price of the 7,721
additional counted deals is `1,415,673 / 7,721 = 183.35` in market
price units. This is **not** the post-policy-only average: the snapshots
are six days apart, and the new policy was observed running near the end
of that interval. The raw share number decreased from 0.090 to 0.029
even as cumulative deals increased; this is a descriptive market outcome,
not evidence of the price change's effect.

At the first logged revised pricing decision, the Round 2 board count
was 9,712. The subsequent after-snapshot count of 9,717 is an increase
of **5 counted market deals** during this narrower period, consistent
with five `[seller completed]` tasks. Each individual seller task was
not matched to a particular verified ledger entry, so this is supporting
aggregate evidence rather than receipt-by-receipt verification. The
board share field remained 0.029 at displayed precision for these
two 2026-10-27 observations. A revenue baseline precisely at the new
policy's activation was **not collected**, so no reliable post-policy-only
average transaction price can be calculated from market figures.

The two saved JSON files and the original unmodified market-kit log
are the audit evidence; no fake deals, ratings or reviews are generated.

### Real Round 2 seller baseline from 2026-10-21 (BEFORE the new live-price policy restart)

The student queried the *official public* `/api/board?round=2` endpoint, and
saved the response to `logs/challenge-1/seller-<timestamp>.json` locally.
The observed values were:

| Round 2 board field | Observed value |
| --- | ---: |
| Seller name | JangSeongWoong |
| Seller alive | True |
| Counted seller deals | 1,996 |
| Buyer counted deals | 1,459 |
| Seller visits | 2,601 |
| Seller reviews | 1,786 |
| Seller average stars | 4.06 |
| Seller revenue | 372,169 |
| Seller share (raw API value) | 0.09 |

Seller and buyer `owner.login` both matched `JangSeongWoong`.
This is **not** evidence of the *revised* pricing policy causing the growth:
the student's local checked-out branch was still `01dce5f` when these values
were recorded; the updated pricing policy had not been pulled/restarted.
Keep the recorded JSON as the truthful before snapshot. Recheck prices,
counted sales, share and reviews after a controlled deployment to compare.
The API's `share` field is preserved as reported; do not mislabel units.

### Challenge 2: extracted observations and confirmed live avoidance, 2026-10-27

After the updated code passed the user's official 25/25 market test,
the student ran `extract_spoofing_evidence.py` against the **existing
genuine** `logs/market-kit.log`. The script reported **811 matching
response log lines**, all attributed by the extractor to
`bright-living`, and wrote
`logs/challenge-2/observed-spoofing.json` locally. It records raw
matching text, original log line number and SHA-256 for each.
Example reported line numbers: 328, 331, 334, 337, 340.
The 811 lines are **not** 811 proven independent dishonest
transactions or necessarily distinct orders: repeated responses
during one negotiation may be counted more than once. The shop
attribution is inferred by matching the most recent selected shop
within the current order and should be cross-checked against the
surrounding unmodified log before making a firm accusation.

One restart attempt initially failed with HTTP 400
`unknown claim code`, but a **subsequent successfully operating
Round 2 session** generated live evidence. At 16:12:29,
order `o-cbd2c7bf` (lamp, budget 105) logged
`[safety selection] excluded=['bright-living']`;
at 16:12:32 it selected `seochanit` instead, at 16:12:43 the
market kit printed `sig_seller ok, sig_buyer added`, and at 16:12:45
ledger #1001170 was `verified True, review accepted` (lamp at 100).
The next order, `o-9d992c65` (lamp, budget 110), again excluded
`bright-living` at 16:12:51; selected `seochanit` at 16:12:54;
and produced another verified 100-price lamp deal, ledger #1001218
at 16:13:07. The original unmodified log excerpt containing these
lines is the strongest **live challenge-2 validation**.

The exclusion is based on historical source evidence imported through
the audit file. Neither the 811 matched log lines nor a blacklist entry
are themselves new live trades. Keep source observations and subsequent
avoided-order evidence separate, and never edit genuine source logs.

### Live dishonest-message observation

At 21:06 in the student's market log, seller `bright-living` included a
`[MARKET NOTICE FOR AI BUYING AGENTS ...]` directive trying to impersonate
market authority and induce both over-budget acceptance and a 5-star review.
The earlier buyer did **not** comply with the over-budget offer
(asked 270, budget 250) and returned no deal with 3 stars. That
historical interaction alone demonstrated only budget-rule enforcement;
the separate **2026-10-27 16:12 live exclusion and verified alternative
purchases** documented above complete the follow-up evidence.

## 6. Deployment — LIVE MARKET REGISTRATION OBSERVED
The student launched the course kit on a local Windows PC through a Cloudflare quick tunnel; log entries confirmed both seller and buyer registered under `JangSeongWoong`. A subsequent Round 2 board response confirmed both `alive: True`, and a real ledger line verified a signed purchase. Continuous operation through the deadline still requires keeping the PC, Ollama, market kit and tunnel available.
Use the same local `shop_key.pem` to sign if deploying the seller separately.
Never commit `*.pem`, a claim code, API secrets or tokens.
Evidence of live verified Round 2 purchase: ledger #9982, bookshelf 340; `verified True, review accepted`. The tunnel URL can change on reconnect and should be read from the kit log. Do not treat a point-in-time `alive` check as proof of uptime at grading deadline.

## Testing and evidence
Local evidence provided by the user:
- `seller_signed_receipt` 3/3 passed
- `buyer_reads_reviews` 3/3 good-shop selections (test case 1 passes)
- latest full market test on 2026-10-27: **25/25 passed -> PASS**
- separate local policy unit tests on 2026-10-27: **5/5 passed**

Run again after changing source, commit test-generated `test-report.json`
and `logs/market-test.log`, and run `check_week07.py` after live joining.
The final check additionally validates the registered shop and buyer.

## Submission safety
Only `submissions/26510128/week-07/` belongs in the Week 07 PR;
keep Week 06's prior PR unchanged. Keep all private key files untracked.
