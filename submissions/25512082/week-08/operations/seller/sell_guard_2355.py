"""11-17 19:2x 재개용 (사용자 승인: 22:00까지 판매 연장, stars_rank 2회 연속 확인) → 19:2x 추가 승인으로 23:55까지 연장, 조건 동일, 신규 매입 금지 Seller 감시자 (판매 전용, 신규 매입 금지). 23:55 KST 또는 안전 조건 위반 시 키트를 SIGTERM.
기록은 sell_guard_2355.log에만 추가한다. 제출물 파일은 읽기만 한다."""
import json, os, signal, subprocess, sys, time, urllib.request, datetime as dt
from pathlib import Path
K = dt.timezone(dt.timedelta(hours=9)); now_s = lambda: dt.datetime.now(K).strftime("%m-%d %H:%M:%S")
A = Path("/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08"); M = A / ".market"
LOG = Path("/home/user/.local/state/ax-round3-ops/sell_guard_2355.log")
END = dt.datetime(2026, 11, 17, 23, 55, 0, tzinfo=K).timestamp()
SUP = int(sys.argv[1])                     # run_market_locked.sh pid
START_UNITS = {"u-5a9bafc674c7": 11730}    # 재개 시점 재고 (unit -> cost)
BASE = {"refunds": 0, "broken": 0}
def log(kind, **kw):
    with LOG.open("a") as f: f.write(json.dumps({"t": now_s(), "kind": kind, **kw}, ensure_ascii=False) + "\n")
def board():
    b = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=3", timeout=30))
    return next(y for y in b["shops"] if y["name"] == "sohyunkim11")
def alive(p):
    try: os.kill(p, 0); return True
    except OSError: return False
def stop(reason):
    log("STOP" if reason != "time" else "end", reason=reason)
    if alive(SUP): os.kill(SUP, signal.SIGTERM)
    time.sleep(5)
    for p in kids:
        if alive(p): os.kill(p, signal.SIGTERM)
    for _ in range(60):
        if not alive(SUP): break
        time.sleep(1)
    left = [l for l in subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout.splitlines()
            if any(k in l for k in ("market_kit.py", "run_market_locked", "seller.py", "cloudflared")) and "grep" not in l]
    log("final_process_check", remaining=left)
    sys.exit(0)
log("start", sup=SUP, end="23:55:00", note="replaces sell_guard_1920 (22:00); same conditions; seller not restarted")
low = 0
kids = set()
seen = set()
while True:
    if time.time() >= END:
        def pending():
            ev = [json.loads(l) for l in (M / "seller-events.jsonl").read_text().splitlines()[-50:] if l.strip()]
            acc = [e for e in ev if e.get("event") == "accept" and time.time() - e["ts"] < 300]
            kitl = (A / "logs/market-kit.log").read_text(errors="ignore").splitlines()[-50:]
            return [e for e in acc if not any("[acp] settled" in l and e.get("unit", "-") in l for l in kitl)]
        w = time.time()
        while pending() and time.time() - w < 120:
            log("end_wait_pending_settle", pending=[(e.get("unit"), e.get("price")) for e in pending()]); time.sleep(10)
        log("end_pending_check", pending=[(e.get("unit"), e.get("price")) for e in pending()])
        stop("time")
    if not alive(SUP): log("supervisor_exited"); sys.exit(0)
    try:
        ps = [l.split(None, 2) for l in subprocess.run(["ps", "-eo", "pid,ppid,args"], capture_output=True, text=True).stdout.splitlines()[1:]]
        kitp = {int(p) for p, pp, a in ps if int(pp) == SUP}
        kids |= kitp | {int(p) for p, pp, a in ps if int(pp) in kitp}
        if (A / "restock-approval.json").exists() or (A / "restock-approval.claimed.json").exists():
            stop("approval file appeared (new purchase forbidden after 19:00)")
        inv = json.loads((M / "inventory.json").read_text())
        units = {u["unit"]: u.get("cost") for u in inv.get("units", [])}
        new = set(units) - set(START_UNITS)
        if new: stop(f"new stock units appeared {sorted(new)}")
        kit = (A / "logs/market-kit.log").read_text(errors="ignore").splitlines()[-400:]
        for l in kit:
            if "[acp] settled" in l and l not in seen:
                seen.add(l)
                if l.split()[0] >= "19:20":
                    log("settle", line=l)
                    u = next((x for x in START_UNITS if x in l), None)
                    if u:
                        price = int(l.split(" at ")[1].split(",")[0])
                        if price < START_UNITS[u]: stop(f"below-cost settle {price} < {START_UNITS[u]}")
        settled = [l for l in seen if l.split()[0] >= "19:20"]
        if len([l for l in settled if "u-5a9bafc674c7" in l]) > 1: stop("duplicate settle of same unit")
        s = board()
        if (s.get("refunds") or 0) > BASE["refunds"] or (s.get("broken") or 0) > BASE["broken"]:
            stop(f"refund/broken {s.get('refunds')}/{s.get('broken')}")
        if s.get("stars_rank") is not None and s["stars_rank"] < 3.93:
            low += 1; log("warn_stars_rank", value=s["stars_rank"], consecutive=low)
            if low >= 2: stop(f"stars_rank {s['stars_rank']} < 3.93 twice in a row")
        else: low = 0
        log("tick", alive=s.get("alive"), deals=s.get("deals"), book=s.get("book"), stock_cost=s.get("stock_cost"), units=list(units))
    except SystemExit: raise
    except Exception as e:
        log("warn", err=repr(e)[:200])
    time.sleep(max(1, min(60, END - time.time())))
