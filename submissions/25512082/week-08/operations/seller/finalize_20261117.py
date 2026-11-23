"""Round 3 마감 기록 보존 (읽기 전용 수집 → FINAL-20261117/ 아래 새 파일만 작성). 제출물·기존 로그는 수정하지 않는다."""
import json, re, shutil, subprocess, time, urllib.request, datetime as dt
from pathlib import Path
K = dt.timezone(dt.timedelta(hours=9)); kst = lambda t: dt.datetime.fromtimestamp(t, K).strftime("%m-%d %H:%M:%S")
at = lambda h, m, s=0, d=17: dt.datetime(2026, 11, d, h, m, s, tzinfo=K).timestamp()
A = Path("/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08"); M = A / ".market"
O = Path("/home/user/.local/state/ax-round3-ops"); F = O / "FINAL-20261117"; F.mkdir(exist_ok=True)
MK = "https://market.ourolabs.ai"; ME = "sohyunkim11"
KEYS = ("necessary-sciences-pension-arab", "noble-printable-ensuring-contributor", "overseas-acc-beyond-extras", ME, "u-5a9bafc674c7")
def note(**kw):
    with (F / "finalize.log").open("a") as f: f.write(json.dumps({"t": kst(time.time()), **kw}, ensure_ascii=False) + "\n")
def get(p):
    for i in range(6):
        try: return json.load(urllib.request.urlopen(MK + p, timeout=60))
        except Exception as e: err = e; time.sleep(5)
    raise err
def until(t):
    while time.time() < t: time.sleep(min(30, max(1, t - time.time())))
def snap(label):
    try:
        b = get("/api/board?round=3")
        (F / f"board_raw_{label}.json").write_text(json.dumps({k: v for k, v in b.items() if k != "reviews"}, ensure_ascii=False))
        s = next(y for y in b["shops"] if y["name"] == ME); x = next(y for y in b["buyers"] if y["name"] == ME)
        stu = sorted([p for p in b["players"] if p.get("student")], key=lambda p: -p["student_total"]); i = [p["player"] for p in stu].index(ME)
        allp = sorted(b["players"], key=lambda p: -p["total"]); j = [p["player"] for p in allp].index(ME)
        r = {"label": label, "measured_at": kst(time.time()), "round": b.get("round"), "current": b.get("current"),
             "shop": {k: s.get(k) for k in ("alive", "deals", "revenue", "profit", "book", "stars", "reviews", "stars_rank", "refunds", "broken", "stock_units", "stock_cost", "claims", "honest_claims", "all")},
             "buyer": {k: x.get(k) for k in ("orders", "deals", "saved", "forum", "posts", "all")},
             "student_shop": stu[i]["student_shop"], "student_buyer": stu[i]["student_buyer"], "student_total": stu[i]["student_total"],
             "student_rank": i + 1, "students": len(stu), "student_pct": stu[i].get("student_pct"),
             "neighbors": {"above": stu[i - 1]["student_total"] if i else None, "below": stu[i + 1]["student_total"] if i + 1 < len(stu) else None},
             "players_rank": j + 1, "players": len(allp), "players_total": allp[j]["total"]}
    except Exception as e:
        r = {"label": label, "measured_at": kst(time.time()), "error": repr(e)[:300]}
    note(kind="board", **r); return r
note(kind="start")
snaps = {}
until(at(23, 58, 30)); snaps["pre_close_2358"] = snap("pre_close_2358")
# 감시자 종료 확인 (최대 00:00까지)
while time.time() < at(0, 0, 0, 18):
    gl = (O / "sell_guard_2355.log").read_text().splitlines()
    if any('"kind": "end"' in l or '"kind": "STOP"' in l or '"supervisor_exited"' in l for l in gl): break
    time.sleep(10)
ps = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout.splitlines()
left = [l.strip() for l in ps if re.search(r"market_kit\.py|run_market_locked|seller\.py|cloudflared|sell_guard|buyer", l) and "grep" not in l and "finalize" not in l]
note(kind="process_check", remaining=left)
until(at(0, 0, 30, 18)); snaps["post_close_0000"] = snap("post_close_0000")
until(at(0, 5, 0, 18)); snaps["post_close_0005"] = snap("post_close_0005")
# 원장: #7789220 이전 마지막 정산부터 끝까지 우리 가게 항목
cur, mine, n = 7789219, [], 0
try:
    while True:
        e = get(f"/api/ledger?after={cur}"); e = e if isinstance(e, list) else e.get("entries", [])
        if not e: break
        for x in e:
            n += 1
            if any(k in json.dumps(x, ensure_ascii=False) for k in KEYS): mine.append(x)
        cur = e[-1]["seq"]
except Exception as ex: note(kind="ledger_error", err=repr(ex)[:300])
(F / "ledger_ours_after_7789219.json").write_text(json.dumps(mine, ensure_ascii=False, indent=1))
settles = [x for x in mine if x["kind"] == "settle"]
note(kind="ledger", scanned=n, to_seq=cur, ours=len(mine), settles=[(x["seq"], kst(x["ts"]), x["data"].get("unit"), x["data"].get("amount")) for x in settles])
until(at(0, 15, 0, 18)); snaps["post_close_0015"] = snap("post_close_0015")
# 증빙 복사 (원본은 그대로)
for p in [A / "logs/market-kit.log", M / "seller-events.jsonl", M / "inventory.json", M / "seller-ledger.json", M / "shop-only-console.out",
          O / "HANDOFF-20261117-1810.md", O / "HANDOFF-20261117-1810.json", O / "ops15_state.json", O / "ops15.log",
          O / "sell_guard_1917.log", O / "sell_guard_1920.log", O / "sell_guard_2355.log", O / "sell_guard_1917.py", O / "sell_guard_1920.py", O / "sell_guard_2355.py",
          Path("/home/user/.local/state/ax-round3-next/shutdown_1810.log"), Path("/home/user/.local/state/ax-round3-next/launch_ops16.log")]:
    try: shutil.copy2(p, F / ("copy_" + p.name))
    except Exception as e: note(kind="copy_error", file=str(p), err=repr(e)[:200])
inv = json.loads((M / "inventory.json").read_text())
kit = (A / "logs/market-kit.log").read_text(errors="ignore").splitlines()
last_settle = [l for l in kit if "[acp] settled" in l][-1:]
gl = [json.loads(l) for l in (O / "sell_guard_2355.log").read_text().splitlines()]
stops = [g for g in gl if g["kind"] in ("STOP", "end", "supervisor_exited")]
pre, post = snaps.get("pre_close_2358", {}), snaps.get("post_close_0015", {})
diff = {k: (pre.get(k), post.get(k)) for k in ("student_total", "student_rank", "players_rank", "players_total") if pre.get(k) != post.get(k)}
for side in ("shop", "buyer"):
    for k in (pre.get(side) or {}):
        if (pre.get(side) or {}).get(k) != (post.get(side) or {}).get(k): diff[f"{side}.{k}"] = (pre[side][k], (post.get(side) or {}).get(k))
S = {"written_at": kst(time.time()), "snapshots": snaps, "pre_vs_post_close_diff": diff,
     "wallet_balance": inv.get("balance"), "realized_profit": (inv.get("balance") or 0) - (inv.get("start") or 0),
     "unsold_units": [{"unit": u["unit"], "sku": u["sku"], "name": u.get("name"), "cost": u.get("cost"), "expires": kst(u["expires_at"])} for u in inv.get("units", [])],
     "last_settle_kit_line": last_settle[0] if last_settle else None,
     "ledger_settles_since_7789220": [(x["seq"], kst(x["ts"]), x["data"].get("unit"), x["data"].get("amount")) for x in settles],
     "ops15": {"spent": 858966, "units_bought": 95}, "ops16": {"spent": 0, "units_bought": 0}, "new_purchases_after_1900": 0,
     "guard_terminal_records": stops, "safety_stop_1910": "stars_rank 3.0 single reading after re-registration (sell_guard_1917.log)",
     "processes_remaining_at_close": left}
(F / "FINAL_RESULT.json").write_text(json.dumps(S, ensure_ascii=False, indent=1))
note(kind="done")
print(json.dumps(S, ensure_ascii=False)[:3000])
