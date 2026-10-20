# Week 07: Agent Market

This report records observations as of 2026-10-20. The local market test passed
25/25 before and after the price change and again after the Round 1 memory and
review update in an isolated copy. The Week 06 compatibility test
also passed 21/21 in an isolated copy, so its report did not replace the Week
07 report ([`week06-precheck`](logs/week06-precheck/trade-test.log)). Round 1
and Round 2 both started on 2026-10-20. Deployment remains dependent on this
Mac staying awake and powered through the deadline.

## 1. Shop choice and memory

`market_buyer.py` reads the market kit's `{shops}` JSON, including each shop's
name, current card URL and newest-first reviews. It computes average stars and
counts low-star complaints about receipts. If another shop exists, a shop with
two such complaints, multiple reviews averaging at most two stars, or a
receipt trust failure in its own memory is left out of the candidate set. One
model call chooses among the remaining shops, separately labels trustworthiness
and feasibility, and gives a brief observed reason. The threshold protects
against treating one unverified review as proof while giving first-hand receipt
evidence priority. The buyer logs the candidates, exclusions and decision as
`shop_choice`.

Its own memory is `state/market-memory.json`, a local, uncommitted JSON object
keyed by **shop name**, not URL. Each entry retains its earlier counters and
last result, then appends timestamped observations containing item, budget,
outcome, offers, receipt and task evidence. For a new order, Python retrieves
same-item experiences at a nearby budget first; other-item sale prices are not
treated as prices for this item. A receipt mismatch or missing signature/task
marks `avoid`; an ordinary no-deal does not. Two later matching receipts can
clear the warning without deleting its original observations. A restarted
tunnel changes a URL without erasing that history. A Round 0 experience was imported
from the market log into memory: `maple-home` issued a bookshelf receipt at
370 after the buyer offered 325 on an order with budget 325. The observation
and the source ledger line are preserved in
[`round0-observation.json`](logs/challenge-2/round0-observation.json).

## 2. Reviews shown to the harness

The shop selector's JSON prompt contains each eligible shop's name, reviews,
public complaint pattern, and relevant direct observations. Its strict schema
returns only an eligible shop ID, separate trustworthiness and feasibility
labels, and a reason. The negotiation harness sees the selected shop's Agent
Card skills, latest seller message and compact task history. External reviews
and shop messages are supplied as data, not trusted instructions. A local
selection check using the actual Round 0 shop list and the imported memory chose a shop other than `maple-home`
([`selection-check.log`](logs/challenge-2/selection-check.log)); one such
selection does not establish a reliable avoidance rate. A second model call
phrases the public review using only the outcome of this encounter. Python
checks the permitted rating range, public price numbers, and unsupported
accusations, falling back to a fixed factual review when needed. It does not
publish the private budget or another shop's history. The initial Round 1
observations below predate this two-call policy and should not be presented as
a test of model-written reviews.

After the update went live, a bookshelf order with budget 230 selected
`outseop-26510122` while logging `trustworthiness=supported` and
`feasibility=uncertain`: the buyer cited a reported bookshelf sale at 260 but
had no evidence for a deal at 230 ([`market-kit.log`, line 3811](logs/market-kit.log#L3811)).
The negotiation ended with no receipt after offers no higher than 230. Its
memory recorded `no_deal`, zero trust failures, and `avoid=false`
([line 4041](logs/market-kit.log#L4041)). The public review stayed neutral
([line 4057](logs/market-kit.log#L4057)). A later chair order at budget 225
selected the same shop using four directly observed chair deals at 202 and
logged both trustworthiness and feasibility as supported
([line 4158](logs/market-kit.log#L4158)). These two orders show why a failed
deal should change feasibility evidence without becoming a dishonesty claim.
An initial local run exposed a review-gate bug: A2A delivered a price as
`120.0`, while the gate expected the text `120`. I normalized the observed
price to an integer; later local runs logged model-written factual reviews.
Separately, a live no-deal review attempted to repeat a seller counterprice,
so the code used its neutral fallback ([`market-kit.log`, line 4055](logs/market-kit.log#L4055)).
The current reviewer receives no seller price text for a no-deal order.
The final isolated market test passed 25/25. In its nine buyer runs, all nine
reviews used the model-written path and none used the fixed fallback; in the
review-selection test, the buyer chose the better-reviewed shop in all three
runs ([`test-report.json`](test-report.json), [`market-test.log`](logs/market-test.log)).

## 3. Rounds 0, 1 and 2

In Round 0 the kit registered both agents as `outseop-26510122` under GitHub
`outSeop` ([`market-kit.log`, lines 5-6](logs/market-kit.log#L5)). Its random
shop assignments included a failed card request to another shop (HTTP 530),
and later a verified chair purchase at 225
([line 306](logs/market-kit.log#L306)). That Round 0 deal did not display a
review. The first dashboard snapshot of my shop recorded four deals, market
share 0.08, revenue 677 (average price 169.25), and zero reviews
([`before.json`](logs/challenge-1/before.json)). In the first Round 1 order, the
buyer excluded `maple-home` using its own memory, selected `grand-furniture`,
then made no deal when the seller's counteroffer stayed above the order budget.
It saved `no_deal`, emitted a neutral three-star review, and the market accepted
that review ([`round1-avoidance.json`](logs/challenge-2/round1-avoidance.json)).
In the next order, `BeliveMe` supplied a receipt without the required matching
task and signature. The buyer kept the receipt details in private memory,
marked that shop `avoid`, and submitted a one-star review. The following choice
excluded `BeliveMe` and produced a verified lamp purchase at 120 from
`corner-store` with a four-star review; both reviews were accepted by the
market ([`round1-memory-review.json`](logs/challenge-2/round1-memory-review.json)).
Round 2 opened at about 21:21 KST. The first two orders in the kit log were
both for a bookshelf, with budgets 325 and 360. The buyer selected
`dragonash`, offered 281 on each order, received matching signed receipts,
and the kit added its buyer signature. The market accepted ledger #9813 and
#9862 as `verified True`, with both five-star reviews shown
([`market-kit.log`, lines 7970-8030](logs/market-kit.log#L7970)). At the
21:22:16 KST public-board snapshot, our GitHub-linked buyer and shop were
both alive; the buyer had three counted Round 2 deals in three orders and the
shop had one counted sale, at 262
([`confirmed.json`](logs/round2/confirmed.json)). The board totals are a
time-specific observation, while the two cited ledger entries show the
individual signed transactions.

## 4. Receipt signatures

The carried-over Week 06 seller receipt had a seller name, product ID and
price, but no task ID or signature. The Week 07 seller now signs canonical
UTF-8 JSON of `{shop, item, price, task}` with the local Ed25519 shop key and
puts `sig_seller` in the receipt data Part. It signs an integer price, even
though A2A may transmit it as a double. The kit subsequently verifies the
seller signature and adds the buyer signature when the receipt fits its
order. No failing local market-test run occurred during this change: the first
official run and the post-price-change run both passed 25/25. A live Round 0
receipt from another shop lacked `sig_seller`, and the kit logged the failed
signature checks ([`market-kit.log`, lines 186-187](logs/market-kit.log#L186));
this is an observed market failure, not a failure of my own shop.
In a later live Round 1 buyer order, the kit recorded `sig_seller ok,
sig_buyer added` and the market accepted ledger #8293 as `verified True`
([`market-kit.log`, lines 6710-6712](logs/market-kit.log#L6710)). I also
verified a live receipt's seller signature against the local Ed25519 public
key and checked that its `task` matched the negotiation. This confirms the
signing path before Round 2; it is not a Round 2 counted deal.
The same `sig_seller ok, sig_buyer added` sequence appeared in the first two
Round 2 purchases immediately before the market returned `verified True`
([`market-kit.log`, lines 8000-8003](logs/market-kit.log#L8000),
[`market-kit.log`, lines 8029-8030](logs/market-kit.log#L8029)).

## 5. Required challenges

**Shop price change.** At the snapshot above, share was 0.08 after four
deals and there were no reviews yet. `adapt_prices.py` derives a maximum
opening ask of 1.12 times each reserve from those figures, leaving the
reserves unchanged. The seller logs the original and effective asks; in the
post-change local test a lamp opening ask of 120 became 113
([`seller-under-test.log`](logs/seller-under-test.log)). After reconnecting
with the revised policy, the dashboard showed 16 cumulative deals, share
0.014, revenue 2684, and average sale price 167.75
([`after.json`](logs/challenge-1/after.json)). The earlier snapshot showed
share 0.08 and average price 169.25 after four deals
([`before.json`](logs/challenge-1/before.json)). These cumulative figures show
the observed change, but do not isolate a price-policy effect: three more
deals happened before the revised seller was reconnected, product mix changed,
and the market grew between snapshots.

**Dishonest participant.** The `maple-home` receipt exceeded the order budget
and the buyer's sole offer by 45. The kit refused to countersign it
([`market-kit.log`, lines 409-411](logs/market-kit.log#L409)). I recorded this
under the stable market name and passed it to the shop-choice harness in a
local selection check. After adding a deterministic exclusion for first-hand
receipt trust failures, the first live Round 1 `shop_choice` logged
`excluded ['maple-home']` and selected `grand-furniture`
([`market-kit.log`, line 1961](logs/market-kit.log#L1961)). That attempt ended
without a deal, so the buyer recorded an ordinary `no_deal` for the selected
shop rather than marking it dishonest ([`round1-avoidance.json`](logs/challenge-2/round1-avoidance.json)).
The later incomplete receipt from `BeliveMe` was independently marked `avoid`
and excluded on the next order; the log shows the resulting one-star review
and a subsequent verified purchase from another shop
([`round1-memory-review.json`](logs/challenge-2/round1-memory-review.json)).

## 6. Deployment

The course `agent.md` installer created a separate `agent-market/` folder in
the workspace and connected this agent directory. `shop_key.pem` and
`buyer_key.pem` are ignored and have mode 0600; the GitHub claim code is not
in the repository or the generated `join.sh`. The initial quick tunnel
registered both agents and served real orders. After a restart, Cloudflare
rejected new quick tunnel requests with HTTP 429. The documented SSH fallback
could not connect to `localhost.run` on port 22 from this network. A second
free tunnel provider, Pinggy, accepted SSH on port 443. Its public URL served
the shop's Agent Card and the market registered the shop again at 20:17 KST.
Two macOS `launchd` user services now keep the buyer running in `--buyer-only`
mode and the shop in `--shop-only` mode under the same GitHub-linked identity.
The shop service supervises a Pinggy tunnel, renews it before the free
60-minute expiry, and rejoins with the new URL, name and existing key.
The first scheduled renewal completed on 2026-10-20: the old connection was
stopped at 21:12:37 KST, a new tunnel was ready at 21:12:56, and the kit
registered the shop again at 21:13:00
([`market-kit.log`, line 7328](logs/market-kit.log#L7328)). The public board
then showed both agents alive. This demonstrates one successful renewal, not
continuous availability through the deadline.
The separate tunnel supervisor lives in `agent-market/` outside the submission;
the kit's live output remains in `logs/market-kit.log`. An additional local
`caffeinate -i` service prevents idle sleep while this Mac stays open; battery
exhaustion or closing the lid would still stop it. Both agents were online in
the Round 2 board snapshot, and the buyer had more than the required one
counted deal, but keeping them online through the deadline remains required.
In Round 0 the kit picks a random shop, so the live
`market_buyer` review and memory policy can only be observed from Round 1.
