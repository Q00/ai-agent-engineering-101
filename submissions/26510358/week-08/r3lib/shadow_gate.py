"""Shadow evaluation gate (CI-1): live policy vs a candidate on the same twin window, same seed.

    python -m r3lib.shadow_gate --hours 6 --candidate policies/candidates/lever1.json [--candidate ...]

For each candidate it replays the window twice (live, candidate) with identical data and random draws, prints
book / revenue / stars / deals / close rate / profit per visit / stockouts for both and the delta, an estimated
shop ALL score, a PASS/FAIL verdict, and a calibration of the twin's live replay against what actually happened.
The JSON report goes to logs/shadow/ (gitignored). Nothing live is written: the DB is opened read-only, restock
plans are dry runs on a private copy, config values are patched in this process only.

Score (agent.md "Rankings"): shops rank by book, revenue and stars; ALL weighs the three percentiles 0.5/0.3/0.2,
among shops with a deal or a purchase; book = cash + what the unsold units cost. The gate takes the latest
logs/monitor/*/board-*.json, puts our shop at its board values, adds the twin's candidate - live delta (scaled
from the window to DELTA_HORIZON_H) and recomputes our percentiles against the other shops held fixed.
Percentile = share of the other ranked shops strictly below us; ranked = shops the board gives an `all`; stars =
the board's `stars_rank`. On board-20261105T1500 this reproduces every shop's `all` within 0.01 (mean error
0.003; ours 0.7885 vs 0.79), see board_fit, which every report re-checks. Still an estimate: the other shops do
not move, stars are held at the window's actual stars (the twin does not model reviews), and book is the board's
book field (= profit + stock_cost; else derived, see board_book).

Verdict (documented rule): PASS when, for the candidate minus live,
    book_delta >= 0, estimated ALL >= 0, close rate drop <= MAX_CLOSE_DROP (0.05), served close rate drop <=
    MAX_SERVED_CLOSE_DROP (0.03), and extra stockouts <= max(2, 10% of live visits)
(close_rate = deals / negotiations; served_close_rate = deals / visits, stockouts included, MO 2026-11-05: a
candidate that turns buyers away can raise close_rate and still lose deals). Reruns with the same --end give the same report: the board is
the latest one at or before --end (board_at), and the inventory and demand rows are pinned in
logs/shadow/inputs-<end>.json on the first run (pinned_inputs) and read back on later runs; the plan DB copy (with shop_stock, shops and meta, which have no event time) is kept as
logs/shadow/memory-<end>.sqlite on the first run (pinned_db) and copied on later runs. A calibration warning (twin live deals off the actual deals by
more than CALIBRATION_TOL) is reported next to the verdict: the number is less trustworthy, the rule still applies.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from . import config, twin

MAX_CLOSE_DROP = 0.05
MAX_SERVED_CLOSE_DROP = 0.03                # MO 2026-11-05
CALIBRATION_TOL = 0.25
DELTA_HORIZON_H = 24
ALL_WEIGHTS = {"book": 0.5, "revenue": 0.3, "stars": 0.2}
WEEK = Path(__file__).resolve().parent.parent
REPORT_DIR = WEEK / "logs" / "shadow"
MONITOR_DIR = WEEK / "logs" / "monitor"
SHOWN = ("book_delta", "book_end", "deal_revenue", "stars", "deals", "negotiations", "close_rate",
         "served_close_rate", "profit", "profit_per_visit", "stockouts", "visits", "served", "final_price_ratio",
         "first_ask_ratio", "stock_cost_end", "supply_spend")
CLOSE_RATE_DEF = ("close_rate = deals / negotiations (a session with >= 1 buyer offer), same for observed and twin;"
                  " served_close_rate = deals / visits, stockouts included (observed: negotiations rows +"
                  " stockout rows)")
REVENUE_BASIS = ("twin revenue = deal revenue (twin deal prices), compared with observed deal_revenue "
                 "(negotiations rows with outcome deal); observed settled_revenue (settles rows) is shown apart")


# ------------------------------------------------------------------------------------------- policies
def load_candidate(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return twin.make_policy(data.get("policy", data), name=data.get("name") or Path(path).stem)


def restock_mode(live, cand, requested="auto"):
    """'auto': plan when the candidate changes anything restock reads, else replay our actual deliveries."""
    if requested != "auto":
        return requested
    return "plan" if twin.restock_differs(live, cand) else "actual"


def run_pair(w, live, cand, seed=0, restock="auto", plan_fn=None, seeds=1):
    """Both policies on the same window, same seeds, same restock mode. Averages over `seeds` seeds."""
    mode = restock_mode(live, cand, restock)
    runs = {"live": [], "candidate": []}
    for s in range(seed, seed + seeds):
        runs["live"].append(twin.replay(w, live, seed=s, restock=mode, live=live, plan_fn=plan_fn))
        runs["candidate"].append(twin.replay(w, cand, seed=s, restock=mode, live=live, plan_fn=plan_fn))
    out = {k: average(v) for k, v in runs.items()}
    out["candidate"]["policy"] = cand.get("name")
    out["delta"] = delta(out["live"], out["candidate"])
    out["restock_mode"] = mode
    out["seeds"] = list(range(seed, seed + seeds))
    return out


def average(results):
    if len(results) == 1:
        return dict(results[0])
    out = dict(results[0])
    for k, v in results[0].items():
        vals = [r[k] for r in results if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and len(vals) == len(results):
            out[k] = round(statistics.mean(vals), 4)
    return out


def delta(live, cand):
    d = {}
    for k, v in live.items():
        c = cand.get(k)
        if isinstance(v, (int, float)) and isinstance(c, (int, float)) and not isinstance(v, bool):
            d[k] = round(c - v, 4)
    return d


# ------------------------------------------------------------------------------------------- score
def latest_board(monitor_dir=MONITOR_DIR):
    files = sorted(glob.glob(str(Path(monitor_dir) / "*" / "board-*.json")), key=lambda p: Path(p).name)
    if not files:
        return None, None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f), files[-1]


def file_stamp(path):
    """Epoch s from a monitor file name such as board-20261105T1520.json (local time, as the monitor writes it)."""
    m = re.search(r"-(\d{8}T\d{4})(\d{2})?\.json$", Path(path).name)
    if not m:
        return None
    return time.mktime(time.strptime(m.group(1) + (m.group(2) or "00"), "%Y%m%dT%H%M%S"))


def monitor_file_at(monitor_dir, prefix, end):
    """The latest logs/monitor/*/<prefix>-*.json taken at or before `end`, else (None, None)."""
    best = None
    for p in glob.glob(str(Path(monitor_dir) / "*" / f"{prefix}-*.json")):
        ts = file_stamp(p)
        if ts is not None and ts <= end and (best is None or (ts, p) > best):
            best = (ts, p)
    return (best[1], best[0]) if best else (None, None)


def board_at(monitor_dir=MONITOR_DIR, end=None):
    """Board pinned to the window: the latest board at or before `end` (None = the latest board). If every board is
    after `end`, the first one after it, so a rerun still picks the same file."""
    if end is None:
        return latest_board(monitor_dir)
    path, _ = monitor_file_at(monitor_dir, "board", end)
    if path is None:
        later = sorted((file_stamp(p), p) for p in glob.glob(str(Path(monitor_dir) / "*" / "board-*.json"))
                       if file_stamp(p) is not None)
        if not later:
            return None, None
        path = later[0][1]
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def pinned_inputs(end, monitor_dir=MONITOR_DIR, out_dir=REPORT_DIR):
    """Inventory and demand rows for a window ending at `end`, the same on every rerun. The first run writes
    <out_dir>/inputs-<end>.json: the latest logs/monitor inventory snapshot at or before `end` (inventory_ts = its
    stamp), else the kit's live inventory.json (inventory_ts = now, a known look-ahead, noted), plus the kit's
    demand.json as read then. Later runs read that file back. The catalog is the kit's static catalog.json; its hash
    is stored and a change is reported."""
    from . import market
    path = Path(out_dir) / f"inputs-{end:.0f}.json"
    cat_sha = _sha(market.catalog())
    if path.exists():
        pin = json.loads(path.read_text(encoding="utf-8"))
        pin["reused"] = True
    else:
        src, ts = monitor_file_at(monitor_dir, "inventory", end)
        if src:
            inv = json.loads(Path(src).read_text(encoding="utf-8"))
        else:
            src, ts, inv = "live inventory.json", time.time(), market.read_json("inventory.json", {}) or {}
        pin = {"end": end, "inventory_source": str(src), "inventory_ts": ts, "inventory": inv,
               "demand_rows": market.read_json("demand.json", []) or [], "catalog_sha": cat_sha,
               "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pin, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        pin["reused"] = False
    pin["path"] = str(path)
    pin["catalog_changed"] = pin.get("catalog_sha") != cat_sha
    return pin


def pinned_db(end, db_path, out_dir=REPORT_DIR):
    """The DB copy for restock_r3.plan, the same on every rerun for one `end`. snapshot_db removes rows with
    ts > end, but shop_stock and shops (latest-state upserts: competitor stock) and meta (no ts: unmet demand,
    restock_adjust, explore_spent) hold the state at copy time. The first run keeps its copy as
    <out_dir>/memory-<end>.sqlite; later runs use that file. Returns (path, reused). The state in it is the state
    at the first run, not at `end` (a known look-ahead)."""
    path = Path(out_dir) / f"memory-{end:.0f}.sqlite"
    if path.exists():
        return path, True
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="twin-", dir=path.parent))
    try:
        made = twin.snapshot_db(db_path or config.DB_PATH, tmp, end=end)
        os.replace(made, path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return path, False


def board_book(shop):
    """Book from a board shop row: its book field, else profit (cash) + stock cost if both are there, else None."""
    for k in ("book", "book_value"):
        if isinstance(shop.get(k), (int, float)):
            return float(shop[k])
    cash = shop.get("profit", shop.get("cash"))
    stock = shop.get("stock_cost", shop.get("stock_value", shop.get("inventory_cost")))
    if isinstance(cash, (int, float)) and isinstance(stock, (int, float)):
        return float(cash + stock)
    return None


def _ranked(shop):
    """agent.md: percentiles among shops with a deal or a purchase. The board marks those with an `all` value;
    older boards without it fall back to deals > 0."""
    if "all" in shop:
        return shop.get("all") is not None
    return (shop.get("deals") or 0) > 0


def _stars(shop):
    """The board ranks stars on `stars_rank` (stars shrunk by review count) when it has one."""
    v = shop.get("stars_rank")
    return v if isinstance(v, (int, float)) else (shop.get("stars") or 0)


def percentile(values, mine):
    """Our percentile among the ranked shops: share of the OTHER shops strictly below us (values includes ours),
    in [0, 1]. This variant reproduces the board's shop `all` best (see board_fit)."""
    if not values:
        return None
    if len(values) == 1:
        return 1.0
    return sum(1 for v in values if v < mine) / (len(values) - 1)


def is_mine(shop):
    names = {n.lower() for n in config.MY_NAMES}
    owner = shop.get("owner")
    login = owner.get("login") if isinstance(owner, dict) else owner
    return str(shop.get("name") or "").lower() in names or str(login or "").lower() in names


def _metric(shop, k):
    return board_book(shop) if k == "book" else _stars(shop) if k == "stars" else shop.get(k)


def shop_all(shops, me, add=None):
    """0.5 / 0.3 / 0.2 weighted percentiles of book, revenue, stars for `me` (with `add` deltas) among `shops`."""
    add = add or {}
    parts = {}
    for k in ALL_WEIGHTS:
        mine = _metric(me, k)
        if not isinstance(mine, (int, float)):
            return None, parts
        mine += add.get(k, 0)
        pool = [_metric(s, k) for s in shops if s is not me]
        pool = [v for v in pool if isinstance(v, (int, float))] + [mine]
        parts[k] = round(percentile(pool, mine), 4)
    return round(sum(ALL_WEIGHTS[k] * parts[k] for k in ALL_WEIGHTS), 4), parts


def board_fit(board):
    """How well shop_all reproduces the board's own `all` per shop: mean / max absolute error."""
    shops = [s for s in (board or {}).get("shops") or [] if _ranked(s)]
    errs = []
    for s in shops:
        if isinstance(s.get("all"), (int, float)):
            est, _ = shop_all(shops, s)
            if est is not None:
                errs.append(abs(est - s["all"]))
    if not errs:
        return None
    return {"shops": len(errs), "mae": round(sum(errs) / len(errs), 4), "max_err": round(max(errs), 4)}


def estimate_all(board, d_book=0.0, d_revenue=0.0, d_stars=0.0):
    """Our estimated shop ALL (0-1, the board's scale) with the deltas added to our board row; others fixed."""
    shops = [s for s in (board or {}).get("shops") or [] if _ranked(s) or is_mine(s)]
    me = next((s for s in shops if is_mine(s)), None)
    if me is None:
        return None
    est, parts = shop_all(shops, me, {"book": d_book, "revenue": d_revenue, "stars": d_stars})
    return {"all": est, "parts": parts, "board_all": me.get("all"), "ranked_shops": len(shops),
            "ours": {k: _metric(me, k) for k in ALL_WEIGHTS}}


def score_effect(board, pair, hours):
    if not board:
        return {"note": "no logs/monitor board file: score not estimated"}
    scale = DELTA_HORIZON_H / hours
    d = pair["delta"]
    base = estimate_all(board)
    cand = estimate_all(board, d_book=d.get("book_delta", 0) * scale, d_revenue=d.get("revenue", 0) * scale)
    out = {"horizon_h": DELTA_HORIZON_H, "scale": round(scale, 3), "live": base, "candidate": cand,
           "board_fit": board_fit(board)}
    if base and cand and base.get("all") is not None and cand.get("all") is not None:
        out["delta_all"] = round(cand["all"] - base["all"], 4)
    return out


# ------------------------------------------------------------------------------------------- verdict
UNSIMULATED_KEYS = ("forum",)                       # levers the twin has no model for (see forum_estimate)
OK_VERDICTS = ("PASS", "ESTIMATED")                 # main exits 0 only when every result is one of these


def forum_shadow(w, board, spec):
    """Forum-only candidate (lever2-forum): the twin cannot simulate it, so run the deterministic dry run as of the
    window end (read-only memory.sqlite, no send, no model call) and its checks. ESTIMATED if the checks pass,
    else FAIL. The buyer score effect is an estimate (forum_lever2.estimate), never a simulation."""
    from . import forum_lever2
    db = getattr(w, "db_path", None) or twin.config.DB_PATH
    dry = forum_lever2.dry_run(forum_lever2.ReadOnlyMem(db), board, w.end, spec=spec or {})
    dry["checks"] = forum_lever2.checks(dry)
    c = dry["checks"]
    reasons = [] if c["ok"] else [k for k in ("schedule_ok", "unique_ok", "refs_ok") if not c[k]] + \
        (["default -1 votes"] if c["default_downvotes"] else []) + \
        (["votes that would not count"] if c["uncounted_votes"] else []) + (["no drafts"] if not c["posts"] else [])
    est = dry["estimate"]
    summary = (f"dry run {c['posts']} posts, checks ok; ESTIMATE buyer ALL {est.get('live', {}).get('buyer_all')} -> "
               f"{est.get('candidate', {}).get('buyer_all')} ({est.get('delta_buyer_all')}) over "
               f"{est.get('horizon_h')} h") if c["ok"] else "dry-run checks failed: " + ", ".join(reasons)
    return dry, {"verdict": "ESTIMATED" if c["ok"] else "FAIL", "reasons": [summary]}


def forum_estimate(board, target):
    """Forum lever (posts / votes): the twin does not model how forum activity changes buyers or the forum
    score, so this is the documented target next to our latest board values, not a simulated effect."""
    me = next((s for s in (board or {}).get("shops") or [] if is_mine(s)), None) or {}
    now = {k: v for k, v in me.items() if any(t in k.lower() for t in ("forum", "post", "vote"))}
    return {"simulated": False, "target": target, "board_now": now,
            "note": "UNSIMULATED: shop metrics above are unchanged by construction; forum effect not modelled"}


def verdict(pair, score):
    d, live = pair["delta"], pair["live"]
    reasons = []
    if d.get("book_delta", 0) < 0:
        reasons.append(f"book down {d['book_delta']:+.0f}")
    if (score or {}).get("delta_all") is not None and score["delta_all"] < 0:
        reasons.append(f"estimated ALL down {score['delta_all']:+.4f}")
    if d.get("close_rate") is not None and d["close_rate"] < -MAX_CLOSE_DROP:
        reasons.append(f"close rate down {d['close_rate']:+.3f}")
    if d.get("served_close_rate") is not None and d["served_close_rate"] < -MAX_SERVED_CLOSE_DROP:
        reasons.append(f"served close rate down {d['served_close_rate']:+.3f} (allowed -{MAX_SERVED_CLOSE_DROP:.2f})")
    allowed = max(2, 0.10 * (live.get("visits") or 0))
    if d.get("stockouts", 0) > allowed:
        reasons.append(f"stockouts up {d['stockouts']:+.0f} (allowed {allowed:.0f})")
    return {"verdict": "FAIL" if reasons else "PASS", "reasons": reasons,
            "rule": "PASS iff d.book>=0, d.ALL>=0, close-rate drop<=%.2f, served-close-rate drop<=%.2f, "
                    "extra stockouts<=max(2,10%% visits)" % (MAX_CLOSE_DROP, MAX_SERVED_CLOSE_DROP)}


def calibration(w, seed=0, plan_fn=None, modes=("actual", "plan")):
    """Twin replay of the live policy vs what happened in the window."""
    live = twin.live_policy()
    out = {"observed": w.actual}
    for mode in modes:
        try:
            r = twin.replay(w, live, seed=seed, restock=mode, live=live, plan_fn=plan_fn)
        except Exception as e:                      # plan mode needs the market catalog; report, do not die
            out[f"twin_{mode}"] = {"error": f"{type(e).__name__}: {e}"}
            continue
        a = w.actual
        err = (r["deals"] - a["deals"]) / a["deals"] if a.get("deals") else None
        keys = ("deals", "close_rate", "revenue", "final_price_ratio", "first_ask_ratio", "stockouts", "visits",
                "served")
        t = out[f"twin_{mode}"] = {k: r[k] for k in keys + ("negotiations", "served_close_rate", "deal_revenue")}
        t["deals_rel_err"] = round(err, 4) if err is not None else None
        t["close_rate_diff"] = round(r["close_rate"] - a["close_rate"], 4) \
            if r.get("close_rate") is not None and a.get("close_rate") is not None else None
        t["revenue_rel_err"] = round((r["revenue"] - a["deal_revenue"]) / a["deal_revenue"], 4) \
            if a.get("deal_revenue") else None
        t["ok"] = err is not None and abs(err) <= CALIBRATION_TOL
    return out


# ------------------------------------------------------------------------------------------- run
def evaluate(w, candidates, seed=0, restock="auto", board=None, board_path=None, plan_fn=None, seeds=1,
             calibrate=True):
    """All candidates on one window. Without a plan_fn, ONE twin.DefaultPlan (one DB copy, rows <= w.end) serves
    every plan-mode replay of this run and is removed in finally, also on an exception or KeyboardInterrupt."""
    if plan_fn is not None:
        return _evaluate(w, candidates, seed, restock, board, board_path, plan_fn, seeds, calibrate)
    with twin.DefaultPlan(w) as shared:
        rep = _evaluate(w, candidates, seed, restock, board, board_path, shared, seeds, calibrate)
        rep["plan_snapshots"] = shared.snapshots
        return rep


def not_applicable(live, cand, mode):
    """A restock lever replayed in actual mode has no effect (real deliveries are replayed), so never PASS."""
    if mode == "actual" and twin.restock_differs(live, cand):
        return {"verdict": "NOT_APPLICABLE", "reasons": ["restock lever in restock=actual mode: real supplies are "
                                                         "replayed, the candidate's restock change has no effect; "
                                                         "run with --restock plan (or auto)"]}
    return None


def _evaluate(w, candidates, seed, restock, board, board_path, plan_fn, seeds, calibrate):
    live = twin.live_policy()
    report = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "window": {
        "start": w.start, "end": w.end, "hours": round((w.end - w.start) / 3600, 2),
        "start_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(w.start)),
        "end_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(w.end)),
        "end_source": w.end_source, "reproduce": f"--end {w.end!r} --hours {round((w.end - w.start) / 3600, 4)}",
        "visits": len(w.visits), "stockout_rows": len(w.stockouts), "supplies": len(w.supplies),
        "fill_rate": round(w.fill_rate, 3), "stars": w.stars, "star_count": w.star_count, "notes": w.notes},
        "seed": seed, "seeds": seeds, "board": board_path, "close_rate_def": CLOSE_RATE_DEF,
        "revenue_basis": REVENUE_BASIS, "results": []}
    hours = (w.end - w.start) / 3600 or 1
    modes_used = set()
    for cand in candidates:
        pair = run_pair(w, live, cand, seed=seed, restock=restock, plan_fn=plan_fn, seeds=seeds)
        modes_used.add(pair["restock_mode"])
        score = score_effect(board, pair, hours)
        pair["score"] = score
        pair["gate"] = verdict(pair, score)
        unsim = {k: cand[k] for k in UNSIMULATED_KEYS if cand.get(k) and cand.get(k) != live.get(k)}
        if unsim:
            pair["unsimulated"] = {"forum": forum_estimate(board, unsim["forum"])} if "forum" in unsim else unsim
            only_unsim = all(k in UNSIMULATED_KEYS or k == "name" for k, v in cand.items() if live.get(k) != v)
            if only_unsim and "forum" in unsim:
                dry, g = forum_shadow(w, board, unsim["forum"])
                pair["forum_dry_run"] = dry
                pair["gate"] = dict(g, rule=pair["gate"]["rule"])
            elif only_unsim:
                pair["gate"] = {"verdict": "UNSIMULATED", "reasons": [f"{', '.join(unsim)}: not modelled by the twin"],
                                "rule": pair["gate"]["rule"]}
        na = not_applicable(live, cand, pair["restock_mode"])
        if na:
            pair["gate"] = dict(na, rule=pair["gate"]["rule"])
        pair["candidate_policy"] = {k: v for k, v in cand.items() if live.get(k) != v or k == "name"}
        report["results"].append(pair)
    if calibrate:
        report["calibration"] = calibration(w, seed=seed, plan_fn=plan_fn,
                                            modes=tuple(sorted(modes_used | {"actual"})))
        warn = [m[5:] for m, c in report["calibration"].items() if m.startswith("twin_") and not c.get("ok")]
        for pair in report["results"]:
            pair["gate"]["calibration_warning"] = pair["restock_mode"] in warn
    return report


def write_report(report, out_dir=REPORT_DIR):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"gate-{time.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


def _fmt(v):
    if isinstance(v, float):
        return f"{v:,.4f}" if abs(v) < 10 else f"{v:,.0f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def render(report):
    lines = [f"window {report['window']['start_iso']} .. {report['window']['end_iso']} "
             f"({report['window']['hours']} h): {report['window']['visits']} visits, "
             f"{report['window']['stockout_rows']} stockout rows, {report['window']['supplies']} supplies, "
             f"fill {report['window']['fill_rate']}, stars {report['window']['stars']}",
             f"end ts {report['window']['end']!r} ({report['window'].get('end_source')}); reproduce with "
             f"{report['window'].get('reproduce')}",
             f"{report.get('close_rate_def')}", f"revenue basis: {report.get('revenue_basis')}"]
    for r in report["results"]:
        lines.append(f"\n== live vs {r['candidate']['policy']}  (restock={r['restock_mode']}, seeds={r['seeds']})")
        lines.append(f"{'metric':<20}{'live':>16}{'candidate':>16}{'delta':>16}")
        for k in SHOWN:
            lines.append(f"{k:<20}{_fmt(r['live'].get(k)):>16}{_fmt(r['candidate'].get(k)):>16}"
                         f"{_fmt(r['delta'].get(k, '')):>16}")
        s = r["score"]
        if s.get("live") and s["live"].get("all") is not None:
            lines.append(f"estimated shop ALL = 0.5 pct(book) + 0.3 pct(revenue) + 0.2 pct(stars), delta x{s['scale']}"
                         f" to {s['horizon_h']} h: live {s['live']['all']} (board says {s['live']['board_all']}) ->"
                         f" candidate {s['candidate']['all']} ({s.get('delta_all'):+})")
            lines.append(f"  parts live {s['live']['parts']} candidate {s['candidate']['parts']};"
                         f" formula vs board all over all shops: {s.get('board_fit')}")
        else:
            lines.append(f"estimated shop ALL: {s}")
        if r.get("unsimulated"):
            lines.append(f"unsimulated levers: {r['unsimulated']}")
        fd = r.get("forum_dry_run")
        if fd:
            v, rb = fd["votes"], fd["rollback_baseline"]
            lines.append(f"forum dry run (sent nothing, llm_calls {fd['llm_calls']}): checks {fd['checks']}")
            lines.append(f"  votes on {v['posts_seen']} recent posts: +1 {v['plus']}, -1 {v['minus']}, none {v['none']}"
                         f"; rollback baseline last {rb['hours']} h: ours {rb['ours']}, others {rb['others']},"
                         f" would trip now {rb['would_trip_now']}")
            lines.append(f"  ESTIMATE: {json.dumps({k: fd['estimate'].get(k) for k in ('now', 'live', 'candidate', 'delta_buyer_all', 'players_total')}, ensure_ascii=False)}")
            if fd.get("files"):
                lines.append(f"  files: {fd['files']}")
        g = r["gate"]
        lines.append(f"GATE: {g['verdict']} {'; '.join(g['reasons'])}"
                     f"{'  [calibration warning]' if g.get('calibration_warning') else ''}")
    c = report.get("calibration")
    if c:
        a = c["observed"]
        lines.append(f"\ncalibration (twin live vs actual window): actual deals {a['deals']}, negotiations "
                     f"{a.get('negotiations')}, close_rate {a['close_rate']}, served_close_rate "
                     f"{a.get('served_close_rate')}, deal revenue {a['deal_revenue']:,}, settle revenue "
                     f"{a.get('settled_revenue', 0):,}, final/list {a['final_price_ratio']},"
                     f" first ask/list {a['first_ask_ratio']}, stockouts {a['stockouts']}")
        for m, v in c.items():
            if m != "observed":
                lines.append(f"  twin[{m}]: {v}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--candidate", action="append", default=[], help="candidate policy JSON (repeatable)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, default=1, help="average over this many consecutive seeds")
    ap.add_argument("--restock", choices=("auto", "actual", "plan"), default="auto")
    ap.add_argument("--db", default=None, help="memory.sqlite (default config.DB_PATH, opened read-only)")
    ap.add_argument("--end", type=float, default=None,
                    help="window end (epoch s; default last negotiation). The resolved value is written to the "
                         "report (window.end, end_source); comparison runs should pass a fixed --end")
    ap.add_argument("--monitor-dir", default=str(MONITOR_DIR))
    ap.add_argument("--out", default=str(REPORT_DIR))
    ap.add_argument("--no-calibration", action="store_true")
    a = ap.parse_args(argv)
    if not a.candidate:
        ap.error("at least one --candidate")
    cands = [load_candidate(p) for p in a.candidate]
    if a.end is not None:
        pin = pinned_inputs(a.end, a.monitor_dir, a.out)
        w = twin.load_window(a.db, hours=a.hours, end=a.end, inventory=pin["inventory"],
                             demand_rows=pin["demand_rows"], inventory_ts=pin["inventory_ts"])
        w.plan_db, db_reused = pinned_db(a.end, w.db_path, a.out)
        w.notes.append(f"plan DB pinned in {Path(w.plan_db).name} ({'reused' if db_reused else 'written now'};"
                       " shop_stock, shops and meta as at its first run)")
        w.notes.append(f"inputs pinned in {os.path.relpath(pin['path'], WEEK) if pin['path'].startswith(str(WEEK)) else pin['path']}"
                       f" ({'reused' if pin['reused'] else 'written now'}; inventory from {Path(pin['inventory_source']).name})"
                       + ("; WARNING catalog changed since the pin" if pin["catalog_changed"] else ""))
    else:
        w = twin.load_window(a.db, hours=a.hours, end=a.end)
        w.notes.append("no --end: inputs read live (inventory.json, demand.json); a rerun can differ")
    board, board_path = board_at(a.monitor_dir, w.end if a.end is not None else None)
    report = evaluate(w, cands, seed=a.seed, restock=a.restock, board=board, board_path=board_path, seeds=a.seeds,
                      calibrate=not a.no_calibration)
    for r in report["results"]:
        if r.get("forum_dry_run"):
            from . import forum_lever2
            r["forum_dry_run"]["files"] = [str(f) for f in forum_lever2.write_dry_run(r["forum_dry_run"], a.out)]
    path = write_report(report, a.out)
    print(render(report))
    print(f"\nreport: {os.path.relpath(path, WEEK) if str(path).startswith(str(WEEK)) else path}")
    return 0 if all(r["gate"]["verdict"] in OK_VERDICTS for r in report["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
