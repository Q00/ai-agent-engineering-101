"""Week 08 — review_r3.py: the kit's review hook for a paid (settled) Round 3 deal.

    python review_r3.py --deal .market/deal.json

The kit runs it after the payment with what the market recorded (grade at settle, whether the shop's grade
claim was true, shelf life, warranty, value). Stars come from those facts only, and the review states them in
checkable terms (SKU, grade, list price, ledger number). A false grade claim also goes into my memory as an
offense of that shop (buyer_memory.sanction).
"""
import argparse
import json
import time
from pathlib import Path

from market_buyer import remember


def shop_name(url):
    try:
        for s in json.loads(Path(".market/shops.json").read_text(encoding="utf-8")):
            if s["url"].rstrip("/") == url.rstrip("/"):
                return s["name"]
    except (OSError, ValueError, KeyError):
        pass
    return url


def judge(deal):
    o, rec, st = deal.get("order") or {}, deal.get("receipt") or {}, deal.get("settled") or {}
    price, sku = rec.get("price"), st.get("sku") or rec.get("item")
    lp = st.get("value") or 0
    try:
        lp = json.loads(Path(".market/catalog.json").read_text(encoding="utf-8"))
        lp = next((r["list_price"] for r in lp if r["sku"] == sku), None) if isinstance(lp, list) else lp[sku]["list_price"]
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        lp = st.get("value") or None
    facts, problem = [f"[sku {sku}] {str(st.get('name') or '')[:28]}"], None
    if price and lp:
        facts.append(f"paid {price} vs list {lp} ({(lp - price) / lp:+.0%} kept)")
    grade, claim = st.get("grade"), st.get("claim")
    facts.append(f"grade={grade}" + (f" claim={claim}" if claim else ""))
    stars = 3
    if price and lp:
        keep = (lp - price) / lp
        stars = 5 if keep >= 0.25 else 4 if keep >= 0.10 else 3 if keep >= 0 else 2
    if claim and st.get("claim_honest") is False and "ABC".find(str(claim)) < "ABC".find(str(grade)):
        # only a claim BETTER than the true grade is a lie; claiming B for an A unit is in the buyer's favour
        # (Kiro729 was paused for that, 11-04)
        stars, problem = 1, f"misbehaved (hallucination): claimed grade {claim}, the market says {grade}"
        facts.append("claim_honest=false")
    elif claim:
        facts.append("claim_honest=true")
    if o.get("kind") == "food" and st.get("expires_at") and o.get("use_within_hours"):
        left = (st["expires_at"] - time.time()) / 3600
        if left < o["use_within_hours"]:
            stars, problem = 1, problem or f"misbehaved (hallucination): food lasts {left:.1f} h, needed {o['use_within_hours']} h"
            facts.append(f"shelf_left={left:.0f}h < needed {o['use_within_hours']}h")
        else:
            facts.append(f"shelf_left={left:.0f}h")
    if st.get("warranty_hours"):
        facts.append(f"warranty={st['warranty_hours']}h")
    if st.get("ledger"):
        facts.append(f"ledger #{st['ledger']}")
    data = {"sku": sku, "paid": price, "list": lp, "kept": round((lp - price) / lp, 3) if price and lp else None,
            "grade": grade, "claim": claim, "claim_true": st.get("claim_honest"),
            "shelf_left_h": round((st["expires_at"] - time.time()) / 3600) if st.get("expires_at") else None,
            "needed_h": o.get("use_within_hours"), "warranty_h": st.get("warranty_hours"), "ledger": st.get("ledger"),
            "stars": stars}
    return stars, review_text(facts, data)[:640], problem, st.get("shop") or ""


def review_text(facts, data):
    """HQ 11-04: written for the agents that read it -- one Korean line for people, then the same facts as compact
    JSON with fixed keys (no parsing guesswork), every value checkable on the ledger."""
    return (json.dumps({k: v for k, v in data.items() if v is not None}, ensure_ascii=False, separators=(",", ":"))
            + "\n" + HUMAN + "\n" + bilingual(facts))          # HQ: JSON on top, then the review for people


HUMAN = "-- 사람용 요약 (에이전트는 건너뛰어도 됨) / for people, agents can skip --"


KO = [("paid ", "결제 "), (" vs list ", " / 정가 "), (" kept)", " 절약)"), ("grade=", "등급="), ("claim=", "주장="),
      ("claim_honest=true", "등급 주장 일치"), ("claim_honest=false", "등급 주장 불일치"), ("shelf_left=", "남은 유통기한 "),
      (" < needed ", " < 필요 "), ("warranty=", "보증 "), ("ledger #", "원장 #")]


def bilingual(facts):
    """HQ 11-04: reviews in Korean and English. The same checkable facts, Korean first."""
    en = " | ".join(facts)
    ko = en
    for a, b in KO:
        ko = ko.replace(a, b)
    return f"{ko}\n{en}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    deal = json.loads(Path(ap.parse_args().deal).read_text(encoding="utf-8"))
    stars, review, problem, url = judge(deal)
    if problem:
        remember({"ts": round(time.time(), 1), "shop": shop_name(url), "outcome": "review", "problem": problem,
                  "ledger": (deal.get("settled") or {}).get("ledger")})
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
