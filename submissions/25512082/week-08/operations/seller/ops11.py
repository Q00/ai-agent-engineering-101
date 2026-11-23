"""Multi-SKU selective restock run 11-15 03:50~10:00 KST (approved 2026-11-14 ~22:40 KST: <=40 units, <=300,000;
first 2 h <=150,000, rest only if >=3 run settlements, profit > 0 and no safety stop at the 2 h mark).

New run, new state (ops10_state.json / ops10.log); earlier runs, approvals and stop records are not reused.
Limits: <=10 h, <=30 new units, cumulative spend <=150,000, held cost <=100,000, <=1 unit per SKU per
window, never buy a SKU while an unsold unit of it is held. SKUs: buffalo wings, fried rice.

Per window, per SKU (market ledger, right-censoring handled):
- cohort = units of that SKU supplied 3-6 h ago (each has had a full 3 h to sell)
- 3 h sell rate of the cohort >= 70%, market settles in last 3 h >= 20
- realized profit (settle - supply cost of the same unit) median >= 1,000 over settled units, last 6 h
- share of settled units below their supply cost <= 15%
- worst-case cost (list x 0.75 x surge) <= approved unit cap
Normal end (`end`): time over, unit or spend limit reached.
Safety stop (`STOP`, new buys halt, selling continues, never auto-restarted):
- refund / breakage / expiry / settle below cost / duplicate settle / process or lock problem
- official stars_rank < 3.93, >= 2 distinct verified (paid) reviews <= 2 stars
- no settlement for 2 h measured from max(last settlement, arrival of the OLDEST unsold run unit)
- 2 h checkpoint: at START+2h, 0 settlements of run units while run units are unsold
Supply reduction: >= 3 run units unsold for > 120 min -> at most one SKU per window.
safe_restock.py (unchanged) re-validates and claims each single-use approval.
"""
import collections, json, math, os, statistics, subprocess, sys, time, urllib.request, datetime as dt
from pathlib import Path

AGENT = Path("/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08")
MD = AGENT / ".market"
HERE = Path(__file__).parent
STATE, LOG = HERE / "ops11_state.json", HERE / "ops11.log"
KST = dt.timezone(dt.timedelta(hours=9))
ROUND_END = dt.datetime(2026, 11, 17, 23, 59, tzinfo=KST).timestamp()
SKUS = {"G2560a8c9": 13425, "G2108d9bc": 14175, "G1831f2c0": 9675, "G0a5f780c": 13425}
END_AT = dt.datetime(2026, 11, 15, 10, 0, tzinfo=KST).timestamp()
DURATION, MAX_UNITS, TOTAL_CAP, HELD_CAP = 12 * 3600, 40, 300000, 100000  # END_AT (10:00) always governs
PHASE1_CAP, PHASE1_S, UNLOCK_MIN_SETTLES = 150000, 7200, 3
MIN_SELL_RATE, MIN_PROFIT, MAX_BELOW_COST, MIN_SETTLES_3H, MIN_COHORT = 0.70, 1000, 0.15, 20, 15
STARS_FLOOR = 3.93
NO_SETTLE_S, CHECKPOINT_S, SLOW_S = 7200, 7200, 7200
SHOP_URL = "https://necessary-sciences-pension-arab.trycloudflare.com"
MARKET = "https://market.ourolabs.ai"


def kst(t): return dt.datetime.fromtimestamp(t, KST).strftime("%m-%d %H:%M:%S")
def log(kind, **kw):
    kw.update(t=kst(time.time()), kind=kind)
    with LOG.open("a") as f: f.write(json.dumps(kw, ensure_ascii=False) + "\n")
def get(path):
    for i in range(4):
        try: return json.load(urllib.request.urlopen(MARKET + path, timeout=30))
        except Exception:
            if i == 3: raise
            time.sleep(2)
def save(st): STATE.write_text(json.dumps(st, indent=1, ensure_ascii=False))


# ---------------- pure decision logic (unit-tested) ----------------

def run_units(st):
    return {u: p for w in st["windows"] for p in w.get("purchases", []) for u in p["units"]}


def realized(st):
    units = run_units(st); out = []
    for s in st["settles"]:
        p = units.get(s["unit"])
        if p: out.append({**s, "cost": p["unit_cost"], "profit": s["amount"] - p["unit_cost"],
                          "minutes": round((s["ts"] - p["supplied_ts"]) / 60, 1)})
    return out


def stop_reasons(st, bm, held_ids, now):
    r = []
    if bm["refunds"] > st["base"]["refunds"] or bm["broken"] > st["base"]["broken"]: r.append("refund/broken increased")
    if st["incidents"]: r.append(f"incident {st['incidents'][-1]['kind']}")
    if any(x["profit"] < 0 for x in realized(st)): r.append("settled below cost")
    seen = set(); dup = [s["unit"] for s in st["settles"] if s["unit"] in seen or seen.add(s["unit"])]
    if dup: r.append(f"duplicate settle {dup}")
    if bm["stars_rank"] < STARS_FLOOR: r.append(f"official stars_rank {bm['stars_rank']} < {STARS_FLOOR}")
    low = {x["buyer"] for x in st["results"] if x.get("verified") is True and (x.get("stars") or 5) <= 2}
    if len(low) >= 2: r.append(f"verified low-star accounts {sorted(low)}")
    unsold = {u: p for u, p in run_units(st).items() if u in held_ids}
    if unsold:
        oldest = min(p["supplied_ts"] for p in unsold.values())
        last_settle = max([s["ts"] for s in st["settles"]] or [0])
        base = max(last_settle, oldest)
        if now - base >= NO_SETTLE_S:
            r.append(f"no settlement for 2 h since {kst(base)} while run units stay unsold")
        pre = [p for p in unsold.values() if p["supplied_ts"] < st["start"] + CHECKPOINT_S]
        if now - st["start"] >= CHECKPOINT_S and pre and not realized(st):
            r.append("2 h checkpoint: 0 settlements of run units while units bought in the first 2 h stay unsold")
    return r


def budget_cap(st, now):
    """First 2 h: 150,000. Afterwards the full 300,000 only if, at the 2 h mark, run settlements >= 3,
    realized profit > 0 and no safety stop; the decision is taken once and never reversed upward."""
    if now - st["start"] < PHASE1_S: return PHASE1_CAP
    if st.get("unlocked") is None:
        r = realized(st)
        st["unlocked"] = bool(len(r) >= UNLOCK_MIN_SETTLES and sum(x["profit"] for x in r) > 0 and not st["stopped"])
        st["unlock_decision"] = {"settles": len(r), "profit": sum(x["profit"] for x in r), "stopped": st["stopped"]}
    return TOTAL_CAP if st["unlocked"] else PHASE1_CAP


def slow_units(st, held_ids, now):
    return [u for u, p in run_units(st).items() if u in held_ids and now - p["supplied_ts"] > SLOW_S]


def sku_stats(sku, mkt, now):
    """mkt: {"sup": {unit: [sku, cost, ts]}, "set": {unit: [amount, ts]}, "settle_ts": {sku: [ts...]}}"""
    cohort = [(u, c, ts) for u, (s, c, ts) in mkt["sup"].items() if s == sku and now - 6 * 3600 <= ts <= now - 3 * 3600]
    if len(cohort) < MIN_COHORT: return None, f"cohort too small ({len(cohort)})"
    sold3 = [1 for u, c, ts in cohort if u in mkt["set"] and mkt["set"][u][1] - ts <= 3 * 3600]
    rate = len(sold3) / len(cohort)
    settled = [(mkt["set"][u][0], c) for u, (s, c, ts) in mkt["sup"].items() if s == sku and u in mkt["set"]
               and now - 6 * 3600 <= mkt["set"][u][1] <= now]
    if len(settled) < 10: return None, f"too few settled units with known cost ({len(settled)})"
    profit = statistics.median(a - c for a, c in settled)
    below = sum(a < c for a, c in settled) / len(settled)
    n3 = sum(1 for t in mkt["settle_ts"].get(sku, []) if now - t <= 3 * 3600)
    info = {"cohort": len(cohort), "sell_rate_3h": round(rate, 3), "median_profit": int(profit),
            "below_cost": round(below, 3), "settles_3h": n3}
    if rate < MIN_SELL_RATE: return None, f"sell rate {rate:.0%} < 70% {info}"
    if profit < MIN_PROFIT: return None, f"median profit {int(profit)} < 1000 {info}"
    if below > MAX_BELOW_COST: return None, f"below-cost {below:.0%} > 15% {info}"
    if n3 < MIN_SETTLES_3H: return None, f"settles in 3 h {n3} < 20 {info}"
    return info, None


# ---------------- I/O ----------------

def board_me():
    b = get("/api/board?round=3")
    s = next(x for x in b["shops"] if x["name"] == "sohyunkim11")
    bu = next(x for x in b["buyers"] if x["name"] == "sohyunkim11")
    pl = sorted([p for p in b["players"] if p.get("student")], key=lambda p: -p["student_total"])
    i = [p["player"] for p in pl].index("sohyunkim11")
    return {"current": b.get("current"), "alive": s["alive"], "deals": s["deals"], "book": s["book"],
            "revenue": s["revenue"], "stars": s["stars"], "reviews": s["reviews"], "stars_rank": s["stars_rank"],
            "refunds": s["refunds"], "broken": s["broken"], "stock_cost": s["stock_cost"], "forum": bu.get("forum"),
            "posts": bu.get("posts"), "student_total": pl[i]["student_total"], "rank": i + 1, "of": len(pl),
            "t44": pl[43]["student_total"], "t43": pl[42]["student_total"]}


MKT = {"sup": {}, "set": {}, "settle_ts": collections.defaultdict(list)}


def poll_ledger(st, backfill_s=0):
    cur = st.get("cursor")
    if cur is None:
        top = get("/api/ledger?after=-1")[-1]["seq"]
        lo, hi = top - 400000, top
        target = time.time() - backfill_s
        while hi - lo > 500:
            m = (lo + hi) // 2; e = get(f"/api/ledger?after={m}"); e = e if isinstance(e, list) else e.get("entries", [])
            if e and e[0]["ts"] < target: lo = m
            else: hi = m
        cur = lo
    for _ in range(2000):
        e = get(f"/api/ledger?after={cur}")
        e = e if isinstance(e, list) else e.get("entries", [])
        if not e: break
        for x in e:
            d = x.get("data") or {}; k = x["kind"]
            if k == "supply" and d.get("sku") in SKUS:
                for u in d.get("units", []): MKT["sup"][u] = [d["sku"], d["total"] / max(1, d["qty"]), x["ts"]]
            if k == "settle" and d.get("sku") in SKUS:
                MKT["set"][d.get("unit")] = [d.get("amount"), x["ts"]]; MKT["settle_ts"][d["sku"]].append(x["ts"])
            if d.get("shop") not in (SHOP_URL, "sohyunkim11"): continue
            if x["ts"] < st["start"] - 600 and k != "settle": continue
            if k == "settle":
                st["settles"].append({"seq": x["seq"], "ts": x["ts"], "unit": d.get("unit"), "sku": d.get("sku"),
                                      "amount": d.get("amount"), "buyer": d.get("buyer"), "grade": d.get("grade")})
            elif k == "result":
                st["results"].append({"seq": x["seq"], "ts": x["ts"], "buyer": d.get("buyer"), "stars": d.get("stars"),
                                      "verified": d.get("verified"), "review": (d.get("review") or "")[:200]})
            elif k == "supply":
                st["supplies"].append({"seq": x["seq"], "ts": x["ts"], "sku": d.get("sku"), "qty": d.get("qty"),
                                       "total": d.get("total"), "units": d.get("units")})
            elif k in ("broken", "refund", "expire"):
                st["incidents"].append({"seq": x["seq"], "kind": k, "data": d})
        cur = e[-1]["seq"]
        if len(e) < 500: break
    st["cursor"] = cur
    cut = time.time() - 7 * 3600
    for u in [u for u, v in MKT["sup"].items() if v[2] < cut]: MKT["sup"].pop(u, None); MKT["set"].pop(u, None)
    for s in MKT["settle_ts"]: MKT["settle_ts"][s] = [t for t in MKT["settle_ts"][s] if t >= cut]


def procs_ok():
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout.splitlines()
    cnt = {k: sum(1 for l in ps if k in l and "bash -c" not in l and "ops11" not in l)
           for k in ("scripts/market_kit.py join", "python seller.py", "cloudflared tunnel")}
    locked = subprocess.run(["flock", "-n", str(MD / "market-kit.lock"), "true"]).returncode != 0
    return cnt, locked


def gate(st, inv, cat, bm, now):
    fails = []
    cnt, locked = procs_ok()
    if any(v != 1 for v in cnt.values()): fails.append(f"process count {cnt}")
    if not locked: fails.append("kit lock not held")
    if bm["current"] != 3 or not bm["alive"]: fails.append("round/alive")
    if now - (MD / "inventory.json").stat().st_mtime > 60: fails.append("inventory stale")
    for n in ("restock-approval.json", "restock-approval.claimed.json"):
        if (AGENT / n).exists(): fails.append(f"{n} present")
    units = inv.get("units", [])
    if any(type(u.get("cost")) is not int for u in units): fails.append("unit cost unknown")
    held = sum(u["cost"] for u in units if type(u.get("cost")) is int)
    held_skus = {u.get("sku") for u in units}; held_ids = {u.get("unit") for u in units}
    fac = {x.get("sku"): float(x.get("factor", 1.0)) for x in inv.get("surge", [])}
    cap = min(HELD_CAP - held, budget_cap(st, now) - st["spent"], 100000)
    max_lines = 1 if len(slow_units(st, held_ids, now)) >= 3 else len(SKUS)
    lines, worst, skipped, econ = [], 0, {}, {}
    for sku, ucap in SKUS.items():
        lp = cat[sku]["list_price"]; f = fac.get(sku, 1.0); w = math.ceil(lp * 0.75 * f)
        if sku in held_skus: skipped[sku] = "unsold unit held"; continue
        if w > ucap: skipped[sku] = f"worst {w} > {ucap} (surge {f})"; continue
        info, why = sku_stats(sku, MKT, now); econ[sku] = info or why
        if why: skipped[sku] = why; continue
        if worst + w > cap: skipped[sku] = f"cap {cap}"; continue
        if len(lines) >= min(max_lines, MAX_UNITS - st["units_bought"]): skipped[sku] = "unit limit / supply reduced"; continue
        lines.append({"sku": sku, "qty": 1, "max_unit_cost": ucap}); worst += w
    if held + worst > HELD_CAP: fails.append("held cap")
    if not lines: fails.append(f"no eligible SKU {skipped}")
    return fails, {"held": held, "cap": cap, "lines": lines, "worst": worst, "skipped": skipped, "econ": econ}


def write_once(path, obj):
    if path.exists(): path = path.with_name(path.stem + "-b.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.write(fd, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode()); os.close(fd)


def reconcile(st, w, inv):
    claimed, pending = AGENT / "restock-approval.claimed.json", AGENT / "restock-approval.json"
    stamp = dt.datetime.fromtimestamp(w["opens_at"], KST).strftime("%Y%m%dT%H%M")
    sup = [s for s in st["supplies"] if w["opens_at"] - 5 <= s["ts"] <= w["closes_at"] + 120]
    units = {u["unit"]: u for u in inv.get("units", [])}
    purchases = []
    for s in sup:
        u0 = units.get(s["units"][0], {})
        purchases.append({"sku": s["sku"], "qty": s["qty"], "actual_cost": s["total"], "unit_cost": s["total"] // max(1, s["qty"]),
                          "units": s["units"], "grade": u0.get("grade"), "market_ledger_supply": s["seq"], "supplied_ts": s["ts"],
                          "expires_at_kst": kst(u0["expires_at"]) if u0.get("expires_at") else None})
    bad = [p for p in purchases if p["sku"] not in {l["sku"] for l in w["approval"]["lines"]} or p["unit_cost"] > SKUS.get(p["sku"], 0)]
    note = f"Recorded {kst(time.time())} KST: 03:50-10:00 multi-SKU run (ops11) window {len(st['windows'])}, single-use, not reused."
    if claimed.exists():
        write_once(AGENT / f"restock-approval.consumed-{stamp}.json",
                   {"status": "consumed", "round": 3, "approval": json.loads(claimed.read_text()),
                    "window": [int(w["opens_at"]), int(w["closes_at"])], "purchases": purchases,
                    "total_actual_cost": sum(p["actual_cost"] for p in purchases), "note": note})
        claimed.unlink()
    elif pending.exists():
        write_once(AGENT / f"restock-approval.unused-{stamp}.json",
                   {"status": "unused_expired", "approval": json.loads(pending.read_text()),
                    "window": [int(w["opens_at"]), int(w["closes_at"])], "note": "not claimed before close; not reused"})
        pending.unlink()
    w["purchases"] = purchases; w["reconciled"] = True
    st["spent"] += sum(p["actual_cost"] for p in purchases)
    st["units_bought"] += sum(p["qty"] for p in purchases)
    return bad


def main(start, dry=False):
    if STATE.exists() and not dry: raise SystemExit("ops11 state exists: a finished or stopped run is never reused")
    st = {"start": start, "end": min(start + DURATION, END_AT), "skus": SKUS, "windows": [], "spent": 0, "units_bought": 0,
          "stopped": None, "settles": [], "results": [], "supplies": [], "incidents": [], "skipped_windows": [],
          "snapshots": [], "base": None}
    poll_ledger(st, backfill_s=6.5 * 3600)
    cat = json.loads((MD / "catalog.json").read_text())
    if dry:
        inv = json.loads((MD / "inventory.json").read_text()); bm = board_me()
        print(json.dumps({"board": bm, "gate": gate(st, inv, cat, bm, time.time()), "end": kst(st["end"])}, ensure_ascii=False, indent=1)); return
    st["base"] = board_me(); log("start", start=kst(start), end=kst(st["end"]), skus=SKUS, base=st["base"]); save(st)
    last_board, last_rep = 0, start
    while True:
        now = time.time()
        try:
            poll_ledger(st)
            inv = json.loads((MD / "inventory.json").read_text())
            held_ids = {u.get("unit") for u in inv.get("units", [])}
            r = inv.get("restock") or {}; opens, closes = r.get("opens_at"), r.get("closes_at")
            for w in st["windows"]:
                if not w.get("reconciled") and now > w["closes_at"] + 60:
                    bad = reconcile(st, w, inv)
                    log("reconciled", window=len(st["windows"]), purchases=w["purchases"], spent=st["spent"],
                        units=st["units_bought"], realized=realized(st))
                    if bad and not st["stopped"]: st["stopped"] = f"purchase outside approval {bad}"; log("STOP", reasons=[st["stopped"]])
            if now - last_board > 120:
                bm = board_me(); last_board = now
                reasons = stop_reasons(st, bm, held_ids, now)
                if reasons and not st["stopped"]:
                    st["stopped"] = "; ".join(reasons); log("STOP", reasons=reasons, board=bm)
                if now - last_rep >= 7200:
                    last_rep = now; snap = {"hours": round((now - start) / 3600, 1), **bm, "spent": st["spent"],
                                             "units_bought": st["units_bought"], "realized": realized(st),
                                             "unsold": [u for u in run_units(st) if u in held_ids]}
                    st["snapshots"].append(snap); log("report", **snap)
            pending = any(not w.get("reconciled") for w in st["windows"])
            normal_done = now >= st["end"] or now >= ROUND_END or st["units_bought"] >= MAX_UNITS or st["spent"] >= TOTAL_CAP
            if (st["stopped"] or normal_done) and not pending:
                bm = board_me(); st["final"] = bm
                if st["stopped"]: log("stopped_final", reason=st["stopped"], board=bm, spent=st["spent"], realized=realized(st))
                else: log("end", reason="time" if now >= st["end"] else "limit", board=bm, spent=st["spent"], realized=realized(st))
                save(st); return
            if (not st["stopped"] and not normal_done and isinstance(opens, (int, float)) and opens >= start
                    and closes <= st["end"] + 180 and 0 < opens - now <= 75 and not pending
                    and opens not in [w["opens_at"] for w in st["windows"]] and opens not in st["skipped_windows"]):
                bm = board_me(); last_board = time.time()
                fails, info = gate(st, inv, cat, bm, now)
                if fails:
                    st["skipped_windows"].append(opens); log("skip", window=kst(opens), why=fails, info=info)
                else:
                    appr = {"enabled": True, "round": 3, "expires_at": int(min(closes, st["end"])),
                            "purchase_cap": int(info["cap"]), "allow_food": True, "lines": info["lines"]}
                    tmp = AGENT / f".restock-approval.{os.getpid()}.tmp"
                    tmp.write_text(json.dumps(appr, indent=2))
                    os.link(tmp, AGENT / "restock-approval.json"); tmp.unlink()
                    st["windows"].append({"opens_at": opens, "closes_at": closes, "approval": appr, "gate": info,
                                          "written": kst(time.time())})
                    log("approval_written", window=kst(opens), n=len(st["windows"]), info=info)
            save(st)
        except Exception as e:
            log("error", err=repr(e)[:300]); save(st)
        time.sleep(15)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else int(time.time()), dry="--dry-run" in sys.argv)
