# Challenge 2 design: one buyer, about 20 shops, a shopping list, one budget (v1, 2026-10-18)

Status: draft; open decisions at the end.

## Benchmarks chosen (2026-10-18)

| # | benchmark | what it gives the buyer |
|---|---|---|
| 2 | Procurement RFQ = Contract Net (week 3) | ask several shops at once, award one, cancel the rest |
| 3 | Card pre-authorisation / hotel hold | money in an open negotiation is held in a ledger, so parallel deals cannot overspend |
| 5 | Sales CRM log | each negotiation is carried as one summary row, not as its transcript |

Price comparison (1), split carts (4) and travel meta-search timeouts (6) fold into these.

## How the three pass conditions are met

| pass condition | met by | enforced in |
|---|---|---|
| every item on the list is bought | quotes from 2-3 shops per item; if a negotiation fails, the next quote is tried | code (fallback order) |
| total within budget | ledger: spent + held <= budget before any accept | code guard |
| no item bought twice | one award per item (Contract Net); an item marked bought closes and cancels every other task for it | code guard |

The harness never sees the ledger rules as something it may break: it proposes, code checks.

## Flow

```
directory.json (about 20 URLs) + shopping list + budget
  part A  read every Agent Card (GET /.well-known/agent-card.json)
  part B  harness reads the card summaries and the list -> 2-3 candidate shops per item   (DF, yellow pages)
  part C  call for proposals: send each item's description to its candidate shops, in parallel
          -> quote table: item, shop, offered item_id, opening ask
  part D  award: one shop per item; CancelTask on the other quotes (reject-proposal)
          budget: each item gets a cap from the ledger
  part E  negotiate with every winner in parallel; each harness call sees one summary row
          a failed negotiation -> next quote for that item (back to D for that item)
  part F  done when every item is bought or no quotes are left; print the ledger
```

## Part A: the directory (built for the test)

The README: "run several copies of your seller with different catalogs and prices".
`catalog_seller.py` gets three options so one program can be many shops:

- `--categories lamp,desk` (only these categories from catalog.json; default all)
- `--markup 1.2` (reserve and asking prices x this; a cheap shop 0.9, a dear one 1.3)
- `--name "Lamp Corner"` (card name; the card description lists the categories it sells)

About 20 shops of three kinds, like a real shopping street:

| kind | example | categories | markup |
|---|---|---|---|
| specialist | Lamp Corner, Sofa House | 1-2 | 0.9-1.1 |
| general store | Big Furniture Hall | all 10 | 1.2-1.4 |
| discount | Outlet Depot | 3-4 | 0.8, but opens close to its floor and barely moves |

`directory.json` lists the URLs only; the buyer learns everything else from the cards.

## Part B: choosing shops from the cards (DF)

One harness call per run: the shopping list plus one line per card (name, skill description,
tags). Answer: `{"item 1": ["shop url", ...], ...}`, 2-3 shops per item.
Code guards: unknown URLs dropped; an item left with no shop gets every shop whose card names
its category; at most 3 per item.

## Part C: call for proposals (parallel)

The buyer sends each item's description (as in challenge 1, e.g. "a lamp to read in bed") to its
candidate shops at the same time. Each shop answers with one offered item and an opening ask,
or a question (answered from the list's notes), or a rejection. Result: the quote table.
Parallel calls run in a thread pool with a limit (each shop runs about three harness calls
for a first message, so 15 quotes at once would be about 45 harness processes).

## Part D: award and the ledger

Ledger (budget B):

```
spent   sum of receipts
held    for each open negotiation, its cap (the most the buyer may still pay for it)
free    B - spent - held
```

- Award: per item, the quote with the lowest opening ask (code). Losing quotes: CancelTask.
- Caps: the budget is split over the awarded items in proportion to their opening asks, so a
  sofa gets more than a lamp; caps never add up to more than B.
- After a deal, the item's unused cap goes back to free and is spread over the open items.
- Guard: accept only if price <= cap and spent + price + other holds <= B; never a second
  accept for an item already bought.

## Part E: the summary row (the core of the challenge)

Each negotiation is one row; the harness sees only its own row, never other shops' talks:

```
item: reading lamp | shop: Lamp Corner | offered: LP-0360 Clear Glass Clip Lamp
asks: 50 -> 42 | my offers: 30 | round 2 of 6 | cap 45 | status: negotiating
```

The full transcript stays in the log, not in the prompt. A prompt stays the same size whether
the buyer talks to 2 shops or 20; the log records the prompt size per call so the report can show
it (part 5 of REPORT).

## Part F: test and logs

`challenge2_test.py`: starts the 20 shops from a profile file, runs the buyer with a list and a
budget, then checks the three pass conditions from the receipts (not from the buyer's own
claims): every list item has exactly one receipt, the receipts' total <= budget.
Writes `logs/challenge-2/` (buyer log, one log per shop, report json).

## Open decisions

1. **Shopping list wording.** Descriptions the catalog sellers understand ("a lamp to read in bed")
   or plain names ("lamp"). Descriptions reuse challenge 1 and are closer to real shopping.
2. **Who chooses shops (part B).** The harness reads the cards (the README's "reads the skills on
   the cards"), with the code guard above; or code matches categories by keyword.
3. **Who awards (part D).** Code picks the lowest opening ask; or the harness compares the quotes
   (item fit and price). Code is simpler to verify; the harness can weigh fit.
4. **Parallel limit.** How many shop conversations run at once (3, 5, or all).
5. **List and budget for the test.** For example 4 items, budget about 1.2 x the sum of cheapest
   reserves, so the budget is tight but every item can be bought.
