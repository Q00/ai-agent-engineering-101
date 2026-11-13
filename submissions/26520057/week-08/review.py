"""Review hook: stars for a settled deal, a memory of every deal, and a buyer forum post every 5+ minutes.

The kit runs `python review.py --deal {deal}` after each paid deal. Stars and the review come only from the
market's settled record (grade, claim honesty, warranty). Every deal goes to logs/buyer-settled.jsonl; when 310 s
have passed since the last post, the deals not yet posted (and goods of ours that broke) become one post in
.market/buyer-box/outbox.jsonl, which run_buyer.py hands to the kit as the buyer. Facts only, from the ledger.
"""
import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MD = ROOT / ".market"
MEMORY = ROOT / "logs/buyer-settled.jsonl"
STATE = ROOT / ".market/buyer-forum-state.json"
BOX = MD / "buyer-box"
POST_GAP = 310           # the market allows one buyer post per 5 minutes
MAX_LINES = 5


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def read_jsonl(path):
    rows = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return rows


def shop_names():
    shops = read_json(MD / "buyer-public-metadata.json", {}).get("shops", {})
    return {v.get("url", "").rstrip("/"): k for k, v in shops.items()}


def record(deal):
    s, r, o = deal.get("settled", {}), deal.get("receipt", {}), deal.get("order", {})
    sku = s.get("sku") or r.get("item")
    product = read_json(MD / "catalog.json", {}).get(sku, {})
    url = (s.get("shop") or "").rstrip("/")
    return {"ts": time.time(), "order": o.get("order"), "ledger": s.get("ledger"), "sku": sku,
            "name": s.get("name") or product.get("name") or sku, "category": o.get("category") or product.get("category"),
            "kind": o.get("kind") or product.get("kind"), "price": r.get("price"), "list_price": product.get("list_price"),
            "grade": s.get("grade"), "claim": s.get("claim"), "claim_honest": s.get("claim_honest"),
            "warranty_hours": s.get("warranty_hours") or 0, "value": s.get("value"),
            "shop_url": url, "shop": shop_names().get(url) or url}


def stars_for(rec):
    if rec["claim_honest"] is False:
        return 2, f"{rec['shop']} claimed grade {rec['claim']} but the unit was {rec['grade']}."
    if rec["value"] == 0:
        return 2, f"{rec['name'][:40]} was worth nothing to the order at settle."
    if rec["grade"] == "C":
        return 4, f"Honest grade C at {rec['price']:,}; ledger #{rec['ledger']}."
    return 5, f"Grade {rec['grade']} as claimed at {rec['price']:,}; ledger #{rec['ledger']}."


def line_for(rec):
    pct = f" (정가 {rec['list_price']:,}의 {round(100 * rec['price'] / rec['list_price'])}%)" if rec.get("list_price") else ""
    honest = {True: "정직", False: "부정직"}.get(rec["claim_honest"], "확인 안 됨")
    claim = f", 주장 {rec['claim']} {honest}" if rec.get("claim") else ""
    warranty = f" | 보증 {rec['warranty_hours']:g}h" if rec["kind"] != "food" else ""
    return (f"- {rec['name'][:34]} ({rec['sku']}) | {rec['price']:,}{pct} | 등급 {rec['grade']}{claim}{warranty}"
            f" | 판매 {rec['shop']} | #{rec['ledger']}")


def broken_lines(memory, state):
    """Our goods that broke: match broken.jsonl names to our own purchases in the 6 h before."""
    seen, lines = set(state.get("broken_seen", [])), []
    for b in read_jsonl(MD / "broken.jsonl"):
        key = f"{b.get('name')}|{b.get('broke_at')}"
        if key in seen:
            continue
        seen.add(key)
        broke = b.get("broke_at") or 0
        match = [m for m in memory if m.get("name") == b.get("name") and 0 <= broke - m["ts"] <= 6 * 3600 + 120]
        if match:
            m = match[-1]
            cover = f"보증 {m['warranty_hours']:g}h로 환불" if m["warranty_hours"] * 3600 >= broke - m["ts"] else "보증 없음"
            lines.append((m, f"- 고장: {m['name'][:34]} | 판매 {m['shop']} | 구매 {round((broke - m['ts']) / 60)}분 뒤 | {cover} | #{m['ledger']}"))
    state["broken_seen"] = sorted(seen)[-500:]
    return lines


def maybe_post(memory):
    state = read_json(STATE, {"last_post": 0, "posted": []})
    if time.time() - state.get("last_post", 0) < POST_GAP or (BOX / "outbox.jsonl").exists():
        return
    posted = set(state.get("posted", []))
    fresh = [m for m in memory if m.get("ledger") and m["ledger"] not in posted][-MAX_LINES:]
    broken = broken_lines(memory, state)
    if not fresh and not broken:
        return
    honest = [m for m in memory if m.get("claim_honest") is not None]
    rows = [m for m in memory if m.get("price") and m.get("list_price")]
    head = f"[구매 기록 · cheapest-in-town] 결제·정산된 거래 {len(fresh)}건 (공개 원장 번호 첨부)"
    tail = (f"누적 {len(memory)}건: 등급 주장 정직 {sum(m['claim_honest'] for m in honest)}/{len(honest)}"
            + (f", 평균 체결가 정가의 {round(100 * sum(m['price'] / m['list_price'] for m in rows) / len(rows))}%" if rows else ""))
    text = "\n".join([head] + [line_for(m) for m in fresh] + [l for _, l in broken] + [tail])
    refs = [m["ledger"] for m in fresh] + [m["ledger"] for m, _ in broken]
    BOX.mkdir(exist_ok=True)
    with open(BOX / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"post": text, "refs": refs[:10], "as": "buyer"}, ensure_ascii=False) + "\n")
    state["last_post"] = time.time()
    state["posted"] = sorted(posted | {m["ledger"] for m in fresh})[-2000:]
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deal", type=Path, required=True)
    args = parser.parse_args()
    rec = record(read_json(args.deal, {}))
    with open(MEMORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    stars, review = stars_for(rec)
    try:
        maybe_post(read_jsonl(MEMORY))
    except Exception:
        pass
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
