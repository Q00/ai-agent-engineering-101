---
name: round3-sales-strategy
version: 2
description: Round 3 shop and buyer strategy for psyoung82. The numbers under policy are read by the rule engine on every call; the text below is read by the planner model.
policy:
  seller:
    first_ask_list: 0.85          # D3 first ask = max(list x this, floor)
    first_ask_big_list: 0.78      # D3 first ask for big-ticket goods (list above stock.big_ticket_list): the market clears them at 0.67-0.72
    floor_cost_mult: 1.08         # D3 floor = max(unit cost x this, list x floor_list)
    floor_list: 0.60
    food_clear_uwh_factor: 2.0    # D4 food below cost when hours left < factor x observed use_within median and grade C (shelf left < 45%)
    food_clear_min_obs: 10        # D4 below this many observations use food_clear_left_share instead
    food_clear_left_share: 0.25
    goods_stale_days: 5           # D4 unsold goods: floor = cost; restocked if focus/trend or the market sold it in 48 h
    warranty_on_request: true     # D5 warranty only when the buyer asks
    warranty_hours: 6
    warranty_grades: [A, B]
    warranty_premium: 0.15
    warranty_cap_6h: 500000       # D5 sum of warranted sales in the last 6 h
    fact_block: true              # L5 first reply carries the fact line and the market certificate
    claim_food_margin_h: 1.0      # L5 claim a food grade only with this much margin to the next grade cut
    promise_ttl_s: 180            # a unit promised in an open negotiation is held this long
    intro_price_enabled: false    # L4 (conditional, day-1 data)
  stock:
    inventory_cap_share: 0.30     # D1 inventory cost <= share x (cash + inventory cost)
    cash_floor: 1000000           # D1 bankruptcy line 500,000 + refund reserve 500,000
    worst_cost_share: 0.75        # D1 affordability uses the worst supply cost
    food_shelf_hours: 720         # D2 food categories opened from the start
    per_sku_qty: 2
    short_food_max_shelf_h: 168   # D2 shorter shelf categories wait for observations
    short_food_min_uwh_obs: 10
    goods_max_list: 600000        # D2 skip goods categories whose median list price is above this
    supply_demand_priority: true  # L1 priority (demand + 1) / (supply + 1)
    demand_window_h: 6
    regular_min_stock: 1          # L1 keep at least this many units in a regular customer's top categories
    max_lines: 10
    max_qty: 20                   # units per supply line (the kit's per-call limit)
    max_hold: 40                  # units one SKU may hold (the cover target's cap)
    min_line_qty: 4               # skip a top-up of an in-stock SKU below min(this, target / 2): lines are scarce
    require_own_card: true        # buy nothing while our own card does not answer
    bankrupt_min_hours_to_end: 48 # D10
    bankrupt_recovery_threshold: 500000
    bankrupt_zero_sales_h: 12
    end_ts: 1794927540            # D14 2026-11-17 23:59 KST
    taper_h: 0                    # D14 buy only what sells before the end from T-this h (0 = off: stock counts at cost)
    taper_factor: 0.5
    goods_stop_h: 6               # D14 no new goods from T-this h (0 = off)
    clear_cost_h: 48              # D14 floor = cost from T-this h (0 = off)
    clear_70_h: 0                 # D14 floor = cost x 0.7 from T-this h (0 = off: lowers Book)
    clear_any_h: 0                # D14 floor = 1 from T-this h (0 = off: lowers Book)
    no_warranty_h: 8
    window_lead_s: 60             # supply opens every ~20 min for 3 min: plan only when open or opening this soon
    cover_min: 25                 # stock per SKU covers expected sales for this many minutes (until the next window)
    cover_safety: 1.5             # expected sales x this
    focus_skus: []                # trending SKUs from the ledger (set by the strategist): stocked deeper
    focus_max: 8
    focus_cover_mult: 2.0         # a focus SKU's cover target x this
    focus_cap_share: 0.5          # focus buys use at most this share of the D1 headroom
    focus_min_settles: 10         # market settles in 6 h a SKU needs before it can be a focus SKU
    trend_skus: []                # the next best sellers (set by the strategist): picked like focus, ordered after it
    trend_max: 16
    trend_share: 0.08             # focus/trend SKUs cover at least this share of the market's sales rate
    big_ticket_share: 0.40        # goods above big_ticket_list: held + planned cost <= this x inventory_cap_share x funds
    big_ticket_list: 40000        # goods listed above this are big-ticket
    bigticket_probe_qty: 3        # a big-ticket SKU holds at most this many units until we sold 2 of it in 6 h
    min_margin_share: 0.05        # surge: buy while clearing price - this covers cost x factor x floor_cost_mult
  market:
    surge_free: 50                # Patch 3.2: units of a SKU the whole market buys per rolling hour at the plain price
    surge_per_unit: 0.002         # each further unit adds this to the cost factor
    surge_max: 1.5                # the factor's cap
  buyer:
    cap_goods_no_warranty: 0.70   # D6 price caps as a share of the shipped SKU's list price
    cap_goods_warranty: 0.94
    cap_food: 0.95
    min_surplus_list: 0.05
    min_kept: 0.0                 # D6 every cap <= (1 - this) x list: Saved is an average share kept, a deal below it lowers the score
    open_list: 0.60
    max_shops: 3
    food_margin_h: 0.2            # D7 min_hours_left = use_within_hours + this (settle follows payment)
    target_s: 20                  # D8
    continue_p: 0.75
    msg_timeout_s: 12
    order_cap_s: 60
    binding: true                 # D12 pre-payment binding check in the kit copy
    accept_cert: true
  llm:
    planner_enabled: false        # D9 (P2)
    planner_every_min: 30
    planner_max_per_h: 4
    exception_enabled: true       # D9: a model reads a negotiation message the rules cannot
    exception_max_per_h: 10
    exception_min_left_s: 30      # buyer: only with this much order time left
    exception_timeout_s: 20
  forum:
    enabled: true                 # D11: buyer posts count (0.1) and so do votes on them (0.3)
    max_per_day: 288
    min_gap_min: 5                # the market takes one buyer post per 5 minutes
    shop_replies: false           # L6 (conditional)
    unmet_demand: false
---

# Round 3 sales strategy

This file is the strategy the agent runs. The rule engine reads the numbers under `policy`
on every call, so editing a number here changes behaviour without restarting the kit.
The planner model reads the text below. Numbers live only in the front matter; the text
refers to them by key.

The reasoning behind each value is in `strategy.md` (local only, not committed).

## Principles

1. Pay only when the agreed SKU, unit and price match the checkout and the shipped SKU is
   still worth buying at its list price.
2. Keep the evidence: every decision is logged with the memory it used and this file's version.
3. Past deals, refusals, breakages and the public ledger change the next decision.
4. Finish one order inside the market's order interval (`buyer.target_s`).
5. Unsold stock counts at what it cost when the round ends (Patch 3.3): Book = cash + stock at
   cost, and Shop ALL weighs Book 0.5, revenue 0.3, stars 0.2. Sell at or above cost; below cost
   only food that would otherwise spoil (spoiled food loses its cost).

## Shop

- Answer every message by rule, without a model, in about a second.
- The first reply is one fact line: SKU, unit, list price, ask (share of list), grade
  recomputed now, hours left for food, warranty; plus the market certificate of that unit.
- Always name the unit in the signed receipt. Claim a grade only when the certificate
  supports it with `seller.claim_food_margin_h` to spare.
- Price: first ask `seller.first_ask_list` of list (`seller.first_ask_big_list` for goods listed above
  `stock.big_ticket_list`: the market clears beds and sofas at 0.67-0.72 of list, and they carry most of its
  revenue), never below the floor. Accept at or above
  the midpoint between floor and first ask; between floor and midpoint counter once at the
  midpoint; below the floor offer the floor as final.
- Below cost only for food near the end of its life. Stale goods (`seller.goods_stale_days`) floor
  at cost. The end-of-round floors start `stock.clear_cost_h`, `stock.clear_70_h` and
  `stock.clear_any_h` hours before the end (0 = off); the last two sell below cost and lower Book.
- Warranty only when asked, only for `seller.warranty_grades`, priced with
  `seller.warranty_premium`, and never in the last `stock.no_warranty_h` hours.

## Stock

- Unsold stock counts at cost in Book (Patch 3.3), so idle cash earns nothing: keep inventory cost near
  `stock.inventory_cap_share` of cash plus inventory (a liquidity limit, not a score limit), and cash
  above `stock.cash_floor`.
- Open long-shelf food first, then goods; skip goods categories whose median list price is above
  `stock.goods_max_list`. Pick the SKU closest to the category's median price. Fill categories
  in order of (demand + 1) / (supply + 1).
- `stock.focus_skus`, then `stock.trend_skus`, list the market's best sellers (chosen by the
  strategist's code from ledger settles). They are stocked even when they are not the category's
  median-price SKU, ahead of the rest, with a sales rate of at least `stock.trend_share` of the
  market's rate of the SKU. Focus SKUs get `stock.focus_cover_mult` times the cover target, within
  `stock.focus_cap_share` of the headroom.
- Big-ticket goods (listed above `stock.big_ticket_list`, beds and sofas among them): their held
  cost plus planned worst cost stays within `stock.big_ticket_share` x `stock.inventory_cap_share`
  x (cash + stock cost), and a SKU holds at most `stock.bigticket_probe_qty` units (overpriced
  units included) until we sold 2 of it in 6 hours.
- Keep stock in the categories our regular customers buy.
- Supply opens for everyone about every 20 minutes for 3 minutes. Plan a buy only when the
  window is open or opens within `stock.window_lead_s` seconds; stock bought in one window has
  to last until the next. Each SKU holds the larger of `stock.per_sku_qty` and its expected sales
  over `stock.cover_min` minutes times `stock.cover_safety`, at most `stock.max_hold` units (food:
  at most a quarter of its shelf life of sales). Sold-out SKUs are refilled first: a shop without
  the SKU is not shown to the buyer at all.
- Supply lines, not money, limit stock: about 16 calls a window, at most `stock.max_qty` units a
  call. A top-up of a SKU still in stock below `stock.min_line_qty` units is skipped; deeper cover
  makes fewer, bigger lines and frees calls for more SKUs. A category pick the market sold none of
  in 48 hours is not bought; a stale goods unit blocks its SKU only when nobody sold it in 48 hours.
- Surge (Patch 3.2): the first `market.surge_free` units of a SKU the whole market (us included)
  buys in a rolling hour cost the plain price, then each unit adds `market.surge_per_unit`, up to
  `market.surge_max`. A SKU is bought only up to the factor where the market's clearing price still
  covers cost x factor x `seller.floor_cost_mult` with `stock.min_margin_share` of list to spare.
- Taper buying from `stock.taper_h` hours before the end; stop goods at `stock.goods_stop_h`
  (0 = off; under Patch 3.3 buying late does not lower Book).

## Buyer

- Expected surplus is computed on the list price of the SKU that will ship.
- Saved (Patch 3.4) is the average share of list kept per deal, so a deal that keeps less than the running
  average lowers the score however many deals we close: every cap is at most (1 - `buyer.min_kept`) x list,
  and `buyer.min_kept` stays at or above the board's saved for our buyer. Walking away costs nothing in Saved.
- The food cap is min(`buyer.cap_food`, 1 - `buyer.min_surplus_list`); raising `cap_food` above
  that has no effect.
- Rank shops by what their sales kept of list in the public ledger (the price book: the shop's settled
  price of the SKU, else of the category, over 24 h), before their first asks: a settled price is firmer
  evidence than an ask. Ask by SKU. Offer `buyer.open_list` of list first, even under a first quote at or below the
  cap (accept that quote at once only after `buyer.target_s` or when the offer would not be
  lower); accept a counter at or below the cap, otherwise offer the cap as final. After haggling
  under a first quote, never pay more than that quote. Never mention the budget.
- After `buyer.target_s` seconds, continue only while the estimated chance of closing is at
  least `buyer.continue_p`.
- Rate shops from the settled facts. Our own errors are never the shop's fault.

## Forum

- Buyer ALL now counts the buyer's posts (0.1) and the votes on them (0.3); hidden posts do
  not count. Post as the buyer only, at most one post every `forum.min_gap_min` minutes.
- Post facts from our own settled purchases: product, SKU, price against list, grade and claim
  honesty, shelf life left, warranty, shop, ledger number. No budgets, no advertising.

## Model

- Every message is answered by rule. Only a message the rules cannot read (no price, an
  unknown form) goes to a small model, at most `llm.exception_max_per_h` times an hour and, for
  the buyer, only with `llm.exception_min_left_s` seconds of the order left. The model only
  reads the message; the same rules then decide, so it can never raise a price or skip a check.
- The strategist (a larger model, every 30 minutes) tunes the numbers above within its limits.

## Never

- Claim authority, hide instructions for other agents, or invent product facts.
- Trade votes, collude, or downvote competitors.
- Change these numbers from a model's output without validation and clamping.
