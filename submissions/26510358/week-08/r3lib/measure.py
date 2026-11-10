"""Before/after numbers for a deploy (RL_PROACTIVE.md 5.3-3 and the week-10 lab check "write the baseline down").

    python3 -m r3lib.measure baseline --label rl-proactive      # board + KPI now -> logs/r3/baseline-<label>.json
    python3 -m r3lib.measure compare --at 1794312000 --hours 2  # kpi.jsonl means before and after that moment

The baseline keeps our scored values, what the score model says they are worth, and the latest KPI row. compare
reads only kpi.jsonl (one row per 30 min), so it also works for deploys made before this file existed.
"""
import argparse
import json
import time

from . import config, market, score_model

FIELDS = (("shop_all", ("shop", "all")), ("buyer_all", ("buyer", "all")), ("rank", ("player", "rank")),
          ("total", ("player", "total")), ("book", ("shop", "book", "value")),
          ("revenue", ("shop", "revenue", "value")), ("stars_rank", ("shop", "stars_rank", "value")),
          ("saved", ("buyer", "saved", "value")), ("forum", ("buyer", "forum", "value")),
          ("buyer_deals", ("buyer", "deals", "value")), ("posts", ("buyer", "posts", "value")),
          ("stock_cost", ("logistics.health", "stock_cost")), ("close_rate", ("marketing.pricing", "close_rate")))


def _dig(row, path):
    for k in path:
        row = row.get(k) if isinstance(row, dict) else None
    return row if isinstance(row, (int, float)) else None


def kpi_rows(path=None):
    rows, _ = market.read_jsonl(path or (config.LOG_DIR / "kpi.jsonl"))
    return [r for r in rows if isinstance(r, dict) and r.get("ts")]


def baseline(label, board=None, now=None, out_dir=None):
    now = now or time.time()
    board = board if board is not None else (market.get_json("/api/board") or {})
    b = score_model.Board.from_api(board, ts=now)
    rows = kpi_rows()
    players = board.get("players") or []
    me = next((i for i, p in enumerate(players) if (p.get("shop") or {}).get("name") in config.MY_NAMES), None)
    snap = {"label": label, "ts": round(now), "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "player": None if me is None else {"rank": me + 1, "of": len(players), "total": players[me].get("total")},
            "shop": b and b.me_shop, "buyer": b and b.me_buyer,
            "model": b and {"shop_all": round(score_model.shop_all(b), 4), "buyer_all": round(score_model.buyer_all(b), 4),
                            "saved_threshold": round(score_model.saved_threshold(b), 4)},
            "kpi": rows[-1] if rows else None}
    out = (out_dir or config.LOG_DIR) / f"baseline-{label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    return snap, out


def compare(at, hours=2.0, rows=None):
    """Mean of each KPI field over the `hours` before and after `at`, and book per hour on each side."""
    rows = kpi_rows() if rows is None else rows
    side = {"before": [r for r in rows if at - hours * 3600 <= r["ts"] < at],
            "after": [r for r in rows if at <= r["ts"] <= at + hours * 3600]}
    out = {"at": at, "hours": hours, "rows": {k: len(v) for k, v in side.items()}}
    for name, path in FIELDS:
        vals = {k: [x for x in (_dig(r, path) for r in v) if x is not None] for k, v in side.items()}
        mean = {k: (sum(v) / len(v) if v else None) for k, v in vals.items()}
        out[name] = {**{k: (round(m, 4) if m is not None else None) for k, m in mean.items()},
                     "change": round(mean["after"] - mean["before"], 4) if None not in mean.values() else None}
    for k, v in side.items():
        pts = [(r["ts"], _dig(r, ("shop", "book", "value"))) for r in v]
        pts = [p for p in pts if p[1] is not None]
        out.setdefault("book_per_h", {})[k] = round((pts[-1][1] - pts[0][1]) / ((pts[-1][0] - pts[0][0]) / 3600)) \
            if len(pts) >= 2 and pts[-1][0] > pts[0][0] else None
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--label", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--at", type=float, required=True)
    c.add_argument("--hours", type=float, default=2.0)
    a = ap.parse_args()
    if a.cmd == "baseline":
        snap, out = baseline(a.label)
        print(json.dumps({k: snap[k] for k in ("at", "player", "shop", "buyer", "model")}, ensure_ascii=False))
        print(out)
    else:
        print(json.dumps(compare(a.at, a.hours), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
