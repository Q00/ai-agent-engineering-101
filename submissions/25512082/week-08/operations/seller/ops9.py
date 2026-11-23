"""Six-hour selective restock run #2 (approved 2026-11-14 ~04:45 KST: 6 h, revised stop rule, 2-hourly reports).

New run, new state (ops8_state.json / ops8.log); earlier runs and approvals are not reused.
Policy:
- 6 h from START, at most 12 new units, cumulative spend <=120,000, held cost <=100,000
- per window at most 1 unit per SKU; never buy a SKU while an unsold unit of it is held
- per window, per SKU, from the market's own settles of the last 90 min:
  grade-weighted expected profit >= 1,000 and, for every grade, the market median >= that grade's
  mean cost x 1.05 (sellable above our floor); otherwise the SKU is skipped this window
- the grade and cost are drawn at arrival, so profit is never assumed: it is measured after sale
- stop new buys: refund / breakage / expiry / settle below cost / duplicate settle / process or lock
  problem / official stars_rank < 3.93 / >=2 distinct verified reviews <=2 stars /
  2+ run units unsold for over 90 min, or no settlement for 2 h while run units stay unsold
- hourly snapshot of board metrics in the log
safe_restock.py (unchanged) re-validates and claims every single-use approval.
"""
import collections, json, math, os, statistics, subprocess, sys, time, urllib.request, datetime as dt
from pathlib import Path

AGENT = Path("/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08")
MD = AGENT / ".market"
HERE = Path(__file__).parent
STATE, LOG = HERE / "ops9_state.json", HERE / "ops9.log"
KST = dt.timezone(dt.timedelta(hours=9))
START = int(sys.argv[1]) if len(sys.argv) > 1 else int(time.time())
END = START + 6 * 3600
ROUND_END = dt.datetime(2026, 11, 17, 23, 59, tzinfo=KST).timestamp()
SKUS = {"G2108d9bc": 14175}                      # fried rice (main)
if "--with-latte" in sys.argv: SKUS["G772009ce"] = 18375
MAX_UNITS, TOTAL_CAP, HELD_CAP, MIN_EV = 12, 120000, 100000, 1000
STARS_FLOOR, WINDOW_MED_S = 3.93, 5400
GRADES = {"A": (.25, .55, .75), "B": (.50, .40, .60), "C": (.25, .25, .45)}
SHOP_URL = "https://necessary-sciences-pension-arab.trycloudflare.com"
MARKET = "https://market.ourolabs.ai"


def kst(t): return dt.datetime.fromtimestamp(t, KST).strftime("%m-%d %H:%M:%S")
def log(kind, **kw):
    kw.update(t=kst(time.time()), kind=kind)
    with LOG.open("a") as f: f.write(json.dumps(kw, ensure_ascii=False) + "\n")
def get(path):
    for i in range(3):
        try: return json.load(urllib.request.urlopen(MARKET + path, timeout=30))
        except Exception:
            if i == 2: raise
            time.sleep(2)
def save(st): STATE.write_text(json.dumps(st, indent=1, ensure_ascii=False))


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


MKT = collections.deque()                         # market settles (ts, sku, grade, amount) for medians


def poll_ledger(st, backfill=False):
    cur = st.get("cursor")
    if cur is None:
        top = get("/api/ledger?after=-1")[-1]["seq"]
        cur = top - (30000 if backfill else 1500)
    for _ in range(120):
        e = get(f"/api/ledger?after={cur}")
        e = e if isinstance(e, list) else e.get("entries", [])
        if not e: break
        for x in e:
            d = x.get("data") or {}; k = x["kind"]
            if k == "settle" and d.get("sku") in SKUS:
                MKT.append((x["ts"], d["sku"], d.get("grade"), d.get("amount")))
            if d.get("shop") not in (SHOP_URL, "sohyunkim11"): continue
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
    while MKT and MKT[0][0] < time.time() - WINDOW_MED_S: MKT.popleft()


def sku_economics(sku, lp, factor):
    by = collections.defaultdict(list)
    for ts, s, g, a in MKT:
        if s == sku: by[g].append(a)
    allv = [a for v in by.values() for a in v]
    if len(allv) < 10: return None, f"too few market settles ({len(allv)})"
    ev, detail = 0.0, {}
    for g, (p, lo, hi) in GRADES.items():
        med = statistics.median(by[g]) if len(by[g]) >= 3 else statistics.median(allv)
        mean_cost = lp * (lo + hi) / 2 * factor
        detail[g] = {"median": int(med), "n": len(by[g]), "mean_cost": int(mean_cost), "profit": int(med - mean_cost)}
        if med < mean_cost * 1.05: return None, f"grade {g} median {int(med)} below floor of mean cost {int(mean_cost)}"
        ev += p * (med - mean_cost)
    if ev < MIN_EV: return None, f"expected profit {int(ev)} < {MIN_EV}"
    return int(ev), detail


def procs_ok():
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout.splitlines()
    cnt = {k: sum(1 for l in ps if k in l and "bash -c" not in l and "ops9" not in l)
           for k in ("scripts/market_kit.py join", "python seller.py", "cloudflared tunnel")}
    locked = subprocess.run(["flock", "-n", str(MD / "market-kit.lock"), "true"]).returncode != 0
    return cnt, locked


def run_units(st):
    return {u: p for w in st["windows"] for p in w.get("purchases", []) for u in p["units"]}


def realized(st):
    units = run_units(st); out = []
    for s in st["settles"]:
        p = units.get(s["unit"])
        if p: out.append({**s, "cost": p["unit_cost"], "profit": s["amount"] - p["unit_cost"],
                          "minutes": round((s["ts"] - p["supplied_ts"]) / 60, 1)})
    return out


def stop_reasons(st, bm, held_ids):
    r = []
    if bm["refunds"] > st["base"]["refunds"] or bm["broken"] > st["base"]["broken"]: r.append("refund/broken increased")
    if st["incidents"]: r.append(f"incident {st['incidents'][-1]['kind']}")
    if any(x["profit"] < 0 for x in realized(st)): r.append("settled below cost")
    seen = set(); dup = [s["unit"] for s in st["settles"] if s["unit"] in seen or seen.add(s["unit"])]
    if dup: r.append(f"duplicate settle {dup}")
    if bm["stars_rank"] < STARS_FLOOR: r.append(f"official stars_rank {bm['stars_rank']} < {STARS_FLOOR}")
    low = {x["buyer"] for x in st["results"] if x.get("verified") is True and (x.get("stars") or 5) <= 2}
    if len(low) >= 2: r.append(f"verified low-star accounts {sorted(low)}")
    unsold = [u for u, p in run_units(st).items() if u in held_ids]
    slow = [u for u, p in run_units(st).items() if u in held_ids and time.time() - p["supplied_ts"] > 90 * 60]
    if len(slow) >= 2: r.append(f"2+ run units unsold for over 90 min {slow}")
    last_settle = max([s["ts"] for s in st["settles"]] or [START])
    if unsold and time.time() - max(last_settle, START) >= 7200:
        r.append("no settlement for 2 h while run units stay unsold")
    return r


def gate(st, inv, cat, bm):
    fails = []
    cnt, locked = procs_ok()
    if any(v != 1 for v in cnt.values()): fails.append(f"process count {cnt}")
    if not locked: fails.append("kit lock not held")
    if bm["current"] != 3 or not bm["alive"]: fails.append("round/alive")
    if time.time() - (MD / "inventory.json").stat().st_mtime > 60: fails.append("inventory stale")
    for n in ("restock-approval.json", "restock-approval.claimed.json"):
        if (AGENT / n).exists(): fails.append(f"{n} present")
    units = inv.get("units", [])
    if any(type(u.get("cost")) is not int for u in units): fails.append("unit cost unknown")
    held = sum(u["cost"] for u in units if type(u.get("cost")) is int)
    held_skus = {u.get("sku") for u in units}
    fac = {x.get("sku"): float(x.get("factor", 1.0)) for x in inv.get("surge", [])}
    cap = min(HELD_CAP - held, TOTAL_CAP - st["spent"])
    left_units = MAX_UNITS - st["units_bought"]
    lines, worst, skipped, econ = [], 0, {}, {}
    for sku, ucap in SKUS.items():
        p = cat[sku]; lp = p["list_price"]; f = fac.get(sku, 1.0); w = math.ceil(lp * 0.75 * f)
        if sku in held_skus: skipped[sku] = "unsold unit held"; continue
        if w > ucap: skipped[sku] = f"worst {w} > {ucap} (surge {f})"; continue
        ev, detail = sku_economics(sku, lp, f); econ[sku] = detail
        if ev is None: skipped[sku] = detail; continue
        if worst + w > cap: skipped[sku] = f"cap {cap}"; continue
        if len(lines) >= left_units: skipped[sku] = "12-unit limit"; continue
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
    note = f"Recorded {kst(time.time())} KST: six-hour selective run #2 (ops9) window {len(st['windows'])}, single-use, not reused."
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


def main(dry=False):
    st = json.loads(STATE.read_text()) if STATE.exists() and not dry else None
    if st is None:
        st = {"start": START, "end": END, "skus": list(SKUS), "windows": [], "spent": 0, "units_bought": 0, "stopped": None,
              "settles": [], "results": [], "supplies": [], "incidents": [], "skipped_windows": [], "seen_closes": [],
              "snapshots": [], "base": None}
    poll_ledger(st, backfill=True)
    cat = json.loads((MD / "catalog.json").read_text())
    if dry:
        inv = json.loads((MD / "inventory.json").read_text()); bm = board_me()
        print(json.dumps({"board": bm, "gate": gate(st, inv, cat, bm), "skus": SKUS, "end": kst(END)}, ensure_ascii=False, indent=1)); return
    if st["base"] is None:
        st["base"] = board_me(); log("start", start=kst(START), end=kst(END), skus=SKUS, base=st["base"]); save(st)
    last_board, last_hour = 0, START
    while True:
        now = time.time()
        try:
            poll_ledger(st)
            inv = json.loads((MD / "inventory.json").read_text())
            held_ids = {u.get("unit") for u in inv.get("units", [])}
            r = inv.get("restock") or {}
            opens, closes = r.get("opens_at"), r.get("closes_at")
            if isinstance(closes, (int, float)) and opens >= START and closes not in st["seen_closes"]:
                st["seen_closes"].append(closes)
            for w in st["windows"]:
                if not w.get("reconciled") and now > w["closes_at"] + 60:
                    bad = reconcile(st, w, inv)
                    log("reconciled", window=len(st["windows"]), purchases=w["purchases"], spent=st["spent"],
                        units=st["units_bought"], realized=realized(st))
                    if bad and not st["stopped"]: st["stopped"] = f"purchase outside approval {bad}"
            if now - last_board > 120:
                bm = board_me(); last_board = now
                reasons = stop_reasons(st, bm, held_ids)
                if reasons and not st["stopped"]:
                    st["stopped"] = "; ".join(reasons); log("STOP", reasons=reasons, board=bm)
                if now - last_hour >= 3600:
                    last_hour = now; snap = {"hour": round((now - START) / 3600, 1), **bm, "spent": st["spent"],
                                              "units_bought": st["units_bought"], "realized": realized(st),
                                              "unsold": [u for u in run_units(st) if u in held_ids]}
                    st["snapshots"].append(snap); log("hourly", **snap)
            pending = any(not w.get("reconciled") for w in st["windows"])
            done = st["stopped"] or now >= END or now >= ROUND_END or st["units_bought"] >= MAX_UNITS
            if done and not pending:
                bm = board_me(); st["final"] = bm
                log("end", reason=st["stopped"] or ("12 units" if st["units_bought"] >= MAX_UNITS else "time"),
                    board=bm, spent=st["spent"], realized=realized(st)); save(st); return
            if (not done and isinstance(opens, (int, float)) and opens >= START and closes <= END + 180
                    and 0 < opens - now <= 75 and opens not in [w["opens_at"] for w in st["windows"]]
                    and opens not in st["skipped_windows"] and not pending):
                bm = board_me(); last_board = time.time()
                fails, info = gate(st, inv, cat, bm)
                if fails:
                    st["skipped_windows"].append(opens); log("skip", window=kst(opens), why=fails, info=info)
                else:
                    appr = {"enabled": True, "round": 3, "expires_at": int(min(closes, END)),
                            "purchase_cap": int(min(info["cap"], 100000)), "allow_food": True, "lines": info["lines"]}
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
    main(dry="--dry-run" in sys.argv)
