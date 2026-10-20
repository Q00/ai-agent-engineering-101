# Week 07: Agent Market (work in progress)

This report records observations as of 2026-10-20. The local market test passed
25/25 both before and after the price change. The Week 06 compatibility test
also passed 21/21 in an isolated copy, so its report did not replace the Week
07 report ([`week06-precheck`](logs/week06-precheck/trade-test.log)). Live Round
1 and Round 2 had not
started at the time of this draft; their results and the final deployment status
must be added before submission.

## 1. Shop choice and memory

`market_buyer.py` reads the market kit's `{shops}` JSON, including each shop's
name, current card URL and newest-first reviews. It computes average stars and
counts low-star complaints about receipts. If another shop exists, a shop with
two such complaints or multiple reviews averaging at most two stars is left
out of the candidate set. The harness chooses one remaining shop ID. This
keeps an untrusted review from deciding the result by itself. The buyer logs
its choice and reason as `shop_choice`.

Its own memory is `state/market-memory.json`, a local, uncommitted JSON object
keyed by **shop name**, not URL. Each entry records attempts, deals, failures,
last item, last outcome and last price when a sale happened. A restarted tunnel
changes a URL without erasing that history. A Round 0 experience was imported
from the market log into memory: `maple-home` issued a bookshelf receipt at
370 after the buyer offered 325 on an order with budget 325. The observation
and the source ledger line are preserved in
[`round0-observation.json`](logs/challenge-2/round0-observation.json).

## 2. Reviews shown to the harness

The shop selector's JSON prompt contains each eligible shop's name, reviews,
computed risk and matching entry from the buyer's own memory. The strict
response schema allows only an eligible candidate ID as `action` and a null
`price`. The negotiation harness sees the selected shop's Agent Card skills,
latest seller message and compact task history. External text is supplied as
task data, not trusted instructions. A local selection check using the actual
Round 0 shop list and the imported memory chose a shop other than `maple-home`
([`selection-check.log`](logs/challenge-2/selection-check.log)); one such
selection does not establish a reliable avoidance rate.

## 3. Rounds 0, 1 and 2

In Round 0 the kit registered both agents as `outseop-26510122` under GitHub
`outSeop` ([`market-kit.log`, lines 5-6](logs/market-kit.log#L5)). Its random
shop assignments included a failed card request to another shop (HTTP 530),
and later a verified chair purchase at 225
([line 306](logs/market-kit.log#L306)). That Round 0 deal did not display a
review. The first dashboard snapshot of my shop recorded four deals, market
share 0.08, revenue 677 (average price 169.25), and zero reviews
([`before.json`](logs/challenge-1/before.json)). Round 1 and Round 2 outcomes,
including a counted Round 2 deal, remain to be observed.

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

## 5. Required challenges

**Shop price change.** At the snapshot above, share was 0.08 after four
deals and there were no reviews yet. `adapt_prices.py` derives a maximum
opening ask of 1.12 times each reserve from those figures, leaving the
reserves unchanged. The seller logs the original and effective asks; in the
post-change local test a lamp opening ask of 120 became 113
([`seller-under-test.log`](logs/seller-under-test.log)). The before/after
market share and average sale price comparison still needs a live after
snapshot once the revised shop is reachable.

**Dishonest participant.** The `maple-home` receipt exceeded the order budget
and the buyer's sole offer by 45. The kit refused to countersign it
([`market-kit.log`, lines 409-411](logs/market-kit.log#L409)). I recorded this
under the stable market name and passed it to the shop-choice harness in a
local selection check. A live Round 1 or 2 choice showing avoidance is still
needed.

## 6. Deployment

The course `agent.md` installer created a separate `agent-market/` folder in
the workspace and connected this agent directory. `shop_key.pem` and
`buyer_key.pem` are ignored and have mode 0600; the GitHub claim code is not
in the repository or the generated `join.sh`. The initial quick tunnel
registered both agents and served real orders. After a restart, Cloudflare
rejected new quick tunnel requests with HTTP 429. The documented SSH fallback
could not connect to `localhost.run` on port 22 from this network. One macOS
`launchd` user service keeps the **buyer** running in `--buyer-only` mode under
the same GitHub-linked identity. A second service starts the shop with
`--tunnel --shop-only` and retries after a 10-minute throttle if the quick
tunnel fails. The first separate shop attempt again returned no tunnel URL
within 60 seconds, so the shop is currently offline while the buyer continues.
Restoring the shop tunnel and keeping both agents online through the deadline
remain required. In Round 0 the kit picks a random shop, so the live
`market_buyer` review and memory policy can only be observed from Round 1.
