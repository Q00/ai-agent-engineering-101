"""v1.18 seeded synthetic ledger and question set for evals.memory_eval. No memory/ import, no network.

    events, catalog, meta = make(seed)        # events.jsonl rows in seq order (seq 1.., ts strictly increasing)
    qs = questions(events, catalog, meta, seed)   # [{id, ability, kind, args, tag}]

Planted structure (all from random.Random(seed)):
- 5 categories x 2 SKUs; each SKU sells at 2 of 4 shops. Each shop x SKU price is list x U(0.85, 0.95) with +-3% noise;
  half the pairs move to x0.7 or x1.25 from day MOVE_DAY (an UPDATE of the graph's reference price).
- 8 buyers b1..b8. Favourite category A before the shift, B after (75% of settles in the favourite). Odd buyers
  buy in every 8 h slot (one trajectory, phase B at twice the rate); even buyers stop after day 2 and return on
  day 5 (a > 24 h gap: the old trajectory is DELETEd, a new one starts).
- 8% of settles are refunded 1-6 h later (fail/market stage refund, detail.order); 6% break 2-30 h later
  (broken/market); per shop and day 0-3 failed results (fail/market stage result) and 0-2 payment rejects.
"""

import random

T0 = 1_800_000_000
H, DAY = 3600, 86400
IDLE = 24 * H
DAYS = 9
MOVE_DAY = 4
SHOPS = ("s1", "s2", "s3", "s4")
CATS = {
    "김치": (("k1", "포기김치", 10000), ("k2", "총각김치", 12000)),
    "과일": (("f1", "사과", 8000), ("f2", "배", 15000)),
    "가전>TV": (("t1", "TV 43", 500000), ("t2", "TV 55", 800000)),
    "의류>셔츠": (("c1", "셔츠 A", 30000), ("c2", "셔츠 B", 45000)),
    "도서": (("d1", "소설", 15000), ("d2", "에세이", 22000)),
}
FAIL_REASONS = ("mismatch", "over_cap", "timeout")
REJECT_REASONS = ("budget", "price")


def catalog():
    return {sku: {"category": c, "name": n, "list_price": lp} for c, rows in CATS.items() for sku, n, lp in rows}


def make(seed=7):
    """Synthetic ledger -> (events, catalog, meta)."""
    rng = random.Random(seed)
    cat = catalog()
    cats = list(CATS)
    by_cat = {c: [s for s, _, _ in rows] for c, rows in CATS.items()}
    shops_for = {sku: sorted(rng.sample(SHOPS, 2)) for sku in cat}
    pairs = sorted((s, k) for k, ss in shops_for.items() for s in ss)
    m0 = {p: rng.uniform(0.85, 0.95) for p in pairs}
    moved = {p: rng.choice((0.7, 1.25)) for p in pairs if rng.random() < 0.5}
    used, raw, led = set(), [], [5000]

    def stamp(t):
        t = int(t)
        while t in used:
            t += 1
        used.add(t)
        return t

    def ledger_no():
        led[0] += 1
        return led[0]

    favs, breaks = {}, {}
    for i in range(1, 9):
        b = f"b{i}"
        favs[b] = (cats[i % 5], cats[(i + 2) % 5])
        breaks[b] = i % 2 == 0
        for day in range(DAYS):
            if breaks[b] and day in (3, 4):
                continue
            phase_b = day >= (5 if breaks[b] else MOVE_DAY)
            per_slot = 2 if phase_b and not breaks[b] else 1
            for slot in range(3):
                for _ in range(per_slot):
                    fav = favs[b][phase_b]
                    c = fav if rng.random() < 0.75 else rng.choice([x for x in cats if x != fav])
                    sku = rng.choice(by_cat[c])
                    shop = rng.choice(shops_for[sku])
                    mult = m0[(shop, sku)] * (moved.get((shop, sku), 1.0) if day >= MOVE_DAY else 1.0)
                    amount = int(round(cat[sku]["list_price"] * mult * rng.uniform(0.97, 1.03)))
                    ts = stamp(T0 + day * DAY + slot * 8 * H + rng.uniform(0, 8 * H))
                    n = ledger_no()
                    order = f"o{n}"
                    d = {"buyer": b, "shop": shop, "sku": sku, "amount": amount, "ledger": n, "order": order}
                    raw.append((ts, "settle", "market", d))
                    if rng.random() < 0.08:
                        raw.append((stamp(ts + rng.uniform(1 * H, 6 * H)), "fail", "market",
                                    {"stage": "refund", "detail": {"order": order, "shop": shop, "sku": sku}}))
                    if rng.random() < 0.06:
                        raw.append((stamp(ts + rng.uniform(2 * H, 30 * H)), "broken", "market",
                                    {"shop": shop, "sku": sku, "buyer": b, "order": order, "ledger": ledger_no()}))
    for day in range(DAYS):
        for shop in SHOPS:
            for _ in range(rng.randint(0, 3)):
                raw.append((stamp(T0 + day * DAY + rng.uniform(0, DAY)), "fail", "market",
                            {"stage": "result", "shop": shop, "reason_code": rng.choice(FAIL_REASONS)}))
            for _ in range(rng.randint(0, 2)):
                raw.append((stamp(T0 + day * DAY + rng.uniform(0, DAY)), "reject", "payments",
                            {"shop": shop, "reason_code": rng.choice(REJECT_REASONS)}))
    raw.sort(key=lambda r: r[0])
    events = [
        {"seq": i, "ts": float(ts), "type": t, "src": src, "key": f"{src}:{t}:{i}", "role": "market", "mine": False,
         "data": d}
        for i, (ts, t, src, d) in enumerate(raw, 1)
    ]
    meta = {
        "seed": seed,
        "t0": float(T0),
        "t_end": events[-1]["ts"],
        "buyers": sorted(favs),
        "favs": favs,
        "breaks": breaks,
        "shops": list(SHOPS),
        "pairs": pairs,
        "moved": sorted(moved),
    }
    return events, cat, meta


def questions(events, cat, meta, seed=7, per_kind=12):
    """Question set over the synthetic ledger: [{id, ability, kind, args, tag}]. Anchors come from the raw events;
    the answers come from evals.truth."""
    rng = random.Random(seed + 1)
    settles = [e for e in events if e["type"] == "settle"]
    refunds = [e for e in events if e["type"] == "fail" and e["data"].get("stage") == "refund"]
    start, end = settles[0]["ts"], meta["t_end"]
    by_order = {e["data"]["order"]: e for e in settles}
    qs = []

    def q(ability, kind, tag="", **args):
        qs.append({"id": len(qs), "ability": ability, "kind": kind, "args": args, "tag": tag})

    def after(e, lo=60, hi=6 * H):
        return e["ts"] + rng.uniform(lo, hi)

    # IE: one fact
    for _ in range(per_kind):
        e = rng.choice(settles[5:])
        q("IE", "price_last", shop=e["data"]["shop"], sku=e["data"]["sku"], t=after(e))
        e = rng.choice(settles[5:])
        q("IE", "last_cat", buyer=e["data"]["buyer"], t=after(e, 1, 2 * H))
        shop = rng.choice(SHOPS)
        q("IE", "last_failure", shop=shop, t=rng.uniform(start + DAY, end))
    # MS: aggregates across days
    for _ in range(per_kind):
        q("MS", "top_cats", buyer=rng.choice(meta["buyers"]), t=rng.uniform(start + 5 * DAY, end))
        t0 = rng.uniform(start, end - 4 * DAY)
        q("MS", "fail_count", shop=rng.choice(SHOPS), t0=t0, t1=t0 + rng.uniform(2, 4) * DAY)
        s, k = rng.choice(meta["pairs"])
        t0 = rng.uniform(start, end - 4 * DAY)
        q("MS", "trade_count", shop=s, sku=k, t0=t0, t1=t0 + rng.uniform(2, 4) * DAY)
    # TR: the state as of t, and the trajectory pairs (favourite before vs after the shift)
    for _ in range(per_kind):
        e = rng.choice(settles[5:])
        q("TR", "taste_at", buyer=e["data"]["buyer"], t=after(e, 60, 3 * H))
        q("TR", "broken_at", shop=rng.choice(SHOPS), t=rng.uniform(start, end))
    for b in meta["buyers"]:
        b_start = start + (5 if meta["breaks"][b] else MOVE_DAY) * DAY
        q("TR", "fav_at", "trajectory t1", buyer=b, t=b_start - (2 * DAY if meta["breaks"][b] else 60))
        q("TR", "fav_at", "trajectory t2", buyer=b, t=end - H)
    # KU: the current value after UPDATE / DELETE / refund
    moved = set(map(tuple, meta["moved"]))
    late = [e for e in settles
            if (e["data"]["shop"], e["data"]["sku"]) in moved and e["ts"] > start + (MOVE_DAY + 1) * DAY]
    for e in rng.sample(late, min(per_kind, len(late))):
        q("KU", "ref_price", "after price move", shop=e["data"]["shop"], sku=e["data"]["sku"], t=e["ts"] + 1)
    for r in rng.sample(refunds, min(per_kind, len(refunds))):
        b = by_order[r["data"]["detail"]["order"]]["data"]["buyer"]
        q("KU", "taste_at", "after refund", buyer=b, t=r["ts"] + 1)
    for b in meta["buyers"]:
        if meta["breaks"][b]:
            first_b = next(e for e in settles if e["data"]["buyer"] == b and e["ts"] >= start + 5 * DAY)
            q("KU", "taste_at", "new trajectory", buyer=b, t=first_b["ts"] + 6 * H)
            last_a = max(e["ts"] for e in settles if e["data"]["buyer"] == b and e["ts"] < start + 3 * DAY)
            q("KU", "taste_at", "trajectory deleted", buyer=b, t=last_a + IDLE + H)
    # AB: unknown key or before the record
    for i in range(per_kind // 2):
        s, k = rng.choice(meta["pairs"])
        first = next(e["ts"] for e in settles if (e["data"]["shop"], e["data"]["sku"]) == (s, k))
        q("AB", "price_last", "before pair record", shop=s, sku=k, t=rng.uniform(first - DAY, first))
        q("AB", "price_last", "unknown sku", shop=s, sku=f"zz{i}", t=end)
        q("AB", "taste_at", "unknown buyer", buyer=f"ghost{i}", t=rng.uniform(start + DAY, end))
        q("AB", "taste_at", "before record", buyer=rng.choice(meta["buyers"]), t=start - rng.uniform(0, DAY))
        q("AB", "last_failure", "unknown shop", shop=f"s{9 + i}", t=end)
        q("AB", "trade_count", "before record", shop=s, sku=k, t0=start - 2 * DAY, t1=start - rng.uniform(0, DAY))
    return qs
