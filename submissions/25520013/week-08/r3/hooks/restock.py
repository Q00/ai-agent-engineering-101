"""R3 restock hook: python hooks/restock.py --state {state}. Prints {"buy": [...]} as its last line."""
import argparse
import copy
import json
import math
import os
import sys
import time
from pathlib import Path

from common import P, Recorder, emit, ingest, load_stats, read_json
from ledger import Ledger, state_dir


def market_activity(root=None, tau_h=24.0, now=None, pol=None):
    """v1.7 (c): {"last_settle_ts": newest market settle (any shop) in our ledger, "first_stock_ts": our first
    restock/opening unit, "settles": count}. Incremental: byte cursor in <R3_STATE>/activity.json. Never raises.
    v1.9 (c): + "orders"/"last_order_ts"/"results"/"last_result_ts" (market-wide, from the ledger cursor that
    ingest keeps; any buyer and shop, so they keep moving while we are sold out) and "resume_ts" (mtime of
    <R3_STATE>/restock_resume: `touch` it to restart a paused restock by hand, labpc_swap_checklist 3-1).
    v1.9 (b): "own_sales": {sku: [decayed count, ref ts]} of our shop's settles (tau_h, aggregates' estimator).
    v1.10 (QA review_r3_01ac_v19): our shop's settles are matched with owner.is_own_shop_ref (norm_name over
    R3_SHOP_NAME + policy own_shop_names + cached /api/shops names, norm_url over cached URLs, <market>/shop/<name>
    paths, public keys) instead of `shop == R3_SHOP_NAME`; + "own_settles"/"last_own_settle_ts" (own-shop demand
    signal for idle_ref_scope "own"), "no_stock"/"last_no_stock_ts" (seller `no_stock` rows in metrics.jsonl =
    buyers asking OUR shop for stock we lack, byte cursor "moff"); resume_ts is clamped: a future mtime counts as
    the time it was first seen (kept in "resume_seen"), so a mistaken `touch -d +30days` resumes once, not for 30
    days.
    v1.11 (QA review_r3_01ac_v110 major 1): + "own_accepts"/"last_own_accept_ts" = our seller hook's `accept` rows
    in the same metrics.jsonl (hook "seller", decision "accept"): own demand that does not depend on how the market
    ledger writes the settle `shop` field (name / tunnel URL / path) or on the own_shops.json URL cache."""
    try:
        import owner as OW
        now = time.time() if now is None else now
        L = Ledger(root)
        p = L.root / "activity.json"
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        st.setdefault("off", 0)
        own = st.setdefault("own_sales", {})
        tau_s = float(tau_h) * 3600
        keys = OW.own_shop_keys(pol or P.load(), L.root, extra_names=[L.me_shop])
        for off, e in L.iter_from(st["off"]):
            st["off"] = off
            ts = e.get("ts")
            if not isinstance(ts, (int, float)):
                continue
            if e.get("type") == "settle":
                st["last_settle_ts"] = max(ts, st.get("last_settle_ts") or 0)
                st["settles"] = st.get("settles", 0) + 1
                d = e.get("data") or {}
                if OW.is_own_shop_ref(d.get("shop"), keys):
                    st["own_settles"] = st.get("own_settles", 0) + 1
                    st["last_own_settle_ts"] = max(ts, st.get("last_own_settle_ts") or 0)
                    if d.get("sku"):
                        v, ref = own.get(d["sku"], [0.0, ts])
                        v = v * math.exp(-max(0.0, ts - ref) / tau_s) + 1.0
                        own[d["sku"]] = [round(v, 6), max(ts, ref)]
            elif e.get("type") == "restock" and e.get("mine"):
                st["first_stock_ts"] = min(ts, st.get("first_stock_ts") or ts)
        mf = L.root / "metrics.jsonl"
        try:
            size = mf.stat().st_size
        except OSError:
            size = 0
        if size < st.get("moff", 0):
            st["moff"] = 0                                     # metrics file replaced: read it again
        if size > st.get("moff", 0):
            with open(mf, "rb") as f:
                f.seek(st.get("moff", 0))
                for line in f:
                    if not line.endswith(b"\n"):
                        break                                  # half-written last line: next call
                    st["moff"] = st.get("moff", 0) + len(line)
                    if b'"no_stock"' not in line and b'"accept"' not in line:
                        continue
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(m.get("ts"), (int, float)):
                        continue
                    if m.get("decision") == "no_stock":
                        st["no_stock"] = st.get("no_stock", 0) + 1
                        st["last_no_stock_ts"] = max(m["ts"], st.get("last_no_stock_ts") or 0)
                    elif m.get("decision") == "accept" and m.get("hook") == "seller":
                        # v1.11: our seller hook accepted a deal = a buyer is buying from US (no ledger shop format)
                        st["own_accepts"] = st.get("own_accepts", 0) + 1
                        st["last_own_accept_ts"] = max(m["ts"], st.get("last_own_accept_ts") or 0)
        try:
            md = json.loads(L.cursor_file.read_text(encoding="utf-8")).get("market_demand") or {}
        except (OSError, ValueError):
            md = {}
        for k in ("orders", "last_order_ts", "results", "last_result_ts"):
            st[k] = md.get(k)
        try:
            mt = (L.root / "restock_resume").stat().st_mtime
        except OSError:
            mt = None
        if mt is None:
            st["resume_ts"] = None
            st.pop("resume_seen", None)
        else:
            rs = st.get("resume_seen") or {}
            if rs.get("mtime") != mt:
                rs = {"mtime": mt, "ts": min(mt, now)}         # v1.10: never in the future
            st["resume_seen"] = rs
            st["resume_ts"] = rs["ts"]
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, p)
        return {k: st.get(k) for k in ACTIVITY_KEYS}
    except Exception as ex:                                # noqa: BLE001 - restock must still answer
        return {"error": f"{type(ex).__name__}: {ex}"}


ACTIVITY_KEYS = ("last_settle_ts", "first_stock_ts", "settles", "orders", "last_order_ts", "results",
                 "last_result_ts", "resume_ts", "own_sales", "own_settles", "last_own_settle_ts", "no_stock",
                 "last_no_stock_ts", "own_accepts", "last_own_accept_ts")


def taste_ranking(pol, root=None, now=None):
    """v1.19: the category forecast of <R3_STATE>/taste_gate.json (written by memory.run's taste gate) when policy
    taste.enabled, its state is "adopted" and its asof is at most taste.max_age_s old; else None (velocity ranking).
    Read as a file only: the decision path imports nothing from taste/memory/harness/experiments."""
    t = pol.get("taste") or {}
    if not t.get("enabled"):
        return None
    g = read_json(Path(root or state_dir()) / "taste_gate.json", None)
    if not isinstance(g, dict) or g.get("state") != "adopted":
        return None
    asof, f = g.get("asof"), g.get("forecast")
    now = time.time() if now is None else now
    age = now - asof if isinstance(asof, (int, float)) and not isinstance(asof, bool) else None
    if age is None or not 0 <= age <= float(t.get("max_age_s") or 0):
        return None
    return f if isinstance(f, dict) and f else None


def _plan_without_goods_cap(inv, demand, catalog, pol, stats, act, err):
    """v1.16 safety net: a restock_plan exception must not stop ALL restock (QA v1.14: a goods-cap crash stopped
    food too). Plan again without goods_max_storage_frac (= the checklist 3-0b revert); if that fails as well,
    buy nothing this cycle. Either way the error is a warning (stderr + metrics outcome)."""
    e1 = f"{type(err).__name__}: {str(err)[:150]}"
    try:
        p2 = copy.deepcopy(pol)
        p2["shop"].pop("goods_max_storage_frac", None)
        buy, why = P.restock_plan(inv, demand, catalog, p2, stats, activity=act)
        why = dict(why, warnings=list(why.get("warnings") or []) + [f"restock_plan error, planned without the "
                                                                    f"goods storage cap: {e1}"])
    except Exception as e2:                              # noqa: BLE001
        buy, why = [], {"why": "plan_error",
                        "warnings": [f"restock_plan error: {e1}; retry without goods cap failed: "
                                     f"{type(e2).__name__}: {str(e2)[:150]}"]}
    return buy, why


STORAGE_RATE_EXPECTED = 0.01        # SCHEMA / agent.md: 1 %/day; another accepted value gets an info note (v1.16)


def storage_note(inv):
    """v1.16 (QA v1.15 rec): a storage_per_day storage_rate accepts (no warning) but that is not 0.01 is used as given
    (0.05 -> the cap at 0.05); the unit is unconfirmed, so say so (checklist 3-0b 2 decides). None when 0.01 / missing."""
    v = inv.get("storage_per_day")
    rate, warn = P.storage_rate(v)
    if warn or v is None or rate == STORAGE_RATE_EXPECTED or rate == 0:
        return None
    return f"storage_per_day {P._short(v)} != {STORAGE_RATE_EXPECTED} (unconfirmed unit, used as given; checklist 3-0b 2)"


def main():
    """v1.16 safety net (QA v1.14/v1.15 rec): any exception -> traceback + ERROR line on stderr (the kit log, so the
    3-0b Traceback grep sees it), a restock_error metric if it can be written, and an EMPTY plan on stdout, so the
    kit gets an answer and the next cycle tries again."""
    try:
        _main()
    except SystemExit:
        raise
    except Exception as e:                               # noqa: BLE001
        import traceback
        traceback.print_exc()
        err = f"{type(e).__name__}: {str(e)[:150]}"
        print(f"ERROR: restock: crashed ({err}); empty plan this cycle, checklist 3-0b 1", file=sys.stderr, flush=True)
        try:
            Recorder("restock", policy_hash="?").write("restock_error", 0, outcome={"error": err, "lines": 0, "qty": 0})
        except Exception:                                # noqa: BLE001
            pass
        emit({"buy": []})


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    a = ap.parse_args()
    pol = P.load()
    rec = Recorder("restock", policy_hash=pol["_hash"])
    seq, counts, ims = ingest(a.state)
    inv = read_json(f"{a.state}/inventory.json", {}) or {}
    invp = Path(a.state) / "inventory.json"
    if not inv and invp.exists() and invp.stat().st_size > 2 and read_json(invp, None) is None:
        print("WARNING: restock: inventory.json is not readable JSON: planning with no inventory (no units, no "
              "balance) this cycle, checklist 3-0b 1", file=sys.stderr)
    demand = read_json(f"{a.state}/demand.json", []) or []
    catalog = read_json(f"{a.state}/catalog.json", {}) or {}
    if isinstance(catalog, list):
        catalog = {x["sku"]: x for x in catalog}
    stats, serr = load_stats(catalog)
    act = market_activity(tau_h=float(pol["shop"].get("own_velocity_tau_h") or 24.0), pol=pol)
    taste = taste_ranking(pol)
    try:
        buy, why = P.restock_plan(inv, demand, catalog, pol, stats, activity=act, taste=taste)
    except Exception as e:                               # noqa: BLE001 - v1.16 (QA v1.13 minor 11 / v1.14 rec 3)
        buy, why = _plan_without_goods_cap(inv, demand, catalog, pol, stats, act, e)
    for w in why.get("warnings") or []:                  # v1.14: goods cap off (unreadable storage_per_day)
        print(f"WARNING: restock: {w}", file=sys.stderr)
    note = storage_note(inv)
    if note:
        why = dict(why, storage_note=note)              # own key: restock_plan already has "notes"
        print(f"NOTE: restock: {note}", file=sys.stderr)
    out = {"buy": buy}
    stuck = (inv.get("balance") or 0) < inv.get("bankrupt_line", 0)
    if stuck and pol["shop"].get("allow_bankrupt"):
        out["bankrupt"] = True
    rec.write("restock_plan", seq, outcome={"lines": len(buy), "qty": sum(b["qty"] for b in buy), **why,
                                             "balance": inv.get("balance")}, ingest_ms=ims, ingested=counts,
              extra={"stats": stats is not None, "stats_error": serr,
                     "activity": {k: v for k, v in act.items() if k != "own_sales"},
                     "own_velocity": {k: round(v, 4) for k, v in P.own_velocity(act, catalog, pol).items()},
                     "idle_pause_s": P.idle_pause_s(pol), "taste": taste is not None})
    emit(out)


if __name__ == "__main__":
    main()
