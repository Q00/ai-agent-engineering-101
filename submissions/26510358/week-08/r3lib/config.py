"""Paths and strategy constants for Round 3. Every tunable number lives here so REPORT.md can cite one place."""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent              # the agent folder (week-08)
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ROOT / ".market"))
STATE_DIR = MARKET_DIR / "r3"                              # private runtime state (gitignored with .market/)
DB_PATH = STATE_DIR / "memory.sqlite"
SHARED_ENV = ROOT.parent / ".env"                         # submissions/<id>/.env (gitignored): the live OpenAI key
LOG_DIR = ROOT / "logs" / "r3"                             # decision logs we submit as evidence
MARKET_URL = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")

SHOP_NAME = os.environ.get("R3_SHOP_NAME", "Ssadassa Shop")   # receipt "shop" field and card name
MY_NAMES = {"myeonggi", "SUNGMYEONGGI", "Ssadassa Shop"}      # how our agents appear in the ledger

KST = timezone(timedelta(hours=9))
ROUND_END = datetime.fromisoformat(os.environ.get("R3_END", "2026-11-17T23:59:00+09:00")).timestamp()

# --- shop: capital and restock --------------------------------------------------------------------
START_CAPITAL = 5_000_000
CASH_RESERVE = 1_200_000            # never let a restock take the balance below this
MAX_STOCK_COST = 3_000_000          # stock budget floor, and the base of the coverage (explore) budget
STOCK_BUDGET_SHARE = 0.40           # stock on hand may cost up to this share of the balance (2026-11-04: balance
STOCK_BUDGET_MAX = 60_000_000       #   15.8M held 1.6M of stock while mattress EV said 20 units and the cap said 3)
                                    # 25M -> 60M 2026-11-08: stock sat at the 25M cap in 218 of 224 restock ticks (24 h),
                                    #   balance 210M idle, mattress/sofa still turned away 1.8/1.0 req/h. Board: shop rank
                                    #   18, revenue 4.3M/h vs top-6 21-85M/h; top shops hold 130-760M stock at cost. Book
                                    #   counts stock at cost (patch 3.3) and storage is 1%/day, so idle cash buys nothing
                                    # 16M -> 25M 2026-11-07: every supply window 07:05-09:28 KST ended at 15.5-16.0M,
                                    #   mattress/sofa turned away 9.2/2.7 req/h, balance 161M idle, furniture margin
                                    #   16-17% over cost; our market share fell 3% -> 1.2% while profit/h fell 3.5M -> 1.5M
                                    # 8M -> 16M 2026-11-05: stock sat at the 8M cap from 11-04 18:00 with balance 57M,
                                    #   sofa turned away 8-15 req/h; 12 h market 7.75B, our share 1.4%, top shops buy 450-880M
EXPLORE_SHARE = 0.25                # share of MAX_STOCK_COST for coverage of categories with no evidence yet
EXPLORE_MAX_LIST = 60_000           # coverage buys only SKUs at or below this list price
RESTOCK_LEAD_S = 60                 # emit a buy list only when supply is open or opens within this (2026-11-04:
                                    #   505 supply calls refused with 429 vs 634 bought; the kit skips a line after 90 s)
STALE_MISS_WEIGHT = 0.25            # a request turned away while we held food of it that would not last the buyer's
                                    #   need counts this much as missed demand: only grade A of a new lot (25%, 80%+
                                    #   of shelf) serves a 0.75-shelf order. 2026-11-10: fresh fruit showed 4.7
                                    #   stockouts/h with 31 units on hand, which sized more fruit; fruit, vegetables
                                    #   and shellfish supplied 30-72 h before sold 81/62/65% and expired 19/31/35%
OUTCOME_MIN_AGE_H = 12              # the units supplied to us between 72 and 12 h ago, traced on the ledger
OUTCOME_MAX_AGE_H = 72
OUTCOME_MIN_UNITS = 30              # sold + expired units before a category is judged
OUTCOME_CUT = 0.5                   # a category whose supplied units lowered Shop ALL (score model): half the target
OUTCOME_STOP_EXPIRED = 0.30         #   and none while this share of what we spent on it expired
RESTOCK_GATE_RATIO = 0.65           # a category the market pays under this share of list is not bought (the rule until 2026-11-10)
RESTOCK_GATE_BY_EVIDENCE = True     # our own cohorts decide instead (restock_r3.price_gate); the human said yes on 2026-11-10 22:25 KST
RESTOCK_GATE_NO_EVIDENCE = {"food": 0.54, "goods": 0.64}   # under the flag, without cohorts: grade-mix cost x (1 + MIN_MARGIN)
STOCKOUT_HOURS = 6                  # requests our shop turned away for lack of stock, counted over this window,
                                    #   are demand addressed to us: added to the category's expected sales per hour
STOP_BUYING_HOURS = 72              # no FOOD restock in the last 72 h of the round (it would expire unsold: a loss)
GOODS_STOP_BUYING_HOURS = 12        # goods are bought until 12 h before the end: an unsold goods unit stays in book at its
                                    #   cost (server.py econ_board: book = profit + cost of stock), only storage (1%/day)
                                    #   is lost. Until 2026-11-11 goods stopped at 72 h too: 2.5 of 6.5 days left unbought
CLEARANCE_HOURS = 36                # in the last 36 h, food floors fall toward salvage; goods floors fall to cost
DEMAND_HALF_LIFE_H = 8              # recent settles weigh more
RESTOCK_SKU_COVER_H = 24            # no more of a SKU while the units on hand cover this many hours of our own sales
RESTOCK_SKU_COVER_MIN = 5           #   of it, from this many units up (2026-11-10: need was counted per category only,
                                    #   so the couch was bought 274x in 72 h against 190 sold, 93 on hand)
HORIZON_H = 12                      # stock for the sales we expect in the next 12 h
FOOD_DUMP_SHARE = 0.30             # food floor drops to 0.3 x cost under this share of its shelf life left
FOOD_MARKDOWN_SHARE = 0.50         #   and to 0.8 x cost under this one (orders need 0.25/0.5/0.75 of shelf)
FOOD_SELLABLE_SHARE = 0.25         # restock counts food under this share as gone: no order can take it
FOOD_TARGET_SHELF_SHARE = 0.30     # short-life food: stock at most the sales of this share of its shelf life
                                   #   (0.5 until 2026-11-10; units arrive with A 80%+/B 45%+/C less left)
MIN_FOOD_SHELF_FOR_EXPLORE = 720    # coverage only with long-life food (no expiry inside the round)

# --- shop: expected-value restock (pricing.ev_quantity) -------------------------------------------
CATEGORY_MAX_SHARE = 0.25           # one category's stock cost stays under this share of the stock budget
DEEP_CATEGORY_RATE = 100            # market settles per hour above which a category is deep (many buyers)
DEEP_CATEGORY_SHARE = 0.35          # a deep category may take this share: concentration risk is lower there
EV_COST_RATIO = 0.62                # expected unit cost / list price when the grade is not known yet (A-C mix)
EV_DEFAULT_PRICE_RATIO = 0.75       # expected sale price / list price without market evidence
GOODS_END_RISK = 0.10               # share of cost an unsold goods unit is expected to lose (clearance, round end)
FOOD_LEFTOVER_RISK = 0.15           # same for long-life food that does not sell in the horizon

# --- shop: premium pilot (2026-11-04): goods listed at 800k+ sell 36/h across only 9 shops at 0.80 of list,
#     we sold none. A separate small budget, so a slow pilot cannot starve the main stock.
PREMIUM_MIN_LIST = 800_000          # a goods SKU at or above this list price is premium
PREMIUM_BUDGET = 30_000_000         # worst-case cost of premium stock on hand, outside the normal stock budget
                                    #   (8M/10 until 2026-11-07: 695 premium lines skipped at the budget, 7.5-8.0M held)
                                    #   (12M/15 until 2026-11-08: at the cap in all 224 ticks of 24 h, 2211 premium lines
                                    #   skipped; the two revenue leaders sell 800k+ sofas/mattresses, median ticket 236-410k)
PREMIUM_MAX_UNITS = 36              # premium units on hand at most (2.5M/3 until 2026-11-05: 98 sold in 24 h, 0 held)
PREMIUM_HOURS = 12                  # market sales window for picking premium SKUs
PREMIUM_MIN_SOLD = 10               # a premium SKU needs this many market sales in the window
PREMIUM_MIN_RATIO = 0.65           # skip a premium SKU the market buys under this share of list (0.70 until
                                    #   2026-11-09: 2108 skips in 20 h at 0.67-0.69, yet on those SKUs we sold 258 at
                                    #   0.704 of list against 0.595 cost, +11% of list; 7 units / 4.8M held of a 38M budget)
PREMIUM_HOT_COVER_H = 0.15          # hot premium SKU: hold this many hours of the MARKET's sales of it (2026-11-10:
PREMIUM_HOT_MAX_PER_SKU = 8         # (6 until 2026-11-11)   top shops restock 5 SKUs every window, buy ~= sell, 100-500 premium units; we
                                    #   held 1-2 per SKU and sold 2-12/day of SKUs the market sells 500-1600/day)
PREMIUM_PER_SKU = 3                 # premium units of one SKU on hand at least (1 until 2026-11-09, 2 until 2026-11-11)
PREMIUM_LINES = 5                   # supply lines (10 per order) kept for premium while it has candidates (it ran last
                                    #   and 42 of 144 ticks were full of normal lines before it). 3 until 2026-11-11: 800k+
                                    #   goods were 205 of our sales and 260M of revenue in 24 h (~60%), the 51 held had a
                                    #   median age of 3 h, and we had 3.9% of the market's 5,207 such deals; the 3 lines
                                    #   held the buy to ~10M a cycle while ~200M of balance sat idle

# --- shop: adaptive stock cap (r3lib/cashflow.py), 2026-11-08 -------------------------------------
#     Replaces the hand-raised STOCK_BUDGET_MAX / PREMIUM_BUDGET / PREMIUM_MAX_UNITS in the restock plan; those
#     stay as the starting cap. 25M was at the cap in 218/224 ticks of 24 h with 210M idle; 60M by hand is a guess.
CAP_STEP_MIN = 60                   # the cap moves at most once an hour, so each step is measured on real sales
CAP_GROW = 1.25                     # one hour at the cap with demand turned away: +25%
CAP_SHRINK = 0.85                   # supplied stock that does not sell: -15%
CAP_AT_SHARE = 0.90                 # stock cost at 90% of the cap counts as at the cap
CAP_TURNED_PER_H = 1.0              # requests per hour turned away for lack of stock (24 h: mattress 1.8, sofa 1.0)
CAP_FAST_SELL = 0.70                # or this share of what the market supplied us sold in CAP_SELL_HOURS
CAP_SLOW_SELL = 0.40                # under this the cap shrinks
CAP_SELL_HOURS = 12
CAP_MIN_RECEIVED = 30               # units received before a slow sell-through may shrink the cap
CAP_MIN = 25_000_000                # not below the cap already proven to sell out, unless the balance says so
                                    # ceiling: STOCK_BUDGET_SHARE x balance (the rule since 11-04), over CAP_MIN
CAP_CEILING_SHARE = 0.60            # the adaptive cap's ceiling as a share of the balance (STOCK_BUDGET_SHARE 0.40 until
                                    #   2026-11-11, the human's yes): in 36 h the cap asked to grow 29 times and shrink
                                    #   0 times (sell-through 0.78-1.0) and was clipped at 0.40 x balance every time,
                                    #   with ~200M idle. Unsold stock counts at cost, so the cost of more stock is the 1%/day
                                    #   storage; the shrink rule (sell-through < CAP_SLOW_SELL) still pulls the cap back.
STUCK_GRADE_AGE_H = 24              # a SKU is not bought again while one of its grades has STUCK_GRADE_MIN_OLD units
STUCK_GRADE_MIN_OLD = 3             #   older than this that our own sales of that grade would not clear by the round's
                                    #   end (the market draws grades: each order brings A70 B20 C10, also of a stuck grade)
PREMIUM_SHARE = 1.50                # premium budget = this share of the cap (0.50 until 2026-11-10, 0.80 until 2026-11-11;
                                    #   book counts stock at cost, so idle cash earns nothing: 201.6M balance, 146M stock;
                                    #   rollback if 800k+ units held reach a median age of 24 h). Earlier: book counts stock
                                    #   at cost, the round ends 11-17 and top shops hold 100-760M of stock)
PREMIUM_UNIT_COST = 833_000         # premium units allowed = premium budget / this (30M / 36 as set by hand)

# --- shop: competitor scout (r3lib/scout.py) ------------------------------------------------------
SCOUT_MIN_SOLD = 20                 # a category needs this many market settles in 6 h before the scout has an opinion
SCOUT_LOW_PRESSURE = 0.6            # competitors restock under 60% of what sells: a gap
SCOUT_HIGH_PRESSURE = 1.4           # competitors restock 140% of what sells: crowded
GAP_MAX, GAP_MIN = 1.3, 0.7         # demand-target scaling at the two ends
SCOUT_MIN_OUR_SOLD = 5              # our realised share replaces the 1/(1+shops) prior only on this many own sales
SCARCITY_PREMIUM = 0.03             # first ask +3% of list in an under-supplied category

# --- shop: pricing ---------------------------------------------------------------------------------
START_ASK_RATIO = 0.90              # first ask as a share of list price, before memory has prices
ASK_ROOM = 0.07                     # first ask = market ratio + this (0.05 until 2026-11-04: our asks sat ~5 pts under
                                    #   the market; profit per visit 0.052 at ask 0.70-0.75 vs 0.072 at 0.75-0.80)
                                    # 0.09 -> 0.07 2026-11-05: 2 h close rate under 0.5 for ~9 h overnight (low 0.26)
ASK_MAX_OVER_MARKET = 0.04          # final first ask (after scarcity, grade, warranty price-in) <= same-grade SKU market
                                    #   ratio + this. 2026-11-06, 24 h of our negotiations by grade: furniture A profit per
                                    #   negotiation flat 33-39k from +0.00 to +0.12, 18k at +0.16, 13k at +0.20; other A
                                    #   peaks at +0.04. First try (be31c04) capped the all-grade ratio + 0.10, about A +0.02.
                                    # 0.12 -> 0.04 2026-11-07: after patch 3.4 (buyers score on share of list saved) the
                                    #   curve moved. 30 h of terms->deal, list >= 300k, ask minus same-grade market:
                                    #   +0.00 close 0.34 / 35k profit per quote, +0.04 0.26 / 41k, +0.08 0.14 / 19k,
                                    #   +0.12 0.08 / 8k (last 12 h: 38k, 50k, 14k, 8k); 1,100 of 1,840 quotes sat >= +0.08
OVERSTOCK_SKU_UNITS = 10            # goods SKU with more units than this on hand: offer the grade with the widest
                                    #   margin over its same-grade market price first, C last (2026-11-10: best grade
                                    #   first sold 172 A / 18 B / 0 C of 274 couches bought in 72 h; 53 B + 34 C, 20M,
                                    #   never reached a buyer while A, the thinnest margin, was restocked)
SLOW_SUPPLY_DAYS = 2                # an overstocked SKU is slow when the units on hand last longer than this at our
                                    #   own sales of the last 24 h
SLOW_UNDERCUT = 0.03                # first ask of a slow SKU: same-grade market ratio minus this (our share of a SKU
                                    #   is ~2%, so at the market price 53 B couches would take over a week)
SLOW_FLOOR_AGE_H = 48               # a unit of a slow SKU held this long: floor = cost x (1 - storage for the days left
STORAGE_PER_DAY = 0.01              #   in the round), the price at which selling now equals holding to the end (/rules:
                                    #   stock counts at cost when the round ends and pays 1% of cost a day; 67 A
                                    #   chairs, 13.4M, had floor 0.81 of list against a 0.68 market, 2026-11-10)
MIN_MARGIN = 0.08                   # normal floor: cost * (1 + MIN_MARGIN)
MARKDOWN_EVERY_S = 600              # the CEO tick judges markdowns of overstocked SKUs this often, with or without orders
MARKDOWN_TTL_H = 2                  # a markdown lives this long, then its sales are the bandit's reward and it is judged again
MARKDOWN_MAX_CUT = 0.20             # a markdown never cuts the regular first ask by more than this (RL_PROACTIVE.md 2.1)
BANDIT_ASK_ENABLED = True           # L1: the shop's first ask x one of ASK_ARMS per (kind, grade, slow), learned on board score
ASK_ARMS = (0.9, 1.0, 1.1)          # within +-20% of the rule's ask; floors (L0) are untouched
PREMIUM_ASK_ARMS = (0.9, 1.0, 1.1)  # E2 proposes (1.0, 1.2, 1.5) for premium furniture: beyond +-20%, needs the human's yes
# --- shop: the grade shown first and grade C's first ask (r3lib/shelf.py), the human's yes 2026-11-11 ~10:45 KST --
TASTE_EVERY_S = 600                 # F (r3lib/taste.py): the CEO tick rebuilds each buyer style's preferred grades this often
TASTE_HOURS = 72                    # market-wide stars by (buyer, kind, grade) over this window
TASTE_MIN_N = 10                    # reviews a (buyer, kind, grade) cell needs to count
TASTE_MIN_SPREAD = 0.5              # best minus worst grade, in stars, before a kind gets a preference
TASTE_STYLE_SHARE = 0.7             # one buyer must have this share of a style's results on our shop
SHELF_EVERY_S = 600                 # the CEO tick judges the order of each mixed-grade goods SKU this often
SHELF_TTL_H = 2                     # a pushed grade stays first this long, then it is judged again on what it sold
SHELF_MIN_HELD = 3                  # units of a grade that must have waited SHELF_MIN_AGE_H before it is a candidate
SHELF_MIN_AGE_H = 12
SHELF_MIN_MARKET = 8                # market deals of the SKU at that grade in SHELF_MARKET_H: 48 h, because a thin
SHELF_MARKET_H = 48                 #   SKU (office chair: 5 C deals in 24 h, 11 in 48 h, 12 C on our shelf) is the case
SHELF_DEFAULT_CLOSE = 0.3           # our close rate on a SKU without a quote in 24 h
SHELF_MIN_STARS = 4.3               # guard: a grade whose goods deals average under this over SHELF_MIN_REVIEWS
SHELF_MIN_REVIEWS = 30              #   reviews (4 days) is not pushed (2026-11-11: C 4.58 over 342, A 4.52)
C_ANCHOR_ENABLED = True             # grade C goods: first ask = the SKU's grade A market median x one of the arms,
C_ANCHOR_ARMS = (0.8, 0.9, 1.0)     #   never under the rule's ask; floors and concessions untouched. 3 days of ledger:
                                    #   the market's C deals sit at 0.81 of the same SKU's A median (p75 0.90, p90
                                    #   0.99), ours closed at 0.82 of the market's C median and 71% of C quotes closed
GRADE_FACTOR = {"food": {"A": 1.0, "B": 0.95, "C": 0.82}, "goods": {"A": 1.0, "B": 0.90, "C": 0.75}}
UNSTATED_NEED_SHARES = (0.75, 0.5, 0.25)   # food for a buyer that names no need: the soonest unit lasting this share
                                           #   of shelf life, longest first (orders ask 0.25/0.5/0.75 of it)
WARRANTY_HOURS = 72                 # goods break only within 6 h of the sale (/rules; ours: max 6.0 h), so 72 h (the
                                    #   market's maximum) costs what 6 h does; 2026-11-09: buyers rated 6 h "concerningly
                                    #   short", and market-wide the same buyer rated 72 h +0.065 over 6 h (24 up, 7 down);
                                    #   stars_rank +0.06 is +0.06 Shop ALL in today's dense 4.3-4.5 cluster
WARRANTY_MAX_EXPECTED_COST = 0.5    # offer a warranty while p_break * price stays under half the margin
WARRANTY_GRADES = ("A", "B")        # never on C: 27% of C goods broke market-wide (A 2.4%, B 9.1%, 2026-11-04)
WARRANTY_PRICE_IN = True            # a warranty unit's ask carries its expected refund (p_break x ask), like a paid
                                    #   warranty; refunds were 4.2% of our stock cost vs 0.8% at dae-yangkim
BREAK_MIN_N = 200                   # use the grade x category break rate once it rests on this many goods sales

# --- shop: repeat customers and review defence ----------------------------------------------------
LOYALTY_ENABLED = False             # off since 2026-11-04 16:40: 332 codes issued, 0 quoted. Buyer agents start fresh
                                    #   on every order and never see an earlier deal's message; the 3% also ran
                                    #   against the price raise. Code and the members table stay as evidence.
LOYALTY_DISCOUNT = 0.03             # a quoted member code takes 3% off the first ask (the floor does not move)
LOYALTY_PREFIX = "SSD-"
HARSH_MIN_REVIEWS = 15              # a buyer style needs this many star ratings of our deals before we judge it
HARSH_MAX_STARS = 0.0               # styles averaging at or below this are declined (2026-11-04: one buyer left 64
HARSH_EVERY_MIN = 10                #   3-star reviews with an ad for its own shop in each)
                                    # 3.3 -> 0.0 (off) 2026-11-08: since patch 3.4 stars take one mean per reviewing
                                    #   account, and a declined buyer rates the refusal anyway: 25620027 (style
                                    #   5aac87179a37, 3.0 on 15) logged "timed out" 3 stars on all 5 of our timeouts in
                                    #   1.8 h; 240 declines in 27 h, 0 star gain, every one a lost sale

# --- organisation (r3lib/org): departments over internal signed A2A --------------------------------
ORG_A2A = True                      # route the restock cycle through the internal A2A server (falls back in-process)
KPI_EVERY_MIN = 30                  # ceo.kpi snapshot to logs/r3/kpi.jsonl

# --- forum (pro-activeness, see initiative.py) -----------------------------------------------------
FORUM_MIN_GAP_MIN = 20              # never two buyer posts closer than this (the kit allows one per 5 min)
FORUM_MAX_CHARS = 800
REPORT_GAP_MIN = 4.5                # report lane: one fact post per forum run at most (the kit allows one per 5 min and
                                    #   keeps a 429'd post for later). 10 -> 4.5 on 2026-11-06: top buyers post 238-265/24h
                                    #   at like-share 0.89-0.90, we posted 119/24h at 0.889; posts are 0.1 of buyer ALL
                                    #   (our pct 0.46). Under 5 so a run kicked a few seconds early still posts.
REPORT_DEDUPE_H = 6                 # never report the same SKU, category or purchase twice within this
REPORT_ORDER = ("purchase", "sku", "category")   # first-hand facts first, then market-wide numbers
REPORT_PAUSE_DISLIKE = 0.5          # pause the lane while this share of counted votes on our posts are dislikes
REPORT_MAX_MANIPULATION = 0.5       # Jev self-check: drop our own draft when P(manipulation) reaches this
DECISION_MAX_PER_HOUR = 200         # decision-model calls per rolling hour (60 until 2026-11-07: every hour of the last
                                    #   24 sat at the 50 ordinary calls while ~480 new posts/h arrived, so ~90% were voted
                                    #   on the 21 s Codex batch label; Decisions answers in 0.37 s median, priced at
                                    #   $0.10 per 1M input tokens per DevDay coverage, ~$0.3/day at this cap)
DECISION_RESERVE = 20               # of those, kept for priority calls (the self-check of our own posts)
INJECTION_P = 0.9                   # downvote only when the decision model is this sure (honest report scored 0.78)

# lever2-forum (MA-3, 2026-11-05): SHADOW ONLY. 11 new constants, used by r3lib/forum_lever2.py. Live code reads only
# FORUM_LEVER2 and LEVER2_DOWNVOTE_P, in forum_r3.vote(), and only when FORUM_LEVER2 is True.
FORUM_LEVER2 = False                # the switch. False = live forum behaviour as before;
                                    # True: forum_r3.vote() sends no -1 on the LLM label alone
LEVER2_POST_GAP_S = 300             # one buyer post per 5 min (12/h, the kit maximum); live gap is FORUM_MIN_GAP_MIN
LEVER2_NEAR_DUP = 0.8               # character 3-gram Jaccard at or above this = near-duplicate, not posted
LEVER2_DOWNVOTE_P = 0.9             # -1 only when the cached P(manipulation) is at least this; no default -1
LEVER2_UPVOTES_PER_RUN = 12         # at most this many +1 per run, on accurate and useful posts only
LEVER2_VOTE_MIN_SETTLED = 5         # a vote counts only when we have this many settled deals (and owners differ)
LEVER2_ROLLBACK_H = 2               # rollback window after going live
LEVER2_ROLLBACK_BASELINE_H = 2      # baseline = our dislikes per post in this many hours before switch-on
LEVER2_ROLLBACK_RATIO = 1.2         # roll back when dislikes per post after switch-on > this x baseline (relative)
LEVER2_ROLLBACK_ON_HIDDEN = True    # roll back on any hidden post
LEVER2_ROLLBACK_ON_429 = True       # roll back on any HTTP 429

# influencer-forum (MA-4 Task 2, 2026-11-05): buyer-role evidence reporter. Default OFF.
# Live forum_r3 reads these only when FORUM_INFLUENCER is True; with False behaviour is unchanged.
# LEVER2_* count stays 11 — these use the INFLUENCER_ prefix.
FORUM_INFLUENCER = False            # the switch. False = live forum behaviour as before
INFLUENCER_GAP_MIN = 10             # buyer post gap under the flag (live FORUM_MIN_GAP_MIN = 20)
INFLUENCER_MIN_CHARS = 150          # draft length floor
INFLUENCER_MAX_CHARS = 500          # draft length soft ceiling (hard is FORUM_MAX_CHARS)
INFLUENCER_ROLLBACK_AVG_DISLIKES = 1.0   # new 20 posts: avg dislikes above this -> rollback
INFLUENCER_ROLLBACK_AVG_LIKES = 5.0      # new 20 posts: avg likes below this -> rollback
INFLUENCER_ROLLBACK_ONE_DISLIKES = 3     # any one post with dislikes >= this -> rollback
INFLUENCER_ROLLBACK_FORUM_FLOOR = 0.540  # board forum below this twice 1h apart -> rollback
REACT_MAX_STEPS = 6
REACT_BUDGET_S = 240                # one ReAct run, all steps together
RESTOCK_REVIEW_EVERY_MIN = 30       # background ReAct review of the restock plan
ADJUST_TTL_H = 2                    # a review's scaling expires unless renewed
ADJUST_MIN, ADJUST_MAX = 0.0, 1.5   # clamp on a category's demand target scaling
PROTECT_SELL_THROUGH = 0.9          # a review may not scale a category below 1 while we sell this share of what we
                                    #   receive, or while buyers are turned away for lack of stock (2026-11-04: the
                                    #   review halved mattresses, kimchi, fruit and bedding on a wrong sell-through)

BUYER_GRADES = ["A", "B", "C"]     # goods keep full value unless they break; max_pay already prices the break risk
BUYER_C_MIN_WARRANTY_H = 6         # grade C goods only with this much warranty (2026-11-09: C broke 35%; a broken deal
                                   #   without warranty is -1 saved, so our 683 such deals averaged -0.104; breaks
                                   #   come within 6 h of the sale); without it the kit pays for A and B only

# --- buyer -----------------------------------------------------------------------------------------
MIN_SURPLUS_RATIO = 0.30            # keep at least this share of list (0.05 until 2026-11-10). Saved is an average
                                    #   (sum of surplus/list over deals + 100), so a deal below our saved (0.2955)
                                    #   lowers it. 72 h replay: 0.30 keeps 64% of deals at mean 0.374 -> end-of-round
                                    #   saved 0.330 (rank ~8) vs 0.312 (rank ~17) keeping all; deals stay top-10
BUYER_THRESHOLD_BAND = 0.20         # the score model may move the threshold this far from MIN_SURPLUS_RATIO (0.24-0.36)
FIRST_OFFER_RATIO = 0.55            # first offer as a share of list price
MAX_OFFERS = 6
SHOPS_TO_TRY = 3
BUYER_FANOUT_SHARE = 0.20          # canary (2026-11-12): this share of orders asks every candidate shop at once and
                                    #   negotiates with the cheapest quotes first (cheapest shop 9.3% of list under the
                                    #   median one on the same SKU and grade; we paid 8.8% over the cheapest)
BUYER_FANOUT_MAX = 8               # shops asked at once (one best candidate each)
BUYER_QUOTE_TIMEOUT_S = 20         # quotes not in by then are ignored and cancelled when they come
HOURS_MARGIN = 0.25                 # food must last use_within_hours + this (2 h refused 8 deals worth paying)


def stock_budget(balance):
    """Cost of stock on hand we allow: STOCK_BUDGET_SHARE of the balance, between MAX_STOCK_COST and STOCK_BUDGET_MAX."""
    return int(min(STOCK_BUDGET_MAX, max(MAX_STOCK_COST, STOCK_BUDGET_SHARE * (balance or 0))))


def hours_left_in_round(now=None):
    import time
    return (ROUND_END - (now or time.time())) / 3600
