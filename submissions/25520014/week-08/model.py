"""Week 08 model: the posteriors the shop decides with, computed by code from the collector's SQLite archive.

    python model.py            # print the current estimates (what restock and the manager see)

Demand, per catalog category: settled deals arrive as a Poisson process with an unknown rate (deals per hour,
whole market). The rate gets a gamma prior; the gamma is conjugate to the Poisson, so the posterior is two sums:
    alpha = alpha0 + sum of w_i            (w_i = 0.5 ** (age_i / half_life): old sales count less)
    beta  = beta0  + discounted exposure   (the hours watched, discounted the same way)
The prior is the same for all 140 categories (orders pick a category, not a SKU), and it comes from the market's
own data (empirical Bayes): mean lambda0 = the mean of the categories' observed rates, variance = their spread less
the Poisson noise of a count, so the gamma is worth mean / variance hours of observation, at most `prior_hours`.
Until 11-03 it was always worth `prior_hours` (2 h): with demand this skewed that lifted a category with no sale in
3 h (air conditioners) to 4.9 deals an hour, and restock bought 2.1M of large appliances that do not sell.

Our share of a category's deals: Beta, prior mean 1 / (shops holding stock in that category, market average),
weight 5, updated with our deals against everyone's deals in the categories we sold in. One share for the shop,
scaled down in a category more shops than usual stock, never up.

Then for a horizon of H hours, with our rate s * lambda, the gamma-Poisson predictive count is negative binomial:
    P(we sell none in H) = (beta / (beta + s H)) ** alpha,   and tail(c, H)[k-1] = P(we sell at least k in H)
which restock.py uses to price the k-th unit of a category (11-03: one rule for whether to hold and how many).

Sale prices, per category (what restock expects a unit to sell for; the fixed 0.85 x list it used before was a
guess): r = price paid / list price of the other shops' settled deals, a category's weighted mean blended with the
market's mean as if the market were PRICE_PRIOR_W more deals, times one factor for our shop: what our deals got over
their category's ratio, blended with 1 the same way. Our 71 deals by 11-03 23 h spread over 23 categories (5 with
5 or more), too few for a ratio per category; the market had 3,480, and ours sold about 1.08 x the market's ratio.

Refund exposure: our goods sold under a warranty in the last warranty window and not broken yet. Each may still
break with its grade's rate over the part of the 6 h not yet passed; the market refunds the full price from our
balance, and a refund we cannot pay takes the shop under the bankruptcy line. restock keeps the line plus the
expected refunds plus `refund_sd` standard deviations of them out of what it spends.

Breaks, per goods grade: a Beta prior worth BREAK_PRIOR_W sales at the rates said in class on 11-03 (A 3%, B 15%,
C 35%: normal, damaged box, refurbished; said in passing, so the ledger corrects it), updated with the `broken` entries matched to settled goods by
buyer and SKU, over the exposure of those sales: goods break within 6 h, so a sale counts by the share of its 6 h
already passed (a sale 3 h ago counts one half).

Prices, per shop (the buyer's model): r = price paid / list price of every settled deal on the ledger, whoever
bought. A shop's ratio is the weighted mean of its deals' r, blended with the market's mean as if the market
were PRICE_PRIOR_W more deals: (sum w r + 5 x market) / (sum w + 5). A shop with 2 deals sits near the market,
one with 50 near its own mean. Old deals count less (the same half-life). The same blend gives
P(we pay at most a price): the share of the shop's deals at or under that ratio. Other buyers haggle differently,
so the shop's numbers come from their deals only, and our own deals give one factor: what we paid over the shop's
ratio, blended with 1 the same way.

Price bandit, per kind (food, goods) and arm (share of list price for the opening ask): Beta(1, 1), updated from
our own negotiations (data/negotiations.jsonl) with a reward in [0, 1]: 0 for no deal, else
(0.5 x margin + 0.3 x price) / (0.8 x list price), the shop score's weights on profit and revenue.
A deal counts only once it is paid: the buyer's `result` on the ledger (market.db) names our task in its receipt
and says `checks.settled`. In the first hour of Round 3 (11-03) 10 of 24 agreed deals were never paid, and the
bandit had counted them as sales. A deal with no result yet waits PAY_WAIT, then counts as no sale.
"""
import json
import math
import random
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

import policy

DB = policy.DATA / "market.db"
NEGOTIATIONS = policy.DATA / "negotiations.jsonl"
SHOP_NAME = "inextro"           # our join name; settle entries name the shop by URL, the shops table maps it
BREAK_WINDOW = 6 * 3600
BREAK_PRIOR = {"A": 0.03, "B": 0.15, "C": 0.35}
BREAK_PRIOR_W = 10
LAMBDA_FLOOR = 0.01             # deals per hour per category before any data
SHARE_WEIGHT = 5
SHARE_PRIOR_OURS = 10           # a category's own share counts once we have about this many (discounted) sales in it
MISSED = policy.DATA / "missed.jsonl"   # requests to our shop that found nothing (seller.py)
SKU_IN_TEXT = re.compile(r"\bG[0-9a-f]{8}\b")
P_CLOSE_DEFAULT = 0.5           # share of our talks that end in a deal, until 20 talks in 6 h measure it
SKU_SHARE_DEFAULT = 0.3         # our share of a SKU's sales until we sold 2 (discounted) of the SKUs we held
SKU_SHARE_MIN_W = 2.0
SKU_NEW_MIN_H = 0.5             # a SKU new to the market counts at least this many hours (sku_rate): 1 sale in 5 min is not 12/h
SKU_NEW_AFTER_H = 1.0           # a SKU first supplied this long after the window opens is new to the market
SKU_NEW_MIN_SALES = 5          # market settles a SKU new to the market needs before restock enters it
ASKED_RECENT_H = 2.0            # a request we missed lets restock buy a SKU nobody else buys only this long (asked_recent)
SKU_PRIOR_H = 0.5               # hours the market-based guess of our own sales of a SKU is worth (own_sku_rate).
# 2.0 until 11-05 08:15. Backtest (12 h, 24 times, 2,012 SKU-hours, NB log score of our next-hour sales): 2.0 -1.526,
# 0.5 -1.445, 0.25 -1.431 (short SKUs -4.33 / -3.38 / -3.14). 6 h offline, 10 seeds, book: 0.5 beat 2.0 by +3.1M,
# +3.7M and +6.5M with true demand at 0.5 / 0.25 / 2.0, the same end stock (57-61M).
OWN_HALF_LIFE_H = 1.0           # own_sku_rate's memory, its sales and its in-stock hours (11-04 23:28; 1.0 from 11-05 00:45). With the
# policy's half_life_h (24 h) the 자코모 4인 rate read 44 an hour at 22 h when we sold 13: our share fell from 80% to
# 13% after 19 h (A ran short, kimauve came in cheaper) and 24 h kept the afternoon. Backtest 11-04 12-22 h, next
# hour of our sales of our top 40 SKUs, prior unchanged: Poisson deviance 2551 at 24 h, 2256 at 3, 2189 at 1.5, 2249
# at 1; predicted 2627 / 2178 against 2154 sold. The category share and the market rates keep half_life_h.
SHORT_W_H = 1.0                 # shortage(): the window a SKU's market supply and sales are compared over
SHORT_MIN_SOLD = 3              # settles of the SKU in that window, all shops
SHORT_SELL_THROUGH = 0.85       # sold / (market stock at the window's start + supplied in it)
SHORT_STOCK_FRAC = 0.2          # market stock at the start at most max(2, this x sold)
# A SKU that sells as fast as it arrives is short of supply: its sales are the supply, not the demand. 11-04 top 30
# SKUs by amount, hour by hour: 99 such SKU-hours (1,963 sold), we held the SKU in 29% of them against 95% of the
# 237 saturated hours, and the state held into the next hour 72% of the time (saturated 90%). 라클라우드 퀸 sold 15
# an hour with 2 shops and 70 with 10, all it was supplied; we entered at 19 h, 7 h after (unproven, value ranking).
RECENT_W = 0.5                 # own_sku_rate leans this much on our settles of the SKU in the last hour (11-05 00:45),
RECENT_MIN = 5                  # when the market settled it this many times in that hour and we held it 0.5 h of it.
# A share lost shows within the hour: 자코모 4인 read 29/h at 23 h when we sold 1. Backtest 11-04 12-23 h (top 40
# SKUs, 421 SKU-hours, next hour's settles over its in-stock time): Poisson deviance 2389 (1.5 h) -> 1918 with 1.0 h
# and 0.5 of the last hour, predicted 2290 -> 2252 against 2258; SKUs that gained 849 -> 1039 (1685 sold), lost
# 1441 -> 1213 (573). The last hour alone: 3325; 0.7 of it 1809 but +3.5% bias; the prior share at 3 h adds little.


def read_jsonl(path, since):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("ts", 0) >= since:
                    out.append(r)
    except OSError:
        pass
    return out


def connect(path=DB):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def catalog(db):
    return {r["sku"]: dict(r) for r in db.execute("SELECT * FROM catalog")}


def our_urls(db):
    return {r[0] for r in db.execute("SELECT url FROM shops WHERE name=?", (SHOP_NAME,))}


def round_start(db):
    row = db.execute("SELECT MIN(ts) FROM ledger WHERE round >= 3").fetchone()
    return row[0]


def entries(db, kind, since):
    """(ts, data) of Round 3 ledger entries of one kind since a time."""
    for r in db.execute("SELECT ts, data FROM ledger WHERE round >= 3 AND kind=? AND ts >= ? ORDER BY seq",
                        (kind, since)):
        yield r["ts"], json.loads(r["data"])


def sku_of(d):
    """settle entries name the SKU as `item` (agent.md); accept `sku` too in case the market names it so."""
    return d.get("item") if isinstance(d.get("item"), str) else d.get("sku")


def settled(d):
    """A settle entry that counts. Fields not confirmed before Round 3 started are read defensively."""
    return d.get("verified", True) is not False and sku_of(d)


def nb_tail(alpha, beta, hours, kmax=40):
    """[P(at least k sales within `hours`) for k = 1..kmax] when the rate is gamma(alpha, beta per hour): the
    negative binomial predictive."""
    if alpha <= 0:
        return [0.0] * kmax
    q = beta / (beta + hours)
    pmf = q ** alpha
    cdf, out = pmf, []
    for k in range(kmax):
        out.append(max(0.0, 1 - cdf))
        pmf *= (k + alpha) / (k + 1) * (1 - q)
        cdf += pmf
    return out


class Estimates:
    def __init__(self, db=None, pol=None, now=None, competitors=None, cat=None):
        """competitors: {category: number of other shops holding stock in it}, from GET /api/shops. cat: the kit's
        catalog.json, used when the collector has not stored the catalog yet (the first minutes of a run)."""
        self.db = db or connect()
        self.p = pol or policy.load()
        self.now = now or time.time()
        self.cat = catalog(self.db) or cat or {}
        self.categories = sorted({x["category"] for x in self.cat.values()})
        self.competitors = competitors or {}
        self._demand()
        self._share()
        self._breaks()
        self._prices()

    # --- demand: gamma-Poisson per category with exponential forgetting

    def _demand(self):
        hl = self.p["half_life_h"] * 3600
        start = round_start(self.db) or self.now
        horizon = min(self.now - start, 6 * hl)               # older sales weigh under 1/64: skip them
        watched = hl / math.log(2) * (1 - 0.5 ** (horizon / hl)) / 3600 if horizon > 0 else 0.0
        mine = self.mine = our_urls(self.db)
        self.w = defaultdict(float)                           # category -> discounted deals (everyone)
        self.w_ours = defaultdict(float)
        self.w_sku, self.w_sku_ours = defaultdict(float), defaultdict(float)   # the same per SKU (restock's caps)
        self.w_sku_fast = defaultdict(float)                  # our settles per SKU at OWN_HALF_LIFE_H (own_sku_rate)
        self.n1_all, self.n1_ours = defaultdict(int), defaultdict(int)   # settles per SKU in the last hour (RECENT_W)
        self.watched = watched
        self._spans = None                                     # SKU -> spans we held a unit of it (own_sku_rate)
        self.sales = []                                       # (ts, data) for the manager's summary
        for ts, d in entries(self.db, "settle", self.now - horizon):
            if not settled(d):
                continue
            c = self.cat.get(sku_of(d), {}).get("category")
            if not c:
                continue
            wt = 0.5 ** ((self.now - ts) / hl)
            self.w[c] += wt
            self.w_sku[sku_of(d)] += wt
            if d.get("shop") in mine:
                self.w_ours[c] += wt
                self.w_sku_ours[sku_of(d)] += wt
                self.w_sku_fast[sku_of(d)] += 0.5 ** ((self.now - ts) / (OWN_HALF_LIFE_H * 3600))
            if ts >= self.now - 3600:
                self.n1_all[sku_of(d)] += 1
                if d.get("shop") in mine:
                    self.n1_ours[sku_of(d)] += 1
            self.sales.append((ts, d))
        # when each SKU first reached the market in the window: a SKU new to the market sold only since then.
        # Supplies before the window leave the SKU at the whole window (sku_rate).
        self.first_supply = {}
        for ts, d in entries(self.db, "supply", self.now - horizon):
            s = d.get("sku")
            if s and s not in self.first_supply:
                self.first_supply[s] = ts
        self._hl, self._horizon = hl, horizon
        # the prior from the market itself: the spread of the categories' observed rates (empirical Bayes).
        # A gamma with that mean and variance is worth mean / variance hours; demand is skewed (mattresses near
        # 200 deals an hour, air conditioners none), so it is worth minutes and each category's own count decides.
        b0 = self.p["prior_hours"]
        self.lambda0, self.prior_weight_h = LAMBDA_FLOOR, b0
        if watched > 0 and self.categories:
            rates = [self.w[c] / watched for c in self.categories]
            m = sum(rates) / len(rates)
            var = sum((r - m) ** 2 for r in rates) / len(rates) - m / watched   # less the Poisson noise of a count
            self.lambda0 = max(m, LAMBDA_FLOOR)
            if var > 0:
                self.prior_weight_h = min(b0, self.lambda0 / var)
        self.alpha = {c: self.lambda0 * self.prior_weight_h + self.w[c] for c in self.categories}
        self.beta = self.prior_weight_h + watched
        self._asked(hl, horizon, watched)

    def _asked(self, hl, horizon, watched):
        """Requests that came to our shop for what we did not hold (data/missed.jsonl, written by the seller), per
        category and SKU, discounted like the sales. They are demand for us that the market's settles do not show
        as ours, so share() lets them raise our share in that category. Food asked to last longer than the SKU's
        whole shelf life could never have been served and does not count."""
        self.asked_w, self.asked_sku = defaultdict(float), defaultdict(float)
        self.asked_recent = set()      # SKUs asked of us in the last ASKED_RECENT_H: restock's at-least-one rule.
        # The weights above fade over the 24 h half-life, so one request kept a SKU in at least one unit all day
        # (bed G6a08354d: 2 requests at 04 h, no market sale for 8 h, 3 bought at 13 h; owner 11-04 14 h).
        self.asked_rate, self.p_close = {}, P_CLOSE_DEFAULT
        if watched <= 0:
            return
        for r in read_jsonl(MISSED, self.now - horizon):
            m = SKU_IN_TEXT.search(r.get("text") or "")
            x = self.cat.get(m.group(0)) if m else None
            if not x:
                continue
            need = r.get("need_hours")
            if x.get("kind") == "food" and need and (x.get("shelf_hours") or 0) < need + 1:
                continue
            wt = 0.5 ** ((self.now - r["ts"]) / hl)
            self.asked_w[x["category"]] += wt
            self.asked_sku[m.group(0)] += wt
            if self.now - r["ts"] <= ASKED_RECENT_H * 3600:
                self.asked_recent.add(m.group(0))
        self.asked_rate = {c: w / watched for c, w in self.asked_w.items()}
        negs = read_jsonl(NEGOTIATIONS, self.now - 6 * 3600)
        opened = sum(1 for r in negs if r.get("outcome") == "open")
        if opened >= 20:                                       # our close rate once there are enough talks
            self.p_close = sum(1 for r in negs if r.get("outcome") == "deal") / opened

    def sku_rate(self, sku):
        """The market's discounted sales of one SKU per hour (all shops). Buyers settle on few SKUs of a category
        (11-04: 255 of 415 fresh fruit sales one 10 kg apple box, none for the 3.5 kg box we bought at the median
        price), so restock picks SKUs by this and caps each by it. The hours are those since the SKU first reached
        the market (any shop's supply), at least SKU_NEW_MIN_H: until 11-04 13 h they were the whole window, so
        sofas and beds of 1-2M won first supplied at 11 h (yms979) read 1.5 an hour against about 11 sold, and
        dae-yangkim sold 57 of them in 2 h to our 3."""
        if self.watched <= 0:
            return 0.0
        return self.w_sku.get(sku, 0.0) / self.sku_hours(sku)

    def unproven(self, sku):
        """A SKU new to the market (first supplied inside the window) that the market has not yet settled
        SKU_NEW_MIN_SALES of: restock waits for sales before it enters (owner, 11-04 13 h)."""
        first = self.first_supply.get(sku)
        start = self.now - self._horizon
        new = first is not None and first - start > SKU_NEW_AFTER_H * 3600   # not one of the round's opening SKUs
        return new and self.w_sku.get(sku, 0.0) < SKU_NEW_MIN_SALES

    def sku_hours(self, sku):
        """Discounted hours a SKU has been on the market in the window (the whole window if supplied before it)."""
        first = self.first_supply.get(sku)
        if first is None or self.now - first >= self._horizon:
            return self.watched
        age = max(self.now - first, SKU_NEW_MIN_H * 3600)
        hl = self._hl
        return min(self.watched, hl / math.log(2) * (1 - 0.5 ** (age / hl)) / 3600)

    def sku_share(self, c):
        """Our share of the sales of the SKUs of c that we sold: where we stocked a SKU, what part of its sales we
        got. SKU_SHARE_DEFAULT until those come to SKU_SHARE_MIN_W discounted sales of ours."""
        skus = [k for k, w in self.w_sku_ours.items() if w > 0 and self.cat.get(k, {}).get("category") == c]
        ours = sum(self.w_sku_ours[k] for k in skus)
        if ours < SKU_SHARE_MIN_W:
            return SKU_SHARE_DEFAULT
        return ours / sum(self.w_sku[k] for k in skus)

    def own_sku_rate(self, sku):
        """Our own paid sales of one SKU per hour: a gamma-Poisson posterior of our settles of it around the market's
        guess (its sales x our share of the category), worth SKU_PRIOR_H hours. The hours of evidence are the
        hours we had a unit of it in stock (discounted like the sales): buyers ask only shops whose shops.json offers
        list the SKU, so while we hold none nobody can buy it from us, and those hours say nothing about demand.
        Until 11-04 11 h they were the hours since our first supply, empty hours included: quilt G5cd4ffbc sold 5 in
        the 0.6 h we had it (8.4 an hour; the market 27) and its cap stayed at 1-2, while dae-yangkim sold 87 in
        12 h. A SKU we never held has shown nothing and stays at the guess (a1's check, 11-04). Where we sell a SKU
        above our share of its category its cap rises, where below it falls (owner, 11-04 01:30). Settles, not
        agreed talks: of our food deals in 6 h only 125 of 187 were paid."""
        a, b = self.own_sku_posterior(sku)
        return a / b

    def own_sku_posterior(self, sku):
        """(alpha, beta) of own_sku_rate's gamma: alpha discounted settles (plus the guess's), beta hours in stock
        (plus the guess's SKU_PRIOR_H). restock prices the k-th unit of a SKU with its negative binomial."""
        c = self.cat.get(sku, {}).get("category")
        prior = self.sku_share(c) * self.sku_rate(sku) if c else 0.0
        if self._spans is None:
            self._spans = self._stock_spans()
        spans = self._spans.get(sku)
        if not spans:
            return SKU_PRIOR_H * prior, SKU_PRIOR_H
        hl = OWN_HALF_LIFE_H * 3600
        exposed = sum(hl / math.log(2) * (0.5 ** ((self.now - b) / hl) - 0.5 ** ((self.now - a) / hl))
                      for a, b in spans) / 3600
        a, b = self.w_sku_fast.get(sku, 0.0) + SKU_PRIOR_H * prior, exposed + SKU_PRIOR_H
        if RECENT_W and self.n1_all.get(sku, 0) >= RECENT_MIN:
            held = sum(max(0.0, min(y, self.now) - max(x, self.now - 3600)) for x, y in spans) / 3600
            if held >= 0.5:                       # the same spread (b), its mean moved toward the last hour
                # per hour held, like a / b (11-05 01:40 review: settles per clock hour undercounted a SKU out
                # of stock part of the hour)
                a = (RECENT_W * self.n1_ours.get(sku, 0) / held + (1 - RECENT_W) * a / b) * b
        return a, b

    def shortage(self):
        """SKUs short of supply in the market's last SHORT_W_H: {sku: {"sold", "supplied", "stock0", "shops"}}, the
        shops being those that supplied it in the window. Market stock is every shop's supplies less its settles and
        expiries since the round began, from the ledger."""
        if getattr(self, "_short", None) is not None:
            return self._short
        start = round_start(self.db) or 0
        w0 = self.now - SHORT_W_H * 3600
        stock0, sup, sold, shops = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(set)
        for ts, d in entries(self.db, "supply", start):
            s, n = d.get("sku"), len(d.get("units") or []) or d.get("qty") or 0
            if not s or ts >= self.now:
                continue
            if ts < w0:
                stock0[s] += n
            else:
                sup[s] += n
                shops[s].add(d.get("shop"))
        for ts, d in entries(self.db, "settle", start):
            s = sku_of(d)
            if ts < w0:
                stock0[s] -= 1
            elif ts < self.now:
                sold[s] += 1
        for ts, d in entries(self.db, "expire", start):
            if ts < w0:
                for s in d.get("skus") or []:
                    stock0[s] -= 1
        out = {}
        for s, n in sold.items():
            s0 = max(stock0.get(s, 0), 0)
            avail = s0 + sup.get(s, 0)
            if (n >= SHORT_MIN_SOLD and avail > 0 and n / avail >= SHORT_SELL_THROUGH
                    and s0 <= max(2, SHORT_STOCK_FRAC * n)):
                out[s] = {"sold": n, "supplied": sup.get(s, 0), "stock0": s0, "shops": len(shops.get(s, ()))}
        self._short = out
        return out

    def _stock_spans(self):
        """SKU -> the merged time spans we held at least one unit of it: each unit from its supply to its settle or
        expiry (now while it is still here), from the ledger."""
        since = self.now - 6 * self.p["half_life_h"] * 3600
        unit = {}                                             # unit -> [sku, supplied, gone]
        for ts, d in entries(self.db, "supply", since):
            if d.get("shop") == SHOP_NAME and d.get("sku"):
                for u in d.get("units") or []:
                    unit[u] = [d["sku"], ts, None]
        for ts, d in entries(self.db, "settle", since):
            x = unit.get(d.get("unit"))
            if x and x[2] is None and d.get("shop") in self.mine:
                x[2] = ts
        for ts, d in entries(self.db, "expire", since):
            if d.get("shop") == SHOP_NAME:
                for u in d.get("units") or []:
                    if u in unit and unit[u][2] is None:
                        unit[u][2] = ts
        by = defaultdict(list)
        for sku, a, b in unit.values():
            by[sku].append((a, min(b or self.now, self.now)))
        out = {}
        for sku, iv in by.items():
            merged = []
            for a, b in sorted(iv):
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            out[sku] = merged
        return out

    def rate(self, c):
        """Posterior mean deals per hour in category c, whole market."""
        return self.alpha[c] / self.beta

    # --- our share of a category's deals

    def _share(self):
        held = [n for n in self.competitors.values() if n is not None]
        mean = 1 / (1 + (sum(held) / len(held) if held else 3))
        a, b = SHARE_WEIGHT * mean, SHARE_WEIGHT * (1 - mean)
        for c, w_ours in self.w_ours.items():
            a += w_ours
            b += self.w[c] - w_ours
        self.share_ab = (a, b)
        self.share_mean = a / (a + b)

    def share(self, c):
        """Our expected share in c: the category's own share (our sales in c over the market's), shrunk toward a
        prior worth SHARE_PRIOR_OURS of our sales. The prior is the shop-wide share, scaled down where more shops
        than usual hold stock; never scaled up where few hold: few shops stock what few buyers order (11-03: 8% for
        air conditioners nobody bought, against 3% measured). A category we never sold in keeps the prior. Until
        11-04 07 h every category used the shop-wide 5.8%: mattresses (8.6% ours, 32-40 deals an hour) were planned
        at 4.2% and sofas (24.7%) at 4.1%, so restock thought 30 mattresses would not sell in a cycle. Requests that came to us and found nothing raise it: they would have closed
        at our close rate, so our rate (share x market rate) is at least requests per hour x close rate. That can
        pass 1 x the market where the market's few settles undercount what is asked of us."""
        n = self.competitors.get(c)
        s = self.share_mean
        if n is not None:
            held = [x for x in self.competitors.values() if x is not None]
            usual = 1 + (sum(held) / len(held) if held else 3)
            s *= min(1.0, usual / (1 + n))
        ours = self.w_ours.get(c, 0.0)
        if ours > 0 and s > 0:
            k = SHARE_PRIOR_OURS / s                           # the prior, in market sales
            s = (ours + SHARE_PRIOR_OURS) / (self.w[c] + k)
        asked = self.asked_rate.get(c, 0.0) * self.p_close
        return max(s, asked / max(self.rate(c), 1e-9)) if asked else s

    def p_none(self, c, hours):
        """P(we sell nothing in c within `hours`): negative binomial at zero."""
        s = self.share(c)
        return (self.beta / (self.beta + s * hours)) ** self.alpha[c]

    def tail(self, c, hours, kmax=40):
        """[P(we sell at least k in c within `hours`) for k = 1..kmax]: the negative binomial predictive."""
        return nb_tail(self.alpha[c], self.beta / self.share(c) if self.share(c) > 0 else 1e12, hours, kmax)

    # --- sale prices: the market's ratio per category, times our shop's factor

    def _prices(self):
        hl = self.p["half_life_h"] * 3600
        rows = []                                             # (category, paid / list, weight, ours)
        for ts, d in self.sales:
            x, a = self.cat.get(sku_of(d)), amount_of(d)
            if x and a and x.get("list_price"):
                rows.append((x["category"], a / x["list_price"], 0.5 ** ((self.now - ts) / hl), d.get("shop") in self.mine))
        others = [(c, r, w) for c, r, w, o in rows if not o]
        self.price_market = (sum(r * w for _, r, w in others) + PRICE_R0) / (sum(w for *_, w in others) + 1)
        sw, srw = defaultdict(float), defaultdict(float)
        for c, r, w in others:
            sw[c] += w
            srw[c] += r * w
        self.price_cat = {c: (srw[c] + PRICE_PRIOR_W * self.price_market) / (sw[c] + PRICE_PRIOR_W) for c in sw}
        ours = [(c, r, w) for c, r, w, o in rows if o]
        self.price_ours_n = len(ours)
        self.price_ours = ((sum(w * r / self.cat_ratio(c) for c, r, w in ours) + PRICE_PRIOR_W)
                           / (sum(w for *_, w in ours) + PRICE_PRIOR_W))

    def cat_ratio(self, c):
        """The market's price paid / list in category c (its mean when nobody sold there yet)."""
        return self.price_cat.get(c, self.price_market)

    def price_ratio(self, c):
        """What we expect a unit of category c to sell for, as a share of its list price (never above list)."""
        return min(1.0, self.cat_ratio(c) * self.price_ours)

    # --- refunds we may still owe: our goods under warranty, not broken yet

    def refund_exposure(self, hours):
        """(expected refunds, their standard deviation) over our goods sold in the last `hours` with a warranty."""
        sold = [(ts, d) for ts, d in entries(self.db, "settle", self.now - hours * 3600)
                if settled(d) and d.get("shop") in self.mine and (d.get("warranty_hours") or 0) > 0
                and self.cat.get(sku_of(d), {}).get("kind") == "goods"]
        broke = {d.get("unit") for _, d in entries(self.db, "broken", self.now - hours * 3600)}
        mean = var = 0.0
        for ts, d in sold:
            a = amount_of(d) or 0
            if d.get("unit") in broke:                        # already refunded (or will be at once)
                continue
            p, e = self.break_rate.get(d.get("grade"), max(self.break_rate.values())), min(1.0, (self.now - ts) / BREAK_WINDOW)
            p = p * (1 - e) / (1 - p * e) if p * e < 1 else 0.0   # it has not broken in the part already passed
            mean += a * p
            var += a * a * p * (1 - p)
        return mean, math.sqrt(var)

    # --- break rates per goods grade

    def _breaks(self):
        # A break names its unit (and order); until 11-05 00:20 it was matched to the last sale of its (buyer, SKU),
        # and buyers buy a SKU many times in several grades: A read 0.063, B 0.094, C 0.134 while the unit-matched
        # market rates were A 0.030, B 0.12, C 0.34 (11-04, 1,680 breaks), so A floors sat ~2.5%p of list too high
        # and C under warranty was sold at prices its refunds (34% of C revenue) did not cover.
        since = self.now - 7 * 86400
        sold = {}                                             # unit -> (ts, grade)
        n = defaultdict(float)
        for ts, d in entries(self.db, "settle", since):
            sku = sku_of(d)
            if not settled(d) or self.cat.get(sku, {}).get("kind") != "goods":
                continue
            g = d.get("grade") or "?"
            if d.get("unit"):
                sold[d["unit"]] = (ts, g)
            n[g] += min(1.0, (self.now - ts) / BREAK_WINDOW)   # exposure: a 3-hour-old sale has had half its window
        k = defaultdict(int)
        for ts, d in entries(self.db, "broken", since):
            s = sold.get(d.get("unit"))
            if s and 0 <= ts - s[0] <= BREAK_WINDOW + 60:
                k[s[1]] += 1
        self.break_counts = {g: (k[g], round(n[g], 1)) for g in "ABC"}
        self.break_rate = {g: (BREAK_PRIOR_W * BREAK_PRIOR[g] + k[g]) / (BREAK_PRIOR_W + max(n[g], k[g]))
                           for g in "ABC"}


# --- prices per shop, from everyone's settled deals (the buyer's model)

PRICE_PRIOR_W = 5               # a shop's own deals outweigh the market's mean after 5
PRICE_R0 = 0.85                 # the market's ratio before any deal


def amount_of(d):
    """agent.md's example settle names the price `amount`; accept `price` too."""
    a = d.get("amount", d.get("price"))
    return a if isinstance(a, (int, float)) and a > 0 else None


MKT_GRADE_H = (2, 24)    # market_grade_prices: the recent window, then the day when it has too few settles
MKT_GRADE_MIN = 5        # other shops' settles of a SKU and grade needed for its median


def market_grade_prices(db=None, now=None, cat=None):
    """{sku: {grade: median paid / list}} of goods other shops settled: over the last MKT_GRADE_H[0] hours, or the
    last MKT_GRADE_H[1] when the recent window has under MKT_GRADE_MIN of that SKU and grade. What a unit of ours
    can sell for now: 11-04 22-23 h 자코모 4인 A sold at 0.80-0.81 of list in the market while our floor (cost x
    1.08 + warranty, the A units bought at 0.75-0.81 with the surge) sat at 0.86-0.93: 92 talks, 4 deals, and
    restock still bought A at 23:05 and 23:22 (owner, 11-04 23:50)."""
    db = db or connect()
    now = now or time.time()
    cat = cat or catalog(db)
    ours = our_urls(db)
    recent, day = {}, {}
    for ts, d in entries(db, "settle", now - MKT_GRADE_H[1] * 3600):
        s, price = sku_of(d), amount_of(d)
        x = cat.get(s) or {}
        if d.get("shop") in ours or not price or not x.get("list_price") or x.get("kind", "goods") != "goods":
            continue
        g = d.get("grade")
        if g not in ("A", "B", "C"):
            continue
        r = price / x["list_price"]
        day.setdefault(s, {}).setdefault(g, []).append(r)
        if ts >= now - MKT_GRADE_H[0] * 3600:
            recent.setdefault(s, {}).setdefault(g, []).append(r)
    med = lambda v: sorted(v)[len(v) // 2]
    out = {}
    for s, by in day.items():
        for g, v in by.items():
            rv = recent.get(s, {}).get(g, [])
            use = rv if len(rv) >= MKT_GRADE_MIN else v if len(v) >= MKT_GRADE_MIN else None
            if use:
                out.setdefault(s, {})[g] = round(med(use), 4)
    return out


class Prices:
    def __init__(self, db=None, cat=None, now=None, half_life_h=24, us=SHOP_NAME):
        """us: our buyer's name on the ledger (settle `buyer`). Shops are named as in shops.json (`name`)."""
        self.db = db or connect()
        self.now = now or time.time()
        self.cat = catalog(self.db) or cat or {}
        hl = half_life_h * 3600
        # shops by name: a shop's URL changes when its kit restarts (a new tunnel), settle entries carry the URL
        names = {r["url"]: r["name"] for r in self.db.execute("SELECT url, name FROM shops")}
        self.deals = defaultdict(list)                        # shop name -> [(r, w)], other buyers' deals
        ours = []                                             # (shop, r, w)
        for ts, d in entries(self.db, "settle", self.now - 6 * hl):
            x, a = self.cat.get(sku_of(d)), amount_of(d)
            if not settled(d) or not x or not a or not x.get("list_price") or not d.get("shop"):
                continue
            r, w = a / x["list_price"], 0.5 ** ((self.now - ts) / hl)
            shop = names.get(d["shop"], d["shop"])
            if d.get("buyer") == us:
                ours.append((shop, r, w))
            else:
                self.deals[shop].append((r, w))
        every = [rw for v in self.deals.values() for rw in v]
        self.n = len(every) + len(ours)
        self.market = (sum(r * w for r, w in every) + PRICE_R0) / (sum(w for _, w in every) + 1)
        self.W = {s: sum(w for _, w in v) for s, v in self.deals.items()}
        self.shop = {s: (sum(r * w for r, w in v) + PRICE_PRIOR_W * self.market) / (self.W[s] + PRICE_PRIOR_W)
                     for s, v in self.deals.items()}
        self.Wu = sum(w for _, _, w in ours)
        self.us = (sum(r / self.ratio_market(s) * w for s, r, w in ours) + PRICE_PRIOR_W) / (self.Wu + PRICE_PRIOR_W)

    def ratio_market(self, shop):
        """The shop's ratio from other buyers' deals (the market's mean for a shop nobody bought from)."""
        return self.shop.get(shop, self.market)

    def ratio(self, shop):
        """The price / list price we expect to pay at `shop`."""
        return self.ratio_market(shop) * self.us

    def p_within(self, shop, price, list_price):
        """P(we end at or under `price` at `shop`): the share of its deals at or under that ratio (for us), blended
        with the market's share the same way."""
        cut = price / list_price / self.us
        every = [rw for v in self.deals.values() for rw in v]
        tot = sum(w for _, w in every)
        # the market's share, with one deal's worth of a flat prior over 50-100% of list (an empty ledger at the
        # start of the round must not rule out every order whose budget is under the usual price)
        flat = min(max((cut - 0.5) / 0.5, 0.0), 1.0)
        market = (sum(w for r, w in every if r <= cut) + flat) / (tot + 1)
        v = self.deals.get(shop, [])
        return (sum(w for r, w in v if r <= cut) + PRICE_PRIOR_W * market) / (self.W.get(shop, 0) + PRICE_PRIOR_W)



# --- price bandit: Thompson sampling over opening-ask arms, from our own negotiations

def reward(rec):
    if not rec.get("price"):
        return 0.0
    margin = max(rec["price"] - rec["cost"], 0)
    return min(1.0, (0.5 * margin + 0.3 * rec["price"]) / (0.8 * rec["list_price"]))


STAR_W = 0.2     # the bandit's reward: this share from the buyer's stars (the shop score weighs stars 0.2). On
                 # Round 3's first 24 h, with stars left out the bandit chose 0.9/1.0 in 60% of talks (stars 4.2-4.3
                 # against 4.6-4.8 at 0.7/0.8, our stars rank 31 of 41); at 0.2 the best arm per talk is 0.7 for food
                 # and goods, the pair a replay of the shop score ranks first (0.938 against 0.838 for 0.9/0.9)
NO_STARS = 4     # a paid deal whose review has not arrived counts as this many stars
PAY_WAIT = 900   # seconds a deal may wait for the buyer's result before it counts as unpaid


def paid_tasks(db_path=DB):
    """{task: settled} from the buyers' results on the ledger; None when market.db cannot be read."""
    try:
        db = connect(db_path)
        rows = db.execute("SELECT json_extract(data, '$.receipt.task'), json_extract(data, '$.checks.settled') "
                          "FROM ledger WHERE round >= 3 AND kind = 'result' AND data LIKE '%\"task\"%'").fetchall()
        db.close()
    except sqlite3.Error:
        return None
    out = {}
    for task, ok in rows:
        if task:
            out[task] = out.get(task, False) or bool(ok)
    return out


def task_stars(db_path=DB):
    """{task: stars} the buyers gave us on the ledger (their results); {} when market.db cannot be read."""
    try:
        db = connect(db_path)
        rows = db.execute("SELECT json_extract(data, '$.receipt.task'), json_extract(data, '$.stars') "
                          "FROM ledger WHERE round >= 3 AND kind = 'result' AND data LIKE '%\"task\"%'").fetchall()
        db.close()
    except sqlite3.Error:
        return {}
    return {t: s for t, s in rows if t and s is not None}


def bandit_stats(path=NEGOTIATIONS, since=0, now=None, paid=..., stars=...):
    """Beta counts per (kind, arm). A negotiation is its `open` line plus a `deal` line, or no deal when it was
    cancelled or has been open for 10 minutes (buyers often just stop answering). A deal is a sale only when the
    buyer paid (paid_tasks); unknown for PAY_WAIT, then no sale. A sale's reward mixes margin and revenue (reward)
    with the buyer's stars (STAR_W). Without market.db every deal counts, as before."""
    now = now or time.time()
    if paid is ...:
        paid = paid_tasks()
    if stars is ...:
        stars = task_stars()
    stats = {(k, a): [1.0, 1.0] for k in ("food", "goods") for a in policy.arms(k)}   # goods talks before
    # 11-05 00:30 have list-share arms (0.7-1.0) that are not goods arms any more: left out
    opened, closed = {}, {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("ts", 0) < since or not rec.get("task"):
            continue
        if rec.get("outcome") == "open":
            opened[rec["task"]] = rec
        else:
            closed[rec["task"]] = rec
    for task, rec in opened.items():
        end = closed.get(task)
        if end is None and now - rec["ts"] < 600:
            continue
        key = (rec.get("kind"), rec.get("arm"))
        if key not in stats or (end and end.get("fallback")):   # a C sold at the buyer's offer (seller.c_fallback)
            continue                                              # is not the arm's price
        r = 0.0
        if end and end.get("outcome") == "deal":
            if paid is not None and task not in paid and now - end["ts"] < PAY_WAIT:
                continue
            if paid is None or paid.get(task):
                r = (1 - STAR_W) * reward(end) + STAR_W * (stars.get(task, NO_STARS) - 1) / 4
        stats[key][0] += r
        stats[key][1] += 1 - r
    return stats


def pick_arm(kind, stats=None):
    stats = stats or bandit_stats()
    draws = {a: random.betavariate(*stats[(kind, a)]) for a in policy.arms(kind)}
    return max(draws, key=draws.get)


if __name__ == "__main__":
    e = Estimates()
    print(f"lambda0 {e.lambda0:.3f}/h per category, prior worth {e.prior_weight_h:.3f} h, beta {e.beta:.2f} h, share {e.share_mean:.3f}, "
          f"{len(e.sales)} settles in the window")
    top = sorted(e.categories, key=e.rate, reverse=True)[:10]
    for c in top:
        print(f"  {e.rate(c):7.2f}/h  ours {e.w_ours[c]:.1f}  {c}")
    print("break rate by grade:", {g: round(r, 3) for g, r in e.break_rate.items()}, e.break_counts)
    pr = Prices(e.db, cat=e.cat, now=e.now, half_life_h=e.p["half_life_h"])
    print(f"prices: {pr.n} settles, market ratio {pr.market:.3f}, our buyer x{pr.us:.3f} ({pr.Wu:.1f} deals)")
    for s in sorted(pr.shop, key=pr.shop.get)[:5]:
        print(f"  {pr.shop[s]:.3f}  ({pr.W[s]:.1f} deals)  {s}")
    s = bandit_stats()
    for k in ("food", "goods"):
        print(k, {a: round(s[(k, a)][0] / sum(s[(k, a)]), 2) for a in policy.arms(k)})
