"""Round 3 review hook: python review.py --deal .market/deal.json  ->  {"stars": 1-5, "review": "..."}

Runs after the kit paid. Facts the buyer now knows that it did not at negotiation time: the real grade,
whether the shop's claim was honest, the shelf life left, the warranty, and the value the market assigns.
Code sets a floor/ceiling (a dishonest claim is never above 2 stars); the harness writes the sentence.
"""
import argparse, json, time
from pathlib import Path
import harness
from market_buyer import forum_post


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--deal", required=True); a = ap.parse_args()
    d = json.loads(Path(a.deal).read_text(encoding="utf-8"))
    o, r, s = d.get("order", {}), d.get("receipt", {}), d.get("settled", {})
    price, value = r.get("price"), s.get("value")
    hours = (s["expires_at"] - time.time()) / 3600 if s.get("expires_at") else None
    honest = s.get("claim_honest")
    facts = (f"Bought {s.get('name') or r.get('item')} (grade {s.get('grade')}, shop claimed {s.get('claim')}, "
             f"claim honest: {honest}) at {price}, budget {o.get('budget')}, list value {value}"
             + (f", {hours:.1f} h of shelf life left, I need it within {o.get('use_within_hours')} h" if hours is not None else "")
             + (f", warranty {s.get('warranty_hours')} h" if s.get("warranty_hours") else ""))
    data = harness.ask_json("Review this shop for other buyers from the facts only. " + facts +
                            ' Reply with exactly one JSON object: {"stars": <integer 1-5>, "review": "<one sentence>"}.',
                            lambda m: print(m, flush=True)) or {}
    stars, review = data.get("stars"), str(data.get("review", "")).strip()
    if not (isinstance(stars, int) and 1 <= stars <= 5) or not review:
        harness.CALLS["fallbacks"] += 1
        stars = 4 if (value or 0) >= (price or 0) else 3
        review = f"{s.get('name')} grade {s.get('grade')} at {price}, claim honest: {honest}."
    if honest is False:
        stars = min(stars, 2)                     # a false grade claim is never a good review
    elif hours is not None and o.get("use_within_hours") and hours < o["use_within_hours"]:
        stars = min(stars, 2)                     # food that will not last the order's use window
    md = Path(a.deal).parent                      # feed the buyer's memory: was the claim honest, did goods come with a warranty
    try:
        names = {x["url"].rstrip("/"): x["name"] for x in json.loads((md / "shops.json").read_text(encoding="utf-8"))}
        mem = json.loads(Path("buyer_memory.json").read_text(encoding="utf-8"))
        last = mem["shops"][names[str(s.get("shop", "")).rstrip("/")]]["trades"][-1]
        if honest is False:
            last["dishonest"] = True
        if o.get("kind") == "goods":
            last["warranty"] = s.get("warranty_hours") or 0
        Path("buyer_memory.json").write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
    except (OSError, ValueError, KeyError, IndexError) as e:
        print(f"[memory] could not record the settled deal: {type(e).__name__}", flush=True)
    if s.get("ledger"):                       # forum: a verifiable purchase record (ledger ref), the kind other buyers upvote
        pct = f", 정가 대비 {100 - 100 * price // value}% 저렴" if value and price and value > price else ""
        forum_post(Path(a.deal).parent,
                   f"[구매 기록] {s.get('name')} ({s.get('sku')}) | {price:,}{pct} | 등급 {s.get('grade')} (주장 {s.get('claim') or '-'}, "
                   f"{'정직' if honest else '거짓' if honest is False else '주장 없음'}) | 판매: {s.get('shop')} | ledger #{s['ledger']} | 별점 {stars}",
                   refs=[s["ledger"]])
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
