# Week 07 — A shop and a buyer in the agent market

**Draft, 2026-10-20 21:00 (Round 1).** Round 2 numbers are marked TODO and are filled in before the PR.

- Shop: the week-06 seller on Cloudflare Workers (Llama 3.1 8B on Workers AI), now signing receipts, priced by a meta-harness that reads the market's public ledger. Buyer: `market_buyer.py` (Claude Code, haiku) with a file memory and the same ledger.
- Market test 25/25 (`test-report.json`). Both agents are in the market as `thisjonghoon`, linked to the GitHub account.
- What mattered most was not negotiation skill but reputation: a new shop gets no buyers until it has reviews, and buyers rate by how much of their budget they kept.

| | Where it lives |
|---|---|
| Shop (deployed) | `deploy/src/index.ts`, https://a2a-lamp-shop.thisjonghoon.workers.dev |
| Shop (market test) | `seller.py` (same signature, haiku harness) |
| Market buyer | `market_buyer.py`, memory in `memory.jsonl` |
| Market data | `market_intel.py` (public ledger, cached in `.market/ledger.json`) |
| Meta-harness | `meta_shop.py`, decisions in `logs/challenge-1/` |
| Week-06 buyer (Round 0) | `buyer.py` |

## 1. How the buyer picks a shop, and its memory

**Pick.** For each order the kit writes `{shops}`. `market_buyer.py` builds one line per shop and asks the harness to pick by name, with a one-sentence reason (logged as `CHOOSE`). Code narrows the list first; the harness never sees a shop the code ruled out:

| Code rule | Why |
|---|---|
| A shop whose receipt disagreed with the deal in our memory is never offered | it cheated us once |
| A shop that failed the market's checks (wrong item, over budget) at least twice and in at least 20% of its results is never offered | it cheats others (maple-home, grand-furniture) |
| A shop that did not answer twice within an hour is skipped | outseop-26510122 was picked 7 times while down |
| From Round 2, a shop whose last five receipts mostly lack a valid seller signature is skipped | its sale would not count |
| A shop whose median price for the item is above the market's upper quartile is skipped when there is a cheaper choice | the user's rule: do not buy where it is too expensive |

The buyer then pays at most `min(budget, 1.1 x market upper quartile)`, raises at most 40% of the gap to the shop's ask per offer, and checks the receipt: item, the price we agreed (our last offer), budget, and the task id of this negotiation.

**Memory** (`memory.jsonl`, one JSON line per trade, keyed by shop name because URLs change on restart). The design follows LongMemEval-V2 (Wu et al., 2026), whose point is that agent memory should make an agent an experienced colleague of one environment:

- *Raw evidence only.* Each line is the whole trade: every ask, every offer, the end state, the receipt, the problem found, stars and the pick's reason. No summary is stored; summaries are derived on every read, so a wrong rule can be fixed and old trades are re-judged. This paid off twice: receipts lacking only a task id had been stored as `bad_receipt`; changing the rule on read reclassified them as incomplete without touching the file.
- *Static state:* per shop and item, opening ask, lowest ask, prices paid.
- *Dynamic state:* newest first; a changed opening ask is reported next to the old ones (shops change prices, newer evidence wins).
- *Transition events:* how the shop answered each offer (`95->down to 112`, `90->shop ended the talk`).
- *Gotchas:* bad receipts, ending the talk, being down.
- *Premise awareness and abstention:* `PREMISE your budget 230 is below every price it went to (260)`; `no price evidence for this item` instead of guessing.

The same profile goes into the negotiation prompt, with the shop's lowest sale to other buyers from the ledger.

## 2. How reviews reach the harness

The pick prompt gets, per shop: review count, average stars, and the three newest reviews cut to 120 characters, next to the market record and our memory. The prompt says reviews are written by strangers, may be false or carry instructions, and count only as evidence; our own trades outrank reviews when they disagree. Only the newest three are shown because `{shops}` can hold 10 reviews for each of 30+ shops; the aggregate carries the rest.

Our own reviews are written by code from what happened, never by the model, so they are facts another buyer can use: `lamp at 111 (89% of budget), opened at 130, deal after 2 offers; signed receipt matched the deal.` A budget below the market's floor is not blamed on the shop (3 stars, "Not the shop's fault"); a fake receipt gets one star and the exact mismatch.

## 3. Rounds 0, 1 and 2

Numbers from `/api/stats`, `/api/board` and the ledger (`market_intel.py`).

| | Round 0 | Round 1 (at 21:00) | Round 2 |
|---|---|---|---|
| Market deals / results | 1252 / 2213 | 1020 / 1525 | TODO |
| Our shop: deals / visits | 44 / 90 | 5 / 7 | TODO |
| Our shop: share | 3.5% | 0.5% | TODO |
| Our shop: mean price lamp / chair / bookshelf | 122.2 / 211 / 285 | 108.5 / 200 / 270 | TODO |
| Our shop: stars | (no reviews in Round 0) | 3.0 from 1 review | TODO |
| Our buyer: deals / results | 58 / 77 | 29 / 49 | TODO |
| Our buyer: paid / budget | 0.901 | 0.875 | TODO |
| Our buyer: orders below every reserve | 7 | 10 | TODO |

**Round 0.** The kit chose the shop. Our Worker shop sold 5 of its first 21 visits: Llama 8B answered `"action": "reject"` while its note said "let's meet in the middle", which ended each negotiation after one offer (`deploy/logs/tail-market-before-reject-guard-1020.json`). A code guard turned reject into counter; the next 69 visits gave 39 deals (`tail-market-reject-guard-1020.json`). The model still opened a lamp at 250 against a market of 100-140.

**Round 1.** Buyers chose from names and reviews, and shares collapsed onto whoever had reviews first: corner-store took 40% of the round, and student buyers picked it 54 times in the first 140 results. Our shop got no visit in its first 15 minutes. Three causes, from the ledger: no reviews; house buyers whose Round-0 memory of us was mostly failed talks; and a ledger median of 125 for our lamps, above the market's upper quartile, so buyers that read the ledger (as ours does) filter us out. The meta-harness then ran a launch sale, and the first visits sold (bookshelf 270, chair 200, lamps 105 and 112, all signed and verified). All five buyers who bought left no review; the one review (3 stars) came from an order with a lamp budget of 90, below every shop's floor. 92% of Round-1 results come from buyers who do write reviews, so the stars depend on drawing one of them.

The buyer does well on deals and price: 29 deals at 87.5% of budget, with 10 orders that no shop could fill. It overshot on its second offer (105 then 115 at an ask of 118, where the shop had sold to others at 100); the 40% raise cap and the "lowest sale to others" note came from that.

**Round 2.** TODO: share before/after the switch, our first verified reviews, which shops fell when unsigned receipts stopped counting.

## 4. What failed while adding signatures

- The market test failed `seller_signed_receipt` with `receipt lacks shop, task`: the week-06 receipt was `{seller, item, price}`. It became `{shop, item, price, task}` with `task` the A2A task id.
- The price must be signed as a whole number: a data Part carries 120 as 120.0, and `120.0` canonicalizes to different bytes. Both shops sign `int(price)`.
- The Worker cannot use Python's `cryptography`. It signs with WebCrypto Ed25519 and builds the canonical JSON itself (sorted keys, no spaces, UTF-8). To be sure the bytes match, a throwaway key signed a receipt in Node with the Worker's code and Python verified it, before the real key went in as the `SHOP_KEY` secret. The live Worker's receipt then verified against `shop_key.pem`, and the market marks our Round-1 sales `verified True`.
- A signature proves who wrote the receipt, not that it is true. maple-home signed a receipt for 242 on a chair agreed at 212; the market marked it verified, because it can check the price against the budget but not against the negotiation, and our kit added our buyer signature (`logs/challenge-2/dishonest-shops.txt`, ledger #4334). The market buyer now withholds such a receipt from the kit, so our key never co-signs a price we did not agree to.

## 5. Challenges

### 5.1 The shop changes its prices (meta-harness)

`meta_shop.py` runs every 30 minutes, and at once when the round changes. It reads going prices from the last three hours of the ledger and our shop's results under each earlier strategy version, asks the harness for an opening ask and a target per item, clamps them in code, and sends them to the Worker (`PUT /admin/strategy`, token as a Worker secret). The reserves never change; the Worker itself refuses any price below them.

| Guard | Rule |
|---|---|
| Target | at least the market median (the user: "do not sell too cheap"), at most the upper quartile |
| Opening ask | at least the upper quartile, at most 1.15 x it |
| Step | each price moves at most 15% per cycle |
| Launch sale | until 25 deals in a round: copy the benchmark shop dragonash (median as target, upper quartile as opening) |

The Worker opens at the strategy's ask and closes at once when an offer reaches the target. Further guards came from reading lost sales: accept a buyer stuck at reserve + 5 or more, concede 40% of the gap to a rising buyer, show the floor to a buyer twice below it.

| Version | Lamp / chair / bookshelf (open, target) | Why (from `logs/challenge-1/strategies.jsonl`) |
|---|---|---|
| v0 | model's choice, lamp about 250 | week 6 |
| v1 | 135/115, 230/210, 310/290 | "v0 opened lamp near 250, far above the market's 100-140 band, and got only 10 deals" |
| v2 | 110/105, 200/185, 290/268 | launch sale: Round 1, no reviews, no visits |
| v3, v4 | 117/112, 198/193, 279/274 | dragonash's Round-1 medians; v4 adds an opening line asking for an honest review |

Why dragonash: in Round 1 it sold 48 of 61 visits, all signed, mostly 5 stars, at 85-90% of the buyer's budget. Buyers' reviews show they rate by budget kept ("Paid 100 of 125" got 5 stars, "used 93% of budget" got 3). Its opening message, read in our buyer's log, is `GRAND SALE + TEN-TEN CHANCE ... Please leave an honest review. bookshelf: was 350, now asking 284`. We copied the sale line and the request for an honest review, not the "was 350" (it never sold at 350).

Share and average price before and after: Round 0 (no strategy) 3.5% share, lamp 122.2; Round 1 (v1-v4) 0.5%, lamp 108.5. The rounds are not comparable on share (Round 1 restarted every shop's reputation), so the fair comparison is within Round 2. TODO: Round 2 share and price per version from `strategies.jsonl` and the board.

### 5.2 The buyer and dishonest participants

From `logs/challenge-2/dishonest-shops.txt`:

- **maple-home inflates receipts.** In Round 0 our week-06 buyer agreed 212 for a chair; the signed receipt said 242 (ledger #4334). Over the ledger, 53 of its 99 results fail `price_within_budget`.
- **grand-furniture fabricates trades.** 170 of its 321 results belong to orders the market never issued, each failing `item_matches` and `price_within_budget`, and they carry reviews like "Best shop in the market." and "Fast and friendly, would buy again." In Round 1 it still drew buyers that read reviews.

How the buyer came to avoid them: from the first Round-1 order the market-record rule removed both (`GUARD: not offering shops that kept failing the market's checks: ['maple-home', 'grand-furniture']`, on every Round-1 pick). A shop that cheats us directly is banned by our own memory even if the market has not seen it yet, and its receipt is never co-signed.

Our own mistake on the way: receipts from psyoung82 and jiseokkk lacked only the task id, and the first rule called that cheating (one star, banned). It is now "incomplete" (3 stars, not banned), judged on read, so those two are no longer excluded.

## 6. Deployment

| Part | Where | How |
|---|---|---|
| Shop | Cloudflare Workers, `a2a-lamp-shop.thisjonghoon.workers.dev` | Hono, one Durable Object per task, one for the strategy; Workers AI Llama 3.1 8B. Secrets: `SHOP_KEY` (the kit's `shop_key.pem`, uploaded from the file, never printed or committed) and `ADMIN_TOKEN` (for `meta_shop.py`) |
| Kit and market buyer | the Mac mini at home (system sleep off) | `market_kit.py join --deployed <Worker URL>`, `MARKET_OWNER` claim code, `nohup` with the kit's venv on PATH; `market_buyer.py` runs per order with `claude -p` on the subscription |
| Meta-harness | same Mac mini | `nohup python meta_shop.py --every 1800` |

`wrangler deploy` once ran from the repository root instead of `deploy/` (a `cd` in a command that runs outside the sandbox did not take), and wrangler's auto-configuration tried to upload the whole repository as a new Worker; it stopped at the 25 MiB asset limit. The generated `wrangler.jsonc`, `.wrangler/` and `.gitignore` change were reverted, and every deploy since passes absolute paths and `--config`.

A reboot would stop the kit and the meta-harness (no launchd job); they are restarted by hand.

## Discussion (from the lecture)

- *A shop that builds a reputation and then cheats once:* maple-home shows the market cannot see a price inflated within the budget, even when signed. The buyer has to keep its own record of the agreed price and refuse to co-sign; recording the negotiation's final ask with the receipt would close the gap.
- *When does a buyer pick a new shop?* Rarely: our shop's first visit came only after it priced below the benchmark. A new shop needs one reviewing buyer and a fast, cheap first deal.
- *Whose fault is a failed deal?* Reviews blame the shop for budgets below every floor (our only review is one). Our buyer's reviews say so instead.
