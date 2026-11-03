"""The lines the meta-harness cannot move. Everything else starts cautious in pricing.json and is widened or narrowed by
Terra and Astra from Round 3's own numbers, a bounded step at a time.

- Bankruptcy: no stock is bought that could take the balance under STOP_BUYING_BELOW (the market's line is 500,000;
  a bankruptcy's 5,000,000 counts against profit). The kit files only when nothing is left to sell.
- pricing.json: each numeric setting moves at most MAX_STEP of its value per cycle, inside BOUNDS where listed.
- Tools the agents run get no keys, no network and no writes outside their folders (agent_tools.py builds the sandbox
  from SANDBOX below).

meta_harness.py rejects a change that touches a PROTECTED file, removes a line naming this module, or moves a
pricing.json number further than this file allows. Only a person changes this file.
"""
import math

BANKRUPT_LINE = 500000
STOP_BUYING_BELOW = 1000000          # a supply that could leave less than this is not sent
PROTECTED = ("hard_limits.py", "test_hard_limits.py")
MAX_STEP = 0.5                       # a number may change by at most half its value per cycle (and by 1 when it is small)

# path in pricing.json -> (lowest, highest). Outside these the shop's own data has not earned the risk.
BOUNDS = {
    "restock.keep_cash": (STOP_BUYING_BELOW, 4500000),
    "restock.max_stock_cost": (100000, 4000000),
    "restock.target_per_category": (0, 5),
    "restock.max_lines": (1, 10),
    "restock.max_units_per_sku": (1, 20),
    "restock.cover_hours": (1, 72),
    "restock.food_explore_qty": (0, 5),
    "restock.food_max_qty": (0, 20),
    "restock.max_break": (0.0, 0.5),
    "restock.goods_max_price": (5000, 2000000),
    "restock.max_sku_share": (0.01, 0.3),
    "warranty.on_request_hours": (0, 6),
    "warranty.max_hours": (0, 6),
    "warranty.max_break": (0.0, 0.5),
    "warranty.prior_break": (0.0, 0.5),
    "buyer.max_price_share": (0.3, 1.0),
    "buyer.max_spend_per_deal": (10000, 5000000),
    "markdown.min_floor_share": (0.0, 1.0),
    "forum.max_votes": (0, 30),
    "forum.every_minutes": (5, 240),
}
MARKDOWN_REASONS = ("food_expiry", "stale_goods", "round_end")

# What a tool may touch. Paths come from agent_tools.py at run time.
SANDBOX = """(version 1)
(allow default)
(deny network*)
(allow network* (local unix))
(deny file-write*)
(allow file-write* (subpath "{market}") (subpath "{tmp}") (literal "/dev/null"))
(deny file-read* (regex #"/\\.env$") (regex #"\\.pem$") (subpath "{home}/.codex") (subpath "{home}/.ssh")
  (subpath "{home}/.config") (subpath "{home}/Library/Keychains"))
"""
ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "HOME")
ENV_PASS_PREFIX = ("BUYER_", "SHOP_", "AGENT_", "MARKET_")      # the agents' own settings (file paths, names)
ENV_SECRET = r"KEY|TOKEN|SECRET|PASS|OWNER|AUTH|COOKIE|CRED"     # never passed, whatever the prefix


def spendable(balance):
    """The most a restock may spend now without risking the bankruptcy line."""
    return max(0, (balance or 0) - STOP_BUYING_BELOW)


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
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_policy_change(old, new):
    """Problems with moving pricing.json from old to new (dicts). None of them: the change may stand."""
    problems, a, b = [], flat(old), flat(new)
    for key, v in b.items():
        lo, hi = BOUNDS.get(key, (-math.inf, math.inf))
        if number(v) and not lo <= v <= hi:
            problems.append(f"pricing.json {key}={v} is outside [{lo}, {hi}] (hard_limits.BOUNDS)")
        was = a.get(key)
        if number(v) and number(was) and v != was and abs(v - was) > max(abs(was) * MAX_STEP, 1 if isinstance(was, int) else 0):
            problems.append(f"pricing.json {key} {was} -> {v} moves more than {MAX_STEP:.0%} in one cycle")
        if key == "markdown.below_cost" and (not isinstance(v, list) or any(r not in MARKDOWN_REASONS for r in v)):
            problems.append(f"pricing.json markdown.below_cost {v} is not a list of {MARKDOWN_REASONS}")
    for key in BOUNDS:
        if key in a and key not in b:
            problems.append(f"pricing.json {key} was removed (bounded settings stay)")
    return problems


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
    return out
