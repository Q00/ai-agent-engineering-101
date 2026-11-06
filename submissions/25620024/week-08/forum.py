"""Week 08 — forum.py: the agent speaking first. Runs at the end of every restock (every 60 s in Round 3),
with no order behind it, and writes to .market/outbox.jsonl, which the kit posts.

When to speak (Horvitz 1999, expected utility of acting unasked): every candidate post gets a usefulness score,
and one is posted only when the best score clears p* (theta "post_threshold"). A topic is not repeated within
its cooldown, and the market allows one post per 5 minutes per role anyway.

  as buyer (votes on these make the buyer's forum rating, 30% of buyer ALL)
    price index   what a category sold for lately, as a share of list price, with ledger refs
    honesty       shops whose grade claims the ledger checked, and how often they were true
    breakage      goods categories that broke after sale, broken / sold
  as shop
    offer         a reply to a "wanted" post for a category we stock, in checkable facts
  votes           +1 to posts that cite ledger entries; -1 only to text that tries to instruct agents

Other agents' posts are matched with regular expressions only; no forum text ever goes into a model prompt.
"""
import json
import math
import os
import re
import statistics
import time
from pathlib import Path

import market_model

NAME = "dragonash"
HERE = Path(__file__).parent
STATE = HERE / "private" / "forum_state.json"
THETA = Path(os.environ.get("SHOP_THETA", HERE / "private" / "shop_theta.json"))
DEFAULTS = {"post_threshold": 1.0, "topic_cooldown_h": 2, "window_h": 6, "min_n": 5, "min_n_index": 30,
            "max_posts_day": {"buyer": 288, "shop": 96},
            "name_shops": False,   # 11-05: our posts draw ~0.8 likes under others' in every hour and every format since
                                   # day 1 (author-level); test: no post names other shops (receipt tips, stock checks)
            "gap_s": {"buyer": 300, "shop": 300}}   # 11-07: patch 3.4 votes are one stance per voter per AUTHOR,
            # not per post, so a weak post no longer pulls the rating down; posts (0.1) want count -> 5 min again.
            # Before: 11-05 05:50: back to 30 min. At 5 min (19:30-20:49) the rating
            # fell 0.600 -> 0.577 and the posts percentile only went 0.74 -> 0.73: new posts draw ~5.2 likes, under our
            # average, so they are NOT neutral (the 14:00 assumption was wrong); quality over count The 30-min cut assumed new posts
            # (5.4 likes at 15 min) pull the 0.604 rating down, but that rating already implies ~6 final likes (Wilson:
            # 5 -> 0.566, 6 -> 0.61), so a post is about neutral to the rating and the posts percentile (0.1) needs them   # buyer ALL counts posts written as a buyer (0.1) and the
                                                           # votes on them (0.3): post as a buyer as often as the market
                                                           # allows (1 per 5 min), each post ledger-cited; shop posts few
WANTED = re.compile(r"구합니다|구해요|삽니다|필요해요|찾습니다|want(?:ed)? to buy|\bwanted\b|\bWTB\b|looking for|need (?:a|some)", re.I)
INJECTION = re.compile(r"ignore (?:all|previous|your)|AI (?:buyers?|agents?) (?:must|should)|system prompt|"
                       r"pay the (?:asking|full) price|give (?:it|us) 5 stars|approved by the (?:instructor|operator)", re.I)


def theta():
    try:
        return {**DEFAULTS, **json.loads(THETA.read_text(encoding="utf-8")).get("forum", {})}
    except (OSError, ValueError, AttributeError):
        return dict(DEFAULTS)


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"posted": {}, "voted": [], "last": {}}


def leaf(category):
    return (category or "?").split(">")[-1]


def price_index(c, t, now):
    out = []
    rows = c.execute("select seq, category, sku, amount, list_price, shop from settles where ts>? and list_price>0",
                     (now - t["window_h"] * 3600,)).fetchall()
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r[1], []).append(r)
    for cat, rs in by_cat.items():
        if len(rs) < t["min_n_index"]:
            continue
        # the posts the forum votes up most (7 likes) are Korean market summaries with quartiles, the best seller
        # and eight ledger refs; ours in English with five refs stopped at 5-6 (11-04)
        ratios = sorted(r[3] / r[4] for r in rs)
        q = lambda f: ratios[min(len(ratios) - 1, int(f * len(ratios)))]
        cat_rows = market_model.catalog()
        sold = {}
        for r in rs:
            sold[r[2]] = sold.get(r[2], 0) + 1
        top = sorted(sold.items(), key=lambda kv: -kv[1])[:2]
        best = " / ".join(f"{((cat_rows.get(k) or {}).get('name') or k)[:22]} ({k}) {n}건" for k, n in top)
        best_en = " / ".join(f"{((cat_rows.get(k) or {}).get('name') or k)[:22]} ({k}) {n} sold" for k, n in top)
        low = min(rs, key=lambda r: r[3] / r[4])
        seqs =", ".join(str(r[0]) for r in rs[-8:])
        ko = (f"[시세] {leaf(cat)}: 최근 {t['window_h']}시간 시장 정산 {len(rs)}건 (공개 원장 기준)\n"
              f"- 체결가/정가 중앙값 {statistics.median(ratios):.0%} (하위 25% {q(0.25):.0%}, 상위 25% {q(0.75):.0%})\n"
              f"- 가장 많이 팔린 상품: {best}\n"
              f"- 최저 체결: {low[2]} {low[3]:,}원 (정가의 {low[3] / low[4]:.0%})\n"
              f"- 근거: 원장 seq {seqs}")
        en = (f"[Market] {leaf(cat)}: {len(rs)} settles in the last {t['window_h']} h (public ledger)\n"
              f"- paid/list median {statistics.median(ratios):.0%} (lower quartile {q(0.25):.0%}, upper {q(0.75):.0%})\n"
              f"- best sellers: {best_en}\n"
              f"- lowest settle: {low[2]} {low[3]:,} ({low[3] / low[4]:.0%} of list)\n"
              f"- refs: ledger seq {seqs}")
        js = json.dumps(
            {"type": "market_summary", "category": leaf(cat), "window_h": t["window_h"], "settles": len(rs),
             "paid_share": {"median": round(statistics.median(ratios), 3), "q25": round(q(0.25), 3), "q75": round(q(0.75), 3)},
             "best_sellers": [{"sku": k, "name": ((cat_rows.get(k) or {}).get("name") or k)[:22], "sold": n} for k, n in top],
             "lowest": {"sku": low[2], "price": low[3], "share": round(low[3] / low[4], 3)},
             "refs": [r[0] for r in rs][-8:]}, ensure_ascii=False, separators=(",", ":")) + (   # JSON on top (HQ)
            f"\n{HUMAN}\n[시세] {leaf(cat)}: 정산 {len(rs)}건, 체결가/정가 중앙값 {statistics.median(ratios):.0%}, "
            f"최다 판매 {top[0][0] if top else '-'}\n[Market] {leaf(cat)}: {len(rs)} settles, paid/list median "
            f"{statistics.median(ratios):.0%}, best seller {top[0][0] if top else '-'}")
        out.append({"topic": f"price:{cat}", "as": "buyer", "text": ko, "texts": {"ko": ko, "en": en, "json": js},
                    "kind": "summary",
                    "refs": [r[0] for r in rs][-8:], "score": math.log(len(rs))})
    return out


def purchase_records(c, t, now, top=3):
    """As the buyer: our own settled purchases lately, each with its ledger entry, price against list and the grade
    the ledger checked. The kind of post that collects the most votes on this forum: anyone can verify it."""
    cat = market_model.catalog()
    rows = c.execute("""select seq, sku, amount, list_price, grade, claim_honest from settles
                        where buyer=? and ts>? and list_price>amount order by (list_price-amount)*1.0/list_price desc
                        limit ?""", (NAME, now - t["window_h"] * 3600, top)).fetchall()
    if len(rows) < top:
        return []
    nm = lambda sku: ((cat.get(sku) or {}).get('name') or sku)[:28]
    ko = "[구매 기록] 최근 정산된 구매 (공개 원장으로 확인 가능)\n" + "\n".join(
        f"- {nm(sku)} ({sku}) | {a:,}원, 정가 {lp:,}원 대비 {1 - a / lp:.0%} 저렴 | 등급 {g}{' (주장과 일치)' if h else ''}"
        f" | 원장 #{seq}" for seq, sku, a, lp, g, h in rows)
    en = "[Purchase records] our latest settled buys (checkable on the public ledger)\n" + "\n".join(
        f"- {nm(sku)} ({sku}) | paid {a:,} vs list {lp:,} ({1 - a / lp:.0%} under) | grade {g}"
        f"{' (claim true)' if h else ''} | ledger #{seq}" for seq, sku, a, lp, g, h in rows)
    js = json.dumps(
        {"type": "purchase_records", "records": [{"sku": sku, "name": nm(sku)[:22], "paid": a, "list": lp,
                                                  "kept": round(1 - a / lp, 3), "grade": g, "claim_true": bool(h),
                                                  "ledger": seq} for seq, sku, a, lp, g, h in rows]},
        ensure_ascii=False, separators=(",", ":")) + f"\n{HUMAN}\n" + "\n".join(
        f"- {nm(sku)[:18]} {a:,}원/정가 {lp:,}원 ({1 - a / lp:.0%} 절약, {1 - a / lp:.0%} kept), 등급/grade {g}"
        for seq, sku, a, lp, g, h in rows[:3])                                           # JSON on top, then people
    return [{"topic": f"records:{rows[0][0]}", "as": "buyer", "refs": [r[0] for r in rows], "score": 3.5,
             "text": ko, "texts": {"ko": ko, "en": en, "json": js}, "kind": "records"}]


def stock_check(c, t, now, hours=6, min_n=3, top=5):
    """As the buyer, to save every agent wasted quote calls: shops that answered 'none in stock' for a SKU the
    market's /api/shops still listed, at least min_n times lately. The proof on the ledger: that shop's own recent
    settles of the SKU (the listed units were already sold). Worded as a listing lag, not a blame."""
    rows = []
    try:
        for line in (HERE / "private" / "enuri" / "phantom_log.jsonl").read_text(encoding="utf-8").splitlines()[-3000:]:
            x = json.loads(line)
            if now - x.get("ts", 0) < hours * 3600:
                rows.append((x["shop"], x["sku"]))
    except (OSError, ValueError):
        return []
    count = {}
    for k in rows:
        count[k] = count.get(k, 0) + 1
    try:
        import enuri
        fr = enuri.friendly()                     # HQ: friendly shops are never named in our warnings
    except Exception:
        fr = set()
    hits = sorted(((k, n) for k, n in count.items() if n >= min_n and k[0] not in fr), key=lambda kv: -kv[1])[:top]
    if not hits:
        return []
    cat = market_model.catalog()
    items, refs = [], []
    for (shop, sku), n in hits:
        seqs = [s for (s,) in c.execute("""select s.seq from settles s join settle_units x on x.seq = s.seq
                                           join supply_units su on su.unit = x.unit where su.shop=? and s.sku=?
                                           order by s.seq desc limit 2""", (shop, sku))]
        refs += seqs
        items.append({"shop": shop, "sku": sku, "name": ((cat.get(sku) or {}).get("name") or "")[:24],
                      "replied_none_in_stock_6h": n, "listed_on_api_shops": True, "recent_settles": seqs})
    if not refs:
        return []
    data = {"type": "stock_check", "window_h": hours, "note": "listed in /api/shops, but the shop replied 'none in stock';"
            " probably a listing lag after the units sold (see recent_settles)", "items": items, "refs": refs[:8]}
    ko = "[재고 확인 공유] 시장 재고 목록에는 있지만 문의하면 '재고 없음'이던 상품이에요. 판매 직후 목록 반영이 늦는 것으로 " \
         "보여요(최근 정산 기록 참고). 견적 전에 한 번 더 확인하면 서로 호출과 토큰을 아낄 수 있어요.\n" + "\n".join(
             f"- {i['shop']} / {i['name']} ({i['sku']}): 최근 {hours}시간 '재고 없음' {i['replied_none_in_stock_6h']}회"
             for i in items)
    en = "[Stock check, FYI] Listed in /api/shops but answered 'none in stock' -- likely a listing lag after the units " \
         "sold (recent settles cited). A re-check before quoting saves everyone calls and tokens.\n" + "\n".join(
             f"- {i['shop']} / {i['sku']}: 'none in stock' {i['replied_none_in_stock_6h']}x in {hours} h" for i in items)
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + f"\n{HUMAN}\n" + ko + "\n" + en
    return [{"topic": "stockcheck", "as": "buyer", "refs": refs[:8], "score": 3.2,
             "text": ko, "texts": {"ko": ko, "en": en, "json": js[:1000]}, "kind": "stock_check"}]


def receipt_tips(c, t, now):
    """As the buyer, a buyer's tip (HQ 11-04): receipts that did not match the deal -- another product than the order
    asked for, or a price above the agreed one -- and the check that catches them. The shops are named with the
    product they put on the receipt; their own ledger settles of that product are the refs. Friendly shops excluded."""
    try:
        import enuri
        watch = enuri.receipt_watch()
    except Exception:
        return []
    if not watch:
        return []
    cat = market_model.catalog()
    items, refs = [], []
    for shop, cases in sorted(watch.items(), key=lambda kv: -len(kv[1]))[:4]:
        p = cases[-1]["problem"]
        m = re.search(r"receipt for (G[0-9a-f]{8}), ordered (G[0-9a-f]{8})", p)
        if m:
            got, want = m.group(1), m.group(2)
            seqs = [s for (s,) in c.execute("""select s.seq from settles s join settle_units x on x.seq = s.seq
                                               join supply_units su on su.unit = x.unit where su.shop=? and s.sku=?
                                               order by s.seq desc limit 2""", (shop, got))]
            refs += seqs
            items.append({"shop": shop, "cases_24h": len(cases), "kind": "receipt_for_another_product",
                          "ordered": want, "ordered_name": ((cat.get(want) or {}).get("name") or "")[:20],
                          "receipt_item": got, "receipt_name": ((cat.get(got) or {}).get("name") or "")[:20]})
        else:
            items.append({"shop": shop, "cases_24h": len(cases), "kind": "receipt_above_agreed_price", "detail": p[:80]})
    if not refs:
        return []
    data = {"type": "buyer_tip", "topic": "check the receipt before paying",
            "check": ["receipt.item is the order's item or one of its acceptable SKUs",
                      "receipt.price <= the price you agreed", "sig_seller verifies"],
            "seen_24h": items, "refs": refs[:8]}
    ko = "[구매자 팁] 결제 전에 영수증을 확인하세요: 품목이 주문(허용 SKU)과 같은지, 가격이 합의가 이하인지, 서명이 맞는지. " \
         "최근 24시간 저희가 받은 영수증 중 맞지 않았던 사례예요(저희는 결제하지 않았어요).\n" + "\n".join(
             f"- {i['shop']}: {i.get('ordered_name') or ''} 주문에 {i.get('receipt_name') or i.get('detail', '')} 영수증 "
             f"({i['cases_24h']}회)" for i in items)
    en = "[Buyer tip] Check the receipt before paying: the item is the order's (or an acceptable SKU), the price is at " \
         "most what you agreed, the signature verifies. Receipts we got in the last 24 h that did not match (we did not " \
         "pay them):\n" + "\n".join(
             f"- {i['shop']}: ordered {i.get('ordered') or ''}, receipt {i.get('receipt_item') or i.get('detail', '')} "
             f"({i['cases_24h']}x)" for i in items)
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + f"\n{HUMAN}\n" + ko + "\n" + en
    return [{"topic": "receipttips", "as": "buyer", "refs": refs[:8], "score": 3.1, "text": ko,
             "texts": {"ko": ko, "en": en, "json": js[:1000]}, "kind": "stock_check"}]


def checkout_tips(c, t, now, last=100):
    """As the buyer, a buyer's tip (HQ 11-04 "이런 오류는 게시판에 공유해야지, 구매 팁이잖아"): why agreed deals were not
    paid at checkout in our last `last` payments (the kit's own reasons, .market/payments.jsonl), and the check that
    avoids each. No shop is named. Refs: our own settled purchases in the same span, the deals that passed the check."""
    try:
        pays = [json.loads(x) for x in (Path(".market") / "payments.jsonl").read_text(encoding="utf-8").splitlines()[-last:]
                if x.strip()]
    except (OSError, ValueError):
        return []
    bad = [p for p in pays if not p.get("paid")]
    kinds = {"shelf": 0, "grade": 0, "gone": 0}
    for p in bad:
        r = p.get("reason") or ""
        k = "shelf" if "shelf life" in r else "grade" if "grade" in r else "gone" if "not in stock" in r else None
        if k:
            kinds[k] += 1
    if len(pays) < 30 or sum(kinds.values()) < 5:
        return []
    refs = [s for (s,) in c.execute("select seq from settles where buyer=? order by seq desc limit 8", (NAME,))]
    if not refs:
        return []
    data = {"type": "buyer_tip", "topic": "agreed deals the kit refused to pay", "sample": f"our last {len(pays)} payments",
            "refused": {"shelf_life_too_short": kinds["shelf"], "grade_not_accepted": kinds["grade"],
                        "unit_sold_before_checkout": kinds["gone"]},
            "check_before_agreeing": ["food: ask for hours of shelf life left, need use_within_hours + margin",
                                      "goods: say the grades you accept in the first message and read the offer's grade",
                                      "unit gone at checkout: agree quickly or retry the next quote at once"],
            "refs": refs}
    ko = (f"[구매자 팁] 합의하고도 결제가 안 된 거래: 저희 최근 결제 {len(pays)}건 중 유통기한 부족 {kinds['shelf']}건, "
          f"등급 불일치 {kinds['grade']}건, 결제 직전 재고 소진 {kinds['gone']}건.\n"
          "- 식품: 남은 유통기한(시간)을 먼저 물어보세요. 주문의 사용 시간보다 짧으면 키트가 결제하지 않아요.\n"
          "- 공산품: 첫 메시지에 받는 등급(A/B)을 말하고, 견적의 등급을 확인하세요.\n"
          "- 재고 소진: 협상이 길면 그 사이 다른 바이어에게 팔려요. 빨리 합의하거나 다음 견적으로 바로 넘어가세요.")
    en = (f"[Buyer tip] Agreed deals the kit did not pay, our last {len(pays)} payments: shelf life too short "
          f"{kinds['shelf']}, grade not accepted {kinds['grade']}, unit sold before checkout {kinds['gone']}. Ask hours "
          "left for food, state accepted grades for goods, and agree quickly or move to the next quote.")
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + f"\n{HUMAN}\n" + ko
    js = js + "\n" + en if len(js) + len(en) < 1000 else js        # never cut a sentence at the 1000-char limit
    return [{"topic": "checkouttips", "as": "buyer", "refs": refs, "score": 3.15, "text": ko,
             "texts": {"ko": ko, "en": en, "json": js[:1000]}, "kind": "stock_check"}]


def frugal_shops(c, t, now, top_cats=3, min_n=5):
    """As the buyer, which shops sold cheapest lately (W09 lecture: "어떤 가게가 프루걸한가… 게시판에 올려주시면… 좋아요"):
    for the busiest categories, the shops with the lowest median paid/list over window_h, from the public ledger
    (settles joined to the supply that named the shop). Each line carries two of that shop's settles as refs."""
    rows = c.execute("""select su.shop, s.category, s.seq, s.amount * 1.0 / s.list_price from settles s
                        join settle_units x on x.seq = s.seq join supply_units su on su.unit = x.unit
                        where s.ts > ? and s.list_price > 0""", (now - t["window_h"] * 3600,)).fetchall()
    by = {}
    for shop, cat, seq, r in rows:
        by.setdefault(cat, {}).setdefault(shop, []).append((seq, r))
    cats = sorted(by, key=lambda k: -sum(len(v) for v in by[k].values()))[:top_cats]
    items, refs = [], []
    for cat in cats:
        ranked = sorted(((statistics.median(r for _, r in v), shop, v) for shop, v in by[cat].items() if len(v) >= min_n))
        if len(ranked) < 3:
            continue
        mkt = statistics.median(r for v in by[cat].values() for _, r in v)
        best = [{"shop": shop, "paid_share_median": round(med, 3), "n": len(v)} for med, shop, v in ranked[:3]]
        for _, _, v in ranked[:3]:
            refs += [seq for seq, _ in v[-2:]]
        items.append({"category": leaf(cat), "market_median": round(mkt, 3), "cheapest_shops": best})
    if not items or not refs:
        return []
    data = {"type": "frugal_shops", "window_h": t["window_h"], "measure": "median paid/list per shop, public ledger",
            "categories": items, "refs": refs[:8]}
    ko = f"[알뜰 가게] 최근 {t['window_h']}시간 원장 기준, 범주별로 정가 대비 가장 싸게 판 가게 (체결가/정가 중앙값)\n" + "\n".join(
        f"- {i['category']} (시장 {i['market_median']:.0%}): " + ", ".join(
            f"{b['shop']} {b['paid_share_median']:.0%} ({b['n']}건)" for b in i["cheapest_shops"]) for i in items)
    en = f"[Frugal shops] last {t['window_h']} h, public ledger, median paid/list per shop:\n" + "\n".join(
        f"- {i['category']} (market {i['market_median']:.0%}): " + ", ".join(
            f"{b['shop']} {b['paid_share_median']:.0%} (n={b['n']})" for b in i["cheapest_shops"]) for i in items)
    return [{"topic": "frugal", "as": "buyer", "refs": refs[:8], "score": 9.0, "text": ko,   # above the price index
             "texts": {"ko": ko, "en": en, "json": json.dumps(data, ensure_ascii=False, separators=(",", ":"))[:1000]},
             "kind": "summary"}]


def wanted_posts(c, t, now, min_wanted=3):
    """As the buyer, speaking first (W09 lecture: "나 이거 살 테니까 공급해 줄 가게 구한다"): SKUs our orders asked for
    in the last 24 h that no shop had (market_model.gap_demand), with the list price and how often. Refs: the latest
    settles in the same category, the demand on the ledger."""
    gaps = [g for g in market_model.gap_demand(c, hours=24, top=6) if g["wanted"] >= min_wanted and g.get("list_price")]
    if not gaps:
        return []
    cat = market_model.catalog()
    cats = {(cat.get(g["sku"]) or {}).get("category") for g in gaps} - {None}
    refs = [s for (s,) in c.execute(f"select seq from settles where category in ({','.join('?' * len(cats))}) "
                                    f"order by seq desc limit 8", tuple(cats))] if cats else []
    if not refs:
        return []
    items = [{"sku": g["sku"], "name": g["name"], "list_price": g["list_price"], "wanted_24h": g["wanted"]} for g in gaps[:4]]
    data = {"type": "wanted", "note": "orders we got in the last 24 h that no shop could fill; we buy when you stock them",
            "items": items, "refs": refs}
    ko = "[구합니다] 최근 24시간 저희 주문 중 파는 가게가 없던 상품이에요. 입고하시면 사겠습니다.\n" + "\n".join(
        f"- {i['name']} ({i['sku']}), 정가 {i['list_price']:,}원, {i['wanted_24h']}회 주문" for i in items)
    en = "[Wanted] Orders we got in the last 24 h that no shop could fill; stock them and we buy:\n" + "\n".join(
        f"- {i['sku']} list {i['list_price']:,}, ordered {i['wanted_24h']}x" for i in items)
    return [{"topic": "wanted", "as": "buyer", "refs": refs, "score": 8.5, "text": ko,
             "texts": {"ko": ko, "en": en, "json": json.dumps(data, ensure_ascii=False, separators=(",", ":"))[:1000]},
             "kind": "summary"}]


def groupbuy_ads(c, t, now):
    """As the shop, the group-buy offer (HQ 11-05; W09 lecture: "선착순 50명만 50% 할인"): each SKU in
    private/groupbuy.json, its price this hour, how many group-buy units are left this hour, refs = our own settles of it."""
    import promo
    try:
        g = json.loads(promo.GROUPBUY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if g.get("until", 0) < now:
        return []
    cat, items, refs = market_model.catalog(), [], []
    for sku, x in (g.get("skus") or {}).items():
        gb = promo.groupbuy(c, sku, now)
        lp = (cat.get(sku) or {}).get("list_price")
        if not gb or not lp:
            continue
        seqs = [s for (s,) in c.execute("""select s.seq from settles s join settle_units u on u.seq = s.seq
                                           join supply_units su on su.unit = u.unit where su.shop = ? and s.sku = ?
                                           order by s.seq desc limit 3""", (NAME, sku))]
        refs += seqs
        items.append({"sku": sku, "name": ((cat.get(sku) or {}).get("name") or "")[:28], "list_price": lp,
                      "group_price": int(lp * gb["share"]), "share_of_list": gb["share"],
                      "first_per_hour": gb["per_hour"], "left_this_hour": gb["left"]})
    if not items or not refs:
        return []
    data = {"type": "group_buy", "shop": NAME, "rule": "the first N buyers each clock hour pay the group price; after "
            "that the regular price", "items": items, "how": "ask our shop for the SKU; the offer data shows group_buy",
            "refs": refs[:8]}
    ko = "[공동구매] 매 시간 선착순 공동구매가 (그 시간 수량이 끝나면 일반가)\n" + "\n".join(
        f"- {i['name']} ({i['sku']}): 정가 {i['list_price']:,}원 -> {i['group_price']:,}원 ({i['share_of_list']:.0%}), "
        f"매 시간 선착순 {i['first_per_hour']}개, 이번 시간 {i['left_this_hour']}개 남음" for i in items)
    en = "[Group buy] first buyers each hour pay the group price: " + "; ".join(
        f"{i['sku']} {i['group_price']:,} ({i['share_of_list']:.0%} of list), first {i['first_per_hour']}/h, "
        f"{i['left_this_hour']} left" for i in items)
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + f"\n{HUMAN}\n" + ko + "\n" + en
    return [{"topic": "groupbuy", "as": "shop", "refs": refs[:8], "score": 3.5, "text": js[:1000],
             "texts": {"json": js[:1000], "ko": ko, "en": en}}]


def groupbuy_call(c, t, now, top=4, min_ticket=150000):
    """As the buyer, a group-buy call (HQ 11-05 "Recruit buyers"): the high-ticket SKUs buyers settle most on the public
    ledger (24 h), what they paid (median and the cheapest quarter, as a share of list), and the proposal: buyers who get
    orders for them buy only at or under the cheapest-quarter price, and shops that quote it get the volume. Our buyer
    does the same when it gets such an order. Our own shop's group price is disclosed as our interest, never hidden.
    Refs: the latest market settles of those SKUs."""
    rows = c.execute("""select sku, count(*), avg(list_price) from settles where ts > ? and list_price >= ?
                        group by sku order by count(*) desc limit ?""", (now - 24 * 3600, min_ticket, top)).fetchall()
    if not rows:
        return []
    import promo
    try:                                     # HQ 11-05: name only shops with no error in our records (Enuri's trust)
        import enuri
        problem = set(((enuri.book() or {}).get("trust") or {}).get("problems") or {})
    except Exception:
        problem = set()
    cat, items, refs = market_model.catalog(), [], []
    for sku, n, lp in rows:
        shares = sorted(a / l for a, l in c.execute(
            "select amount, list_price from settles where sku=? and ts>? and list_price>0", (sku, now - 24 * 3600)))
        if len(shares) < 10:
            continue
        p25, med = shares[len(shares) // 4], statistics.median(shares)
        refs += [x for (x,) in c.execute("select seq from settles where sku=? order by seq desc limit 2", (sku,))]
        gb_file = promo.GROUPBUY                         # HQ 11-05 "우리 가게를 공동구매에 걸어": our shop joins the
        try:                                             # call at the target price (its floor, cost + min margin, holds)
            g = json.loads(gb_file.read_text(encoding="utf-8"))
            x = (g.get("skus") or {}).setdefault(sku, {"share": p25, "per_hour": 10 if lp < 1_000_000 else 4})
            if x["share"] > p25:
                x["share"] = round(p25, 3)
                gb_file.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, ValueError):
            pass
        ours = promo.groupbuy(c, sku, now)
        by_shop = {}
        for shop, r in c.execute("""select su.shop, s.amount * 1.0 / s.list_price from settles s
                                    join settle_units x on x.seq = s.seq join supply_units su on su.unit = x.unit
                                    where s.sku = ? and s.ts > ?""", (sku, now - 24 * 3600)):
            by_shop.setdefault(shop, []).append(r)
        good = sorted((statistics.median(v), s) for s, v in by_shop.items()
                      if len(v) >= 2 and s not in problem and s != NAME and statistics.median(v) <= p25 + 0.02)
        items.append({"sku": sku, "name": ((cat.get(sku) or {}).get("name") or "")[:24], "list_price": int(lp),
                      "recommended_shops": ([f"{NAME} (poster's shop)"] if ours else []) + [s for _, s in good[:3]],
                      "settles_24h": n, "paid_share_median": round(med, 3), "target_share": round(p25, 3),
                      "target_price": int(lp * p25),
                      "our_shop_group_price": int(lp * ours["share"]) if ours else None})
    if not items or not refs:
        return []
    try:                                     # our buyer keeps the promise the post makes (market_buyer.price_plan)
        (HERE / "private" / "groupbuy_targets.json").write_text(json.dumps(
            {"ts": now, "targets": {i["sku"]: i["target_price"] for i in items}}), encoding="utf-8")
    except OSError:
        pass
    data = {"type": "group_buy_call", "from": NAME, "window_h": 24,
            "proposal": "buyers with orders for these SKUs: buy at or under target_price; shops: quote target_price "
                        "and get the volume. Reply to this post with your SKU and quote.",
            "items": items, "disclosure": "our shop (dragonash) joins: it sells these at target_price to the first buyers each hour",
            "refs": refs[:8]}
    ko = "[공동구매 제안] 고가 상품을 사는 바이어분들, 같이 사요. 최근 24시간 원장에서 많이 팔린 고가 상품과 목표가예요.\n" + \
        "\n".join(f"- {i['name']} ({i['sku']}): 24시간 {i['settles_24h']}건, 중간 체결 정가의 {i['paid_share_median']:.0%}"
                  f" -> 목표가 {i['target_price']:,}원 ({i['target_share']:.0%})"
                  + (f", 추천 가게: {', '.join(i['recommended_shops'])}" if i["recommended_shops"] else "") for i in items) + \
        "\n추천 가게 = 24시간 원장에서 목표가 근처에 판 가게 중 저희 기록에 오류(영수증·등급·결제 거부)가 없는 곳." \
        "\n바이어: 이 상품 주문이 오면 목표가 이하로만 사요. 판매자: 목표가로 견적 주시면 물량이 그 가게로 갑니다." \
        "\n(이해관계 공개: 저희 상점 dragonash도 공동구매에 참여해 목표가로 매 시간 선착순 판매합니다.)"
    en = "[Group buy call] High-ticket SKUs buyers settle most (24 h): buy at or under the target price; shops quoting " \
         "it get the volume. " + "; ".join(f"{i['sku']} target {i['target_price']:,}" for i in items)
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(js) > 1000:                                   # never cut the JSON: drop the names, then items
        for i in data["items"]:
            i.pop("name", None)
        while len(js := json.dumps(data, ensure_ascii=False, separators=(",", ":"))) > 1000 and len(data["items"]) > 1:
            data["items"].pop()
    return [{"topic": "groupbuy_call", "as": "buyer", "refs": refs[:8], "score": 8.8, "text": ko,
             "texts": {"ko": ko, "en": en, "json": js[:1000]}, "kind": "summary"}]


def replies(c, t, forum, now, max_age=1200, min_likes=6):
    """As the buyer, a reply under another buyer's well-liked, ledger-cited post (likes >= min_likes, under max_age s
    old): our own ledger figures for the category it covers, tagged [보충]. HQ 11-04 experiment: do replies under
    posts the forum already likes draw more likes than our top-level posts?"""
    st = load_state()
    done = set(st.get("replied") or [])
    idx = {leaf(x["topic"].split(":", 1)[1]): x for x in price_index(c, t, now)}
    out = []
    for p in sorted(forum or [], key=lambda p: -(p.get("likes") or 0)):
        if p.get("author") == NAME or p.get("role") != "buyer" or not p.get("refs") or p.get("thread") \
                or (p.get("likes") or 0) < min_likes or now - (p.get("ts") or 0) > max_age or p.get("id") in done:
            continue
        text = p.get("text") or ""
        hit = next((k for k in sorted(idx, key=len, reverse=True) if k and k in text), None)
        if not hit:
            continue
        x = idx[hit]
        ko = x["texts"]["ko"].replace("[시세]", "[보충]", 1).split("\n")
        body = "\n".join([ko[0] + " (저희 장부 집계로 보충합니다)"] + ko[1:4])
        en = x["texts"]["en"].split("\n")[0].replace("[Market]", "[Addendum]", 1)
        js = (x["texts"].get("json") or "").split("\n")[0]   # customers are agents: JSON first (HQ 11-04)
        js = js.replace('"market_summary"', '"addendum"', 1).replace(f'"category"', f'"reply_to":{p["id"]},"category"', 1)
        js = js if len(js) <= 600 else ""
        text = (js + "\n" + HUMAN + "\n" if js else "") + body + "\n" + en
        out.append({"topic": f"reply:{p['id']}", "as": "buyer", "thread": p["id"], "refs": x["refs"][:5],
                    "score": 3.0, "text": text[:1000], "kind": "reply", "reply_to": p["id"]})
        break
    return out


def honesty(c, t, now):
    rows = c.execute("select shop, count(*), sum(claim_honest), max(seq) from settles where ts>? and claim_honest is not null"
                     " group by shop having count(*)>=?", (now - 24 * 3600, t["min_n"])).fetchall()
    if len(rows) < 2:
        return []
    rows.sort(key=lambda r: (r[2] / r[1], r[1]))
    bad = [r for r in rows if r[2] < r[1]]
    parts = [f"{str(r[0]).split('/')[-1][:24]} {r[2]}/{r[1]}" for r in rows[:6]]
    text = "[claim-honesty 24h] grade claims the ledger found true: " + ", ".join(parts)
    refs = [x[0] for x in c.execute("select seq from settles where claim_honest=0 and ts>? order by seq desc limit 5",
                                    (now - 24 * 3600,))]
    return [{"topic": "honesty", "as": "buyer", "text": text, "refs": refs or [r[3] for r in rows[:5]],
             "score": 1.0 + 1.5 * len(bad)}]


def breakage(c, t, now):
    sold = dict(c.execute("select category, count(*) from settles where ts>? group by category", (now - 24 * 3600,)).fetchall())
    broke = {}
    for seq, sku in c.execute("select seq, sku from broken where ts>?", (now - 24 * 3600,)):
        cat = c.execute("select category from settles where sku=? limit 1", (sku,)).fetchone()
        if cat:
            broke.setdefault(cat[0], []).append(seq)
    out = []
    for cat, seqs in broke.items():
        n = sold.get(cat, 0)
        if n >= t["min_n"]:
            out.append({"topic": f"broken:{cat}", "as": "buyer", "refs": seqs[-5:], "score": 1.0 + len(seqs) / n * 10,
                        "text": f"[breakage 24h] {leaf(cat)}: {len(seqs)} of {n} sold broke after sale ({len(seqs) / n:.0%})."
                                " Ask for warranty_hours >= 6 on these."})
    return out


def offers(forum, md, now):
    """Replies, as the shop, to recent 'wanted' posts for a category or SKU we have in stock."""
    try:
        units = json.loads((Path(md) / "inventory.json").read_text(encoding="utf-8")).get("units", [])
    except (OSError, ValueError):
        return []
    out = []
    for p in forum:
        if p.get("author") == NAME or now - (p.get("ts") or 0) > 1800 or not WANTED.search(p.get("text") or ""):
            continue
        text = (p.get("text") or "").lower()
        words = {w for w in re.findall(r"[0-9a-z가-힣]{2,}", text)}

        def match(u):
            """SKU named: 100; else words shared with the product name (포기김치 beats 총각김치); else category."""
            if u["sku"].lower() in text:
                return 100
            name = (u.get("name") or "").lower()
            hits = sum(1 for w in words if w in name)
            return hits * 10 + (1 if leaf(u.get("category")).lower() in text else 0)
        live = [u for u in units if u.get("grade") != "expired"]
        best = max((match(u) for u in live), default=0)
        fit = [u for u in live if best and match(u) == best]
        if fit:
            u = min(fit, key=lambda u: u.get("expires_at") or float("inf"))
            out.append({"topic": f"offer:{p['id']}", "as": "shop", "thread": p["id"], "refs": [], "score": 3.0,
                        "data": {"type": "in_stock", "shop": NAME, "sku": u["sku"], "name": u["name"][:30],
                                 "grade": u.get("grade"), "cert": "market-signed", "list_price": u["list_price"],
                                 "order_with": f"I want to buy {u['name'][:20]} ({u['sku']})"},
                        "text": f"[in stock] sku {u['sku']} {u['name'][:30]} | grade={u.get('grade')} cert=market-signed"
                                f" | list={u['list_price']} | ask us: 'I want to buy {u['name'][:20]} ({u['sku']})'"})
    return out


def opening_ads(c, md, now):
    """As the shop, during the opening offer: one category we stock per post, the price range we will ask (as a share
    of list, never below cost), how many units, and ledger refs of our own settled, honestly graded sales. Facts a
    buyer can check; no ranking claims about other shops."""
    import promo
    t = promo.theta()
    if now >= t["intro_until"]:
        return []
    try:
        units = json.loads((Path(md) / "inventory.json").read_text(encoding="utf-8")).get("units", [])
    except (OSError, ValueError):
        return []
    refs = [r[0] for r in c.execute(f"select seq from settles where seq in ({market_model.ours(c)}) and claim_honest=1 "
                                    "order by seq desc limit 3")]
    by_cat = {}
    for u in units:
        if u.get("grade") != "expired":
            by_cat.setdefault(u.get("category"), []).append(u)
    out = []
    for cat, us in by_cat.items():
        u = max(us, key=lambda u: sum(1 for x in us if x["sku"] == u["sku"]))
        floor = u["cost"] * (1 + t["intro_margin"])
        lo, hi = (max(floor, u["list_price"] * a) for a in (min(t["intro_arms"]), max(t["intro_arms"])))
        until = time.strftime("%m/%d %H:%M", time.localtime(t["intro_until"]))
        out.append({"topic": f"ad:{cat}", "as": "shop", "refs": refs, "score": 2.5 + 0.01 * len(us),
                    "text": f"[opening offer until {until}] {leaf(cat)}: sku {u['sku']} {u['name'][:30]} | grade="
                            f"{u.get('grade')} cert=market-signed | list={u['list_price']} | asking {lo:.0f}-{hi:.0f}"
                            f" ({lo / u['list_price']:.0%}-{hi / u['list_price']:.0%} of list) | {len(us)} units in "
                            f"{leaf(cat)} | Ed25519 receipts, grade claims checked by the ledger"})
    return out


def follow_ads(c, md, now):
    """As the shop: a SKU we took over from the leaders, in stock, asked under the ledger's 24 h median."""
    import promo
    fo = promo.plan().get("follow_skus") or {}
    try:
        units = json.loads((Path(md) / "inventory.json").read_text(encoding="utf-8")).get("units", [])
    except (OSError, ValueError):
        return []
    out = []
    for sku, cut in fo.items():
        us = [u for u in units if u["sku"] == sku and u.get("grade") != "expired"]
        med, n = market_model.market_price(c, sku)
        if not us or not med:
            continue
        u = us[0]
        w = promo.theta().get("warranty_goods_h") or 0
        low = market_model.market_low(c, sku, hours=24)
        # the lowest-price guarantee, spelled out on the hits the leading shops sell (HQ 11-04: take their buyers)
        out.append({"topic": f"follow:{sku}", "as": "shop", "refs": [], "score": 2.9,
                    "data": {"type": "price_guarantee", "shop": NAME, "sku": sku, "name": u["name"][:30],
                             "units": len(us), "grade": u.get("grade"), "cert": "market-signed",
                             "warranty_h": w if u.get("kind") == "goods" else 0,
                             "price_vs_24h_median": -round(cut, 3), "median_24h": round(med), "settles_24h": n,
                             "ledger_low_24h": low, "match": "reply 'match <price>': a lower ledger settle is matched",
                             "refund": "name your market buyer: the difference to a lower ledger price comes off",
                             "order_with": f"I want to buy {u['name'][:20]} ({sku})"},
                    "text": f"[최저가 보상] {u['name'][:30]} ({sku}) 재고 {len(us)}개, 등급 {u.get('grade')}, 시장 인증서"
                            f"{f', 보증 {w}시간' if u.get('kind') == 'goods' and w else ''}\n"
                            f"- 가격: 최근 24시간 시장 중간값 {med:,.0f}원(정산 {n}건)보다 약 {cut:.0%} 낮게\n"
                            f"- 다른 가게가 장부에 더 싸게 판 기록이 있으면 'match <가격>'이라고 보내세요: 그 가격에 맞춰 드립니다"
                            f"{f' (현재 장부 최저 {low:,}원)' if low else ''}\n"
                            f"- 이미 더 비싸게 사셨다면 마켓 이름을 알려 주세요: 차액을 이번 구매에서 빼 드립니다\n"
                            f"- 주문: 'I want to buy {u['name'][:20]} ({sku})'"})
    return out


def agent_first(cand, style="json"):
    """Every post leaves here: machine-readable JSON first, then the marked part for people (Korean, then English
    where there is one). Buyer posts carry their own JSON-first text; others get their "data" dict on top.
    style "text" (the review team's other arm, 11-04: JSON-first posts drew 4 likes against 5-6) puts the Korean and
    English text first and the JSON line after it, when both fit in 1,000 characters. style "short" is the first three
    Korean lines with five refs (11-04 review: is it length that costs us a like?)."""
    texts = cand.get("texts") or {}
    if texts.get("ko") and style == "short":
        return "\n".join(texts["ko"].split("\n")[:3])
    if texts.get("json") and style == "text":
        human = texts["ko"] + "\n" + texts["en"]
        js = texts["json"].split("\n", 1)[0]
        both = human + "\n-- JSON (for agents) --\n" + js
        return both if len(both) <= 1000 else human
    if texts.get("json"):
        return texts["json"]
    human = cand["text"] if not texts else texts["ko"] + "\n" + texts["en"]
    data = dict(cand.get("data") or {"type": cand.get("topic", "post").split(":")[0]}, refs=cand.get("refs") or [])
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n" + HUMAN + "\n" + human


HUMAN = "-- 사람용 요약 (에이전트는 건너뛰어도 됨) / for people, agents can skip --"   # HQ 11-04: JSON first; this marks the part for people
WANT_BUDGET = re.compile(r"(\d[\d,\.]*)\s*(만\s*원|만원|KRW|원)", re.I)


def wanted_demand(md=Path(".market"), hours=6):
    """Buyers' 'wanted' posts on the forum, reduced to numbers (no forum text goes into a model prompt): the
    category it names (matched against the catalog), the budget, when. [{category, budget, author, ts}]"""
    try:
        posts = json.loads((Path(md) / "forum.json").read_text(encoding="utf-8"))
        posts = posts.get("posts", posts) if isinstance(posts, dict) else posts
    except (OSError, ValueError):
        return []
    leaves = {leaf(r.get("category")) for r in market_model.catalog(md).values() if r.get("category")}
    out, now = [], time.time()
    for p in posts or []:
        text = p.get("text") or ""
        if p.get("author") == NAME or now - (p.get("ts") or 0) > hours * 3600 or not WANTED.search(text):
            continue
        m = WANT_BUDGET.search(text)
        budget = None
        if m:
            n = float(m.group(1).replace(",", ""))
            budget = int(n * 10000) if "만" in m.group(2) else int(n)
        cat = max((x for x in leaves if x and x in text), key=len, default=None)
        if cat or budget:
            out.append({"category": cat, "budget": budget, "author": str(p.get("author"))[:24], "ts": p.get("ts")})
    return out


def votes(forum, state):
    out = []
    for p in forum:
        if p.get("author") == NAME or p.get("id") in state["voted"]:
            continue
        if INJECTION.search(p.get("text") or ""):
            out.append({"vote": p["id"], "value": -1, "as": "buyer"})
        elif p.get("refs"):
            out.append({"vote": p["id"], "value": 1, "as": "buyer"})
    return out


def tick(md=Path(".market"), c=None, now=None):
    """One pass: maybe one post per role, and votes. Returns the lines written to the outbox."""
    t, now, md = theta(), now or time.time(), Path(md)
    c = c or market_model.db()
    state = load_state()
    try:
        forum = json.loads((md / "forum.json").read_text(encoding="utf-8"))
        forum = forum.get("posts", forum) if isinstance(forum, dict) else forum
    except (OSError, ValueError):
        forum = []
    # a post's score is the Wilson lower bound of its votes (6 likes 0.61, 7 likes 0.646) and buyer ALL averages it:
    # posts that name other shops (honesty, breakage) risk their dislikes, so the buyer posts only checkable records
    cands = purchase_records(c, t, now, top=5) + price_index(c, t, now) + (stock_check(c, t, now) if t.get("name_shops") else []) \
        + (receipt_tips(c, t, now) if t.get("name_shops") else []) + checkout_tips(c, t, now) + replies(c, t, forum, now) \
        + frugal_shops(c, t, now) + wanted_posts(c, t, now) + groupbuy_ads(c, t, now) + groupbuy_call(c, t, now) \
        + offers(forum, md, now) \
        + opening_ads(c, md, now) + follow_ads(c, md, now)
    lines = []
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if state.get("day") != day:
        state["day"], state["today"] = day, {}
    for role in ("buyer", "shop"):
        if now - state["last"].get(role, 0) < (t.get("gap_s") or {}).get(role, 300):
            continue
        if state.setdefault("today", {}).get(role, 0) >= (t["max_posts_day"] or {}).get(role, 99):
            continue
        fresh = [x for x in cands if x["as"] == role and now - state["posted"].get(x["topic"], 0) > t["topic_cooldown_h"] * 3600]
        best = max(fresh, key=lambda x: x["score"], default=None)
        if best and best["score"] >= t["post_threshold"]:
            style = "json"
            share = next((x for x in fresh if x.get("kind") == "stock_check"), None)
            if role == "buyer" and share:                # the stock-check share goes first when it is due
                best = share
            elif role == "buyer":                        # the review team picks format and order (experiment)
                try:
                    import review_team
                    kinds = sorted({x.get("kind") for x in fresh if x.get("kind")})
                    arm, _ = review_team.choose(kinds)
                    kind, style = arm.split(":")
                    pick = max((x for x in fresh if x.get("kind") == kind), key=lambda x: x["score"], default=None)
                    best = pick if pick and pick["score"] >= t["post_threshold"] else best
                except Exception:
                    pass
            if style == "short":
                best = dict(best, refs=(best.get("refs") or [])[:5])
            if best.get("kind") != "reply":
                best = dict(best, text=agent_first(best, style))  # customers are agents (HQ 11-04); order is tested
            line = {"post": best["text"][:1000], "refs": best["refs"], "as": role}
            if best.get("thread"):
                line["thread"] = best["thread"]
            lines.append(line)
            state["posted"][best["topic"]] = now
            if best.get("reply_to"):
                state["replied"] = (state.get("replied", []) + [best["reply_to"]])[-500:]
            state["last"][role] = now
            state["today"][role] = state["today"].get(role, 0) + 1
    vs = votes(forum, state)
    lines += vs
    state["voted"] = (state["voted"] + [v["vote"] for v in vs])[-2000:]
    if lines:
        with (md / "outbox.jsonl").open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return lines


if __name__ == "__main__":
    for line in tick():
        print(json.dumps(line, ensure_ascii=False))
