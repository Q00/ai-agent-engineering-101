"""Forum notice written AS THE SHOP (yms979 11-11, user: 'make those two accounts come to us').

Until now every forum post of ours was the buyer's; other shops post their stock and terms, and buyer agents read the
newest posts (.market/forum.json). This appends one truthful notice to .market/outbox.jsonl (the kit sends it; the
market allows one shop post per 5 minutes) each time watch_round.sh runs it, alternating two kinds:
  food       the must_stock foods we hold in grade A (>= 80% of shelf life left), with the asking share of list
  furniture  our best-selling goods with their A/B units in stock and the price they settled at in the last 6 h
Only what is in stock and only what the seller really does (grade A food, A/B goods, the star ask) is stated.
Votes on shop posts do not enter a shop's score; the notice is there so a buyer agent that reads the forum finds us.
"""
import json
import statistics
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD = HERE / ".market"
STATE = MD / "shop_post.json"
STAR_ASK = 0.56          # keep in step with seller_r3.STAR_ASK
FOOD_FLOOR = 0.80        # ... and seller_r3.FOOD_SHELF_FLOOR


def read(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def food_notice(inv, cat, must, now):
    rows = []
    for sku in must:
        c = cat.get(sku) or {}
        sh = c.get("shelf_hours")
        n = sum(1 for u in inv if u["sku"] == sku and u.get("expires_at") and sh
                and (u["expires_at"] - now) / 3600 >= FOOD_FLOOR * sh)
        if n:
            rows.append(f"{c.get('name', sku)[:30]} ({sku}) {n}개, {round(c['list_price'] * STAR_ASK):,}부터")
    if not rows:
        return None
    return ("[yms979 상점 · 신선 식품] 유통기한이 80% 이상 남은 A등급만 판매합니다. 마켓 인증서 포함, 호가는 정가의 56%부터이고 흥정 가능합니다. "
            "지금 재고: " + "; ".join(rows[:8]) + ". 결제는 A2A 견적 후 바로 정산됩니다.")


def goods_notice(inv, cat, now):
    sv = read(MD / "shop_value.json", {})
    D = sv.get("deals") or []
    t1 = max((d["ts"] for d in D), default=now)
    ours = [d for d in D if d.get("shop") == "yms979" and d.get("goods") and t1 - d["ts"] < 6 * 3600]
    if len(ours) < 10:
        return None
    ab = Counter(u["sku"] for u in inv if u.get("kind") == "goods" and u.get("grade") in ("A", "B"))
    sold = Counter(d["sku"] for d in ours)
    rows = []
    for sku, n in sold.most_common(12):
        if ab[sku] < 2:
            continue
        c = cat.get(sku) or {}
        x = statistics.median(d["x"] for d in ours if d["sku"] == sku)
        rows.append(f"{c.get('name', sku)[:26]} ({sku}) A·B {ab[sku]}개, 최근 체결 정가의 {x:.0%}")
        if len(rows) == 6:
            break
    if not rows:
        return None
    med = statistics.median(d["x"] for d in ours)
    return (f"[yms979 상점 · 가구] A·B등급만 판매하고 C등급(리퍼)은 팔지 않습니다. 72시간 보증과 마켓 인증서 포함. "
            f"최근 6시간 {len(ours)}건 체결, 중앙값 정가의 {med:.0%}. 재고: " + "; ".join(rows) + ".")


def main():
    now = time.time()
    st = read(STATE, {})
    if now - st.get("t", 0) < 600:              # at most one notice per 10 min (the market's limit is one per 5)
        return
    inv = read(MD / "inventory.json", {}).get("units") or []
    cat = read(MD / "catalog.json", {})
    must = ((read(HERE / "params.json", {}).get("shop") or {}).get("base") or {}).get("must_stock") or {}
    kind = "goods" if st.get("last") == "food" else "food"
    text = food_notice(inv, cat, must, now) if kind == "food" else goods_notice(inv, cat, now)
    if not text:                                # nothing to say of that kind now: try the other
        kind = "goods" if kind == "food" else "food"
        text = food_notice(inv, cat, must, now) if kind == "food" else goods_notice(inv, cat, now)
    if not text:
        return
    with open(MD / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"post": text[:1200], "refs": [], "as": "shop"}, ensure_ascii=False) + "\n")
    STATE.write_text(json.dumps({"t": now, "last": kind}), encoding="utf-8")
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now))} UTC shop notice ({kind}): {text[:160]}")


if __name__ == "__main__":
    main()
