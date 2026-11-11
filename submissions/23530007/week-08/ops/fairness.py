"""Category-adjusted saved: how much of each student buyer's saved comes from the categories it was assigned.

    python3 ops/fairness.py                           # every Round 3 settle from data/market_archive.db (~1 min);
                                                      # writes reports/fairness.json + .png (the chart needs matplotlib)
    .venv/bin/python ops/fairness.py --sample --pages 10   # no archive: sample the market's ledger (500 entries a page)

Each buyer gets its orders mostly from a few hidden favourite categories (market rules), and categories settle at
very different shares of list (furniture ~.68-.72, many foods ~.55-.60). For every sampled settle we take
    v = 1 - price/list   and   ref = the mean v of ALL buyers' settles in the same category and 12-h bucket.
Per student buyer:
    mix_v   = mean ref over its deals: the saved its category mix and timing would give an average shopper
    skill   = mean (v - ref): how much better or worse it bought than the market, category for category
    adjusted saved = board saved - (mix_v - mean mix_v of all students): the saved rank with the mix luck removed
Sampled (a few pages per bucket at evenly spaced sequence numbers); settles only, so breaks are not in v (the board's
saved counts them). Read-only; no model call.
"""
import argparse
import json
import statistics as st
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
MARKET = "https://market.ourolabs.ai"
OUT = HERE / "reports"
METRIC_CHANGE = 1793912460   # 2026-11-05 21:01 UTC (11-06 06:01 KST): the buyer ranking switched to saved (Patch 3.4; our watcher saw
                             # the course repo publish its rules at 21:15 UTC; was 11-06 12:00 KST by mistake)


def _get(path):
    for _ in range(3):
        try:
            with urllib.request.urlopen(f"{MARKET}{path}", timeout=30) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001
            time.sleep(2)
    return []


def _page(after):
    return _get(f"/api/ledger?after={int(after)}") or []


def _bisect(pred, lo, hi):
    """Smallest seq (to ~500) whose page's first entry satisfies pred (monotone)."""
    while hi - lo > 500:
        mid = (lo + hi) // 2
        p = _page(mid)
        if not p or pred(p[0]):
            hi = mid
        else:
            lo = mid
    return hi


def sample(pages=8, bucket_h=12.0):
    cat = json.loads((HERE / ".market" / "catalog.json").read_text())
    head = _page(-1)[-1]
    hi, now = head["seq"], head["ts"]
    start_seq = _bisect(lambda e: e.get("round", 0) >= 3, 0, hi)
    t0 = _page(start_seq)[0]["ts"]
    n_b = max(1, int((now - t0) / 3600 / bucket_h))
    edges_t = [t0 + i * (now - t0) / n_b for i in range(n_b + 1)]
    seqs = [start_seq] + [_bisect(lambda e, t=t: e["ts"] >= t, start_seq, hi) for t in edges_t[1:-1]] + [hi]
    rows = []
    for b in range(n_b):
        a, z = seqs[b], seqs[b + 1]
        step = max(500, (z - a) // pages)
        for s in range(a, z, step):
            for e in _page(s):
                if e.get("kind") != "settle" or e.get("round") != 3:
                    continue
                d = e.get("data") or {}
                item = cat.get(d.get("sku")) or {}
                lp = item.get("list_price")
                if not lp or not d.get("amount"):
                    continue
                rows.append({"b": b, "ts": e.get("ts"), "buyer": d.get("buyer"), "cat": item.get("category", "?").split(">")[-1],
                             "food": d.get("expires_at") is not None, "v": 1 - d["amount"] / lp})
    return rows, {"t0": t0, "t1": now, "buckets": n_b, "pages_per_bucket": pages}


def from_db(bucket_h=12.0):
    """Every Round 3 settle, from the local archive of the ledger (ops/archive_db.py; data/market_archive.db holds the
    whole chain). No sampling: two 0.3% samples eight hours apart gave r = .50 and r = .33 for the same question."""
    import sqlite3
    cat = json.loads((HERE / ".market" / "catalog.json").read_text())
    c = sqlite3.connect(f"file:{HERE / 'data' / 'market_archive.db'}?mode=ro", uri=True, timeout=60)
    t0, t1 = c.execute("select min(ts), max(ts) from ledger where round=3").fetchone()
    n_b = max(1, int((t1 - t0) / 3600 / bucket_h))
    w = (t1 - t0) / n_b
    rows = []
    for ts, buyer, sku, amount, exp in c.execute(
            "select ts, buyer, sku, amount, expires_at from ledger where round=3 and kind='settle'"):
        item = cat.get(sku) or {}
        lp = item.get("list_price")
        if not lp or not amount:
            continue
        rows.append({"b": min(n_b - 1, int((ts - t0) / w)), "ts": ts, "buyer": buyer,
                     "cat": item.get("category", "?").split(">")[-1], "food": exp is not None, "v": 1 - amount / lp})
    return rows, {"t0": t0, "t1": t1, "buckets": n_b, "pages_per_bucket": None, "source": "archive db: every settle"}


def retotal(res, board):
    """The class total (shop + buyer) of the students in `res`, with the saved percentile taken from the board's saved
    and from the adjusted saved; the other parts (forum rating, deals and posts percentiles, the shop score) the same."""
    sys.path.insert(0, str(HERE / "ops"))
    from final_rank import _pct
    R = {r["player"]: r for r in res["rows"]}
    P = {p["player"]: p for p in board["players"] if p.get("student") and p["player"] in R}
    B = {x["name"]: x for x in board["buyers"]}
    bu = {k: B[(p.get("buyer") or {}).get("name")] for k, p in P.items()}
    deals, posts = _pct({k: x.get("deals") or 0 for k, x in bu.items()}), _pct({k: x.get("posts") or 0 for k, x in bu.items()})
    out = {}
    for key in ("board_saved", "adjusted_saved"):
        sv = _pct({k: R[k][key] for k in P})
        tot = {k: (P[k].get("student_shop") or 0) + 0.5 * sv[k] + 0.3 * (bu[k].get("forum") or 0) + 0.1 * deals[k] + 0.1 * posts[k]
               for k in P}
        out[key] = {k: i + 1 for i, (k, _) in enumerate(sorted(tot.items(), key=lambda kv: -kv[1]))}
    moves = sorted(((k, out["board_saved"][k], out["adjusted_saved"][k]) for k in P), key=lambda x: x[1])
    return {"students": len(P), "changed": sum(1 for _, a, z in moves if a != z),
            "moved_3_or_more": sum(1 for _, a, z in moves if abs(a - z) >= 3),
            "ranks": [{"player": k, "rank_total": a, "rank_total_adjusted": z} for k, a, z in moves]}


def analyse(rows, meta):
    b = _get("/api/board?round=3")
    P = [p for p in b["players"] if p.get("student")]
    names = {(p.get("buyer") or {}).get("name"): p["player"] for p in P}
    board = {x["name"]: x for x in b["buyers"] if x["name"] in names and x.get("saved") is not None}
    ref = {}
    for r in rows:
        ref.setdefault((r["b"], r["cat"]), []).append(r["v"])
    ref = {k: st.mean(v) for k, v in ref.items() if len(v) >= 3}
    per = {}
    for r in rows:
        k = (r["b"], r["cat"])
        if r["buyer"] in board and k in ref:
            e = per.setdefault(r["buyer"], {"v": [], "ref": [], "food": 0, "cats": {}, "pre": [], "post": []})
            (e["pre"] if (r.get("ts") or 0) < METRIC_CHANGE else e["post"]).append(r["v"] - ref[k])
            e["v"].append(r["v"])
            e["ref"].append(ref[k])
            e["food"] += r["food"]
            e["cats"][r["cat"]] = e["cats"].get(r["cat"], 0) + 1
    out = []
    for name, e in per.items():
        if len(e["v"]) < 30:
            continue
        top = sorted(e["cats"].items(), key=lambda kv: -kv[1])[:3]
        out.append({"buyer": name, "player": names[name], "n": len(e["v"]), "board_saved": board[name]["saved"],
                    "raw_v": round(st.mean(e["v"]), 4), "mix_v": round(st.mean(e["ref"]), 4),
                    "skill": round(st.mean(x - y for x, y in zip(e["v"], e["ref"])), 4),
                    "food_share": round(e["food"] / len(e["v"]), 2),
                    "skill_pre": round(st.mean(e["pre"]), 4) if len(e["pre"]) >= 15 else None,
                    "skill_post": round(st.mean(e["post"]), 4) if len(e["post"]) >= 15 else None,
                    "n_pre": len(e["pre"]), "n_post": len(e["post"]),
                    "top_cats": [f"{c} {n / len(e['v']):.0%}" for c, n in top]})
    mean_mix = st.mean(r["mix_v"] for r in out)
    for r in out:
        r["adjusted_saved"] = round(r["board_saved"] - (r["mix_v"] - mean_mix), 4)
    for key in ("skill_post",):
        for i, r in enumerate(sorted((r for r in out if r.get(key) is not None), key=lambda r: -r[key])):
            r["rank_" + key] = i + 1
    for key in ("board_saved", "adjusted_saved", "skill"):
        for i, r in enumerate(sorted(out, key=lambda r: -r[key])):
            r["rank_" + key] = i + 1
    out.sort(key=lambda r: r["rank_board_saved"])
    corr = None
    try:
        corr = round(st.correlation([r["mix_v"] for r in out], [r["board_saved"] for r in out]), 3)
    except Exception:  # noqa: BLE001
        pass
    res = {"meta": dict(meta, settles_sampled=len(rows), students=len(out),
                        metric_change_utc=time.strftime("%m-%d %H:%M", time.gmtime(METRIC_CHANGE)),
                        window_utc=[time.strftime("%m-%d %H:%M", time.gmtime(meta["t0"])),
                                    time.strftime("%m-%d %H:%M", time.gmtime(meta["t1"]))]),
           "mean_mix_v": round(mean_mix, 4), "corr_mix_vs_board_saved": corr, "rows": out}
    try:
        res["class_total"] = retotal(res, b)
    except Exception as e:  # noqa: BLE001
        res["class_total"] = {"error": repr(e)}
    return res


def chart(res, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = res["rows"]
    fig, ax = plt.subplots(figsize=(8, 5.5), dpi=150)
    for r in rows:
        me = r["player"] == "yms979"
        ax.scatter(r["mix_v"], r["board_saved"], s=60 if me else 28, color="#d6336c" if me else "#4c6ef5",
                   alpha=0.95 if me else 0.7, zorder=3 if me else 2)
        if me or r["rank_board_saved"] <= 5:
            ax.annotate(r["player"], (r["mix_v"], r["board_saved"]), fontsize=7, xytext=(4, 3), textcoords="offset points")
    ax.set_xlabel("category-mix expected v (market mean v of the categories and hours each buyer bought in)")
    ax.set_ylabel("board saved")
    ax.set_title(f"Saved follows the assigned categories (r = {res['corr_mix_vs_board_saved']})", fontsize=11)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=8)
    ap.add_argument("--sample", action="store_true", help="sample the market's ledger instead of the local archive")
    a = ap.parse_args()
    rows, meta = sample(a.pages) if a.sample or not (HERE / "data" / "market_archive.db").is_file() else from_db()
    res = analyse(rows, meta)
    OUT.mkdir(exist_ok=True)
    (OUT / "fairness.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        chart(res, OUT / "fairness.png")
    except Exception as e:  # noqa: BLE001
        print("chart skipped:", e)
    me = next((r for r in res["rows"] if r["player"] == "yms979"), None)
    print(json.dumps(res["meta"]), "corr", res["corr_mix_vs_board_saved"], "mean mix", res["mean_mix_v"])
    print(f"{'player':14s} {'n':>5s} {'board':>6s} {'rk':>3s} {'mix_v':>6s} {'raw_v':>6s} {'skill':>7s} {'sk_rk':>5s} {'adj':>6s} {'adj_rk':>6s} food")
    for r in res["rows"]:
        print(f"{r['player'][:14]:14s} {r['n']:5d} {r['board_saved']:.4f} {r['rank_board_saved']:3d} {r['mix_v']:.4f} {r['raw_v']:.4f}"
              f" {r['skill']:+.4f} {r['rank_skill']:5d} {r['adjusted_saved']:.4f} {r['rank_adjusted_saved']:6d} {r['food_share']:.2f}")
    if me:
        print("US:", me)
    ct = res.get("class_total") or {}
    if ct.get("ranks"):
        print(f"class total with the adjusted saved: {ct['changed']} of {ct['students']} students change rank, "
              f"{ct['moved_3_or_more']} by 3 or more")
        print("  " + "; ".join(f"{r['player']} {r['rank_total']}->{r['rank_total_adjusted']}" for r in ct["ranks"]
                               if abs(r["rank_total"] - r["rank_total_adjusted"]) >= 3))
    else:
        print("class total:", ct)


if __name__ == "__main__":
    main()
