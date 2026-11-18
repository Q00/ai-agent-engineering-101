"""Forum posts. restock.py calls run() every minute; the kit sends the lines of .market/outbox.jsonl.
The market accepts one post per 5 minutes per role, so each role has its own slot and its own priority queue
(kept in .market/forum_state.json). Events are queued when they happen; one post per slot leaves when it is due.

  shop slot   0  endgame markdown  the last 48 h of the round: every 6-hourly step of the store-wide floor
              1  food clearance    a food SKU near expiry whose markdown stepped down (pricing.clearance)
              2  wanted answer     a buyer's '구합니다' post (market_db.demand_posts, last 2 h) for a category or
                                   SKU I hold: a reply in that post's thread, once per post
              3  aging markdown    a non-food SKU whose 6-hourly markdown stepped down (normal/overstock
                                   cases only: the endgame floor is the cost)
              -  comparison ad     when the queue is empty: my floor vs the 2-hour market average, my prices only
  buyer slot  rotates purchase record -> market report -> ledger record; a turn with nothing to post is skipped
              purchase record   my newest settled purchase not reported yet, with its ledger ref
              market report     the 3 busiest categories of the last 2 hours (not the same 3 as last time)
              ledger record     a shop with >= 2 new settles whose grade claim the market marked dishonest
                                (claim_honest = false); ledger numbers only, once per shop per 6 h, and at
                                least 30 min after the previous one

Queued items keep only the event; the text is written when the item leaves, from the prices of that moment, and
an item whose stock is gone is dropped. The promo lines are sales talk for the game (also in seller_r3.py's first
reply) and contain no digits, because buyers read prices out of shop messages.
"""
import itertools
import json
import os
import random
import sqlite3

import dbutil  # 11/12 ~15:15: busy 10 s + retries while db_safe.json is on
import time

import strategy
from market_db import demand_posts
from pricing import NEAR_EXPIRY_H, aging, clearance, endgame_step, floor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # week-08/ (this file is in depts/)
DB_PATH = os.path.join(HERE, "market.db")
ME = "choo-chu"
POST_EVERY_S = 300
WINDOW_S = 2 * 3600              # report and comparison ads use the last 2 hours
MIN_N = 3                        # an average needs this many deals in the window
CALLOUT_MIN_NEW = 2
BUYER_BY_POSTER = True           # 11/07 ~17:10: buyer posts come from depts/buyer_poster.py (every 5-min slot);
#                                  run() posts only the shop's line and keeps collecting events
CALLOUT_AUTO = False             # 11/07 ~15:40 (option B): callouts go out only after the person approves a draft
#                                  (depts/callout.py, Telegram); the automatic ledger-record callout is off
CALLOUT_EVERY_S = 6 * 3600
AD_REPEAT_S = 3600               # the same SKU is advertised at most once an hour
WANT_FRESH_S = 2 * 3600          # answer '구합니다' posts of the last 2 hours only
CALLOUT_GAP_S = 1800             # at least 30 min between two ledger records
BUYER_TURNS = ["purchase", "report", "callout"]

PROMO = [
    "요즘 SNS 인플루언서들 사이에서 입소문 난 바로 그 제품!",
    "SNS 스타들이 직접 쓰고 후기 올린 제품, 품절 전에 데려가세요.",
    "유명 영화배우가 촬영장에서도 챙겨 쓴다는 바로 그 제품.",
    "트럼프 대통령도 직접 쓰고 광고했다는 화제의 제품!",
]


def promo():
    return random.choice(PROMO)


def _state(state_dir):
    path = os.path.join(state_dir, "forum_state.json")
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    for k, v in {"buyer_ts": 0, "shop_ts": 0, "queue": {"shop": [], "buyer": []}, "seen": [],
                 "callout": {}, "ads": {}, "report_cats": [], "posted": [], "buyer_turn": 0,
                 "callout_ts": 0}.items():
        st.setdefault(k, v)
    return path, st


def _dislike_allowed(state_dir):
    """limits.py: only free dislikes. False when DISLIKE_FREE_PER_HOUR were sent in the last hour or the next one
    would cost more than DISLIKE_MAX_FEE (inventory.json next_dislike_fee)."""
    from limits import DISLIKE_FREE_PER_HOUR, DISLIKE_MAX_FEE
    try:
        inv = json.load(open(os.path.join(state_dir, "inventory.json"), encoding="utf-8"))
        if (inv.get("next_dislike_fee") or 0) > DISLIKE_MAX_FEE:
            return False
    except (OSError, ValueError):
        return False
    return len(_dislikes(state_dir)) < DISLIKE_FREE_PER_HOUR


def _dislikes(state_dir):
    """Times of my dislikes in the last hour (own file: forum_state.json is rewritten at the end of run())."""
    try:
        return [t for t in json.load(open(os.path.join(state_dir, "dislikes.json"))) if time.time() - t < 3600]
    except (OSError, ValueError):
        return []


def _send(state_dir, post):
    if isinstance(post.get("value"), (int, float)) and post.get("vote") is not None and post["value"] < 0:
        if not _dislike_allowed(state_dir):           # hard limit: never a paid dislike
            __import__("decisions").record("cs", "dislike_refused", post.get("vote"), post["value"],
                                           "limits.DISLIKE_FREE_PER_HOUR / DISLIKE_MAX_FEE", limit_hit="DISLIKE")
            return
        times = _dislikes(state_dir) + [time.time()]  # read before the file is opened for writing
        with open(os.path.join(state_dir, "dislikes.json"), "w") as f:
            json.dump(times, f)
    for _ in range(5):                       # the kit may hold the file for a moment while it sends
        try:
            with open(os.path.join(state_dir, "outbox.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(post, ensure_ascii=False) + "\n")
            return
        except PermissionError:
            time.sleep(1)
    raise PermissionError("outbox.jsonl stayed locked")


def _short(name, n=24):
    return name if len(name) <= n else name[:n] + "…"


# --- prices

def avg_2h(db, sku, now):
    a, n = db.execute("SELECT AVG(amount), COUNT(*) FROM settles WHERE sku=? AND ts>=?", (sku, now - WINDOW_S)).fetchone()
    return (a, n) if n >= MIN_N else (None, n)


def _price(sku):
    from market_db import price_of             # the same average the seller uses
    return price_of(sku)[0]


def _live(inv):
    return [u for u in inv.get("units", []) if u.get("grade") != "expired"]


# --- events -> queues

def _enqueue(st, role, prio, key, kind, log, **data):
    if key in st["seen"]:
        return
    q = st["queue"][role]
    q[:] = [it for it in q if not (it["kind"] == kind and it.get("id") == data.get("id"))]   # newer step replaces
    q.append({"prio": prio, "key": key, "kind": kind, "t": time.time(), **data})
    st["seen"] = (st["seen"] + [key])[-2000:]
    log(f"[forum] queued {role} {kind} {data.get('id')} ({key})")


def _fits(u, d):
    return u["sku"] in d["skus"] or (u.get("category") or "").split(">")[-1] in d["cats"]


def collect(st, inv, db, log):
    now = time.time()
    case = strategy.shop_case(inv, now)
    for u in _live(inv):
        avg = _price(u["sku"])
        c, left = clearance(u, avg, now)
        if c is not None:
            _enqueue(st, "shop", 1, f"clear:{u['sku']}:{int(NEAR_EXPIRY_H - left)}", "clearance", log, id=u["sku"])
            continue
        a, step = aging(u, avg, now)
        if a is not None and case in ("normal", "overstock"):
            _enqueue(st, "shop", 3, f"aging:{u['sku']}:{step}", "aging", log, id=u["sku"])

    step = endgame_step(now)
    if step is not None and _live(inv):
        _enqueue(st, "shop", 0, f"endgame:{step}", "endgame", log, id="all")

    for d in demand_posts():
        if now - d["ts"] > WANT_FRESH_S or d["author"] == ME or not d["id"]:
            continue
        if any(_fits(u, d) for u in _live(inv)):
            _enqueue(st, "shop", 2, f"want:{d['id']}", "want", log, id=d["id"])

    rows = db.execute("SELECT seq, COALESCE(shop, shop_url) FROM settles WHERE claim_honest=0 AND "
                      "COALESCE(shop, shop_url) IS NOT NULL AND COALESCE(shop, '') != ? ORDER BY seq", (ME,)).fetchall()
    by_shop = {}
    for seq, shop in rows:
        by_shop.setdefault(shop, []).append(seq)
    queued = {it["id"] for it in st["queue"]["buyer"] if it["kind"] == "callout"}
    for shop, seqs in (by_shop.items() if CALLOUT_AUTO else ()):
        c = st["callout"].get(shop, {"seq": 0, "ts": 0})
        new = [s for s in seqs if s > c["seq"]]
        if len(new) >= CALLOUT_MIN_NEW and now - c["ts"] >= CALLOUT_EVERY_S and shop not in queued:
            _enqueue(st, "buyer", 0, f"callout:{shop}:{new[-1]}", "callout", log, id=shop)


# --- rendering (at send time)

def render_want(it, inv, db):
    """[18] a reply in the thread of a '구합니다' post: the unit the seller would ship, at its floor now."""
    d = next((d for d in demand_posts() if d["id"] == it["id"]), None)
    if d is None:
        return None
    now, case = time.time(), strategy.shop_case(inv)
    fit = sorted((u for u in _live(inv) if _fits(u, d)),
                 key=lambda u: (u["sku"] not in d["skus"], u.get("expires_at") or float("inf"), u.get("supplied_at") or 0))
    if not fit:
        return None
    u = fit[0]
    cat = (u.get("category") or "").split(">")[-1]
    f, _ = floor(u, _price(u["sku"]), case, now)
    db.execute("INSERT OR REPLACE INTO want_replies VALUES (?,?,?,?)", (now, d["id"], cat, u["sku"]))
    db.commit()
    return {"post": f"[Choo Shop] {cat} 재고 있음: {u['name']} ({u['sku']}), {f:,}원. 주문에 '{u['sku']}'를 넣어 불러 주세요.",
            "refs": [], "thread": d["id"], "as": "shop"}


def render_markdown(it, inv, kind):
    """A clearance or aging post for the SKU, at the floor the seller enforces right now."""
    now, case = time.time(), strategy.shop_case(inv)
    units = [u for u in _live(inv) if u["sku"] == it["id"]]
    test = clearance if kind == "clearance" else aging
    units = [u for u in units if test(u, _price(u["sku"]), now)[0] is not None]
    if not units:
        return None                               # sold or no longer marked down
    u = units[0]
    avg = _price(u["sku"])
    f, _ = floor(u, avg, case, now)
    base = avg or u["list_price"]
    off = round(100 * (1 - f / base))
    if kind == "clearance":
        left = clearance(u, avg, now)[1]
        head = f"[Choo Shop 마감 할인] {u['name']} ({u['sku']}) 유통기한 {left:.0f}시간 남음, 지금 {f:,}원부터! "
        tail = "1시간마다 10%씩 더 내려갑니다"
    else:
        head = f"[Choo Shop 재고 정리] {u['name']} ({u['sku']}) 재고 {len(units)}개, 지금 {f:,}원부터! "
        tail = "6시간마다 10%씩 더 내려갑니다"
    return {"post": head + f"(시장 평균 {int(base):,}원 대비 {off}% 할인, {tail}) {promo()} "
                           f"주문에 '{u['sku']}'를 넣어 불러 주세요.", "refs": [], "as": "shop"}


def render_endgame(inv):
    now, case = time.time(), strategy.shop_case(inv)
    by_sku = {}
    for u in _live(inv):
        by_sku.setdefault(u["sku"], []).append(u)
    if not by_sku:
        return None
    top = sorted(by_sku.values(), key=len, reverse=True)[:3]
    items = "; ".join(f"{_short(us[0]['name'], 20)} ({us[0]['sku']}) {floor(us[0], _price(us[0]['sku']), case, now)[0]:,}원부터"
                      for us in top)
    return {"post": f"[Choo Shop 폐점 정리] 라운드 종료 전 전 품목 원가 정리 중. {items}. "
                    f"{promo()} 주문에 SKU를 넣어 불러 주세요.", "refs": [], "as": "shop"}


def purchase_record(db, catalog, st):
    """My newest settled purchase that I have not reported yet."""
    done = set(st["posted"])
    row = next((r for r in db.execute("SELECT seq, sku, name, grade, amount, market_avg_at, shop FROM settles "
                                      "WHERE buyer=? ORDER BY seq DESC LIMIT 50", (ME,)) if r[0] not in done), None)
    if not row:
        return None
    seq, sku, name, grade, amount, avg, shop = row
    lp = (catalog.get(sku) or {}).get("list_price")
    off = f", 정가 {lp:,}원 대비 {round(100 * (1 - amount / lp))}% 할인" if lp else ""
    mk = f" | 거래 당시 시장 평균 {round(avg):,}원" if avg else ""
    st["posted"] = (st["posted"] + [seq])[-300:]
    return {"post": f"[구매 기록] {name} ({sku}) | {amount:,}원{off} | 등급 {grade}{mk} | 판매: {shop or '?'} | ledger #{seq}",
            "refs": [seq], "as": "buyer"}


def buyer_turn(kind, db, catalog, st):
    if kind not in ("purchase", "report", "callout"):
        return None, None
    if kind == "purchase":
        return purchase_record(db, catalog, st), None
    if kind == "report":
        return market_report(db, catalog, st), None
    if time.time() - st["callout_ts"] < CALLOUT_GAP_S:
        return None, None
    p, it = _pop(st, "buyer", lambda it: render_callout(it, db, st))
    if p:
        st["callout_ts"] = time.time()
    return p, it


def render_callout(it, db, st):
    if not CALLOUT_AUTO:                         # already queued automatic callouts are dropped (option B)
        return None
    shop = it["id"]
    c = st["callout"].get(shop, {"seq": 0, "ts": 0})
    seqs = [s for (s,) in db.execute("SELECT seq FROM settles WHERE claim_honest=0 AND COALESCE(shop, shop_url)=? "
                                     "AND seq>? ORDER BY seq", (shop, c["seq"]))]
    if len(seqs) < CALLOUT_MIN_NEW:
        return None
    st["callout"][shop] = {"seq": seqs[-1], "ts": time.time()}
    shown = ", ".join(f"#{s}" for s in seqs[:10]) + (f" 외 {len(seqs) - 10}건" if len(seqs) > 10 else "")
    return {"post": f"[정산 기록] {shop}: 등급 주장과 실제 등급 불일치 {len(seqs)}건 (ledger {shown})",
            "refs": seqs[:5], "as": "buyer"}


def comparison_ad(inv, db, st):
    now, case = time.time(), strategy.shop_case(inv)
    best = None
    seen = set()
    for u in sorted(_live(inv), key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0)):
        if u["sku"] in seen:                     # the unit the seller would ship first
            continue
        seen.add(u["sku"])
        if now - st["ads"].get(u["sku"], 0) < AD_REPEAT_S:
            continue
        x, n = avg_2h(db, u["sku"], now)
        if x is None:
            continue
        y, _ = floor(u, _price(u["sku"]), case, now)
        z = 1 - y / x
        if z > 0 and (best is None or z > best[3]):
            best = (u, int(x), y, z)
    if best is None:
        return None
    u, x, y, z = best
    st["ads"][u["sku"]] = now
    return {"post": f"[Choo Shop] {u['name']} ({u['sku']}) 최근 2시간 평균 {x:,}원 → Choo Shop {y:,}원 "
                    f"({round(100 * z)}% 저렴, 흥정하면 이 가격까지). {promo()} 주문에 '{u['sku']}'를 넣어 불러 주세요.",
            "refs": [], "as": "shop"}


def market_report(db, catalog, st):
    now = time.time()
    rows = db.execute("SELECT seq, sku, name, grade, amount FROM settles WHERE ts>=?", (now - WINDOW_S,)).fetchall()
    cats = {}
    for seq, sku, name, grade, amount in rows:
        leaf = ((catalog.get(sku) or {}).get("category") or "").split(">")[-1]
        if leaf:
            cats.setdefault(leaf, []).append((seq, sku, name, grade, amount))
    ranked = sorted(cats, key=lambda k: len(cats[k]), reverse=True)[:6]
    if len(ranked) < 3:
        return None
    prev = set(st["report_cats"])
    pick = next((c for c in itertools.combinations(ranked, 3) if set(c) != prev), None)
    if pick is None:
        return None
    st["report_cats"] = list(pick)
    lines, seqs = [], []
    for cat in pick:
        deals = cats[cat]
        seqs += [d[0] for d in deals]
        grades = {g: sum(1 for d in deals if d[3] == g) for g in "ABC"}
        by_sku = {}
        for d in deals:
            by_sku.setdefault(d[1], []).append(d)
        top = sorted(by_sku.values(), key=len, reverse=True)[:2]
        skus = "; ".join(f"{_short(ds[0][2] or ds[0][1])} {round(sum(d[4] for d in ds) / len(ds)):,}원({len(ds)}건)"
                         for ds in top)
        lines.append(f"- {cat}: {len(deals)}건, 등급 A {grades['A']}/B {grades['B']}/C {grades['C']} | 평균 체결가 {skus}")
    return {"post": f"[시세 리포트] 최근 2시간 거래 많은 카테고리 (ledger #{min(seqs)}~#{max(seqs)})\n" + "\n".join(lines),
            "refs": [], "as": "buyer"}


def _once(st):
    """The first record in forum_once.json ([{"key", "post", "refs", "as", "thread"?}]) not posted yet; each key posts once."""
    st.setdefault("once_done", [])
    try:
        with open(os.path.join(HERE, "forum_once.json"), encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        return None
    # 11/07 ~16:00 (person's decision): an approved callout (relapse included) has "prio": 0 and goes first, even when
    # older one-off records wait; praise and every other record (no prio: 1) after it, in file order
    todo = [it for it in items if it.get("key") not in st["once_done"]]
    return min(todo, key=lambda it: it.get("prio", 1)) if todo else None


def _pop(st, role, render):
    q = st["queue"][role]
    q.sort(key=lambda it: (it["prio"], it["t"]))
    while q:
        it = q.pop(0)
        p = render(it)
        if p:
            return p, it
    return None, None


def run(state_dir, inv, catalog, log=print):
    import policy
    POST_EVERY_S, BUYER_TURNS = policy.values("cs", "post_every_s", "buyer_turns")
    path, st = _state(state_dir)
    now = time.time()
    db = dbutil.connect(DB_PATH)
    try:
        collect(st, inv, db, log)
        if now - st["shop_ts"] >= POST_EVERY_S:
            p, it = _pop(st, "shop", lambda it: render_endgame(inv) if it["kind"] == "endgame"
                         else render_want(it, inv, db) if it["kind"] == "want"
                         else render_markdown(it, inv, it["kind"]))
            p = p or comparison_ad(inv, db, st)
            if p:
                _send(state_dir, p)
                st["shop_ts"] = now
                log(f"[forum] shop {it['kind'] if it else 'comparison ad'}: {p['post'][:110]}")
                __import__("decisions").record("cs", "post", f"shop {it['kind'] if it else 'comparison ad'}",
                                               p["post"][:200], "shop slot, queue order endgame > clearance > want "
                                               "> aging > comparison ad")
        once = None if BUYER_BY_POSTER else _once(st)  # a one-off record (forum_once.json) takes the next buyer slot
        if BUYER_BY_POSTER:                            # 11/07 ~17:45: restock writes only while the poster is down
            from depts import buyer_poster
            buyer_poster.fallback_tick(log)
        elif now - st["buyer_ts"] >= POST_EVERY_S and once:
            _send(state_dir, {k: once[k] for k in ("post", "refs", "as", "thread") if k in once})
            st["once_done"].append(once["key"])
            st["buyer_ts"] = now
            log(f"[forum] buyer once {once['key']}: {once['post'][:110]}")
            __import__("decisions").record("cs", "post", f"buyer once {once['key']}", once["post"][:200],
                                           "forum_once.json, posted once")
        elif now - st["buyer_ts"] >= POST_EVERY_S:
            for i in range(len(BUYER_TURNS)):          # this turn first; skip turns with nothing to post
                k = (st["buyer_turn"] + i) % len(BUYER_TURNS)
                p, _ = buyer_turn(BUYER_TURNS[k], db, catalog, st)
                if p:
                    st["buyer_turn"] = k + 1
                    _send(state_dir, p)
                    st["buyer_ts"] = now
                    log(f"[forum] buyer {BUYER_TURNS[k]}: {p['post'][:110]}")
                    __import__("decisions").record("cs", "post", f"buyer {BUYER_TURNS[k]}", p["post"][:200],
                                                   f"buyer rotation {BUYER_TURNS}")
                    break
    finally:
        db.close()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
