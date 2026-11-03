"""In-round monitor (read only; v1.7 tools/monitor_v17.py, renamed in v1.9). Run from the agent folder (week-08):

    python <r3>/tools/monitor.py [--dir .] [--policy <r3>/policy.json] [--now TS]

Prints balance vs thresholds, inventory at cost (food/goods) vs the policy cap, food by hours to expiry, goods
storage per day, last market settle / demand age vs idle_pause_s, and the last restock_plan outcomes. Exit code
0 = OK, 1 = WATCH, 2 = ACT (see labpc_swap_checklist.md 3-1 for what to do), 3 = the monitor itself crashed
(v1.15: an unexpected exception prints a traceback + "CRASH" and exits 3, never 1, so it cannot read as WATCH).
v1.15 (QA review_r3_01ac_v114 major 1): storage_per_day is read with policy.storage_rate (the restock parser): an
unparsable / percent-like / out-of-range value prints "goods storage/day ? (warning)" and the other checks go on.
v1.9 (QA review_r3_01ac_v17 item 2): WATCH when the newest market settle (any shop) or market demand (orders /
results) is older than idle_pause_s; ACT when the newest restock_plan paused as idle while we have 0 sellable units,
or while demand was seen within idle_pause_s (orders/results/settles, or buyers asking our shop for stock we do not
have: seller `no_stock` rows after the pause). Manual resume: checklist 3-1 (touch .r3/restock_resume).
v1.10 (QA review_r3_01ac_v19 major 3): ACT when OUR shop has had no demand (own settle / no_stock ask / resume;
before the first sale: our first stock; policy.own_demand_ref) for longer than idle_pause_s while we hold sellable
units - other shops' orders do not clear it (the restock gate's "market" scope cannot see this case).
v1.11 (QA review_r3_01ac_v110): own demand also counts our seller's accepts (own_accept); the own-gap message is
ACT only while stock is above low_stock (idle_low_stock_frac x the (a) cap) - at or below it the gate uses the
market rule and does NOT pause, so it is a WATCH that says so; ACT when our seller accepted >= ACCEPT_NO_SETTLE_MIN
deals but no ledger settle matched our shop (settle `shop` format / tunnel URL cache not matched: checklist 3-0,
revert idle_ref_scope=market in the first hour); WATCH when inventory at cost >= 80% of the (a) cap (= the
end-inventory risk ceiling, checklist 3-1a). Ages under 1 h print in minutes.
v1.12 (QA review_r3_01ac_v111 condition 1): the accept/settle check follows checklist 3-0 (decided on own_accepts)
with ONE threshold, ACCEPT_NO_SETTLE_MIN, and only when idle_ref_scope is "own" (scope "market": info line only):
  own_accepts >= MIN, own_settles 0, ledger settles (any shop) 0 -> note "no settle rows in our ledger yet" (keep own);
  own_accepts >= MIN, own_settles 0, ledger settles > 0          -> WATCH settle `shop` mismatch (keep own; record the
                                                                    shop value + own_shops.json urls, checklist 3-0);
  own_settles >= MIN and own_accepts 0                           -> ACT the accept signal is broken (R3_STATE /
                                                                    metrics.jsonl not seen): revert to market (3-0).
M-stage 2 (QA review_r3_01ac_v112 recommendation, CoS-approved 2026-11-02; deploy needs Owner approval): partial
accept loss - own_accepts >= 1 but own_settles >= own_accepts + ACCEPT_NO_SETTLE_MIN -> WATCH (keep own; the own gate
undercounts our demand: some seller accept rows were not read; compare the checklist 3-0 grep count with
activity.json own_accepts). Scope own only, like the rest of the accept/settle rule.
v1.17 (QA review_r3_01ac_v116 finding 3 / review_r3_01ac_v1161: the monitor said OK with restock_error, plan_error
and policy_reload_failed rows): the same METRICS_WINDOW rows are counted by kind and printed on one line
("error signals ..."); any of them -> WATCH; the last ERROR_ACT_CYCLES restock cycles (restock_plan or restock_error
rows, in file order) all failed (restock_error, or restock_plan why plan_error = an empty plan) -> ACT. The count is
over the window, not the whole file (checklist 3-1 grep stays the cumulative check), so a WATCH stays until the rows
leave the last METRICS_WINDOW lines."""

METRICS_WINDOW = 2000          # only the last 2,000 metrics lines are read (older rows / bad lines are not seen)
ACCEPT_NO_SETTLE_MIN = 3        # v1.12: the same number is the checklist 3-0 revert / record threshold
ERROR_ACT_CYCLES = 2            # v1.17: 2 failed restock cycles in a row -> ACT (checklist 3-1 "2주기 이상 연속")
ERROR_KINDS = ("restock_error", "plan_error", "policy_reload_failed")
import argparse
import collections
import json
import sys
import time
from pathlib import Path

R3 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--now", type=float, default=None, help="evaluate at this unix time (tests)")
    a = ap.parse_args()
    import policy as P
    pol = P.load(a.policy)
    sp = pol["shop"]
    d = Path(a.dir)
    inv = json.loads((d / ".market" / "inventory.json").read_text(encoding="utf-8"), parse_int=P.json_int)  # v1.16
    now = a.now or time.time()
    bal = int(inv.get("balance") or 0)
    tot, food, goods = P.inventory_cost(inv)
    cap = int(float(sp.get("max_inventory_cost_frac") or 0) * bal)
    level, msgs = 0, []

    def flag(lv, m):
        nonlocal level
        level = max(level, lv)
        msgs.append(("OK", "WATCH", "ACT")[lv] + " " + m)

    bins = collections.Counter()
    for u in inv.get("units") or []:
        if u.get("kind") == "food" and u.get("expires_at"):
            h = (u["expires_at"] - now) / 3600
            b = "<2h" if h < 2 else "<12h" if h < 12 else "<48h" if h < 48 else ">=48h"
            bins[b] += int(u.get("cost") or 0)
    print(f"balance {bal:,} (floor {sp['balance_floor']:,}, liquidation {sp['liquidation_line']:,}, "
          f"bankrupt {inv.get('bankrupt_line', 0):,})  units {len(inv.get('units') or [])}")
    print(f"inventory at cost {tot:,} = food {food:,} + goods {goods:,}; cap {cap:,} "
          f"(max_inventory_cost_frac {sp.get('max_inventory_cost_frac')})")
    srate, swarn = P.storage_rate(inv.get("storage_per_day"))
    if swarn:
        print(f"food cost by hours to expiry {dict(bins)}; goods storage/day ? ({swarn})")
    else:
        print(f"food cost by hours to expiry {dict(bins)}; goods storage/day ~{int(goods * srate):,}")
    if bal < int(inv.get("bankrupt_line") or 0):
        flag(2, "balance under the bankrupt line: stop the kit, checklist 4 / bankruptcy decision")
    elif bal < sp["liquidation_line"]:
        flag(2, "liquidation mode (restock off, sell at cost+1): confirm, consider stopping restock by hand")
    elif bal < sp["balance_floor"]:
        flag(1, "under balance_floor: restock buys nothing until sales lift the balance")
    if cap and tot > cap * 1.05:
        flag(2, "inventory at cost over the cap: check policy_hash / that the v1.7 restock is the one running")
    elif cap and tot >= cap * 0.8:
        flag(1, f"inventory at cost {tot:,} >= 80% of the (a) cap {cap:,} = end-inventory risk ceiling: if our shop "
                "is not selling, follow checklist 3-1a")
    if bins["<12h"] + bins["<2h"] > 0:
        flag(1, f"food within 12 h (sold at cost+1 by policy): {bins['<12h'] + bins['<2h']:,}")
    if bins["<48h"] + bins["<12h"] + bins["<2h"] > max(100_000, 0.02 * bal):
        flag(2, "food cost within 48 h above max(100k, 2% balance): lower food_sell_through / food_cold_units")
    if swarn:
        flag(1, f"storage_per_day not understood ({swarn}): goods cap is off, follow checklist 3-0b")
    elif goods * srate > 5000:
        flag(1, "goods storage over 5,000/day: check old goods, consider a lower max_inventory_cost_frac")
    act = {}
    try:
        act = json.loads((d / ".r3" / "activity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    ls = act.get("last_settle_ts")
    pause = P.idle_pause_s(pol)
    ref, src = P.demand_ref(act)
    def dur(sec):
        return f"{sec / 3600:.1f} h" if sec >= 3600 else f"{sec / 60:.0f} min"

    age = lambda t: "never" if not t else f"{dur(now - t)} ago"  # noqa: E731
    print(f"last market settle {age(ls)}; newest demand evidence {age(ref)} ({src}); orders seen "
          f"{act.get('orders') or 0}; idle_pause_s {pause:.0f}")
    units = P.sellable_units(inv)
    oref, osrc = P.own_demand_ref(act, now)
    low, _lu, _lc = P.low_stock(inv, pol)
    scope = P.idle_ref_scope(pol)
    print(f"our shop: settles {act.get('own_settles') or 0}, seller accepts {act.get('own_accepts') or 0}, newest own "
          f"demand {age(oref)} ({osrc}); no_stock asks {act.get('no_stock') or 0}; idle_ref_scope {scope}; "
          f"low_stock {'yes' if low else 'no'}")
    acc, own_set, all_set = act.get("own_accepts") or 0, act.get("own_settles") or 0, act.get("settles") or 0
    if scope != "own":
        if acc >= ACCEPT_NO_SETTLE_MIN and not own_set:
            print(f"info: {acc} seller accepts, 0 own settles matched; idle_ref_scope {scope} does not use own demand, "
                  "no action (checklist 3-0 applies to scope own only)")
    elif acc >= ACCEPT_NO_SETTLE_MIN and not own_set and not all_set:
        print(f"note: our seller accepted {acc} deals; no settle rows in our ledger yet (any shop: 0), so 0 own settles "
              "is expected - keep idle_ref_scope own (checklist 3-0)")
    elif acc >= ACCEPT_NO_SETTLE_MIN and not own_set:
        flag(1, f"settle shop mismatch: our seller accepted {acc} deals, the ledger has {all_set} settles (any shop) but "
                "0 matched OUR shop: keep idle_ref_scope own (demand rests on accepts); record the settle `shop` value "
                "and .r3/own_shops.json urls (checklist 3-0)")
    elif own_set >= ACCEPT_NO_SETTLE_MIN and not acc:
        flag(2, f"accept signal broken: {own_set} ledger settles matched OUR shop but 0 seller accept rows "
                "(R3_STATE / .r3/metrics.jsonl not read by restock?): revert idle_ref_scope=market (checklist 3-0)")
    elif acc >= 1 and own_set >= acc + ACCEPT_NO_SETTLE_MIN:
        flag(1, f"partial accept loss: {own_set} ledger settles matched OUR shop but only {acc} seller accept rows "
                f"(>= {ACCEPT_NO_SETTLE_MIN} missing): keep idle_ref_scope own, compare the checklist 3-0 grep count with "
                "activity.json own_accepts (R3_STATE / metrics.jsonl rotation?) and record both")
    if pause > 0 and units > 0 and oref and now - oref > pause:
        what = (f"no demand at OUR shop for {dur(now - oref)} > idle_pause_s ({osrc}) while holding {units} "
                f"sellable units (cost {tot:,})")
        if low:
            flag(1, f"{what}: stock is at/below low_stock (idle_low_stock_frac x cap), so restock follows the market "
                    "rule and does NOT pause; checklist 3-1")
        else:
            flag(2, f"{what}: check our asks vs the market (read only); restock "
                    f"{'pauses by policy' if scope == 'own' else 'does NOT pause (scope market)'}; checklist 3-1, 3-1a")
    if pause > 0 and act.get("settles") and ls and now - ls > pause:
        flag(1, f"no market settle (any shop) for {dur(now - ls)} > idle_pause_s: check the market (read only)")
    if pause > 0 and src == "market_demand" and now - ref > pause:
        flag(1, f"no market order/result for {dur(now - ref)} > idle_pause_s: restock pauses by policy")
    whys = collections.Counter()
    last_plan, no_stock_after = None, 0
    rows, bad = [], 0
    try:
        # v1.16 (QA v1.15.1 minor): utf-8-sig = a first-line BOM is not a bad line; split("\n") not splitlines(),
        # which also cuts at U+2028 / U+2029 / U+0085 inside a JSON string (item names) -> one good row = 2 bad lines
        lines = (d / ".r3" / "metrics.jsonl").read_text(encoding="utf-8-sig", errors="replace").rstrip("\n").split("\n")
        lines = lines[-METRICS_WINDOW:]
    except OSError:
        lines = []
    for x in lines:             # v1.15.1 (QA v115 condition 1): line by line, a bad line never hides the others
        if not x.strip():
            continue
        try:
            m = json.loads(x)
        except (ValueError, RecursionError):      # v1.16: a very deeply nested line is a bad line, not a CRASH
            m = None
        if isinstance(m, dict):
            rows.append(m)
        else:
            bad += 1
    if bad:
        flag(1, f"{bad} unreadable metrics lines (half-written / not a JSON object): skipped, the other rows are "
                "still checked; checklist 3-1 bad-line check")
    def outcome(m):
        o = m.get("outcome")
        return o if isinstance(o, dict) else {}
    for m in rows:
        if m.get("decision") == "restock_plan":
            o = outcome(m)
            w = o.get("why")
            whys[w if isinstance(w, str) and w else f"bought {o.get('qty')}"] += 1
            last_plan, no_stock_after = m, 0
        elif m.get("decision") == "no_stock":
            no_stock_after += 1
    below = sum(1 for m in rows if outcome(m).get("below_cost"))
    if below:
        flag(2, f"{below} below-cost rows in metrics: stop the kit (checklist 4)")
    print(f"recent restock_plan outcomes {dict(whys.most_common(6))}")
    errs, cycles = collections.Counter(), []          # v1.17: error signals are not OK any more
    for m in rows:
        dec = m.get("decision")
        if dec == "restock_error":
            errs["restock_error"] += 1
            cycles.append(True)
        elif dec == "restock_plan":
            failed = outcome(m).get("why") == "plan_error"
            errs["plan_error"] += failed
            cycles.append(failed)
        elif dec == "policy_reload_failed":
            errs["policy_reload_failed"] += 1
    errs = {k: errs[k] for k in ERROR_KINDS if errs[k]}
    print(f"error signals in the last {METRICS_WINDOW} metrics lines {errs}")
    if len(cycles) >= ERROR_ACT_CYCLES and all(cycles[-ERROR_ACT_CYCLES:]):
        flag(2, f"the last {ERROR_ACT_CYCLES} restock cycles failed (restock_error / plan_error = empty plan, "
                f"signals {errs}): record the error text and kit log ERROR: restock lines (checklist 3-1)")
    elif errs:
        flag(1, f"error signals {errs}: restock_error/plan_error = an empty restock plan that cycle, "
                "policy_reload_failed = seller kept its previous policy (checklist 3-1 error-signal list)")
    if last_plan and outcome(last_plan).get("why") == "idle":
        demand_recent = bool(ref) and now - ref <= pause
        if units == 0:
            flag(2, "restock paused (idle) and 0 sellable units: confirm the market has no orders; "
                    "if it does, resume by hand (checklist 3-1)")
        if demand_recent or no_stock_after:
            flag(2, f"restock paused (idle) while demand was seen (demand {age(ref)}, no_stock asks {no_stock_after}): "
                    "resume by hand (checklist 3-1)")
    for m in msgs or ["OK all thresholds"]:
        print(m)
    return level


CRASH_EXIT = 3      # v1.15: distinct from WATCH 1 / ACT 2 (argparse usage errors stay 2 = argparse's own code)


def run():
    try:
        return main()
    except SystemExit:
        raise
    except BaseException:                                # noqa: BLE001 - any crash must not look like WATCH
        import traceback
        traceback.print_exc()
        print("CRASH monitor failed (exit 3): not a WATCH/ACT result - read the traceback, checklist 3-1", flush=True)
        return CRASH_EXIT


if __name__ == "__main__":
    sys.exit(run())
