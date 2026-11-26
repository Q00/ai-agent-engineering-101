"""Is our shop alive and reachable? (used by the /market-status skill and the market tool 'shop_health')

    .venv/bin/python ops/health.py            # verdict + checks
    .venv/bin/python ops/health.py --json

Checks, all read-only (no model call):
- board: our shop's alive flag, and how many shops the market marks dead (many = a market-wide outage, not us)
- card: GET <public url>/.well-known/agent-card.json from here (status, seconds)
- seller: age of the last seller task in logs/market-kit.log (> 5 min = buyers are not reaching us)
- kit: process running; supervisor exits/starts and their reasons over the last 6 h
- relists: shop_guard tunnel swaps over the last 6 h
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "ops"))
import status  # noqa: E402


def _lines(p, n=4000):
    try:
        return (HERE / p).read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]
    except OSError:
        return []


def _ts(line):
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
    return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) - time.timezone if m else None


def check(hours=6):
    now = time.time()
    out = {"utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now))}
    try:
        b = status.get("/api/board?round=3")
        me = next((s for s in b.get("shops", []) if s.get("name") == status.NAME), {})
        live = [s for s in b.get("shops", []) if s.get("deals")]
        out["board"] = {"alive": me.get("alive"), "market_dead": f"{sum(1 for s in live if not s.get('alive'))}/{len(live)}"}
    except Exception as e:  # noqa: BLE001
        out["board"] = {"error": f"{type(e).__name__}: {e}"[:160]}
    k = status.kit_state()
    out["kit"] = {"running": k["kit_running"], "url": k["public_url"]}
    url = k["public_url"]
    if url:
        t0 = time.time()
        try:
            req = urllib.request.Request(f"{url}/.well-known/agent-card.json", headers={"User-Agent": "curl/8.5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:   # Cloudflare answers 403 to Python's default UA
                out["card"] = {"status": r.status, "seconds": round(time.time() - t0, 2)}
        except Exception as e:  # noqa: BLE001
            out["card"] = {"error": f"{type(e).__name__}: {e}"[:160], "seconds": round(time.time() - t0, 2)}
    last = k["last_seller_task"]
    if last:
        h, m, s = map(int, last.split(":"))
        g = time.gmtime(now)
        age = (g.tm_hour * 3600 + g.tm_min * 60 + g.tm_sec - (h * 3600 + m * 60 + s)) % 86400
        out["seller"] = {"last_task_utc": last, "age_s": age}
    since = now - hours * 3600
    sup = [l for l in _lines("logs/market-supervisor.log") if (_ts(l) or 0) >= since]
    exits = [l for l in sup if "kit exited" in l]
    out["kit"]["exits"] = len(exits)
    out["kit"]["starts"] = sum(1 for l in sup if "starting the kit" in l)
    out["kit"]["recent_exits"] = [re.sub(r"\s+", " ", l)[:220] for l in exits[-3:]]
    rel = [l for l in _lines("logs/shop_guard.log") if "relist:" in l and "killed tunnel" in l and (_ts(l) or 0) >= since]
    out["relists"] = {"n": len(rel), "last": rel[-1][:160] if rel else None}
    problems = []
    if out["board"].get("alive") is False:
        problems.append("board marks us dead")
    if "error" in out.get("card", {}) or out.get("card", {}).get("status") != 200:
        problems.append("card unreachable from here")
    if out.get("seller", {}).get("age_s", 9999) > 300:
        problems.append("no seller task for > 5 min")
    if not out["kit"]["running"]:
        problems.append("kit not running")
    dead = out["board"].get("market_dead", "0/1").split("/")
    outage = len(dead) == 2 and int(dead[1]) > 0 and int(dead[0]) / int(dead[1]) >= 0.3
    if not problems:
        out["verdict"] = "OK"
    elif outage and "card unreachable from here" not in problems and out["kit"]["running"]:
        out["verdict"] = ("MARKET OUTAGE (not us): " + "; ".join(problems) +
                          f"; {out['board']['market_dead']} shops marked dead, our card answers; the kit watchdog re-joins within ~5 min")
    else:
        out["verdict"] = "PROBLEM: " + "; ".join(problems)
    return out


def main():
    out = check()
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"[{out['utc']} UTC] {out['verdict']}")
    print(f"board   {out['board']}")
    print(f"card    {out.get('card')}  url {out['kit']['url']}")
    print(f"seller  {out.get('seller')}")
    print(f"kit     running {out['kit']['running']}  6h: {out['kit']['starts']} starts, {out['kit']['exits']} exits")
    for l in out["kit"]["recent_exits"]:
        print(f"        {l}")
    print(f"relists 6h: {out['relists']['n']}  last: {out['relists']['last']}")


if __name__ == "__main__":
    main()
