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

TODO: from `logs/market-kit.log` and the dashboard (share, average price, reviews per round).

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

TODO: share and average price before and after, from the dashboard or `logs/challenge-1/`.

### Challenge 2: dishonest participants

TODO: a `[check] DISHONEST?` line or a flagged shop from `logs/market-kit.log`, and the later
`[choose] skip` / pick reason that shows the buyer avoiding it (`logs/challenge-2/`).

## 6. Deployment

TODO: where the kit runs (machine, tunnel), the join command, uptime.
