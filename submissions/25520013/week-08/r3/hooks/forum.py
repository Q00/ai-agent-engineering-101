"""R3 forum poster: python hooks/forum.py --state .market [--loop 60]. No LLM.

Writes one price post as the buyer to .market/outbox.jsonl every forum.interval_s (policy.json). The post is a
summary of market settles in the last forum.window_h hours for one category, read from our own ledger
(<R3_STATE>/events.jsonl, type "settle", src "market", filled by the hooks' ingest), with the settles' market
ledger seqs as refs. Buyer ALL counts votes on posts written as a buyer (agent.md Rankings), so posts go "as": "buyer".
State (byte cursor, window buffer, last post) lives in <R3_STATE>/forum_state.json; each queued post is also
appended to <R3_STATE>/forum_posts.jsonl.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from common import P, market_dir, read_json
from ledger import state_dir

DEFAULTS = {
    "enabled": True,
    "interval_s": 900,
    "window_h": 3,
    "min_settles": 3,
    "max_refs": 8,
    "rotate": 4,
}


def settings(pol):
    return dict(
        DEFAULTS,
        **{k: v for k, v in (pol.get("forum") or {}).items() if not k.startswith("_")},
    )


def read_new_settles(events, offset):
    """Market settles appended to events.jsonl after byte `offset` -> (rows, new offset). A partial last line is
    left for the next call."""
    rows = []
    try:
        with open(events, "rb") as f:
            f.seek(offset)
            for line in f:
                if not line.endswith(b"\n"):
                    break
                offset += len(line)
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") != "settle" or e.get("src") != "market":
                    continue
                d = e.get("data") or {}
                if (
                    d.get("sku")
                    and isinstance(d.get("amount"), int)
                    and d.get("ledger") is not None
                ):
                    rows.append(
                        {
                            "ts": e.get("ts"),
                            "sku": d["sku"],
                            "amount": d["amount"],
                            "ledger": d["ledger"],
                        }
                    )
    except OSError:
        pass
    return rows, offset


def compose(rows, catalog, cfg, recent_cats, now):
    """Pick the category with the most settles in the window (not one of the last `rotate` posted) and write the
    post. Returns (post dict, category) or (None, reason)."""
    lo = now - cfg["window_h"] * 3600
    by_cat = {}
    for r in rows:
        c = catalog.get(r["sku"]) or {}
        lp = c.get("list_price")
        if r["ts"] is None or r["ts"] < lo or not c.get("category") or not lp:
            continue
        by_cat.setdefault(c["category"], []).append(
            dict(r, list_price=lp, name=c.get("name") or r["sku"])
        )
    ranked = sorted(
        ((len(v), k) for k, v in by_cat.items() if len(v) >= cfg["min_settles"]),
        reverse=True,
    )
    if not ranked:
        return None, "no category with enough settles"
    cat = next((k for _, k in ranked if k not in recent_cats), ranked[0][1])
    rs = by_cat[cat]
    share = sorted(r["amount"] / r["list_price"] for r in rs)
    q = statistics.quantiles(share, n=4) if len(share) >= 2 else [share[0]] * 3
    per_sku = {}
    for r in rs:
        per_sku.setdefault(r["sku"], []).append(r)
    sku, srs = max(per_sku.items(), key=lambda kv: (len(kv[1]), kv[0]))
    leaf = cat.split(">")[-1]
    text = (
        f"[시세] {leaf}: 최근 {cfg['window_h']}시간 시장 정산 {len(rs)}건 (공개 원장 기준)\n"
        f"- 체결가/정가 중앙값 {statistics.median(share):.0%} (하위 25% {q[0]:.0%}, 상위 25% {q[2]:.0%})\n"
        f"- 가장 많이 팔린 상품: {srs[0]['name']} ({sku}) {len(srs)}건, "
        f"체결가 중앙 {int(statistics.median(r['amount'] for r in srs)):,} / 정가 {srs[0]['list_price']:,}\n"
        f"- 근거: refs의 원장 seq"
    )
    refs = [
        r["ledger"]
        for r in sorted(rs, key=lambda r: r["ts"], reverse=True)[: cfg["max_refs"]]
    ]
    return {"post": text, "refs": refs, "as": "buyer"}, cat


def pending_buyer_post(md):
    """True while the kit still holds one of our buyer posts (it keeps a post refused with 429 in the outbox)."""
    for name in ("outbox.jsonl", "outbox.taking"):
        try:
            for line in (md / name).read_text(encoding="utf-8").splitlines():
                m = json.loads(line)
                if "post" in m and m.get("as") == "buyer":
                    return True
        except (OSError, ValueError):
            continue
    return False


def run_once(md, now=None, pol=None):
    now = time.time() if now is None else now
    cfg = settings(pol if pol is not None else P.load())
    if not cfg["enabled"]:
        return "disabled"
    root = state_dir()
    sp = root / "forum_state.json"
    st = read_json(sp, {}) or {}
    rows, st["offset"] = read_new_settles(
        root / "events.jsonl", int(st.get("offset", 0))
    )
    keep = now - cfg["window_h"] * 3600
    st["buffer"] = [
        r for r in (st.get("buffer") or []) + rows if (r.get("ts") or 0) >= keep
    ]
    why = None
    if now - float(st.get("last_post_ts", 0)) < cfg["interval_s"]:
        why = "interval"
    elif pending_buyer_post(md):
        why = "previous post still in the outbox"
    else:
        post, cat = compose(
            st["buffer"],
            read_json(md / "catalog.json", {}) or {},
            cfg,
            st.get("recent_cats") or [],
            now,
        )
        if post is None:
            why = cat
        else:
            with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(post, ensure_ascii=False) + "\n")
            with open(root / "forum_posts.jsonl", "a", encoding="utf-8") as f:
                f.write(
                    json.dumps({"ts": now, "category": cat, **post}, ensure_ascii=False)
                    + "\n"
                )
            st["last_post_ts"] = now
            st["recent_cats"] = ((st.get("recent_cats") or []) + [cat])[
                -cfg["rotate"] :
            ]
            why = f"queued: {cat} ({len(post['refs'])} refs)"
    root.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    tmp.replace(sp)
    return why


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--state", default=None, help=".market path (default MARKET_DIR or .market)"
    )
    ap.add_argument(
        "--loop", type=float, default=0, help="seconds between runs; 0 = run once"
    )
    ap.add_argument("--now", type=float, default=None, help="clock override (tests)")
    a = ap.parse_args()
    md = market_dir(a.state)
    while True:
        try:
            why = run_once(md, a.now)
        except Exception as e:  # noqa: BLE001  the loop must outlive one bad run
            why = f"error {type(e).__name__}: {str(e)[:150]}"
        print(f"{time.strftime('%H:%M:%S')} [forum-agent] {why}", flush=True)
        if not a.loop:
            return
        time.sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
