"""v1.18 forum post audit (report 6.3): python -m evals.forum_check --state <R3_STATE dir> [--market <market dir>]

Checks each post in <state>/forum_posts.jsonl ({ts, category, post, refs}, written by hooks/forum.py) against
<state>/events.jsonl and <market>/catalog.json (market dir default MARKET_DIR or .market):
- refs: every ref is the market ledger # of a market settle of the post's category inside the window
  [ts - window_h, ts], and the refs are the newest len(refs) settles of that window (forum keeps max_refs);
- numbers: recomputed independently from the whole window (not from the refs): the count, the median and the
  quartiles (statistics.quantiles n=4) of amount / list_price, the top SKU (most settles, ties to the larger SKU
  id, like compose), its count, its median amount and list price. Percentages compare as the post prints them
  (.0%). window_h is read from the post text. A settle counts as hooks/forum.py reads it: type settle, src market,
  with sku, int amount and a ledger #, whose catalog entry has the category and a list price.
Prints PASS/FAIL per post; exit 1 on any FAIL.
"""

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

LINE1 = re.compile(r"\[시세\] (?P<leaf>.+): 최근 (?P<wh>\d+)시간 시장 정산 (?P<n>\d+)건 \(공개 원장 기준\)$")
LINE2 = re.compile(r"- 체결가/정가 중앙값 (?P<med>\d+%) \(하위 25% (?P<q1>\d+%), 상위 25% (?P<q3>\d+%)\)$")
LINE3 = re.compile(r"- 가장 많이 팔린 상품: (?P<name>.+) \((?P<sku>[^()]+)\) (?P<k>\d+)건, "
                   r"체결가 중앙 (?P<amt>[\d,]+) / 정가 (?P<lp>[\d,]+)$")


def read_jsonl(path):
    rows = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return [r for r in rows if isinstance(r, dict)]


def market_settles(events):
    out = []
    for e in events:
        d = e.get("data") or {}
        if (e.get("type") == "settle" and e.get("src") == "market" and d.get("sku") and isinstance(d.get("amount"), int)
                and not isinstance(d.get("amount"), bool) and d.get("ledger") is not None
                and isinstance(e.get("ts"), (int, float))):
            out.append({"ts": e["ts"], "sku": d["sku"], "amount": d["amount"], "ledger": d["ledger"]})
    return out


def check_post(p, settles, catalog):
    """-> list of problems ([] = PASS)."""
    lines = str(p.get("post") or "").split("\n")
    m1, m2, m3 = (rx.match(lines[i]) if len(lines) > i else None for i, rx in enumerate((LINE1, LINE2, LINE3)))
    if not (m1 and m2 and m3):
        return ["post text does not parse"]
    ts, cat = p.get("ts"), p.get("category")
    if not isinstance(ts, (int, float)) or not cat:
        return ["post has no ts or category"]
    lo = ts - int(m1["wh"]) * 3600
    win = [dict(r, lp=catalog[r["sku"]]["list_price"]) for r in settles
           if lo <= r["ts"] <= ts and (catalog.get(r["sku"]) or {}).get("category") == cat
           and (catalog.get(r["sku"]) or {}).get("list_price")]
    bad = []
    if m1["leaf"] != cat.split(">")[-1]:
        bad.append(f"category leaf {m1['leaf']!r} != {cat.split('>')[-1]!r}")
    refs = p.get("refs") or []
    in_win = {str(r["ledger"]) for r in win}
    for ref in refs:
        if str(ref) not in in_win:
            bad.append(f"ref {ref} is not a {cat} market settle in the window")
    newest = {str(r["ledger"]) for r in sorted(win, key=lambda r: r["ts"], reverse=True)[: len(refs)]}
    if refs and {str(x) for x in refs} != newest:
        bad.append("refs are not the newest settles of the window")
    if not win:
        return bad + ["no settle of the category in the window"]
    share = sorted(r["amount"] / r["lp"] for r in win)
    q = statistics.quantiles(share, n=4) if len(share) >= 2 else [share[0]] * 3
    per_sku = {}
    for r in win:
        per_sku.setdefault(r["sku"], []).append(r)
    sku, srs = max(per_sku.items(), key=lambda kv: (len(kv[1]), kv[0]))
    info = catalog[sku]
    want = {
        "n": str(len(win)),
        "med": f"{statistics.median(share):.0%}",
        "q1": f"{q[0]:.0%}",
        "q3": f"{q[2]:.0%}",
        "sku": sku,
        "name": info.get("name") or sku,
        "k": str(len(srs)),
        "amt": f"{int(statistics.median(r['amount'] for r in srs)):,}",
        "lp": f"{info['list_price']:,}",
    }
    got = {**m1.groupdict(), **m2.groupdict(), **m3.groupdict()}
    bad += [f"{k}: post {got[k]} != ledger {v}" for k, v in want.items() if got[k] != v]
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", required=True, help="R3_STATE dir with forum_posts.jsonl and events.jsonl")
    ap.add_argument("--market", default=None, help="market dir with catalog.json (default MARKET_DIR or .market)")
    a = ap.parse_args(argv)
    st, md = Path(a.state), Path(a.market or os.environ.get("MARKET_DIR", ".market"))
    try:
        catalog = json.loads((md / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"FAIL no catalog at {md / 'catalog.json'} ({type(e).__name__})")
        return 1
    settles = market_settles(read_jsonl(st / "events.jsonl"))
    posts = read_jsonl(st / "forum_posts.jsonl")
    nbad = 0
    for i, p in enumerate(posts):
        bad = check_post(p, settles, catalog)
        nbad += bool(bad)
        print(f"{'FAIL' if bad else 'PASS'} post {i} ts={p.get('ts')} category={p.get('category')} "
              f"refs={len(p.get('refs') or [])}"
              + "".join(f"\n  - {b}" for b in bad))
    print(f"forum_check: {len(posts) - nbad}/{len(posts)} posts PASS")
    return 1 if nbad else 0


if __name__ == "__main__":
    sys.exit(main())
