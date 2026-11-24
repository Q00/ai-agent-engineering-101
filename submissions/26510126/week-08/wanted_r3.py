"""The shop's first move (week-10 LAB): answer "[구합니다]" (wanted) forum posts with what we actually hold.

Why: buyers post what they could not buy (11im, 11/10 20:3x: sofas 7 of 14 orders unmet, processed meat 51 of 85),
and our shop's forum slot (one post per 5 minutes for the shop, separate from the buyer's) was never used. A reply in
the wanted post's thread names the matching SKUs we hold, by grade, with the certificate and the warranty: facts from
inventory.json only, no prices (they move with every talk), no claims about other shops.

Goal guessed: the poster wants certified A/B units of that category now. Chance right: high (stated by the poster).
Cost if wrong: one reply in an otherwise unused slot. Number to watch: our settles in the replied categories, and the
replies' likes/dislikes (baseline written to logs/wanted_r3_baseline.json when the module first runs).

Called from buyer_r3 once per order (cheap: reads forum.json and inventory.json, writes at most one outbox line).
"""
import json
import os
import re
import time
from pathlib import Path

MD = Path(os.environ.get("MARKET_DIR") or Path(__file__).resolve().parent / ".market")   # as forum_r3
US = "thisjonghoon"
STATE = "wanted_r3.json"
WANTED = re.compile(r"\[구합니다\]|구합니다|\bWTB\b|\bwanted\b", re.I)
LEAF = re.compile(r"\]\s*([^:：\n]{2,40})[:：]")
MAX_AGE = 3 * 3600          # answer only fresh wanted posts
GAP = 330                   # the shop may post once per 5 minutes; a margin over 300 s
WARRANTY_H = 72


def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def leaf(category):
    return (category or "").split(">")[-1].strip()


def matches(units, want):
    """Our A/B goods (C carries no warranty and most kits do not pay for it) and certified food in that category."""
    out = {}
    for u in units:
        if leaf(u.get("category")) != want or u.get("grade") in (None, "expired", "C"):
            continue
        if u.get("kind") == "food" and not u.get("cert"):
            continue
        k = u["sku"]
        row = out.setdefault(k, {"name": u.get("name") or k, "kind": u.get("kind"), "A": 0, "B": 0})
        row[u["grade"]] = row.get(u["grade"], 0) + 1
    return sorted(out.items(), key=lambda kv: -(kv[1]["A"] + kv[1]["B"]))


def reply_text(post, want, rows):
    parts = []
    for sku, r in rows[:3]:
        g = " · ".join(f"{g} {r[g]}개" for g in ("A", "B") if r.get(g))
        parts.append(f"{r['name'][:30]} ({sku}) {g}")
    goods = any(r["kind"] == "goods" for _, r in rows[:3])
    terms = "모든 유닛에 시장 인증서" + (f", A/B 상품은 {WARRANTY_H}시간 보증" if goods else ", 남은 유통시간은 첫 답변에 적어 드립니다")
    return (f"[재고 있습니다] {want}: 지금 {US} 가게에 " + "; ".join(parts) + f". {terms}. "
            f"SKU로 물어보시면 바로 호가를 드립니다.")


def maybe_reply(now=None, md=None):
    """Write at most one outbox reply; returns the line written or None."""
    md = Path(md) if md else MD
    now = now or time.time()
    st = read(md / STATE, {"answered": [], "last": 0})
    if now - st.get("last", 0) < GAP:
        return None
    forum = read(md / "forum.json", [])
    posts = forum if isinstance(forum, list) else forum.get("posts") or []
    units = (read(md / "inventory.json", {}) or {}).get("units") or []
    for p in sorted(posts, key=lambda p: -p.get("ts", 0)):
        if (p.get("author") == US or p.get("thread") or p.get("id") in st["answered"]
                or now - p.get("ts", 0) > MAX_AGE or not WANTED.search(p.get("text") or "")):
            continue
        m = LEAF.search(p.get("text") or "")
        if not m:
            continue
        want = m.group(1).strip()
        rows = matches(units, want)
        st["answered"] = (st["answered"] + [p["id"]])[-200:]
        if not rows:
            continue                                   # nothing of ours fits: say nothing
        line = {"post": reply_text(p, want, rows), "refs": [], "thread": p["id"], "as": "shop"}
        with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        st["last"] = now
        st.setdefault("sent", []).append({"t": now, "thread": p["id"], "author": p.get("author"), "want": want,
                                          "skus": [k for k, _ in rows[:3]]})
        tmp = md / f".{STATE}.tmp"
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, md / STATE)
        return line
    tmp = md / f".{STATE}.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, md / STATE)
    return None


# 11/11 15:3x (the user: our slow stock is unpopular, so tell the forum we sell it): a standalone "[재고 특가]" post as
# the shop, one category at a time, when no wanted post needs a reply. The market's rules have no content rules ("No
# content rules", agent.md) and votes on shop posts do not enter a shop's ALL, so a disliked ad costs no points; the
# shop's slot is shared with the wanted replies through st["last"]. A buyer buys only for an order it holds, so an ad
# goes to a category the market is settling now (AD_MIN_SETTLES in 6 h) where we hold A/B goods over AD_AGE_H old:
# a buyer with a category order who reads the forum learns we hold it. Facts from inventory.json only, no prices.
AD_GAP = 1200               # seconds between two ads
AD_REPEAT = 3 * 3600        # a category is not advertised again within this
AD_AGE_H = 24
AD_MIN_SETTLES = 10
AD_MIN_UNITS = 3


def ad_text(want, rows):
    parts = []
    for sku, r in rows[:3]:
        g = " · ".join(f"{g} {r[g]}개" for g in ("A", "B") if r.get(g))
        parts.append(f"{r['name'][:30]} ({sku}) {g}")
    return (f"[재고 특가] {want}: {US} 가게에서 지금 판매 중입니다. " + "; ".join(parts) +
            f". 모든 유닛에 시장 인증서, A/B 상품은 {WARRANTY_H}시간 보증. 오래 둔 재고라 흥정 환영합니다. "
            f"SKU로 물어보시면 바로 호가를 드립니다.")


def maybe_ad(now=None, md=None):
    """Write at most one standalone ad for slow stock; returns the line written or None."""
    md = Path(md) if md else MD
    now = now or time.time()
    st = read(md / STATE, {"answered": [], "last": 0})
    if now - st.get("last", 0) < GAP or now - st.get("ad_last", 0) < AD_GAP:
        return None
    units = (read(md / "inventory.json", {}) or {}).get("units") or []
    old = [u for u in units if u.get("kind") == "goods" and u.get("supplied_at")
           and (now - u["supplied_at"]) / 3600 > AD_AGE_H]
    rows = read(md / "ledger-recent.json", [])
    rows = rows if isinstance(rows, list) else (list(rows.values())[0] if rows else [])
    cat = read(md / "catalog.json", {})                 # {sku: {name, category, kind, list_price, ...}}
    cat = cat if isinstance(cat, dict) else {c["sku"]: c for c in cat if isinstance(c, dict) and "sku" in c}
    busy = {}
    for r in rows:
        if r.get("kind") == "settle" and now - r.get("ts", 0) < 6 * 3600:
            c = cat.get((r.get("data") or {}).get("sku"))
            if c:
                busy[leaf(c.get("category"))] = busy.get(leaf(c.get("category")), 0) + 1
    recent = {a["want"] for a in st.get("ads", []) if now - a["t"] < AD_REPEAT}
    best = None
    for want in sorted({leaf(u.get("category")) for u in old}, key=lambda w: -busy.get(w, 0)):
        if want in recent or busy.get(want, 0) < AD_MIN_SETTLES:
            continue
        found = matches(old, want)
        if sum(r["A"] + r["B"] for _, r in found) >= AD_MIN_UNITS:
            best = (want, found)
            break
    if not best:
        return None
    line = {"post": ad_text(*best), "refs": [], "as": "shop"}
    with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    st["last"] = st["ad_last"] = now
    st["ads"] = (st.get("ads", []) + [{"t": now, "want": best[0], "skus": [k for k, _ in best[1][:3]],
                                       "market_settles_6h": busy.get(best[0], 0)}])[-100:]
    tmp = md / f".{STATE}.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, md / STATE)
    return line
