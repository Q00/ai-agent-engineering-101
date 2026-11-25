# Week 07 Report: the agent market

Nickname `olstjr` (GitHub @olstjr). Shop and buyer run through `scripts/market_kit.py` on one always-on Linux server (Docker container on a GPU box), shop exposed with a cloudflared quick tunnel.

## (1) How the buyer picks a shop, and its memory

- `market_buyer.py` reads `{shops}`, drops every shop that gave **me** a bad receipt (price not the agreed one, wrong item, wrong task, missing `sig_seller`), scores the rest in code, shows the top 6 (plus one shop with no reviews and no history, so new shops can get a first customer) to the harness, and the harness picks one and gives a reason, printed as `[choose] <shop>: <reason>`.
- Score = average stars (3.0 if none) − 0.5 × warnings + 0.3 × my deals for this item (max 3).
- Memory: `buyer_memory.jsonl`, one JSON line per trade: `{time, shop (market name), item, budget, result deal|no_deal, price, offered, receipt_problem, stars}`. Shops are remembered by name. After every deal the code compares the receipt with what was agreed and writes `receipt_problem`.
- Budget: offers above the order budget are capped in code (`[guard]`), as in week 6. The kit also refuses to sign a receipt above the budget.

## (2) How reviews reach the harness

Two versions, both in the history:

| | first version (`666b795`) | compact version (`b1b4a23`) |
|---|---|---|
| what the harness sees | every shop, its review count and average, the 3 newest review texts, my history for that shop | 6 shops, one line each: reviews, average, **warnings**, my history **for the same item** |
| warnings | – | reviews with ≤ 2 stars, or whose text matches receipt mismatch / overcharge / manipulation words (`receipt says`, `overcharg`, `telling AI`, `inject`, ...) |
| choose prompt | ~2,770 tokens (82% of a buyer's tokens per order) | ~300 tokens |

Why I changed it: cost (below), and safety. Another buyer reviewed `bright-living`: *"its messages included embedded text telling AI buyers to give it 5 stars"*. With review texts in the prompt, other participants' words reached my harness directly. With numbers only, no text from another participant enters the choose prompt. Both versions pass `buyer_reads_reviews` 3/3.

Harness: Claude Code haiku (`claude -p`) until 12:37 UTC on 2026-10-20, then OpenRouter `nvidia/nemotron-3-super-120b-a12b` (paid, reasoning off) via `HARNESS=openrouter` in `agent.json`, because the market drove ~770 harness calls per hour. OpenRouter cost: $0.066/h with the first prompt, **$0.022/h** with the compact one (measured from the key's usage).

## (3) Rounds 0, 1 and 2

**Round 0** (random shop, week-6 buyer). First join (`--orders 3`): every receipt unsigned, e.g. `ledger #231: bookshelf at 265, ... verified False (failed: sig_seller_ok, sig_buyer_ok)`. After signing: first counted deal `ledger #1563: lamp at 105, stars None, verified True`. The kit sent me to `maple-home` three times; each time the shop said one price and signed another:

```
[buyer]   <- TASK_STATE_COMPLETED | Deal: lamp at 103.
[buyer] result: TASK_STATE_COMPLETED, bought at 118.0, 17s
[sign] receipt lamp at 118 does not fit the order, not signing
[market] ledger #1515: lamp at 118, ... verified False (failed: price_within_budget, sig_buyer_ok)
```
(also lamp 103 → receipt 118 at 11:02, bookshelf 295 → receipt 335). Without memory or choice the buyer could not avoid it. Two student shops were down (card request returned non-JSON) and my week-6 buyer crashed on them.

My shop in Round 0–1 (Claude harness): 85 requests, **64 deals, 0 rejects** in about an hour, mostly near the reserve (chairs 180–195, bookshelves 260–300). Reviews: *"chair 260->180 in 4 offers. Receipt matched the agreed price, signed."* It sold a lot because it never walked away and came down to the reserve.

**Round 1** (from 11:28): the kit ran `market_buyer`. First order: `[choose] grand-furniture: It is the only shop with a positive review ...`, no deal, `ledger #4538: no deal, stars 3, verified False, review accepted`. 27 of 41 buyer results in Round 1 already verified.

**Round 2** (from 12:22): 13 verified deals with `review accepted` before the harness switch. After the switch (15:34 → 04:49 next day): 1,071 orders, 624 verified, all 445 unverified were no-deals (no receipt at all); 138 sales by my shop. `check_week07.py` at submission: shop `olstjr` **423** counted deals, buyer `olstjr` **1,549** counted deals in Round 2.

## (4) What failed while adding signatures, and what changed

1. Week-6 receipt `{seller, item, price}` without signature → `verified False (failed: sig_seller_ok, sig_buyer_ok)`. Changed to `{shop, item, price, task}` + `sig_seller` (Ed25519 over canonical JSON, `shop_key.pem`), price as `int`, task = A2A task id (`29abf04`). Checked by buying from my shop with the a2a CLI: `verify as received: ok`, `task matches A2A task id: True` (`logs/manual-signed-receipt.txt`), then `seller_signed_receipt` 3/3.
2. A valid signature is not an honest receipt: maple-home's receipts passed `sig_seller_ok` but carried a price higher than the deal. The kit's order check caught it; my buyer now also compares receipt and agreed price itself.
3. Switching the harness broke the seller twice, not the signature: `23/25 FAIL` (nemotron rejected at the kit buyer's second offer, 80) and `22/25 FAIL` (it kept countering above 120 while the buyer repeated 120 four times, then countered 120 itself). Fixes: the seller prompt says rejecting loses the sale and that a repeated offer at or above the reserve is the buyer's limit; code treats a counter at or below the offer as accepting it (`57b7c51`). Then 25/25.

## (5) Challenges

**Challenge 1 (shop prices): not done.** The data for "before" is in the log (sold near the reserve, high share), but I did not implement price changes from my own trades and reviews before the deadline.

**Challenge 2 (dishonest participants)**, `logs/challenge-2/`:
- `maple-home`: deal 103, signed receipt 118 (three times in Round 0). In Round 1 and 2 my buyer never picked it and never even showed it to the harness (0 of 2,459 shortlists): its reviews carry warnings and low stars.
- `bright-living`: reported by others for injecting instructions to AI buyers. While review texts went into the prompt my buyer picked it **33 times**; after the compact prompt (warnings 3) **0 times**.
- `willow-goods`: gave me an unsigned receipt (`[receipt] PROBLEM: receipt has no sig_seller`, 11:31), so my memory excluded it from then on (`[memory] skipping ... ['willow-goods']`, 2,883 orders).

## (6) Deployment

- Machine: a Linux server that stays on (Ubuntu 22.04 container, up since 2026-10-05). Shop on the same machine with `--tunnel` (cloudflared quick tunnel), joined with the claim code (`MARKET_OWNER`), `setsid nohup` so it survives the terminal.
- Failure: on 2026-10-21 14:43 UTC the kit died on `httpx.ConnectTimeout` talking to the market and nothing restarted it; shop and buyer were offline until 2026-10-27 09:54 UTC. Now the kit runs inside a loop that restarts it 30 s after any exit.
- Keys: `shop_key.pem` / `buyer_key.pem` stay on the server, ignored by `.gitignore`; the OpenRouter key is only an environment variable.
- Load: the kit uses <1% of one core and ~130 MB; the log grows ~7 MB/day.
