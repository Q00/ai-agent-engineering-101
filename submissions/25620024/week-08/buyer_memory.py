"""Week 08 — buyer_memory.py: the buyer's memory in two layers.

  episodes  memory.jsonl, one line per trade, append only. The evidence; read in full only to rebuild.
  profiles  shop_profiles.json, one compact entry per shop, built from the episodes and kept up to date
            incrementally: the file remembers how far into memory.jsonl it has read (a byte offset) and
            reads only the lines added since. A retraction line ({"retracts": ts}) or a shorter file
            triggers a full rebuild.

The profiles are what the buyer reads when it picks a shop and haggles ("memory as a behavior prior":
where to look and what to expect, not the evidence itself). Before this, every order re-read all of
memory.jsonl (11,217 lines on 10/27) to recompute the same summaries.

Sanctions are graduated (Ostrom), judged on a recent window (Amazon's order defect rate), and forgive
once (generous tit-for-tat). Before this, one raised ask blocked ggari-shop (1,923 deals) for good.

  severe   a receipt above the agreed price, or for another item       blocked for good
  medium   a raised ask, a deal message that names another price       paused for COOLDOWN_H hours;
                                                                        a second one within REPEAT_DAYS: for good
  minor    an absurd first ask                                          only lowers the score; MINOR_TO_MEDIUM
                                                                        of them within REPEAT_DAYS count as medium
The numbers are defaults; theta.json (written by the meta-harness, the outer loop) overrides them.

    python buyer_memory.py              # rebuild and print the profiles
"""
import json
import re
import os
import statistics
import time
from pathlib import Path

MEMORY = Path(os.environ.get("MARKET_BUYER_MEMORY", Path(__file__).parent / "memory.jsonl"))
PROFILES = MEMORY.with_name(MEMORY.stem.replace("memory", "shop_profiles") + ".json") \
    if "memory" in MEMORY.stem else MEMORY.with_name("shop_profiles.json")
THETA = Path(os.environ.get("MARKET_THETA", Path(__file__).parent / "theta.json"))
PLAN = Path(os.environ.get("MARKET_BUYER_PLAN", Path(__file__).parent / "private" / "buyer_plan.json"))
SCHEMA = 5                 # bump when the profile layout changes: the next load() rebuilds   # 4: re-judge recorded problems (11-04 false offenses)
RATIOS = 20                # recent settle ratios kept per shop
LOWS = 200                 # recent agreed prices kept per item, across all shops
DEFAULTS = {"window": 50, "cooldown_h": 24, "repeat_days": 7, "minor_to_medium": 3, "prior_defect": 0.05,
            "prior_weight": 10,
            # haggling: the first offer is at most first_offer_max x the ask; each later offer closes at most
            # concede of the gap to the ask; after take_after low offers on an affordable ask, take the ask
            "first_offer_max": 0.70, "concede": 0.5, "take_after": 3,
            # Round 3: quotes asked in parallel, first offer and most I pay as a share of the list price, the price
            # share I expect before I know a shop, food shelf life beyond use_within_hours, goods grades I accept
            # close_ratio: when haggling ends (walk-away or out of offers), still take an ask at or under
            # close_ratio x list and the budget -- the board's surplus is list minus paid, so any such deal adds
            # surplus and a deal, a cancel adds nothing (11-04: 475 such walk-aways in 24 h, 2.87M surplus)
            "close_ratio": 0.9, "close_ratio_big": 0.9, "big_list": 100000,
            "rfq": 3, "r3_first_offer": 0.6, "max_pay_ratio": 0.95, "prior_price_ratio": 0.85, "food_margin_h": 1,
            "goods_grades": ["A", "B"],
            # Round 3 reach: an offer is a candidate while min_reach x list price fits the budget (leaders settle
            # near 0.5 of list; 0.6, the first-offer share, dropped half the big orders)
            "min_reach": 0.45,
            # tuned by the buyer team (11-04): a shop is paused after pause_no_answers silent trades within pause_h;
            # a food need above shelf_strict x the SKU's shelf life takes no deal without a stated shelf life
            "pause_no_answers": 3, "pause_h": 1.0, "shelf_strict": 0.45,
            "retry_alts": 2,         # quotes tried after a failed negotiation (11-04: 1 retry lifted deals 52% -> 64%)
            # quotes asked in parallel for an order of big_budget or more: the board counts absolute surplus, and
            # one 250k deal kept more than all small deals together
            "rfq_big": 5, "big_budget": 60000}


def theta():
    """The buyer's tunable numbers, merged in order: DEFAULTS < theta.json "buyer" (the meta-harness) <
    private/buyer_plan.json "theta" (the buyer team; its gate already clamped them). A missing file is skipped."""
    t = dict(DEFAULTS)
    for path, key in ((THETA, "buyer"), (PLAN, "theta")):
        try:
            t.update(json.loads(path.read_text(encoding="utf-8")).get(key) or {})
        except (OSError, ValueError, AttributeError):
            pass
    return t


def profile():
    """The theta profile as one block of text for the front of every prompt: the version and the rules the
    meta-harness wrote. It changes only when the meta-harness writes a new version, so between versions
    every prompt starts with the same text (the provider's prompt cache stays warm)."""
    try:
        t = json.loads(THETA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    rules = t.get("rules") or []
    if not rules:
        return ""
    return f"[buyer profile v{t.get('version', 0)}]\n" + "\n".join(f"- {r}" for r in rules) + "\n\n"


def CATEGORY_OF(sku, _c={}):
    """A SKU's catalog category (cached)."""
    if not sku:
        return None
    if not _c:
        try:
            rows = json.loads((Path(".market") / "catalog.json").read_text(encoding="utf-8"))
            _c.update({r["sku"]: r.get("category") for r in (rows if isinstance(rows, list) else rows.values())})
        except (OSError, ValueError):
            _c["_"] = None
    return _c.get(sku)


FALSE_OFFENSES = re.compile(
    r"raised its ask from (\d+) to (\d+)|claimed grade ([ABC]), the market says ([ABC])|receipt for (G[0-9a-f]{8}), ordered")


def severity(problem):
    """How bad a recorded problem was. Three kinds the buyer used to count were not offenses (11-04): an ask raised
    by 1% or less (rounding), a grade claim WORSE than the true grade, a receipt for another SKU of the order's
    category (any acceptable SKU settles); they are judged again here, so the bans they caused lift."""
    if not problem:
        return None
    m = FALSE_OFFENSES.search(problem)
    if m and m.group(1) and int(m.group(2)) <= int(m.group(1)) * 1.01 and problem.count("raised") == 1 and \
            problem.startswith("misbehaved") and "; " not in problem:
        return None
    if m and m.group(3) and "ABC".find(m.group(3)) > "ABC".find(m.group(4)):
        return None
    if m and m.group(5) and CATEGORY_OF(m.group(5)) and CATEGORY_OF(m.group(5)) == CATEGORY_OF(
            (re.search(r"ordered (G[0-9a-f]{8})", problem) or [None, None])[1]):
        return None
    if problem.startswith(("receipt above", "receipt for", "receipt names")):
        return None          # HQ 11-04: no ban for a bad receipt (the buyer never pays one); Enuri watches the shop
    if problem.startswith("misbehaved (hallucination)"):
        return "medium"
    if problem.startswith("misbehaved"):
        return "minor"
    return None                                  # no answer, no receipt: not cheating


def _empty():
    return {"trades": 0, "deals": 0, "signed": 0, "no_answer": 0, "offenses": [], "problems": [],
            "recent": [], "paid": [0.0, 0], "ratios": [], "lratios": [], "last_price": {}, "last_ts": 0}


def _add(p, m, window):
    if m.get("outcome") == "review":         # Round 3 review hook: what the settled deal showed, not a new trade
        if severity(m.get("problem")):
            p["offenses"] = (p.get("offenses", []) + [[m.get("ts") or 0, severity(m["problem"]), m["problem"]]])[-10:]
        return
    p.setdefault("lratios", [])
    p["trades"] += 1
    ts = m.get("ts") or 0
    p["last_ts"] = max(p["last_ts"], ts)
    prob = m.get("problem")
    sev = severity(prob)
    p["recent"] = (p["recent"] + [[ts, m.get("outcome"), sev]])[-window:]
    if prob:
        p["problems"] = (p["problems"] + [prob])[-2:]
    if sev:
        p["offenses"] = (p["offenses"] + [[ts, sev, prob]])[-10:]
    if m.get("outcome") == "no answer":
        p["no_answer"] += 1
    if m.get("outcome") == "deal":
        p["deals"] += 1
        p["signed"] += bool(m.get("signed"))
        agreed, budget, first = m.get("agreed"), m.get("budget"), m.get("first_ask")
        if agreed and budget:
            p["paid"] = [p["paid"][0] + agreed / budget, p["paid"][1] + 1]
        if agreed and first:
            p["ratios"] = (p["ratios"] + [round(agreed / first, 3)])[-RATIOS:]
        if agreed and m.get("item"):
            p["last_price"][m["item"]] = agreed
        if agreed and m.get("list_price"):
            p["lratios"] = (p["lratios"] + [round(agreed / m["list_price"], 3)])[-RATIOS:]


def _item_low(state, m):
    if m.get("outcome") == "deal" and m.get("agreed") and m.get("item"):
        lows = state.setdefault("items", {}).setdefault(m["item"], [])
        lows.append(m["agreed"])
        del lows[:-LOWS]


def _rebuild(window):
    state, profiles, rows = {"items": {}}, {}, []
    if MEMORY.exists():
        for line in MEMORY.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    withdrawn = {m["retracts"] for m in rows if "retracts" in m}
    for m in rows:
        if "retracts" in m or m.get("ts") in withdrawn or not m.get("shop"):
            continue
        _add(profiles.setdefault(m["shop"], _empty()), m, window)
        _item_low(state, m)
    offset = MEMORY.stat().st_size if MEMORY.exists() else 0
    return {**state, "schema": SCHEMA, "window": window, "offset": offset, "built": time.time(), "profiles": profiles}


def load(full=False):
    """The shop profiles, brought up to date with any lines added to memory.jsonl since the last call.
    full=True returns the whole state (profiles plus the recent agreed prices per item)."""
    window = theta()["window"]
    try:
        state = json.loads(PROFILES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = None
    size = MEMORY.stat().st_size if MEMORY.exists() else 0
    if not state or state.get("schema") != SCHEMA or state.get("window") != window or size < state.get("offset", 0):
        state = _rebuild(window)
    elif size > state["offset"]:
        with MEMORY.open("rb") as f:
            f.seek(state["offset"])
            new = f.read().decode("utf-8", errors="ignore")
        lines = [json.loads(x) for x in new.splitlines() if x.strip().startswith("{")]
        if any("retracts" in m for m in lines):
            state = _rebuild(window)
        else:
            for m in lines:
                if m.get("shop"):
                    _add(state["profiles"].setdefault(m["shop"], _empty()), m, window)
                    _item_low(state, m)
            state["offset"] = size
    try:
        PROFILES.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return state if full else state["profiles"]


def sanction(p, now=None, t=None):
    """(level, reason): ("ban", ...) for good, ("pause", ...) for now, or (None, None)."""
    if not p or not p["offenses"]:
        return None, None
    t, now = t or theta(), now or time.time()
    severe = [o for o in p["offenses"] if o[1] == "severe"]
    if severe:
        return "ban", severe[-1][2]
    lately = [o for o in p["offenses"] if now - o[0] < t["repeat_days"] * 86400]
    medium = [o for o in lately if o[1] == "medium"]
    minor = [o for o in lately if o[1] == "minor"]
    if len(minor) >= t["minor_to_medium"]:
        medium += [minor[-1]]
    if len(medium) >= 2:
        return "ban", f"{len(medium)} offenses within {t['repeat_days']} days, latest: {medium[-1][2]}"
    if medium and now - medium[-1][0] < t["cooldown_h"] * 3600:
        left = t["cooldown_h"] - (now - medium[-1][0]) / 3600
        return "pause", f"{medium[-1][2]} (paused {left:.0f} h more; once more within {t['repeat_days']} days and it is for good)"
    return None, None


def defect_rate(p, t=None):
    """Share of my recent trades with this shop that had an offense, pulled toward a prior when there are few."""
    t = t or theta()
    recent = p["recent"] if p else []
    bad = sum(1 for r in recent if len(r) > 2 and r[2])
    return (bad + t["prior_defect"] * t["prior_weight"]) / (len(recent) + t["prior_weight"])


def market_low(state, item):
    """Where the market's floor is: the 10th percentile of my recent agreed prices for this item at any
    shop, not the minimum, so one shop selling far below the floor does not move it."""
    lows = sorted((state.get("items") or {}).get(item) or [])
    return lows[len(lows) // 10] if len(lows) >= 5 else None


def list_ratio(p):
    """Round 3: median of price paid / list price over my recent deals with this shop, or None with fewer than 2."""
    return statistics.median(p["lratios"]) if p and len(p.get("lratios") or []) >= 2 else None


def settle_ratio(p):
    """Median of agreed / first ask over my recent deals with this shop, or None with fewer than 3."""
    return statistics.median(p["ratios"]) if p and len(p["ratios"]) >= 3 else None


if __name__ == "__main__":
    t = time.time()
    state = load(full=True)
    prof = state["profiles"]
    print(f"{len(prof)} shops from {MEMORY.name} in {time.time() - t:.2f} s; market lows "
          f"{ {i: market_low(state, i) for i in state.get('items', {})} }")
    for name, p in sorted(prof.items(), key=lambda kv: -kv[1]["deals"])[:15]:
        r = settle_ratio(p)
        print(f"  {name:20} trades {p['trades']:5} deals {p['deals']:5} signed {p['signed']:5} "
              f"settles at {f'{r:.0%}' if r else '-':>4} of first ask  defects {defect_rate(p):.1%}")
    for name, p in prof.items():
        level, why = sanction(p)
        if level:
            print(f"  {level:5} {name}: {why}")
