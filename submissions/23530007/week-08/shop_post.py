"""Forum notice written AS THE SHOP (yms979 11-11, user: 'make those two accounts come to us').

Until now every forum post of ours was the buyer's; other shops post their stock and terms, and buyer agents read the
newest posts (.market/forum.json). This appends one truthful notice to .market/outbox.jsonl (the kit sends it; the
market allows one shop post per 5 minutes) each time watch_round.sh runs it, alternating two kinds:
  food       the foods we hold with at least food_shelf_floor of their shelf life left, units by grade now
  furniture  our best-selling goods with their A/B units in stock and the price they settled at in the last 6 h
Only what is in stock and only what the seller really does is stated.
11-13: the terms are read from the live shop policy (food_shelf_floor, warranty_hours, warranty_default_h, sell_c), not
from constants: at 18:30 the food notice still said 'grade A only, >= 80% shelf, 56% of list' while the seller sold from
45% shelf at normal prices since 17:15 (food_shelf_floor .45, must_stock {} and food_wide off).
Votes on shop posts do not enter a shop's score; the notice is there so a buyer agent that reads the forum finds us.
  --dry   print the notice that would be posted; write nothing (no outbox line, no state).
"""
import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD = HERE / ".market"
STATE = MD / "shop_post.json"
FOOD_CUTS = (0.80, 0.45)  # shelf life left for grade A, B (market rule; the same as seller_r3.FOOD_CUTS)


def read(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def live_policy():
    """The seller's live shop policy (the same merge seller_r3 reads)."""
    from policy import shop_params
    return next(x for x in shop_params() if isinstance(x, dict))


def seller_const(pattern, default=None):
    """A constant of seller_r3, read from its source (importing the seller pulls in the A2A server)."""
    try:
        m = re.search(pattern, (HERE / "seller_r3.py").read_text(encoding="utf-8"), re.M)
        return float(m.group(1)) if m else default
    except OSError:
        return default


def star_ask():
    """seller_r3.STAR_ASK."""
    return seller_const(r"^STAR_ASK\s*,\s*STAR_FLOOR\s*=\s*([0-9.]+)")


def grade_now(u, shelf, now):
    left = (u["expires_at"] - now) / 3600 / shelf
    return "A" if left >= FOOD_CUTS[0] else "B" if left >= FOOD_CUTS[1] else "C"


def food_notice(inv, cat, p, now):
    fsf = p.get("food_shelf_floor", .8)
    must = p.get("must_stock") or {}
    wide = bool((p.get("food_wide") or {}).get("on"))
    star = star_ask() if (must or wide) else None   # the star ask is the seller's price only for must_stock / food_wide
    rows = []
    for sku in {u["sku"] for u in inv if u.get("kind") == "food"}:
        c = cat.get(sku) or {}
        sh = c.get("shelf_hours")
        if not sh:
            continue
        g = Counter(grade_now(u, sh, now) for u in inv if u["sku"] == sku and u.get("expires_at")
                    and (u["expires_at"] - now) / 3600 >= fsf * sh)
        if not g:
            continue
        txt = f"{c.get('name', sku)[:30]} ({sku}) " + "·".join(f"{k} {g[k]}" for k in "ABC" if g[k]) + "개"
        if star and (wide or sku in must):
            txt += f", {round(c['list_price'] * star):,}부터"
        rows.append((sku not in must, -sum(g.values()), sku, txt))
    if not rows:
        return None
    rows.sort()
    # what pick_unit does: a stated need gets any unit that lasts it (+1 h, grade C too); no stated need gets the
    # freshest unit only if it has max(food_shelf_floor, UNSTATED_NEED) of its shelf life left
    un = max(fsf, seller_const(r"^UNSTATED_NEED\s*=\s*([0-9.]+)", 0.75))
    return (f"[yms979 상점 · 신선 식품] 필요 시간을 알려 주시면 그 시간보다 오래 가는 상품을, 알려 주지 않으시면 남은 "
            f"유통기한이 {un:.0%} 이상인 상품을 팝니다(등급은 견적에 표시). 마켓 인증서 포함, "
            + (f"호가는 정가의 {star:.0%}부터, " if star and wide else "") + f"흥정 가능. 남은 유통기한 {fsf:.0%} 이상 재고: "
            + "; ".join(r[-1] for r in rows[:8]) + ".")   # must_stock only: the star ask per row


def goods_header(p):
    wh = p["warranty_hours"]
    dh = p.get("warranty_default_h", wh)
    if dh is None:
        dh = wh
    wg = set(p.get("warranty_grades", ["A", "B"])) if wh else set()
    if not {"A", "B"} <= wg:      # the book-lead guard (shop_guard.grades_now) can cut the warranty grades: say so
        g = "·".join(x for x in "AB" if x in wg)
        head = (f"[yms979 상점 · 가구] {g}등급은 {wh:g}시간 보증, 그 밖에는 보증 없음(견적에 표시)" if g
                else "[yms979 상점 · 가구] 보증 없음(견적에 표시)")
    else:
        mid = "요청이 없으면 보증 없음(견적에 표시)" if dh == 0 else f"요청이 없으면 견적과 영수증에 적힌 {dh:g}시간 보증"
        # the seller's test is the English word (MENTIONS_WARRANTY 'warrant'), not '보증'
        ask = "보증을 요청하시면(메시지에 warranty)" if dh != wh else "보증을 요청하시면"
        head = f"[yms979 상점 · 가구] A·B등급: {ask} {wh:g}시간 보증, {mid}"
    return (head + ((". C등급은 C를 받는 구매자에게만 " + ("" if "C" in wg else "보증 없이 ") + "C로 표기해 판매")
                    if p.get("sell_c") else "") + ". 마켓 인증서 포함.")


def goods_notice(inv, cat, p, now):
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
    return (goods_header(p) + f" 최근 6시간 {len(ours)}건 체결, 중앙값 정가의 {med:.0%}. 재고: " + "; ".join(rows) + ".")


def main(dry=False):
    now = time.time()
    st = read(STATE, {})
    if not dry and now - st.get("t", 0) < 600:  # at most one notice per 10 min (the market's limit is one per 5)
        return
    inv = read(MD / "inventory.json", {}).get("units") or []
    cat = read(MD / "catalog.json", {})
    p = live_policy()
    kinds = ("food", "goods") if dry else (("goods", "food") if st.get("last") == "food" else ("food", "goods"))
    for kind in kinds:
        text = food_notice(inv, cat, p, now) if kind == "food" else goods_notice(inv, cat, p, now)
        if dry:
            print(f"[dry] shop notice ({kind}): {text[:1200] if text else '(nothing to post)'}")
            continue
        if text:                                # nothing to say of that kind now: try the other
            break
    if dry or not text:
        return
    with open(MD / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"post": text[:1200], "refs": [], "as": "shop"}, ensure_ascii=False) + "\n")
    STATE.write_text(json.dumps({"t": now, "last": kind}), encoding="utf-8")
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now))} UTC shop notice ({kind}): {text[:160]}")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv[1:])
