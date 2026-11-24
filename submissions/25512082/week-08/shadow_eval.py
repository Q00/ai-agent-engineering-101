"""Score logged restock proposals after the fact (shadow mode). Read-only; never buys.

For each logged decision, look at market settlements in the `horizon` after it and
give each proposed SKU a hypothetical one-unit result: if the SKU settled at all in
the horizon, assume our unit sells at the median observed price share; else it is
held (goods: storage, food: no change in the horizon). Compare model vs rules.

    python shadow_eval.py --log logs/model-decisions.jsonl --settles settles.json --catalog .market/catalog.json
`settles.json` is a list of market settle entries ({ts, data:{sku, amount, buyer, grade}}) fetched separately.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def score(proposal: list[dict], settles: list[dict], catalog: dict, start: float, horizon: float) -> dict:
    window = [s for s in settles if start <= s["ts"] < start + horizon]
    total, sold = 0.0, 0
    for line in proposal:
        sku = line["sku"]
        lp = catalog.get(sku, {}).get("list_price")
        hits = [s["data"]["amount"] / lp for s in window if s["data"].get("sku") == sku and lp]
        if hits:
            sold += 1
            total += statistics.median(hits) * lp - 0.59 * lp  # expected cost share (goods/food mix)
    return {"skus": [p["sku"] for p in proposal], "hypothetical_sold": sold, "hypothetical_margin": round(total)}


def top_overlap(a: list[str], b: list[str], k: int = 3) -> float:
    x, y = set(a[:k]), set(b[:k])
    return len(x & y) / len(x | y) if x | y else 1.0


def evaluate(rows: list[dict], settles: list[dict], catalog: dict, horizon: float = 7200) -> dict:
    model_rows = [r for r in rows if r.get("model_called")]
    out = {"decisions": len(rows), "model_calls": len(model_rows),
           "model_failures": sum(r.get("error") is not None for r in model_rows),
           "latency_s_median": statistics.median([r["latency_s"] for r in model_rows]) if model_rows else None,
           "picks_dropped_by_rules": sum(r.get("model_picks_dropped") or 0 for r in model_rows), "pairs": []}
    for r in model_rows:
        if r.get("source") != "model":
            continue
        m = score(r["enforced"], settles, catalog, r["ts"], horizon)
        b = score(r["rule_proposal"], settles, catalog, r["ts"], horizon)
        out["pairs"].append({"ts": r["ts"], "model": m, "rules": b,
                             "top3_overlap": top_overlap(m["skus"], b["skus"])})
    by_prompt: dict[str, list[list[str]]] = {}
    for r in model_rows:
        if r.get("source") == "model":
            by_prompt.setdefault(r["prompt_sha256"], []).append([p["sku"] for p in r["enforced"]])
    out["consistency_same_context"] = [
        statistics.mean(top_overlap(v[0], w) for w in v[1:]) for v in by_prompt.values() if len(v) > 1]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, default=Path("logs/model-decisions.jsonl"))
    ap.add_argument("--settles", type=Path, required=True)
    ap.add_argument("--catalog", type=Path, default=Path(".market/catalog.json"))
    ap.add_argument("--horizon", type=float, default=7200)
    a = ap.parse_args()
    rows = [json.loads(x) for x in a.log.read_text(encoding="utf-8").splitlines() if x.strip()]
    print(json.dumps(evaluate(rows, json.loads(a.settles.read_text()), json.loads(a.catalog.read_text()), a.horizon),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
