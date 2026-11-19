"""The saved frontier over time: how the best student buyers' per-deal value moves, and where we are against it
(used by the /market-whatif skill and the market tool 'frontier').

    .venv/bin/python ops/frontier.py                 # last 4 days in 12-h buckets, top 8 saved students + us
    .venv/bin/python ops/frontier.py --days 6 --bucket 24 --json

The full ledger is millions of entries, so it is SAMPLED: in each bucket a few 500-entry pages at evenly spaced
sequence numbers (seq <-> time found by bisection). Per deal v = 1 - price/list (breaks are not in settles; the
board's saved includes them). Per buyer and bucket: deals sampled, mean v, food share, warranty share, median list
price. Read-only; no model call; ~1-2 min of ledger reads.
"""
import argparse
import json
import statistics as st
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
MARKET = "https://market.ourolabs.ai"
NAME = "yms979"


def _get(path):
    with urllib.request.urlopen(f"{MARKET}{path}", timeout=30) as r:
        return json.load(r)


def _page(after):
    try:
        return _get(f"/api/ledger?after={int(after)}")
    except Exception:  # noqa: BLE001
        return []


def _seq_at(t, lo, hi):
    """Smallest seq whose entry time is >= t (bisection over pages)."""
    for _ in range(26):
        if hi - lo <= 500:
            break
        mid = (lo + hi) // 2
        p = _page(mid)
        if not p:
            hi = mid
            continue
        if p[0]["ts"] < t:
            lo = mid
        else:
            hi = mid
    return lo


def frontier(days=4.0, bucket_h=12.0, pages_per_bucket=6, top=8):
    days, bucket_h = min(float(days or 4), 8.0), max(3.0, float(bucket_h or 12))
    cat = json.loads((HERE / ".market" / "catalog.json").read_text())
    cat = cat if isinstance(cat, dict) else {c["sku"]: c for c in cat}
    b = _get("/api/board?round=3")
    P = [p for p in b["players"] if p.get("student")]
    names = {(p.get("buyer") or {}).get("name"): p["player"] for p in P}
    board = sorted([x for x in b["buyers"] if x["name"] in names and x.get("saved") is not None], key=lambda x: -x["saved"])
    watch = [x["name"] for x in board[:top]]
    if NAME not in watch:
        watch.append(NAME)
    head = _page(-1)
    hi = head[-1]["seq"]
    now = time.time()
    n_b = int(days * 24 / bucket_h)
    edges = [now - (n_b - i) * bucket_h * 3600 for i in range(n_b + 1)]
    seqs = [_seq_at(t, 0, hi) for t in edges[:-1]] + [hi]
    acc = {}                        # (bucket, buyer|"market") -> rows
    for i in range(n_b):
        a, z = seqs[i], seqs[i + 1]
        step = max(500, (z - a) // pages_per_bucket)
        for s in range(a, z, step):
            for e in _page(s):
                if e.get("kind") != "settle":
                    continue
                d = e.get("data") or {}
                item = cat.get(d.get("sku")) or {}
                lp = item.get("list_price")
                if not lp or not d.get("amount"):
                    continue
                r = {"v": 1 - d["amount"] / lp, "food": d.get("expires_at") is not None,
                     "w": (d.get("warranty_hours") or 0) > 0, "lp": lp}
                acc.setdefault((i, "market"), []).append(r)
                if d.get("buyer") in names:
                    acc.setdefault((i, "students"), []).append(r)
                if d.get("buyer") in watch:
                    acc.setdefault((i, d["buyer"]), []).append(r)

    def summ(rows):
        if not rows:
            return None
        return {"n": len(rows), "v": round(st.mean(r["v"] for r in rows), 3),
                "food": round(sum(r["food"] for r in rows) / len(rows), 2),
                "warr": round(sum(r["w"] for r in rows) / len(rows), 2),
                "list_med_k": round(st.median(r["lp"] for r in rows) / 1000)}

    labels = [time.strftime("%m-%d %H", time.gmtime(t)) for t in edges[:-1]]
    series = {}
    for who in ["market", "students"] + watch:
        series[who] = [summ(acc.get((i, who), [])) for i in range(n_b)]
    saved = {x["name"]: x["saved"] for x in board}
    rank = {x["name"]: i + 1 for i, x in enumerate(board)}
    top_v = []
    for i in range(n_b):
        vs = [series[w][i]["v"] for w in watch if w != NAME and series[w][i] and series[w][i]["n"] >= 5]
        top_v.append(round(st.mean(vs), 3) if vs else None)
    return {"buckets_utc": labels, "bucket_h": bucket_h, "watch": [{"buyer": w, "player": names.get(w), "saved": saved.get(w),
                                                                    "saved_rank": rank.get(w)} for w in watch],
            "top_mean_v": top_v, "series": series,
            "note": "sampled ledger; v per settle (breaks excluded); top_mean_v = mean of the top students' per-deal v"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=4)
    ap.add_argument("--bucket", type=float, default=12)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = frontier(a.days, a.bucket)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    L = out["buckets_utc"]
    print("per-deal v (sampled n) by bucket start UTC:", " | ".join(L))
    rows = [("market", None), ("students", None)] + [(w["buyer"], w) for w in out["watch"]]
    for who, w in rows:
        tag = f"{who[:16]:16s}" + (f" #{w['saved_rank']:<2} {w['saved']:.3f}" if w else " " * 10)
        cells = []
        for c in out["series"][who]:
            cells.append(f"{c['v']:.3f}({c['n']:>3}) f{c['food']:.2f}" if c else " " * 18)
        print(tag, " ".join(cells))
    print(f"{'top mean v':26s}", " ".join(f"{v:.3f}" if v else "  -  " for v in out["top_mean_v"]))


if __name__ == "__main__":
    main()
