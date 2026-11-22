---
name: round3-sales-strategy
version: 653
description: Round 3 shop and buyer strategy for psyoung82. The numbers under policy are read by the rule engine on every call; the text below is read by the planner model.
policy:
  seller:
    first_ask_list: 0.8          # D3 first ask = max(list x this, floor)
    first_ask_big_list: 0.67      # D3 first ask for big-ticket goods (list above stock.big_ticket_list): the market clears them at 0.67-0.72
    floor_cost_mult: 1.0         # D3 floor = max(unit cost x this, list x floor_list)
    floor_list: 0.57
    food_clear_uwh_factor: 3.0    # D4 food below cost when hours left < factor x observed use_within median
    food_clear_min_obs: 10        # D4 below this many observations use food_clear_left_share instead
    food_clear_left_share: 0.25
    goods_stale_days: 1           # D4 unsold goods: floor drops to cost and the SKU is not restocked
    warranty_on_request: false     # D5 warranty only when the buyer asks
    warranty_hours: 6
    warranty_grades: [A, B]
    warranty_premium: 0.15
    warranty_cap_6h: 120000000      # D5 sum of warranted sales in the last 6 h
    fact_block: true              # L5 first reply carries the fact line and the market certificate
    claim_food_margin_h: 1.0      # L5 claim a food grade only with this much margin to the next grade cut
    promise_ttl_s: 180            # a unit promised in an open negotiation is held this long
    intro_price_enabled: false    # L4 (conditional, day-1 data)
    stale_floor_cost_mult: 1.0    # D4 stale goods floor as a share of cost: 1.0 while Book counts stock at cost; below 1 only when holding costs more than the margin
    warranty_q_mult: 1.25          # D5 safety on the measured breakage (memory: market.breakage, SKU x grade); without data the premium is warranty_premium
    market_list_A: 0.69           # D3 the market's price/list for a grade: goods offer first a unit whose floor is at or under it
    market_list_C: 0.4
    market_list_B: 0.6
  stock:
    inventory_cap_share: 0.90     # D1 inventory cost <= share x (cash + inventory cost)
    cash_floor: 1000000           # D1 bankruptcy line 500,000 + refund reserve 500,000
    worst_cost_share: 0.75        # D1 affordability uses the worst supply cost
    food_shelf_hours: 720         # D2 food categories opened from the start
    per_sku_qty: 5
    short_food_max_shelf_h: 168   # D2 shorter shelf categories wait for observations
    short_food_min_uwh_obs: 10
    goods_max_list: 600000
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
    taper_h: 0
    taper_factor: 0.5
    goods_stop_h: 0
    clear_cost_h: 60              # D14 floor = cost from T-48h
    clear_70_h: 0                # D14 floor = cost x 0.7 from T-24h
    clear_any_h: 0                # D14 floor = 1 from T-6h
    no_warranty_h: 8
    window_lead_s: 60             # supply opens every ~20 min for 3 min: plan only when open or opening this soon
    cover_min: 105                 # stock per SKU covers expected sales for this many minutes (until the next window)
    cover_safety: 2.5             # expected sales x this
    focus_skus: [G05277ffe, G3434758a, G021aae64, G07ec17d5, G19a70f63, G114aefb6, Gf6b70da8, G0132ff94, G2560a8c9, G35351812, G9c9f8f94, G9718023b]                # trending SKUs from the ledger (set by the strategist): stocked deeper
    focus_max: 12
    focus_cover_mult: 1.25         # a focus SKU's cover target x this
    focus_cap_share: 0.5          # focus buys use at most this share of the D1 headroom
    focus_min_settles: 10         # market settles in 6 h a SKU needs before it can be a focus SKU
    big_ticket_share: 0.90        # goods above goods_max_list: held + planned cost <= this x inventory_cap_share x funds
    trend_skus: [G29fc9227, Gef4d3036, G53620cc2, G124d3ea5, G022ce883, G582603b2, G74e686be, G32bf9a8e, Gb909a9c8, G5079f192, G64701e11, Gd1f907d6, G200e39a3, G66ded25b, G80435d53, G010744b7, G0b0ca441, G1f2acd72, Gbec2b68f, G1dd63027]                # the next best sellers (set by the strategist): picked like focus, ordered after it
    trend_share: 0.20             # focus/trend SKUs are stocked for at least this share of the market's settle rate (stockouts hide our own rate)
    bigticket_probe_qty: 8        # a big-ticket SKU holds at most this many units until we sold 2 of it in 6 h
    min_margin_share: 0.05        # surge: buy while clearing price - this covers cost x factor x floor_cost_mult
    max_hold: 20                  # units one SKU may hold (the cover target's cap)
    min_line_qty: 3               # skip a top-up of an in-stock SKU below min(this, target / 2): lines are scarce
    trend_max: 20
  buyer:
    cap_goods_no_warranty: 0.8   # D6 price caps as a share of the shipped SKU's list price
    cap_goods_warranty: 0.94
    cap_food: 0.93
    min_surplus_list: 0.07
    min_kept: 0.27                # D6 every cap <= (1 - this) x list: Saved is an average share kept, a deal below it lowers the score
    open_list: 0.2
    max_shops: 10
    food_margin_h: 1.0            # D7 min_hours_left = use_within_hours + this
    target_s: 25                  # D8
    continue_p: 0.55
    msg_timeout_s: 12
    order_cap_s: 60
    binding: true                 # D12 pre-payment binding check in the kit copy
    accept_cert: true
    step_list: 0.05               # added by the strategist (a policy.DEFAULTS key)
    min_kept_C: 0.5               #    so the same kept scores 0.6-0.8); 0 leaves min_kept in charge
    min_kept_A: 0.27               # D6 the kept floor once a quote or receipt names the grade (Patch 3.6 Saved: an A
    min_kept_B: 0.4               #    costs 0.55-0.75 of list, so 0.45 kept already scores 1; a C costs 0.25-0.45,
    break_penalty: 0.5            # a goods deal without the full warranty is capped so that a break, which Saved counts as -1, is priced in (0: off)
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
  at cost x `seller.stale_floor_cost_mult` (1.0 while the market counts unsold stock at cost in Book, so
  holding costs only storage; the strategist lowers it when a rule change makes holding dearer than the
  margin). The end-of-round floors start `stock.clear_cost_h`, `stock.clear_70_h` and
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
- Saved (Patch 3.6) is the mean over deals of (list - price) / (list - unit cost), at most 1 each: the share
  of the shop's margin a deal took. A deal scoring under the running mean lowers it however many deals we
  close, one scoring above raises it. The cost is drawn by grade (A 0.55-0.75 of list, B 0.40-0.60, C
  0.25-0.45), so the kept share of list that scores 1 whatever the cost is A 0.45, B 0.60, C 0.75: every cap
  is at most (1 - `buyer.min_kept`) x list, and once the quote names the grade (1 - `buyer.min_kept_<grade>`)
  x list when that is lower. Walking away costs nothing in Saved.
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
- When no purchase took a slot (the last post is older than the gap plus a minute), post a market price
  report instead: one of the market's best-selling SKUs, its settles per hour, the shops' median paid/list
  and the cheapest shops, with the newest settle seqs as refs (`forum.report_enabled`; a SKU repeats after
  `forum.report_repeat_h` hours). Reports are the posts other buyers like most.

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

## Strategist notes

Saved = margin_share. score_twin is paused, so min_kept is mine; keep it at 0.27. Settings: market_list_A 0.69, C 0.40; stale_floor 1.0; first_ask_big 0.67; clear_cost_h 60 (on), clear_70_h 0, goods_stop_h 0. 23:41 (0.3 h left): class rank 15. Book is 6.5M under the next student up and only 0.53M over the one below. Never sell below cost, because Book weighs 0.5 and the lead below is thin. Revenue gap up 28.8M: out of reach. Saved 0.7392. Leave everything as is to the end.
