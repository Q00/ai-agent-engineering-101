"""Market scouting: every SCOUT_EVERY seconds the harness looks at the market and sets the shop's strategy.

The code gathers the situation (the board: every shop's share, deals, visits, stars, revenue, profit and card;
this round's prices per item; our own numbers, funnel, reviews and lessons; our previous
decisions and what happened after each) and states the objective (first place in share of this
round's verified deals and returning buyers, net profit, review stars; no fixed order) and the rules it
enforces. The model decides:

  PRICES    the listed price per item; the code clamps each to [floor, the largest whole number below this
            round's market mean for the item] (floor * (1 + MAX_MARKUP) while the round has no mean)
  LEAD      which of the card's true sentences come first (ids from a fixed list; nothing is written freely)
  PLAYBOOK  up to PLAYBOOK_LINES lines of guidance that go into the negotiation prompt (unused while
            MODEL_TURNS is 0); a line that pairs a review with a concession is dropped (the shop never
            trades for reviews), the rest stays
  STYLE     per item, fixed (the listed price is the only price) or concede N (the listed price opens and
            the code lowers the ask in N equal steps to the floor on the later, rule-handled turns)
  RULE      the sentence of the first rule-handled reply in the fixed style; it must contain {price}, may
            contain {item}, and holds no other number (the code fills in the true price)
  TAGLINE   the shop's positioning line on the card (no numbers, urls, other shops' names or review rewards)
  TEST      optional: hold the decision N minutes; the code rolls it back early if our share of the market's
            deals falls more than the given points below the hour before the start, and reports the result

Besides the board it is shown the last hour by buyer (who buys from us and from the leader, who
bought from us earlier and left and their last result with us, who never came, and where our low stars
come from), each shop's last hour (deals, close rate, trade seconds, prices, profit), the owner's notes
and the model's past tests.

The decision is written to .market/strategy.json (read by the seller before every negotiation and
every card request) and appended to logs/strategy.jsonl with the scorecard at that moment (board
numbers plus the seller's own funnel: card views and negotiations started), so the next scouting
round can show the model the effect of each decision. A failed harness call or an unreadable
answer keeps the previous strategy.

    python market_scout.py          # scout once and print the picture and the decision
"""

import collections
import json
import math
import os
import re
import statistics
import string
import time
from pathlib import Path

import harness
import httpx
import market_data
import memory_context
import review_feedback

SCOUT_EVERY = 600  # seconds between scouting rounds: one harness call each
MAX_MARKUP = 0.20  # the listed price may not exceed floor * (1 + MAX_MARKUP)
PLAYBOOK_LINES = 5
LEAD_MAX = 3
HISTORY = 12  # previous decisions shown to the model: an hour-long experiment (6 rounds) plus the hour before it
SHOPS_SHOWN = 10  # top shops by share shown to the model, plus ours
HARNESS_TIMEOUT = 90
MAX_STEPS = 4  # concede style: at most this many counters from the listed price down to the floor
RECENT_WINDOW = 3600  # the buyer picture covers the last hour
BUYERS_SHOWN = 12
RULE_MAX = 220
# buyer messages per deal the negotiation model answers (seller.MODEL_TURNS is this value)
MODEL_TURNS = int(os.environ.get("LSS_MODEL_TURNS", "0"))
NEGOTIATION = {
    False: "No negotiation message waits on a model: the code answers at once, so the playbook is not used now (write none).",
    True: "The negotiation model answers only each buyer's first message and reads your playbook as guidance, not as new prices.",
}
STRATEGY = Path(__file__).parent / ".market" / "strategy.json"
EXPERIMENT = Path(__file__).parent / ".market" / "experiment.json"  # owner's fixed prices and style until a time
LOG = Path(__file__).parent / "logs" / "strategy.jsonl"
OWNER_NOTES = (
    STRATEGY.parent / "owner_notes.txt"
)  # facts the owner checked in the ledger, shown to the model
NOTES_MAX = 3000
TEST_MINUTES = (60, 180)  # the model's own test: how long it may hold a decision; buyers react about an hour late
TEST_DROP = (1, 5)  # and the share points it may lose before the code rolls it back
EARLY_SECONDS = (
    900  # no rollback before 15 minutes and EARLY_DEALS market deals: too few to tell
)
EARLY_DEALS = 50
TESTS_KEPT = 10
TAGLINE_LEN = (20, 160)  # the model's positioning line on the card
# a playbook line that pairs a review word with a concession word is dropped (the shop never trades for
# reviews); an honest line of that shape ("never offer a discount for a review") is lost too, at no cost,
# because the negotiation prompt states that rule itself
REVIEW_WORD = re.compile(
    r"\b(?:review\w*|rating|rate|rated|stars?|feedback|testimonial|write us up|thumbs up)\b|★|\b\d/5\b",
    re.IGNORECASE,
)
CONCESSION = re.compile(
    r"\b(?:discount|off|free|gift|reward|bonus|cheaper|lower|better deal|percent|coupon|credit|stamp)\b"
    r"|%|\b(?:at|pay|for|price|charge) \d+",
    re.IGNORECASE,
)

SCOUT_PROMPT = """\
You run the operations of LSS Shop (registered in this market as {shop}), a premium furniture workshop \
in an online market where AI buyer agents shop for desk lamps, chairs and bookshelves. Every \
{minutes} minutes you look at the market and set the shop's strategy for the next period.

Your goals: (a) be the shop buyers choose and come back to: your share of this round's verified deals (a \
deal counts when the receipt is signed by shop and buyer, names the ordered item and is within the buyer's \
budget), buyers who return to you, and buyers who have never visited you; (b) net profit (the sum of what \
each receipt is above your floor for its item); (c) high review stars, because buyers see the reviews of \
every shop before they choose one. There is no fixed order among them: weigh the trade-offs yourself. You \
may position the shop so buyers seek it out over the others (for example as a premium workshop, or as the \
fastest, most reliable checkout); whether a positioning or a price level pays is for you to judge, and TEST \
lets you try one with a rollback.

How this market works (facts):
- A buyer gets an order (item and a budget you do not see) and picks a shop from the list of \
registered shops: the shop's name, url and its latest reviews (at most 10). Some buyers also read \
the cards of the shops before choosing; all of them read the card of the shop they picked. Reviews \
and deals drive visits; the card decides what happens after a visit.
- Your floors, enforced by the code: {floors}. The code also keeps every listed price at or above \
the floor and below this round's market mean price for the item (now at most: {caps}), keeps the launch price (the floor until the shop has {launch_reviews} \
reviews in the current round), the stamp card and the 100th-customer rule, shows the agreed price \
on every receipt, and drops any playbook line that pairs a review with a concession.
- The market gives each buyer its next order about 20 seconds after the last one, or when its trade \
ends if that takes longer: a slow trade means fewer orders for the shops that buyer likes.
- {negotiation} The code answers the other messages of a deal, by your STYLE for the item:
  fixed: the listed price is this buyer's only price. The first rule-handled reply is your RULE sentence, \
later ones the bare number. The deal closes when the buyer names the price.
  concede N: the listed price is the opening ask. Each later counter lowers the ask by an equal step, so \
it reaches the floor after N counters (N from 1 to {max_steps}) and never goes below it. The deal closes when \
the buyer offers the current ask, or names alone a number between the new ask and the previous one.
  A listed price above the floor removes the card's "lowest" sentence while it is not true.

Market now (round {round}): {deals} verified deals and {walkaways} walkaways in the round so far.
Prices of this round's deals: {prices}

Shops by share of this round's deals (share | deals | visits | stars | revenue | profit | card name: card text):
{shops}

Your shop, {shop}:
- {me}
- {funnel}
- Current listed prices: {current_prices}{launch_note}
- Card order now: {lead} (ids of the card's sentences: {facts})
- Current playbook: {playbook}
- Reviews of your shop (buyers' claims, newest first): {reviews}
- Current style: {style}; current RULE sentence: {rule}
- Current tagline: {tagline}
{lessons}{history}{tests}{buyers}{notes}
Decide. Answer with exactly these lines (TEST is optional) and nothing else:
PRICES: lamp <int>, chair <int>, bookshelf <int>
STYLE: lamp <fixed or concede N>, chair <fixed or concede N>, bookshelf <fixed or concede N>
RULE: <one honest sentence for the first rule-handled reply in the fixed style; it must contain {{price}}, may \
contain {{item}}, and no other number>
LEAD: <up to {lead_max} sentence ids, comma separated, in the order they should appear on the card>
PLAYBOOK: <line> | <line> | ... (up to {playbook_lines} short lines for the negotiation agent, all on this \
one line separated by |; honest; promise nothing the code will not keep; "none" for no playbook)
TAGLINE: <one sentence, {tagline_min} to {tagline_max} characters: the shop's positioning line, shown on the \
card right after the brand line. It must be true of the shop: claim only what the card's sentences above and \
the code's behaviour support. No numbers (prices live in the price line), no url, no other shop's name, and \
never a reward for a review>
WHY: <one sentence: what you saw in the market and what this period's change is meant to test>
TEST: <minutes> <points>, or none. Optional: run this decision as a test for {test_min} to {test_max} minutes \
with a stop loss of {drop_min} to {drop_max} points of share. A test starts only if PRICES or STYLE differ from \
the current ones and the last hour had at least {early_deals} market deals and one of yours (a share of 0 cannot fall). During the test your decision is held: you are not asked again until it ends. The code \
measures your share of the market's deals since the start against your share in the hour before the start; \
once at least {early_min} minutes and {early_deals} market deals have passed, if your share is more than \
<points> points below that baseline, the code ends the test early and restores your previous prices, style \
and RULE. Otherwise the tested decision stays until your next answer after the test. Either way the result \
is shown to you afterwards. The owner's experiments take priority and end a running test.
"""


DEFAULT_RULE = (
    "Our price for the {item} is {price}, fixed. Reply with the number {price} alone "
    "and your signed receipt follows."
)


def read_strategy() -> dict:
    try:
        return json.loads(STRATEGY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "updated": 0,
            "round": 0,
            "prices": {},
            "lead": [],
            "playbook": [],
            "why": "",
            "below_mean": False,
            "lowest": False,
            "style": {},
            "rule": "",
            "history": [],
        }


def market_picture() -> dict:
    """The board (every shop), this round's prices and our own reviews, in one dict."""
    with httpx.Client(timeout=15) as http:
        board = http.get(f"{market_data.MARKET}/api/board").raise_for_status().json()
        stats = http.get(f"{market_data.MARKET}/api/stats").raise_for_status().json()
    rnd = board.get("round") or stats.get("round") or 0
    this_round = (stats.get("rounds") or {}).get(str(rnd)) or {}
    shops = [s for s in board.get("shops") or [] if isinstance(s, dict)]
    me = next((s for s in shops if s.get("name") == market_data.SHOP), {})
    c = market_data.market_prices()
    return {
        "round": rnd,
        "deals": this_round.get("deals", 0),
        "walkaways": this_round.get("walkaways", 0),
        "items": this_round.get("items") or {},
        "shops": shops,
        "me": me,
        "reviews": c.get("reviews", {}).get(market_data.SHOP) or [],
    }


def hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def buyer_lines(
    c: dict, prices: dict, now: float | None = None, reserve: dict | None = None
) -> str:
    """The last hour of this round by buyer, from the ledger cache (market_data's recent results).
    Rows cached before the price, review and duration columns are shorter: every read past r[6] checks len(r)."""
    me = market_data.SHOP
    recent = c.get("recent") or []
    if not recent:
        return "- Buyers: no results of this round in the ledger yet.\n"
    now = now or recent[-1][0]
    hour = [r for r in recent if r[0] >= now - RECENT_WINDOW]
    deals = collections.Counter(r[2] for r in hour if r[3])
    total = sum(deals.values()) or 1
    leader = next((n for n, _ in deals.most_common() if n != me), "-")
    lines = [
        f"- Last 60 min of this round: {sum(deals.values())} deals in the market; yours {deals[me]} "
        f"({100 * deals[me] / total:.1f}%), the leader among the others ({leader}) {deals[leader]} "
        f"({100 * deals[leader] / total:.1f}%). Your place in this hour: #{1 + sum(n > deals[me] for n in deals.values())} "
        f"of {len(deals | {me: 0})} shops. The board's share counts the whole round, so it moves slowly and still "
        f"reflects earlier hours; the last hour shows where it is heading."
    ]
    per = collections.defaultdict(collections.Counter)
    for r in hour:
        where = "you" if r[2] == me else ("leader" if r[2] == leader else "other")
        per[r[1]][where + ("" if r[3] else "_walk")] += 1
    rnd = c.get("buyers") or {}
    active = sorted(
        (b for b in per if b != me), key=lambda b: -sum(per[b].values())
    )
    lines.append(
        "- Active buyers, busiest first (last 60 min: deals with you / walkaways at you / deals with the "
        "leader / deals elsewhere; this round with you: deals, walkaways, last visit):"
    )
    for b in active[:BUYERS_SHOWN]:
        p, mine = per[b], (rnd.get(b) or {}).get(me)
        hist = f"{mine[0]} deals, {mine[1]} walkaways, last {hhmm(mine[2])}" if mine else "never visited you"
        lines.append(f"  - {b}: {p['you']} / {p['you_walk']} / {p['leader']} / {p['other']}; {hist}")

    def main_shop(b):
        top = collections.Counter(r[2] for r in hour if r[1] == b and r[3]).most_common(1)
        return top[0][0] if top else "-"

    lost = [
        b for b in active
        if ((rnd.get(b) or {}).get(me) or [0, 0, 0])[0] >= 5
        and (rnd[b][me][2] < now - RECENT_WINDOW)
    ]
    never = [b for b in active if me not in (rnd.get(b) or {})]

    def last_with_us(b):
        r = next((r for r in reversed(recent) if r[1] == b and r[2] == me), None) or (
            c.get("ours_last") or {}
        ).get(b)
        if r is None:
            return "their last result with you is older than the cache"
        floor = (reserve or {}).get(r[6])
        if r[3]:
            what = f"deal at {r[7] if len(r) > 7 else None} (floor {floor})"
        else:
            what = f"walkaway, budget {r[5]} (floor {floor})"
        text = str(r[8])[:120] if len(r) > 8 and r[8] else ""
        claim = f'; their claim: "{text}"' if text else "; no review text"
        return f"last result with you {hhmm(r[0])}: {what}, {r[4]} stars{claim}"

    lines.append(
        "- Bought from you earlier this round, none in the last hour, still buying elsewhere:"
        + (
            "".join(
                f"\n  - {b} (now mostly {main_shop(b)}, last with you {hhmm(rnd[b][me][2])}); {last_with_us(b)}"
                for b in lost
            )
            or " none"
        )
    )
    lines.append(
        "- Active this hour but never sent you an order this round: "
        + (", ".join(f"{b} (mostly {main_shop(b)})" for b in never) or "none")
    )
    low = [r for r in hour if r[2] == me and isinstance(r[4], (int, float)) and r[4] <= 3]
    poor = [
        r for r in low
        if not r[3] and isinstance(r[5], (int, float)) and r[6] in prices and r[5] < prices[r[6]]
    ]
    walks = [r for r in low if not r[3]]
    if reserve is None:  # no floors given: the split by listed price only
        lines.append(
            f"- Your reviews of 3 stars or less in the last hour: {len(low)}; {len(poor)} from walkaways whose "
            f"budget was below your listed price for the item, {len(walks) - len(poor)} from other walkaways, "
            f"{len(low) - len(walks)} on deals."
        )
        return "\n".join(lines) + "\n"
    # a budget below the floor loses at every price we may list, so it is not a reason to lower the price
    unwinnable = [
        r
        for r in walks
        if isinstance(r[5], (int, float)) and r[6] in reserve and r[5] < reserve[r[6]]
    ]
    winnable = [r for r in poor if r not in unwinnable]
    lines.append(
        f"- Your reviews of 3 stars or less in the last hour: {len(low)}; {len(unwinnable)} from walkaways whose "
        f"budget was below your floor for the item (no price could win these), {len(winnable)} from walkaways "
        f"whose budget was at or above the floor but below your listed price for the item (a lower price could "
        f"have won these), {len(walks) - len(unwinnable) - len(winnable)} from other walkaways, "
        f"{len(low) - len(walks)} on deals."
    )
    return "\n".join(lines) + "\n"


def number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def pace_lines(c: dict, reserve: dict, now: float | None = None) -> str:
    """The last hour by shop (deals, close rate, trade seconds, prices, profit) and the market's pace, from the ledger cache."""
    me = market_data.SHOP
    recent = c.get("recent") or []
    if not recent:
        return ""
    now = now or recent[-1][0]
    hour = [r for r in recent if r[0] >= now - RECENT_WINDOW]
    before = [
        r for r in recent if now - 2 * RECENT_WINDOW <= r[0] < now - RECENT_WINDOW
    ]
    deals = collections.Counter(r[2] for r in hour if r[3])
    shown = [n for n, _ in deals.most_common(SHOPS_SHOWN)]
    if me not in shown:
        shown.append(me)
    lines = [
        f"- Market pace: {sum(deals.values())} deals in the last 60 min, {sum(r[3] for r in before)} in the hour "
        f"before; {len({r[1] for r in hour})} active buyers in the last 60 min.",
        "- Shops in the last 60 min, most deals first (deals | close rate: deals / results | median seconds from "
        "order to result | average receipt price per item | average amount over the floor per deal (deals whose "
        "order is in the cache) | profit over those deals):",
    ]
    for name in shown:
        rows = [r for r in hour if r[2] == name]
        secs = sorted(
            r[9] for r in rows if len(r) > 9 and number(r[9])
        )  # rows cached before the column have none
        paid = collections.defaultdict(list)
        for r in rows:
            if r[3] and r[6] in reserve and len(r) > 7 and number(r[7]):
                paid[r[6]].append(r[7])
        over = [p - reserve[k] for k, v in paid.items() for p in v]
        close = (
            f"{100 * deals[name] / len(rows):.0f}% ({deals[name]}/{len(rows)})"
            if rows
            else "- (0/0)"
        )
        lines.append(
            f"  - {name}{' (you)' if name == me else ''}: {deals[name]} | {close} | "
            + (f"{statistics.median(secs):.1f} s" if secs else "-")
            + " | "
            + (
                ", ".join(f"{k} {sum(v) / len(v):.1f}" for k, v in sorted(paid.items()))
                or "-"
            )
            + " | "
            + (f"{sum(over) / len(over):+.1f} ({len(over)})" if over else "-")
            + f" | {sum(over)}"
        )
    return "\n".join(lines) + "\n"


def window(recent: list, start: float, end: float, reserve: dict) -> dict:
    """Market deals, ours, our share (points) and our revenue and profit (receipt price minus floor) in [start, end)."""
    rows = [r for r in recent if start <= r[0] < end and r[3]]
    ours = [r for r in rows if r[2] == market_data.SHOP]
    paid = [r for r in ours if r[6] in reserve and len(r) > 7 and number(r[7])]
    return {
        "market": len(rows),
        "ours": len(ours),
        "share": round(100 * len(ours) / len(rows), 2) if rows else 0.0,
        "revenue": sum(r[7] for r in paid),
        "profit": sum(r[7] - reserve[r[6]] for r in paid),
    }


def owner_notes() -> str:
    try:
        text = OWNER_NOTES.read_text(encoding="utf-8").strip()[:NOTES_MAX]
    except OSError:
        return ""
    return (
        f"Owner's notes (facts the owner checked in the ledger):\n{text}\n"
        if text
        else ""
    )


ENDED = {
    "early": "early: share fell past the stop loss, previous prices, style and RULE restored",
    "time": "at its end: the tested decision was kept",
    "owner": "by the owner's experiment",
}


def tests_lines(tests: list[dict]) -> str:
    """The model's finished tests, newest first, with what the code measured."""
    if not tests:
        return ""
    lines = ["- Your past tests (newest first):"]
    for t in reversed(tests):
        lines.append(
            f"  - {hhmm(t.get('start', 0))}-{hhmm(t.get('end', 0))}, ended {ENDED.get(t.get('ended'), t.get('ended'))}: "
            f"prices {t.get('prices')}, style {style_text(t.get('style') or {})}; your share of market deals "
            f"{t.get('share_before')}% in the hour before -> {t.get('share_during')}% during; your deals/h "
            f"{t.get('deals_per_h_before')} -> {t.get('deals_per_h_during')}; during: revenue/h "
            f"{t.get('revenue_per_h_during')}, profit/h {t.get('profit_per_h_during')}; why: {str(t.get('why', ''))[:140]}"
        )
    return "\n".join(lines) + "\n"


def test_result(
    test: dict, ended: str, recent: list, now: float, reserve: dict
) -> dict:
    during = window(recent, test["start"], float("inf"), reserve)
    hours = max(now - test["start"], 60) / 3600
    return {
        "start": test["start"],
        "end": now,
        "ended": ended,
        "share_before": test["baseline"],
        "share_during": during["share"],
        "deals_per_h_before": test["before_deals"],
        "deals_per_h_during": round(during["ours"] / hours, 1),
        "revenue_per_h_during": round(during["revenue"] / hours, 1),
        "profit_per_h_during": round(during["profit"] / hours, 1),
        "prices": test.get("prices"),
        "style": test.get("style"),
        "why": test.get("why"),
    }


def parse_test(answer: str) -> tuple[dict | None, str | None]:
    """The TEST line: ({"minutes", "max_drop"} or None, a problem or None). Out of bounds or unreadable: no test."""
    body = None
    for line in answer.splitlines():
        head, sep, rest = line.partition(":")
        if sep and head.strip().upper() == "TEST":
            body = rest.strip()
    if not body or body.strip(" .").lower() == "none":
        return None, None
    m = re.fullmatch(
        r"(\d+)\s*(?:min(?:ute)?s?)?(?:\s*[,;]\s*|\s+)(\d+)\s*(?:points?|pts?)?\.?",
        body,
        re.I,
    )
    if not m:
        return None, f"TEST unreadable, no test: {body!r}"
    minutes, drop = int(m.group(1)), int(m.group(2))
    if not (
        TEST_MINUTES[0] <= minutes <= TEST_MINUTES[1]
        and TEST_DROP[0] <= drop <= TEST_DROP[1]
    ):
        return (
            None,
            f"TEST out of bounds (minutes {TEST_MINUTES}, points {TEST_DROP}), no test: {body!r}",
        )
    return {"minutes": minutes, "max_drop": drop}, None


def valid_style(style: dict, reserve: dict) -> dict:
    """Only known items with fixed or concede 1..MAX_STEPS."""
    return {
        k: v
        for k, v in style.items()
        if k in reserve
        and v
        in (
            {"mode": "fixed"},
            *({"mode": "concede", "steps": n} for n in range(1, MAX_STEPS + 1)),
        )
    }


def style_text(style: dict) -> str:
    return ", ".join(
        f"{k} {v.get('mode', 'fixed')}" + (f" {v.get('steps')}" if v.get("mode") == "concede" else "")
        for k, v in style.items()
    ) or "fixed for every item"


def scorecard(picture: dict, funnel: dict | None = None) -> dict:
    """Board numbers for our shop plus the seller's own counters (card views, negotiations started)."""
    me = picture.get("me") or {}
    score = {k: me.get(k, 0) for k in ("deals", "visits", "stars", "share", "revenue", "profit")}
    score.update({k: int(v) for k, v in (funnel or {}).items()})
    return score


def shop_line(s: dict) -> str:
    card = s.get("card") or {}
    text = re.sub(r"\s+", " ", str(card.get("description") or ""))[:120]
    return (
        f"- {s.get('name')}: {100 * (s.get('share') or 0):.1f}% | {s.get('deals', 0)} | {s.get('visits', 0)} | "
        f"{s.get('stars') or 0} | {s.get('revenue', 0)} | {s.get('profit', 0)} | {card.get('name') or '-'}: {text}"
    )


def deltas(then: dict, later: dict) -> str:
    parts = []
    for k, label in (
        ("deals", "deals"),
        ("revenue", "revenue"),
        ("profit", "profit"),
        ("visits", "visits"),
        ("card_views", "card views"),
        ("customers", "negotiations"),
    ):
        if k in then and k in later:  # a counter missing from an older scorecard is not a zero
            parts.append(
                f"{round((later.get(k) or 0) - (then.get(k) or 0), 3):+} {label}"
            )
    return ", ".join(parts)


def history_lines(history: list[dict], now: dict) -> str:
    """Previous decisions, newest first, each with what changed in the scorecard until the next one."""
    if not history:
        return ""
    lines = ["- Your previous decisions and what followed each (newest first):"]
    later = now
    for h in reversed(history[-HISTORY:]):
        then = h.get("score") or {}
        minutes = int(
            (
                (later.get("time") if later is not now else time.time())
                - h.get("time", 0)
            )
            / 60
        )
        lines.append(
            f"  - {time.strftime('%H:%M', time.localtime(h.get('time', 0)))}: prices {h.get('prices')}, "
            f"style {style_text(h.get('style') or {})}, lead "
            f"{h.get('lead')}, playbook {len(h.get('playbook') or [])} line(s); why: {str(h.get('why', ''))[:140]} "
            f"-> in the next {minutes} min: {deltas(then, later)}, stars {then.get('stars', 0)} -> {later.get('stars', 0)}"
        )
        later = dict(then, time=h.get("time", 0))
    return "\n".join(lines) + "\n"


def build_prompt(
    picture: dict,
    strategy: dict,
    reserve: dict,
    facts: dict,
    launch: bool,
    funnel: dict | None = None,
    buyers: str = "",
) -> str:
    items = picture["items"]
    prices = (
        "; ".join(
            f"{item}: {v.get('n', 0)} deals, min {v.get('min')}, mean {v.get('mean')}, max {v.get('max')}"
            for item, v in sorted(items.items())
        )
        or "no deals yet"
    )
    ranked = sorted(picture["shops"], key=lambda s: -(s.get("share") or 0))
    shown = ranked[:SHOPS_SHOWN]
    if picture["me"] and picture["me"] not in shown:
        shown.append(picture["me"])
    me = picture["me"]
    me_line = (
        f"share {100 * (me.get('share') or 0):.1f}%, {me.get('deals', 0)} deals, {me.get('visits', 0)} visits, "
        f"stars {me.get('stars') or 0}, {me.get('reviews', 0)} reviews, revenue {me.get('revenue', 0)}, "
        f"profit {me.get('profit', 0)}, alive {me.get('alive')}; your place on the board: "
        + ", ".join(f"{k} #{1 + sum((s.get(k) or 0) > (me.get(k) or 0) for s in picture['shops'])}"
                    for k in ("share", "revenue", "profit", "stars"))
        if me
        else "not on the board"
    )
    funnel = funnel or {}
    funnel_line = (
        f"Since this seller process started: {funnel.get('card_views', 0)} card views, "
        f"{funnel.get('customers', 0)} negotiations started (the funnel the card has to convert)"
    )
    reviews = (
        "; ".join(
            f"{r.get('stars')} stars: {str(r.get('review', ''))[:150]}"
            for r in picture["reviews"][:5]
        )
        or "none yet"
    )
    learned = review_feedback.prompt_lines(review_feedback.read_operations())
    lessons = (
        ("- Selected experience, not verified causal lessons: " + " / ".join(learned) + "\n")
        if learned
        else ""
    )
    current = strategy.get("prices") or review_feedback.listed_prices(reserve, 0)
    return SCOUT_PROMPT.format(
        shop=market_data.SHOP,
        minutes=SCOUT_EVERY // 60,
        negotiation=NEGOTIATION[MODEL_TURNS > 0],
        floors=", ".join(f"{k} {v}" for k, v in reserve.items()),
        caps=", ".join(f"{k} {v}" for k, v in price_caps(reserve, items).items()),
        launch_reviews=strategy.get("launch_reviews", 5),
        round=picture["round"],
        deals=picture["deals"],
        walkaways=picture["walkaways"],
        prices=prices,
        shops="\n".join(shop_line(s) for s in shown) or "- none",
        me=me_line,
        funnel=funnel_line,
        current_prices=", ".join(f"{k} {current.get(k)}" for k in reserve),
        launch_note=(
            " (the launch price is on: buyers are quoted the floors until the shop has "
            f"{strategy.get('launch_reviews', 5)} reviews this round)"
            if launch
            else ""
        ),
        lead=", ".join(strategy.get("lead") or []) or "default order",
        facts="; ".join(f'{k} = "{v}"' for k, v in facts.items()),
        playbook=" | ".join(strategy.get("playbook") or []) or "none",
        reviews=reviews,
        lessons=lessons,
        history=history_lines(
            (strategy.get("history") or [])[-3:],
            dict(scorecard(picture, funnel), time=time.time()),
        ),
        lead_max=LEAD_MAX,
        playbook_lines=PLAYBOOK_LINES,
        max_steps=MAX_STEPS,
        style=style_text(strategy.get("style") or {}),
        rule=strategy.get("rule") or DEFAULT_RULE,
        buyers=buyers,
        tagline=strategy.get("tagline") or "none",
        tagline_min=TAGLINE_LEN[0],
        tagline_max=TAGLINE_LEN[1],
        tests=tests_lines(strategy.get("tests") or []),
        notes=owner_notes(),
        test_min=TEST_MINUTES[0],
        test_max=TEST_MINUTES[1],
        drop_min=TEST_DROP[0],
        drop_max=TEST_DROP[1],
        early_min=EARLY_SECONDS // 60,
        early_deals=EARLY_DEALS,
    )


def price_caps(reserve: dict, items: dict) -> dict:
    """Per item the highest listed price: the largest whole number below this round's market mean (buyers
    that saw a price above the mean stayed away for hours, 2026-10-23), never below the floor; without a
    mean, floor * (1 + MAX_MARKUP)."""
    caps = {}
    for item, floor in reserve.items():
        mean = (items.get(item) or {}).get("mean")
        cap = math.ceil(mean) - 1 if number(mean) and math.isfinite(mean) else round(floor * (1 + MAX_MARKUP))
        caps[item] = max(floor, min(round(floor * (1 + MAX_MARKUP)), cap))
    return caps


def clamp_prices(wanted: dict, previous: dict, reserve: dict, caps: dict | None = None) -> dict:
    out = {}
    for item, floor in reserve.items():
        v = wanted.get(item, previous.get(item, floor))
        v = v if number(v) else floor  # a damaged strategy file holds no price
        top = (caps or {}).get(item, round(floor * (1 + MAX_MARKUP)))
        out[item] = int(max(floor, min(top, v)))
    return out


def rule_ok(rule: str) -> str | None:
    """None when the RULE sentence is usable, else why not."""
    try:
        parsed = [(f, spec, conv) for _, f, spec, conv in string.Formatter().parse(rule) if f is not None]
        fields = {f for f, _, _ in parsed}
        rule.format(item="lamp", price=100)
    except (ValueError, KeyError, IndexError) as e:
        return f"unreadable placeholders ({e})"
    if any(spec or conv for _, spec, conv in parsed):  # {price:x} would render another number
        return "formatted placeholder (no format spec or conversion allowed)"
    if "price" not in fields or fields - {"item", "price"}:
        return f"placeholders {sorted(fields)}: needs {{price}}, only {{item}} besides"
    if re.search(r"\d", rule):
        return "holds a number (the code fills in the price)"
    if len(rule) > RULE_MAX:
        return f"longer than {RULE_MAX} characters"
    if review_reward(rule):
        return "pairs a review with a concession"
    return None


def tagline_bad(tagline: str, others: tuple = ()) -> str | None:
    """None when the TAGLINE is usable on the card, else why not."""
    if not TAGLINE_LEN[0] <= len(tagline) <= TAGLINE_LEN[1]:
        return f"length {len(tagline)} outside {TAGLINE_LEN}"
    if any(
        ch.isnumeric() for ch in tagline
    ):  # any script's digits, circled or roman numerals too
        return "holds a number (prices live in the price line)"
    if not any(ch.isalpha() for ch in tagline):
        return "holds no words"
    if re.search(r"https?:|www\.|\.(?:com|ai|net|org|io)\b", tagline, re.I):
        return "holds a url"
    if "{" in tagline or "}" in tagline:
        return "holds a brace"
    if review_reward(tagline):
        return "pairs a review with a concession"
    named = [
        n
        for n in others
        if len(n) >= 3 and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", tagline, re.I)
    ]
    if named:
        return f"names another shop ({named[0]})"
    return None


def review_reward(line: str) -> bool:
    """True for a playbook line that pairs a review with a concession, whatever the order or wording."""
    return bool(REVIEW_WORD.search(line) and CONCESSION.search(line))


def parse(
    answer: str, previous: dict, reserve: dict, facts: dict, others: tuple = (), caps: dict | None = None
) -> tuple[dict, list[str]]:
    """Reads the answer lines; whatever is missing or not allowed keeps the previous value.
    others: the other shops' names, which the tagline may not use."""
    fields = {}
    for line in answer.splitlines():
        head, sep, body = line.partition(":")
        if sep and head.strip().upper() in (
            "PRICES",
            "STYLE",
            "RULE",
            "LEAD",
            "PLAYBOOK",
            "WHY",
            "TAGLINE",
        ):
            fields[head.strip().upper()] = body.strip()
    problems = []
    wanted = {
        m.group(1).lower(): int(m.group(2))
        for m in re.finditer(
            r"(lamp|chair|bookshelf)\D{0,4}(\d+)", fields.get("PRICES", ""), re.I
        )
    }
    if set(wanted) != set(reserve):
        problems.append(f"PRICES incomplete: {fields.get('PRICES')!r}")
    prices = clamp_prices(wanted, previous.get("prices") or {}, reserve, caps)
    if any(prices[k] != wanted.get(k, prices[k]) for k in prices):
        problems.append(f"prices clamped: wanted {wanted}, listed {prices}")
    lead = []
    for part in re.split(r"[,\s]+", fields.get("LEAD", "")):
        key = part.strip().lower()
        if key in facts and key not in lead:
            lead.append(key)
    lead = lead[:LEAD_MAX] or list(previous.get("lead") or [])
    raw = fields.get("PLAYBOOK", "")
    if not raw:  # no playbook line, or its body on the next lines: no change
        playbook = list(previous.get("playbook") or [])
    elif raw.strip().lower() == "none":
        playbook = []
    else:
        playbook = []
        for p in re.split(r"\s*\|\s*", raw):
            p = p.strip()[:200]
            if not p:
                continue
            if review_reward(p):
                problems.append(
                    f"playbook line dropped (pairs a review with a concession): {p!r}"
                )
            else:
                playbook.append(p)
        playbook = playbook[:PLAYBOOK_LINES]
    why = fields.get("WHY", "").strip()[:300] or str(previous.get("why") or "")
    style = {k: dict(v) for k, v in (previous.get("style") or {}).items() if k in reserve}
    found = {
        m.group(1).lower(): (m.group(2).lower(), m.group(3))
        for m in re.finditer(
            r"(lamp|chair|bookshelf)\W{0,3}(fixed|concede)(?:\W{0,3}(\d+))?",
            fields.get("STYLE", ""),
            re.I,
        )
    }
    if fields.get("STYLE") and set(found) != set(reserve):
        problems.append(f"STYLE incomplete: {fields.get('STYLE')!r}")
    for item, (mode, steps) in found.items():
        n = int(steps) if steps else 2
        if mode == "concede" and not 1 <= n <= MAX_STEPS:
            problems.append(f"STYLE {item}: concede {n} clamped to [1, {MAX_STEPS}]")
        style[item] = (
            {"mode": "concede", "steps": max(1, min(MAX_STEPS, n))} if mode == "concede" else {"mode": "fixed"}
        )
    rule = fields.get("RULE", "").strip().strip('"')
    bad = rule_ok(rule) if rule else None
    if bad:
        problems.append(f"RULE kept the previous sentence: {bad}: {rule!r}")
    rule = rule if rule and not bad else str(previous.get("rule") or "")
    tagline = fields.get("TAGLINE", "").strip().strip("\"'").strip()
    bad = tagline_bad(tagline, others) if tagline else None
    if bad:
        problems.append(f"TAGLINE kept the previous line: {bad}: {tagline!r}")
    tagline = tagline if tagline and not bad else str(previous.get("tagline") or "")
    return {
        "prices": prices,
        "lead": lead,
        "playbook": playbook,
        "why": why,
        "style": style,
        "rule": rule,
        "tagline": tagline,
    }, problems


def read_experiment(now: float | None = None) -> dict | None:
    """The owner's experiment while it runs: {"until": epoch seconds, "prices": {...}, "style": {...}, "why": "..."}."""
    try:
        e = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(e, dict) or not isinstance(e.get("until"), (int, float)):
        return None
    return e if (now or time.time()) < e["until"] else None


def refresh(
    reserve: dict, facts: dict, launch_reviews: int, funnel: dict | None = None
) -> dict:
    """One scouting round: picture -> harness -> guarded decision -> strategy file and log. Returns the strategy.
    While the owner's experiment runs, its prices and style replace the harness's decision (no harness call);
    the rounds are still recorded, so the model later sees the experiment and what followed it."""
    previous = read_strategy()
    previous["launch_reviews"] = launch_reviews
    picture = market_picture()
    caps = price_caps(reserve, picture["items"])
    ops = review_feedback.read_operations()
    memory_context.compact_seller(ops, previous)
    launch = ops.get("round_reviews", 0) < launch_reviews
    cache = {}
    try:
        cache = market_data.market_prices()
        buyers = buyer_lines(
            cache, previous.get("prices") or reserve, reserve=reserve
        ) + pace_lines(cache, reserve)
    except (
        Exception
    ) as e:  # the buyer picture is extra information; the scouting goes on without it
        print(f"scout: no buyer picture: {e}", flush=True)
        buyers = ""
    recent = cache.get("recent") or []
    now = time.time()
    tests = list(previous.get("tests") or [])
    test = previous.get("test") if isinstance(previous.get("test"), dict) else None
    experiment = read_experiment()
    problems = []
    if test and not (
        all(
            number(test.get(k))
            for k in ("start", "until", "max_drop", "baseline", "before_deals")
        )
        and isinstance(test.get("before"), dict)
    ):  # a hand-edited or damaged test: dropped, the model is asked again
        problems.append(f"test dropped, unreadable: {str(test)[:200]}")
        test = None
    if (
        test and experiment
    ):  # the owner's experiment comes first: the model's test ends where it stands
        tests.append(test_result(test, "owner", recent, now, reserve))
        print("scout: the owner's experiment ended the model's test", flush=True)
        test = None
    if experiment:
        decision = {
            "lead": list(previous.get("lead") or []),
            "playbook": list(previous.get("playbook") or []),
            "rule": str(previous.get("rule") or ""),
            "tagline": str(previous.get("tagline") or ""),
            "prices": clamp_prices(
                experiment.get("prices") or {}, previous.get("prices") or {}, reserve, caps
            ),
            "style": valid_style(experiment.get("style") or {}, reserve),
            "why": f"owner's experiment until {hhmm(experiment['until'])}: {str(experiment.get('why', ''))[:200]}",
        }
        print(
            f"scout: owner's experiment until {hhmm(experiment['until'])}, no harness call",
            flush=True,
        )
    elif (
        test
    ):  # the model's test holds its decision; the code alone decides on a rollback
        during = window(recent, test["start"], float("inf"), reserve)
        early = (
            now - test["start"] >= EARLY_SECONDS
            and during["market"] >= EARLY_DEALS
            and during["share"] < test["baseline"] - test["max_drop"]
        )
        held = test["before"] if early else previous
        decision = {
            "lead": list(previous.get("lead") or []),
            "playbook": list(previous.get("playbook") or []),
            "rule": str(held.get("rule") or ""),
            "tagline": str(previous.get("tagline") or ""),
            "prices": clamp_prices(held.get("prices") or {}, {}, reserve, caps),
            "style": valid_style(held.get("style") or {}, reserve),
            "why": f"model's test until {hhmm(test['until'])} (stop loss {test['max_drop']} points, share "
            f"{test['baseline']}% before, {during['share']}% since the start): {str(test.get('why', ''))[:200]}",
        }
        if early or now >= test["until"]:
            tests.append(
                test_result(test, "early" if early else "time", recent, now, reserve)
            )
            decision["why"] = (
                f"model's test ended {ENDED[tests[-1]['ended']]}; " + decision["why"]
            )
            test = None
        print(f"scout: {decision['why'][:160]}, no harness call", flush=True)
    else:
        prompt = build_prompt(picture, previous, reserve, facts, launch, funnel, buyers)
        try:
            answer = harness.ask(prompt, timeout=HARNESS_TIMEOUT)
        except harness.HarnessError as e:
            print(f"scout: harness failed, strategy kept: {e}", flush=True)
            capped = clamp_prices(previous.get("prices") or {}, {}, reserve, caps)
            if previous.get("prices") and capped != previous["prices"]:  # the mean fell below a kept price
                previous["prices"] = capped
                tmp = STRATEGY.with_suffix(".tmp")
                tmp.write_text(json.dumps(previous, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(STRATEGY)
                print(f"scout: kept prices capped below the market mean: {capped}", flush=True)
            return previous
        others = tuple(
            str(x.get("name"))
            for x in picture["shops"]
            if x.get("name") and x.get("name") != market_data.SHOP
        )
        decision, found = parse(answer, previous, reserve, facts, others, caps)
        problems += found
        request, bad = parse_test(answer)
        if bad:
            problems.append(bad)
        base = window(recent, now - RECENT_WINDOW, now + 1, reserve)
        if request and (base["ours"] == 0 or base["market"] < EARLY_DEALS):
            # a share of 0 cannot fall, so the stop loss would never act
            problems.append(
                f"TEST ignored: no baseline (last hour: our deals {base['ours']}, market deals {base['market']})"
            )
            request = None
        if request:
            current = previous.get("prices") or review_feedback.listed_prices(
                reserve, 0
            )
            fixed = {"mode": "fixed"}  # an item missing from a style is fixed
            same_style = all(
                decision["style"].get(k, fixed)
                == (previous.get("style") or {}).get(k, fixed)
                for k in reserve
            )
            if decision["prices"] == current and same_style:
                problems.append(
                    "TEST ignored: the decision does not change prices or style"
                )
            elif launch:
                problems.append(
                    "TEST ignored: the launch price is on, buyers are quoted the floors"
                )
            else:
                test = {
                    "start": now,
                    "until": now + 60 * request["minutes"],
                    "max_drop": request["max_drop"],
                    "baseline": base[
                        "share"
                    ],  # our share of market deals in the hour before the start
                    "before_deals": base["ours"],
                    "before_market": base["market"],
                    "before": {
                        "prices": dict(current),
                        "style": dict(previous.get("style") or {}),
                        "rule": str(previous.get("rule") or ""),
                    },
                    "prices": decision["prices"],
                    "style": decision["style"],
                    "why": decision["why"],
                }
                print(
                    f"scout: model's test for {request['minutes']} min, stop loss {request['max_drop']} points, "
                    f"baseline share {base['share']}%",
                    flush=True,
                )
    for p in problems:
        print(f"scout: {p}", flush=True)
    effective = dict(reserve) if launch else decision["prices"]
    items = picture["items"]

    def true_for_all(
        key, ok
    ) -> bool:  # a card claim is shown only while this round's statistics make it true
        return bool(items) and all(
            isinstance((items.get(k) or {}).get(key), (int, float))
            and ok(effective[k], items[k][key])
            for k in reserve
        )

    record = dict(
        decision,
        time=now,
        round=picture["round"],
        score=scorecard(picture, funnel),
        problems=problems,
    )
    strategy = {
        **decision,
        "updated": now,
        "round": picture["round"],
        "launch_reviews": launch_reviews,
        "below_mean": true_for_all("mean", lambda ours, mean: ours <= mean),
        "lowest": true_for_all("min", lambda ours, low: ours <= low),
        "history": ((previous.get("history") or []) + [record])[-50:],
        "tests": tests[-TESTS_KEPT:],
        "test": test,
    }
    STRATEGY.parent.mkdir(exist_ok=True)
    tmp = STRATEGY.with_suffix(
        ".tmp"
    )  # written whole, then renamed: the seller never reads a half-written file
    tmp.write_text(json.dumps(strategy, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STRATEGY)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return strategy


def report(strategy: dict) -> str:
    h = strategy.get("history") or []
    score = (h[-1].get("score") if h else None) or {}
    return "\n".join(
        [
            f"round {strategy.get('round')}: decision at {time.strftime('%H:%M:%S', time.localtime(strategy.get('updated', 0)))}, "
            f"scorecard then: {score}",
            f"  prices: {strategy.get('prices')}  (below this round's mean prices: {strategy.get('below_mean')}, "
            f"equal to its lowest: {strategy.get('lowest')})",
            f"  style: {style_text(strategy.get('style') or {})}",
            f"  rule: {strategy.get('rule') or DEFAULT_RULE}",
            f"  tagline: {strategy.get('tagline') or 'none'}",
            f"  lead: {strategy.get('lead')}",
            f"  playbook: {' | '.join(strategy.get('playbook') or []) or 'none'}",
            f"  why: {strategy.get('why')}",
        ]
        + [f"  note: {p}" for p in (h[-1].get("problems") if h else [])]
    )


if __name__ == "__main__":
    import seller

    print(report(refresh(seller.RESERVE, seller.CARD_FACTS, seller.LAUNCH_REVIEWS)))
