"""Round 3 seller: sells the kit's real stock over A2A in milliseconds, no model.

Every turn reads ``inventory.json`` (the market's view of our units, cost and grade),
prices the unit from the policy table and answers at once. The receipt names the
exact unit, an honest grade claim and the warranty the policy promises, so the kit's
checkout ships what we said. The only model in this agent runs in the slow policy
loop (``r3.manager``); this server never waits on one.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, override

import anyio
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import InvalidParamsError
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict, Parse
from google.protobuf.struct_pb2 import Value

from market_a2a.seller_offer import opening_offer
from r3.endgame import ROUND_END
from r3.events import EventLedger, events_path
from r3.fresh_list import (
    CIT_NEEDS_FILE,
    FAIR_RATIO,
    FRESH_LIST_FILE,
    FRESH_RATIO,
    NEED_MARGIN_HOURS,
    NEEDS_FILE,
    load_fresh_list,
    record_cit_need,
)
from r3.guard import suspicious
from r3.interpreter import read_buyer_message
from r3.ledger_db import LedgerDB, ledger_path
from r3.market_files import (
    CatalogItem,
    Inventory,
    Unit,
    category_leaf,
    market_dir,
    read_catalog,
    read_inventory,
)
from r3.model_client import ModelClient
from r3.policy import WARRANTY_MAX_HOURS, SellerPolicy, load_policy, policy_path
from r3.pricing import (
    FOOD_ACCEPT_SHARE,
    FOOD_ASK_SHARE,
    Quote,
    buyer_share_capped,
    claimed_grade,
    decide,
    market_anchored,
    quote,
)
from r3.restock import (
    FAST_FILE,
    INEXTRO_MARGIN,
    INEXTRO_SKUS,
    MF_FILE,
    load_fast_sellers,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from a2a.server.events import EventQueue

    from market_a2a.receipts import ReceiptSigner

ECON_ROUND: Final = 3
MAX_READS_PER_TASK: Final = 2  # model reads of prose per negotiation
SUSPICIOUS_LIMIT: Final = 2  # screened messages before the talk is closed
TRADE_GATE: Final = "AX_R3_TRADE_MODEL"
# A buyer that stops answering frees its unit. 900 s until 11/13 16:4x: over 3.1 h
# of logs, 55 of 86 "none in stock" replies came while a unit of that SKU sat held
# for a buyer who had already walked away (880 of 1,378 unsold talks ended at the
# first ask; unsold talks last 0 s median, 19 s at p99). Owner: "don't let a talk
# that cannot close cost us other customers".
HOLD_SECONDS: Final = 90.0
COMMIT_SECONDS: Final = 600.0  # a signed unit stays reserved until the kit ships it
LEGACY_RESERVE: Final = {"lamp": 100, "chair": 180, "bookshelf": 260}
SKU_TOKEN: Final = re.compile(r"\b[A-Za-z][0-9A-Fa-f]{8}\b")
OFFER_WORDS: Final = re.compile(
    r"(?:\bfor|\boffer|\bpay|\bat|\bprice|\bprice is|\bbid)\s*:?\s*\$?([0-9]{2,9})\b",
    re.IGNORECASE,
)
BARE_OFFER: Final = re.compile(
    r"^\s*(?:(?:i offer|i can pay|i will pay|i'll pay|my offer is|offer|ok|okay)\s+)?"
    r"\$?([0-9]{1,9})(?:\.0{1,2})?\s*(?:credits?|won|원)?\s*[.!]?\s*$",
    re.IGNORECASE,
)
STANDALONE_NUMBER: Final = re.compile(r"(?<![0-9A-Za-z.])([0-9]{1,9})(?![0-9A-Za-z])")
# A digit-free promotion line, written only when the public ledger backs it (the
# middle lines must stay digit-free so no parser mistakes them for the ask).
BELOW_MARKET_LINE: Final = "Priced below this item's recent market median."
MEDIAN_WINDOW_SECONDS: Final = 6 * 3600.0
MEDIAN_CACHE_SECONDS: Final = 60.0
HOUR_WORDS: Final = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
    "twenty-one",
    "twenty-two",
    "twenty-three",
    "twenty-four",
)
DAY_WORDS: Final = ("zero", "one", "two", "three", "four", "five", "six", "seven")
TWO_DAYS: Final = 48
LIFETIME_HOURS: Final = 8760  # a year: longer than the round, so for good


OPENING_TEXT_CHARS: Final = 240
# Owner 11/13 16:1x ("재고 소진노력도 해야해"): goods held this long go to the market
# anchor with a floor of cost itself (no 2 % margin), never below cost.
AGED_GOODS_SECONDS: Final = 48 * 3600
FOOD_URGENT_SECONDS: Final = 48 * 3600
FRESH_SECONDS: Final = 540 * 3600
COST_BAND: Final = 0.05


# 11/14 04:2x (stars come first in the gap to first place): our stars rank is the
# mean of each buyer's own mean, and q00's is 2.29 over 31 reviews (21 one-star
# "SCAM" reviews 11/3-11/12; all 10 since 11/13 11:11 are five stars). It opens
# "I want to buy a 커피/차 (buyer: q00) ...", bargains five rounds and walked from 2
# of 4 coffee asks 11/14 01-03 KST that ended 0.3-0.5 % above its price. Each more
# deal it rates lifts our stars rank by about 0.0013, so for these buyers we
# accept down to the unit's cost (never below it).
COST_FLOOR_BUYERS: Final = frozenset({"q00"})
NAMED_BUYER: Final = re.compile(r"\(buyer:\s*([^)\s]+)\s*\)")
# 11/14 15:2x: dzzdd010 (new at 11:23) rated our first 11 deals 2-4 (mean 2.5) and
# its opening form "It must last N hours (need N h of shelf life)." was put here.
# 15:4x: 8 more deals sold down to cost (34-68 % of list) were rated 2-3 (mean 2.4),
# so price does not move it; it was taken out again and pays the normal line.
# 11/14 16:4x (owner: "별점 주는 구매자들 특징 다 파악해서 각각 맞춤별 판매전략"):
# from 30 h of rated settles, gom31-certified-goods (our mean 4.33) gives A 4.27 and
# pays above 0.65 of list for 4.0 but 4.99 under 0.55; yeonsong2 (3.69) rates its
# cheapest deals highest (under 0.55 of list 4.58). Their openings, and no other
# buyer's in the log, are "I want to buy NAME (SKU)." (15 of 15) and "..., grade A
# or B only" (6 of 6), so both get the accept-down-to-cost line.
SHELF_NEED_FORM: Final = re.compile(
    r"It must last [\d.]+ hours \(need [\d.]+ h of shelf life\)\.$"
)
CHEAPEST_AB_FORM: Final = re.compile(r"^I want to buy [^\n]*\(G[0-9a-f]{8}\)\.$")
COST_FLOOR_OPENINGS: Final[tuple[re.Pattern[str], ...]] = (
    CHEAPEST_AB_FORM,
    # 11/16 18:5x: ovotnwjd-maker/dzzdd010 shelf form (food only is served).
    re.compile(r"It must last [\d.]+ hours \(need [\d.]+ h of shelf life\)\.$"),
    re.compile(r"\(G[0-9a-f]{8}\), grade A or B only\b"),
)


def cost_floor_buyer(text: str) -> bool:
    """Return True when the opening names a buyer we sell to at cost."""
    match = NAMED_BUYER.search(text)
    if match is not None and match.group(1) in COST_FLOOR_BUYERS:
        return True
    stripped = text.strip()
    return any(form.search(stripped) for form in COST_FLOOR_OPENINGS)


def anchor_margin(unit: Unit, now: float, margin: float) -> float:
    """Minimum margin for the market anchor: none for long-held goods."""
    if unit.kind == "goods" and now - unit.supplied_at >= AGED_GOODS_SECONDS:
        return 0.0
    return margin


# 11/14 15:3x (owner: "별점 안주는놈 찾아서 각각 대응방안 모두 동원"): 11im rates every
# grade C deal 3 and every A or B deal 4, here (C 15 at 3.0, A/B 72 at 4.0) and at
# all other shops (2,121 C at 3.0); its openings, and no other buyer's (25 of 25
# sales in the log), end ", it must last at least N hours". 26512076 rates C 3.0
# with us and 3.48 elsewhere against 3.7-4.3 for A; it sends a "min_hours_left"
# policy. These buyers are not offered grade C (none left means no offer).
NO_C_OPENINGS: Final = (
    re.compile(r"\(G[0-9a-f]{8}\), it must last at least [\d.]+ hours$"),
    re.compile(r"min_hours_left"),
    # kcwarni (11/14 16:4x): says it "accepts grade A, B or C" but rates C 3.0 and
    # A/B 5.0; both of its opening forms are its own (97 of 97, 26 of 26).
    re.compile(r"^I want to buy (?:a )?G[0-9a-f]{8}\. This order accepts grade"),
    # lee-sungsoo (11/14 23:4x, owner addendum): food C 4.0 (20) against A/B 5.0
    # in 24 h; its form is its own (10 of 10 sales in the log). buyer-01, buyer-02
    # and chanarun also rate C lower but ask in the common plain form.
    re.compile(r"\(G[0-9a-f]{8}\)\. The unit must last at least [\d.]+ hours\.$"),
    # 11/15 19:1x (owner): "I want to buy G... . Please quote a price in credits."
    # In 24 h fair-furniture (most of this form) rated A 5 (607) and B 4 (275) and
    # settled none of its C receipts (12 h: C 30 receipts, 5 settled; A/B 70/68);
    # chanarun and cheapest-in-town, also seen in it, rate C 4 against A/B 5.
    re.compile(r"^I want to buy G[0-9a-f]{8}\. Please quote a price in credits\.$"),
    # 11/15 22:0x (owner, overnight addendum): leeuichann rated all 20 C deals here 4
    # (food 10, goods 10) and all 50 A/B deals 5; "I want to buy one unit of SKU G..."
    # is its own form (18 of 18 mapped sales, 49 openings in the log).
    re.compile(r"^I want to buy one unit of SKU G[0-9a-f]{8}\b"),
    # 11/16 08:5x (owner, Codex 08:12 strategy): dyishappy rated food B 2.92 (124),
    # food C 3.00 (15), food A 3.56 (16) and goods A 3.30 (40) in 24 h; its bare
    # "I want to buy a G..." form got A, then B, never C from 09:00. Taken back at
    # 10:3x (owner's line: under 3.3 after 30 new ratings): 36 ratings averaged 3.14
    # (A 17, B 19) against 3.02 before; Raksmey (same form) rated 8 at 5.0.
)
# Openings that get grade A first (then B; C excluded via NO_C_OPENINGS). Empty
# since 11/16 10:3x; DYISHAPPY_FORM is kept so the rule can be put back.
DYISHAPPY_FORM: Final = re.compile(r"^I want to buy a G[0-9a-f]{8}$")
# 11/16 13:1x (owner): fair-furniture rated A 5.0 (607) and B 4.0 (275) in 24 h in
# its "I want to buy G... . Please quote a price in credits." form (chanarun and
# cheapest-in-town, also in it, rate A and B 5). A first, food and goods; B when
# no A fits; C stays excluded via NO_C_OPENINGS.
A_FIRST_OPENINGS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^I want to buy G[0-9a-f]{8}\. Please quote a price in credits\.$"),
)


def a_first_buyer(text: str) -> bool:
    """Return True for an opening that gets a grade A unit whenever one fits."""
    stripped = text.strip()
    return any(form.search(stripped) for form in A_FIRST_OPENINGS)


# 11/16 08:5x (owner): buyer-02 rated these two kimchi SKUs (168 h shelf life) 4.87
# with 0.75+ of the shelf life left (46, no 2 stars), 4.00 at 0.5-0.75 (30, ten 2
# stars) and 2.85 under 0.5 (46, 33 two stars) in 12 h. The plain form gets the
# longest-lived unit with KIMCHI_RATIO or more left, else none in stock; units under
# it go first to q00's category asks and to openings whose stated need they meet.
KIMCHI_SKUS: Final = frozenset({"G7e48d0d9", "G5f5f42e0"})
KIMCHI_RATIO: Final = 0.5


def no_c_buyer(text: str) -> bool:
    """Return True for a buyer that rates every grade C unit below its A/B."""
    stripped = text.strip()
    return any(form.search(stripped) for form in NO_C_OPENINGS)


# 11/15 08:2x (owner task B): goods C was 437 units, 129M of cost (26 % of goods),
# 92 of them past 48 h, yet sold 19 in 12 h against 409 across the market (median
# 0.559 of list): with A/B on the shelf C was never offered. Our goods C deals rate
# 4.82, and the C buyers include totato2203-r3 and cheapest-in-town. Their goods
# openings are their own in the log (totato 129 of 129 mapped sales; cheapest-in-
# town 11 of 11), so they get a same-SKU grade C unit first, honestly graded C
# and priced from its own cost (never under it). C-excluded and "A or B only"
# buyers are untouched.
C_FIRST_OPENINGS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"\(G[0-9a-f]{8}\)\. Quote the total price\. Require a market certificate\. "
        r"Confirm available stock, true grade, total price and warranty\.$"
    ),
    re.compile(
        r"^Please quote one [^\n]*\(G[0-9a-f]{8}\) with the market certificate\. "
        r"Please state grade A/B and any breakage refund warranty\.$"
    ),
)


def c_first_buyer(text: str) -> bool:
    """Return True for a goods buyer we offer a grade C unit first."""
    stripped = text.strip()
    if no_c_buyer(stripped):
        return False
    return any(form.search(stripped) for form in C_FIRST_OPENINGS)


# 11/17 07:5x (owner 07:35 task 3): 394 openings on the eight old goods C SKUs were
# offered A 317, C 40, B 14 (C 27 of 40 reached a receipt) while 133 C units sat:
# the plain form was never offered C with A/B on the shelf. Its buyers rate goods C
# 5.00 (buyer-02 43, jiseokkk 41 at all shops); chanarun, also in the form, rates C
# 4.00 (106) against A/B 5.00. Plain-form goods asks get a same-SKU C unit first
# when that C clears at the market median (else the usual A/B switch applies).
PLAIN_GOODS_C_FIRST: Final = True


def plain_goods_c_first(text: str, units: list[Unit] | tuple[Unit, ...]) -> bool:
    """Return True for a plain-form ask whose fitting units are all goods."""
    return (
        PLAIN_GOODS_C_FIRST
        and PLAIN_OPENING.search(text.strip()) is not None
        and bool(units)
        and all(u.kind == "goods" for u in units)
    )


# 11/15 10:3x (owner, Codex seller review): buyer-02 rated food it bought here in 12 h
# 4.87 with 540+ h of shelf life left, 4.23 at 360-540 h and 3.43 under 360 h; its
# reviews ask for 0.75, 0.5, 0.25 or 0.1 of the catalog shelf life. Freshest-first
# food for the plain "I want to buy NAME (SKU)" form and a "Shelf life left: Nh."
# quote line went out at 10:41 and were taken back at 11:5x: in the first hour the
# plain form's food close rate fell from 71.8 % (515 asks) to 61.1 % (270) and
# buyer-02 rated 3.78 (23) against 4.26 (275) before, with 9 two-star shelf reviews.
# The plain "I want to buy NAME (SKU)" form (buyer-01, buyer-02, usagi, chanarun).
PLAIN_OPENING: Final = re.compile(r"^I want to buy [^\n]*\(G[0-9a-f]{8}\)$")
# An opening that names grade A or B as what it wants.
A_OR_B_ASK: Final = re.compile(r"\bA or B\b|\bA/B\b|\bA or B only\b", re.IGNORECASE)
# Buyers whose own opening shows they take lower grades get food B or C first, so
# fresh A food is left for the plain form: dae-yangkim ("Grade B or C is fine if it
# is cheaper", 2 of 2), kimauve (", it must last N hours", 3 of 3) and psyoung82
# ("Please offer a grade A or B unit if you have one", 14 of 14: B first, never C).
LOW_GRADE_OPENINGS: Final[tuple[tuple[re.Pattern[str], dict[str, int]], ...]] = (
    (re.compile(r"Grade B or C is fine if it is cheaper"), {"C": 0, "B": 1, "A": 2}),
    (
        re.compile(r"\(G[0-9a-f]{8}\), it must last [\d.]+ hours$"),
        {"C": 0, "B": 1, "A": 2},
    ),
    (re.compile(r"Please offer a grade A or B unit if you have one"), {"B": 0, "A": 1}),
    # 11/15 19:2x (owner, Codex rating review): totato2203-r3 rated our last 60
    # and 2,931 deals elsewhere in 24 h all 5 ("Payment settled; no unobserved
    # quality or failure claims"); it buys about 660 a day here. Food C first too.
    (
        re.compile(
            r"\(G[0-9a-f]{8}\)\. Quote the total price\. Require a market "
            r"certificate\. Confirm available stock, true grade, total price and "
            r"warranty\.$"
        ),
        {"C": 0, "B": 1, "A": 2},
    ),
)
# 11/15 19:2x (owner): yeonsong2 ("..., grade A or B only") rates dear goods 2-3
# ("72-hour warranty on a high-value sofa is alarmingly short and a major red
# flag") and food or cheap goods 4-5; goods at or above this list price are not
# offered to it (none in stock). Its accept-down-to-cost line stays.
A_OR_B_ONLY: Final = re.compile(r"\(G[0-9a-f]{8}\), grade A or B only\b")
A_OR_B_ONLY_MAX_LIST: Final = 500_000


def low_grade_order(text: str) -> dict[str, int] | None:
    """Return the food grade order for a buyer that takes lower grades, if any."""
    stripped = text.strip()
    if no_c_buyer(stripped):
        return None
    for form, order in LOW_GRADE_OPENINGS:
        if form.search(stripped):
            return order
    return None


def offerable_units(units: tuple[Unit, ...], text: str) -> tuple[Unit, ...]:
    """Units this opening's buyer may be offered (no grade C for no-C buyers)."""
    if A_OR_B_ONLY.search(text.strip()):
        units = tuple(
            u for u in units if u.kind != "goods" or u.list_price < A_OR_B_ONLY_MAX_LIST
        )
    if no_c_buyer(text):
        return tuple(u for u in units if u.grade != "C")
    # 11/17 10:3x (owner, strategy 10:30): cheapest-in-town rated us 4 on 56 of 151
    # deals in 4 h (37 %; 12 % elsewhere): C gets 4, A/B 5. Its food asks get no C
    # (B first via the dyishappy A rule, then A, else none in stock); goods C stays.
    if CIT_FOOD_NO_C and any(form.search(text.strip()) for form in CIT_FORMS):
        return tuple(u for u in units if u.kind != "food" or u.grade != "C")
    if TAO_ON and TAO_FORM.search(text.strip()):
        return tuple(u for u in units if u.cost <= u.list_price * TAO_SHARE)
    return units


# 11/17 20:0x (owner, strategy 20:00): tao (new rater, 2 ratings here, both 5) rates
# at or under 0.64 of list 5, 0.66-0.69 4, 0.76 3; its food B/C 5 almost always.
# Its "... I need at least N h of shelf life left; please ship a unit that fresh."
# form is its own (4 of 4 openings since its first deal at 18:57, 2 of 2 settles).
# That form only gets units that can sell at list x TAO_SHARE or under (never under
# cost), food B/C before A, priced at most list x TAO_SHARE; none else in stock.
TAO_ON: Final = True
TAO_SHARE: Final = 0.62
TAO_FORM: Final = re.compile(
    r"^I want to buy [^\n]*\(G[0-9a-f]{8}\)\. I need at least [\d.]+ h of shelf "
    r"life left; please ship a unit that fresh\.$"
)


def tao_quote(unit: Unit, priced: Quote, text: str) -> Quote:
    """Cap the tao form's price at list x TAO_SHARE (never under the unit's cost)."""
    if not TAO_ON or not TAO_FORM.search(text.strip()):
        return priced
    cap = max(unit.cost, math.floor(unit.list_price * TAO_SHARE))
    if priced.ask <= cap and priced.accept_at <= cap:
        return priced
    return replace(
        priced,
        ask=min(priced.ask, cap),
        accept_at=min(priced.accept_at, cap),
        floor=min(priced.floor, cap),
    )


CIT_FOOD_NO_C: Final = True
# 12:49 judged: its food asks use their own form (160 of 160 mapped sales in 30 h,
# all cheapest-in-town), so food C still went out 48 times; both forms count now.
CIT_FORMS: Final = (
    C_FIRST_OPENINGS[1],
    re.compile(
        r"^Please quote one [^\n]*\(G[0-9a-f]{8}\) with the market certificate\. "
        r"It must last at least [\d.]+ hours\.$"
    ),
)


# Owner 11/13 08:5x: stop selling to sergiobuilds. Its buyer rates every deal two
# stars at every shop (18 of 18 with us, about 2,700 of 2,720 elsewhere in 24 h),
# so each sale only lowers our per-buyer stars mean. The kit's proxy never names
# the buyer; its openings, and no other ledger-matched buyer's (1,729 openings
# 11/13 05-09 KST, 54 of this shape), end "What is your price?".
DECLINED_OPENING: Final = re.compile(r"I want to buy .*What is your price\?", re.DOTALL)
DECLINED_REPLY: Final = "Sorry, we cannot take this order."


# Owner 11/14 15:5x ("걔 차단하자"): stop selling to dzzdd010. It rated our 20 deals
# 2-3 (mean 2.5) at any price, 34-70 % of list, while rating other shops 5 about
# 81 times; its openings, and no other buyer's (32 of 32 in the log), end this way.
# 11/14 17:0x owner "좀 삼십분만 풀고": dzzdd010 served again for a 30-minute trial;
# its pattern stays here so the block can be put back by adding it to the tuple.
DZZDD010_TAIL: Final = re.compile(
    r"It must last [\d.]+ hours \(need [\d.]+ h of shelf life\)\.$"
)
# 18:09 trial over (owner asked for 30 minutes): blocked again.
# 11/16 21:1x (owner night rule: block the form of any new dzzdd010 rating here):
# after the shelf form was closed again at 21:02, dzzdd010 bought a sofa (G33cc4ecf,
# 1,090,314) at 21:15 in this goods form and rated it 1; the form's only receipts in
# the kit log (7 openings since 16:2x) are its two.
DZZDD010_WARRANTY_TAIL: Final = re.compile(
    r"I need a warranty of [\d.]+ hours or more \(warranty [\d.]+ h\), "
    r"grade A or B preferred\.$"
)
# 11/17 05:4x (owner "yms979 막아 차단"): from 23:37 every yms979 deal here was rated 3
# ("Short of 5: only N h of shelf life left for a 180 h need"), its mean 4.763 -> 4.457
# (368). Its opening ends this way; all 38 receipts in this form (kit log) were its.
YMS979_TAIL: Final = re.compile(
    r"as long as it lasts that long: please quote your cheapest unit that does, "
    r"and say how many hours it has left\.$"
)
DECLINED_TAILS: Final[tuple[re.Pattern[str], ...]] = (
    DZZDD010_TAIL,
    DZZDD010_WARRANTY_TAIL,
    YMS979_TAIL,
)


# 11/16 18:5x (owner, urgent): the dzzdd010 form is also ovotnwjd-maker's. Its
# reviews cite needs of 126/84/42/540/360/180 h and the blocked openings asked for
# 127/85/43/541/361/181 h (dzzdd010's reviews state none); both buyers turned to
# 1-star ratings at 16:3x. ovotnwjd-maker's only rating here is one 1 from that
# burst, so food asks in this form are served again (goods stay declined).
# 20:59: blocked again. ovotnwjd-maker never came back (0 deals since 18:59); the
# four food deals in this form 20:56-20:58 were dzzdd010's and all rated 1 (its mean
# here 2.625 -> 2.529) while it rated 31 deals at 14 other shops 5.
SHELF_FORM_FOOD_OPEN: Final = False


def declined_buyer(text: str, catalog: Mapping[str, CatalogItem] | None = None) -> bool:
    """Return True for the opening wording of a buyer the owner stopped serving."""
    stripped = text.strip()
    if DECLINED_OPENING.fullmatch(stripped) is not None:
        return True
    if (
        SHELF_FORM_FOOD_OPEN
        and catalog is not None
        and DZZDD010_TAIL.search(stripped)
        and shelf_form_food(stripped, catalog)
    ):
        return False
    return any(tail.search(stripped) for tail in DECLINED_TAILS)


def shelf_form_food(text: str, catalog: Mapping[str, CatalogItem]) -> bool:
    """Return True when every SKU the opening names is food in the catalog."""
    skus = re.findall(r"G[0-9a-f]{8}", text)
    return bool(skus) and all(
        (item := catalog.get(sku)) is not None and item.kind == "food" for sku in skus
    )


HASHED_HEADERS: Final = ("authorization",)


def opening_note(context: RequestContext) -> str:
    """Describe a new task's opening message so buyers can be told apart later.

    The kit's proxy never names the buyer. 11/10: one buyer gave all 25 of its paid
    deals with us two stars, so the opening's wording, metadata and header names are
    logged to match against the public ledger. Secrets are hashed, never written.
    """
    try:
        message = context.message
        meta: object = {}
        if message is not None and message.HasField("metadata"):
            meta = MessageToDict(message.metadata)
        state = context.call_context.state if context.call_context else {}
        raw_headers = state.get("headers") if isinstance(state, dict) else None
        headers: dict[str, str] = {}
        if isinstance(raw_headers, dict):
            for name, value in raw_headers.items():
                key = str(name).lower()
                text = str(value)
                headers[key] = (
                    "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:10]
                    if key in HASHED_HEADERS
                    else text[:80]
                )
        user = context.call_context.user.user_name if context.call_context else ""
        opening = (context.get_user_input() or "")[:OPENING_TEXT_CHARS]
        return json.dumps(
            {"meta": meta, "headers": headers, "user": user, "text": opening},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    except Exception as exc:  # noqa: BLE001 - a log line must never stop a sale
        return json.dumps({"error": type(exc).__name__})


def warranty_words(hours: int) -> str:
    """Name a warranty without digits: "lifetime", "seven-day" (168 h), "six-hour".

    Asks keep every digit for the price (the ask is the last number of a reply).
    """
    if hours >= LIFETIME_HOURS:
        return "lifetime"
    days, rest = divmod(hours, 24)
    if hours >= TWO_DAYS and rest == 0 and days < len(DAY_WORDS):
        return f"{DAY_WORDS[days]}-day"
    return f"{HOUR_WORDS[min(hours, len(HOUR_WORDS) - 1)]}-hour"


def card_warranty_line(policy: SellerPolicy) -> str:
    """Return the card's warranty sentence from the policy the shop starts with."""
    hours = {policy.warranty(grade) for grade in ("A", "B", "C")}
    if len(hours) == 1 and (only := hours.pop()) > 0:
        return (
            f"every goods unit, any grade, carries a {warranty_words(only)} "
            "warranty: a full refund if it breaks."
        )
    return "goods carry the warranty named in each quote and receipt."


ASSENT: Final = re.compile(
    r"^\s*(?:ok|okay|yes|agreed|deal|accept|accepted|i accept|i agree|"
    r"i accept (?:your|the) (?:offer|quote|price|ask))\s*[.!]?\s*$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Deal:
    """One open negotiation; the unit is held for this task while it is open."""

    unit: Unit | None
    legacy_item: str | None
    quote: Quote
    ask: int
    rounds: int = 0
    opened_at: float = field(default_factory=time.time)
    last_offer: int | None = None
    # The category the buyer named instead of a SKU; the receipt echoes it.
    asked_category: str | None = None
    # Warranty hours the opening asked for ("Please include a 6-hour warranty").
    warranty_ask: int | None = None


# 11/16 10:2x (owner, urgent): grade A goods (72 h warranty) refunded 30.98M on 83
# breaks in 24 h; breaks come a median 2.2 h after the sale (25 % within 0.9 h, 90 %
# within 5.3 h). B went 72 -> 1 h on 11/13 with the same buyers' stars unchanged
# (fair-furniture 4.00 -> 4.00, Kiro729 4.94 -> 5.00, ...) and receipts settle 0.99.
# The policy's warranty_a is now 1 h; an opening that names the warranty it needs
# ("Please include a 6-hour warranty.", "Require 6-hour warranty.") gets that many
# hours on grade A goods, up to WARRANTY_MAX_HOURS. B, C and food are unchanged.
WARRANTY_ASK: Final = re.compile(
    r"\b(?:require[sd]?|include|need|want|with)\s+(?:an?\s+)?(\d+(?:\.\d+)?)"
    r"\s*-?\s*(?:hours?|h)\s+warranty\b"
    r"|\bwarranty\s+of\s+(?:at\s+least\s+)?(\d+(?:\.\d+)?)\s*(?:hours?|h)\b",
    re.IGNORECASE,
)


def asked_warranty(text: str) -> int | None:
    """Return the warranty hours an opening asks for, capped at the market's max."""
    hours = [float(a or b) for a, b in WARRANTY_ASK.findall(text)]
    if not hours:
        return None
    return min(WARRANTY_MAX_HOURS, math.ceil(max(hours)))


def deal_warranty(deal: Deal, policy: SellerPolicy) -> int:
    """Warranty hours for this deal's goods unit (0 for food or no unit)."""
    if deal.unit is None or deal.unit.kind != "goods":
        return 0
    hours = policy.warranty(deal.quote.grade_now)
    if deal.quote.grade_now == "A" and deal.warranty_ask is not None:
        return max(hours, deal.warranty_ask)
    return hours


@dataclass(frozen=True, slots=True)
class Request:
    """What the buyer asked for in the opening message."""

    skus: tuple[str, ...]
    offer: int | None
    text: str


def parse_request(text: str, known_skus: frozenset[str]) -> Request:
    """Extract named SKUs and an explicit opening offer; never invent either."""
    lowered = text.lower()
    skus = tuple(
        dict.fromkeys(
            token for token in SKU_TOKEN.findall(text) if token.lower() in known_skus
        )
    )
    offer: int | None = None
    matched = OFFER_WORDS.search(text)
    if matched is not None:
        offer = int(matched[1])
    elif (bare := BARE_OFFER.match(text)) is not None:
        offer = int(bare[1])
    return Request(tuple(s for s in skus if s.lower() in lowered), offer, text)


def asked_category(request: Request, unit: Unit) -> str | None:
    """Return the category leaf a buyer ordered by, when it named no SKU.

    11/12: q00 orders by category ("소파/의자/스툴"), gets a receipt naming the
    SKU, calls it forged and rates 1 star (21 of 21 reviews), and per-buyer means
    make that one vote cost our stars rank about 0.06. The kit accepts a receipt
    whose item is the order's item, which for Round 3 is the category leaf.
    """
    if request.skus or not unit.category:
        return None
    leaf = unit.category.split(">")[-1].strip()
    return leaf if leaf and leaf in request.text else None


def bargaining_offer(text: str, ask: int) -> int | None:
    """Parse a whole-message number, a ``pay N`` phrase, or assent to the ask."""
    if ASSENT.match(text) is not None:
        return ask
    matched = BARE_OFFER.match(text)
    if matched is not None:
        return int(matched[1])
    worded = OFFER_WORDS.search(text)
    if worded is not None:
        return int(worded[1])
    numbers = STANDALONE_NUMBER.findall(text)
    return int(numbers[0]) if len(numbers) == 1 else None


def legacy_item(text: str) -> str | None:
    """Return the week-07 item a message names; used before Round 3 (no stock)."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    found = [item for item in LEGACY_RESERVE if item in words]
    return found[0] if len(found) == 1 else None


class StockView:
    """Cached, mtime-checked reads of the kit's inventory and catalog files."""

    def __init__(self, folder: Path) -> None:
        """Bind to one ``.market`` folder."""
        self.folder: Path = folder
        self._inventory_mtime: float = -1.0
        self._inventory = read_inventory(folder)
        self._catalog_mtime: float = -1.0
        self._catalog: dict[str, CatalogItem] = {}

    def inventory(self) -> Inventory:
        """Return the latest inventory snapshot."""
        try:
            mtime = (self.folder / "inventory.json").stat().st_mtime
        except OSError:
            return self._inventory
        if mtime != self._inventory_mtime:
            self._inventory = read_inventory(self.folder)
            self._inventory_mtime = mtime
        return self._inventory

    def catalog(self) -> dict[str, CatalogItem]:
        """Return the catalog (sku -> item), reloaded when the kit rewrites it."""
        try:
            mtime = (self.folder / "catalog.json").stat().st_mtime
        except OSError:
            return self._catalog
        if mtime != self._catalog_mtime:
            self._catalog = read_catalog(self.folder)
            self._catalog_mtime = mtime
        return self._catalog

    def shelf_hours(self, sku: str) -> float | None:
        """Shelf life of a food SKU from the catalog, if known."""
        item = self.catalog().get(sku)
        return float(item.shelf_hours) if item and item.shelf_hours else None

    def fast_sellers(self) -> dict[str, float]:
        """Return SKU -> market median for fast market-first sellers (restock)."""
        path = self.folder.resolve().parent / FAST_FILE
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if mtime != getattr(self, "_fast_mtime", -1.0):
            self._fast = load_fast_sellers(path)
            self._fast_mtime = mtime
        return dict(getattr(self, "_fast", {}))

    def market_first_medians(self) -> dict[str, float]:
        """Return SKU -> market median for market-first SKUs (restock MF_FILE)."""
        path = self.folder.resolve().parent / MF_FILE
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if mtime != getattr(self, "_mf_mtime", -1.0):
            self._mf = load_fast_sellers(path)
            self._mf_mtime = mtime
        return dict(getattr(self, "_mf", {}))

    def buyer02_needs(self) -> dict[str, float]:
        """Return SKU -> buyer-02's largest stated shelf need (r3/fresh_list.py)."""
        path = self.folder.resolve().parent / NEEDS_FILE
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if mtime != getattr(self, "_needs_mtime", -1.0):
            self._needs = load_fresh_list(path)
            self._needs_mtime = mtime
        return dict(getattr(self, "_needs", {}))

    def fresh_list(self) -> dict[str, float]:
        """Return the buyer-02 food SKU list the manager writes (r3/fresh_list.py)."""
        path = self.folder.resolve().parent / FRESH_LIST_FILE
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if mtime != getattr(self, "_fresh_mtime", -1.0):
            self._fresh = load_fresh_list(path)
            self._fresh_mtime = mtime
        return dict(getattr(self, "_fresh", {}))


# A buyer saying it takes grade C (or any grade / refurbished stock).
ACCEPTS_C: Final = re.compile(
    r"grade\s*[:=-]?\s*c\b.{0,40}\b(ok|fine|accept|acceptable|welcome|allowed)|"
    r"\b(accept|accepts|accepting|ok with|fine with|allow)\b.{0,30}\bgrade\s*c\b|"
    r"\bany grade\b|\brefurbished\b.{0,20}\b(ok|fine|welcome)|c급\s*(가능|괜찮|허용)|"
    r"grades?\s*[:=]?\s*\[?\s*(?:['\"]?[ab]['\"]?\s*,\s*){1,2}['\"]?c\b",
    re.IGNORECASE,
)


# 11/13 19:0x: buyers that state a shelf life ("it must last 361.0 hours", "need:
# shelf life 181.0 h or more", "at least 414.0 hours of shelf life left") sold 3-26 %
# (93 asks at 3 %, 85 at 20 %, 104 at 26 %) while we offered the lowest-cost or
# soonest-expiring unit without reading the requirement.
SHELF_NEED = re.compile(
    r"(?:last|shelf[ -]?life)[^.;|]*?(\d+(?:\.\d+)?)\s*(?:h\b|hours?)"
    r"|(\d+(?:\.\d+)?)\s*(?:h|hours?)\s+(?:of\s+)?shelf"
    # 11/13 23:0x: 26512076 sends '... policy: {..., "min_hours_left": 17.05}'.
    r'|min_hours_left"?\s*:\s*(\d+(?:\.\d+)?)',
    re.IGNORECASE,
)


def required_shelf_hours(text: str) -> float | None:
    """Return the largest shelf life in hours the request asks for, if any."""
    hours = [float(a or b or c) for a, b, c in SHELF_NEED.findall(text)]
    return max(hours) if hours else None


def pick_unit(  # noqa: PLR0913, PLR0911, PLR0912, PLR0915, C901 - one choice
    units: tuple[Unit, ...],
    request: Request,
    now: float,
    excluded: set[str],
    *,
    cheapest: bool = False,
    fresh_skus: Mapping[str, float] | None = None,
    shelf_hours: Callable[[str], float | None] | None = None,
    plain_needs: Mapping[str, float] | None = None,
) -> Unit | None:
    """Pick the fitting unit that expires first (goods: oldest), SKU names first.

    With ``cheapest`` a category ask gets the A/B unit of the lowest absolute cost.
    ``fresh_skus`` (the buyer-02 list) with ``shelf_hours`` keeps fresh food of
    those SKUs for the plain form (:func:`fresh_choice`).
    """
    text = request.text.lower()
    available = [
        u
        for u in units
        if u.unit not in excluded
        and u.grade != "expired"
        and (u.expires_at is None or u.expires_at > now + 60)
    ]
    wanted = {s.lower() for s in request.skus}
    fit = [u for u in available if u.sku.lower() in wanted]
    if not fit:
        fit = [u for u in available if u.name and u.name.lower() in text]
    by_category = False
    if not fit:
        fit = [
            u
            for u in available
            if category_leaf(u.category).lower() in text and category_leaf(u.category)
        ]
        by_category = True
    if not fit:
        return None
    need = required_shelf_hours(request.text)
    if need is not None:
        lasting = [
            u for u in fit if u.expires_at is None or u.expires_at - now >= need * 3600
        ]
        # 11/14 03:0x: when no unit lasts as long as the ask states ("Must last at
        # least 540.0 hours"), selling the shorter one drew 19 two-star reviews in
        # 12 h ("Only 264.2 h of shelf life left; I needed 540.0 h"). We say we
        # have none that fits instead of selling a unit the buyer already refused.
        if not lasting:
            return None
        fit = lasting
    plain = PLAIN_OPENING.search(request.text.strip()) is not None

    def stale_kimchi(u: Unit) -> bool:
        if u.sku not in KIMCHI_SKUS or shelf_hours is None:
            return False
        left = u.shelf_left_ratio(now, shelf_hours(u.sku))
        return left is not None and left < KIMCHI_RATIO

    # 11/17 07:2x (owner): the plain form (buyer-02) gets only food that lasts the
    # largest need buyer-02 named for the SKU plus NEED_MARGIN_HOURS, else none in
    # stock. This replaced the two-kimchi 0.5 rule.
    if plain and plain_needs:
        named = {u.sku for u in fit if u.kind == "food" and u.sku in plain_needs}
        if named and all(u.sku in named for u in fit):
            need_h = max(plain_needs[sku] for sku in named) + NEED_MARGIN_HOURS
            fit = [
                u
                for u in fit
                if u.expires_at is None or u.expires_at - now >= need_h * 3600
            ]
            if not fit:
                return None
    # 11/17 08:2x (owner): grade A food goes to the dyishappy form first (it rates
    # A at its price 4-5, B 2-3). Other openings that do not name grade A alone get
    # a B unit when one fits; the plain form (buyer-02's shelf needs) is untouched.
    if (
        DYISHAPPY_A_FOOD_FIRST
        and not plain
        and not DYISHAPPY_FORM.search(request.text.strip())
        and all(u.kind == "food" for u in fit)
        and not names_only_a(request.text)
        and any(u.grade == "B" for u in fit)
    ):
        fit = [u for u in fit if u.grade != "A"]
    # 11/17 08:5x (owner, strategy 08:51): food with FRESH_RATIO+ of its shelf life
    # left goes to the dyishappy form, then the plain form (buyer-02); other
    # openings get an older unit that meets their stated need when one fits.
    if (
        FRESH_FOR_TWO_FORMS
        and shelf_hours is not None
        and not plain
        and not DYISHAPPY_FORM.search(request.text.strip())
        and all(u.kind == "food" for u in fit)
    ):

        def fresh_unit(u: Unit) -> bool:
            left = u.shelf_left_ratio(now, shelf_hours(u.sku))
            return left is not None and left >= FRESH_RATIO

        older = [u for u in fit if not fresh_unit(u)]
        if older:
            fit = older
    if (
        TAO_ON
        and TAO_FORM.search(request.text.strip())
        and all(u.kind == "food" for u in fit)
        and any(u.grade in {"B", "C"} for u in fit)
    ):
        fit = [u for u in fit if u.grade in {"B", "C"}]
    if a_first_buyer(request.text):
        graded_a = [u for u in fit if u.grade == "A"]
        fit = graded_a or fit
    # 11/16 16:5x (owner, urgent): ovotnwjd-maker (5 stars on 99 % of 2,160 deals in
    # 24 h; its first deal here drew a random 1) asks in the "I want to buy NAME
    # (SKU)." form, shared with gom31 (rates cheap deals highest). It pays 0.47-0.69
    # of list, so the A/B unit that costs least goes first; accept-down-to-cost
    # still applies (goods A at cost x A_REFUND_FLOOR).
    # 11/16 18:5x: the shelf form pays 0.50-0.60 of list for its best ratings; the
    # unit meeting the stated need with the lowest cost share goes first.
    if SHELF_NEED_FORM.search(request.text.strip()) and all(
        u.kind == "food" for u in fit
    ):
        return min(
            fit,
            key=lambda u: (u.cost / max(1, u.list_price), u.expires_at or 0.0, u.unit),
        )
    # 11/17 07:3x (owner): dyishappy rates grade A at or under 0.65 of list 4 and at or
    # under 0.585 5 (B/C 3 or less); its A unit with the lowest cost share goes first.
    if DYISHAPPY_FORM.search(request.text.strip()):
        graded_a = [u for u in fit if u.grade == "A"]
        if graded_a:
            return min(
                graded_a,
                key=lambda u: (u.cost / max(1, u.list_price), u.expires_at or 0.0),
            )
    if CHEAPEST_AB_FORM.search(request.text.strip()):
        graded_ab = [u for u in fit if u.grade in {"A", "B"}]
        if graded_ab:
            return min(
                graded_ab,
                key=lambda u: (u.cost, -(u.expires_at or float("inf")), u.unit),
            )
    listed = (
        bool(fresh_skus)
        and shelf_hours is not None
        and all(u.kind == "food" and u.sku in (fresh_skus or {}) for u in fit)
    )
    if listed and plain and shelf_hours is not None:
        return fresh_choice(fit, now, shelf_hours)
    # Buyers' kits pay for certified A or B unless the buyer opted into C: on 11/9
    # 136 of our 140 unpaid receipts in six hours were grade C units offered
    # oldest-first while A/B stock of the same SKU sat on the shelf. Offer C only
    # when the buyer accepts it or nothing else fits; within a grade group the
    # unit that expires first (goods: oldest) still goes first.
    c_first = c_first_buyer(request.text) or plain_goods_c_first(request.text, fit)
    low_order = low_grade_order(request.text)
    # 11/15 14:2x (owner): 315 food units (4.5M of cost, all C) expire before the
    # round closes and leave the book unsold. An opening that states its shelf need
    # (and does not ask for A or B by name) gets, among the units that meet it, one
    # that expires before the close first; its buyers paid such C (25512088: 96 C in
    # 24 h, median 47 h left). The plain form no longer gets 48 h food first.
    close_first = need is not None and A_OR_B_ASK.search(request.text) is None
    accepts_c = (
        ACCEPTS_C.search(text) is not None
        or c_first
        or (low_order is not None and "C" in low_order)
    )

    # 11/13 18:2x (owner: study the 37 % that sold, offer what loses us nothing,
    # for every buyer type): across all asks, food units costing <= 0.55 of list
    # sold 42-72 %, 0.60-0.65 sold 18-22 %, 0.70+ sold 0-8 %; goods <= 0.55 sold
    # 33-59 %, 0.65 sold 10 % and 0.70 sold 4 % of 559. 66 SKUs had a unit 3+ % of
    # list cheaper than the oldest one we offered first. Every ask now gets the
    # SKU's lowest-cost unit (the market anchor then prices it lowest, never under
    # cost x1.02); food within FOOD_URGENT_SECONDS of expiry still goes first.
    def urgent(u: Unit) -> int:
        expires = u.expires_at
        if plain:
            return 1
        return 0 if expires is not None and expires - now < FOOD_URGENT_SECONDS else 1

    # 11/13 20:1x: buyer-01 and buyer-02 ask plainly ("I want to buy X") yet need
    # 360-540 h of shelf life, and rated all 11 shorter units 2 stars in 12 h
    # ("Only 432.9 h of shelf life left; I needed 540.0 h"). Within one cost band
    # (COST_BAND of list) a unit with FRESH_SECONDS left goes first.
    def short_shelf(u: Unit) -> int:
        expires = u.expires_at
        return 1 if expires is not None and expires - now < FRESH_SECONDS else 0

    # 11/14 20:0x (task 2-Q): q00 orders by category with a budget per order. It
    # paid a median 12,000 for 72 processed-meat deals in 12 h (50 of them the
    # 17,900-list G2560a8c9) and walked from 6 of our offers of the 60,900-list
    # G9ac69ed6 at about 43,300: the lowest cost share is not the lowest price. A
    # cost-floor buyer's category ask gets the cheapest unit in absolute cost.
    if cheapest and by_category:
        # 11/15 15:4x (owner): q00 bought 434 food C of 809 food in 24 h and rates
        # by price against its budget (0.8 or less: 5.00 over 4,147 deals), so the
        # cheapest unit in absolute cost goes first whatever its grade.
        return min(
            fit,
            key=lambda u: (
                0 if stale_kimchi(u) else 1,
                u.cost,
                u.expires_at or float("inf"),
                u.unit,
            ),
        )

    return min(
        fit,
        key=lambda u: (
            *buyer_rank(u, c_first=c_first, low_order=low_order)[:1],
            0
            if accepts_c or u.grade != "C" or (close_first and expires_before_close(u))
            else 1,
            # Kimchi units under KIMCHI_RATIO go to a stated need they meet first.
            0 if need is not None and stale_kimchi(u) else 1,
            buyer_rank(
                u, c_first=c_first, low_order=low_order, close_first=close_first
            )[1:],
            # Listed SKUs, other openings: shortest life first, so the 0.75+ units
            # stay for the plain form (stated needs were already met above).
            (u.expires_at or float("inf")) if listed else 0.0,
            urgent(u),
            # Grade A before B: our goods B reviews average 4.47 against A 4.86
            # (a buyer that says it takes C gets the cheapest unit of any grade).
            0 if accepts_c or u.grade == "A" else 1,
            int(u.cost / max(1, u.list_price) / COST_BAND + 1e-9),
            short_shelf(u),
            u.cost / max(1, u.list_price),
            u.expires_at or float("inf"),
            u.supplied_at,
        ),
    )


def fresh_choice(
    fit: list[Unit], now: float, shelf_hours: Callable[[str], float | None]
) -> Unit | None:
    """Plain-form ask on a buyer-02 list SKU (owner 11/15 15:0x).

    (a) the cheapest unit with FRESH_RATIO or more of its shelf life left, else
    (b) the longest-lived unit with FAIR_RATIO or more, else (c) the longest-lived
    unit (none in stock until 11/15 23:5x).
    """

    def ratio(u: Unit) -> float:
        left = u.shelf_left_ratio(now, shelf_hours(u.sku))
        return left if left is not None else 1.0

    fresh = [u for u in fit if ratio(u) >= FRESH_RATIO]
    if fresh:
        return min(fresh, key=lambda u: (u.cost, -(u.expires_at or 0.0), u.unit))
    fair = [u for u in fit if ratio(u) >= FAIR_RATIO]
    if fair:
        return max(fair, key=lambda u: (u.expires_at or 0.0, -u.cost))
    # (c) was "none in stock". 11/15 23:5x (owner's rule: listed plain-form close
    # rate fell more than 5 points, 88.3 % -> 70 % / 68 % on 22 SKUs, while
    # buyer-02's shelf two-star share stayed ~10 %): released first, so the
    # longest-lived unit goes instead.
    return max(fit, key=lambda u: (u.expires_at or 0.0, -u.cost)) if fit else None


# 11/16 06:3x (owner, 06:25 measurement): over 12 h goods A sold 1,152 for 314M and
# kept 9.09M (2.9 %) while 24 h of A breaks (72 h warranty) refunded 30.98M, about
# 5 % of A takings; B kept 10.5 % and C 36.5 % with 1 h warranties. Breaks come a
# median 2.2-3.0 h after the sale, so the warranty stays and grade A goods are not
# sold under cost x A_REFUND_FLOOR (cost-floor buyers included). B, C and food keep
# their lines.
# 11/16 09:1x (owner, book defence step): the book gap to inextro fell to 24.8M
# (08:53; ours +3.0M/h, inextro +4.5M/h since 06:25), so the line went 1.05 -> 1.08
# for all grade A goods. Judged on goods profit net of refunds (1.05: 2.46M/h):
# higher with A sales within -25 % of the 1.05 hour -> 1.10 (the cap); lower -> one
# step back. 10:12 (1.05 h at 1.08): A adjusted profit (A margin - 5 % of A takings)
# 0.46M/h against 0.13M/h at 1.05, A sales 66.4/h against 81.0 (-18 %): 1.10 went out
# at 10:18 and came back to 1.08 at 10:3x (owner: hold 1.08 until the grade A 1 h
# warranty is judged, so the two effects do not mix).
A_REFUND_FLOOR: Final = 1.08


# 11/17 04:4x (owner night rule: book gap under 18M -> 2 h test): the book gap to
# inextro fell to 15.7M (inextro +9.0M in the last hour), so grade A goods listed
# under A_LOW_LIST_LINE take cost x A_LOW_FLOOR; dearer A keep A_REFUND_FLOOR.
A_LOW_LIST_LINE: Final = 500_000
A_LOW_FLOOR: Final = 1.10


def a_floor_factor(unit: Unit) -> float:
    """Cost multiple a grade A goods unit is never accepted under."""
    return A_LOW_FLOOR if unit.list_price < A_LOW_LIST_LINE else A_REFUND_FLOOR


DYISHAPPY_PRICE_STEPS: Final = ((0.575, 0.585), (0.64, 0.65))
DYISHAPPY_FLOOR: Final = 1.015


# 11/17 08:3x (owner, approved in chat: "스위치는 켜놔. 그것도 허용한다"): the one
# exception to never selling under cost. Grade A food for the dyishappy form sells
# at list x DYISHAPPY_BELOW_SHARE when that is at least cost x DYISHAPPY_BELOW_MIN,
# even under cost. Each such receipt adds (cost - price) to DYISHAPPY_LOSS_FILE; at
# DYISHAPPY_LOSS_CAP the exception turns itself off. Other buyers, goods and B/C
# never sell under cost.
DYISHAPPY_BELOW_ON: Final = True
DYISHAPPY_BELOW_SHARE: Final = 0.585
DYISHAPPY_BELOW_MIN: Final = 0.85
DYISHAPPY_LOSS_CAP: Final = 2_000_000
DYISHAPPY_LOSS_FILE: Final = "r3-dyishappy-loss.json"


def load_dyishappy_loss(path: Path) -> int:
    """Return the running loss of the dyishappy under-cost exception."""
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return 0
    return int(raw.get("loss") or 0) if isinstance(raw, dict) else 0


def add_dyishappy_loss(path: Path, loss: int, now: float) -> int:
    """Add one receipt's loss; return the new total."""
    total = load_dyishappy_loss(path) + max(0, loss)
    count = 0
    with contextlib.suppress(OSError, ValueError):
        raw = json.loads(path.read_text("utf-8"))
        count = int(raw.get("count") or 0) if isinstance(raw, dict) else 0
    with contextlib.suppress(OSError):
        _ = path.write_text(
            json.dumps({"loss": total, "count": count + 1, "ts": now}), "utf-8"
        )
    return total


def dyishappy_quote(
    unit: Unit, priced: Quote, text: str, loss_so_far: int | None = None
) -> Quote:
    """Price grade A for the dyishappy form at its 5- or 4-star share of list.

    Cost at most 0.575 of list -> 0.585 of list; at most 0.64 -> 0.65; otherwise the
    usual quote. Never under cost x DYISHAPPY_FLOOR (the A refund line is not used
    for this form, owner 11/17 07:3x), except grade A food under the owner's
    08:3x exception (``loss_so_far`` under DYISHAPPY_LOSS_CAP).
    """
    if not DYISHAPPY_FORM.search(text.strip()) or priced.grade_now != "A":
        return priced
    list_price = unit.list_price
    if (
        DYISHAPPY_BELOW_ON
        and unit.kind == "food"
        and loss_so_far is not None
        and loss_so_far < DYISHAPPY_LOSS_CAP
    ):
        below = math.floor(list_price * DYISHAPPY_BELOW_SHARE)
        if below >= unit.cost * DYISHAPPY_BELOW_MIN:
            return replace(priced, ask=below, accept_at=below, floor=below)
    floor = math.ceil(round(unit.cost * DYISHAPPY_FLOOR, 6))
    for cost_share, price_share in DYISHAPPY_PRICE_STEPS:
        if unit.cost <= list_price * cost_share:
            price = max(floor, math.floor(list_price * price_share))
            return replace(priced, ask=price, accept_at=price, floor=floor)
    return priced


def refund_floor(unit: Unit, priced: Quote) -> Quote:
    """Keep a grade A goods quote at or above cost x a_floor_factor(unit)."""
    if unit.kind != "goods" or priced.grade_now != "A":
        return priced
    line = math.ceil(round(unit.cost * a_floor_factor(unit), 6))
    if priced.accept_at >= line:
        return priced
    return replace(
        priced,
        accept_at=line,
        floor=max(priced.floor, line),
        ask=max(priced.ask, line),
    )


# 11/16 16:3x (owner, book defence): totato2203-r3 rates every deal 5 and pays 0.60-0.70
# of list elsewhere; in 12 h it brought 59.0M of takings and 14.8M of margin here.
# Grade C goods offered to its opening form are priced TOTATO_C_MARKUP higher (ask,
# accept line and floor). Judged at 1 h: C sales to this form down 20 %+ -> back.
# 18:2x: goods C settles to totato fell 18 -> 13 in 1.6 h (-28 %, stars all 5), so
# the markup is back to 1.0 (the function stays so it can be put back).
TOTATO_C_MARKUP: Final = 1.0


def totato_c_markup(unit: Unit, priced: Quote, text: str) -> Quote:
    """Raise a grade C goods quote for totato's opening form by TOTATO_C_MARKUP."""
    if (
        TOTATO_C_MARKUP == 1.0
        or unit.kind != "goods"
        or priced.grade_now != "C"
        or not C_FIRST_OPENINGS[0].search(text.strip())
    ):
        return priced

    def up(value: int) -> int:
        return math.ceil(round(value * TOTATO_C_MARKUP, 6))

    return replace(
        priced,
        ask=up(priced.ask),
        accept_at=up(priced.accept_at),
        floor=up(priced.floor),
    )


# 11/17 07:5x (owner 07:35 task 4, 2 h test): grade B ask and accept line +5 % for
# the plain form (buyer-02, chanarun; usagi and jiseokkk ask in it too) and the
# cheapest-in-town form, food and goods; the floor and clearance units are kept.
# Keep if B margin rose; back to 1.0 if deals fell 25 %+ without it.
# 10:08 judged (receipts aged 45 min+, 06:51-08:06 vs 08:06-09:21): B settles in the
# two forms 95 -> 17, B margin 0.179M -> 0.035M (buyer-02 need rule overlaps): off.
B_TEST_MARKUP: Final = 1.0


def b_test_markup(unit: Unit, priced: Quote, text: str) -> Quote:
    """Raise a grade B quote for the plain and cheapest-in-town forms."""
    stripped = text.strip()
    if (
        B_TEST_MARKUP == 1.0
        or priced.grade_now != "B"
        or priced.clearance
        or not (PLAIN_OPENING.search(stripped) or C_FIRST_OPENINGS[1].search(stripped))
    ):
        return priced
    del unit  # same signature as the other quote steps

    def up(value: int) -> int:
        return math.ceil(round(value * B_TEST_MARKUP, 6))

    return replace(priced, ask=up(priced.ask), accept_at=up(priced.accept_at))


# 11/17 08:0x (owner, 2 h test; r3/restock.py INEXTRO_SKUS): units of the sofas
# inextro sells most, bought today, accept down to cost x INEXTRO_MARGIN. Older
# units and other SKUs keep their lines; never under the unit's own cost.
INEXTRO_SINCE: Final = 1794841200.0  # 2026-11-17 00:00 KST


def inextro_quote(unit: Unit, priced: Quote) -> Quote:
    """Lower the accept line of today's INEXTRO_SKUS units to cost x INEXTRO_MARGIN."""
    if (
        unit.kind != "goods"
        or unit.sku not in INEXTRO_SKUS
        or unit.supplied_at < INEXTRO_SINCE
        or priced.clearance
        or priced.grade_now != "A"  # 10:3x (owner): A only; B/C keep their lines
    ):
        return priced
    line = max(unit.cost, math.ceil(round(unit.cost * INEXTRO_MARGIN, 6)))
    if priced.accept_at <= line:
        return priced
    return replace(
        priced,
        accept_at=line,
        floor=min(priced.floor, line),
        ask=max(priced.ask, line),
    )


DYISHAPPY_A_FOOD_FIRST: Final = True
FRESH_FOR_TWO_FORMS: Final = True


def names_only_a(text: str) -> bool:
    """Return True for an opening that names grade A and not B or C."""
    return (
        NAMES_A.search(text) is not None
        and NAMES_B.search(text) is None
        and ACCEPTS_C.search(text) is None
    )


# 11/17 08:2x (owner, 2 h test): market-first goods whose held units sold 40 %+ in
# the last hour (r3/restock.py FAST_FILE) accept, for units bought today, no lower
# than max(cost x 1.08, min(market median x 0.98, cost x 1.15)). Keep if those
# SKUs' margin per hour rose; back if sales fell 40 %+ and margin fell.
FAST_ACCEPT_ON: Final = True


def fast_quote(unit: Unit, priced: Quote, median: float | None) -> Quote:
    """Raise today's fast-seller goods accept line toward the market median."""
    if (
        not FAST_ACCEPT_ON
        or median is None
        or unit.kind != "goods"
        or unit.sku in INEXTRO_SKUS
        or unit.supplied_at < INEXTRO_SINCE
        or priced.clearance
        or priced.grade_now != "A"  # 10:3x (owner): A only
    ):
        return priced
    low = math.ceil(round(unit.cost * 1.08, 6))
    high = math.ceil(round(unit.cost * 1.15, 6))
    line = max(low, min(math.floor(median * 0.98), high))
    if priced.accept_at >= line:
        return priced
    return replace(priced, accept_at=line, ask=max(priced.ask, line))


# 11/17 10:3x (owner, strategy 10:36): refunds hit 2.36M in an hour. Goods B sold
# at cost x 1.02 (the market anchor's floor) breaks within the 1 h warranty 3-4 %
# of the time (5 of 148 in 48 h); B at 500k+ netted -1.04M in 3 h. Goods B accept
# no lower than cost x B_GOODS_HIGH (ask 500k+) or cost x B_GOODS_LOW.
B_GOODS_FLOOR_ON: Final = True
B_GOODS_LINE: Final = 500_000
B_GOODS_HIGH: Final = 1.10
B_GOODS_LOW: Final = 1.06


def b_goods_floor(unit: Unit, priced: Quote) -> Quote:
    """Keep a grade B goods quote at or above its refund-safe accept line."""
    if not B_GOODS_FLOOR_ON or unit.kind != "goods" or priced.grade_now != "B":
        return priced
    factor = B_GOODS_HIGH if priced.ask >= B_GOODS_LINE else B_GOODS_LOW
    line = math.ceil(round(unit.cost * factor, 6))
    if priced.accept_at >= line:
        return priced
    return replace(
        priced,
        accept_at=line,
        floor=max(priced.floor, line),
        ask=max(priced.ask, line),
    )


# 11/17 10:5x (owner, strategy 10:52): today's grade A units of market-first SKUs
# accept at max(cost x MF_LOW, min(median x MF_MEDIAN_SHARE, cost x MF_HIGH)):
# dear-market SKUs keep 1.08-1.15, SKUs that clear only 1.05-1.08 sell near 1.05.
# The sofas keep their own test (inextro_quote); B and C are untouched.
MF_QUOTE_ON: Final = True
MF_LOW: Final = 1.05
MF_HIGH: Final = 1.15
MF_MEDIAN_SHARE: Final = 0.98


def mf_quote(unit: Unit, priced: Quote, median: float | None) -> Quote:
    """Set today's market-first grade A accept line from the market median."""
    if (
        not MF_QUOTE_ON
        or median is None
        or unit.kind != "goods"
        or priced.grade_now != "A"
        or unit.sku in INEXTRO_SKUS
        or unit.supplied_at < INEXTRO_SINCE
        or priced.clearance
    ):
        return priced
    low = math.ceil(round(unit.cost * MF_LOW, 6))
    high = math.ceil(round(unit.cost * MF_HIGH, 6))
    line = max(low, min(math.floor(median * MF_MEDIAN_SHARE), high))
    if priced.accept_at == line:
        return priced
    return replace(
        priced,
        accept_at=line,
        floor=min(priced.floor, line),
        ask=max(priced.ask, line),
    )


# 11/17 11:0x (owner, strategy 11:07, 2 h test): goods C buyers took the first ask
# 93 % of the time (87 in 3 h, 2.78M/h); removing the endgame cut raised asks 13 %
# and sales still rose 48 -> 55. Goods C asks go up C_GOODS_ASK_UP; the accept line
# and floor stay. Judged: C goods margin/h over 2.78M keeps it; sales -30 % with
# lower margin, or falling stars from its buyers, takes it back.
# 13:08 (owner): taken back. 84 min before/after: receipts 57 -> 62, settles 26 -> 33,
# margin 1.60M -> 1.03M; C over 200k fell to 0-1 per 30 min and jiseokkk stopped.
C_GOODS_ASK_UP: Final = 1.0


def c_goods_ask(unit: Unit, priced: Quote) -> Quote:
    """Raise a grade C goods ask by C_GOODS_ASK_UP (accept line unchanged)."""
    if (
        C_GOODS_ASK_UP == 1.0
        or unit.kind != "goods"
        or priced.grade_now != "C"
        or priced.clearance
    ):
        return priced
    ask = max(priced.accept_at, math.ceil(round(priced.ask * C_GOODS_ASK_UP, 6)))
    return replace(priced, ask=ask)


# 11/17 13:1x (owner, strategy 13:08): refunds in 2.5 h were A 2.55M, C 1.13M, B
# 0.29M; 13:03 a 4-seat sofa A (1.44M, sold at cost x 1.09 with a 6 h warranty
# to Kiro729) broke 4.5 h after sale. Goods asks naming a warranty over 1 h accept
# no lower than cost x WARRANTY_HIGH (ask 500k+) or WARRANTY_LOW, any grade; the
# 1.05 and fast-seller lines do not lower them.
WARRANTY_FLOOR_ON: Final = True
WARRANTY_LINE: Final = 500_000
WARRANTY_HIGH: Final = 1.12
WARRANTY_LOW: Final = 1.10


def warranty_floor(unit: Unit, priced: Quote, asked_hours: int | None) -> Quote:
    """Keep a goods quote for a >1 h warranty ask at or above its refund line."""
    if (
        not WARRANTY_FLOOR_ON
        or unit.kind != "goods"
        or asked_hours is None
        or asked_hours <= 1
        or priced.clearance
    ):
        return priced
    factor = WARRANTY_HIGH if priced.ask >= WARRANTY_LINE else WARRANTY_LOW
    line = math.ceil(round(unit.cost * factor, 6))
    if priced.accept_at >= line:
        return priced
    return replace(
        priced,
        accept_at=line,
        floor=max(priced.floor, line),
        ask=max(priced.ask, line),
    )


# 11/17 13:5x (owner, strategy 13:52): Zinus grade A goods sold since 07:10 broke
# 5 of 75 times (6.7 %, margin 1.44M, refunds 1.11M) against 3 of 686 for other A.
# They accept no lower than cost x ZINUS_FLOOR (the 1.05 and fast lines don't
# lower them); restock buys them at ZINUS_FLOOR too.
ZINUS_MARK: Final = "지누스"
ZINUS_FLOOR: Final = 1.12


def zinus_floor(unit: Unit, priced: Quote) -> Quote:
    """Keep a Zinus grade A goods quote at or above cost x ZINUS_FLOOR."""
    if (
        unit.kind != "goods"
        or priced.grade_now != "A"
        or ZINUS_MARK not in (unit.name or "")
        or priced.clearance
    ):
        return priced
    line = math.ceil(round(unit.cost * ZINUS_FLOOR, 6))
    if priced.accept_at >= line:
        return priced
    return replace(
        priced,
        accept_at=line,
        floor=max(priced.floor, line),
        ask=max(priced.ask, line),
    )


def expires_before_close(u: Unit) -> bool:
    """Return True for food that will expire before the round closes."""
    return u.kind == "food" and u.expires_at is not None and u.expires_at <= ROUND_END


def buyer_rank(
    u: Unit,
    *,
    c_first: bool,
    low_order: dict[str, int] | None,
    close_first: bool = False,
) -> tuple[int, int, float]:
    """Rank a unit for this opening's buyer: (C-first goods, food order, expiry)."""
    c_rank = 0 if c_first and u.kind == "goods" and u.grade == "C" else 1
    if u.kind != "food":
        return (c_rank, 0, 0.0)
    expiry = u.expires_at or float("inf")
    if low_order is not None:
        # C takers get the C that expires soonest first (11/15 14:2x, owner).
        soonest = expiry if "C" in low_order else 0.0
        return (c_rank, low_order.get(u.grade, len(low_order)), soonest)
    if close_first and expires_before_close(u):
        return (c_rank, 0, expiry)
    return (c_rank, 1 if close_first else 0, 0.0)


def _ask_cap(unit: Unit, policy: SellerPolicy) -> int:
    list_price = max(1, unit.cost, unit.list_price)
    ratio = (
        policy.cheap_max_ask_ratio
        if list_price < policy.cheap_list_price
        else policy.max_ask_ratio
    )
    return round(list_price * ratio)


def priceable_goods_unit(
    original: Unit,
    units: tuple[Unit, ...],
    excluded: set[str],
    now: float,
    policy: SellerPolicy,
) -> Unit:
    """Unblock a costly FIFO quote using an available same-SKU, same-grade unit."""
    if original.kind != "goods":
        return original
    priced = quote(original, now, None, policy)
    if priced.ask <= _ask_cap(original, policy):
        return original
    choices: list[tuple[Unit, Quote]] = []
    for candidate in units:
        if (
            candidate.unit in excluded
            or candidate.sku != original.sku
            or candidate.grade != original.grade
            or candidate.kind != "goods"
            or (candidate.expires_at is not None and candidate.expires_at <= now + 60)
        ):
            continue
        candidate_quote = quote(candidate, now, None, policy)
        if candidate_quote.ask <= _ask_cap(candidate, policy):
            choices.append((candidate, candidate_quote))
    if not choices:
        return original
    return min(
        choices,
        key=lambda row: (row[1].ask, row[1].accept_at, row[0].supplied_at),
    )[0]


# A buyer naming grade A without grade B (or any grade) wants A only.
NAMES_A: Final = re.compile(r"\bgrade\s*[:=-]?\s*a\b|a급", re.IGNORECASE)
NAMES_B: Final = re.compile(
    r"\bgrade\s*[:=-]?\s*b\b|b급|\bor\s+b\b|/\s*b\b|,\s*['\"]?b\b|\bany grade\b",
    re.IGNORECASE,
)
GRADE_ORDER: Final = {"A": 0, "B": 1, "C": 2}


def market_fit_unit(  # noqa: PLR0913, PLR0917 - one choice, explicit inputs
    original: Unit,
    units: tuple[Unit, ...],
    excluded: set[str],
    now: float,
    policy: SellerPolicy,
    median: float | None,
    text: str,
) -> Unit:
    """Offer a same-SKU unit whose lowest price clears the market when ours cannot.

    11/10: a couch's A unit cost us 2.39M, so its lowest price (2.58M) sat above
    the market's median paid (2.13M); it was offered 70 times and never sold while
    a B unit that cost 1.36M stayed on the shelf. When the picked unit's floor is
    above the six-hour median, the best grade whose floor is not takes its place:
    B only when the buyer did not name grade A alone, C only when it accepts C.
    """
    if original.kind != "goods" or median is None:
        return original

    def clearing_accept(candidate: Unit) -> int:
        # 11/12: compare what we accept after the market anchor (down to cost x
        # 1.02), not the raw cost x 1.18 quote. A mattress A unit at 0.759 of
        # list was offered while A units at 0.604 and 0.627 sat on the shelf:
        # their raw quotes (~0.70) were above the median, so no switch happened.
        # 37 of 105 unsold offers on units with a floor of 0.72+ of list had
        # such a same-SKU unit on the shelf.
        raw = quote(candidate, now, None, policy)
        return market_anchored(raw, candidate.cost, median, policy.min_margin).accept_at

    if clearing_accept(original) <= median:
        return original
    if original.grade == "C" and c_first_buyer(text):
        return original  # the C-first buyer's unit is already the cheapest grade
    if original.grade == "A" and a_first_buyer(text):
        return original  # the A-first buyer keeps grade A (11/16 08:5x, owner)
    accepts_c = ACCEPTS_C.search(text) is not None
    only_a = (
        NAMES_A.search(text) is not None
        and NAMES_B.search(text) is None
        and not accepts_c
    )
    allowed = {"A"} if only_a else ({"A", "B", "C"} if accepts_c else {"A", "B"})
    choices: list[tuple[Unit, int]] = []
    for candidate in units:
        if (
            candidate.unit in excluded
            or candidate.unit == original.unit
            or candidate.sku != original.sku
            or candidate.kind != "goods"
            or candidate.grade not in allowed
            or (candidate.expires_at is not None and candidate.expires_at <= now + 60)
        ):
            continue
        accept = clearing_accept(candidate)
        if accept <= median:
            choices.append((candidate, accept))
    if not choices:
        return original
    return min(
        choices,
        key=lambda row: (GRADE_ORDER.get(row[0].grade, 3), row[1], row[0].supplied_at),
    )[0]


def ask_data(deal: Deal, sku: str, now: float) -> dict[str, object]:
    """Build the ask's data part: the price plus unit facts a buyer's code can check.

    Other shops send the market certificate in the first reply, so a buyer can see
    the grade and the expiry before it agrees; ours sent only the ask. The unit,
    its certified grade, the market's signed certificate and, for food, the hours
    of shelf life left now go with it (the 11/4 advisor's #18: buyers refused 211
    deals a day at checkout for shelf life, and shelf life tops our complaints).
    Data only: the text keeps its digit-free middle for counterpart parsers.
    """
    data: dict[str, object] = {"ask": deal.ask, "sku": sku}
    unit = deal.unit
    if unit is None:
        return data
    data["unit"] = unit.unit
    data["grade_now"] = deal.quote.grade_now
    if unit.cert:
        data["cert"] = unit.cert
    if unit.expires_at is not None:
        data["hours_left"] = round(max(0.0, (unit.expires_at - now) / 3600), 1)
    return data


class R3Seller(AgentExecutor):
    """Deterministic A2A seller over the kit's stock, with honest receipts."""

    def __init__(  # noqa: PLR0913 - explicit wiring, no hidden globals
        self,
        name: str,
        *,
        signer: ReceiptSigner | None,
        stock: StockView,
        policy_file: Path,
        events: EventLedger | None,
        emit: Callable[[str], None],
        reader: ModelClient | None = None,
        ledger: LedgerDB | None = None,
    ) -> None:
        """Keep per-task state in memory; the kit owns the durable ledger."""
        self.name: str = name
        self.signer: ReceiptSigner | None = signer
        self.stock: StockView = stock
        self.policy_file: Path = policy_file
        self.events: EventLedger | None = events
        self.emit: Callable[[str], None] = emit
        self.reader: ModelClient | None = reader
        self.ledger: LedgerDB | None = ledger
        self.medians: dict[str, tuple[float, float | None]] = {}
        self.reads: dict[str, int] = {}
        self.suspicious: dict[str, int] = {}
        self.open: dict[str, Deal] = {}
        self.committed: dict[str, float] = {}
        self.closed: set[str] = set()
        self.below_cost: dict[str, int] = {}
        self.locks: dict[str, anyio.Lock] = {}

    def policy(self) -> SellerPolicy:
        """Read the active seller policy (cheap; the file is small)."""
        return load_policy(self.policy_file).seller

    def below_market(self, sku: str, ask: int) -> bool:
        """Say whether the public ledger shows our ask under the SKU's median.

        The median covers every shop's settlements of the last six hours and needs
        at least five of them; it is cached for a minute per SKU. Any read failure
        means no claim, so the line is never written without evidence.
        """
        median = self.market_median(sku)
        return median is not None and ask < median

    def market_median(self, sku: str, grade: str | None = None) -> float | None:
        """Six-hour median paid price of a SKU over all shops, cached a minute."""
        if self.ledger is None:
            return None
        now = time.time()
        key = f"{sku}|{grade or ''}"
        stamp, median = self.medians.get(key, (0.0, None))
        if now - stamp > MEDIAN_CACHE_SECONDS:
            try:
                median = self.ledger.median_price(
                    sku, now - MEDIAN_WINDOW_SECONDS, grade=grade
                )
            except (OSError, sqlite3.Error):
                median = None
            self.medians[key] = (now, median)
        return median

    def excluded_units(self, now: float) -> set[str]:
        """Units promised to another open task or just sold and awaiting checkout."""
        for task_id, deal in list(self.open.items()):
            if now - deal.opened_at > HOLD_SECONDS:
                del self.open[task_id]
                self.closed.add(task_id)
        for unit_id, stamp in list(self.committed.items()):
            if now - stamp > COMMIT_SECONDS:
                del self.committed[unit_id]
        held = {d.unit.unit for d in self.open.values() if d.unit is not None}
        return held | set(self.committed)

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Serialize turns per task; one tool-free decision per message."""
        task = context.current_task
        if task is None:
            if context.task_id is None or context.context_id is None:
                raise InvalidParamsError(message="Task identifiers are required.")
            task = Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message] if context.message is not None else [],
            )
            await event_queue.enqueue_event(task)
            self.emit(f"seller.first task={task.id} note={opening_note(context)}")
        async with self.locks.setdefault(task.id, anyio.Lock()):
            if task.id in self.closed:
                raise InvalidParamsError(message="The negotiation is finished.")
            updater = TaskUpdater(event_queue, task.id, task.context_id)
            text = context.get_user_input() or ""
            if task.id not in self.open and declined_buyer(text, self.stock.catalog()):
                self.emit(f"seller.refused task={task.id} reason=declined_buyer")
                self.closed.add(task.id)
                await updater.reject(
                    updater.new_agent_message([Part(text=DECLINED_REPLY)])
                )
                return
            await self.turn(task.id, text, updater)

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Release the held unit."""
        task = context.current_task
        if task is None:
            raise InvalidParamsError(message="Cancellation requires an existing task.")
        async with self.locks.setdefault(task.id, anyio.Lock()):
            _ = self.open.pop(task.id, None)
            self.closed.add(task.id)
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()

    async def turn(self, task_id: str, text: str, updater: TaskUpdater) -> None:
        """Open a negotiation or answer an offer."""
        now = time.time()
        policy = self.policy()
        deal = self.open.get(task_id)
        # Screen before parsing: a number inside an injection is not an offer.
        flag = suspicious(text)
        if deal is None:
            deal = (
                None if flag is not None else self.opening(task_id, text, now, policy)
            )
            if deal is None:
                self.emit(f"seller.refused task={task_id} reason={flag or 'no_stock'}")
                self.closed.add(task_id)
                await updater.reject(
                    updater.new_agent_message(
                        [Part(text="Sorry, we have none of that in stock.")]
                    )
                )
                return
            self.open[task_id] = deal
            offer = self.opening_offer(text, deal)
            if offer is None:
                await self.publish_ask(task_id, deal, updater, opening=True)
                return
        else:
            offer = None if flag is not None else bargaining_offer(text, deal.ask)
            deal.rounds += 1
        hint, answer = "", ""
        if deal.rounds > 0 and flag is not None:
            hint = self.screened(task_id, flag)
        elif offer is None and deal.rounds > 0:
            offer, hint, answer = await self.read_prose(task_id, text, deal)
        deal.last_offer = offer
        if hint == "restate" and deal.rounds < policy.max_rounds:
            # A question or unreadable prose never lowers the ask; a question gets
            # the model's screened answer (or the fact line) above the price line.
            await self.publish_ask(
                task_id, deal, updater, opening=False, facts=True, answer=answer
            )
            return
        if hint == "reject":
            _ = self.open.pop(task_id, None)
            self.closed.add(task_id)
            await updater.reject(updater.new_agent_message([Part(text="No deal.")]))
            return
        action, price = decide(
            offer, deal.ask, deal.quote.accept_at, deal.rounds, policy
        )
        match action:
            case "accept":
                await self.accept(task_id, deal, price, now, policy, updater)
            case "reject":
                _ = self.open.pop(task_id, None)
                self.closed.add(task_id)
                await updater.reject(updater.new_agent_message([Part(text="No deal.")]))
            case _:
                deal.ask = min(deal.ask, price)
                await self.publish_ask(task_id, deal, updater, opening=deal.rounds == 0)

    def screened(self, task_id: str, flag: str) -> str:
        """Count a screened message; restate once, close the talk on the second."""
        count = self.suspicious.get(task_id, 0) + 1
        self.suspicious[task_id] = count
        self.emit(f"seller.screened task={task_id} reason={flag} count={count}")
        return "reject" if count >= SUSPICIOUS_LIMIT else "restate"

    def public_facts(self, deal: Deal, now: float) -> dict[str, object]:
        """Return the only facts a model may answer a buyer from; nothing about cost."""
        unit = deal.unit
        if unit is None:
            return {"item": str(deal.legacy_item), "quantity": 1}
        facts: dict[str, object] = {
            "item": unit.name,
            "sku": unit.sku,
            "kind": unit.kind,
            "quantity": 1,
            "grade": deal.quote.grade_now,
            "certificate": "attached, issued by the market",
            "warranty_hours": deal_warranty(deal, self.policy())
            if unit.kind == "goods"
            else "none (food)",
            "current_ask": deal.ask,
        }
        if unit.kind == "food" and unit.expires_at is not None:
            facts["shelf_life_left_hours"] = round((unit.expires_at - now) / 3600, 1)
        return facts

    async def read_prose(
        self, task_id: str, text: str, deal: Deal
    ) -> tuple[int | None, str, str]:
        """Read a non-numeric buyer message; return (offer, hint, answer).

        Hints: ``""`` act on the offer, ``restate`` repeat the ask with facts (and
        the model's screened answer when the buyer asked something), ``concede``
        make one policy concession, ``reject`` close the talk. Without a model,
        prose is restated, never discounted (a question is not an offer).
        """
        if self.reads.get(task_id, 0) >= MAX_READS_PER_TASK:
            return None, "restate", ""
        self.reads[task_id] = self.reads.get(task_id, 0) + 1
        name = deal.unit.name if deal.unit is not None else str(deal.legacy_item)
        read = await read_buyer_message(
            self.reader,
            text,
            item=name,
            ask=deal.ask,
            facts=self.public_facts(deal, time.time()),
        )
        if read is None:
            return None, "restate", ""
        self.emit(
            f"seller.read task={task_id} intent={read.intent} offer={read.offer} "
            f"answer={bool(read.answer)} screened={read.screened or '-'}"
        )
        if read.intent == "suspicious":
            count = self.suspicious.get(task_id, 0) + 1
            self.suspicious[task_id] = count
            return None, "reject" if count >= SUSPICIOUS_LIMIT else "restate", ""
        if read.intent in ("offer", "accept") and read.offer is not None:
            return read.offer, "", ""
        hint = {"reject": "reject", "ask_discount": "concede"}.get(
            read.intent, "restate"
        )
        return None, hint, read.answer

    @staticmethod
    def opening_offer(text: str, deal: Deal) -> int | None:
        """Read an explicit opening offer; week-07 phrasing before Round 3."""
        if deal.legacy_item is not None:
            parsed = opening_offer(text)
            if parsed is not None and parsed.sku == deal.legacy_item:
                return parsed.price
            return None
        return parse_request(text, frozenset()).offer

    def buyer_floor(self, task_id: str, text: str, unit: Unit, priced: Quote) -> Quote:
        """Accept down to cost for a buyer in COST_FLOOR_BUYERS, never below."""
        if not cost_floor_buyer(text) or priced.accept_at <= unit.cost:
            return priced
        self.emit(
            f"seller.cost_floor task={task_id} sku={unit.sku} "
            f"accept_at={priced.accept_at}->{unit.cost}"
        )
        return replace(priced, accept_at=unit.cost, floor=min(priced.floor, unit.cost))

    def opening(
        self, task_id: str, text: str, now: float, policy: SellerPolicy
    ) -> Deal | None:
        """Find the unit a buyer asked for and price it; Rounds 0-2 use fixed items."""
        inventory = self.stock.inventory()
        if inventory.round < ECON_ROUND:
            item = legacy_item(text)
            if item is None:
                return None
            reserve = LEGACY_RESERVE[item]
            ask = round(reserve * 1.10)
            legacy = Quote(ask, reserve, reserve, "A", clearance=False, stale=False)
            return Deal(None, item, legacy, ask)
        request = parse_request(text, frozenset(u.sku.lower() for u in inventory.units))
        excluded = self.excluded_units(now)
        units = offerable_units(inventory.units, text)
        # 11/14 22:31: the q00 cheapest-unit path (20:26) was turned off; q00 did
        # not visit in 2 h. 11/15 15:4x (owner): back on. In 24 h q00 left 37 "No
        # deal for 김치 within budget N; my last offer was M" against 20 kimchi deals
        # here; the unit picked by cost share was often dear in absolute cost.
        named = NAMED_BUYER.search(text)
        unit = pick_unit(
            units,
            request,
            now,
            excluded,
            cheapest=named is not None and named.group(1) in COST_FLOOR_BUYERS,
            fresh_skus=self.stock.fresh_list(),
            shelf_hours=self.stock.shelf_hours,
            plain_needs=self.stock.buyer02_needs(),
        )
        if unit is None:
            self.emit(f"seller.no_stock task={task_id} text={text[:80]!r}")
            self.note_cit_need(text, request)
            return None
        original = unit
        unit = priceable_goods_unit(unit, units, excluded, now, policy)
        unit = market_fit_unit(
            unit,
            units,
            excluded,
            now,
            policy,
            self.market_median(unit.sku) if unit.kind == "goods" else None,
            text,
        )
        if unit.unit != original.unit:
            self.emit(
                " ".join(
                    (
                        f"seller.unit_switch task={task_id} sku={unit.sku}",
                        f"grade={unit.grade} previous={original.unit}",
                        f"selected={unit.unit}",
                    )
                )
            )
        priced = quote(unit, now, self.stock.shelf_hours(unit.sku), policy)
        if unit.kind == "goods" and not priced.clearance:
            anchored = market_anchored(
                priced,
                unit.cost,
                self.market_median(unit.sku, priced.grade_now),
                anchor_margin(unit, now, policy.min_margin),
            )
            if anchored is not priced:
                self.emit(
                    f"seller.market_anchor task={task_id} sku={unit.sku} "
                    f"ask={priced.ask}->{anchored.ask} "
                    f"accept_at={priced.accept_at}->{anchored.accept_at}"
                )
                priced = anchored
        elif unit.kind == "food" and not priced.clearance:
            capped = buyer_share_capped(priced, unit.cost, unit.list_price)
            if capped is not priced:
                self.emit(
                    f"seller.share_cap task={task_id} sku={unit.sku} "
                    f"accept_at={priced.accept_at}->{capped.accept_at}"
                )
                priced = capped
            # Owner 11/13 15:0x (benchmark top shops): over 12 h the market paid
            # 0.600 of list for kimchi, 0.580 juice, 0.617 grain, 0.608 meat; we
            # sold at 0.652, 0.642, 0.659, 0.656 (Nocha12 sold 2,388 kimchi a day
            # at 0.628, we 197). Our food cost is 0.455 of list (median), so food
            # takes the goods' market anchor too, never under cost x1.02.
            anchored = market_anchored(
                priced,
                unit.cost,
                self.market_median(unit.sku, priced.grade_now),
                policy.min_margin,
                accept_share=FOOD_ACCEPT_SHARE,
                ask_share=FOOD_ASK_SHARE,
            )
            if anchored is not priced:
                self.emit(
                    f"seller.market_anchor task={task_id} sku={unit.sku} "
                    f"ask={priced.ask}->{anchored.ask} "
                    f"accept_at={priced.accept_at}->{anchored.accept_at} food"
                )
                priced = anchored
        priced = self.buyer_floor(task_id, text, unit, priced)
        priced = refund_floor(unit, priced)
        priced = totato_c_markup(unit, priced, text)
        priced = self.dyishappy_priced(task_id, unit, priced, text)
        priced = b_test_markup(unit, priced, text)
        priced = inextro_quote(unit, priced)
        priced = fast_quote(unit, priced, self.stock.fast_sellers().get(unit.sku))
        priced = mf_quote(unit, priced, self.stock.market_first_medians().get(unit.sku))
        priced = b_goods_floor(unit, priced)
        priced = c_goods_ask(unit, priced)
        priced = warranty_floor(unit, priced, asked_warranty(text))
        priced = zinus_floor(unit, priced)
        priced = tao_quote(unit, priced, text)
        self.emit(
            f"seller.open task={task_id} unit={unit.unit} sku={unit.sku} "
            f"grade={priced.grade_now} cost={unit.cost} ask={priced.ask} "
            f"accept_at={priced.accept_at} clearance={priced.clearance}"
        )
        return Deal(
            unit,
            None,
            priced,
            priced.ask,
            asked_category=asked_category(request, unit),
            warranty_ask=asked_warranty(text),
        )

    def dyishappy_priced(
        self, task_id: str, unit: Unit, priced: Quote, text: str
    ) -> Quote:
        """Apply the dyishappy price; remember a quote under cost (owner 08:3x)."""
        loss_path = self.stock.folder.resolve().parent / DYISHAPPY_LOSS_FILE
        priced = dyishappy_quote(unit, priced, text, load_dyishappy_loss(loss_path))
        if priced.accept_at < unit.cost:
            self.below_cost[task_id] = unit.cost
        return priced

    def record_below_cost(
        self, task_id: str, item: str, price: int, now: float
    ) -> None:
        """Add an under-cost dyishappy receipt's loss to the running total."""
        cost = self.below_cost.pop(task_id, None)
        if cost is None or price >= cost:
            return
        loss_path = self.stock.folder.resolve().parent / DYISHAPPY_LOSS_FILE
        total = add_dyishappy_loss(loss_path, cost - price, now)
        self.emit(
            f"seller.dyishappy_below task={task_id} sku={item} cost={cost} "
            f"price={price} loss_total={total}"
        )

    def note_cit_need(self, text: str, request: Request) -> None:
        """Record a cheapest-in-town food ask no unit met (owner 13:5x)."""
        if not CIT_FORMS[1].search(text.strip()):
            return
        need = required_shelf_hours(text)
        if need is None:
            return
        path = self.stock.folder.resolve().parent / CIT_NEEDS_FILE
        for sku in request.skus:
            record_cit_need(path, sku, need, time.time())

    async def publish_ask(  # noqa: PLR0913 - one message, explicit flags
        self,
        task_id: str,
        deal: Deal,
        updater: TaskUpdater,
        *,
        opening: bool,
        facts: bool = False,
        answer: str = "",
    ) -> None:
        """Quote in the starter's wording plus machine-readable metadata.

        The ask is written three ways so any counterpart parser reads it: the
        starter's template on the first line, a data part ``{"ask": N}`` beside the
        text, and the ask again as the last number of the message. Everything in
        between is digit-free: ``facts`` adds the certified grade and the warranty
        in words; ``answer`` (a screened, digit-free model sentence) replaces that
        line. Neither discloses cost, margin or policy.
        """
        name = deal.unit.name if deal.unit is not None else str(deal.legacy_item)
        sku = deal.unit.sku if deal.unit is not None else str(deal.legacy_item)
        text = (
            f"{name}: asking {deal.ask}. Send your offer as a number."
            if opening
            else f"Counteroffer: {deal.ask}."
        )
        if answer:
            text += f"\n{answer}"
        elif facts and deal.unit is not None:
            warranty = deal_warranty(deal, self.policy())
            text += (
                f"\nGrade {deal.quote.grade_now} with the market's certificate "
                "attached"
                + (
                    f", a {warranty_words(warranty)} warranty: a full refund if it "
                    "breaks"
                    if warranty
                    else ""
                )
                + "."
            )
        if opening and self.below_market(sku, deal.ask):
            text += f"\n{BELOW_MARKET_LINE}"
        text += f"\nReply with a number, or ok to take the ask: {deal.ask}"
        metadata: dict[str, object] = {
            "ask": deal.ask,
            "sku": sku,
            "item": sku,
            "name": name,
            "grade": deal.quote.grade_now,
            "certificate": deal.unit is not None,
        }
        if deal.unit is not None and deal.unit.kind == "goods":
            metadata["warranty_hours"] = deal_warranty(deal, self.policy())
        self.emit(f"seller.ask task={task_id} ask={deal.ask} rounds={deal.rounds}")
        ask_part = Part(
            data=Parse(json.dumps(ask_data(deal, sku, time.time())), Value()),
            media_type="application/json",
        )
        await updater.requires_input(
            updater.new_agent_message([Part(text=text), ask_part], metadata=metadata)
        )

    async def accept(  # noqa: PLR0913, PLR0917 - the sale needs every fact at once
        self,
        task_id: str,
        deal: Deal,
        price: int,
        now: float,
        policy: SellerPolicy,
        updater: TaskUpdater,
    ) -> None:
        """Sign the exact agreed price and publish the unit, claim and warranty."""
        unit = deal.unit
        item = (
            deal.asked_category or unit.sku
            if unit is not None
            else str(deal.legacy_item)
        )
        receipt: dict[str, object] = {
            "shop": self.name,
            "item": item,
            "price": int(price),
            "task": task_id,
        }
        claim, warranty = "", 0
        if unit is not None:
            receipt["unit"] = unit.unit
            shelf = self.stock.shelf_hours(unit.sku)
            if policy.claim_grade:
                claim = claimed_grade(unit, now, shelf, policy)
                if claim:
                    receipt["claim"] = claim
            if unit.kind == "goods":
                warranty = deal_warranty(deal, policy)
                if warranty > 0:
                    receipt["warranty_hours"] = warranty
        if self.signer is not None:
            signed = self.signer.sign(
                shop=self.name, item=item, price=int(price), task=task_id
            )
            receipt["sig_seller"] = signed.sig_seller
        _ = self.open.pop(task_id, None)
        self.closed.add(task_id)
        if unit is not None:
            self.committed[unit.unit] = now
        await updater.add_artifact(
            [
                Part(
                    data=Parse(json.dumps(receipt), Value()),
                    media_type="application/json",
                )
            ],
            name="receipt",
        )
        name = unit.name if unit is not None else item
        await updater.complete(
            updater.new_agent_message([Part(text=f"Deal: {name} at {price}.")])
        )
        self.record_below_cost(task_id, item, int(price), now)
        self.emit(
            f"seller.receipt task={task_id} sku={item} price={price} "
            f"unit={unit.unit if unit else '-'} claim={claim or '-'} "
            f"warranty={warranty} rounds={deal.rounds}"
        )
        if self.events is not None and unit is not None:
            try:
                self.events.record_receipt(
                    task=task_id,
                    unit=unit.unit,
                    sku=unit.sku,
                    price=int(price),
                    cost=unit.cost,
                    grade=deal.quote.grade_now,
                    claim=claim,
                    warranty_hours=warranty,
                    rounds=deal.rounds,
                )
            except OSError:
                self.emit("seller.events_write_failed")


def create_r3_app(  # noqa: PLR0913 - explicit wiring
    base_url: str,
    name: str,
    *,
    signer: ReceiptSigner | None,
    emit: Callable[[str], None],
    folder: Path | None = None,
    policy_file: Path | None = None,
    events_file: Path | None = None,
) -> FastAPI:
    """Build the A2A server for the stock seller."""
    state = market_dir(folder)
    stock = StockView(state)
    policy_file = policy_file if policy_file is not None else policy_path(state)
    events_file = events_file if events_file is not None else events_path(state)
    try:
        events: EventLedger | None = EventLedger(events_file)
    except OSError:
        events = None
    reader = ModelClient.from_env(events, gate=TRADE_GATE)
    try:
        ledger: LedgerDB | None = LedgerDB(ledger_path(state))
    except (OSError, sqlite3.Error):
        ledger = None
    policy_now = load_policy(policy_file).seller
    card = AgentCard(
        name=name,
        description=(
            "Sells from real market stock with the market's unit certificate "
            "attached to every unit; the grade claimed is the certified grade and "
            + card_warranty_line(policy_now)
            + " Receipts are Ed25519-signed. Ask for an "
            "item by SKU or name; the shop quotes at once from code. The ask is in "
            'message metadata, in a data part {"ask": N} and is the last number of '
            "every reply. Reply with a whole number to offer, or 'ok' to take the "
            "ask; matching the ask closes the deal in one message."
        ),
        version="2.0.0-r3",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        supported_interfaces=[
            AgentInterface(
                url=base_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        skills=[
            AgentSkill(
                id="negotiate",
                name="Negotiate a purchase",
                description=(
                    "Name a SKU or product; the shop quotes from its certified stock. "
                    "Send a number to offer, or 'ok' to take the ask; the receipt "
                    "names the exact unit, its grade claim and warranty."
                ),
                examples=[
                    "I want to buy 종가 알뜰맘 포기김치 10kg (G00001)",
                    "52000",
                    "ok",
                ],
                tags=["shopping", "negotiation", "round-3"],
            )
        ],
    )
    executor = R3Seller(
        name,
        signer=signer,
        stock=stock,
        policy_file=policy_file,
        events=events,
        emit=emit,
        reader=reader,
        ledger=ledger,
    )
    handler = DefaultRequestHandler(executor, InMemoryTaskStore(), card)
    app = FastAPI(title=name)
    app.router.routes.extend(create_agent_card_routes(card))
    app.router.routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))
    return app
