"""R3 context cards: fixed slots, fixed size, built from aggregates.Aggregates (architecture review, plan A:
"컨텍스트 카드: 고정 슬롯, 고정 크기"). Owner: ML Engineer (R3-01b).

A card is (a) a dict with a fixed key set (for code: policy.py / restock / buyer) and (b) a text block of exactly
`lines` lines of exactly LINE_W characters (for an LLM prompt, e.g. the layer-4 policy editor). The text size is
constant whatever the number of events: a slot without data is filled with "-", long names are cut.

    from cards import context_card
    card = context_card(A, focus=["식품>계란/유제품>요거트"], shops=["https://.../shop/x"])
    card["text"]   # K_CAT + K_SHOP + 2 lines, each LINE_W chars -> CARD_CHARS characters

Slots: 1 header (seq, events, exclusions), 1 my P&L, K_CAT category lines (focus first, then highest velocity),
K_SHOP shop lines (given shops first, then most recent settles). Selection costs O(categories + shops), bounded
by the catalog (140 categories) and the shop list, never by the event count.
"""
LINE_W = 120
K_CAT = 8
K_SHOP = 5
LINES = 2 + K_CAT + K_SHOP
CARD_CHARS = LINES * LINE_W + (LINES - 1)       # newline-joined
CAT_KEYS = ("category", "n", "p25", "p50", "p75", "velocity_per_h", "fail_rate", "sell_rate_by_hours_left")
SHOP_KEYS = ("shop", "settled", "claim_honest_rate", "broken_rate", "recent_claim_honest_rate", "recent_broken_rate")
ME_KEYS = ("sales", "revenue", "cogs", "gross_profit", "net_profit_est", "expired", "storage_est", "live_units",
           "inventory_days", "buys", "spend", "surplus_list")


def _fit(s, w=LINE_W):
    """Exactly w display slots in characters (cut or pad). Korean text counts as one char each."""
    s = s.replace("\n", " ")
    return s[:w].ljust(w) if len(s) <= w else s[: w - 1] + "~"


def _num(x, nd=2):
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def category_slot(A, cat):
    st = A.category(cat) if cat else None
    d = {k: None for k in CAT_KEYS}
    d["category"] = cat
    if st:
        d.update({k: st.get(k) for k in CAT_KEYS if k != "category"})
    sr = d["sell_rate_by_hours_left"] or {}
    srs = ",".join(f"{k}:{_num(v)}" for k, v in sr.items() if v is not None) or "-"
    leaf = (cat or "-").split(">")[-1]
    text = (f"C {leaf[:24]:<24} n={_num(d['n'])} p25/50/75={_num(d['p25'])}/{_num(d['p50'])}/{_num(d['p75'])} "
            f"v/h={_num(d['velocity_per_h'], 3)} fail={_num(d['fail_rate'], 3)} sell@h={srs}")
    return d, _fit(text)


def shop_slot(A, shop):
    st = A.shop(shop) if shop else None
    d = {k: None for k in SHOP_KEYS}
    d["shop"] = shop
    if st:
        d.update({k: st.get(k) for k in SHOP_KEYS if k != "shop"})
    name = (shop or "-").rstrip("/").split("/")[-1]
    text = (f"S {name[:28]:<28} settled={_num(d['settled'])} honest={_num(d['claim_honest_rate'], 3)} "
            f"broken={_num(d['broken_rate'], 3)} recent_honest={_num(d['recent_claim_honest_rate'], 3)} "
            f"recent_broken={_num(d['recent_broken_rate'], 3)}")
    return d, _fit(text)


def me_slot(A):
    m = A.me()
    d = {k: m.get(k) for k in ME_KEYS}
    text = (f"ME sales={d['sales']} rev={d['revenue']} cogs={d['cogs']} gp={d['gross_profit']} "
            f"net_est={_num(d['net_profit_est'], 0)} expired={d['expired']} live={d['live_units']} "
            f"inv_days={_num(d['inventory_days'], 1)} buys={d['buys']} spend={d['spend']} surplus={d['surplus_list']}")
    return d, _fit(text)


def header_slot(A):
    s = A.summary()
    ex = s["excluded"]
    d = {"seq": s["seq"], "events": s["events"], "categories": s["categories"], "skus": s["skus"],
         "shops": s["shops"], "self_trade": ex["self_trade"], "own_market_stats": ex["own_market_stats"]}
    text = (f"H seq={d['seq']} events={d['events']} cats={d['categories']} skus={d['skus']} shops={d['shops']} "
            f"excl_self={d['self_trade']} excl_own={d['own_market_stats']}")
    return d, _fit(text)


def pick_categories(A, focus=(), k=K_CAT):
    out = [c for c in dict.fromkeys(focus or ()) if c][:k]
    if len(out) < k:
        taken = set(out)
        out += [c for c in A.cat_rank() if c not in taken][: k - len(out)]
    return out + [None] * (k - len(out))


def pick_shops(A, shops=(), k=K_SHOP):
    out = []
    for x in dict.fromkeys(shops or ()):
        if x and len(out) < k:
            out.append(x)
    if len(out) < k:
        have = {A.s["alias"].get(str(x).lower().rstrip("/"), str(x).lower().rstrip("/")) for x in out}
        rest = sorted((c for c in A.s["shop"] if c not in have), key=lambda c: (-A.s["shop"][c]["settled"], c))
        out += rest[: k - len(out)]
    return out + [None] * (k - len(out))


def context_card(A, focus=(), shops=()):
    h, ht = header_slot(A)
    m, mt = me_slot(A)
    cats = [category_slot(A, c) for c in pick_categories(A, focus)]
    shs = [shop_slot(A, s) for s in pick_shops(A, shops)]
    lines = [ht, mt] + [t for _, t in cats] + [t for _, t in shs]
    assert len(lines) == LINES and all(len(x) == LINE_W for x in lines)
    return {"header": h, "me": m, "categories": [d for d, _ in cats], "shops": [d for d, _ in shs],
            "text": "\n".join(lines)}
