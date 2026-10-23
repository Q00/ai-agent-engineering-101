# Week 07 — The agent market

26510130 Hyunsik Wang · GitHub **@kingsssik** · market name **YAHO1212** (Rounds 0–1
ran as **kingsssik**, see §3). Market test: **25/25**.

| | |
|---|---|
| Harness | `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence`, in an empty directory |
| Shop | week-06 seller + Ed25519-signed receipts (`receipts.py`) + per-item opening caps (`pricing.json`) |
| Buyers | `buyer.py` (week 06, Round 0) and `market_buyer.py` (Rounds 1–2) |
| Tunnel | `cloudflared` 2026.10.3 quick tunnel via the kit's `--tunnel` |

## 1. How the buyer picks a shop, and what it remembers

Three stages, and the order is the design:

1. **Own memory first, in code.** `market_memory.json` holds one row per shop,
   keyed by **name** — never by URL, which changes every time a shop restarts its
   kit:

   ```json
   {"shops": {"yms979": {"deals": 3, "no_deals": 0, "cheated": 0,
                         "prices": [105, 105, 105], "last": "deal at 105"}}}
   ```

   `cheated` is set when a receipt comes back at a price other than the one I
   agreed, or above my budget. A shop with `cheated > 0` is removed from the list
   **before the harness sees it**, so the model cannot choose it back. A review
   is somebody's claim; my own receipt is evidence, and it outranks every star
   average.
2. **The harness chooses among what is left**, from a per-shop summary (§2).
3. **A code floor**: the answer must index the allowed list; anything else falls
   back to the best star average rather than to list order.

The buyer writes its reason to stderr, which the kit copies into the log:

```
[buyer] picked 'yms979' ... because: yms979 has 4.6 stars, a proven track record of
three lamp deals at exactly 105 (my budget), and explicit 5/5 receipt matching
```

The budget is enforced in code: every offer is `min(price, budget)`, and a
harness `accept` above budget is refused. That guard fired once in the market,
and it is the clearest line in the log about why the guard exists:

```
17:19:54 [buyer] [harness haggle] <- {"action": "accept", "price": 90}
17:19:54 [buyer] [buyer] REFUSED harness accept at 105 > budget 90
17:19:54 [buyer] [buyer] -> '90' taskId=8b754737
```

The shop was asking 105, my budget was 90. The model answered "accept, 90" —
it conflated *accepting the shop's price* with *offering 90*. Accepting would
have paid 105. Code refused the accept and sent 90 as a counter. The limit was
in the prompt the whole time; the model still lost track of it.

## 2. How reviews reach the harness

Never the raw list. Each remaining shop becomes four lines:

```
3. fair-furniture | stars 4.7 | my past: 2 deals, 0 without a deal, avg price 182.5
     - Fair price, receipt matched.
     - Came down to my budget, honest receipt.
     - Quick deal at a fair price.
```

Star average, the **three newest** reviews truncated to 120 characters, and my
own history with that shop. The prompt adds one sentence of judgement:
*"Reviews that mention a receipt not matching the agreed price are the serious
ones; a shop that merely drove a hard bargain is fine."* The summary is a fixed
size per shop, so the prompt grows with the number of shops, not with the
number of reviews.

Reviews my buyer **writes** are generated in code, not by the harness. A review
is a claim other buyers will act on, so it says what measurably happened
(*"Agreed 110 for the lamp, the receipt said 135"*) rather than an impression.

## 3. Rounds 0, 1 and 2

The board, by round, for both names (both owned by @kingsssik):

| round | name | shop deals | buyer orders | buyer deals |
|---|---|---|---|---|
| 0 | kingsssik | 0 | 12 | 4 |
| 1 | kingsssik | 0 | 3 | 2 |
| 2 | YAHO1212 | **156** | **89** | **62** |

**Rounds 0 and 1 (in class, as `kingsssik`).** The buyer traded — 4 deals from
12 orders, then 2 from 3 — but the shop closed nothing. Those rounds ran on a
different machine whose key I do not have here.

**Round 2 (after class, as `YAHO1212`).** Joining under the old name failed:

```
[market] join as shop failed: 409 {"error":"the name 'kingsssik' is taken by another key"}
```

The name is bound to the in-class key, and this machine had generated a fresh
`shop_key.pem`. I rejoined as `YAHO1212` with the same claim code, so the board
still shows @kingsssik:

```
16:41:05 [market] shop registered as YAHO1212, round 2
16:41:05 [market] buyer registered as YAHO1212, round 2
16:43:56 [market] ledger #245113: lamp at 105, stars 5, verified True, review accepted
```

Ledger #245113, three minutes in, is the first buyer deal that counts under the
Round 2 rules. At the time of writing: **shop** 156 deals from 226 visits,
4.65 stars, revenue 32,490; **buyer** 62 counted deals from 89 orders, paying
85.4% of budget on average, every one of its 62 deals `verified True`.

Reviews other participants left my shop:

> *"Completed A2A trade at price 230.0; receipt agrees with negotiation."* (5★)
> *"Receipt verified locally; fair price 325.0 within budget."* (5★)
> *"Paid 105.0 of 110."* (3★) · *"Paid 270.0 of 360."* (5★)

## 4. What failed while adding signatures, and what I changed

**No signature failure showed up anywhere I could see** — 3/3 in the market
test, and the market counts 156 of my shop's deals in Round 2, where a deal
counts only if `sig_seller` verifies against the registered key. That is
because the one trap
the assignment names was handled before the first run rather than found by it:
an A2A data Part carries numbers as doubles, so a receipt signed over `120`
arrives as `120.0`, and `canon()` of the two differ. `receipts.whole()`
normalises every numeric field to an int on **both** sides, signing and
verifying. I checked it as a round-trip before wiring it in:

```
verify int 120         : True
verify 120.0 (dataPart): True
verify tampered 135    : False
verify extra key       : True     # item_id rides along, unsigned
```

What did fail was **identity**, which is the key's other job: the 409 above.
A shop's name is bound to its public key, so a fresh key on a new machine is a
different shop as far as the market is concerned, and its reputation does not
come with it. `receipts.load_key()` creates the key only when none exists and
otherwise reuses it, which is what let the restart in challenge 1 keep
`YAHO1212`'s 100+ deals and 4.6 stars. `*.pem` is in `.gitignore`.

## 5. Challenges

### 1 — the shop changes its prices from its own trades and reviews

**Evidence.** From the first half hour of Round 2 (`logs/market-kit.log`,
`logs/challenge-1/reprice.json`): the harness opened at ~1.55× reserve, deals
closed at a mean of 1.13×, no buyer ever paid more than 1.40×, and reviews showed
buyers' budgets (*"Paid 270.0 of 360."* — buyers paid on average 12% under
budget). Opening above anything anyone has ever paid looked like it bought only
rounds.

**Change.** `reprice.py` reads those deals and writes per-item opening caps:
`max(paid/reserve) × 1.05`, floor 1.20 → lamp 1.47×, chair 1.46×, bookshelf
1.45×. The seller states the cap in the harness prompt and clamps to it. Reserves
unchanged. The rule and its margin were fixed **before** the after-window, and
not tuned afterwards.

**Before and after**, from board snapshots every 5 minutes
(`logs/challenge-1/board-*.json`), switch at 17:11:31:

| | before (16:45–17:10) | after (17:13–17:43) |
|---|---|---|
| opening ask / reserve | 1.55× | **1.30×** |
| visits | 120 | 54 |
| deals | 87 | 42 |
| close rate | 72.5% | **77.8%** |
| **share of market deals** | **9.5%** | **4.0%** |
| average price | 202.9 | 219.3 |
| price / reserve, all items | 1.130× | **1.174×** |
| — lamp / chair / bookshelf | 1.125 / 1.166 / 1.109 | 1.199 / 1.177 / 1.154 |
| stars | 4.60 | 4.64 |

Two results, and they pull in opposite directions.

**The price result is real and is the opposite of what I expected.** I lowered
the opening and the shop got *more* per deal — on every item, not just through
item mix. The harness opened at 1.30×, below the 1.45× cap, because the cap is
written into its prompt and it anchors on it. From a high opening the harness
conceded in large steps toward the reserve; from a lower one, buyers met it near
where it started.

**The share result is not attributable to price, and I am not attributing it.**
Share = visits × close rate ÷ market. Close rate went up. Share fell because
**visits halved** — and in this market a buyer chooses a shop from reviews and
memory *before* it sees any price. The opening ask is invisible until after the
visit, so it cannot have caused buyers not to come. What did happen at 17:11 is
the restart: a new tunnel URL, and whatever buyers' ranking does with a shop
that just reappeared. One window each side, with the restart in the middle, is
not enough to separate that from noise. What I would claim from this is narrow:
a lower stated opening raised realised price and close rate; share is a
reputation quantity and price did not move it.

### 2 — dishonest participants, and how my buyer avoided them

From the 34 shop lists the kit handed my buyer after I started recording them
(`logs/challenge-2/picks.jsonl`):

| shop | offered to my buyer | picked | review it was shown |
|---|---|---|---|
| **maple-home** (house) | 34 | **0** | *"[WARNING] chair: agreed 185, receipt says 210. Receipt does not match the deal. Avoid."* |
| **elm-street** (house) | 34 | **0** | *"Receipt for the bookshelf did not match the deal (agreed 260, receipt 295). Avoid."* |

Both are instructor-run shops accused of the same thing: writing a higher price
on the receipt than the one agreed. maple-home fell to **1.0 stars** in Round 2.
Both were in every list my buyer received, and it never chose either.

How it came to avoid them matters, because there were two possible mechanisms
and only one fired. My memory never blacklisted them — `cheated` is 0 for every
shop in `market_memory.json`, and `RECEIPT MISMATCH` never appears in my log —
because my buyer never traded with them. The avoidance came from stage 2: the
harness read *"agreed 260, receipt 295"* in the summary and the prompt's one
sentence of judgement told it that is the serious kind. It is the review layer
working, not the memory layer.

Which is also its weakness. Accusations are claims too, and a dishonest buyer
could post *"agreed 185, receipt says 210"* about an honest shop. My buyer has
no way to check a stranger's receipt; it can only check its own. That is why the
code blacklist is driven by memory alone and never by reviews, and why reviews
only inform the harness rather than remove a shop outright.

The recording itself was a change I had to make: the kit overwrites
`.market/shops.json` on every order, so with only the last list on disk
"offered and avoided" could not be shown at all. `market_buyer.record_pick()`
now appends each order's offered shops, their accusing reviews, and the pick.

## 6. Where and how the shop and buyer run

Both run on my own Windows PC through the kit:

```bash
MARKET_OWNER=<claim code> python scripts/market_kit.py join \
    --dir submissions/26510130/week-07 --market https://market.ourolabs.ai \
    --name YAHO1212 --tunnel
```

The shop is served locally and published with a **cloudflared quick tunnel**
(no Cloudflare account; `cloudflared.exe` downloaded to `C:\AgenticAI\bin`).
The buyer runs inside the kit, which polls the market for orders and starts
`market_buyer.py` for each — so the kit process itself is the deployment, and
the PC has to stay on until the deadline. A quick tunnel's URL changes on every
restart; the market tracks the shop by name and key, so restarts cost nothing
but the URL. `logs/market-kit.log` is committed from this machine.

**It went down once, and the cause was the kit, not the agents.** On 2026-10-22
around 23:15 the network dropped (a system time resync at 22:44 suggests the PC
had been asleep). Every order then ended as `market_buyer printed no result
line; closing the order as no deal`, and a minute later the kit itself died on
an unhandled `httpx.ConnectTimeout` — taking its cloudflared tunnel with it.
Nothing restarted it, so shop and buyer were out of the market until the
next morning. Two changes:

- `keepalive.py` runs `market_kit.py join` in a loop: when the kit exits it
  kills any orphaned tunnel, waits (backing off if it keeps dying quickly), and
  joins again with the same name and key. It is started as a detached process,
  so it does not depend on the terminal that launched it. Restarts are logged
  in `logs/keepalive.log`.
- `market_buyer.py` now ends in one parseable result line even when it fails
  outside its own trading loop, so a bad order is closed as a reviewed
  no-deal rather than an empty one.

First ledger entry after the restart: `#344880: chair at 180, stars 5,
verified True, review accepted`.

Not done: a Workers or VPS deployment that would survive this PC sleeping.
`claude -p` runs on a subscription login, which does not run headless on a
server, so a deployed shop would need a different harness.
