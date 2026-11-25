"""The lines the meta-harness cannot move. Everything else starts cautious in pricing.json and is widened or narrowed by
Terra and Astra from Round 3's own numbers, a bounded step at a time.

- Bankruptcy: no stock is bought that could take the balance under cash_buffer(): the market's 500,000 line plus the
  refunds still owed at the worst rate and a day of storage (never under STOP_BUYING_BELOW;
  a bankruptcy's 5,000,000 counts against profit). The kit files only when nothing is left to sell. Since 11/06 19:03
  (a person) this is the only line on stock and capital: the stock cap and the step limits on a rise are open.
- pricing.json: a rise is not limited; a fall moves at most MAX_STEP_DOWN of its value per cycle, inside BOUNDS where
  listed, and the meta-harness may not take a META_FLOOR setting under its line.
- Tools the agents run get no keys, no network and no writes outside their folders (agent_tools.py builds the sandbox
  from SANDBOX below).

meta_harness.py rejects a change that touches a PROTECTED file, removes a line naming this module, or moves a
pricing.json number further than this file allows. Only a person changes this file.
"""
import math
import re
import time

BANKRUPT_LINE = 500000
STOP_BUYING_BELOW = 1000000          # a supply that could leave less than this is not sent
# A person, 11/06 19:03 (대표님): "복리를 진짜 제대로 활용해야해 hard_limits 는 완전 풀어버려 그냥 파산만 안되면 문제없어".
# So the caps on stock, capital and step size are open, and the one line left is the cash a buy may not cross: the
# bankruptcy line plus what the shop can still owe. Its numbers, Round 3 11/03 21:12 - 11/06 20:47 (collector ledger and
# board, review/W-20261106-1900-AX자본투입/buffer.py):
# - refunds: our goods refunds over our warranted goods sales were 5.4% (29.6M of 546.6M), 13.0% in the worst 6 h
#   bucket (p90 10.0%); the most refunded in 1 h 2.76M, in 6 h 5.65M, in 24 h 14.58M. A unit breaks within 6 h of the
#   sale, spread evenly (2,098 market breaks 11/06: median 2.9 h, p90 5.4 h), so a sale a hours old still owes
#   rate x price x (6 - a) / 6.
# - storage: 1% of the stock's cost a day (agent.md).
# - the worst 24 h fall of book was 2.5M (11/04 14:45), mostly these same refunds; cash fell 90.7M in 24 h only because
#   it went into stock.
# A person, 11/06 22:22 ("최대한 점수를 올려 더더욱 극단적이어도 돼", still no bankruptcy): keep what the refunds are
# expected to cost, from the measured rate and the refunds actually paid, not the worst bucket on top of a bad day.
# Round 3 11/03 21:12 - 11/06 22:2x (review/W-20261106-2225-AX극단상향, capital-1900/buffer.py): refunds were 5.6% of
# our warranted goods sales (33.1M of 588.8M; 6 h buckets median 5.6%, p90 10%, max 13%), and the most refunded in any
# 6 h was 5.76M, about what 6% of our 6 h sales comes to now (~5.9M at 22:25). The old buffer (13% + 2 days of storage
# + a 2.5M bad day) kept 17-18M against a 20M balance and held supplies back from 21:05. The sales still coming in pay
# refunds too; this is what is kept if they stop.
REFUND_EXPECTED = 0.06
# Tail (independent review, 11/06 22:4x): a bad 6 h can refund much more than the average while sales stop, so the buffer
# never keeps less than the worst rate's exposure. A shop with no recent sales keeps nothing extra.
# GPT Pro 11/07 04:30 S1 replay (review/W-20261107-0600-AX파산안전, testdata/r3_cash_replay.json): of the 549 past board
# times whose next 6 h the ledger has seen, the worst 6 h refunded 18.6% of the weighted exposure (11/06 05:25, 27M) and
# 14.9% at 83M (11/06 20:33); at 13% capped at REFUNDS_6H_WORST the cash spent to the buffer fell short 3 times
# (worst 673,731 under the line). 20%, with no cap, covers every one of them on its own.
REFUND_WORST = 0.20
# The most Round 3 refunded in 6 h, from the ledger: 5.76M up to 11/06 22:2x, then 14,438,775 in the 6 h to 11/07 01:24
# (175 refunds, GPT Pro 11/07 01:35, recounted from r3_supply_broken_expire_storage.csv). At 5.8M the buffer was 12.9M
# (02:34), so that window with sales stopped would have taken the balance under BANKRUPT_LINE. 20M keeps ~1.4x that.
# Since 11/07 06:xx it no longer caps REFUND_WORST's exposure (a cap is what fell short 3 of 549 times, GPT Pro 04:30
# S1); it stays the least an unreadable ledger would have to keep (UNKNOWN_EXPOSURE). Raise it when a 6 h refunds more.
REFUNDS_6H_WORST = 20000000
# GPT Pro 11/06 23:40 replay: with the two rates alone 14 of 481 past board times would have crossed the line had nothing
# sold after them; the BIGGEST_SALES biggest warranted sales of the last BREAK_HOURS, refunded in full, made it 0 of 481
BIGGEST_SALES = 3
BREAK_HOURS = 6
STORAGE_DAYS = 1.0
STORAGE_PER_DAY = 0.01
# A unit bought adds its own storage to the buffer: each one is paid for at cost x (1 + BUY_STORAGE) out of the cash over
# it (GPT Pro 11/07 04:30 S1: the storage after the buy belongs in the same cash check)
BUY_STORAGE = STORAGE_DAYS * STORAGE_PER_DAY
# "Open" bounds are a large finite number, never inf: json reads Infinity, and an inf keep_cash made guard() compute
# int(nan) and crash the hook (independent review). 1e12 is 200,000 x the starting 5M.
OPEN = 10 ** 12
# Without the ledger the exposure is not known. It was kept at this guess (13% of 6 h of goods sales at our 11/06
# 19:15-20:47 rate, never under the worst 6 h refunded); since 11/07 06:xx an unreadable ledger buys nothing at all
# (cash_buffer returns OPEN; GPT Pro 04:30 S1: exposure missing -> no new stock), since 20% of 11/07 04:31's exposure
# (196M) was already 39M, twice this guess. Kept as the record of that floor (test_hard_limits pins it), unused.
UNKNOWN_EXPOSURE = max(15000000, REFUNDS_6H_WORST)
PROTECTED = ("hard_limits.py", "test_hard_limits.py", "run_market.sh", "run_meta.sh", "ensure_running.sh")
# Steps: a rise is no longer limited (a person, 11/06 19:03: compounding should not wait for cycles); a fall still is,
# since a cut does nothing for growth and one cycle once cut a cap to nothing (11/05 11:48).
MAX_STEP = math.inf                  # was 1.0: double at most
# ... or by four times its value (five times it) once the evidence is a real sample, so the stock cap can go from 15M
# toward the top ten's 78M average in two cycles. A fall stays at MAX_STEP_DOWN whatever the sample: Patch 3.3 scores
# book (cash + unsold stock at cost), the shop held 3.2M of stock against the top ten's 24-158M (11/05 11:09 board),
# and a strong step of 100% let one cycle cut a cap to nothing (a person, 11/05 11:48).
MAX_STEP_STRONG = math.inf           # was 4.0
MAX_STEP_UNBOUNDED = math.inf        # was 2.0: a setting with no BOUNDS (food_max_stock_cost, sku_target_units)
MAX_STEP_DOWN = 0.5
STRONG_SAMPLE = 40                   # settled deals (shop + buyer) in the Round 3 log that make a sample
# The meta-harness may not take these under the line (a person may, in pricing.json): scale is the strategy now and a
# bankruptcy is out of reach (balance 51.7M against the 0.5M line, 11/05 11:50). Narrow per SKU instead. keep_cash: with
# the stock cap open to 150M, the cash a restock leaves is what pays the 6 h warranty refunds of goods already sold and
# the storage fee (1%/day of stock cost) after the buy; 3M covers the dearest listed unit's refund (2.3M) and a day of
# fees on 70M of stock.
META_FLOOR = {"restock.max_stock_cost": 10000000, "restock.keep_cash": 3000000}
# Settings the meta-harness may not change at all (a person may): unlisted dear goods stay out of the explore lines,
# whose price bound is now 10M.
# buyer.oneshot: the talk priced by code instead of the harness (oneshot.py) is the owner's switch, not a cycle's step
# 11/11, work-state instruction 8: the profit window and fast restock switch are a person's decisions.
FIXED = {"restock.high_price_listed_only": True, "buyer.oneshot": True, "negotiate.engine": "llm",
         "negotiate.formula": "share", "negotiate.counter_share": [], "restock.window_fast": False,
         "price.grade_profit_until": 0, "price.grade_profit_hours": 0, "price.grade_profit_default": False}   # values unused: the keys are a person's switches
FIXED_NEW = ("restock.window_fast", "price.grade_profit_until", "price.grade_profit_hours", "price.grade_profit_default")
GRADE_PROFIT_PREFIX = "price.grade_profit_share."

# path in pricing.json -> (lowest, highest). Outside these the shop's own data has not earned the risk.
BOUNDS = {
    # The floor is STOP_BUYING_BELOW, the least that keeps supplies going: restock never spends under it whatever
    # keep_cash says (spendable), so a lower floor would change nothing. The meta-harness stops at META_FLOOR (3M).
    "restock.keep_cash": (STOP_BUYING_BELOW, OPEN),     # was 20M: more kept is never a bankruptcy risk
    # 150M: Patch 3.3 scores book (unsold stock at cost); the 4M cap was what cut our lines (847 of the last 2,000 guard
    # cuts, 11/05 11:1x) while the 60만+ goods that bring the top ten 82% of their revenue sell in 0.37 h, and the top
    # ten hold 78M of stock on average, 150M at most (review/W-20261105-0953-AX패치3.3/scale-analysis.md). A person set
    # the bound there (11/05 11:48: "크게크게"); pricing.json holds the working value, grown inside it as sales follow.
    # 11/06 19:03 a person opened it (was 150M): the cash buffer (cash_buffer) is what limits a buy now.
    "restock.max_stock_cost": (100000, OPEN),
    "restock.target_per_category": (0, 100),
    # A person, 11/11 12:05 (대표님): "하드 리밋 풀어버려 왜 쓸데없는거 유지하고 잇지?"
    # Open to 20: /rules allows ~18 calls per 180 s window; the kit's 10-line cut left 3-8 lines behind
    # at each close in four measured windows (12-14 calls), with 220M above the 20M cash buffer.
    # 20 is what the kit sends of one command (scripts/market_kit.py MAX_SUPPLY_LINES, restock.KIT_LINES): a line
    # past it would leave the agent's queue and never be sent.
    "restock.max_lines": (1, 20),
    "restock.max_units_per_sku": (1, 1000),
    "restock.cover_hours": (1, 72),
    "restock.food_explore_qty": (0, 20),
    "restock.food_max_qty": (0, 20),  # units per food line: MAX_LOT (20) cuts a line anyway
    "restock.max_break": (0.0, 0.5),
    # 10M / 1M: over twice the dearest goods (4,045,800) and food (435,000) in the 11/05 catalog. Both only pick the
    # two explore lines; goods over high_price_goods off the proven / priority lists stay out (high_price_listed_only).
    "restock.goods_max_price": (5000, 10000000),
    "restock.food_max_price": (5000, 1000000),
    "restock.band_low": (0.3, 1.0),
    "restock.band_high": (1.0, 3.0),
    "restock.prior_break": (0.0, 0.5),
    "opening.explore_budget_worst": (0, OPEN),
    "opening.judge_settles": (10, 1000),
    "opening.phase_gap_minutes": (5, 120),
    "restock.max_sku_share": (0.01, 1.0),
    # What counts as dear goods (listed only, FIXED above) and how many of each: unbounded, a few cycles of doubling
    # would lift high_price_goods past every price and switch the listed-only rule off.
    "restock.high_price_goods": (50000, 1000000),
    "restock.high_price_max_units": (0, 1000),
    "restock.high_price_proven_max_units": (0, 1000),
    # floors sit at break-even (a person 11/06 22:22): the least goods break chance stays over our measured grade A
    # rate (2.3% of units, 1.0% of value, 1,107 sold 11/03-11/06), so a meta cycle cannot price under the refunds
    "price.goods_break": (0.03, 0.5),
    "warranty.on_request_hours": (0, 6),
    "warranty.max_hours": (0, 72),
    "warranty.max_break": (0.0, 0.5),
    "warranty.prior_break": (0.0, 0.5),
    "buyer.max_price_share": (0.3, 1.0),
    # A person, 11/11 12:05 (the same decision as restock.max_lines above). The old bound equalled the working
    # value of 10. Each quote is a shop conversation, while the order budget, list price and 6 h goods warranty
    # bound every payment. A rise above 10 is a person's to make (meta/terra.md): it talks to that many shops an order.
    "buyer.quote_shops": (1, 100),
    "markdown.min_floor_share": (0.0, 1.0),
    "forum.max_votes": (0, 30),
    "forum.every_minutes": (5, 240),
}
MARKDOWN_REASONS = ("food_expiry", "stale_goods", "round_end")
# Goods break up to 6 hours after the sale (agent.md). My buyer pays for goods only with a warranty that covers all of it,
# and my shop sells goods with one by default (its reserve carries the break chance). Fixed: not a setting.
# The shop's is the market's longest, 72 h (/rules): a unit breaks only within 6 h of the sale, so 72 h costs what 6 h
# does, and inextro and 25620027 (revenue 1st and 3rd, 11/06) quote 72 h to buyers comparing shops.
# 대표님 7955 승인 (2026-11-08 17:55, "일단 너가 찾은 수식으로 해"): 기대값 상한 내 예외. With the switch
# .market/buyer_short_warranty.json on, a goods receipt with a shorter warranty is paid only at a price/list at or under
# p_max(h) = 1 - (S + q) / (1 - b), q = b * max(0, 6 - h) / 6 (a break outside the warranty counts -1, agent.md 229),
# under an hourly exposure cap (buyer_short_warranty.py). With the
# switch off this 6 h holds exactly as before. The other lines here (bankruptcy, cash buffer, budget) are unchanged.
BUYER_GOODS_WARRANTY_HOURS = 6
SELLER_GOODS_WARRANTY_HOURS = 72
# The buyer spends the market's money, not the shop's: each order brings its own budget (agent.md: no buyer wallet), and
# the order budget, the list price, the 6 h goods warranty and the food shelf-life check bound every payment. A cap per
# deal only gave up the dear goods most of the top buyers' surplus comes from (80만: 1 of 113 such orders settled), so
# there is none, and a pricing.json value for it is dropped here whoever wrote it (a person decided this, 2026-11-04).
UNCAPPED = {"buyer": ("max_spend_per_deal",)}
# The cautious start, used when a bounded setting is missing a number.
# The working values of 11/06 19:20 (a person, 19:03: not the cautious start of 11/03); the cash buffer still applies.
SAFE = {"restock": {"keep_cash": 4500000, "max_stock_cost": 150000000, "target_per_category": 1, "max_lines": 10,
                    "max_units_per_sku": 20, "cover_hours": 6, "food_explore_qty": 1, "food_max_qty": 4, "max_break": 0.15,
                    "goods_max_price": 45000, "max_sku_share": 0.2, "food_max_price": 30000, "band_low": 0.8,
                    "band_high": 1.2, "prior_break": 0.15, "high_price_goods": 100000, "high_price_max_units": 20,
                    "high_price_proven_max_units": 20},
        "warranty": {"on_request_hours": 3, "max_hours": 72, "max_break": 0.25, "prior_break": 0.15},
        "buyer": {"max_price_share": 0.9, "quote_shops": 3},
        "markdown": {"min_floor_share": 0.1, "below_cost": ["food_expiry"]},
        "forum": {"max_votes": 5, "every_minutes": 15},
        "opening": {"explore_budget_worst": 350000, "judge_settles": 50, "phase_gap_minutes": 15}}

# What a tool may touch. Paths come from agent_tools.py at run time: {work} is a fresh folder for this one call, {reads}
# the repository and Python's own folders. Denials repeat the operation they override (file-read-data): a broader
# "file-read*" deny does not win over a later, narrower allow. .market/ is read only: a tool cannot write the forum outbox, its own selftest
# result or the files the kit reads. Other processes (the restock or seller that started it, with the keys in their
# environment) are invisible to it, and it can start no program but Python.
SANDBOX = """(version 1)
(allow default)
(deny network*)
(allow network* (local unix))
(deny process-info*)
(allow process-info* (target self))
(deny process-exec*)
(allow process-exec* {exe})
(deny file-write*)
(allow file-write* (subpath "{work}") (literal "/dev/null"))
(deny file-read-data (subpath "{home}") (subpath "/Volumes"))
(allow file-read-data {reads})
(deny file-read-data (regex #"/\\.env") (regex #"\\.pem$") (regex #"/\\.git/") (subpath "{home}/.codex") (subpath "{home}/.ssh"))
"""
ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "HOME")
ENV_PASS_PREFIX = ("BUYER_", "SHOP_", "AGENT_", "MARKET_")      # the agents' own settings (file paths, names)
ENV_SECRET = r"KEY|TOKEN|SECRET|PASS|OWNER|AUTH|COOKIE|CRED"     # never passed, whatever the prefix


def cash_buffer(stock_cost=0, sales=None, now=None):
    """The cash a buy may not take the balance under: the bankruptcy line, the refunds the shop's warranted goods sales of
    the last BREAK_HOURS can still bring at the worst rate (REFUND_WORST, no cap; never under the BIGGEST_SALES biggest
    of those sales in full) and STORAGE_DAYS of storage on the stock (held and on its way). sales: [(unix time, price)]
    of this shop's warranted goods sales, or None when the ledger could not be read: then OPEN, so nothing is bought.
    Never under STOP_BUYING_BELOW."""
    now = now or time.time()
    span = BREAK_HOURS * 3600
    if sales is None or (isinstance(stock_cost, float) and not math.isfinite(stock_cost)):
        return OPEN         # exposure or stock not known (independent review 11/07 06:xx: a nan stock kept no storage)
    live = [(t, p) for t, p in sales if number(t) and number(p) and p > 0 and 0 <= now - t < span]
    left = sum(p * (span - (now - t)) / span for t, p in live)
    top = sum(sorted((p for _, p in live), reverse=True)[:BIGGEST_SALES])
    owed = max(REFUND_EXPECTED * left, REFUND_WORST * left, top)
    stock = stock_cost if number(stock_cost) and stock_cost > 0 else 0
    return max(STOP_BUYING_BELOW, math.ceil(BANKRUPT_LINE + owed + STORAGE_DAYS * STORAGE_PER_DAY * stock))


def spendable(balance, buffer=STOP_BUYING_BELOW):
    """The most a restock may spend now without crossing the cash buffer (never less than STOP_BUYING_BELOW)."""
    if isinstance(buffer, float) and not math.isfinite(buffer):
        return 0            # a broken buffer buys nothing (fails closed, independent review)
    floor = buffer if number(buffer) and buffer > STOP_BUYING_BELOW else STOP_BUYING_BELOW
    return max(0, (balance or 0) - floor) if number(balance) else 0


def flat(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flat(v, key + "."))
        else:
            out[key] = v
    return out


def number(v):
    """A finite int or float: nan, inf and bools are not numbers here (a setting of Infinity falls back to SAFE)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_policy_change(old, new, step=MAX_STEP):
    """Problems with moving pricing.json from old to new (dicts). None of them: the change may stand."""
    problems, a, b = [], flat(old), flat(new)
    for key, v in b.items():
        lo, hi = BOUNDS.get(key, (-math.inf, math.inf))
        if number(v) and not lo <= v <= hi:
            problems.append(f"pricing.json {key}={v} is outside [{lo}, {hi}] (hard_limits.BOUNDS)")
        was = a.get(key)
        step = min(step, MAX_STEP_STRONG)
        rise = number(v) and number(was) and was >= 0 and v >= was
        move = (step if key in BOUNDS else min(step, MAX_STEP_UNBOUNDED)) if rise else min(step, MAX_STEP_DOWN)
        if number(v) and number(was) and v != was and not math.isinf(move) \
                and abs(v - was) > max(abs(was) * move, 1 if isinstance(was, int) else 0):
            problems.append(f"pricing.json {key} {was} -> {v} moves more than {move:.0%} in one cycle")
        if key in META_FLOOR and number(v) and number(was) and was >= META_FLOOR[key] > v:
            problems.append(f"pricing.json {key} {was} -> {v} goes under {META_FLOOR[key]} (hard_limits.META_FLOOR, a person's line)")
        if key not in a and (key in FIXED_NEW or key.startswith(GRADE_PROFIT_PREFIX)):
            problems.append(f"pricing.json {key} was added (hard_limits.FIXED, a person's setting)")
        if (key in FIXED or key.startswith(GRADE_PROFIT_PREFIX)) and key in a \
                and not (type(v) is type(was) and v == was):     # true -> 1 is a change too
            problems.append(f"pricing.json {key} {was!r} -> {v!r} is a person's setting (hard_limits.FIXED)")
        if key == "markdown.below_cost" and (not isinstance(v, list) or any(r not in MARKDOWN_REASONS for r in v)):
            problems.append(f"pricing.json markdown.below_cost {v} is not a list of {MARKDOWN_REASONS}")
        if key in BOUNDS and not number(v):
            problems.append(f"pricing.json {key}={v!r} must stay a number")
    for key in (*BOUNDS, *FIXED, "markdown.below_cost"):
        if key in a and key not in b:
            reason = "hard_limits.FIXED, a person's setting" if key in FIXED_NEW else "bounded settings stay"
            problems.append(f"pricing.json {key} was removed ({reason})")
    for key in a:
        if key.startswith(GRADE_PROFIT_PREFIX) and key not in b:
            problems.append(f"pricing.json {key} was removed (hard_limits.FIXED, a person's setting)")
    return problems


def clamp_cfg(section, cfg):
    """A section of pricing.json as the code may use it: bounded numbers inside their bounds, and a value that is not a
    number (null, text) replaced by the cautious start (SAFE). Applied at every run, so even a settings file edited
    outside the meta-harness's checks cannot take the shop past these lines."""
    out = {k: v for k, v in cfg.items() if k not in UNCAPPED.get(section, ())}
    for key, (lo, hi) in BOUNDS.items():
        sec, _, name = key.partition(".")
        if sec != section or (name not in out and name not in SAFE.get(section, {})):
            continue
        v = out.get(name)
        out[name] = min(max(v, lo), hi) if number(v) else SAFE[section][name]
    if section == "markdown":
        r = out.get("below_cost")
        out["below_cost"] = [x for x in r if x in MARKDOWN_REASONS] if isinstance(r, list) else list(SAFE["markdown"]["below_cost"])
    return out


def final_buy(buy, balance, catalog, worst=0.75, buffer=STOP_BUYING_BELOW):
    """The last word on a restock's lines, just before the kit buys them: none that could take the balance under the cash
    buffer (cash_buffer, plus what lines already sent will still cost; never under STOP_BUYING_BELOW) at worst cost, with
    the storage each unit bought adds to the buffer (BUY_STORAGE)."""
    left, out = spendable(balance, buffer), []
    for b in buy if isinstance(buy, list) else []:
        if not isinstance(b, dict) or not isinstance(b.get("qty"), int) or isinstance(b.get("qty"), bool):
            continue
        unit = ((catalog.get(b.get("sku")) or {}).get("list_price") or 0) * worst * (1 + BUY_STORAGE)
        qty = min(b["qty"], 20, int(left // unit) if unit > 0 else 0)
        if qty >= 1:
            out.append({"sku": b["sku"], "qty": qty})
            left -= unit * qty
    return out


def policy_diff(old, new):
    """'key a -> b' for each setting that changed: what a commit message says about the change."""
    a, b = flat(old), flat(new)
    return [f"{k} {a.get(k)!r} -> {b.get(k)!r}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def guard_problems(changed_paths, diff_text):
    """A change may not edit the protected files or drop a line that uses this module."""
    out = [f"{p} is protected (hard_limits.PROTECTED)" for p in changed_paths if p.split("/")[-1] in PROTECTED]
    removed = [ln for ln in diff_text.splitlines() if ln.startswith("-") and not ln.startswith("---") and "hard_limits" in ln]
    if removed:
        out.append(f"the change removes {len(removed)} line(s) using hard_limits: {removed[0][:120]}")
    rebinding = [ln for ln in diff_text.splitlines() if re.match(r"\+(?!\+\+).*\b(hard_limits|hl)\.\w+\s*=(?!=)", ln)
                 or re.match(r"\+(?!\+\+).*(setattr\(\s*(hard_limits|hl)\b|import hard_limits as (?!hl\b))", ln)]
    if rebinding:
        out.append(f"the change rebinds hard_limits: {rebinding[0][:120]}")
    return out
