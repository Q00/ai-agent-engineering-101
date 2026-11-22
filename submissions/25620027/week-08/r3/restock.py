"""Restock hook: breadth first, then replenish what sells, never below the cash line.

The kit runs this every 60 seconds and buys at most ten lines. Visibility in Round 3
is stock: a buyer's shop list shows only shops holding an acceptable SKU, and the
house buyers pick randomly among them. So the first hours spread one or two units
over many categories chosen for low risk (food with a 30-day shelf life, mid-priced
A-grade goods), then the plan follows the public ledger's settled categories and our
own sales. Costs are drawn by the market, so every line is checked against a
worst-case cost and a cash reserve before it is printed.
"""

from __future__ import annotations

import contextlib
import json
import math
import random
import sqlite3
import statistics
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import Field, ValidationError

from market_a2a.models import FrozenModel
from r3.bc_rebuy import apply_bc_rebuy, bc_rebuy_lines
from r3.endgame import ROUND_END, buy_share
from r3.fresh_list import (
    CIT_FOOD_AB_LINE,
    CIT_NEEDS_FILE,
    FRESH_LIST_FILE,
    FRESH_RATIO,
    NEED_MARGIN_HOURS,
    NEEDS_FILE,
    fresh_need,
    load_cit_needs,
    load_fresh_list,
)
from r3.growth import build_growth
from r3.growth_plan import (
    BAND_OVERLAP,
    MIN_MARKET_SETTLES,
    probe_targets,
    proven_targets,
)
from r3.market_files import (
    CatalogItem,
    DemandRow,
    Inventory,
    KitFile,
    RestockWindow,
    read_catalog,
    read_demand,
    read_inventory,
)
from r3.overpriced import OVERPRICED_FILE, load_overpriced
from r3.policy import RestockPolicy, load_policy, policy_path
from r3.priority_restock import priority_lines
from r3.rater_demand import (
    EXTRA_SKUS,
    NEW_BUYER_SKUS,
    RATER_DEMAND_FILE,
    load_rater_demand,
)
from r3.stockouts import STOCKOUTS_FILE, load_stockouts, stockout_target

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from r3.growth import GrowthEvidence

STATE_FILE: Final = "r3-restock-state.json"
MAX_LINES: Final = 10
# The market draws a unit's cost at 55-75% of list for grade A; cash checks use the
# top of that range. The policy table cannot lower it.
WORST_CASE_COST_SHARE: Final = 0.75
# The kit retries a refused line three times, sleeping up to 30 s each, so a plan
# printed further than this before the window opens spends its lines on 429s.
WINDOW_LEAD_SECONDS: Final = 30.0
# Owner rule (11/4): no food with under a week of shelf life, whatever the
# strategy sets; 24 h food aged into clearance and sold below cost.
OWNER_MIN_FOOD_SHELF_HOURS: Final = 168
# Owner rule (11/4): high-value goods first. A goods category's score is scaled
# by the median list price of its fitting SKUs over this unit (never below 1):
# a unit at ~0.78 of list on ~0.60 cost earns ~18% of list, so one 420k mattress
# earns what about 28 cheap food sales do.
VALUE_UNIT: Final = 100_000
# Depth (11/4 audit): two mattresses were 43% of market takings and sell within
# minutes of supply, yet we held 3 and 1 while 10-unit lots of SKUs with no market
# sale sat unsold. The top restock.depth_skus goods SKUs by market takings
# (settles x list, 24 h; 12 from 11/4 22:50, owner) are
# held to a depth of one unit per DEPTH_SETTLES market settles (owner 11/4: go
# aggressive, so one per 50), from 2 up to
# units_per_sku; a SKU needs MIN_MARKET_SETTLES to count as selling at all.
# 11/13 12:20 (owner: invest more): one unit per 30 settles, up from 50.
DEPTH_SETTLES: Final = 30
# Replenish depth from our own sales (11/4): goose duvets sold 5-6 an hour with one
# left, but a SKU still in stock was never replenished outside the market top. A
# SKU we sold in the last hour is now kept at its rate x COVER_HOURS (about two
# supply windows), at least replenish_qty and at most units_per_sku.
OWN_RATE_WINDOW: Final = 3600.0
COVER_HOURS: Final = 0.67
# 11/14 06:3x (owner: "안팔리는 재고는 더이상 안사게 ... 잘팔리는거 공격적으로, 재고관리
# 제일 중요"): with replenish_qty 10 (v207) any SKU sold once in 48 h was rebought
# to 10 units: G6398e431 (2.98M list, 5 sold a day by us) took 9 units for 16.4M at
# 05:26 and G9718023b 9 for 15.0M at 04:23. With the manager's economics at hand
# the rebuy target is now our own last 12 h of sales of the SKU (at least
# MIN_SALES_TARGET, at most units_per_sku): the top sellers (G021aae64, G0667d440,
# Gb5d0dde7 at 18-28 a 12 h) go deeper than 10, slow ones stay thin.
MIN_SALES_TARGET: Final = 2
# 11/14 14:0x: food units grew 676 -> 944 in 7 h (about +40 an hour, 1,696 units of
# the 2,000 max_units at 13:59, when every buy would stop) because food is rebought
# to a full 12 h of our sales while supply opens every ~20 min. Food now holds
# FOOD_SALES_SHARE of that (about 6 h); goods keep the full 12 h.
# 11/15 14:0x (owner, 13:53 measurement): food A/B now sells a median 10 min after
# it arrives while supply opens for 3-4 min every ~20 min, so the shelf sat empty
# half of each cycle (A 7, B 51 at 13:3x; "none in stock" ~125 an hour against ~75).
# The window cadence is the market's, so each window refills a full 12 h of our
# settled sales again (max_units is now 3,000 and may rise with food).
FOOD_SALES_SHARE: Final = 1.0
# 11/14 07:0x (same owner ask): other paths still bought goods we barely sell. At
# 06:4x a market-takings depth line bought 12 G07ec17d5 (4 sold by us in 12 h) and
# 15 G7c3f464c came in (3 sold, net -111,035 a unit). Every buy passes through
# affordable(), so with economics a goods SKU's A/B holding plus planned buys is
# capped at SELL_COVER_FACTOR x our own 12 h sales, never under SELL_COVER_MIN (a
# SKU new to us still gets a trial). Probe SKUs keep their own cap.
SELL_COVER_FACTOR: Final = 2
SELL_COVER_MIN: Final = 3
# 11/14 19:0x (owner: "C급 왜 700개나 샀었지?" -> "Yes, add it"): supply grades are
# random, and C does not count as held (11/13 20:21), so filling A/B targets keeps
# bringing C in. Goods C sold 46 a day against 282 held (about 6 days); food C 603
# against 714. For a goods SKU we sell slowly (under C_PILE_EXEMPT_SALES in 12 h)
# that already holds C_PILE or more C units, half of its C units take room in the
# SKU's sales cap, so it is bought again only once that C moves. Fast sellers such
# as G021aae64 (28 in 12 h, 25 C held) keep their full A/B room.
C_PILE: Final = 3
C_PILE_EXEMPT_SALES: Final = 10
# New SKUs (breadth) start small and grow only if they sell (owner, 11/4): a new
# item is bought TRIAL_QTY at a time, and a SKU whose price sits within
# BAND_OVERLAP of one we already hold in the category ranks lower (fill gaps).
TRIAL_QTY: Final = 2
# 11/14 20:4x (task 2-Q, owner "김치는 기다리지 말고 지금 진행"): q00 bought 166 kimchi
# in 12 h at other shops (G5f5f42e0 41, Ge48ed59c 13, G0e64d348 7) and none from
# us; we held no A/B of these. They are bought like any new food SKU (shelf and
# price rules, no grade choice): TRIAL_QTY first, then half of our 12 h sales.
# Kimchi arrives with 32-108 h of shelf left (median 62 h), so they are not added
# once fewer than NAMED_FOOD_MIN_HOURS_LEFT remain before the round ends.
NAMED_FOOD_SKUS: Final = ("G5f5f42e0", "Ge48ed59c", "G0e64d348")
NAMED_FOOD_MIN_HOURS_LEFT: Final = 48.0
# Rater demand list (r3/rater_demand.py, task 2-T): these SKUs pass the goods price
# floor, start at RATER_TRIAL_QTY and then hold up to SELL_COVER_FACTOR x our 12 h
# sales (food: FOOD_SALES_SHARE of it), at most RATER_LINES lines a run.
RATER_TRIAL_QTY: Final = 4
# 11/14 22:4x (owner's overnight rule): above NEW_SKU_STOP_UNITS units held, or
# under NEW_SKU_STOP_CASH cash, no SKU we hold no A/B of is started (coverage, the
# named kimchi, rater-list trials); restock of what we already hold goes on. At
# 22:45 units were 2,006 after +226 in an hour (408 in, 182 out).
# max_units 8000 (11/17 13:2x); 6200 at 6500 (09:4x), 4700 at 5000, 3700 before.
NEW_SKU_STOP_UNITS: Final = 7700
# 11/14 23:3x owner addendum: no goods SKU is started while goods stock is above
# this cost (468M at 23:33); food and goods already held go on as before.
# 11/16 09:2x (owner, Codex review): 520M -> 430M, and no goods SKU is started at all
# (rater-list trials, coverage and breadth included) while NEW_GOODS_PAUSED, until the
# book gap to inextro is back above 35M (24.8M at 08:53). Goods we already hold are
# still replenished by sales; food keeps the 3,700-unit stop.
# 430M 11/16 -> 550M 05:5x -> 750M 07:0x -> 850M 08:0x -> 1,000M 08:2x -> 1,150M 19:2x
NEW_GOODS_STOP_COST: Final = 1_150_000_000
# 11/17 05:5x (owner): pause lifted. Goods 500k+ bought 78-88 units per 2 h at
# 15:42-19:42 and 11-28 after 23:42; 5 of the top 30 goods by 12 h takings were not
# held. A goods SKU we neither hold nor sold starts only when its 12 h market paid
# median is at least NEW_GOODS_MARGIN x its 48 h market supply cost median.
NEW_GOODS_PAUSED: Final = False
NEW_GOODS_MARGIN: Final = 1.08
NEW_SKU_STOP_CASH: Final = 400_000_000
# 11/15 10:2x (owner, from the Codex stock review): food C grew from 840 units (11/14
# 23 h) to 1,193 (11/15 10:11), all under 360 h of shelf life left. Food targets count
# A/B only, so a SKU holding 50 C units and no A/B was still bought 6 more. On every
# path a food SKU whose C units exceed its own C sales in FOOD_C_HOURS is bought that
# much less; with no A/B on hand it still gets MIN_SALES_TARGET units for A/B asks.
FOOD_C_HOURS: Final = 12.0
# 11/15 15:0x / 16:47 (owner): buyer-02 list SKUs (r3/fresh_list.py) short of
# fresh_need() units with FRESH_RATIO+ of their shelf life left get a FRESH_LINE_QTY
# line each supply window, at most FRESH_LINES lines a run, while one SKU holds
# fewer than FRESH_MAX_HELD units and all listed SKUs tie up under FRESH_MAX_COST.
FRESH_LINES: Final = 3
FAST_GOODS_SALES: Final = 40
FAST_GOODS_CAP: Final = 40
MAX_LINE_QTY: Final = 20
SLOW_GOODS_SALES: Final = 4
# 11/16 01:5x: 0 (was 2 from 11/15 22:04). In 3.7 h buyer-02's shelf two-stars
# went to 12.6 % (target 7 %) and food A+B fell 406 -> 245 while listed SKUs that
# rarely arrive fresh piled up (G7fa48eaa 88 held, 0 fresh); fresh lines again
# come only after the sold-out, named-food and rater lines.
# 11/17 08:5x (owner, strategy 08:51): 3 again. Since the buyer-02 need rule (07:47)
# its two-stars stopped but its sales fell from 54-63 to 23-33 per 30 min: listed
# SKUs with no unit lasting its named need (r3-buyer02-needs.json + margin) come
# first, most-bought first, up to 3 lines a window.
FRESH_GUARANTEED_LINES: Final = 3
FRESH_LINE_QTY: Final = 20
FRESH_MAX_HELD: Final = 200
FRESH_MAX_COST: Final = 30_000_000  # 10M until 11/17 10:5x (food pays no storage)
# 11/17 09:4x (owner, strategy 09:47): FRESH_MAX_HELD kept stale stock from making
# room for fresh units (G7fa48eaa 207 held, none with 0.75 left; G00c60e91 177, 1).
# Food SKUs buyer-02 or dyishappy settled here in the last RATER_FOOD_HOURS skip
# it; they hold at most FRESH_OPEN_HOLD units with FRESH_RATIO+ left instead.
RATER_FOOD_BUYERS: Final = ("buyer-02", "dyishappy")
RATER_FOOD_HOURS: Final = 3.0
FRESH_OPEN_HOLD: Final = 40
# dyishappy got B or C for 57 of 77 food deals in 3 h (Gdf79cd07 10, G7fa48eaa 7,
# ...). The SKUs it got B/C for in DYIS_SHORT_HOURS lead the food lines, most first,
# up to DYIS_LINES a window (FRESH_OPEN_HOLD fresh units, 20 a window); off when
# the shop holds DYIS_STOP_UNITS units or more.
DYIS_SHORT_HOURS: Final = 2.0
DYIS_LINES: Final = 5  # 3 until 18:2x (owner: stars 4.395); 3 under DYIS_FULL_AB
DYIS_LINES_LOW: Final = 3
DYIS_FULL_AB: Final = 250
DYIS_FRESH_HOLD: Final = 60  # fresh (0.75+) units per dyishappy SKU (40 until 18:2x)
# 12:14 judged (10:12 deploy): dyishappy got A for 33 of 40 food deals (82 %, from
# 26 %). 12:18 (owner): stays on; the food sales dip came from buyer-02 and B sales
# while food A+B rose 343 -> 461 and food margin moved 0.36M -> 0.35M/h. It drops to
# 2 lines (DYIS_LINES) only if food margin falls under 0.25M/h or food A+B under 250.
DYIS_LINES_ON: Final = True
DYIS_STOP_UNITS: Final = 7500  # 6000 until 13:2x (max_units 8000)
NEW_SKU_CASH_CHECK_UNITS: Final = 1000  # the cash stop is for a shop of our size
# 11/16 19:1x (owner): 4 -> 6. New-buyer SKUs met "none in stock" 20-28 times in 3 h
# (G1ee4e4f8, G124d3ea5) and food A+B fell 473 -> 319; back to 4 under 250 A+B.
RATER_LINES: Final = 6
RATER_FOOD_RESERVE: Final = 2
# Coverage (owner 11/13): a buyer's shops.json lists a shop's offers only for SKUs
# its order accepts, so a busy category we hold nothing in brings us no visits.
# On 11/13 08:30, 16 categories with 200+ market settles a day held nothing (the
# long-life food ones alone about 8,000 settles, 11% of the market) while
# replenishment used every line. Each run first seeds up to COVERAGE_LINES empty
# categories with COVERAGE_MIN_SETTLES+ settles in 24 h, TRIAL_QTY units each.
# 11/13 12:20 (owner: invest more, cash 754M idle): filling 9 empty categories
# raised our visits from 80-105 to 176 an hour, so breadth now continues into thin
# categories too. A category is held to one distinct SKU per
# COVERAGE_SETTLES_PER_SKU market settles a day (at most COVERAGE_MAX_SKUS); at
# 12:20 we held 15 of 129 selling kimchi SKUs (10,617 settles a day), 3 of 46
# grain and 1 of 54 sauce SKUs. The thinnest category (held / target) goes first.
# 11/13 17:0x owner: "새상품종류 그냥 20개로 확늘려서 차차 조율해". The kit takes at
# most MAX_LINES (10) lines a run, so new SKUs get 8 and depth and replenishment
# of what sells keep at least 2 (owner: don't stop buying what sells).
COVERAGE_LINES: Final = 3  # 4 until 11/13 17:0x, 8 until 21:0x
# 11/13 21:0x: with sold-out food (up to 6) and new SKUs (8) first, the market's top
# goods by takings got almost no lines: we held 2 A/B units of G021aae64 against a
# target of 20, 5/11 of G00327e80, 6/14 of G03d0199f, 5/13 of G05277ffe and bought
# none of three of them in 3 h, while Lee-sungsoo's takings tripled on such goods.
# A run now goes: sold-out SKUs (at most SOLD_OUT_LINES), depth (DEPTH_FIRST_LINES),
# new SKUs (COVERAGE_LINES), other replenishment, then any depth left.
SOLD_OUT_LINES: Final = 3
# 11/14 00:0x: pasta sauce G8e05d8be (18 sold in 12 h) sat at 1 unit from 21:46 and
# missed 16 asks in an hour; it was not "sold out", so it waited behind new SKUs and
# the food line cap. SKUs we sold lately and hold LOW_STOCK_UNITS or fewer of now
# count as sold out.
LOW_STOCK_UNITS: Final = 2
DEPTH_FIRST_LINES: Final = 4
# Owner 11/13 16:1x: "don't stop buying what sells; clear old stock too". Goods we
# have held AGED_GOODS_HOURS without selling (at 16:13: 109 units, 55.7M of SKUs
# with no sale in 24 h) no longer count toward max_units, so they cannot crowd out
# SKUs that sell; the seller prices them down to cost to clear them.
AGED_GOODS_HOURS: Final = 48.0
COVERAGE_MIN_SETTLES: Final = 100
COVERAGE_SETTLES_PER_SKU: Final = 100  # 200 until 11/13 17:0x
COVERAGE_MAX_SKUS: Final = 60  # 40 until 11/13 17:0x
# An explicitly named trial cannot grow through another replenishment path.
MAX_PROBE_UNITS: Final = 3
RATING_PROBE_MAX_PRICE: Final = 50_000
MAX_RATING_PROBE_SKUS: Final = 2
# Per-SKU economics from the manager (net per unit after cost and refunds, our
# sales in 12 h): a SKU losing money after MIN_SALES_FOR_ECON sales is not
# rebought; one in the top quarter of net that turns within two hours gets
# BOOST times its target.
ECONOMICS_FILE: Final = "r3-sku-economics.json"
MIN_SALES_FOR_ECON: Final = 3
BOOST: Final = 1.5


class RestockState(FrozenModel):
    """What this hook remembers between runs (its own file, not the kit's)."""

    first_run_at: float = 0.0
    units_seen: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    last_sold_at: dict[str, float] = Field(default_factory=dict)
    planned_cost: int = 0
    # our own sales per SKU (timestamps, last OWN_RATE_WINDOW), for replenish depth
    sold_log: dict[str, list[float]] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DemandSignal:
    """Settled-deal counts from the public ledger, when the collector has them.

    ``hot_categories`` and ``cold_categories`` come from the scout's picture: the
    model read forum requests and settlement counts and named categories from the
    market's own demand list (anything else was dropped before it got here).
    """

    by_category: Mapping[str, int]
    by_sku: Mapping[str, int]
    hot_categories: frozenset[str] = frozenset()
    cold_categories: frozenset[str] = frozenset()


def load_state(path: Path) -> RestockState:
    """Read the hook's own state; corrupt or missing means a fresh start."""
    try:
        return RestockState.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        return RestockState()


def save_state(path: Path, state: RestockState) -> None:
    """Write the state atomically."""
    tmp = path.with_suffix(".tmp")
    _ = tmp.write_text(state.model_dump_json(), encoding="utf-8")
    _ = tmp.replace(path)


def sku_fits(item: CatalogItem, policy: RestockPolicy) -> bool:
    """Risk filter: long-life food in a mid price band, mid-priced goods."""
    if item.kind == "food":
        return (
            (item.shelf_hours or 0)
            >= max(policy.min_food_shelf_hours, OWNER_MIN_FOOD_SHELF_HOURS)
            and policy.food_min_list_price
            <= item.list_price
            <= policy.food_max_list_price
        )
    return policy.goods_min_list_price <= item.list_price <= policy.goods_max_list_price


def stock_budget(balance: int, stock_cost: int, policy: RestockPolicy) -> int:
    """Return the most stock (at cost) to hold: a share of equity, never a fixed cap.

    Equity is cash plus stock at cost, so the budget grows as the shop earns and
    shrinks after losses; ``seed_budget`` is the floor while the shop is small.
    The cash reserve still applies separately.
    """
    return max(policy.seed_budget, round(policy.stock_share * (balance + stock_cost)))


def category_scores(
    demand: tuple[DemandRow, ...],
    catalog: dict[str, CatalogItem],
    signal: DemandSignal | None,
    policy: RestockPolicy,
) -> list[tuple[float, DemandRow]]:
    """Rank categories: settled deals, scaled for goods by typical list price."""
    fitting: dict[str, list[int]] = {}
    for item in catalog.values():
        if sku_fits(item, policy):
            fitting.setdefault(item.category, []).append(item.list_price)
    scored: list[tuple[float, DemandRow]] = []
    for row in demand:
        prices = sorted(fitting.get(row.category, ()))
        candidates = len(prices)
        if candidates == 0:
            continue
        base = 1.0 if row.kind == "goods" else 1.5
        if signal is not None:
            base += 2.0 * signal.by_category.get(row.category, 0)
            if row.category in signal.hot_categories:
                base += 1.5
            if row.category in signal.cold_categories:
                base *= 0.5
        if row.kind == "goods":
            base *= max(1.0, prices[candidates // 2] / VALUE_UNIT)
        scored.append((base * min(1.0, candidates / 5.0), row))
    scored.sort(key=lambda pair: -pair[0])
    return scored


def pick_sku(  # noqa: PLR0913 - explicit inputs
    category: str,
    catalog: dict[str, CatalogItem],
    signal: DemandSignal | None,
    policy: RestockPolicy,
    *,
    rng: random.Random,
    already: set[str],
    held_prices: Mapping[str, list[int]] | None = None,
) -> CatalogItem | None:
    """Choose a SKU: the settled one with the most takings.

    With a market signal, a category with no SKU that sells is skipped: random
    picks bought 10-unit lots nobody wanted (11/4). Without one, a random fit.
    """
    fitting = [
        item
        for item in catalog.values()
        if item.category == category
        and sku_fits(item, policy)
        and item.sku not in already
    ]
    if not fitting:
        return None
    if signal is not None:
        settled = [
            i for i in fitting if signal.by_sku.get(i.sku, 0) >= MIN_MARKET_SETTLES
        ]
        if not settled:
            return None
        # expected takings, not settle count: a dear SKU that sells beats a
        # cheap one that sells a little more often
        held = (held_prices or {}).get(category, [])

        def overlap(item: CatalogItem) -> float:
            near = any(
                abs(item.list_price - price) <= BAND_OVERLAP * max(price, 1)
                for price in held
            )
            return 0.5 if near else 1.0

        return max(
            settled,
            key=lambda i: (
                signal.by_sku.get(i.sku, 0) * i.list_price * overlap(i),
                i.list_price,
            ),
        )
    return rng.choice(fitting)


def depth_targets(
    catalog: dict[str, CatalogItem],
    signal: DemandSignal | None,
    policy: RestockPolicy,
) -> list[tuple[CatalogItem, int]]:
    """Top goods SKUs by market takings and how many units of each to hold."""
    if signal is None:
        return []
    selling = [
        item
        for item in catalog.values()
        if item.kind == "goods"
        and sku_fits(item, policy)
        and signal.by_sku.get(item.sku, 0) >= MIN_MARKET_SETTLES
    ]
    selling.sort(key=lambda i: -signal.by_sku.get(i.sku, 0) * i.list_price)
    return [
        (
            item,
            max(
                2,
                min(
                    policy.units_per_sku,
                    signal.by_sku.get(item.sku, 0) // DEPTH_SETTLES,
                ),
            ),
        )
        for item in selling[: policy.depth_skus]
    ]


# 11/13 22:0x: one warranty break in nine G021aae64 sales (the market's top seller,
# 1,427 a day) put its net at -3,672 a unit (0.7 % of list) and stopped all restock
# of it. With a list price the stop now needs a loss beyond ECON_LOSS_SHARE of list
# (G00327e80 at -281,479, 12.5 % of list, still stops).
ECON_LOSS_SHARE: Final = 0.02


def economic_target(
    sku: str,
    target: int,
    economics: Mapping[str, tuple[float, int]],
    held: int,
    list_price: int = 0,
) -> int:
    """Adjust a stock target by the SKU's net per unit and how fast it turns."""
    net, sold_12h = economics.get(sku, (0.0, 0))
    floor = -ECON_LOSS_SHARE * list_price if list_price > 0 else 0.0
    if sold_12h >= MIN_SALES_FOR_ECON and net <= floor:
        return 0
    nets = sorted(n for n, k in economics.values() if k >= MIN_SALES_FOR_ECON)
    if not nets or sold_12h < MIN_SALES_FOR_ECON:
        return target
    top = nets[(3 * (len(nets) - 1)) // 4]
    turns_fast = sold_12h / 12 >= held / 2
    return math.ceil(target * BOOST) if net >= top and turns_fast else target


def fast_goods_cap(sold_12h: int, cap: int, net_per_unit: float = 0.0) -> int:
    """A/B cap of a goods SKU: about 4 h of sales for fast sellers (owner 11/15 17:0x).

    16:51: 11 goods SKUs sold 3+ in 12 h held under 2 h of sales in A/B (127M of
    12 h takings). At FAST_GOODS_SALES or more settled in 12 h the cap is a third of
    that, at most FAST_GOODS_CAP; other SKUs keep ``cap``.

    11/15 22:0x (owner, Codex): grade A goods lost 2.82M in mature 12 h sales (a
    Simmons LK A -0.84M on 10, a Lacloud Q A -1.10M on 6; a sofa held 20 sold 1 in
    6 h). A SKU losing money per unit, or selling SLOW_GOODS_SALES or fewer in 12 h
    (about 2 in 6 h), holds about 2 x its 6 h sales (its 12 h sales, at least
    SELL_COVER_MIN, never above ``cap``); held stock still sells.
    """
    if net_per_unit < 0 or sold_12h <= SLOW_GOODS_SALES:
        return min(cap, max(SELL_COVER_MIN, sold_12h))
    if sold_12h < FAST_GOODS_SALES:
        return cap
    return max(cap, min(FAST_GOODS_CAP, math.ceil(sold_12h / 3)))


def c_sales(
    ledger: Path, shop: str, now: float, hours: float = FOOD_C_HOURS
) -> dict[str, int] | None:
    """Return our settled grade C sales per SKU from the ledger, None if unread.

    11/15 11:4x (owner, Codex): settles, not receipts. In 12 h our food C receipts
    were 611 but settles 274 (A 784/610, B 1,537/1,256); counting receipts let
    unpaid C look sold. ``sold_12h`` in the manager's economics is settles too.
    """
    if not shop:
        return None
    try:
        con = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = con.execute(
            "SELECT sku, count(*) FROM rows WHERE kind = 'settle' AND ts >= ?"
            " AND rtrim(shop, '/') = ?"
            " AND coalesce(grade, json_extract(data, '$.grade')) = 'C'"
            " GROUP BY sku",
            (now - hours * 3600, shop.rstrip("/")),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return {str(sku): int(n) for sku, n in rows}


def plan(  # noqa: PLR0913, PLR0912, PLR0915, PLR0917, C901 - one explicit pass
    inventory: Inventory,
    catalog: dict[str, CatalogItem],
    demand: tuple[DemandRow, ...],
    policy: RestockPolicy,
    state: RestockState,
    now: float,
    signal: DemandSignal | None = None,
    economics: Mapping[str, tuple[float, int]] | None = None,
    growth: GrowthEvidence | None = None,
    stockouts: Mapping[str, tuple[float, float]] | None = None,
    rater_skus: tuple[str, ...] = (),
    c_sold: Mapping[str, int] | None = None,
    fresh_skus: Mapping[str, float] | None = None,
    fresh_needs: Mapping[str, float] | None = None,
    fresh_open: frozenset[str] = frozenset(),
) -> tuple[list[dict[str, object]], RestockState]:
    """Return the kit's ``buy`` lines and the updated hook state.

    ``c_sold`` (our grade C sales per SKU in FOOD_C_HOURS) turns on the food C
    check; None (not read) leaves food targets as before.
    """
    econ = economics or {}
    if inventory.round < 3:  # noqa: PLR2004 - the market's economy round
        return [], state
    first_run = state.first_run_at or now
    seen_now: dict[str, list[str]] = {}
    for unit in inventory.units:
        seen_now.setdefault(unit.sku, []).append(unit.unit)
    sold_at = dict(state.last_sold_at)
    sold_log = {
        sku: [t for t in stamps if now - t <= OWN_RATE_WINDOW]
        for sku, stamps in state.sold_log.items()
    }
    for sku, previous in state.units_seen.items():
        gone = set(previous) - set(seen_now.get(sku, ()))
        if gone:
            sold_at[sku] = now
            sold_log.setdefault(sku, []).extend([now] * len(gone))
    stock_cost = sum(u.cost for u in inventory.units)
    # A single retained-cash floor covers operating and pending-refund needs.
    # The stock-share limit independently retains cash; do not subtract its
    # retained share again or add two reserves covering the same cash balance.
    reserve = max(policy.reserve_cash, growth.refund_reserve if growth else 0)
    cash = inventory.balance - reserve
    budget = round(stock_budget(inventory.balance, stock_cost, policy) * buy_share(now))
    lines: list[dict[str, object]] = []
    spent = 0
    food_lines = 0
    food_cap = round(MAX_LINES * policy.food_share)
    surge = {entry.sku: entry.factor for entry in inventory.surge}
    stocked = {u.sku for u in inventory.units}
    stocked_categories: dict[str, int] = {}
    for unit in inventory.units:
        stocked_categories[unit.category] = stocked_categories.get(unit.category, 0) + 1

    def affordable(item: CatalogItem, qty: int, *, own_cap: int | None = None) -> int:
        """Return the quantity the reserve and stock caps allow (0 when none).

        ``own_cap`` (fresh list lines) replaces the A/B SKU cap with its own.
        """
        nonlocal spent
        unit_cost = math.ceil(
            item.list_price * WORST_CASE_COST_SHARE * surge.get(item.sku, 1.0)
        )
        if unit_cost <= 0:
            return 0
        room = min(cash - spent, budget - stock_cost - spent)
        planned_qty = sum(
            int(str(line["qty"])) for line in lines if line["sku"] == item.sku
        )
        sku_cap = MAX_PROBE_UNITS if item.sku in policy.probe_skus else 20
        if item.sku in rater_skus:
            sku_cap = min(
                20,
                max(
                    RATER_TRIAL_QTY,
                    SELL_COVER_FACTOR * econ.get(item.sku, (0.0, 0))[1],
                ),
            )
        elif econ and item.kind == "goods" and item.sku not in policy.probe_skus:
            sold_12h = econ.get(item.sku, (0.0, 0))[1]
            sku_cap = min(
                fast_goods_cap(sold_12h, sku_cap, econ.get(item.sku, (0.0, 0))[0]),
                max(SELL_COVER_MIN, SELL_COVER_FACTOR * sold_12h),
            )
            piled = held_c.get(item.sku, 0)
            if sold_12h < C_PILE_EXEMPT_SALES and piled >= C_PILE:
                sku_cap -= piled // 2
        allowed = min(
            qty,
            # Grade C units do not take the SKU cap either (G021aae64, 11/13 21:1x).
            sku_cap - held_ab.get(item.sku, 0) - planned_qty
            if own_cap is None
            else own_cap - planned_qty,
            room // unit_cost,
            policy.max_units - units_total,
        )
        if c_sold is not None and item.kind == "food":
            excess = held_c_food.get(item.sku, 0) - c_sold.get(item.sku, 0)
            if excess > 0:
                floor = (
                    MIN_SALES_TARGET
                    if held_ab.get(item.sku, 0) == 0 and planned_qty == 0
                    else 0
                )
                allowed = min(allowed, max(allowed - excess, floor))
        if allowed <= 0:
            return 0
        spent += unit_cost * allowed
        return allowed

    units_total = sum(
        1
        for unit in inventory.units
        if unit.kind != "goods" or now - unit.supplied_at < AGED_GOODS_HOURS * 3600
    )
    # 11/13 20:3x: grade C units no longer count as held for targets. Buyers' kits
    # pay for A or B, so 17 C units of G021aae64 (the market's top seller, 221
    # sold in 3 h; Lee-sungsoo sold 50, we 3) filled its depth target and we bought
    # none of it for 6 h. C units still sell to buyers that accept C.
    held: dict[str, int] = {}
    for unit in inventory.units:
        if unit.grade != "C":
            held[unit.sku] = held.get(unit.sku, 0) + 1
    held_ab = dict(held)
    held_c: dict[str, int] = {}
    held_c_food: dict[str, int] = {}
    for unit in inventory.units:
        if unit.grade == "C" and unit.kind == "goods":
            held_c[unit.sku] = held_c.get(unit.sku, 0) + 1
        elif unit.grade == "C" and unit.kind == "food":
            held_c_food[unit.sku] = held_c_food.get(unit.sku, 0) + 1
    # Owner-selected low-price goods get one of the kit's ten serial lines.
    # Keep the ordinary price filters intact; this is a bounded, named pilot.
    rating_probes = [
        item
        for sku in policy.probe_skus
        if (item := catalog.get(sku)) is not None
        and item.kind == "goods"
        and 0 < item.list_price < RATING_PROBE_MAX_PRICE
        and item.list_price < policy.goods_min_list_price
        and item.list_price <= policy.goods_max_list_price
        and signal is not None
        and signal.by_sku.get(sku, 0) >= MIN_MARKET_SETTLES
    ][:MAX_RATING_PROBE_SKUS]
    rating_probes.sort(key=lambda item: held.get(item.sku, 0))
    for item in rating_probes:
        if growth is not None and policy.growth_enabled:
            if not growth.available:
                break
            row = growth.by_sku.get(item.sku)
            if (
                row is not None
                and row.mature_sales >= policy.min_mature_sales
                and row.mature_net is not None
                and row.mature_net <= 0
            ):
                continue
        if economic_target(item.sku, MAX_PROBE_UNITS, econ, held.get(item.sku, 0)) <= 0:
            continue
        qty = affordable(item, MAX_PROBE_UNITS - held.get(item.sku, 0))
        if qty:
            lines.append({"sku": item.sku, "qty": qty})
            units_total += qty
            break
    if growth is not None and policy.growth_enabled:
        fitting = [item for item in catalog.values() if sku_fits(item, policy)]
        market_targets = {
            item.sku: target for item, target in depth_targets(catalog, signal, policy)
        }

        def add_target(item: CatalogItem, target: int) -> None:
            """Spend shared constraints once and update coverage for later probes."""
            nonlocal units_total, food_lines
            if len(lines) >= MAX_LINES or units_total >= policy.max_units:
                return
            if item.kind == "food" and food_lines >= food_cap:
                return
            qty = affordable(item, target - held.get(item.sku, 0))
            if qty:
                lines.append({"sku": item.sku, "qty": qty})
                held[item.sku] = held.get(item.sku, 0) + qty
                units_total += qty
                if item.kind == "food":
                    food_lines += 1

        for target in proven_targets(
            fitting, policy, growth, market_targets, held, surge=surge
        ):
            add_target(target.item, target.total)
        for target in probe_targets(
            fitting,
            policy,
            growth,
            signal.by_sku if signal is not None else None,
            held,
            categories=frozenset(row.category for row in demand),
        ):
            if len(lines) >= min(MAX_LINES, policy.categories_per_run):
                break
            add_target(target.item, target.total)
    else:
        # 0. Depth: keep the market's best sellers on the shelf, emptiest first (the
        # kit takes at most MAX_LINES lines a run, so order is priority).
        wanted = {
            item.sku: (item, target)
            for item, target in depth_targets(catalog, signal, policy)
        }
        # Stockouts: SKUs whose market sales went on while we held none get an
        # hour of our own sales, on or off the depth list (owner 11/5).
        for sku, (per_h, share) in (stockouts or {}).items():
            item = catalog.get(sku)
            if item is None or item.kind != "goods" or not sku_fits(item, policy):
                continue
            need = stockout_target(per_h, share, policy.units_per_sku)
            current = wanted.get(sku)
            wanted[sku] = (item, max(need, current[1] if current else 0))
        depth = [
            (
                item,
                economic_target(
                    item.sku, target, econ, held.get(item.sku, 0), item.list_price
                ),
            )
            for item, target in wanted.values()
        ]
        depth.sort(key=lambda pair: held.get(pair[0].sku, 0) / max(1, pair[1]))

        def deepen(limit: int) -> None:
            """Top up depth SKUs, emptiest first, at most ``limit`` new lines."""
            nonlocal units_total
            start = len(lines)
            for item, target in depth:
                if (
                    len(lines) >= MAX_LINES
                    or len(lines) - start >= limit
                    or units_total >= policy.max_units
                ):
                    break
                planned = next((ln for ln in lines if ln["sku"] == item.sku), None)
                if planned is not None:
                    # A fresh-list line (bought for freshness first) still grows to
                    # the SKU's depth target (11/15 15:2x: Gdf79cd07 fell 15 -> 6).
                    if item.sku in fresh_planned:
                        more = affordable(
                            item,
                            target - held.get(item.sku, 0) - int(str(planned["qty"])),
                        )
                        planned["qty"] = int(str(planned["qty"])) + more
                        units_total += more
                    continue
                # 11/16 10:2x (owner, Codex 10:16 repro): depth listed 16 goods SKUs
                # we neither held nor ever sold while new goods were paused; such a
                # SKU is a new one and obeys the same stop. Held or sold SKUs refill.
                if (
                    item.kind == "goods"
                    and not new_goods_ok
                    and held.get(item.sku, 0) <= 0
                    and item.sku not in sold_at
                    and econ.get(item.sku, (0.0, 0))[1] <= 0
                ):
                    continue
                qty = affordable(item, target - held.get(item.sku, 0))
                if qty:
                    lines.append({"sku": item.sku, "qty": qty})
                    units_total += qty

        # 0. Sold out first (owner 11/13 17:3x, "못판거없는지도 봐서"): in the 20 min
        # after coverage took 8 of 10 lines, 21 asks met "none in stock", 18 of
        # them for SKUs we had sold out (water, rice, pasta sauce; the pillow
        # G575cddb8 is a named rating probe and keeps its cap of 3). SKUs we sold
        # recently and now hold none of are rebought before new SKUs take lines.
        def replenish(*, sold_out_only: bool, limit: int = MAX_LINES) -> None:  # noqa: C901 - one pass
            """Rebuy what sold; sold-out SKUs first (owner 11/13 17:3x)."""
            nonlocal units_total, food_lines
            start = len(lines)
            # 11/16 06:4x (owner): in 4.5 h of 173 runs the top food sellers got
            # few lines (G32bf9a8e 177 sold in 12 h: 2 lines, Ge48ed59c 127: 0)
            # while recency decided the order; our 12 h settled sales go first now.
            for sku, stamp in sorted(
                sold_at.items(), key=lambda kv: (-econ.get(kv[0], (0.0, 0))[1], -kv[1])
            ):
                if (
                    len(lines) >= MAX_LINES
                    or len(lines) - start >= limit
                    or units_total >= policy.max_units
                ):
                    break
                if now - stamp > policy.stale_hours * 3600:
                    continue
                if sold_out_only and held.get(sku, 0) > LOW_STOCK_UNITS:
                    continue
                item = catalog.get(sku)
                if item is None or not sku_fits(item, policy):
                    continue
                if item.kind == "food" and food_lines >= food_cap:
                    continue
                rate = len(sold_log.get(sku, ()))
                sold_12h = econ.get(sku, (0.0, 0))[1]
                if item.kind == "food":
                    sold_12h = math.ceil(sold_12h * FOOD_SALES_SHARE)
                floor = (
                    max(MIN_SALES_TARGET, sold_12h) if econ else policy.replenish_qty
                )
                unit_cap = (
                    fast_goods_cap(econ[sku][1], policy.units_per_sku, econ[sku][0])
                    if item.kind == "goods" and sku in econ
                    else policy.units_per_sku
                )
                target = economic_target(
                    sku,
                    min(unit_cap, max(floor, math.ceil(rate * COVER_HOURS))),
                    econ,
                    held.get(sku, 0),
                    item.list_price,
                )
                # A SKU already on a depth line gets the larger target.
                planned = next((line for line in lines if line["sku"] == sku), None)
                if planned is not None:
                    more = affordable(
                        item, target - held.get(sku, 0) - int(str(planned["qty"]))
                    )
                    if more:
                        planned["qty"] = int(str(planned["qty"])) + more
                        units_total += more
                    continue
                qty = affordable(item, target - held.get(sku, 0))
                if qty:
                    lines.append({"sku": sku, "qty": qty})
                    units_total += qty
                    if item.kind == "food":
                        food_lines += 1

        replenish(sold_out_only=True, limit=SOLD_OUT_LINES)
        fresh_planned: set[str] = set()
        # Fresh list (r3/fresh_list.py). A listed SKU short of fresh units gets a
        # full line each supply window whatever its total held (16:07 version: a 3 h
        # hold cap blocked it, G7fa48eaa 79 held, 1 fresh), largest shortfall first,
        # until FRESH_MAX_HELD of one SKU or FRESH_MAX_COST in all listed SKUs.
        # 11/15 22:0x (owner): FRESH_GUARANTEED_LINES of the food lines go to it right
        # after the sold-out lines (12 of 19 listed SKUs were short when it only got
        # what the totato/q00/bjeon01 lines left); more come after those lines.
        fresh_rows: list[tuple[int, CatalogItem, int, float, int]] = []
        listed_cost = 0
        for sku, per_hour in (fresh_skus or {}).items():
            item = catalog.get(sku)
            mine = [u for u in inventory.units if u.sku == sku]
            # 11/17 10:5x (owner): only fresh units (FRESH_RATIO+ left) count toward
            # FRESH_MAX_COST; stale stock was 10.07M and blocked every fresh line.
            listed_cost += sum(
                u.cost
                for u in mine
                if item is not None
                and (r := u.shelf_left_ratio(now, item.shelf_hours)) is not None
                and r >= FRESH_RATIO
            )
            if (
                item is None
                or item.kind != "food"
                or not sku_fits(item, policy)
                or (len(mine) >= FRESH_MAX_HELD and sku not in fresh_open)
            ):
                continue
            cap = FRESH_LINE_QTY
            if sku in fresh_open:
                fresh75 = sum(
                    1
                    for u in mine
                    if (r := u.shelf_left_ratio(now, item.shelf_hours)) is not None
                    and r >= FRESH_RATIO
                )
                cap = min(FRESH_LINE_QTY, FRESH_OPEN_HOLD - fresh75)
                if cap <= 0:
                    continue
            need_h = (fresh_needs or {}).get(sku)
            if need_h is not None:
                lasting = need_h + NEED_MARGIN_HOURS
                fresh = sum(
                    1
                    for u in mine
                    if u.expires_at is None or u.expires_at - now >= lasting * 3600
                )
            else:
                fresh = sum(
                    1
                    for u in mine
                    if (r := u.shelf_left_ratio(now, item.shelf_hours)) is not None
                    and r >= FRESH_RATIO
                )
            gap = fresh_need(per_hour) - fresh
            if gap > 0:
                fresh_rows.append((gap, item, fresh, per_hour, cap))
        # None lasting first, then buyer-02's buying per hour, then the shortfall.
        fresh_rows.sort(key=lambda row: (row[2] > 0, -row[3], -row[0]))
        fresh_lines = 0

        def fresh_fill(limit: int) -> None:
            """Give fresh-list SKUs lines until ``limit`` fresh lines in this run."""
            nonlocal units_total, food_lines, listed_cost, fresh_lines
            for _, item, _, _, cap in fresh_rows:
                if (
                    fresh_lines >= limit
                    or len(lines) >= MAX_LINES
                    or food_lines >= food_cap
                    or units_total >= policy.max_units
                    or listed_cost >= FRESH_MAX_COST
                ):
                    return
                if item.sku in fresh_planned:
                    continue
                planned = next((ln for ln in lines if ln["sku"] == item.sku), None)
                if planned is not None:
                    more = affordable(
                        item,
                        min(cap, FRESH_LINE_QTY - int(str(planned["qty"]))),
                        own_cap=20,
                    )
                    planned["qty"] = int(str(planned["qty"])) + more
                    units_total += more
                    listed_cost += more * item.list_price * WORST_CASE_COST_SHARE
                    fresh_planned.add(item.sku)
                    continue
                qty = affordable(item, cap, own_cap=20)
                if qty:
                    lines.append({"sku": item.sku, "qty": qty})
                    units_total += qty
                    food_lines += 1
                    fresh_lines += 1
                    listed_cost += qty * item.list_price * WORST_CASE_COST_SHARE
                    fresh_planned.add(item.sku)

        fresh_fill(FRESH_GUARANTEED_LINES)
        held_units = len(inventory.units)
        new_skus_ok = held_units < NEW_SKU_STOP_UNITS and (
            held_units < NEW_SKU_CASH_CHECK_UNITS
            or inventory.balance >= NEW_SKU_STOP_CASH
        )
        new_goods_ok = (
            new_skus_ok
            and not NEW_GOODS_PAUSED
            and (
                sum(u.cost for u in inventory.units if u.kind == "goods")
                <= NEW_GOODS_STOP_COST
            )
        )
        if new_skus_ok and (ROUND_END - now) / 3600 >= NAMED_FOOD_MIN_HOURS_LEFT:
            for sku in NAMED_FOOD_SKUS:
                item = catalog.get(sku)
                if (
                    item is None
                    or item.kind != "food"
                    or not sku_fits(item, policy)
                    or food_lines >= food_cap
                    or len(lines) >= MAX_LINES
                    or any(line["sku"] == sku for line in lines)
                ):
                    continue
                sold = econ.get(sku, (0.0, 0))[1]
                target = max(TRIAL_QTY, math.ceil(sold * FOOD_SALES_SHARE))
                qty = affordable(item, target - held.get(sku, 0))
                if qty:
                    lines.append({"sku": sku, "qty": qty})
                    units_total += qty
                    food_lines += 1
        rater_rows = [
            item
            for sku in rater_skus
            if (item := catalog.get(sku)) is not None
            and not any(line["sku"] == sku for line in lines)
            and (
                held.get(sku, 0) > 0
                or (new_goods_ok if item.kind == "goods" else new_skus_ok)
            )
            and (
                sku_fits(item, policy)
                if item.kind == "food"
                else 0 < item.list_price <= policy.goods_max_list_price
            )
        ]

        # The rater buyers' own SKUs first, the bjeon01 extras after them.
        # 11/16 16:3x: a new buyer's SKU we hold none of gets its trial first.
        # 19:1x: and while it holds under MAX_LINE_QTY (G1ee4e4f8 met "none in stock"
        # 28 times in 3 h holding 7), since food lines here are capped by food_cap.
        # 22:0x: within that group the emptiest SKU goes first; the two rice SKUs
        # are also in EXTRA_SKUS and sorted behind the rest, so they never got a
        # line (0 supply attempts since 16:43 with about 4 food lines a run).
        def rater_key(item: CatalogItem) -> tuple[bool, bool, int]:
            have = held.get(item.sku, 0)
            new_low = item.sku in NEW_BUYER_SKUS and have < MAX_LINE_QTY
            return (not new_low, not new_low and item.sku in EXTRA_SKUS, have)

        rater_rows.sort(key=rater_key)
        added = 0
        for item in rater_rows:
            if added >= RATER_LINES or len(lines) >= MAX_LINES:
                break
            # 11/16 19:1x: with 6 rater lines, RATER_FOOD_RESERVE food lines stay for
            # the fresh list and the sold-out refill that follow.
            if item.kind == "food" and food_lines >= food_cap - RATER_FOOD_RESERVE:
                continue
            sold = econ.get(item.sku, (0.0, 0))[1]
            want = (
                math.ceil(sold * FOOD_SALES_SHARE)
                if item.kind == "food"
                else SELL_COVER_FACTOR * sold
            )
            # 11/14 23:4x: food C sells too, so a listed food SKU counts every
            # grade it holds (G010744b7 held C34/B12/A4 against a target of 17
            # and was still bought 39 units in 3 h).
            have = (
                sum(1 for u in inventory.units if u.sku == item.sku)
                if item.kind == "food"
                else held.get(item.sku, 0)
            )
            qty = affordable(item, min(20, max(RATER_TRIAL_QTY, want)) - have)
            if qty:
                lines.append({"sku": item.sku, "qty": qty})
                units_total += qty
                added += 1
                if item.kind == "food":
                    food_lines += 1
        fresh_fill(FRESH_LINES)
        deepen(DEPTH_FIRST_LINES)
        # 0a. Coverage first (owner 11/13 12:20): busy categories we hold nothing in
        # or too few SKUs of take up to COVERAGE_LINES before depth can fill the run.
        held_skus: dict[str, set[str]] = {}
        cover_prices: dict[str, list[int]] = {}
        for unit in inventory.units:
            held_skus.setdefault(unit.category, set()).add(unit.sku)
            cover_prices.setdefault(unit.category, []).append(unit.list_price)
        thin: list[tuple[float, int, DemandRow]] = []
        for _, row in (
            category_scores(demand, catalog, signal, policy) if new_skus_ok else ()
        ):
            settles = signal.by_category.get(row.category, 0) if signal else 0
            if settles < COVERAGE_MIN_SETTLES or (
                row.kind == "goods" and not new_goods_ok
            ):
                continue
            target = min(
                COVERAGE_MAX_SKUS, math.ceil(settles / COVERAGE_SETTLES_PER_SKU)
            )
            have = len(held_skus.get(row.category, ()))
            if have < target:
                thin.append((have / target, -settles, row))
        thin.sort(key=lambda entry: (entry[0], entry[1]))
        covered = 0
        for _, _, row in thin:
            if (
                covered >= COVERAGE_LINES
                or len(lines) >= MAX_LINES
                or units_total >= policy.max_units
            ):
                break
            if row.kind == "food" and food_lines >= food_cap:
                continue
            item = pick_sku(
                row.category,
                catalog,
                signal,
                policy,
                rng=random.Random(int(now // 3600)),  # noqa: S311 - unused with a signal
                already=set(stocked) | {str(line["sku"]) for line in lines},
                held_prices=cover_prices,
            )
            if item is None:
                continue
            qty = affordable(item, min(TRIAL_QTY, policy.units_per_sku))
            if not qty:
                continue
            lines.append({"sku": item.sku, "qty": qty})
            stocked_categories[row.category] = (
                stocked_categories.get(row.category, 0) + qty
            )
            units_total += qty
            covered += 1
            if item.kind == "food":
                food_lines += 1
        # 1. Replenish what just sold, while demand for it is fresh.
        replenish(sold_out_only=False)
        deepen(MAX_LINES)
        # 2. Breadth: new categories, long-life food and mid-priced goods first.
        rng = random.Random(int(now // 3600))  # noqa: S311 - variety, not security
        chosen_skus = set(stocked) | {str(line["sku"]) for line in lines}
        held_prices: dict[str, list[int]] = {}
        for unit in inventory.units:
            held_prices.setdefault(unit.category, []).append(unit.list_price)
        # 11/15 10:2x (Codex stock review): breadth only starts SKUs we hold none
        # of, so it obeys the same new-SKU stops as coverage (2,800 units held).
        for _, row in (
            category_scores(demand, catalog, signal, policy) if new_skus_ok else ()
        ):
            if len(lines) >= min(MAX_LINES, policy.categories_per_run):
                break
            if units_total >= policy.max_units:
                break
            if row.kind == "goods" and not new_goods_ok:
                continue
            if stocked_categories.get(row.category, 0) >= policy.units_per_sku * 2:
                continue
            if row.kind == "food" and food_lines >= food_cap:
                continue
            item = pick_sku(
                row.category,
                catalog,
                signal,
                policy,
                rng=rng,
                already=chosen_skus,
                held_prices=held_prices,
            )
            if item is None:
                continue
            qty = affordable(item, min(TRIAL_QTY, policy.units_per_sku))
            if not qty:
                continue
            lines.append({"sku": item.sku, "qty": qty})
            chosen_skus.add(item.sku)
            stocked_categories[row.category] = (
                stocked_categories.get(row.category, 0) + 1
            )
            units_total += qty
            if item.kind == "food":
                food_lines += 1
    updated = state.model_copy(
        update={
            "first_run_at": first_run,
            "units_seen": {sku: tuple(units) for sku, units in seen_now.items()},
            "last_sold_at": sold_at,
            "sold_log": {k: v for k, v in sold_log.items() if v},
            "planned_cost": spent,
        }
    )
    # 11/15 19:0x: one supply line is at most MAX_LINE_QTY (priority_restock's
    # BuyLine and the market). The 17:04 fast goods cap planned 29 and the whole
    # restock run failed validation (3 runs 18:43-18:45); a larger target now
    # fills over the next runs.
    for line in lines:
        line["qty"] = min(MAX_LINE_QTY, int(str(line["qty"])))
    return lines[:MAX_LINES], updated


class SkuEconomics(KitFile):
    """Legacy manager contribution used only by the non-growth planner."""

    net_per_unit: float = Field(allow_inf_nan=False)
    sold_12h: int = Field(ge=0)


class SkuEconomicsFile(KitFile):
    """Ignore manager metadata while parsing its economics boundary once."""

    skus: dict[str, SkuEconomics] = Field(default_factory=dict)


def load_economics(path: Path) -> dict[str, tuple[float, int]]:
    """Read the manager's per-SKU economics; missing or bad means none."""
    try:
        rows = SkuEconomicsFile.model_validate_json(path.read_bytes()).skus
        return {sku: (row.net_per_unit, row.sold_12h) for sku, row in rows.items()}
    except (OSError, ValidationError):
        return {}


def supply_closed(window: RestockWindow | None, now: float) -> bool:
    """Return True while the supply window stays shut past the kit's retry wait.

    Half the lines printed on 11/4 (237 of 521) hit a closed window and were lost;
    the hook now prints nothing until the window is about to open.
    """
    if window is None or window.open is not False:
        return False
    return window.opens_at - now > WINDOW_LEAD_SECONDS


# 11/17 08:0x (owner 08:07, book defence): 61 goods pass the market-first test (20.0M
# of market margin an hour; top 8 12.1M, 25 18.1M). 25 SKUs; each SKU's hold target
# is its market sales per hour (10-40), at most 20 a run, largest shortfall first.
# Cash stays at MARKET_FIRST_CASH_FLOOR or more and goods cost under
# NEW_GOODS_STOP_COST (market-first lines skip the plan's budget).
MARKET_FIRST_SKUS: Final = 25
# 11/17 08:2x (owner, "more aggressive"): every SKU that passes, not the top 25.
MARKET_FIRST_ALL: Final = True
# A SKU whose units sold FAST_SHARE+ of (held + sold) in the last hour holds twice
# its market sales per hour (up to FAST_HOLD_MAX); one we hold that sold none in
# the last hour gets no more. Fast sellers' medians go to FAST_FILE for the seller
# (accept line max(cost x 1.08, min(median x 0.98, cost x 1.15)), 2 h test).
FAST_SHARE: Final = 0.4
FAST_HOLD_MAX: Final = 60
FAST_FILE: Final = "r3-fast-sellers.json"
FAST_MAX_AGE_SECONDS: Final = 2 * 3600.0
MARKET_FIRST_MIN_SOLD: Final = 20  # market settles in MARKET_FIRST_HOURS
MARKET_FIRST_HOURS: Final = 6.0
# 1.08 until 11/17 10:5x (owner, strategy 10:52: the sofa 1.05 test made 1.93M/h
# against 0.71M/h; 36 A goods at 500k+ sold today, no refund). Today's grade A units
# of these SKUs accept down to max(cost x 1.05, min(median x 0.98, cost x 1.15))
# (r3/seller.py mf_quote; medians in MF_FILE).
MARKET_FIRST_MARGIN: Final = 1.05
MF_FILE: Final = "r3-market-first.json"
ZINUS_MARK: Final = "지누스"  # r3/seller.py zinus_floor
ZINUS_FLOOR: Final = 1.12
# 11/17 11:0x (owner, strategy 11:07, 2 h tests): goods whose market median is
# DEAR_MARGIN x cost or more pass with DEAR_MIN_SOLD settles in 6 h, holding
# DEAR_HOLD at most (DEAR_HOLD a run). SKUs we sold DEEP_SOLD+ of in 2 h with
# DEEP_TODAY_A or fewer of today's A units hold 2 x market per hour, at least
# DEEP_HOLD_MIN (up to FAST_HOLD_MAX).
DEAR_MARGIN: Final = 1.15
DEAR_MIN_SOLD: Final = 8
DEAR_HOLD: Final = 6
DEEP_SOLD: Final = 4
DEEP_TODAY_A: Final = 3
DEEP_HOLD_MIN: Final = 20
DEEP_SINCE: Final = 1794841200.0  # 2026-11-17 00:00 KST
MARKET_FIRST_HOLD_MIN: Final = 10  # units bought in the last 12 h a SKU may hold
MARKET_FIRST_HOLD_MAX: Final = 40
MARKET_FIRST_PER_RUN: Final = 20
MARKET_FIRST_CASH_FLOOR: Final = 100_000_000
# 19:2x (owner): under MF_LOW_CASH cash, market-first buys only SKUs we sold
# MF_LOW_CASH_SOLD+ of in the last 2 h.
MF_LOW_CASH: Final = 150_000_000
MF_LOW_CASH_SOLD: Final = 3
# 11/17 08:0x (owner, 2 h test): goods inextro sold 30 %+ of in the last 3 h (sofas;
# G24f5f979 alone was 8.8M of its 19.4M goods margin) pass at INEXTRO_MARGIN and
# hold at least INEXTRO_HOLD; their units bought today accept down to cost x
# INEXTRO_MARGIN (r3/seller.py inextro_quote). Judged at 10:3x: our sales of them
# per hour 1.5x or more -> keep, else back to 1.08.
INEXTRO_SKUS: Final = frozenset({"G24f5f979", "G6cd0e9d4", "G4a12722c", "G8adbc159"})
INEXTRO_MARGIN: Final = 1.05
INEXTRO_HOLD: Final = 20


def market_first_lines(  # noqa: C901, PLR0912, PLR0913, PLR0915, PLR0917 - one pass
    ledger: Path,
    now: float,
    catalog: Mapping[str, CatalogItem],
    inventory: Inventory,
    sold_log: Mapping[str, list[float]] | None = None,
    fast_out: dict[str, float] | None = None,
    median_out: dict[str, float] | None = None,
) -> list[dict[str, object]]:
    """Goods the market sells most profitably at today's supply cost (owner 07:0x).

    Goods with MARKET_FIRST_MIN_SOLD+ market settles in MARKET_FIRST_HOURS whose
    supply cost (median of all shops' lots, same window, times the current surge)
    x MARKET_FIRST_MARGIN is at most the paid median; the top MARKET_FIRST_SKUS by
    (mean paid - cost) x settles per hour, each topped up to its market sales per
    hour (MARKET_FIRST_HOLD_MIN-MARKET_FIRST_HOLD_MAX) of units bought in the last
    12 h, at most MARKET_FIRST_PER_RUN a run, largest shortfall first. A SKU under a
    surge factor above 1 is skipped this window. INEXTRO_SKUS pass at
    INEXTRO_MARGIN and are always kept.
    """
    since = now - MARKET_FIRST_HOURS * 3600
    paid: dict[str, list[float]] = {}
    cost: dict[str, list[float]] = {}
    try:
        con = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        for sku, amount in con.execute(
            "select sku, amount from rows where kind='settle' and ts>=?"
            " and json_extract(data,'$.expires_at') is null",
            (since,),
        ):
            item = catalog.get(str(sku))
            if item is not None and item.kind == "goods" and amount:
                paid.setdefault(str(sku), []).append(float(amount))
        for sku, qty, total in con.execute(
            "select json_extract(data,'$.sku'), json_extract(data,'$.qty'),"
            " json_extract(data,'$.total') from rows where kind='supply' and ts>=?",
            (since,),
        ):
            if sku in paid and qty and total:
                cost.setdefault(str(sku), []).append(float(total) / float(qty))
    except sqlite3.Error:
        return []
    finally:
        con.close()
    surge = {entry.sku: entry.factor for entry in inventory.surge}
    medians: dict[str, float] = {}
    scored: list[tuple[float, str, float, float]] = []
    dear: set[str] = set()
    for sku, prices in paid.items():
        if len(prices) < DEAR_MIN_SOLD or sku not in cost:
            continue
        factor = surge.get(sku, 1.0)
        if factor > 1.0:
            continue
        unit_cost = statistics.median(cost[sku]) * factor
        margin = INEXTRO_MARGIN if sku in INEXTRO_SKUS else MARKET_FIRST_MARGIN
        item = catalog.get(sku)
        if item is not None and ZINUS_MARK in (item.name or ""):
            margin = max(margin, ZINUS_FLOOR)  # 13:5x (owner): refunds 6.7 %
        if margin * unit_cost > statistics.median(prices):
            continue
        if len(prices) < MARKET_FIRST_MIN_SOLD:
            if statistics.median(prices) < DEAR_MARGIN * unit_cost:
                continue
            dear.add(sku)
        per_hour = len(prices) / MARKET_FIRST_HOURS
        score = (statistics.fmean(prices) - unit_cost) * per_hour
        scored.append((score, sku, per_hour, unit_cost))
        medians[sku] = statistics.median(prices)
        if median_out is not None:
            median_out[sku] = medians[sku]
    scored.sort(reverse=True)
    top = scored if MARKET_FIRST_ALL else scored[:MARKET_FIRST_SKUS]
    top += [row for row in scored[len(top) :] if row[1] in INEXTRO_SKUS]
    fresh = now - 12 * 3600
    hour_ago = now - 3600
    wanted: list[tuple[int, str, float]] = []
    for _, sku, per_hour, unit_cost in top:
        recent = sum(
            1 for u in inventory.units if u.sku == sku and u.supplied_at >= fresh
        )
        held = sum(1 for u in inventory.units if u.sku == sku)
        sold = sum(1 for t in (sold_log or {}).get(sku, ()) if t >= hour_ago)
        if inventory.balance < MF_LOW_CASH and (
            sum(1 for t in (sold_log or {}).get(sku, ()) if t >= now - 7200)
            < MF_LOW_CASH_SOLD
        ):
            continue
        if held and not sold:
            continue  # held units sold none in the last hour: buy no more
        if sold > 0 and sold >= FAST_SHARE * (held + sold):
            hold = min(FAST_HOLD_MAX, max(MARKET_FIRST_HOLD_MIN, round(2 * per_hour)))
            if fast_out is not None:
                fast_out[sku] = medians[sku]
        else:
            hold = min(
                MARKET_FIRST_HOLD_MAX, max(MARKET_FIRST_HOLD_MIN, round(per_hour))
            )
        if sku in INEXTRO_SKUS:
            hold = max(hold, INEXTRO_HOLD)
        sold_2h = sum(1 for t in (sold_log or {}).get(sku, ()) if t >= now - 7200)
        today_a = sum(
            1
            for u in inventory.units
            if u.sku == sku and u.grade == "A" and u.supplied_at >= DEEP_SINCE
        )
        if sold_2h >= DEEP_SOLD and today_a <= DEEP_TODAY_A:
            hold = max(
                hold, min(FAST_HOLD_MAX, max(DEEP_HOLD_MIN, round(2 * per_hour)))
            )
        if sku in dear:
            hold = min(hold, DEAR_HOLD)
        qty = min(MARKET_FIRST_PER_RUN, hold - recent)
        if qty > 0:
            wanted.append((qty, sku, unit_cost))
    wanted.sort(key=lambda row: -row[0])
    cash = inventory.balance - MARKET_FIRST_CASH_FLOOR
    room = NEW_GOODS_STOP_COST - sum(
        u.cost for u in inventory.units if u.kind == "goods"
    )
    out: list[dict[str, object]] = []
    for qty, sku, unit_cost in wanted:
        fits = int(min(cash, room) // max(1.0, unit_cost))
        take = min(qty, fits)
        if take <= 0:
            continue
        out.append({"sku": sku, "qty": take})
        cash -= take * unit_cost
        room -= take * unit_cost
    return out


# 11/17 07:1x (owner): the 07:20 and 07:40 windows fill the 20-unit holds with up to
# 8 lines; after MARKET_FIRST_FILL_UNTIL a run gives market-first at most
# MARKET_FIRST_LINES_LATER lines, skips SKUs short by under MARKET_FIRST_MIN_QTY, and
# keeps FOOD_LINES_KEPT food lines from the plan.
# 11/17 08:0x (owner, 08:02): food A+B fell to 169 (in ~240/h, out 280-370/h). Food
# keeps 6 lines (5 once food A+B is over FOOD_AB_LINE). 08:07: the 3-line cap on
# market-first is cancelled; it takes the lines food leaves, largest shortfall
# first, and any line still free goes to the plan's other goods, then more food.
MARKET_FIRST_FILL_UNTIL: Final = 1794869100.0  # 2026-11-17 07:45 KST
MARKET_FIRST_MIN_QTY: Final = 5
# Per restock run (three runs a window). 09:59 (owner): 8 food lines a window are
# met by 6 a run; each run leaves market-first at least MAX_LINES - 6 = 4 lines.
FOOD_LINES_KEPT: Final = 6
FOOD_LINES_KEPT_FULL: Final = 5
FOOD_AB_LINE: Final = 300  # 250 until 08:5x (owner: 6 lines until A+B passes 300)


def food_ab_units(inventory: Inventory) -> int:
    """Return the number of grade A and B food units held."""
    return sum(1 for u in inventory.units if u.kind == "food" and u.grade in {"A", "B"})


def merge_market_first(
    first: list[dict[str, object]],
    lines: list[dict[str, object]],
    catalog: Mapping[str, CatalogItem],
    now: float,
    food_ab: int | None = None,
) -> list[dict[str, object]]:
    """Put market-first lines ahead of the plan's within MAX_LINES."""
    if now < MARKET_FIRST_FILL_UNTIL:
        picked = {str(row["sku"]) for row in first}
        rest = [row for row in lines if str(row["sku"]) not in picked]
        return [*first, *rest][:MAX_LINES]

    def is_food(row: dict[str, object]) -> bool:
        item = catalog.get(str(row["sku"]))
        return item is not None and item.kind == "food"

    keep = (
        FOOD_LINES_KEPT_FULL
        if food_ab is not None and food_ab > FOOD_AB_LINE
        else FOOD_LINES_KEPT
    )
    short = [row for row in first if int(str(row["qty"])) >= MARKET_FIRST_MIN_QTY]
    short.sort(key=lambda row: -int(str(row["qty"])))
    food_in_plan = sum(1 for row in lines if is_food(row))
    first = short[: MAX_LINES - min(keep, food_in_plan)]
    picked = {str(row["sku"]) for row in first}
    rest = [row for row in lines if str(row["sku"]) not in picked]
    food = [row for row in rest if is_food(row)]
    kept = food[:keep]
    # The lines left go to the plan's other goods first, then to more food.
    others = [row for row in rest if not is_food(row)] + food[keep:]
    return [*first, *kept, *others][:MAX_LINES]


def write_fast_sellers(path: Path, medians: Mapping[str, float], now: float) -> None:
    """Persist fast market-first sellers' market medians for the seller."""
    with contextlib.suppress(OSError):
        _ = path.write_text(json.dumps({"ts": now, "skus": dict(medians)}), "utf-8")


def load_fast_sellers(path: Path, now: float | None = None) -> dict[str, float]:
    """Return SKU -> market median; a missing or stale file means none."""
    stamp = now if now is not None else time.time()
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    age = stamp - float(raw.get("ts") or 0) if isinstance(raw, dict) else None
    if age is None or age > FAST_MAX_AGE_SECONDS:
        return {}
    skus = raw.get("skus")
    if not isinstance(skus, dict):
        return {}
    return {str(k): float(v) for k, v in skus.items() if isinstance(v, (int, float))}


def rater_food(
    ledger: Path, shop: str, now: float, catalog: Mapping[str, CatalogItem]
) -> tuple[frozenset[str], list[tuple[int, str]]]:
    """Food SKUs buyer-02/dyishappy settled here lately; dyishappy's B/C SKUs."""
    if not shop:
        return frozenset(), []
    opened: set[str] = set()
    short: dict[str, int] = {}
    try:
        con = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    except sqlite3.Error:
        return frozenset(), []
    try:
        for sku, buyer, grade, ts in con.execute(
            "select sku, buyer, grade, ts from rows where kind='settle' and ts>=?"
            " and rtrim(shop,'/')=? and buyer in (?,?)",
            (now - RATER_FOOD_HOURS * 3600, shop.rstrip("/"), *RATER_FOOD_BUYERS),
        ):
            item = catalog.get(str(sku))
            if item is None or item.kind != "food":
                continue
            opened.add(str(sku))
            if (
                buyer == "dyishappy"
                and grade in {"B", "C"}
                and float(ts) >= now - DYIS_SHORT_HOURS * 3600
            ):
                short[str(sku)] = short.get(str(sku), 0) + 1
    except sqlite3.Error:
        return frozenset(), []
    finally:
        con.close()
    return frozenset(opened), sorted(
        ((n, sku) for sku, n in short.items()), reverse=True
    )


def dyishappy_food_lines(
    short: list[tuple[int, str]],
    catalog: Mapping[str, CatalogItem],
    inventory: Inventory,
    now: float,
    planned: set[str],
) -> list[dict[str, object]]:
    """Up to DYIS_LINES lines for the SKUs dyishappy got B/C for (owner 09:4x)."""
    if not DYIS_LINES_ON or len(inventory.units) >= DYIS_STOP_UNITS:
        return []
    out: list[dict[str, object]] = []
    limit = DYIS_LINES if food_ab_units(inventory) >= DYIS_FULL_AB else DYIS_LINES_LOW
    for _, sku in short:
        item = catalog.get(sku)  # overpriced SKUs are already out of the catalog
        if len(out) >= limit or item is None or sku in planned:
            continue
        fresh75 = sum(
            1
            for u in inventory.units
            if u.sku == sku
            and (r := u.shelf_left_ratio(now, item.shelf_hours)) is not None
            and r >= FRESH_RATIO
        )
        qty = min(FRESH_LINE_QTY, DYIS_FRESH_HOLD - fresh75)
        if qty > 0:
            out.append({"sku": sku, "qty": qty})
    return out


RELIEF_MARGIN: Final = 1.08
RELIEF_MIN_PAID: Final = 10


def overpriced_relief(
    ledger: Path, now: float, blocked: frozenset[str], surge: Mapping[str, float]
) -> frozenset[str]:
    """Blocked SKUs that clear RELIEF_MARGIN at today's market supply cost.

    Today's cost is the 12 h median unit cost of all shops' supply lots times the
    SKU's current surge factor; the 12 h market paid median must hold at least
    RELIEF_MIN_PAID settles. Units already held keep their own cost floors.
    """
    if not blocked:
        return frozenset()
    marks = ",".join("?" for _ in blocked)
    paid: dict[str, list[float]] = {}
    cost: dict[str, list[float]] = {}
    try:
        con = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    except sqlite3.Error:
        return frozenset()
    try:
        for sku, amount in con.execute(
            f"select sku, amount from rows where kind='settle' and ts>=?"  # noqa: S608 - fixed placeholders
            f" and sku in ({marks})",
            (now - 12 * 3600, *blocked),
        ):
            if amount:
                paid.setdefault(str(sku), []).append(float(amount))
        for sku, qty, total in con.execute(
            "select json_extract(data,'$.sku'), json_extract(data,'$.qty'),"
            " json_extract(data,'$.total') from rows where kind='supply' and ts>=?",
            (now - 12 * 3600,),
        ):
            if sku in blocked and qty and total:
                cost.setdefault(str(sku), []).append(float(total) / float(qty))
    except sqlite3.Error:
        return frozenset()
    finally:
        con.close()
    return frozenset(
        sku
        for sku, prices in paid.items()
        if len(prices) >= RELIEF_MIN_PAID
        and sku in cost
        and RELIEF_MARGIN * statistics.median(cost[sku]) * surge.get(sku, 1.0)
        <= statistics.median(prices)
    )


def goods_margin_ok(ledger: Path, now: float) -> frozenset[str] | None:
    """Goods SKUs whose 12 h market paid median clears NEW_GOODS_MARGIN x supply cost.

    The supply cost is the 48 h median unit cost across all shops' supply lots.
    None when the ledger cannot be read (the old behaviour then applies).
    """
    paid: dict[str, list[float]] = {}
    cost: dict[str, list[float]] = {}
    try:
        con = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        for sku, amount in con.execute(
            "select sku, amount from rows where kind='settle' and ts>=?"
            " and json_extract(data,'$.expires_at') is null",
            (now - 12 * 3600,),
        ):
            if sku and amount:
                paid.setdefault(str(sku), []).append(float(amount))
        for sku, qty, total in con.execute(
            "select json_extract(data,'$.sku'), json_extract(data,'$.qty'),"
            " json_extract(data,'$.total') from rows where kind='supply' and ts>=?",
            (now - 48 * 3600,),
        ):
            if sku in paid and qty and total:
                cost.setdefault(str(sku), []).append(float(total) / float(qty))
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return frozenset(
        sku
        for sku, prices in paid.items()
        if sku in cost
        and statistics.median(prices) >= NEW_GOODS_MARGIN * statistics.median(cost[sku])
    )


def run(
    folder: Path,
    *,
    policy_file: Path | None = None,
    signal: DemandSignal | None = None,
    now: float | None = None,
    shop_url: str = "",
) -> str:
    """Read the kit's files, plan, persist state and return the JSON line to print."""
    stamp = now if now is not None else time.time()
    policy = load_policy(
        policy_file if policy_file is not None else policy_path(folder)
    )
    inventory = read_inventory(folder)
    if supply_closed(inventory.restock, stamp):
        return json.dumps({"buy": []})
    state_path = folder.resolve().parent / STATE_FILE
    state = load_state(state_path)
    # SKUs the market sells below our cost leave the catalog for buying only;
    # units already held still sell through the seller (r3/overpriced.py).
    blocked = load_overpriced(folder.resolve().parent / OVERPRICED_FILE, stamp)
    # 11/17 06:2x (owner): the overpriced list judges by our own 7-day lots, which ran
    # 10-25 % above today's market supply cost; 12 of the 20 goods at 500k+ cleared
    # 1.08 at today's cost. Each run re-checks today's cost (with the surge factor).
    relief = overpriced_relief(
        folder.resolve().parent / "r3-ledger.sqlite",
        stamp,
        blocked,
        {entry.sku: entry.factor for entry in inventory.surge},
    )
    blocked = blocked - relief
    raters = load_rater_demand(folder.resolve().parent / RATER_DEMAND_FILE, stamp)
    catalog = {
        sku: item for sku, item in read_catalog(folder).items() if sku not in blocked
    }
    held_skus = {u.sku for u in inventory.units}
    margin_ok = goods_margin_ok(folder.resolve().parent / "r3-ledger.sqlite", stamp)
    if margin_ok is not None:
        catalog = {
            sku: item
            for sku, item in catalog.items()
            if item.kind != "goods"
            or sku in held_skus
            or sku in state.last_sold_at
            or sku in margin_ok
        }
    rater_open, dyis_short = rater_food(
        folder.resolve().parent / "r3-ledger.sqlite", shop_url, stamp, catalog
    )
    growth = (
        build_growth(folder.resolve().parent, shop_url, catalog, inventory, stamp)
        if policy.restock.growth_enabled
        else None
    )
    fresh_skus = load_fresh_list(folder.resolve().parent / FRESH_LIST_FILE, stamp)
    fresh_needs = load_fresh_list(folder.resolve().parent / NEEDS_FILE, stamp)
    # 13:5x (owner): cheapest-in-town's unmet food asks join the fresh fill while food
    # A+B is over CIT_FOOD_AB_LINE (the fresh line count stays the same).
    if food_ab_units(inventory) > CIT_FOOD_AB_LINE:
        cit_rate, cit_need = load_cit_needs(
            folder.resolve().parent / CIT_NEEDS_FILE, stamp
        )
        for sku, per_hour in cit_rate.items():
            item = catalog.get(sku)
            if item is None or item.kind != "food":
                continue
            fresh_skus = {**fresh_skus, sku: fresh_skus.get(sku, 0.0) + per_hour}
            fresh_needs = {
                **fresh_needs,
                sku: max(fresh_needs.get(sku, 0.0), cit_need[sku]),
            }
    lines, updated = plan(
        inventory,
        catalog,
        read_demand(folder),
        policy.restock,
        state,
        stamp,
        signal,
        load_economics(folder.resolve().parent / ECONOMICS_FILE),
        growth,
        load_stockouts(folder.resolve().parent / STOCKOUTS_FILE, stamp),
        raters,
        # 11/15 13:4x: the food C check is off. Food A/B fell to 58 units (A 7, B 51)
        # against 1,055 C, "none in stock" answers rose from ~75 to ~125 an hour, and
        # the plan bought 33 food units a run with it against 82 without. Aging
        # food turns C on the shelf, so C kept exceeding C sales and starved A/B.
        None,
        fresh_skus,
        fresh_needs,
        rater_open,
    )
    dyis = dyishappy_food_lines(
        dyis_short, catalog, inventory, stamp, {str(r["sku"]) for r in lines}
    )
    # 09:59 (owner): food lines go dyishappy's B/C SKUs, then buyer-02's list SKUs,
    # then the other food (goods keep their places after the food).
    listed = set(load_fresh_list(folder.resolve().parent / FRESH_LIST_FILE, stamp))

    def food_rank(row: dict[str, object]) -> int:
        item = catalog.get(str(row["sku"]))
        if item is None or item.kind != "food":
            return 3
        return 1 if str(row["sku"]) in listed else 2

    lines = [*dyis, *sorted(lines, key=food_rank)][:MAX_LINES]
    # 11/17 07:0x (owner): market-first goods lines lead every run, outside the
    # own-sales caps (the slow-goods cap held G24f5f979 to 3 while the market sold 140
    # in 6 h). Old-cost units keep their own floors; accept lines are unchanged.
    fast: dict[str, float] = {}
    mf_medians: dict[str, float] = {}
    first = market_first_lines(
        folder.resolve().parent / "r3-ledger.sqlite",
        stamp,
        catalog,
        inventory,
        updated.sold_log,
        fast,
        mf_medians,
    )
    write_fast_sellers(folder.resolve().parent / FAST_FILE, fast, stamp)
    write_fast_sellers(folder.resolve().parent / MF_FILE, mf_medians, stamp)
    if first:
        lines = merge_market_first(
            first, lines, catalog, stamp, food_ab_units(inventory)
        )
    # 11/17 17:0x (owner): B/C rebuy lines (r3/bc_rebuy.py) take goods slots first;
    # the food lines the merge kept stay.
    goods_held = sum(u.cost for u in inventory.units if u.kind == "goods")
    rebuy = bc_rebuy_lines(
        folder.resolve().parent,
        shop_url,
        stamp,
        catalog,
        inventory,
        NEW_GOODS_STOP_COST - goods_held,
        inventory.balance - MARKET_FIRST_CASH_FLOOR,
    )
    lines = apply_bc_rebuy(lines, rebuy, catalog, MAX_LINES, FOOD_LINES_KEPT)
    priority = priority_lines(
        folder.resolve().parent,
        inventory,
        catalog,
        policy.restock,
        json.dumps(lines),
        updated.planned_cost,
        stamp,
    )
    lines = [row.model_dump() for row in priority.lines]
    updated = updated.model_copy(
        update={"planned_cost": updated.planned_cost + priority.extra_cost}
    )
    with contextlib.suppress(OSError):
        save_state(state_path, updated)
    return json.dumps({"buy": lines}, ensure_ascii=False)
