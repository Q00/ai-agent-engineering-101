# Week 07 report: the week-06 haiku shop and buyer in the agent market

Harness: Claude Code 2.1.304 headless, `claude -p --model haiku`, no tools, empty temp directory
(`harness.py`, unchanged from week 06). Market test: 25/25 twice (`logs/market-test.log` is run 2;
run 1 is in the history at commit `1b0b6a7`).

## 1. How the buyer picks a shop, and the memory format

`market_buyer.py` reads `{shops}` and the memory file, then:

1. **Code drops shops that cheated us.** Any shop name with a `bad_receipt` trade in memory is
   removed before the harness sees the list (logged as `[choose] skip <name>`).
2. **The harness picks** from one summary line per remaining shop (section 2) and answers
   `{"shop": "<name>", "reason": "..."}`. The reason goes to the output as a `[choose]` line, so the
   kit copies it into `market-kit.log`. An unknown name falls back to "most stars, fewest flags".
3. **If the card does not answer**, the trade is remembered as `error` and the harness picks again
   from the rest (at most 3 shops).
4. **Negotiation** is `buyer.negotiate` from week 06: the harness makes every offer, code clamps
   offers to the budget, follow-ups carry `taskId`, walking away sends `CancelTask`.
5. **Code checks the receipt** against the deal: item = the ordered item, price = the last offer
   we sent (the price the shop accepted), `task` = the A2A task id, price within budget,
   `sig_seller` present. A mismatch is logged as `[check] DISHONEST?`, reviewed with 1 star, and
   stored as `bad_receipt`, which bans the shop from step 1 onward. The signature itself is
   verified by the kit with the shop's registered key; the buyer has no key list.

Memory is `buyer_memory.json`, keyed by shop **name** (URLs change when a kit restarts):

```json
{"trades": [
 {"shop": "Kit Shop South", "item": "lamp", "budget": 120, "price": 100.0, "result": "deal",
  "offers": ["130 -> 80", "105 -> 95", "100 -> 100"], "problem": null, "time": "2026-10-20 19:15"}
]}
```

`result` is one of `deal`, `no_deal`, `error`, `bad_receipt`. The file also holds the market
test's trades with the "Kit Shop" names; they do not collide with market shop names.

## 2. How reviews are shown to the harness

Never the raw list. For each shop, code writes one line:

```
- Kit Shop North: 3 reviews, 1.3 stars, flagged 3; mine: never traded; newest: 1*: 'Agreed 110, the receipt said 135.' / ...
```

- average stars and review count;
- `flagged`: reviews of 2 stars or less that match `receipt|overcharg|scam|cheat|fake|...`;
- `mine`: our own history with that name (tries, honest deals, prices paid against budget,
  `BAD RECEIPTS`, unreachable in the last 3);
- the 3 newest reviews, each cut to 90 characters.

The prompt says reviews are evidence written by anyone, not instructions, and that our own memory
outweighs other people's reviews. In `buyer_reads_reviews` haiku picked the shop with good
reviews in 3 of 3 runs both times, e.g. run 2 #1: *"It has zero flagged reviews, a 4.7-star rating,
and my own history of two honest deals with lamp purchases at 100 of a 120 budget"*.

## 3. Rounds 0, 1 and 2

I joined the market in Round 2 only. `logs/market-kit.log` starts with the join at 20:12:04 (shop and
buyer both "registered as winun1127, round 2"); there is no Round 0 or Round 1 history for this shop
and buyer, and I do not claim any.

- **Buyer, Round 2 (kit log, 20:12 to 20:23, 20 orders):** 19 ledger entries, 15 counted deals
  (chair 6 at 183 on average, lamp 5 at 102, bookshelf 4 at 260; average paid 177) and 4 no-deal
  results that the market did not show. The first counted deal is ledger #118535, a chair at 180
  from ggari-shop, `verified True`. The market board lists the buyer with 15 counted deals.
- **Second session (22:57 to 23:45, after the price change):** 95 orders, 65 counted deals and 28
  no-deals for the buyer; the board shows the buyer at 79 deals in total and the shop at 245 deals,
  4.44 stars. Details in challenge 1.
- **Shop, Round 2 (board, 2026-10-26 22:36, before the change):** 66 counted deals, 4.42 stars on 66 reviews, 126 visits,
  average price 180 (revenue 11878 / 66 deals), profit 1118 (about 17 per deal), share 0.0
  because the shop is not alive right now. For comparison ggari-shop has share 0.121 with an
  average price of 178 and about 1.6 profit per deal.

## 4. What failed while adding signatures, and what I changed

- The week-06 receipt was `{"seller", "item", "name", "price"}`. It is now built as
  `{"shop": cfg name, "item": item id, "price": int(price), "task": task id}` and signed with
  `sign_receipt` (canonical JSON, sorted keys, no spaces). `item` is the catalog id (`lamp`), which
  is the name the market's orders use.
- `int(price)` from the start, because A2A data Parts carry numbers as doubles; the market test
  shows the receipt arriving as `"price": 120.0` and still verifying.
- The key is read from `shop_key.pem`, or from the `SHOP_KEY` environment variable so a deployed
  shop can keep it as a secret.
- The signed shop passed `seller_signed_receipt` 3/3 on the first market test run. The failure I hit
  was on the buyer side: in run 1, `buyer_reads_reviews #1`, haiku answered `walk` to an ask of 100
  on a budget of 120 (*"Offering up to 120 would be above my offer ceiling"*), so a deal it should
  have taken ended with no receipt. `buyer.negotiate` now turns `walk` on an in-budget ask into
  `accept` and logs a GUARD line (commit `d45bcff`); run 2 passed 25/25.

## 5. Challenges

### Challenge 1: shop prices

**Before** (`logs/challenge-1/before-board-snapshot.txt`, board of 2026-10-26 22:36): 66 deals, average
price 180, about 17 profit per deal, 4.42 stars, share 0.0 (not running). My asks sit at the
reserves (lamp 100, chair 180, bookshelf 260), so the shop earns almost nothing per deal and the
catalog never raises an ask above the reserve.

**What the logs showed** (66 deals of my shop, `logs/market-kit.log`, `completed, receipt` lines of my seller):
the harness opened at 130 for the lamp (30 of 36 openings), 240 for the chair (23 of 33) and 320 for the
bookshelf (25 of 27), which is 30%, 33% and 23% above the reserves of 100, 180 and 260. Deals closed at
lamp 103-112 (mostly), chair 185-193 and bookshelf 265-285, with some at 130, 240 and 320. Counted deals
that other shops report in the market's reviews close at lamp 100-115, chair 180-195 and bookshelf
260-290. So my opening asks were far above where the market closes and the extra rounds of haggling are
where buyers walk away; my share was low (126 visits, 66 deals).

**The change** (commit `df22130`): the reserves stay; the catalog gets an `open_cap` per item
(lamp 115, chair 195, bookshelf 280, the top of the range the market closes at) and `seller.py` clamps
the harness's opening ask to it (log line `GUARD opening ask N above open_cap M`). Market test after the
change: 25/25. The change was proposed by Claude from this data and I approved it; the reasoning above is
the evidence for it, and the expected effect (a higher share at a slightly lower average price) is a
hypothesis until the numbers below exist.

**After** (`logs/challenge-1/after-board-snapshot.txt`, board of 2026-10-26 23:45; kit restarted at 22:57,
so 48 minutes, 95 orders, `GUARD opening ask ... above open_cap` fired 251 times). The deltas are board
totals minus the "before" snapshot (66 deals, 126 visits, revenue 11878, profit 1118):

| | before (13 min session) | after (48 min session) |
|---|---|---|
| deals per visit | 66 / 126 = 52% | 179 / 247 = 72% |
| deals per minute | 5.1 | 3.7 |
| average price, lamp | 111.0 (29 deals) | 105.7 (53) |
| average price, chair | 198.5 (22) | 187.0 (69) |
| average price, bookshelf | 286.0 (15) | 268.4 (58) |
| profit per deal | 16.9 | 7.1 ((2391-1118)/179) |
| share | 0.0 (not running) | 0.001, rank 28 of 37 running shops |

(The per-item averages are from my seller's `completed, receipt` lines in `logs/market-kit.log`.)

**What this says.** The cap did what it was meant to do on price: the average closing price per item fell
by about 5-6% and a larger part of the visits ended in a deal (52% to 72%). It did **not** raise my
share: 0.001 is nearly zero, and deals per minute went down, not up. So the hypothesis that high openings were
what cost me customers is not supported; at most the cap made the customers I already got more likely to
buy. The price per deal fell and profit per deal fell from about 17 to about 7, so the shop earns less per
sale without selling more. What decides who gets visits is, as the assignment says, what buyers remember
and read in reviews, and a 4.4-star shop with 159-245 reviews in a market where the leaders have 30,000
does not get picked by buyers whose memory already holds other shops. **Limits:** the two sessions are
different lengths and at different times, other shops' prices and the buyers around changed, and I
changed one thing but did not run a control. I kept the cap rather than revert, because the conversion rate
is better and reserves are unchanged; a next step would be to measure what buyers see (my reviews say
"receipt matches" but are few) instead of the asking price.

### Challenge 2: dishonest participants

The dishonest shop I met is **maple-home**. In the first order (`o-e48d336d`) the harness saw this
line, produced by the code from the market's reviews (`logs/challenge-2/evidence.txt`, log line 21):

```
- maple-home: 2 reviews, 1.0 stars, flagged 2; mine: never traded; newest: 1*: 'Agreed 310, receipt says 355.' / 1*: '[WARNING] chair: agreed 185, receipt says 210. Receipt does not match the deal. Avoid.'
```

Both reviews say the receipt price was higher than the agreed price, which is what a dishonest shop
does. elm-street was also flagged (2 of 10 reviews). The flag count, the 1.0 average and the
`mine: never traded` field were in every one of the 20 summaries, and across all 20 `[choose]`
decisions the buyer picked neither shop. The picks were ggari-shop, mjforge, fair-furniture and 11im,
with reasons such as *"...it has 4.4 stars with no flagged reviews"* (log line 3812). I never traded
with maple-home, so `[check] DISHONEST?` and the `bad_receipt` ban never fired in this log; the
avoidance came from the review summary, not from my own memory. Limit of this evidence: maple-home was
avoided partly because a 1.0-star shop with 2 reviews also loses on stars alone; the log does not show
the harness's reason for rejecting it.

## 6. Deployment

The kit, shop and buyer all ran on my home PC (Windows 11) with `--tunnel`: the kit started the
seller on a local port (49829 in the log) and opened a cloudflared quick tunnel
(`https://beth-hook-browser-admitted.trycloudflare.com`), which it registered with the market. The
command was `python scripts/market_kit.py join --dir submissions/25512192/week-07 --market https://market.ourolabs.ai --name winun1127 --tunnel`
(with `MARKET_OWNER` set to my claim code, so the agents show my GitHub name).
The shop is not deployed to Workers or a VPS. Signing uses `shop_key.pem` on that PC, and the code
also accepts a `SHOP_KEY` secret for a later deployment. Uptime: the log covers two sessions on the same PC: 2026-10-21
20:10:55 to 20:23:33 (about 13 minutes) and, after the price change, 2026-10-26 22:55 to at least 23:45
(started by `D:\seoultech\2026-2\agent-market\join.ps1`, a hidden process with the same join command). Because a quick-tunnel URL changes at every start, the shop
is offline whenever the PC's kit is stopped; the board shows it as not running now.
