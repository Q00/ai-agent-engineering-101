"""R3 review hook: python hooks/review.py --deal {deal}. Stars from settled facts (claim_honest, grade, shelf life)."""
import argparse
import json
from pathlib import Path

from common import P, Recorder, emit, hook_event, ingest, read_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", default=".market/deal.json")
    a = ap.parse_args()
    pol = P.load()
    rec = Recorder("review", policy_hash=pol["_hash"])
    md = Path(a.deal).parent
    seq, counts, ims = ingest(md, with_market=False)
    deal = read_json(a.deal, {}) or {}
    broken = set()
    bp = md / "broken.jsonl"
    if bp.exists():
        for line in bp.read_text(encoding="utf-8").splitlines()[-200:]:
            try:
                broken.add(json.loads(line).get("name"))
            except ValueError:
                pass
    stars, text = P.review_for(deal, broken)
    s = deal.get("settled") or {}
    order = (deal.get("order") or {}).get("order")
    seq = hook_event("review", f"review:{order}", {"order": order, "shop": s.get("shop"), "sku": s.get("sku"),
                                                   "stars": stars, "review": text}, "buyer")
    rec.write("review", seq, outcome={"stars": stars, "claim_honest": s.get("claim_honest"), "grade": s.get("grade")},
              ingest_ms=ims, ingested=counts)
    emit({"stars": stars, "review": text})


if __name__ == "__main__":
    main()
