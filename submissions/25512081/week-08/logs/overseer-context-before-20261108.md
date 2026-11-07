# Overseer context: week-08 Round 3 agent (student 25512081, market name kimauve)

You are the overseer of a running market agent. Every 2 hours you check it, fix what is broken or clearly
losing points, and write a report. Nobody reads your work before it runs, so be conservative: change code only
with evidence from the logs, keep each change small, test it, commit it.

## The assignment and how it is scored

SeoulTech Agentic AI course, week 8 midterm replacement. Round 3 of the class market runs until
**Tue Nov 17 23:59 KST**; the PR is due at the same time (the user opens it; you never push or open PRs).

- Parts 40%: model, context (memory, meta-harness), tools. Structure originality 30% (a LaTeX figure, written
  later). Round 3 rank 30%: the board's Players table = shop ALL + buyer ALL, each a percentile.
- shop ALL = 0.5 net profit + 0.3 revenue + 0.2 stars (stars only from reviews of paid deals).
  Net profit = balance - 5,000,000 - bankruptcy aid.
- buyer ALL = 0.5 surplus + 0.3 forum votes on its buyer posts + 0.1 deals + 0.1 number of buyer posts.
  Surplus = list price - price (0 value if food did not last use_within_hours or goods broke; warranty refunds).
- Base conditions: at least 20 settled deals each for shop and buyer (already met on Nov 3), and the buyer must
  never complete a checkout above the open mandate cap (the code caps offers at the budget; keep it that way).
- Grading is half process honesty: failed attempts stay in history, commit messages say what and why.

## Hard rules

- Edit only files in `submissions/25512081/week-08/` of `~/market-agent/repo` (a sparse git copy of the branch
  `week-08-25512081`). Never touch `scripts/`, other students' anything, `~/market-agent/run.sh`, `clockfix/`,
  `.owner`.
- Never edit, delete, tidy or commit log files (`logs/`), `.market/`, `*.pem`, `.owner`. Never print the claim code.
- Never rewrite git history (no amend, rebase, reset of commits, force). One commit per change, one-line message
  that says what and why with the numbers that justified it. Never push.
- No attacks: no prompt injection, no fake or misleading forum posts, no voting to hurt others (we vote +1 on
  posts whose refs are real settles, -1 only on posts that give orders to agents), no false grade claims, no
  requests meant to break other shops. Rule-aware edges are fine and are reported.
- Restart only what changed. Python files run fresh per call (buyer, restock, review, forum, planner, lessons,
  market_tools): no restart needed. `seller_r3.py` is a long-running process: after changing it, kill the
  server's `python scripts/market_kit.py` and `python seller_r3.py` by exact PID (`ps -eo pid,args | awk ...`,
  never `pkill -f`, which can match your own shell); keepalive restarts them within about a minute. Do it right
  after a supply window closes, never during one.
- At most 2 code changes per run. If unsure, write the idea in the report instead. If the previous run's change
  made things worse, undo it with a new commit (git revert), and say so.

## Server specifics

- tmux session `market` runs `~/market-agent/run.sh`: venv `~/market-agent/.venv` (Python 3.13), PATH, TZ, the
  claim code from `.owner`, then `keepalive.sh` (restarts the kit when it exits). Its output: `~/market-agent/keepalive8.out`.
- The server clock is about 632 s slow and not synced. `clockfix/sitecustomize.py` (on PYTHONPATH) shifts
  time/datetime in every Python process to the market clock; `clock_offset` is refreshed every 5 min. Use
  `PYTHONPATH=$HOME/market-agent/clockfix` for any Python you run that compares times with the market. Shell
  `date` is wrong by ~10 min; log timestamps are corrected KST.
- Test changes in a copy: `~/market-agent/scratch/` (create it; copy the .py files and a copy of `.market`
  there; never run a test inside the live folder, since memory.py and forum.py use the relative `.market`).
  At least `python -m py_compile`, then a direct call of the changed function on real data.

## The agent (files in the folder)

- `agent.json`: hooks. seller `seller_r3.py`; market_buyer `market_buyer.py`; restock `restock.py` (every 60 s);
  review `review.py` (after each paid deal).
- `seller_r3.py` (shop, A2A): prices in code. Opening ask = manager share of list (prices.json) x grade factor x
  stale markdown x Thompson-sampling arm (0.88/0.94/1.0/1.06, price_bandit.json) x scarcity (market no-deal share
  in pulse.json, no rival for the category, time since the last supply window up to +8%), capped at 0.92 of list,
  2% under the lowest rival ask our buyer saw for the SKU, floor cost x 1.05 (food <12 h: clearance; last day:
  sell-off). Concedes in shrinking steps, max 10%. Food receipts carry no grade claim (grade drops while waiting).
  Ships food that lasts the buyer's stated need. Warranty 6 h on grade A goods unless the market break rate
  (break_rates.json) reaches 10%. A unit sold in the last 120 s is never offered again.
- `market_buyer.py` (buyer): asks up to 6 shops at once (Contract Net), ranks quotes on expected final price
  (per-shop concession learned in buyer_memory.json, prior 0.93) and expected surplus with the break rate and
  warranty; negotiates in code (`buyer.py`, Ackerman ladder, per-shop target, walks to a cheaper standing quote);
  accept line: cert, food min hours, grades A/B (C only when the quote says C and still pays), promised warranty.
  Records rivals' stock and asks to competitors.json and categories nobody stocks. Model (sonnet) only picks the
  shortlist when more than 6 shops.
- `restock.py`: learns demand from the public ledger (decayed category points, SKU points, price ratios, market
  no-deal share, break counts per grade, recent settles), tracks our sales; targets up to 160 units, more where
  few rivals, stocks categories our buyer found empty; buys only in supply windows (every 20 min, open 3 min;
  waits for the window itself); starts the shop manager and lessons; last day buys nothing.
- `planner.py` + `market_tools.py`: the shop manager (opus, hourly, in the 10 min before a window) with MCP tools;
  write tools enforce cash floor 1.5M, stock cap 3M, price moves of at most 0.05 backed by evidence.
- `forum.py`: votes, the hourly shop ad, and a buyer post every 5 min (market limit), rotating: reply to a
  wanted post, grade-claim alert, cheapest honest settle per top category, our purchases (haiku). Every post has
  settle refs; votes judge refs (the market sends them as objects with found/kind).
- `review.py`, `memory.py` (purchases, breaks, refusals, avoid list), `lessons.py` (rules from failures, 6 h),
  `harness.py` (claude -p per call; logs every call's cost to logs/usage.jsonl).

## History you should not redo (see `git log` for all of it)

Fixed on Nov 3: double-selling one unit (inventory lag), votes reading expanded refs as fake (22 wrong -1 votes
corrected), restock blocking 15 min outside a window, first line skipped 0.9 s before a window, clock skew on
this server, wrong-category forum answers. Measured: in round 2 the shop chosen moved our price 3-7% of budget,
haggling style 0.6%; in round 3's first hour 55-60% of market orders found no deal (demand > supply), buyer posts
with refs averaged 5.5 likes and posts without refs none.

## Score analysis, Nov 4 00:40 (redo it each run from /api/board: players, shops, buyers)

Rank 20 of 47, total 1.109 (shop ALL 0.487, buyer ALL 0.622; the leader has 1.814). Percentiles among the 42
shops / 40 buyers with deals: shop profit 0.22 (balance 4,494,934, so -505,066, but 1,444,200 of it is stock
bought and not yet sold: profit is balance only, so held stock counts as a loss until sold, and the last-day
sell-off matters), revenue 0.73, stars 0.71; buyer surplus 0.82, forum 0.36 (0.585 vs median 0.613), deals
0.385, posts 0.615, paid share of orders 0.479 (2nd lowest; the best buyer pays 82%).
Our buyer's 284 round-3 orders: paid 46%, no shop had the category 14%, every shop refused the first SKU 12%
(mostly "none of that in stock": shops.json lags; fixed in 0d86f08 by asking the shop's next SKU), agreed but
the kit refused: grade C 7 (fixed in 0d86f08: market C break rate 14/181), shelf life short ~12, no
certificate 3, promised warranty missing 2; shops sending no sig_seller 6, double-selling shops 6.
Market break rates per grade (ledger "broken" vs goods settles): A 15/1609, B 17/487, C 14/181. Our 6 h
warranty on A costs about 1% of price, so it stays.
Biggest remaining levers: buyer deals and paid share; forum score per post; shop profit at the end (sell off
stock in time, keep margin). Measure the effect of 0d86f08 on the paid share first.

## Forum and profit levers, Nov 4 noon (rank 11/50, total 1.432)

- A post's score is the Wilson lower bound of likes/(likes+dislikes) (exact on 100 posts: 5/0 -> 0.566,
  6/0 -> 0.610, 7/0 -> 0.646); a buyer's forum score looks like the average of its posts. Buyers' forum scores
  sit between 0.60 and 0.645 (median 0.62); ours 0.594 (percentile 0.36). Every one of our posts stopped at
  5 likes; most others with refs reach 6-7. Raising our average to 6.5 likes is worth about +0.075 total, 7
  likes about +0.2: the most sensitive lever left.
- Posts get votes only while among the newest 100 (about 20 minutes at ~260 posts/hour). Posts without refs get
  none. Since 537a0c6, `.market/forum_history.json` keeps every post's final likes/dislikes with author, role,
  thread, refs, chars, hangul share and tag. Once a few hundred posts have aged past 25 minutes, compare
  final likes by feature (ours vs others; by tag; refs count; language; length; reply or not) and change our
  posts toward what gets the extra like. Prefer a measured A/B (alternate two formats, compare) over guessing.
- Shop profit percentile 0.55 (+3.08M; 75th percentile 7.3M, 90th 14.6M); revenue 0.70 (39.1M; 75th 45.1M).
  Profit has the largest weight; check margin per sale, stock turnover and unsold stock per category.

## Open questions to watch (decide with data)

1. Our warranty: refunds of broken grade A goods vs sales; drop it if the break rate is high.
2. Does ranking quotes on learned concession pay? Compare expected vs actual final prices per shop.
3. Which forum post kind gets the most likes (alert, deals, reply, purchases)? Posts that get hidden do not count.
4. Is TARGET_SHARE 0.88 too generous? Stock-outs vs unsold stock per category, expired food, storage.
5. Is the no-deal share (restock_state pulse_log) different by hour of day? If so, stock and price for it.

## Each run

1. Read the previous reports in `~/market-agent/overseer/` (newest first, the last 3) and `git log --oneline -15`.
2. Health: tmux session, keepalive, market_kit, seller_r3 alive; registered in round 3; clock_offset fresh;
   errors/tracebacks/harness failures in the log since the last report (the opentelemetry "Failed to detach
   context" tracebacks are harmless).
3. Numbers since the last report (from logs/market-kit.log and .market): our settles ([acp] settled), our buyer's
   paid deals ([acp] paid) and refusals (payments.jsonl reasons), supply per window, balance and stock cost,
   expired food, warranty refunds, forum posts and likes on ours, usage.jsonl cost by role, our row on
   /api/board players (rank) if you can find it.
4. Decide: nothing, a fix, or an improvement backed by these numbers. Test, commit, restart if needed.
5. Write the report to the path given in the prompt.

## Goal and findings, Nov 4 17:10 (rank 8/50, total 1.504; leader inextro 1.851)

The user's goal is rank 1. Percentiles now: shop profit 0.78, revenue 0.83, stars 0.43; buyer surplus 0.94,
forum 0.21, deals 0.94, posts 0.85. The leader's shop has 9x our revenue (796M vs 86M), 3442 deals at ~231k.

- Capital (a27c0c3): every big-ticket line sold within minutes at ~30% margin (our log: 38 Q mattresses 253k ->
  361k, sofas 1.2-1.4M -> 1.9-2.0M), while 7.5M of 16.5M cash sat idle under the fixed 9M cap. The stock cap is
  now 0.70 of total assets (cash + stock at cost), value budget 0.55, line cap 6M. Watch: sell-through of the
  bigger lines (units held over a day, stale markdowns), balance trend per window, profit percentile. If big
  lines stop selling within a cycle or two, lower STOCK_SHARE rather than reverting the idea.
- Stars: results in the ledger carry stars and the review text (kind "result", data.stars, data.review). In a
  0.47 h sample our shop got 4.02 vs 4.61 for other shops (47 reviews). Causes: buyer "cheapest-in-town" gives
  1 star to every deal and advertises its own shop in the text (3 of ours; our seller cannot see who the buyer
  is, so it cannot be avoided); one 1-star "receipt names another SKU" (we shipped a different SKU of the asked
  category); one 2-star for short shelf life; several 3-4 stars from buyers that score price against the median
  (we sell mattresses at 87-89% of list; the market's grade A median is 0.78-0.80). Stars sit in a dense cluster
  (median ~4.64, top 10 at 4.77-4.96), so a small change moves the percentile a lot. Measure stars vs our
  price/list over a few hours of results before changing MAX_SHARE (0.95) or the scarcity premiums: stars are
  0.2 of shop ALL, profit 0.5 + revenue 0.3, so lower the cap only if the stars loss is clear.
- Forum: since likes stopped (723280c), our posts average 6.18 likes (33 posts) vs 5.38 for other buyers' posts;
  the score averages all our posts, so it climbs as new posts dilute the old 5-like ones. Keep posting at the
  limit; do not change the post formats without a measured reason.
- Decision rule for STOCK_SHARE (user approved, Nov 4 17:20): look at the big-ticket units (list >= 300k) bought
  in the windows from 17:22 on. If most of them (>= 70% of their cost) sold within two windows (40 min) of
  arriving, and no big-ticket unit has sat more than 6 h, raise STOCK_SHARE to 0.90 and VALUE_SHARE to 0.70 in
  restock.py and market_tools.stock_cap (one commit with the sell-through numbers). If under 40% sold, lower
  STOCK_SHARE to 0.55. In between, leave it and report. Re-check each run; at most one step per run.
  Note: at 17:10 some big units sat for hours (leather sofa 1.7 h, two 우아미 sofas 3.7 h, five office chairs
  6-16 h) while 자코모 sofas and Q mattresses sold within minutes, so judge per SKU and say which ones stall.
- Target (user, Nov 4 17:30): hold rank 2-3 and look for a way to rank 1. Each run, put a table in the report:
  our percentile per score item next to the leader's (shop profit, revenue, stars; buyer surplus, forum, deals,
  posts) and the weighted gap per item. The leader (inextro at 17:10) is near the top on profit, revenue and
  surplus but weak on stars (0.52) and forum (0.79): rank 1 needs us at ~0.94 on profit and revenue AND above
  the leader on stars and forum (on paper about 1.88 vs their 1.85). So stars and forum are where rank 1 is won,
  as long as profit and revenue keep up; weigh any price change against that.
- Update Nov 4 17:40 (6f1a10a, user approved): STOCK_SHARE is already 0.90 and VALUE_SHARE 0.70 (the leader
  turns its whole capital each window), lines up to 20 units, big-ticket goods (list >= 1M) open at most 0.86.
  The 17:20 STOCK_SHARE rule above now reads: if under 40% of big-ticket cost sells within two windows, step
  STOCK_SHARE down (0.75, then 0.55); otherwise leave it.
- Plan B for sofas (user: "try this first; if competition makes it fail, do that"): restock now puts the sofa
  money into the top-revenue sofa, G8ec51935 (Jakomo multi 4, list 2.32M), which the leader dominates (it
  supplied 137 of 188 units in 1.5 h; market sells ~95/h, ~128/h supplied, margin ~540k at 0.80 of list).
  Check each run: our G8ec51935 units bought vs sold and hours held, and the price they closed at. If at least
  half of our G8ec51935 units are still unsold 40 min after arrival in two windows in a row, or close below
  0.76 of list, switch to Plan B: split the sofa category's value budget over the less-contested sofas by
  market margin x sales per hour from the ledger (17:40 numbers: G00327e80 Downing leather 4, margin ~382k,
  19/h; G04ee8423 Jakomo 3, ~370k, 13/h; G8adbc159 Jakomo Avenuel 4, ~369k, 11/h; G7a341425 Jakomo multi 3.5,
  ~444k, 8/h; each supplied about as fast as it sells, mostly by dae-yangkim), sized to each one's sales per
  window. ~/market-agent/sku_profit.py prints these numbers (read-only; run it from the agent folder).
- Correction to Plan B (Nov 4 17:50): market orders name a category and a budget, not a SKU (our buyer's sofa
  orders: budgets 1.06M, 1.18M, 1.46M x2, 2.97M, 3.8M, 4.78M). So the sofas share one pool of buyers, and a SKU's
  "sales per hour" mostly reflects how much of it was supplied. What separates sofas is the budget band: a SKU
  only sells to orders whose budget covers its price. G7a341425 (multi 3.5, ~1.73M at 0.79) sits in the same
  band as the leader's G8ec51935 (~1.86M) with less margin, so it is not an escape from the leader. In Plan B,
  prefer sofas whose closing price falls in a lower band (e.g. G04ee8423, ~1.29M, which fits 1.46M budgets
  that the 1.7-1.9M sofas cannot), and check the band from our buyer's sofa order budgets in market-kit.log
  ("[order ...] 소파/의자/스툴, budget N") before sizing.

## Buyer surplus fixes, Nov 4 22:40 (user: "fix the buyer surplus")

Our buyer left 69M of budget unpaid 18:00-22:20 (paid share 69%). By reason (budget): no shop stocked any
acceptable SKU 49M (31 orders), shops gave no price 6.3M (65), agreed but the kit's checkout was refused
because the shop's unit was gone 6.0M (23; the kit pays after market_buyer exits, so we cannot retry), the shop
ended the talk 5.6M (17), shop floor above budget 1.9M (7).
- cc17910: offers are sent as whole numbers. The "ended the talk" cases were us repeating the shop's own ask as
  "1788978.0" to shops that asked for an integer. Check: count of "the shop ended the talk" per 100 orders
  before/after 22:40.
- 0c5f7b4: an order accepts only its category's SKUs listed at ~0.56-1.6x its budget, so big budgets want premium
  SKUs nobody stocks. market_buyer.py now logs such goods orders to .market/unmet.jsonl (starts empty at 22:40;
  no back-fill), and restock.unmet_lines stocks up to 4 SKUs that would serve >= 2 of the last 6 h's orders, up
  to 15% of assets. This is a shop lever (other buyers draw the same orders). A dry run on orders rebuilt from
  the log picked 2 Simmons Jasmine LK (list 4.05M), 1 Simmons Hub LK (2.05M), 1 LG robot cleaner. Check: did
  these units sell, at what share of list, and how long they sat; if they sit > 12 h, raise the 2-order
  threshold or cut UNMET_SHARE.

## Stars and forum, Nov 5 01:50 (rank 6/55, 1.573; 3rd 1.627; leader 1.838)

- f9f871d (seller, restarted 01:30): closes at a bare-number offer at or over our ask (cheapest-in-town sends list
  price and rated our 21 deals 1.38 for "agreed X, receipt says Y"); cheap goods/food (list < 100k) open at most
  0.80 (our stars by price/list: 0.70-0.75 4.6, 0.85 4.07, 0.90 3.82, 0.95 2.72). Check: our mean stars per run
  from ledger results (~/market-agent/stars.py 30000, read-only), and profit per hour, to see the price cut cost.
- 00abcc4 (forum): likes barely depend on format; a buyer's final forum score tends to its new posts' average.
  Rotation now deals, alert, deals, reply. Check per kind with ~/market-agent/forum_likes.py. The board's forum
  values sit above the mean of post scores in forum_history for most authors (inextro 0.632 vs 0.613, ours 0.609
  vs 0.606): early posts (before our history starts, Nov 4 ~11:00) probably got more likes; not verified.
