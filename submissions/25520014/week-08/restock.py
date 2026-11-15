"""Week 08 restock hook: which units to buy, one unit at a time, by their expected profit per won on model.py's posteriors.

    python restock.py --state .market        # the kit runs it every 60 s in Round 3 and reads the last JSON line

Supply opens for 3 minutes every 20 (agent.md, 11-03; the first window at the round's start stays open 10), and
stock bought in a window has to last until the next one. Outside a window this hook prints an empty list, so the kit
does not wait 90 s per line for a window that is not coming. The kit buys at most 10 lines a run (a line is one SKU,
up to 20 units), and about two runs fit in a window.

One rule decides whether to hold a SKU and how many units (11-03 23 h per category, 11-04 11 h per SKU). The k-th
unit of SKU s, counting the units of it we already hold, is worth
    value_k = P_k x Cu - (1 - P_k) x Co,     P_k = P(we sell at least k of s within T)  (model.nb_tail)
    Cu = expected price - expected cost - warranty premium, of s itself  (expected price: model.price_ratio)
    Co = what an unsold unit costs over T: goods 1% of cost a day; food the share of its usable life T uses up
         (all of the cost when T is longer). Until 11-05 03:45 everything was at cost in the round's last
         liquidate_hours (unsold stock was a total loss); the board now scores book, which keeps unsold units at cost
P_k comes from our own sales rate of s: a gamma over the hours we held it in stock, around the market's sales of s
times our share (model.own_sku_posterior), plus the requests for it we missed times our close rate. Buyers order a
SKU, so each SKU is its own demand; until 11-04 11 h a category had one count of units priced at its best seller,
and a second SKU got only what the first left (quilt G5cd4ffbc, 34k a unit, none behind a 15k sheet).
T is the refill cycle: how long until this SKU gets a line again. With H SKUs to keep and L lines a window, a SKU is
refilled about every ceil(H / L) windows, so T = ceil(H / L) x the time between windows; L and that time are
measured from our own runs (data/restock.jsonl). H depends on what the money buys, so T is found by repeating the
allocation a few times.

Every unit with a positive value is ranked by value / cost (the profit a won tied up in it earns per cycle: a unit
that would sell late is worth less, so turnover counts), and units are taken from the top while the money lasts: the
cash above the reserve (the bankruptcy line plus the warranty refunds we may still owe, model.refund_exposure), each
unit counted at the top of its cost range, no unit above unit_cap_frac of the balance. Each SKU takes at most its
demand to us over the refill cycle (sku_demand x the last plan's cycle within CAP_HOURS), and a SKU nobody bought
gets none unless a buyer asked us for it (11-04: buyers settle on few SKUs, 255 of 415 fresh fruit sales one apple
box). The lines worth most go first; this
run buys the best of those within the kit's 10 lines. A category a forum post asked for in the last two hours
(wanted.py) gets one unit when we hold none and a line is left. Categories the manager's playbook skips (at most
24 h each) are not bought; their stock stays on sale. Bankruptcy only when the balance is under the line and nothing
is left to sell (the kit stops filing by itself once a restock hook exists).
"""
import argparse
import json
import math
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

import model
import policy
import wanted

MARKET = "https://market.ourolabs.ai"
STATE = policy.DATA / "restock_state.json"   # the plan (target per category, cycle), for the manager
LOG = policy.DATA / "restock.jsonl"
COST_SHARE = {"food": 0.50, "goods": 0.59}     # expected cost / list price over the grade mix (agent.md)
WORST_COST = 0.75                               # grade A at the top of its range: used for the caps
FOOD_LIFE = 0.20                                # expected share of shelf life a fresh supply has before grade C
MAX_LINES = 10                                  # the kit's lines per run
MAX_QTY = 20                                    # the kit's units per line
RUN_GAP = 300                                   # runs further apart than this are in different windows
OLD_C_H = 3                                     # a goods unit of grade C held this long is not counted as stock
# 3 h from 16:31, 0 from 19:22 (with A or B held the seller never offered C, and under the 1 h cover a fresh C took
# a place of A or B), 3 again from 11-04 20:27: 72% of goods supply is A and the seller sells A first, so B and C
# pile up behind it; uncounted, every C was bought over again in A and B (자코모 4인 G8ec51935 at 20:11: 13 A, 36 B,
# 32 C, 92M at cost; stock 175M -> 192M from 19:04). Offline, 6 h with grades, paid shares and the C fallback:
# counting C again adds 29-50M to the board in each of 4 cases (A-only buyers 20/40%, C paid 55/35%)
WINDOW_MIN, RUNS_PER_WINDOW = 20, 2             # until three windows are measured (agent.md; 11-03 logs)
WANTED_H = 2                                    # wanted posts this recent are worth one unit (wanted.py)
WINDOW_LEAD = 45                                # print the plan when a window opens within this many seconds
CAP_HOURS = (1.0, 2.0)                          # a SKU's units: its demand to us over the last plan's refill cycle,
                                                # within these hours (owner, 11-04 07:20; before, 1 h of our sales)
# A SKU this fast to us gets a line in every window (about 20 min apart, 16-24 min over 24 h on 11-04), so its stock
# only has to last FAST_COVER_H, not the refill cycle of all SKUs (about 2 h): its cap and its P_k horizon. 11-04 19 h:
# sofa G8ec51935 was bought in each of 24 runs and held 69 units (94M at cost) for 2 h; offline over 6 h a 1 h cover
# held half the stock and sold more of the high-end SKUs we never stocked (gross +5%), while 0.5 h ran out more
# than it gained (owner, 11-04 19:09).
FAST_RATE = 3.0                                 # demand to us per hour (sku_demand); FAST_BUFFER uses it
FAST_COVER_H = 0.34                             # one supply window (20 min, 16-24 min measured)
CAP_FLOOR = 3                                   # units a SKU may hold below the fast cover (owner, 11-05 01:00).
# 11-05 14 h (owner): a goods SKU of FAST_RATE+ an hour holds FAST_BUFFER units past its cap. A SKU is shown to buyers
# only while we hold a unit (56 depth.py, last 5 h: 0 units 2.3% of its market sales, 1 unit 15.2%, 2-7 13-21%), and
# 자코모 4인 alone lost ~26M in 5 h while out. 56's sim, 6 h, 8 seeds (book change / revenue / end stock): now +98.1M /
# 582M / 69.5M; +2 on fast goods +105.8M / 636M / 76.9M; +1 fast +100.7M; +1 all goods +103.0M / 97.2M stock; +2 all
# +102.5M / 127.8M; FAST_COVER_H 0.75 +102.9M / 633M / 83.1M. The sim sells at the market median, not our floor.
FAST_BUFFER = 2
# With FAST_RATE as a cliff, a SKU just under 3/h held 2 h of demand (6 units) and one just over 0.34 h (2): the
# demand fix moved 시몬스 퀸 3.61 -> 2.65/h and its cap 2 -> 6, the next buy 5.8M -> 11.0M. Offline 6 h (3 seeds,
# true demand = new model): cliff + old model gross 68.2M, board +142.8M, stock 56.6M; cliff + new model 65.6M,
# +137.7M, 59.0M; continuous floor 2: 69.8M, +165.7M, 35.2M; floor 3: 74.7M, +161.2M, 44.6M, fewest stockouts
# (true demand = old model: floor 3 81.9M / +171.3M against the cliff's 77.3M / +157.3M). FAST_RATE: FAST_BUFFER only.
# 1.0 from 19:09, 0.34 from 11-04 21:25 (owner: recover cash first). A deep stock sells A and leaves B and C: dae-yangkim
# holds 9 goods units (4M, 0.1 h of its sales) and sells grades as supplied (71/20/9%); we held 314 (186M, 3 h) and
# sold 80/14/6%. Offline 6 h with grades, paid shares and the B/C fallbacks: board +144-154M (1 h) -> +220-250M,
# stock 129-131M -> 34-44M, gross 110-123M -> 101-121M. A fast SKU out for the minutes a late window adds is the price.
SHORTAGE_ENTRY = True                           # model.shortage(): enter without the unproven wait, demand floor
SHORT_W_H = model.SHORT_W_H                     # sku_demand: an equal share of the last hour among its suppliers + us
SHORT_COVER_H = 1.0                             # a short SKU's cap: this many hours of its demand to us
# The caps above (CAP_FLOOR, FAST_COVER_H, CAP_HOURS, SHORT_COVER_H) were set while an unsold unit was a loss: cash
# scored, everything at cost in the last liquidate_hours. Since 11-05 03:45 the board scores book (cash + unsold
# units at cost), so a goods unit that does not sell by its next refill costs storage, 1% of its cost a day
# (ranked's Co), and the newsvendor alone sets the units: P_k x Cu > (1 - P_k) x Co, up to a line (MAX_QTY). Cash
# still binds through allocate (value per won) once it runs short; lines go to the SKUs whose units are worth most.
# Off, not deployed (owner, 11-05 07:40): with Co one cycle's storage (about 0.08% of cost) a unit is bought once
# P_k passes about 0.3%, so every line goes to MAX_QTY whatever the demand, and the high predictions it leans on are
# the least reliable (4c backtest 00-07 h: predicted 4-8 units, 5.5 predicted against 3.95 sold). Offline 6 h it
# added 21M of book but held 347M of stock (56M) and 323 B/C units (64). A SKU that sells well refills itself from
# its sales record; only a short SKU's demand is hidden by its supply, so only that cap moves (SHORT_OWN).
BOOK_NEWSVENDOR = False
# A short goods SKU's demand is our own sales of it over the hours we held it in the last hour (owner, 11-05 07:50):
# while we hold units the buyers who want it are not cut off, only once we run out. The market's sales split among
# its suppliers (the floor) and the gamma's prior (category share x market rate) both come from sales the supply cut
# short, so they read it low (our 77 short units sold 92%, median 13 min). With fewer than SHORT_OWN_MIN of our sales
# in the hour the floor stays. Its cap is that rate x the time to the next refill (one window, cadence). Short goods
# stock at cost is held under SHORT_STOCK_MAX.
SHORT_OWN = False                               # off (owner, 11-05 08h): 56 replay of 33 short SKU-hours, 1 h of our rate +0.5M (a third of it over 10 h) for 30M more stock; one window -2.3M
SHORT_OWN_MIN = 3
SHORT_STOCK_MAX = 40e6
# An A unit whose floor is over the market's A price for its SKU (underwater, as clearance.terms) does not count
# toward the SKU's units (owner, 11-06 11:55). 다우닝 플라나 4인 G24f5f979: an A at 0.814 of list (09:21, top of the
# A range with surge) had a floor of 0.906 against the market's A 0.766; from 09:46 every A talk was offered it,
# our deals fell from 11 to 1 per 30 min while the market kept selling 20-30, and with 1 B and 3 C it filled the
# cap of 5, so nothing more was bought (and the in-stock rate, then the cap, fell with the sales). 23 of 71 goods
# SKUs held such an A (34 units, 27.9M). clearance.UW_FLOOR_COST lets it go just under the market meanwhile.
# Off 11-10 (owner): with seller.MKT_PRICING a dear A sells at the market's price like any other, so it is stock like
# any other. Uncounted, 327 such A (291M of 755M at cost, 11-10 20 h) sat outside the caps while their SKUs were bought again.
UW_A_UNCOUNTED = False
# 11-10 (owner): the goods stock follows our sales, not our cash. restock spent everything above the reserve in every
# window, so the stock grew with the book: 11-10 20 h cash 20M, stock 756M (97% of book, 9.3 h of sales, 56% of it
# held 6 h+, storage 7.5M a day); yms979 34%, SungJinho 22%, Kiro729 15%, seochanit 2%. Past STOCK_HOURS of our goods
# sales (the last STOCK_RATE_H hours, at the prices paid) a window buys at most STOCK_REFILL of what we sold since
# the last one, so the stock runs down without the shelf going empty; under it, as before.
STOCK_TARGET = True
STOCK_HOURS = 5.0
STOCK_RATE_H = 6
STOCK_REFILL = 0.7


# 11-11 (owner): seller.MKT_HOLD_GUARD keeps an A whose floor (what holding it is worth plus the warranty refund) is
# over the market's price for its SKU; such a unit does not sell, so it is not stock that follows our sales. Counted,
# the stock would never come down to the target and every window would buy STOCK_REFILL of the last one's sales (the
# 11-07 loop the other way round). The per-SKU counts (candidates) still count it: UW_A_UNCOUNTED stays off.
STOCK_SKIP_KEPT = True
# 11-12 (owner): the target is never under STOCK_MIN_SHARE of what we could hold (cash + the counted goods stock).
# The target was made of our own sales alone, and our sales are held down by what is on the shelf: the quote stall of
# 11-11 11-16 h cut them 38 -> 10M an hour, the target followed (190 -> 43M), every window bought 0.7 of the little
# it sold, and it did not come back when the stall was fixed. To 11-12 10 h: goods bought 86-102M -> 20-36M per 6 h,
# cash 20 -> 455M (57% of book), book flat at 788-792M. Not SKUs missing (93-97% of the market's goods volume was in
# SKUs we held) but units that can sell: the share of the market's A volume for which we held an A costing no more
# than the price paid fell 89% -> 45% (7.4 -> 2.2 such units per settle) while our share inside it was back at
# 11-13%; for B the SKUs held fell 48% -> 19%. What was bought sold: 59-75% of a 6 h cohort's cost within 6 h, net
# +9-10% of the cost sold (~/wk8-test/verify1112.py, verify2.py). 0.65 is where 11-11 06-10 h stood (counted stock
# 510-566M, cash 227-285M, net +2.4M an hour, 76% of the A volume); it is a level, not a replayed optimum.
# 11-13 19 h (owner: the stock held sells or stays, the cash is played anew): 0.65 -> 0.85. The room under the target
# fell 230M (12 h) -> 98M (18:23), about 20M an hour, and would be 0 near midnight with 320M of cash idle; past the
# target a run buys only STOCK_REFILL of what sold. Same state at 0.85: 231M (~/wk8-test/bseek_dry.py). With B_SEEK.
STOCK_MIN_SHARE = 0.85


def kept_a(inv, est, now):
    """The ids of the goods A units seller.MKT_HOLD_GUARD keeps: floor over the market's A price for the SKU."""
    import clearance
    try:
        mkt = model.market_grade_prices(getattr(est, "db", None), now, est.cat or None)
    except Exception as e:
        print(f"[restock] market grade prices unavailable: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return set()
    share = clearance.hold_share(now) / (1 - est.break_rate.get("A", 0.0))     # as seller.hold_floor
    out = set()
    for u in inv.get("units", []):
        m = (mkt.get(u.get("sku")) or {}).get("A")
        if (u.get("kind", "goods") == "goods" and u.get("grade") == "A" and m and u.get("list_price")
                and (u.get("cost") or 0) * share > m * u["list_price"]):
            out.add(u.get("unit"))
    return out


def market_rates(est, now):
    """{goods sku: other shops' settles of it per hour over MARKET_RATE_H} (MARKET_DEMAND)."""
    db = getattr(est, "db", None)
    cat = est.cat or {}
    ours = model.our_urls(db)
    n = Counter()
    for ts, d in model.entries(db, "settle", now - MARKET_RATE_H * 3600):
        s = model.sku_of(d)
        if d.get("shop") not in ours and (cat.get(s) or {}).get("kind", "goods") == "goods":
            n[s] += 1
    return {s: k / MARKET_RATE_H for s, k in n.items()}


def stock_room(inv, est, now, every_min, runs=1.0):
    """(the most this run may spend under STOCK_TARGET or None, goods stock at cost, our goods sales per hour).
    runs: restock runs in a window (the window's allowance is split among them)."""
    db = getattr(est, "db", None)
    cat = est.cat or {}
    ours = model.our_urls(db)
    sold = 0.0
    for ts, d in model.entries(db, "settle", now - STOCK_RATE_H * 3600):
        if d.get("shop") in ours and (cat.get(model.sku_of(d)) or {}).get("kind", "goods") == "goods":
            sold += model.amount_of(d) or 0
    rate = sold / STOCK_RATE_H
    kept = kept_a(inv, est, now) if STOCK_SKIP_KEPT else set()
    stock = sum(u.get("cost") or 0 for u in inv.get("units", [])
                if u.get("kind", "goods") == "goods" and u.get("grade") != "expired" and u.get("unit") not in kept)
    sold_w = rate * every_min / 60 / runs               # what a run's share of a window sells
    target = STOCK_HOURS * rate
    if STOCK_MIN_SHARE:
        target = max(target, STOCK_MIN_SHARE * ((inv.get("balance") or 0) + stock))
    if stock <= target:
        # 11-10 22 h: under the target this returned None (all the cash above the reserve again), so the stock
        # would have jumped back past the target in one window once it got there; now the gap to the target plus
        # what the window sells
        return target - stock + sold_w, stock, rate
    return STOCK_REFILL * sold_w, stock, rate
# A goods SKU's demand is at least our sales of it per hour we held a sellable A (an A whose floor is under the market's
# A price) over the last A_RATE_H hours (owner, 11-06 13 h). The in-stock hours of own_sku_rate count any grade, so a
# SKU whose buyers take only A read low while we held only B, C or a dear A: 12 h to 11-06 13 h, 프리미엄 앤 모달
# 이불패드 4.51 an hour held against 9.55 an hour with an A, 파르페 호텔 침구 6.65 / 12.30, 지누스 클라우드 4.61 / 6.41
# (over all goods SKUs B and C sold as fast, 1.90 against 1.58, so most SKUs are unchanged). Needs A_RATE_MIN_H held
# with an A and A_RATE_MIN_N of our settles in that time.
A_RATE, A_RATE_H, A_RATE_MIN_H, A_RATE_MIN_N = True, 12, 0.5, 3
A_RATES = {}
# Goods caps x GOODS_CAP_X (owner, 11-06 13 h). The caps were set while unsold stock cost its whole value (cash
# scored); under book an unsold unit costs 1% a day. 56's sim (~/wk8-test/nv_sweep3.py, 6 h, 8 seeds, true demand
# with the A-held rates): now 706M revenue / book +111.6M / end stock 98M; A_RATE 753M / +115.8M / 109M; A_RATE and
# x1.5 818M / +116.0M / 189M; A_RATE and x2 852M / +118.2M / 246M. With today's estimate as the truth x2 gave
# +61M revenue at the same book. 1.5 for now (owner).
# 11-13 13 h (owner): 3.0. Since 11-11 16:10 a goods talk makes +0.9k when we hold only A of the SKU (2,911 talks a
# day), +17.8k with A and B, +35.6k with only B (~/wk8-test/bavail.py): the profit is B and C (B +23.4M a day, A +0.7M)
# and the grade is drawn at supply (60/24/16), so more B means more units. At 1.5 a window bought 7 goods units
# (1.1M) with 457M of cash idle; dry run on the same state (~/wk8-test/cap_dry.py): x2 15 units 7.3M, x3 41 units
# 29.1M, x5 94 units 61.7M. What the extra units sell for is not replayed: judge by B and C sales and the A stock.
GOODS_CAP_X = 3.0
# 11-13 19 h (owner): a dear goods SKU with no B on the shelf is bought B_SEEK_QTY more units a run whatever A and C
# it holds, while the cash is over B_SEEK_CASH. The profit of these SKUs is their B (list 2M+, since 11-11 16:10:
# +297k a SKU-hour with a B in stock against +33k with A only, A sales the same) and they had no B 51% of the time
# they had an A: the A held fills the cap and the k-th unit looks unsold. A B is 1 in 4.2 units; backtest
# (~/wk8-test/bless.py): the gap is 1.9-2.4M an hour, the A and C that come with the B and stay are about 20M an
# hour, so about 10% on the cash they take; unsold they cost 1% a day to the end. Market purchases of such a SKU in
# an hour: median 5-7, 95% 21-31 (the surge starts past 50).
B_SEEK = True
B_SEEK_LIST = 2_000_000                         # list price from which a SKU is dear enough
B_SEEK_RATE = 1.0                               # its demand to us per hour (sku_demand); the SKUs under it had 0-1 B arrive
                                                # (~/wk8-test/barrive.py), over it every B of 40 sold (median 0.5-1.5 h)
B_SEEK_QTY = 3                                  # units a run: a B in 56% of the runs
B_SEEK_CASH = 100_000_000                       # not under this much cash
# 11-15 02:42 (owner: "A 시세는 보고 사야지"): B_SEEK no longer skips the A price check. The A that came with the B were
# bought at 0.72-0.745 of list (surge) against a market A of 0.65-0.70: at 11-15 02:30, 384 of 1,098 A (302M at
# cost) had a floor over the market, most of them B_SEEK SKUs (Gbec2b68f, G24f5f979, G4a12722c, G8ec51935,
# G47788edd, G6cd0e9d4), and the A sales of those SKUs stopped once the cheaper A of them were sold (플라나 A 41 sold
# at 0.640 11-14 06-14 h, the 10 left cost 0.725+). Off: the 11-13 rule.
B_SEEK_A_CHECK = True
# 11-16 00 h (owner): not while the SKU's A held pass B_SEEK_A_H hours of our A sales of it (at least B_SEEK_A_MIN).
# With CASH_RETURN the cash went 90 -> 131M, over B_SEEK_CASH again, and B_SEEK bought Gbec2b68f 3 a run without
# a B ever arriving: 17 units 13:42-17:02, 47 A held at midnight against 6 of our sales of it in 6 h (~130M at
# cost). The 420M of dear A of 11-15 came from the same rule. Off: B_SEEK_A_H = None.
B_SEEK_A_H, B_SEEK_A_MIN = 6, 3


def own_a_sales(est, now):
    """{goods sku: our settles of grade A over the last B_SEEK_A_H hours} (B_SEEK_A_H)."""
    db = getattr(est, "db", None)
    cat = est.cat or {}
    ours = model.our_urls(db)
    n = Counter()
    for ts, d in model.entries(db, "settle", now - B_SEEK_A_H * 3600):
        s = model.sku_of(d)
        if d.get("shop") in ours and d.get("grade") == "A" and (cat.get(s) or {}).get("kind", "goods") == "goods":
            n[s] += 1
    return n
# 11-15 (owner): a goods SKU's demand is at least MARKET_SHARE of the market's settles of it per hour (other shops,
# last MARKET_RATE_H hours), and the A that seller.MKT_HOLD_GUARD keeps (kept_a: floor over the market's A price) are
# not counted against its cap up to one cap of them (KEPT_FREE caps). Our demand was made of our own sales, which stop
# when what we hold is dear A, and those A filled the cap: G021aae64 held 14 such A (cost 0.725-0.771 of list),
# read 0.48 an hour against 15+ A an hour in the market, last bought 11-14 15:24; G03d0199f 13:03. sergiobuilds bought
# both every window at 0.46-0.66 and sold 185 + 82 A in 12 h (11-15 10 h, ~/wk8-test/why_skip.py, sergio2.py); our
# goods sales 40-80M -> 12-19M an hour with the market flat. The KEPT_FREE bound is the 11-07 loop (UW_A_UNCOUNTED:
# every A bought over the market left the count and the SKU was bought again). Off: MARKET_DEMAND = False.
MARKET_DEMAND = True
MARKET_SHARE = 0.08
MARKET_RATE_H = 3
KEPT_FREE = 1.0
# 11-14 00 h (owner): a food SKU that fair-furniture asked us for and we had none of FAIR_FOOD_ASKS times in
# FAIR_FOOD_H hours is kept at FAIR_FOOD_MIN units, whatever its units are worth. It goes back to shops where its
# talks close, and "none in stock" is a result without a deal like a refused price (3 of its 6 results here since
# 11-13 20:45, at the minutes of our no-stock answers); it asks us for food about 45 times a day, 128 in 72 h we had
# none of, 39 of them one SKU (G1c373db9, which it buys 56 of a day in the market). The asked-recently rule buys one
# unit after the miss. Food not sold is lost at its cost: a few SKUs x 4 units x 10-30k. Off: FAIR_FOOD_MIN = 0.
FAIR_TEXT = "Please quote a price in credits"   # seller.FAIR_BUYER
FAIR_FOOD_MIN, FAIR_FOOD_ASKS, FAIR_FOOD_H = 4, 3, 24
GRADE_CAP = False                               # a goods SKU's B and C count only up to their share of its cap
GRADE_SHARE_H = 6                               # our goods settles this recent give the grade shares
# 11-04 21:15, offline only, not deployed (off): the seller sells the best grade first, so the cap filled with B and C left no room for A: 자코모 4인
# G8ec51935 held 13 A, 36 B, 32 C against a cap of 49 and its A sales fell 55 -> 24 -> 9 an hour (17-21 h), gross
# 35M -> 20-25M an hour. Buyers of a SKU take B and C about as often as our goods settles do (6 h: A 81%, B 13%, C 5%),
# so a B or C unit beyond that share of the cap is not stock an A buyer can use. The B and C fallbacks sell them.
# Rejected offline (6 h, 3 seeds, live restock.decide with grades): A of 자코모 4인 34 -> 38 an hour, but every
# line bought for A brings 28% B and C, so stock 124-140M -> 222-224M and board +158M -> +62M.
# Surge (agent.md, 11-04 21:27): a SKU's units past SURGE_FREE in the market's last hour each add SURGE_STEP to its
# cost, up to SURGE_MAX. inventory.json `surge` is the factor now, but we buy later in the window and others buy
# before us; the units bought an hour earlier leave the count meanwhile (windows are 20 min apart, so the window three
# back leaves while this one is open). Forecast U at our purchase (surge_counts) = units still in the hour then +
# what other shops bought in the previous window over the same seconds; the j-th new unit pays factor(U + j).
# Backtest on the Round 3 ledger (1,081 SKU-windows, a purchase 83 s into the window, owner 11-04 23:30):
# factor now MAE 2.26%p, bias +1.65%p (too low); this forecast MAE 1.28%p, bias -0.08%p.
# Market check (owner, 11-04 23:50): a goods unit sells only above its floor (seller.floor_price: cost x (1 + min_margin)
# + warranty break premium), so a SKU whose A sells in the market under the floor of an A at our mean A cost is not
# bought, and its price is at most the market's grade prices over the supply mix. 22-23 h 자코모 4인 A sold at
# 0.80-0.81 of list in the market, our A floor 0.86-0.93 (units bought at 0.75-0.81 with the surge): 92 talks, 4
# deals, yet bought again at 23:05 and 23:22, priced at our own category price (0.82).
MARKET_CHECK = True
# 11-05 10 h (owner): the check compares an A's cost and break cost with the market's A median, without min_margin.
# With the margin an average A sat on the market's median (자코모 4인 G8ec51935: 0.66 x surge 1.048 x 1.08 + break
# 0.05 = 0.797 against 0.797), so a SKU flipped out of restock on small moves although it was among our best (12 h:
# 195 deals, A 0.811 of list at cost 0.688, B and C more; 235M sold in the market in 6 h while we held none). The
# median is what half the buyers pay over, and B and C come with every A line. Backtest over the last 12 h, hour by
# hour (~/wk8-test/acheck.py): SKU-hours blocked only by the margin 94, our deals in them 180 at 0.147 of list
# profit (A 0.094, B 0.191, C 0.281; 7.8% under cost against 2.4% where it passed); blocked either way 14. cu and
# the value-per-won ranking still decide the units.
A_CHECK_MARGIN = False
# 11-05 14 h (owner): the check ran on the next unit's surge only, so a line of 20 could buy its last units above the
# market A median (each unit +0.2% past 50 in the hour). Now every unit up to the last one bought must pass; the line
# is cut at the first that does not. Without min_margin (56: 147 of 235 goods units with a floor over the market sold
# in their first hour at the floor; unsold ones stay in book at cost).
A_CHECK_TOP = True
A_COST = 0.66                                   # mean cost / list of a goods A unit (828 supplied before 21:27: 0.55-0.75)
SUPPLY_MIX = {"A": 0.60, "B": 0.24, "C": 0.16}  # goods grades as supplied to us (1,378 units before 21:27)
GRADE_REL = {"A": 1.0, "B": 0.87, "C": 0.75}    # a grade the market has too few sales of: this x its A price
WARRANTY_ASKED = {"B": 1.0, "C": 0.08}   # B: always from 11-06 13 h (seller.WARRANTY_ON_ASK); was 0.13         # share of our first messages asking for a warranty (11-04, 24 h)
SURGE_FREE, SURGE_STEP, SURGE_MAX = 50, 0.002, 1.5
SURGE_WINDOW = 3600
SUPPLY_OPEN_S = 180                             # a window stays open 3 minutes
BUY_OFFSET = 83                                 # our supply lands this many seconds into a window (median, 11-04)
SURGE_STALE = 120                               # archive older than this: fall back to inventory.json `surge`


def window_shut(inv, now):
    """True when supply is closed and will not open before the kit's next run (inventory.json `restock`)."""
    r = inv.get("restock") or {}
    if not r or r.get("open", True):
        return False
    at = r.get("opens_at") or 0
    if at > 1e12:                                # milliseconds
        at /= 1000
    return at - now > WINDOW_LEAD


def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def competitors(catalog):
    """category -> how many other shops hold stock in it now, from GET /api/shops."""
    try:
        shops = httpx.get(f"{MARKET}/api/shops", timeout=15).json()["shops"]
    except Exception:
        return {}
    cat = {k: v["category"] for k, v in catalog.items()}
    out = {}
    for s in shops:
        if s.get("name") == model.SHOP_NAME:
            continue
        for c in {cat.get(k) for k, n in (s.get("stock") or {}).items() if n}:
            if c:
                out[c] = out.get(c, 0) + 1
    return out


def cadence(now, path=LOG):
    """(lines per window, minutes between windows), measured from our runs of the last 3 hours: runs closer than
    RUN_GAP are one window. Defaults until three full windows are in the log."""
    ts = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                t = json.loads(line).get("ts", 0)
                if t > now - 3 * 3600:
                    ts.append(t)
    except (OSError, ValueError):
        pass
    groups = []
    for t in sorted(ts):
        if groups and t - groups[-1][-1] <= RUN_GAP:
            groups[-1].append(t)
        else:
            groups.append([t])
    full = groups[:-1]                           # the newest window may still be running
    if len(full) < 3:
        return MAX_LINES * RUNS_PER_WINDOW, WINDOW_MIN
    runs = statistics.median(len(g) for g in full[-6:])
    gap = statistics.median(b[0] - a[0] for a, b in zip(full[-7:], full[-6:])) / 60
    return MAX_LINES * max(1, round(runs)), min(max(gap, 10), 60)


def asked(units, by_cat, median, skip, p, balance, cat, demand, now):
    """One unit of each category a wanted post asked for in the last WANTED_H hours that we hold none of: the SKU
    the post named, else the one nearest the median price. After the ranked units, when a line is left."""
    try:
        posts = wanted.recent(model.connect(), cat, demand, now - WANTED_H * 3600)
    except Exception:
        return []
    stocked = {u.get("category") for u in units}
    out, seen = [], set()
    for w in posts:
        c = w["category"]
        if c in stocked or c in skip or c in seen or c not in by_cat:
            continue
        seen.add(c)
        skus = [s for s in by_cat[c] if WORST_COST * s[1]["list_price"] <= p["unit_cap_frac"] * balance]
        named = [s for s in skus if s[0] in w["skus"]]
        if not skus:
            continue
        m = median.get(c) or sorted(x["list_price"] for _, x in skus)[len(skus) // 2]
        sku, x = named[0] if named else min(skus, key=lambda s: abs(math.log(s[1]["list_price"] / m)))
        out.append({"sku": sku, "qty": 1, "kind": x["kind"], "category": c, "asked": w["id"],
                    "worst_cost": WORST_COST * x["list_price"]})
    return out


def sku_demand(est, sku):
    """Units of one SKU an hour that buyers would pay us for: our paid sales of it (model.own_sku_rate) plus the
    requests for it that found nothing at our shop (data/missed.jsonl) times our close rate. Paid sales alone stop
    when the SKU runs out, so a cap built on them stayed low: 300 of 311 requests we missed in 3 h (11-04 04-07 h)
    were SKUs we stock, mattress G1515d951 54 of them."""
    asked = est.asked_sku.get(sku, 0.0) / est.watched if est.watched > 0 else 0.0
    rate = est.own_sku_rate(sku) + asked * est.p_close
    short = est.shortage().get(sku) if SHORTAGE_ENTRY else None
    own = short_own_rate(est, sku) if short and est.cat.get(sku, {}).get("kind") == "goods" else None
    if own is not None:
        return own                              # our in-stock rate, not the floor or the prior (SHORT_OWN)
    if short:
        # sells as fast as it arrives (model.shortage): an equal share of the last hour's sales among the shops
        # that supplied it and us, at least. A SKU we never held reads only its prior (category share x market
        # rate over 24 h), 라클라우드 퀸 0.1 x 15 an hour while it sold 70 (11-04 20 h)
        rate = max(rate, short["sold"] / (short["shops"] + 1) / SHORT_W_H)
    return max(rate, A_RATES.get(sku, 0.0))     # A_RATE: our sales of it per hour a sellable A was held


def short_own_rate(est, sku):
    """Our settles of a short SKU in the last hour per hour we held it then (SHORT_OWN), or None with fewer than
    SHORT_OWN_MIN of them (or under 3 minutes held)."""
    if not SHORT_OWN or est.n1_ours.get(sku, 0) < SHORT_OWN_MIN:
        return None
    if est._spans is None:
        est._spans = est._stock_spans()
    held = sum(max(0.0, min(y, est.now) - max(x, est.now - 3600)) for x, y in est._spans.get(sku, [])) / 3600
    return est.n1_ours[sku] / held if held >= 0.05 else None


def cap_hours():
    """The refill cycle of the last plan in hours, within CAP_HOURS: stock bought now has to last until the
    category gets a line again (98 min on 11-04 07 h, when a 1 h cap ran the best sellers out between refills)."""
    cyc = read(STATE, {}).get("cycle_minutes")
    return min(max((cyc or 60) / 60, CAP_HOURS[0]), CAP_HOURS[1])


def old_c(u, cat, now):
    """A goods unit of grade C held OLD_C_H hours or more (food's C is its date, and food is not kept that long)."""
    return (u.get("grade") == "C" and cat.get(u.get("sku"), {}).get("kind", u.get("kind")) != "food"
            and now - (u.get("supplied_at") or now) >= OLD_C_H * 3600)


def underwater_a(u, mkt, premium_a, margin):
    """A goods unit of grade A whose own floor (cost x (1 + min_margin) + the A break premium, seller.floor_price)
    is over what the market pays for an A of its SKU (UW_A_UNCOUNTED)."""
    m = (mkt.get(u.get("sku")) or {}).get("A")
    if u.get("grade") != "A" or not m or not u.get("list_price"):
        return False
    return u["cost"] * (1 + margin) + premium_a * m * u["list_price"] > m * u["list_price"]


def a_rates(est, cat, mkt, premium_a, margin, now):
    """{goods sku: our settles per hour while we held a sellable A} over A_RATE_H (A_RATE). Unit spans from the
    ledger (supply -> settle or expiry); a unit's grade and cost from the inventory snapshots, our talks
    (negotiations.jsonl) or inventory.json; sellable = not underwater_a at today's market A prices."""
    t0 = now - A_RATE_H * 3600
    since = t0 - 86400
    goods = lambda s: (cat.get(s) or {}).get("kind") == "goods"
    info = {}
    for _, raw in est.db.execute("SELECT ts, raw FROM inventory WHERE ts >= ?", (since,)):
        for u in (json.loads(raw).get("units", []) if raw else []):
            info[u.get("unit")] = u
    neg = policy.DATA / "negotiations.jsonl"
    if neg.exists():
        with neg.open(encoding="utf-8") as f:
            f.seek(max(0, neg.stat().st_size - 8_000_000))     # the last ~16 h of talks
            f.readline()
            for line in f:
                if '"cost"' not in line or '"unit"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("unit") and d.get("grade") and d.get("cost") and d.get("sku"):
                    info.setdefault(d["unit"], {"sku": d["sku"], "grade": d["grade"], "cost": d["cost"],
                                                "list_price": (cat.get(d["sku"]) or {}).get("list_price")})
    spans = {}
    for ts, d in model.entries(est.db, "supply", since):
        if d.get("shop") == model.SHOP_NAME and goods(d.get("sku")):
            for u in d.get("units") or []:
                spans[u] = [d["sku"], ts, now]
    for ts, d in model.entries(est.db, "settle", since):
        x = spans.get(d.get("unit"))
        if x and d.get("shop") in est.mine:
            x[2] = min(x[2], ts)
    for ts, d in model.entries(est.db, "expire", since):
        for u in d.get("units") or []:
            if u in spans:
                spans[u][2] = min(spans[u][2], ts)
    held = {}
    for u, (s, a, b) in spans.items():
        x = info.get(u)
        if not x or x.get("grade") != "A" or underwater_a(dict(x, sku=s, list_price=x.get("list_price") or cat[s]["list_price"]),
                                                         mkt, premium_a, margin):
            continue
        a, b = max(a, t0), min(b, now)
        if b > a:
            held.setdefault(s, []).append((a, b))
    out = {}
    for s, iv in held.items():
        merged = []
        for a, b in sorted(iv):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        held[s] = merged
    n = Counter()
    for ts, d in model.entries(est.db, "settle", t0):
        s = d.get("sku")
        if d.get("shop") in est.mine and s in held and any(a <= ts - 1 < b for a, b in held[s]):
            n[s] += 1
    for s, iv in held.items():
        h = sum(b - a for a, b in iv) / 3600
        if h >= A_RATE_MIN_H and n[s] >= A_RATE_MIN_N:
            out[s] = n[s] / h
    return out


def grade_shares(est, cat, now):
    """{grade: its share of our goods settles over GRADE_SHARE_H}, B and C at least 0.02 (a SKU's cap keeps one)."""
    c = Counter()
    for ts, d in model.entries(est.db, "settle", now - GRADE_SHARE_H * 3600):
        if d.get("shop") in est.mine and cat.get(d.get("sku"), {}).get("kind") == "goods":
            c[d.get("grade")] += 1
    n = sum(c[g] for g in "ABC")
    return {g: max(c[g] / n if n else 0.0, 0.02) for g in "BC"}


def surge_factor(u):
    """The cost factor of a unit that is the u-th of its SKU in the market's last hour."""
    return min(SURGE_MAX, 1 + SURGE_STEP * max(0, u - SURGE_FREE))


def window_open_at(inv, now):
    """When the current supply window opened, or the next one opens (inventory.json `restock`)."""
    r = inv.get("restock") or {}
    at, close = r.get("opens_at"), r.get("closes_at")
    at = at / 1000 if at and at > 1e12 else at
    close = close / 1000 if close and close > 1e12 else close
    if close and close - SUPPLY_OPEN_S <= now < close:
        return close - SUPPLY_OPEN_S
    return at or now


def surge_counts(inv, now, db=None):
    """sku -> the market's units of it in the last hour at our purchase, forecast: the units still in the hour then
    (the ledger up to now, less those older than an hour by then) + what other shops bought in the previous window
    over the same seconds of it. Falls back to inventory.json `surge` when the archive is stale or unreadable."""
    fallback = {x["sku"]: x.get("units") or 0 for x in (inv.get("surge") or []) if x.get("sku")}
    own = db is None
    try:
        db = db or model.connect()
        last = db.execute("SELECT MAX(ts) FROM ledger").fetchone()[0] or 0
        if now - last > SURGE_STALE:
            return fallback
        t_open = window_open_at(inv, now)
        t_buy = max(t_open + BUY_OFFSET, now + 10)
        off = t_buy - t_open
        rows = [(ts, d) for ts, d in model.entries(db, "supply", min(t_buy - SURGE_WINDOW, t_open - 1800))]
        u = Counter()
        for ts, d in rows:
            if t_buy - SURGE_WINDOW <= ts <= now:
                u[d.get("sku")] += d.get("qty") or 1
        starts, prev_ts = [], None                       # windows before this one: runs of supply 5+ min apart
        for ts, _ in rows:
            if ts >= t_open - 60:
                break
            if prev_ts is None or ts - prev_ts > RUN_GAP:
                starts.append(ts)
            prev_ts = ts
        if starts and starts[-1] >= t_open - 1800:
            a, b = starts[-1] + max(0.0, now - t_open), starts[-1] + off
            for ts, d in rows:
                if a <= ts < b and d.get("shop") != model.SHOP_NAME:
                    u[d.get("sku")] += d.get("qty") or 1
        u.pop(None, None)
        return dict(u)
    except Exception:
        return fallback
    finally:
        if own and db is not None:
            db.close()


def unit_cost(x, j):
    """(expected cost, top cost, margin) of the j-th new unit of a candidate SKU (j = 1 for the next one bought)."""
    f = surge_factor(x["surge_u"] + j)
    cost = x["base_cost"] * f
    return cost, x["base_worst"] * f, x["price"] - cost - x["warranty"]


def candidates(inv, cat, demand, est, p, now):
    """What each SKU's units are worth, before the horizon: {sku: facts}. Each SKU is its own demand (buyers order
    a SKU, and those of a quilt are not those of a sheet): its rate is our posterior for it (model.own_sku_posterior,
    in-stock hours) plus the requests for it we missed times our close rate, and its unit pays its own margin.
    Until 11-04 11 h a category had one count of units, priced at its best-selling SKU, so a second SKU only got
    what the first left: quilt G5cd4ffbc (34k a unit, 3.6 an hour) got none behind sheet Ge62449c9 (15k)."""
    balance = inv.get("balance") or 0
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    have_sku = Counter(u.get("sku") for u in units)
    # 11-04: the seller offers the best grade held, so A and B sell and C stays; counted as stock, old C kept the
    # A and B a buyer asked for from being bought (sheet Ge62449c9: 16 C, 3 B, 0 A; 13-16 h, 15 talks that opened
    # on C were cancelled by buyers who take only A or B). A goods C unit held OLD_C_H hours is still on sale but
    # does not count toward a SKU's units.
    count_sku = Counter(u.get("sku") for u in units if not old_c(u, cat, now))
    by_grade = {}
    for u in units:
        if not old_c(u, cat, now):
            by_grade.setdefault(u.get("sku"), Counter())[u.get("grade")] += 1
    share = grade_shares(est, cat, now) if GRADE_CAP else None
    median = {d["category"]: d["median_price"] for d in demand}
    by_cat = {}
    for sku, x in cat.items():
        by_cat.setdefault(x["category"], []).append((sku, x))
    skip = policy.skipped(policy.load_playbook(), now)   # the manager's playbook: leave these alone for now
    unit_cap = p["unit_cap_frac"] * balance
    cap_h = cap_hours()
    warranty_rate = 0.0
    if p["warranty_hours"] > 0:
        # weighted by the grades as supplied (11-05 00:20); a plain mean of A, B, C counted a C in every third unit,
        # harmless while the break rates were mismatched low (model._breaks) and 0.163 against 0.101 once fixed
        # B and C carry a warranty only for buyers who ask (seller.WARRANTY_ON_ASK, 11-05 00:50)
        warranty_rate = sum(w * est.break_rate[g] * WARRANTY_ASKED.get(g, 1.0)
                            for g, w in SUPPLY_MIX.items() if p[f"warranty_{g}"])
    # 11-04 21:27 rule: past 50 units of a SKU in the market's last hour, each unit adds 0.2% to its cost, up to +50%.
    # Costs here were list x COST_SHARE, so a surged SKU looked cheaper than it was (지누스 그린티 x1.23 makes an A
    # cost about its market price, 0.73 of list). 22:41: x inventory.json `surge` now; 11-04 23:30: each new unit at
    # the forecast count at our purchase (surge_counts, unit_cost), so a surged unit is bought when it still pays.
    counts = surge_counts(inv, now, getattr(est, "db", None))
    mkt = {}
    if MARKET_CHECK:
        try:
            mkt = model.market_grade_prices(getattr(est, "db", None), now, est.cat or None)
        except Exception as e:                          # no market prices: buy as before
            print(f"[restock] market grade prices unavailable: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
    premium_a = est.break_rate.get("A", 0.0) if p["warranty_hours"] > 0 and p.get("warranty_A") else 0.0
    A_RATES.clear()
    if A_RATE and mkt:
        try:
            A_RATES.update(a_rates(est, cat, mkt, premium_a, p["min_margin"], now))
        except Exception as e:                          # no A rates: the demand as before
            print(f"[restock] A rates unavailable: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
    if UW_A_UNCOUNTED and mkt:
        gone = lambda u: old_c(u, cat, now) or underwater_a(u, mkt, premium_a, p["min_margin"])
        count_sku = Counter(u.get("sku") for u in units if not gone(u))
        by_grade = {}
        for u in units:
            if not gone(u):
                by_grade.setdefault(u.get("sku"), Counter())[u.get("grade")] += 1
    mrates, kept_sku = {}, Counter()                    # MARKET_DEMAND
    if MARKET_DEMAND:
        try:
            mrates = market_rates(est, now)
            kept = kept_a(inv, est, now)
            kept_sku = Counter(u.get("sku") for u in units if u.get("unit") in kept)
        except Exception as e:                          # no market rates: the demand as before
            print(f"[restock] market demand unavailable: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
    own_a = None                                        # B_SEEK_A_H: None = no cap (no ledger)
    if B_SEEK and B_SEEK_A_H:
        try:
            own_a = own_a_sales(est, now)
        except Exception as e:
            print(f"[restock] own A sales unavailable: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
    fair_asks = Counter()                               # FAIR_FOOD_MIN: its requests we had none of, per SKU
    if FAIR_FOOD_MIN:
        for r in model.read_jsonl(model.MISSED, now - FAIR_FOOD_H * 3600):
            m = re.search(r"G[0-9a-f]{8}", r.get("text") or "") if FAIR_TEXT in (r.get("text") or "") else None
            if m:
                fair_asks[m.group(0)] += 1
    out = {}
    for s, x in cat.items():
        c, lp, kind = x["category"], x["list_price"], x["kind"]
        surge_u = counts.get(s, 0)
        f = surge_factor(surge_u + 1)                   # the next unit; later ones cost more (unit_cost)
        if c in skip or WORST_COST * lp * f > unit_cap:
            continue
        keep = FAIR_FOOD_MIN if kind == "food" and fair_asks[s] >= FAIR_FOOD_ASKS else 0
        asked = s in est.asked_recent or bool(keep)     # a request we missed in the last 2 h (model.ASKED_RECENT_H)
        if est.sku_rate(s) <= 0 and not asked:
            continue                                    # nobody buys it (11-04: 255 of 415 fruit sales one box)
        if not asked and not have_sku.get(s) and est.unproven(s) and not (SHORTAGE_ENTRY and s in est.shortage()):
            continue                                    # new to the market: wait for its first sales (not when short)
        rate = sku_demand(est, s)
        if kind == "goods" and mrates:
            rate = max(rate, MARKET_SHARE * mrates.get(s, 0.0))     # MARKET_DEMAND
        # continuous since 11-05 01:00: min(cap_h of demand, max(CAP_FLOOR, FAST_COVER_H of demand)); fast (a line
        # every window, P_k over FAST_COVER_H) once FAST_COVER_H of demand reaches CAP_FLOOR
        fast = rate * FAST_COVER_H >= CAP_FLOOR
        cap = math.ceil(min(rate * cap_h, max(CAP_FLOOR, rate * FAST_COVER_H)) - 1e-9)
        if FAST_BUFFER and kind == "goods" and rate >= FAST_RATE:
            cap += FAST_BUFFER                          # units past the window's demand (FAST_BUFFER)
        if kind == "goods":
            cap = math.ceil(cap * GOODS_CAP_X - 1e-9)   # GOODS_CAP_X
        if asked:
            cap = max(cap, 1)                           # a buyer asked us for it
        if SHORTAGE_ENTRY and s in est.shortage():
            # sells as it arrives: an hour of its demand to us (11-04 top 30: a short SKU-hour was followed by a
            # saturated one 0% of the time, by another short one 72%)
            own = short_own_rate(est, s) if kind == "goods" else None
            if own is not None:                         # our in-stock rate x the time to the next refill
                cap = max(cap, math.ceil(own * cadence(now)[1] / 60 - 1e-9))
            else:
                cap = max(cap, math.ceil(rate * SHORT_COVER_H - 1e-9))
        have = count_sku.get(s, 0)
        if kind == "goods" and kept_sku.get(s):
            # MARKET_DEMAND: the kept A do not sell; up to KEPT_FREE caps of them are not counted
            have -= min(kept_sku[s], math.floor(KEPT_FREE * cap + 1e-9))
        if share and kind == "goods":
            g = by_grade.get(s, Counter())
            have = g["A"] + sum(min(g[k], math.ceil(cap * share[k] - 1e-9)) for k in "BC")
        if BOOK_NEWSVENDOR and kind == "goods":        # food past its date is lost: its caps stay
            cap = have + MAX_QTY                      # the units are where value_k turns negative (ranked)
        room = min(cap - have, MAX_QTY)
        seek = (B_SEEK and kind == "goods" and lp >= B_SEEK_LIST and rate >= B_SEEK_RATE and balance > B_SEEK_CASH
                and bool(have_sku.get(s)) and not by_grade.get(s, Counter())["B"]
                and (own_a is None or by_grade.get(s, Counter())["A"] <= max(B_SEEK_A_MIN, own_a[s])))
        if seek:
            have, room = 0, B_SEEK_QTY                  # B_SEEK: the units held do not sell as the B would
        if keep:
            room = max(room, min(keep - have, MAX_QTY)) # FAIR_FOOD_MIN
        if room <= 0:
            continue
        price = est.price_ratio(c) * lp
        m = mkt.get(s) if kind == "goods" else None
        if m and "A" in m:
            # f is the forecast factor of our next unit (surge_counts), not the factor now
            margin = p["min_margin"] if A_CHECK_MARGIN else 0.0
            pays = lambda j: (A_COST * lp * surge_factor(surge_u + j) * (1 + margin) + premium_a * m["A"] * lp
                              <= m["A"] * lp)
            free = seek and not B_SEEK_A_CHECK          # B_SEEK before 11-15: its A may stay (bless.py counts it so)
            if not pays(1) and not free:
                continue                                # an A of it would not pay its cost and breaks at the market
            if A_CHECK_TOP and not free:                             # each unit, up to the last (dearest) one bought
                while room > 1 and not pays(room):
                    room -= 1
            price = min(price, lp * sum(w * m.get(g, m["A"] * GRADE_REL[g]) for g, w in SUPPLY_MIX.items()))
        warranty = warranty_rate * price if kind == "goods" else 0.0
        cost = COST_SHARE[kind] * lp * f
        cu = price - cost - warranty
        if cu <= 0:
            continue
        a, b = est.own_sku_posterior(s)
        a += (rate - est.own_sku_rate(s)) * b           # the missed requests, at the same weight
        out[s] = {"kind": kind, "category": c, "lp": lp, "cost": cost, "worst": WORST_COST * lp * f, "cu": cu,
                  "price": price, "warranty": warranty, "base_cost": COST_SHARE[kind] * lp,
                  "base_worst": WORST_COST * lp, "surge_u": surge_u,
                  "life": FOOD_LIFE * (x.get("shelf_hours") or 24) if kind == "food" else None,
                  "have": have, "room": room, "a": a, "b": b}
        if fast:
            out[s]["h"] = FAST_COVER_H                  # ranked: P_k over this, not the refill cycle
        if keep:
            out[s]["keep"] = keep                       # ranked: its first `keep` units whatever their value
    return out, units, have_sku, by_cat, median, skip


def ranked(cands, p, hours, left):
    """Every unit with a positive value over a refill cycle of `hours`: [(value per won, value, sku, k)]. The k-th
    unit of a SKU is worth P(it sells by the next refill) x its margin - P(not) x what holding it costs, per won of
    its cost: the profit a won tied up in it earns per cycle, so a unit that sells late (slow turnover) is worth less
    than one that sells at once. Each new unit pays its own surge (unit_cost)."""
    h = min(hours, left)
    out = []
    for s, x in cands.items():
        tail = model.nb_tail(x["a"], x["b"], min(h, x.get("h", h)), kmax=x["have"] + x["room"])
        for k in range(x["have"] + 1, x["have"] + x["room"] + 1):
            cost, _, cu = unit_cost(x, k - x["have"])
            if x["kind"] == "food":
                co = cost * min(1.0, h / max(x["life"], 0.1))    # it ages; past its usable life it is lost
            else:
                co = 0.01 * cost * h / 24                        # storage
            pk = tail[k - 1]
            value = pk * cu - (1 - pk) * co
            if k <= x.get("keep", 0):           # FAIR_FOOD_MIN: held for the buyer, ahead of the food lines
                value = max(value, cost)
            if value <= 0:                      # P_k only falls with k and the cost only rises: the rest are worse
                break
            out.append((value / cost, value, s, k))
    out.sort(key=lambda u: -u[0])
    return out


def allocate(units, cands, room, groom=None):
    """Take units from the top while `room` (cash, at the top cost with its surge) lasts: {sku: new units}.
    groom: the most goods may take of it (STOCK_TARGET); food is not held to it."""
    target = Counter()
    for _, _, s, k in units:
        top = unit_cost(cands[s], k - cands[s]["have"])[1]
        goods = groom is not None and cands[s]["kind"] == "goods"
        if top <= room and (not goods or top <= groom):
            room -= top
            if goods:
                groom -= top
            target[s] += 1
    return target


def decide(inv, cat, demand, est, p, now, lines=None, every=None):
    """(the lines to buy now, the plan) for one run."""
    balance = inv.get("balance") or 0
    left = policy.hours_left(now)
    lines_w, every_min = (lines, every) if lines else cadence(now)
    refunds = est.refund_exposure(min(p["warranty_hours"] or 6, model.BREAK_WINDOW / 3600))   # breaks come within 6 h
    reserve = policy.reserve(p, inv.get("bankrupt_line"), *refunds)
    room = balance - reserve
    plan = {"lines_per_window": lines_w, "window_minutes": round(every_min, 1), "reserve": round(reserve),
            "refunds_expected": round(refunds[0]), "refunds_sd": round(refunds[1]), "cash_to_spend": round(room)}
    groom = None
    if STOCK_TARGET:
        try:
            cap, stock, rate = stock_room(inv, est, now, every_min, max(1.0, lines_w / MAX_LINES))
            plan.update(goods_stock=round(stock), goods_sales_per_h=round(rate), stock_cap=None if cap is None else round(cap))
            if cap is not None:
                # goods only (11-10 22 h): the cap is made of goods sales and goods stock, and it also held food,
                # which ranks first by value per won and is counted at 0.75 of list like goods (1.5 x its cost)
                groom = max(0.0, cap)
        except Exception as e:                              # no sales rate: spend as before
            print(f"[restock] stock target unavailable: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
    if policy.restock_stops(left) or room <= 0:             # too close to the end to sell what it buys
        return [], dict(plan, target={}, cycle_minutes=None)
    cands, units, have_sku, by_cat, median, skip = candidates(inv, cat, demand, est, p, now)
    sold = {k for k in have_sku if est.sku_rate(k) > 0 or est.asked_sku.get(k, 0.0) > 0}
    cycle = every_min                                       # refill cycle: start at one window, then settle
    # P_k looks at most CAP_HOURS[1] ahead: with a line per SKU the mean cycle over all SKUs kept passed 3.5 h
    # (11-04 11 h, 200 SKUs over 20 lines a window), but the lines go to the best SKUs first, so those are refilled
    # every window or two; over 3.7 h slow units looked sure to sell and the plan held 37% more units
    horizon = lambda cyc: min(cyc, 60 * CAP_HOURS[1]) / 60
    for _ in range(5):
        order = ranked(cands, p, horizon(cycle), left)
        target = allocate(order, cands, room, groom)
        keep = len(set(target) | sold)                      # a line is one SKU: SKUs to keep refilled
        nxt = math.ceil(max(keep, 1) / lines_w) * every_min
        if nxt == cycle:
            break
        cycle = nxt
    else:                                                   # did not settle: rank once more on the last cycle
        order = ranked(cands, p, horizon(cycle), left)
        target = allocate(order, cands, room, groom)
    # this run: money chose the target (value per won); lines are what this run is short of, so the lines worth
    # most go first
    value = Counter()
    for _, v, s, k in order:
        if k - cands[s]["have"] <= target[s]:
            value[s] += v
    buy, spent, spent_g = {}, 0.0, 0.0
    short_g = {s for s in est.shortage() if cat.get(s, {}).get("kind") == "goods"} if SHORT_OWN and SHORTAGE_ENTRY else set()
    short_room = SHORT_STOCK_MAX - sum(u.get("cost") or 0 for u in units if u.get("sku") in short_g)
    for s in sorted(target, key=lambda s: -value[s]):
        if len(buy) >= MAX_LINES:
            break
        x = cands[s]
        qty, top, exp = 0, 0.0, 0.0                         # units at their top cost, each with its own surge
        while qty < target[s]:
            c, w, _ = unit_cost(x, qty + 1)
            if spent + top + w > room or (s in short_g and exp + c > short_room):
                break
            if groom is not None and x["kind"] == "goods" and spent_g + top + w > groom:
                break
            qty, top, exp = qty + 1, top + w, exp + c
        if qty <= 0:
            continue
        if s in short_g:
            short_room -= exp                               # short goods stock at expected cost (SHORT_STOCK_MAX)
        buy[s] = {"sku": s, "qty": qty, "kind": x["kind"], "category": x["category"], "worst_cost": round(top / qty),
                  "k_first": x["have"] + 1, "value": round(value[s] * qty / target[s]),
                  "surge_u": x["surge_u"], "surge_last": round(surge_factor(x["surge_u"] + qty), 3)}
        spent += top
        if x["kind"] == "goods":
            spent_g += top
    if not policy.END_FLOOR_TO_COST or left >= p["liquidate_hours"]:
        for w in asked(units, by_cat, median, skip, p, balance, cat, demand, now):
            if len(buy) < MAX_LINES and w["sku"] not in buy and spent + w["worst_cost"] <= room:
                buy[w["sku"]] = w
                spent += w["worst_cost"]
    by_c = Counter()                                        # the manager reads the target per category
    for k, n in have_sku.items():
        if k in sold:
            by_c[cat.get(k, {}).get("category")] += n
    for s, n in target.items():
        by_c[cands[s]["category"]] += n
    by_c.pop(None, None)
    plan.update(cycle_minutes=round(cycle, 1), target=dict(by_c), target_skus=len(set(target) | sold),
                stocked_categories=len({u.get("category") for u in units if u.get("sku") in sold}),
                price_ours_factor=round(est.price_ours, 3), price_market_ratio=round(est.price_market, 3),
                asked_of_us_per_h={c.split(">")[-1]: round(r, 2) for c, r in sorted(
                    est.asked_rate.items(), key=lambda t: -t[1])[:10]}, close_rate=round(est.p_close, 3),
                cap_hours=round(cap_hours(), 2),
                sku_demand_per_h={k: round(v, 2) for k, v in sorted(
                    ((k, sku_demand(est, k)) for k in have_sku), key=lambda t: -t[1])[:10]})
    return list(buy.values()), plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    a = ap.parse_args()
    md = Path(a.state)
    inv = read(md / "inventory.json", {})
    out = {"buy": []}
    if inv.get("round", 0) < 3:
        print(json.dumps(out))
        return
    stuck = (inv.get("balance") or 0) < inv.get("bankrupt_line", 0) and (inv.get("bankruptcies_left") or 0) > 0
    if stuck and not [u for u in inv.get("units", []) if u.get("grade") != "expired"]:
        print("[restock] below the bankruptcy line with nothing to sell: filing")
        print(json.dumps({"buy": [], "bankrupt": True}))
        return
    now = time.time()
    if window_shut(inv, now):
        print(json.dumps(out))
        return
    p = policy.load()
    cat = read(md / "catalog.json", {})
    demand = read(md / "demand.json", [])
    try:
        est = model.Estimates(pol=p, now=now, competitors=competitors(cat), cat=cat)
    except Exception as e:                       # no archive (collector down): buy nothing, the stock stays on sale
        print(f"[restock] estimates unavailable ({type(e).__name__}: {e}); buying nothing", file=sys.stderr)
        print(json.dumps(out))
        return
    buy, plan = decide(inv, cat, demand, est, p, now)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"held": sorted(plan["target"]), "ts": now, **plan}, ensure_ascii=False),
                     encoding="utf-8")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now, "balance": inv.get("balance"), "held": len(plan["target"]), "buy": buy,
                            "plan": {k: v for k, v in plan.items() if k != "target"},
                            "lambda0": round(est.lambda0, 4), "prior_h": round(est.prior_weight_h, 4), "share": round(est.share_mean, 4)},
                           ensure_ascii=False) + "\n")
    print(f"[restock] {len(plan['target'])} categories in the plan (cycle {plan.get('cycle_minutes')} min), buying "
          f"{sum(b['qty'] for b in buy)} units in {len(buy)} lines, balance {inv.get('balance')}, reserve {plan['reserve']}")
    print(json.dumps({"buy": [{"sku": b["sku"], "qty": b["qty"]} for b in buy]}))


if __name__ == "__main__":
    main()
