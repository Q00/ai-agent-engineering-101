"""Backfill the market's paid sales from 11/5 to the ledger cache's start, slowly, then file them as 10-minute buckets.

11/9: the past number verdicts could not be re-judged on our share of the market (rejudge_r3) because the ledger
cache keeps only ~6 h of settles and nothing kept the market's totals over time; 87 of 89 verdicts stayed
UNVERIFIED. The public ledger (https://market.ourolabs.ai/api/ledger?after=<seq>, 500 entries a page) still holds
them. The market is shared by the class, so this reads one page at a time with a pause between pages, backs off on
any error, keeps only what the buckets need (settles; our own supply and join entries) and resumes where it stopped.

  python backfill_r3.py fetch [from_seq] [to_seq]   # slow fetch into .market/backfill_r3.jsonl (resumable)
  python backfill_r3.py buckets                     # file the fetched span as buckets (metrics_r3.BUCKETS)
"""
import json
import sys
import time
from pathlib import Path

import httpx

import metrics_r3

HERE = Path(__file__).resolve().parent
MARKET = "https://market.ourolabs.ai"
OUT = HERE / ".market" / "backfill_r3.jsonl"
STATE = HERE / ".market" / "backfill_r3.state.json"
PAUSE = 3.0            # the first pause; after that twice the last reply's time, PAUSE_MIN..PAUSE_MAX, one request at
PAUSE_MIN, PAUSE_MAX = 1.0, 10.0   # a time: fast while the shared server answers fast, slower as soon as it slows
FROM_SEQ = 2_796_875   # 11/05 13:48, two hours and more before the first number verdict (11/05 16:32)


def log(msg):
    print(f"[{time.strftime('%m/%d %H:%M:%S')}] {msg}", flush=True)


def cache_start():
    """The seq of the ledger cache's first settle: the backfill stops there."""
    s = metrics_r3._settles(HERE)
    return min(e["seq"] for e in s) if s else None


def keep(e):
    """The compact entry the buckets need, or None: every settle; our shop's supply and join entries."""
    d = e.get("data") or {}
    if e.get("kind") == "settle":
        return {"seq": e["seq"], "ts": e["ts"], "kind": "settle", "shop": d.get("shop"), "amount": d.get("amount"),
                "unit": d.get("unit")}
    if e.get("kind") == "supply" and d.get("shop") == metrics_r3.ME:
        return {"seq": e["seq"], "ts": e["ts"], "kind": "supply", "units": d.get("units"), "total": d.get("total")}
    if e.get("kind") == "join" and d.get("name") == metrics_r3.ME:
        return {"seq": e["seq"], "ts": e["ts"], "kind": "join", "url": d.get("url"), "role": d.get("role")}
    return None


def fetch(start=None, stop=None):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    after = state.get("after") or start or FROM_SEQ
    stop = stop or state.get("stop") or cache_start()
    if not stop:
        log("no ledger cache to stop at")
        return
    log(f"fetching seq {after} -> {stop} (~{(stop - after) // 500} pages)")
    client, wait, pages = httpx.Client(timeout=60, headers={"User-Agent": "week08-backfill/1.0"}), PAUSE, 0
    while after < stop:
        try:
            t0 = time.time()
            r = client.get(f"{MARKET}/api/ledger", params={"after": after})
            took = time.time() - t0
            if r.status_code == 429 or r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            page = [e for e in r.json() if e["seq"] > after]
        except Exception as e:
            wait = min(wait * 2, 300)
            log(f"page after {after} failed ({e}); waiting {wait:.0f} s")
            time.sleep(wait)
            continue
        if not page:
            log(f"empty page after {after}; stopping")
            break
        rows = [x for x in map(keep, page) if x and x["seq"] < stop]
        with OUT.open("a", encoding="utf-8") as f:
            f.write("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))
        after, pages = page[-1]["seq"], pages + 1
        wait = min(max(2 * took, PAUSE_MIN), PAUSE_MAX)
        STATE.write_text(json.dumps({"after": after, "stop": stop, "t": time.time()}))
        if pages % 100 == 0:
            log(f"{pages} pages, at seq {after} ({time.strftime('%m/%d %H:%M', time.localtime(page[-1]['ts']))}), "
                f"last reply {took:.1f} s, pause {wait:.1f} s")
        time.sleep(wait)
    log(f"done at seq {after}")


def buckets():
    """File the fetched span as 10-minute buckets the buckets file lacks (ours by our shop's urls, margin by the
    supply cost of each unit, else the talk agreed at that price, as metrics_r3.settled_sales does)."""
    rows = metrics_r3.jl(OUT)
    if not rows:
        return 0
    joins = [x for x in rows if x["kind"] == "join"] + \
        [{"url": (e.get("data") or {}).get("url"), "role": (e.get("data") or {}).get("role")}
         for e in json.loads((HERE / ".market" / "ledger-recent.json").read_text(encoding="utf-8"))
         if e.get("kind") == "join" and (e.get("data") or {}).get("name") == metrics_r3.ME]
    urls = {(x.get("url") or "").rstrip("/") for x in joins if x.get("role") == "shop"}
    cost = {u: (x.get("total") or 0) / len(x["units"]) for x in rows if x["kind"] == "supply" and x.get("units")
            for u in x["units"]}
    talks = sorted(((x["t"], x["price"], x.get("cost")) for x in metrics_r3.jl(HERE / "logs" / "talks_r3.jsonl")
                    if x.get("price") and x.get("t")), key=lambda x: x[0])
    times = [x[0] for x in talks]
    path = HERE / metrics_r3.BUCKETS
    done = {x.get("t") for x in metrics_r3.jl(path)}
    settles = [x for x in rows if x["kind"] == "settle"]
    first, last = min(x["ts"] for x in settles), max(x["ts"] for x in settles)
    B = metrics_r3.BUCKET
    out = {b: {"t": b, "ours_n": 0, "ours_value": 0, "ours_margin": 0, "mkt_n": 0, "mkt_value": 0, "from": "backfill"}
           for b in range((int(first) // B + 1) * B, int(last) // B * B, B) if b not in done}
    import bisect
    for x in settles:
        b = int(x["ts"]) // B * B
        if b not in out:
            continue
        out[b]["mkt_n"] += 1
        out[b]["mkt_value"] += x.get("amount") or 0
        if str(x.get("shop") or "").rstrip("/") in urls:
            c = cost.get(x.get("unit"))
            if c is None:                               # the talk agreed at that price before the settle
                i = bisect.bisect_right(times, x["ts"])
                c = next((tc for tt, tp, tc in reversed(talks[max(0, i - 400):i]) if tp == x.get("amount")
                          and tt >= x["ts"] - 900), 0)
            out[b]["ours_n"] += 1
            out[b]["ours_value"] += x.get("amount") or 0
            out[b]["ours_margin"] += (x.get("amount") or 0) - (c or 0)
    with path.open("a", encoding="utf-8") as f:
        for b in sorted(out):
            f.write(json.dumps(out[b]) + "\n")
    return len(out)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if cmd == "fetch":
        fetch(*(int(x) for x in sys.argv[2:4]))
    elif cmd == "buckets":
        print(buckets(), "buckets filed")
