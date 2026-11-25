"""Round 3's opening stock, from the GPT Pro review (review/W-20261103-2025/gpt-pro-answer1.md §5, §6):

- Supply opens every 20 minutes for 3 minutes (agent.md); the kit sends at most 20 lines a run (10 until 11/11), 10 s apart.
- Window 1: phase 1, ten categories near their median price, one unit each (six food with shelf_hours 720 and list
  price <= 30,000, four goods <= 45,000). Windows 2 and 3: phases 2 (ten) and 3 (four), each a conditional candidate:
  once the Round 3 ledger has judge_settles settles, a candidate whose category has settled nothing is dropped, and one
  whose category settles mostly another SKU that fits the same rules is replaced by that SKU.
- Exploration in the first 24 hours stays under explore_budget_worst at worst cost (0.75 x list price).
- Outside a window the hook buys nothing (the kit would wait 90 s per line and then skip it).
pricing.json "opening" holds all of it; the meta-harness may move it like any setting. State: .market/opening_state.json.
"""
import collections
import json
import os
import time

WORST_COST = 0.75
DEFAULTS = {"phases": [], "phase_gap_minutes": 15, "judge_settles": 50, "explore_budget_worst": 350000,
            "explore_hours": 24, "food_max_price": 30000, "goods_max_price": 45000, "band_low": 0.8, "band_high": 1.2,
            "min_shelf_hours": 720}


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


RETRY_AFTER = 300      # seconds after a phase is sent before "nothing arrived" means the window was missed
MAX_TRIES = 2          # a phase nothing of which ever arrives is skipped after this many sends
SECONDS_PER_LINE = 10  # the kit's pace between supply calls
# The kit tries a line 3 times and sleeps min(wait, 30) s after each 429, so the third try comes about 60 s after the
# first: a line sent 70 s before the window opened was refused three times and lost (05:41:19, "opens in 4 s").
# The three tries cover a wait of about 60 s; 50 s leaves 10 s for slow calls. A run this holds back is followed by the
# next one about 60 s later, which finds the window open or under 50 s away (the window stays open 180 s).
WINDOW_LEAD = 50


def save(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, path)


def window_closed(inv, now=None, lead=WINDOW_LEAD, need=0):
    """True when inventory.json says supply is closed and will not open within lead seconds, or is open but closes before
    need more seconds of supply calls could finish. An open window whose closes_at has passed is closed whatever need is:
    inventory.json is read when the run starts and can still say open after the window ended."""
    r, now = inv.get("restock"), now or time.time()
    if not isinstance(r, dict):
        return False
    if r.get("open") is not False:
        closes = r.get("closes_at")
        return isinstance(closes, (int, float)) and (closes <= now or closes - now < need)
    opens = r.get("opens_at")
    return not isinstance(opens, (int, float)) or opens - now > lead


def fits(sku, x, cfg, median):
    """The opening's rules for a SKU: food with the long shelf life under the food cap, goods under the goods cap, and a
    price within band_low..band_high of the category's median."""
    price = x.get("list_price") or 0
    if x.get("kind") == "food":
        if (x.get("shelf_hours") or 0) < cfg["min_shelf_hours"] or price > cfg["food_max_price"]:
            return False
    elif price > cfg["goods_max_price"]:
        return False
    return not median or cfg["band_low"] <= price / median <= cfg["band_high"]     # no median known: the caps alone


def revise(candidates, rows, catalog, medians, cfg):
    """Phase 2 and 3 candidates after what the ledger showed: dropped, replaced or kept."""
    settles = [r for r in rows if r.get("k") == "settle" and r.get("sku") in catalog]
    if len(settles) < cfg["judge_settles"]:
        return list(candidates), []
    by_cat = collections.defaultdict(collections.Counter)
    for r in settles:
        by_cat[catalog[r["sku"]]["category"]][r["sku"]] += 1
    out, notes = [], []
    for sku in candidates:
        cat = (catalog.get(sku) or {}).get("category")
        sold = by_cat.get(cat)
        if not sold:
            notes.append(f"{sku} dropped: no settle in its category among {len(settles)}")
            continue
        best = next((s for s, _ in sold.most_common() if fits(s, catalog[s], cfg, medians.get(cat))), None)
        if best and best != sku and sold[best] > sold.get(sku, 0):
            notes.append(f"{sku} -> {best}: {sold[best]} settles vs {sold.get(sku, 0)}")
            sku = best
        out.append(sku)
    return out, notes


def lines(md, inv, catalog, demand, rows, cfg, now=None, keep=None):
    """(buy lines for this run, why) while the opening is under way, or (None, why) once it is done or not configured.
    An empty list means: the opening wants this run to buy nothing (window closed, waiting for the next phase).
    keep(lines) -> lines is the caller's guard: only what passes it is sent and counted as sent."""
    now = now or time.time()
    phases = cfg.get("phases") or []
    if not phases:
        return None, "no opening configured"
    path = os.path.join(md, "opening_state.json")
    st = {"started": now, "sent": {}, "spent_worst": 0, "notes": [], "tries": {}, **load(path, {})}
    have = {u.get("sku") for u in inv.get("units") or []}
    last = st["sent"].get(str(len(st["sent"])))
    if last and not last.get("confirmed"):
        if have & set(last["skus"]) or not last["skus"]:
            last["confirmed"] = True
        elif now - last["at"] > RETRY_AFTER:          # nothing arrived: the window was missed (or the market refused)
            n = str(len(st["sent"]))
            st["spent_worst"] -= last.get("worst", 0)
            if st["tries"].get(n, 1) >= MAX_TRIES:
                last.update(confirmed=True, skipped=True, worst=0)
                st["notes"].append(f"phase {n}: nothing arrived after {MAX_TRIES} sends, skipped")
            else:
                st["tries"][n] = st["tries"].get(n, 1) + 1
                st["notes"].append(f"phase {n}: nothing arrived in {RETRY_AFTER // 60} min, sending again")
                del st["sent"][n]
    if len(st["sent"]) >= len(phases):
        return None, "opening done"
    if now - st["started"] > cfg["explore_hours"] * 3600:
        return None, "opening time is over"
    k = len(st["sent"])
    if window_closed(inv, now, need=SECONDS_PER_LINE * len(phases[k]) + 15):
        save(path, st)
        return [], "supply window closed or closing"
    if k and (not st["sent"][str(k)].get("confirmed") or now - st["sent"][str(k)]["at"] < cfg["phase_gap_minutes"] * 60):
        save(path, st)
        return [], f"phase {k + 1} waits for the next window"
    medians = {d.get("category"): d.get("median_price") for d in demand}
    cand = [s for s in phases[k] if s in catalog]
    notes = []
    if k:
        cand, notes = revise(cand, rows, catalog, medians, cfg)
    out, budget = [], cfg["explore_budget_worst"] - st["spent_worst"]
    for sku in dict.fromkeys(cand):
        x = catalog[sku]
        if sku in have or not fits(sku, x, cfg, medians.get(x.get("category"))):
            continue
        worst = x["list_price"] * WORST_COST
        if worst > budget:
            notes.append(f"{sku} left out: exploration budget")
            continue
        out.append({"sku": sku, "qty": 1})
        budget -= worst
    if keep and out:
        out = keep(out)
        budget = cfg["explore_budget_worst"] - st["spent_worst"] - sum(catalog[b["sku"]]["list_price"] * WORST_COST * b["qty"] for b in out)
    worst = cfg["explore_budget_worst"] - st["spent_worst"] - budget
    st["sent"][str(k + 1)] = {"at": now, "skus": [b["sku"] for b in out], "worst": worst, "confirmed": not out}
    st["spent_worst"] += worst
    st["notes"] += notes
    save(path, st)
    if not out:                            # nothing of this phase fits: the usual plan runs instead
        return None, f"opening phase {k + 1}: nothing to buy" + (f" ({'; '.join(notes)})" if notes else "")
    return out, f"opening phase {k + 1}: {len(out)} lines" + (f" ({'; '.join(notes)})" if notes else "")
