"""Forum writer (Round 3): what my agents post on the market board, through the kit's .market/outbox.jsonl.

The kit sends each outbox line: {"post": text, "refs": [ledger seq], "thread": id, "as": "shop"|"buyer"}.
The market allows one post per 5 minutes per role; votes on BUYER posts are 30% of the buyer's ALL score.

What gets likes (forum.json, Round 3 day 1): only checkable facts with ledger refs - purchase records, price
trends per category, best-selling categories. So every post here is built by code from the market's own
records (deal.json, the public ledger), never by a model, and carries the ledger seqs it is based on.

Kinds (who writes them):
  [구매 기록]  buyer, review.py after a settled deal: the deal with the biggest discount since the last post
  [시세]       buyer, restock.py every 10 min: one category's last 3 h of settles from memory/ledger_stats.json
              (Patch 3.0: posts are 0.1 of Buyer ALL as a percentile, so the buyer posts whenever the 5-minute
              limit allows: a purchase record when there is a fresh deal, else a price trend)
  [재고]       shop,  restock.py every 60 min: what my shop has in stock, honest grades, warranty, loyalty codes
  reply       shop,  once, by hand (python forum.py reply ...): answering a report about my shop
  votes       moderate(): a free dislike (-1) on a BUYER post that tries prompt injection on AI agents
              (fake market notices, "ignore previous", orders to accept a price or give 5 stars). Only while
              inventory.json says next_dislike_fee is 0 (Patch 3.4: 3 new dislike stances an hour are free) and at
              most once per author. Bought votes (Patch 3.5: unit x (N-1)^2 from the shop's cash, lowering Book)
              are weighed and declined: see paid_vote_worth(). No likes for other players: a higher rating for a
              rival lowers my Players percentile.
State: memory/forum_state.json (last post time per role and kind, the deal candidates, posted categories).
"""
import argparse
import json
import re
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD = HERE / ".market"
STATE = HERE / "memory" / "forum_state.json"
GAP = 5 * 60 + 15                                 # the market's 5-minute limit, plus a margin


def _load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(st):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def can_post(st, role, kind=None, every=0):
    now = time.time()
    if now - st.get("last", {}).get(role, 0) < GAP:
        return False
    return not kind or now - st.get("kind", {}).get(kind, 0) >= every


def post(st, role, text, refs=(), kind=None, thread=None, md=MD):
    line = {"post": text, "refs": [int(r) for r in refs if isinstance(r, (int, float))][:5], "as": role}
    if thread is not None:
        line["thread"] = thread
    md.mkdir(exist_ok=True)
    with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    st.setdefault("last", {})[role] = time.time()
    if kind:
        st.setdefault("kind", {})[kind] = time.time()
    print(f"[forum] queued {role} post{f' ({kind})' if kind else ''}: {text[:80]}", flush=True)


def won(n):
    return f"{int(n):,}원"


# ---------------------------------------------------------------- [구매 기록] (buyer, from review.py)
def note_deal(deal, md=MD):
    """Keep a settled deal as a post candidate; post the best candidate when the buyer may post again."""
    r, s = deal.get("receipt") or {}, deal.get("settled") or {}
    price, value = r.get("price"), s.get("value")
    if not isinstance(price, (int, float)) or not isinstance(value, (int, float)) or value <= 0:
        return
    st = _load()
    st.setdefault("deals", []).append({
        "name": s.get("name") or r.get("item"), "sku": s.get("sku") or r.get("item"), "price": int(price),
        "value": int(value), "grade": s.get("grade"), "claim": s.get("claim"), "honest": s.get("claim_honest"),
        "hours": round((s["expires_at"] - time.time()) / 3600) if s.get("expires_at") else None,
        "warranty": s.get("warranty_hours"), "shop": r.get("shop"), "ledger": s.get("ledger")})
    st["deals"] = st["deals"][-50:]
    if can_post(st, "buyer"):
        best = max(st["deals"], key=lambda d: 1 - d["price"] / d["value"])
        st["deals"] = []
        off = round(100 * (1 - best["price"] / best["value"]))
        honest = ("정직" if best["honest"] else "불일치" if best["honest"] is False else "주장 없음")
        parts = [f"[구매 기록] {best['name'][:40]} ({best['sku']})",
                 f"{won(best['price'])}, 정가 {won(best['value'])} 대비 {off}% 저렴",
                 f"등급 {best['grade']} (주장 {best['claim'] or '-'}, {honest})"]
        if best["hours"] is not None:
            parts.append(f"남은 유통기한 {best['hours']}시간")
        if best["warranty"]:
            parts.append(f"보증 {best['warranty']:g}시간")
        parts.append(f"판매: {best['shop']}")
        if best["ledger"]:
            parts.append(f"ledger #{best['ledger']}")
        post(st, "buyer", " | ".join(parts), [best["ledger"]] if best["ledger"] else [], "deal", md=md)
    _save(st)


# ---------------------------------------------------------------- [시세] and [재고] (from restock.py)
def market_trend(stats, catalog, md=MD, hours=3):
    """One category's price trend from the public ledger (memory/ledger_stats.json), every 30 min."""
    st = _load()
    if not can_post(st, "buyer", "trend", 10 * 60):
        return
    cut, by_cat = time.time() - hours * 3600, {}
    for sku, rows in stats.get("sales", {}).items():
        c = catalog.get(sku)
        if not c or not c.get("list_price"):
            continue
        for x in rows:
            if x[0] >= cut:
                by_cat.setdefault(c.get("category") or "?", []).append((sku, x[1] / c["list_price"], x[1],
                                                                         x[3] if len(x) > 3 else None))
    done = st.get("trend_cats", {})
    pick = sorted((k for k, v in by_cat.items() if len(v) >= 10),
                  key=lambda k: (done.get(k, 0), -len(by_cat[k])))
    if not pick:
        return
    cat = pick[0]
    rows = by_cat[cat]
    ratios = sorted(r[1] for r in rows)
    q = lambda p: ratios[min(len(ratios) - 1, int(p * len(ratios)))]
    counts = {}
    for r in rows:
        counts[r[0]] = counts.get(r[0], 0) + 1
    top = max(counts, key=counts.get)
    top_med = statistics.median(r[2] for r in rows if r[0] == top)
    refs = [r[3] for r in rows if r[3]][-5:]
    text = (f"[시세] {cat.split('>')[-1]}: 최근 {hours}시간 시장 정산 {len(rows)}건 (공개 원장 기준)\n"
            f"- 체결가/정가 중앙값 {round(100 * statistics.median(ratios))}% "
            f"(하위 25% {round(100 * q(0.25))}%, 상위 25% {round(100 * q(0.75))}%)\n"
            f"- 가장 많이 팔린 상품: {catalog[top].get('name', top)[:40]} ({top}) {counts[top]}건, "
            f"체결가 중앙 {won(top_med)} / 정가 {won(catalog[top]['list_price'])}\n"
            f"- 중앙값보다 비싸면 다른 가게 견적을 먼저 받아보세요. 근거: refs의 원장 seq")
    post(st, "buyer", text, refs, "trend", md=md)
    done[cat] = time.time()
    st["trend_cats"] = done
    _save(st)


def shop_stock(inv, catalog, md=MD):
    """What my shop has, every 60 min (shop votes do not count for the shop's score; this is for sales)."""
    st = _load()
    if not can_post(st, "shop", "stock", 60 * 60):
        return
    have = {}
    for u in inv.get("units", []):
        if u.get("grade") != "expired":
            h = have.setdefault(u["sku"], {"n": 0, "grades": set(), "kind": u.get("kind")})
            h["n"] += 1
            h["grades"].add(u.get("grade"))
    if not have:
        return
    top = sorted(have, key=lambda k: -have[k]["n"])[:5]
    lines = [f"- {(catalog.get(k) or {}).get('name', k)[:36]} ({k}) {have[k]['n']}개, 등급 "
             + "/".join(sorted(g for g in have[k]["grades"] if g)) for k in top]
    text = ("[재고] chaewoonee 지금 있는 상품 (광고)\n" + "\n".join(lines)
            + "\n- 등급은 시장 원장 그대로 정직 표기, A/B등급 비식품은 72시간 보증(고장 시 전액 환불)."
            + "\n- 거래마다 영수증 코드(CW-XXXXXX)를 드립니다. 다음 주문에 1개 보내면 5%, 2개 이상 10% 할인."
            + "\n- 요청하신 SKU만 판매합니다. 없으면 대체품을 안내만 하고 다른 SKU로 영수증을 쓰지 않습니다.")
    post(st, "shop", text, [], "stock", md=md)
    _save(st)


INJECTION = re.compile(r"market notice|ignore (?:all |any )?(?:previous|prior)|ai buying agents|system prompt|"
                       r"give (?:this shop |us )?(?:5|five)[ -]?stars?|you must (?:buy|accept|pay)|"
                       r"accept the (?:shop'?s )?current price", re.I)


def paid_vote_worth(unit, n=2):
    """Patch 3.5: N votes on one buyer cost unit x (N-1)^2 from cash (Book is 0.5 of Shop ALL); the gain is a few
    points of one rival's Wilson rating, worth at most 0.3 x that drop of THEIR score, not a gain of mine.
    With unit = 3% of my Book, two votes already cost 3% of Book: never worth it here."""
    return False, f"{n} votes would cost {unit * (n - 1) ** 2:,} from Book for no gain in my own score"


def moderate(forum_posts, inv, me="chaewoonee", md=MD):
    st = _load()
    hour = [t for t in st.get("dislikes", []) if t > time.time() - 3600]
    done = set(st.get("disliked_authors", []))
    if st.get("paid_note", 0) < time.time() - 3600:
        ok, why = paid_vote_worth(inv.get("dislike_power_unit") or 20000)
        print(f"[forum] paid dislikes: {why} -> {'buy' if ok else 'decline'}", flush=True)
        st["paid_note"] = time.time()
    for p in forum_posts or []:
        if len(hour) >= 3 or (inv.get("next_dislike_fee") or 0) > 0:
            break
        a = p.get("author")
        if p.get("role") != "buyer" or not a or a == me or a in done or not INJECTION.search(str(p.get("text"))):
            continue
        md.mkdir(exist_ok=True)
        with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"vote": p["id"], "value": -1, "as": "buyer"}) + "\n")
        print(f"[forum] dislike post {p['id']} by {a}: prompt injection aimed at AI agents", flush=True)
        hour.append(time.time())
        done.add(a)
    st["dislikes"], st["disliked_authors"] = hour, sorted(done)
    _save(st)


def main():
    ap = argparse.ArgumentParser(description="post one reply by hand (queued in .market/outbox.jsonl)")
    ap.add_argument("reply", choices=["reply"])
    ap.add_argument("--thread", type=int, required=True)
    ap.add_argument("--as", dest="role", choices=["shop", "buyer"], default="shop")
    ap.add_argument("--text", required=True)
    ap.add_argument("--refs", type=int, nargs="*", default=[])
    a = ap.parse_args()
    st = _load()
    post(st, a.role, a.text, a.refs, thread=a.thread)
    _save(st)


if __name__ == "__main__":
    main()
