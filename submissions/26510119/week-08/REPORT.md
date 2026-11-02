# Week 08 Report: Round 3, a market with payments

Nickname `olstjr` (GitHub @olstjr). Same shop and buyer as week 07 (same `shop_key.pem` / `buyer_key.pem`, kept out of git), now under kit v3 (`market_kit 3`), which adds the ACP checkout (`/.well-known/acp.json`, `/checkout_sessions`) and the AP2 mandates. Harness: OpenRouter `nvidia/nemotron-3-super-120b-a12b` (reasoning off) via `harness.py`.

Setup check on 2026-10-27 (class, still Round 2), `logs/2026-10-27-class-kit-v3-round2.log`:

```
10:30:36 [kit] market_kit 3, .../week-07
10:31:54 [market] shop registered as olstjr, round 2
```

and `GET <shop>/.well-known/acp.json` returned `"services":["checkout"], "payment_methods":["ap2-mandate"]` with the five checkout tools.

## (1) What happened to my shop and buyer in Round 3

_To fill after Round 3 opens (Mon 2026-11-02): settled deals for the shop (`[acp] settled`) and the buyer (`[acp] paid ... verified True`), refusals and their failing checks (`[acp] settle refused: [...]`, `not paying`), quoted from `logs/market-kit.log`._

The buyer side needed no change: kit v3 refuses to pay when the checkout total is above the open payment mandate's `amount_range` max (`[acp] checkout total ... is above the mandate's limit; not paying`) or when the checkout does not match the signed receipt, and `market_buyer.py` already caps every offer at the order budget in code.

## (2) Pricing after the budget left the ledger

**Before (Round 2).** The shop had no memory across negotiations: every opening ask was the model's guess from the item and the reserve alone. In the hour after the kit v3 restart (still Round 2) the opening asks were flat, about 1.3–1.5 × the reserve every time, and sales sat 10–15% above the reserve:

| item | reserve | avg opening ask | sales | avg sale price | sold at the reserve |
|---|---|---|---|---|---|
| lamp | 100 | 150 | 7 | 116 | 3 |
| chair | 180 | 245 | 8 | 197 | 3 |
| bookshelf | 260 | 338 | 8 | 298 | 1 |

**Change.** The shop now keeps its own sales history in `sales_log.jsonl` (`shop_memory.py`): one `open` line per negotiation (item, opening ask) and one `end` line (deal or reject, price, the buyer's offers). Before each opening ask the seller computes, per item over the last 60 negotiations, the deal rate, average opening ask, average/min/max sale price, how many sold exactly at the reserve, and the buyers' average first offer; it logs this as a `[pricing]` line and gives it to the harness, which picks the opening ask from it. Each counter also sees the item's recent average sale price. The reserve is still enforced in code.

The history was seeded once from the shop lines of the Round 0–2 logs (`seed_sales.py`): 790 negotiations, 404 deals. What the harness saw at the start of Round 3:

| item | deal rate (last 60) | avg opening ask | avg sale price | buyers' avg first offer |
|---|---|---|---|---|
| lamp | 42% | 150 | 122 | 101 |
| chair | 65% | 248 | 206 | 180 |
| bookshelf | 63% | 336 | 289 | 253 |

Hidden budgets matter less to this shop than to q00, which read them from the ledger; this shop never read the ledger. What changes in Round 3 is that every buyer's budget is private, so the only signal about willingness to pay is the shop's own history (deal rate, buyers' first offers), which is what the new memory provides.

**After (Round 3).** _To fill: opening asks and average sale prices per item from the `[pricing]` and `accept` lines and the dashboard, compared with the table above._

## (3) A week-07 incident that Round 3 does not stop: collusion

In week 7 the buyer of `fair-furniture` made 86% of its purchases (12,192) at `ggari-shop`, and `ggari-shop`'s owner's buyer made 91% of its purchases (4,978) at `fair-furniture`; the card even claimed "Alliance, approved by the instructor". Round 3 does not stop this, because every check it adds is about whether a payment is authorized and matches the checkout: the open mandate proves the buyer may spend up to its budget, the shop-signed `checkout_jwt` and the `checkout_hash` prove the price was not changed, `amount_in_range` and one settlement per order prevent overspending and replay. A colluding pair passes all of them, since both signatures are real, the price is inside the budget and the order is used once. The checks look at one transaction; collusion is a pattern across many transactions between the same parties (who buys from whom, at what prices, and who reviews whom). It belongs to the record and reputation layer: the ledger already has the data (buyer, shop, price per settlement), so it would need a market-level analysis of the trade graph, for example flagging pairs where most of one party's volume goes to one counterparty and discounting their reviews, which neither ACP nor AP2 defines.
