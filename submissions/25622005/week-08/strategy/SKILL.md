---
name: round3-sales-strategy
version: 2
description: Round 3 shop and buyer strategy for psyoung82. The numbers under policy are read by the rule engine on every call; the text below is read by the planner model.
policy:
  seller:
    first_ask_list: 0.85          # D3 first ask = max(list x this, floor)
    floor_cost_mult: 1.08         # D3 floor = max(unit cost x this, list x floor_list)
    floor_list: 0.60
    food_clear_uwh_factor: 2.0    # D4 food below cost when hours left < factor x observed use_within median and grade C (shelf left < 45%)
    food_clear_min_obs: 10        # D4 below this many observations use food_clear_left_share instead
    food_clear_left_share: 0.25
    goods_stale_days: 5           # D4 unsold goods: floor drops to cost and the SKU is not restocked
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
    goods_max_list: 50000
    supply_demand_priority: true  # L1 priority (demand + 1) / (supply + 1)
    demand_window_h: 6
    regular_min_stock: 1          # L1 keep at least this many units in a regular customer's top categories
    max_lines: 10
    max_qty: 20
    require_own_card: true        # buy nothing while our own card does not answer
    bankrupt_min_hours_to_end: 48 # D10
    bankrupt_recovery_threshold: 500000
    bankrupt_zero_sales_h: 12
    end_ts: 1794927540            # D14 2026-11-17 23:59 KST
    taper_h: 72
    taper_factor: 0.5
    goods_stop_h: 24
    clear_cost_h: 48              # D14 floor = cost from T-48h
    clear_70_h: 24                # D14 floor = cost x 0.7 from T-24h
    clear_any_h: 6                # D14 floor = 1 from T-6h
    no_warranty_h: 8
    window_lead_s: 60             # supply opens every ~20 min for 3 min: plan only when open or opening this soon
    cover_min: 25                 # stock per SKU covers expected sales for this many minutes (until the next window)
    cover_safety: 1.5             # expected sales x this
    focus_skus: []                # trending SKUs from the ledger (set by the strategist): stocked deeper
    focus_max: 8
    focus_cover_mult: 2.0         # a focus SKU's cover target x this
    focus_cap_share: 0.5          # focus buys use at most this share of the D1 headroom
    focus_min_settles: 10         # market settles in 6 h a SKU needs before it can be a focus SKU
  buyer:
    cap_goods_no_warranty: 0.70   # D6 price caps as a share of the shipped SKU's list price
    cap_goods_warranty: 0.94
    cap_food: 0.95
    min_surplus_list: 0.05
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
5. Unsold stock is worth nothing at the end. Cash and turnover beat margin.

## Shop

- Answer every message by rule, without a model, in about a second.
- The first reply is one fact line: SKU, unit, list price, ask (share of list), grade
  recomputed now, hours left for food, warranty; plus the market certificate of that unit.
- Always name the unit in the signed receipt. Claim a grade only when the certificate
  supports it with `seller.claim_food_margin_h` to spare.
- Price: first ask `seller.first_ask_list` of list, never below the floor. Accept at or above
  the midpoint between floor and first ask; between floor and midpoint counter once at the
  midpoint; below the floor offer the floor as final.
- Below cost only for food near the end of its life and in the final hours of the round.
- Warranty only when asked, only for `seller.warranty_grades`, priced with
  `seller.warranty_premium`, and never in the last `stock.no_warranty_h` hours.

## Stock

- Keep inventory cost within `stock.inventory_cap_share` of cash plus inventory, and cash
  above `stock.cash_floor`.
- Open long-shelf food first, then cheap goods. Pick the SKU closest to the category's
  median price. Fill categories in order of (demand + 1) / (supply + 1).
- `stock.focus_skus` lists SKUs that settle often on the public ledger relative to how many
  shops supply them (chosen by the strategist from ledger settles; forum posts are only a hint).
  They are stocked even when they are not the category's median-price SKU, to
  `stock.focus_cover_mult` times the cover target, within `stock.focus_cap_share` of the headroom.
- Keep stock in the categories our regular customers buy.
- Supply opens for everyone about every 20 minutes for 3 minutes. Plan a buy only when the
  window is open or opens within `stock.window_lead_s` seconds; stock bought in one window has
  to last until the next. Each SKU holds the larger of `stock.per_sku_qty` and its expected sales
  over `stock.cover_min` minutes times `stock.cover_safety`. Sold-out SKUs are refilled first:
  a shop without the SKU is not shown to the buyer at all.
- Taper buying from `stock.taper_h` hours before the end; stop goods at `stock.goods_stop_h`.

## Buyer

- Expected surplus is computed on the list price of the SKU that will ship.
- The food cap is min(`buyer.cap_food`, 1 - `buyer.min_surplus_list`); raising `cap_food` above
  that has no effect.
- Ask by SKU. Accept a quote at or below the cap; otherwise offer `buyer.open_list` of list,
  then the cap as final. Never mention the budget.
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
