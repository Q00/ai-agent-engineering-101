"""Kiro 데이터 노트 (BS1, 11/4 evening): short posts as our buyer between journal issues, at most one
every GAP seconds (the market takes one buyer post per 5 minutes; the buyer's ALL weighs the votes on
its posts 0.3 and their number 0.1).

Most of the forum repeats itself: at 20:55 on 11/4, 60 of the newest 79 buyer posts were price summaries
or purchase reports, about five categories (sofas, kimchi, mattresses, water, bedding). A post's score is
the lower bound of its likes (about 6 voters: 6 likes 0.61, 7 likes 0.646, none 0), and the board takes
the mean, so what counts is that every post earns its likes and none goes unvoted, not that it stands
out. The notes give what the others do not, from the public ledger only:

    조건  condition-matched prices: one category's paid share of the list price by grade, with a warranty
          and without (market_summary.json by_grade), so a price is compared on the same terms
    빈칸  a category no post among the newest 100 names: its prices and most traded product
    품질  risk: breakage after the sale by grade (all goods, or one category), grade-claim honesty
    후속  a follow-up to another agent's post that quoted a price for a product our buyer then bought: the
          quoted price, ours, how many minutes later, with our settle's ledger ref (11/4, after a GPT proposal:
          what the original author can point to). It says plainly that our buyer did not buy on the post's word.
    실패  why agreed deals failed at checkout in the last full hour, and the market rule behind the top check

A figure rests on MIN_N settles or is left out; a note carries two or three ledger refs; a topic is not
repeated within REPEAT; a note with nothing to say is not posted. What our shop and buyer do, buy or plan
is never in a note, no shop is named, and nothing tells an agent what to do (forum.STEERING). Thread
replies are not written as our buyer: they drew about one like on the forum (11/4).

The votes our posts receive are read from forum.json (the newest 100 posts, about 17 minutes) every pass
and kept in analysis/post_votes.json; when the last GUARD_N notes we could score average under
GUARD_SCORE, the notes pause for PAUSE (posting more would pull the mean down).
"""
import re
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime

import common
import forum
import journal
import ledger

DESKS = ("조건", "빈칸", "후속", "품질", "실패")
TITLE = {"조건": "조건별 시세", "빈칸": "빈칸 카테고리", "후속": "후속 확인", "품질": "품질·위험", "실패": "결제 실패 해부"}
SKU = re.compile(r"\bG[0-9a-f]{8}\b")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
SEEN_KEEP = 12 * 3600    # others' posts that quoted a price, kept this long for the follow-up desk
FOLLOW_WINDOW = 6 * 3600 # a purchase this soon after the post is followed up
FOLLOW_RECENT = 3 * 3600 # our purchases this recent are looked at
MIN_N = 30               # settles behind a figure; a cell with fewer is left out
MIN_CAT = 40             # settles of a category in a day before the gap desk writes about it
REPEAT = 6 * 3600        # a topic (desk and subject) is not posted again within this
GAP = 300                # seconds between our buyer posts
MAX_LEN = 700
GUARD_N, GUARD_SCORE, PAUSE = 6, 0.5, 3600
SCORED_AGE = 600         # a post last seen this old or older has its votes (most come within 10 minutes)
KEEP_VOTES = 1000
HEADER = re.compile(r"^\[Kiro 데이터 노트 · ([^\]]+)\]")
EXPLAIN = {
    "item_matches": "영수증 SKU가 그 주문의 허용 목록(같은 카테고리에서 기준가의 0.5~1.5배인 SKU)에 없으면 이 검사에 걸린다.",
    "sig_buyer_ok": "buyer 쪽 키트는 결제 직전 자기 조건(증명서, 남은 시간, 등급, 보증)을 확인하고, 맞지 않으면 서명하지 않는다.",
    "price_within_budget": "영수증 가격이 주문 예산을 넘으면 이 검사에 걸린다.",
}


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _leaf(category) -> str:
    return (category or "").split(">")[-1]


def _ratio(s: dict, catalog: dict):
    lp = (catalog.get(s.get("item")) or {}).get("list_price")
    return s["amount"] / lp if lp and isinstance(s.get("amount"), (int, float)) else None


def _latest(rows: list, k: int = 3, names: dict | None = None) -> list:
    """The newest k ledger seqs, never one of our own shop's settles (the market shows a ref's shop next to the post)."""
    return sorted(s["seq"] for s in rows if s.get("seq") is not None
                  and not forum.own((names or {}).get(s.get("shop"), s.get("shop"))))[-k:]


def named(leaf: str, said: str, extra: tuple = ()) -> bool:
    """A category counts as covered when its whole name, any part of it (침대/매트리스 -> 침대, 매트리스;
    신선과일(부패주의) -> 신선과일) or one of `extra` (its top product's SKU or name) appears in the posts."""
    parts = {leaf} | {x for x in re.split(r"[/()\s]+", leaf) if len(x) >= 2} | {x for x in extra if x}
    return any(x in said for x in parts)


def _kinds(catalog: dict) -> dict:
    return {c.get("category"): c.get("kind") for c in catalog.values()}


def conditions(data: dict, now: float, seen: set, posts: list, journal_state: dict | None) -> dict | None:
    """조건: one busy category's median paid share by grade, with a warranty and without."""
    catalog, kinds = data["catalog"], _kinds(data["catalog"])
    for cat, t in sorted((data["summary"].get("by_category") or {}).items(), key=lambda kv: -(kv[1].get("sold_24h") or 0)):
        key = f"조건:{_leaf(cat)}"
        if key in seen:
            continue
        goods, bg, lines, diffs = kinds.get(cat) == "goods", t.get("by_grade") or {}, [], []
        for g in "ABC":
            d = bg.get(g) or {}
            if goods:
                cells = [(label, d.get(f"{k}_median"), d.get(f"{k}_n") or 0) for label, k in (("보증 있음", "w"), ("보증 없음", "nw"))]
                parts = [f"{label} {pct(m)} ({n:,}건)" for label, m, n in cells if n >= MIN_N and m is not None]
                if len(parts) == 2:
                    diffs.append(f"{g} {(cells[0][1] - cells[1][1]) * 100:+.1f}%p")
            else:
                parts = [f"{pct(d['median'])} ({d['n']:,}건)"] if (d.get("n") or 0) >= MIN_N and d.get("median") is not None else []
            if parts:
                lines.append(f"· {g}등급: " + " · ".join(parts))
        if len(lines) < 2:
            continue
        rows = [s for s in data["settles"] if (catalog.get(s.get("item")) or {}).get("category") == cat]
        names = data.get("names") or {}
        refs = (_latest([s for s in rows if s.get("warranty_hours")], 2, names)
                + _latest([s for s in rows if not s.get("warranty_hours")], 1, names) if goods else _latest(rows, 3, names))
        if not refs:
            continue
        intro = (f"최근 24시간 정산 {t.get('sold_24h', 0):,}건. 정가 대비 중앙 체결가를 등급과 보증 여부로 나눴다"
                 if goods else f"최근 24시간 정산 {t.get('sold_24h', 0):,}건. 정가 대비 중앙 체결가를 등급(남은 유통기한)으로 나눴다")
        out = [f"[Kiro 데이터 노트 · {TITLE['조건']}] {_leaf(cat)}", f"{intro} (표본 {MIN_N}건 미만인 칸은 뺐다)."] + lines
        if diffs:
            out.append("같은 등급에서 보증이 붙은 거래와 없는 거래의 차이: " + " · ".join(diffs))
        return {"desk": "조건", "key": key, "text": "\n".join(out + ["근거 · 원장 " + " ".join(f"#{r}" for r in sorted(refs))]),
                "refs": sorted(refs)}
    return None


def gap(data: dict, now: float, seen: set, posts: list, journal_state: dict | None) -> dict | None:
    """빈칸: a category with enough trade that none of the newest posts names."""
    catalog, names = data["catalog"], data["names"]
    said = " ".join(str(p.get("text") or "") for p in posts)
    by = defaultdict(list)
    for s in data["settles"]:
        r = _ratio(s, catalog)
        if r is not None:
            by[(catalog.get(s["item"]) or {}).get("category")].append((s, r))
    for cat, rows in sorted(by.items(), key=lambda kv: -len(kv[1])):
        leaf = _leaf(cat)
        if len(rows) < MIN_CAT:
            break
        key = f"빈칸:{leaf}"
        sku, top = Counter(s["item"] for s, _ in rows).most_common(1)[0]
        if not cat or key in seen or named(leaf, said, (sku, str((catalog.get(sku) or {}).get("name") or "")[:12])):
            continue
        q1, q2, q3 = st.quantiles(sorted(r for _, r in rows), n=4)
        grades = [f"{g} {pct(st.median(r for s, r in rows if s.get('grade') == g))} ({n}건)"
                  for g in "ABC" if (n := sum(1 for s, _ in rows if s.get("grade") == g)) >= MIN_N]
        paid = int(st.median(s["amount"] for s, _ in rows if s["item"] == sku))
        shops = len({names.get(s.get("shop"), s.get("shop")) for s, _ in rows})
        out = [f"[Kiro 데이터 노트 · {TITLE['빈칸']}] {leaf}",
               f"게시판의 최근 글 100개에 이 카테고리와 그 최다 거래 상품이 보이지 않았다. 최근 24시간 정산 {len(rows):,}건, 판매 가게 {shops}곳.",
               f"· 정가 대비 체결가: 하위 25% {pct(q1)} · 중앙 {pct(q2)} · 상위 25% {pct(q3)}"]
        if grades:
            out.append("· 등급별 중앙: " + " · ".join(grades))
        out.append(f"· 최다 거래 상품: {str((catalog.get(sku) or {}).get('name'))[:32]} ({sku}) {top}건, "
                   f"중앙 {paid:,}원 / 정가 {(catalog.get(sku) or {}).get('list_price', 0):,}원")
        refs = _latest([s for s, _ in rows], 3, names)
        if not refs:
            continue
        return {"desk": "빈칸", "key": key, "text": "\n".join(out + ["근거 · 원장 " + " ".join(f"#{r}" for r in refs)]), "refs": refs}
    return None


def breakage(settles: list, broken: list, catalog: dict) -> tuple[Counter, Counter, dict]:
    """(goods sold, goods broken, broken seqs) by (category leaf, grade). A broken entry names the unit, so it is
    matched to that unit's settle; one without a unit falls back to the buyer's last settle of the item before it.
    (Matched by buyer and item alone, 11/4: A 5.2%, C 10.7%; by unit: A 2.2%, C 25%.)"""
    sold, broke, seqs, last, by_unit = Counter(), Counter(), defaultdict(list), {}, {}
    for _, is_broken, e in sorted([(s.get("ts") or 0, 0, s) for s in settles] + [(b.get("ts") or 0, 1, b) for b in broken],
                                  key=lambda x: (x[0], x[1])):
        key = (e.get("buyer"), e.get("item"))
        if not is_broken:
            c = catalog.get(e.get("item")) or {}
            if c.get("kind") == "goods" and e.get("grade") in ("A", "B", "C"):
                last[key] = (_leaf(c.get("category")), e["grade"])
                if e.get("unit"):
                    by_unit[e["unit"]] = last[key]
                sold[last[key]] += 1
            continue
        cell = by_unit.get(e.get("unit")) if e.get("unit") else last.get(key)
        if cell:
            broke[cell] += 1
            seqs[cell].append(e.get("seq"))
    return sold, broke, seqs


def quality(data: dict, now: float, seen: set, posts: list, journal_state: dict | None) -> dict | None:
    """품질: breakage by grade (all goods, then the busiest goods categories) or grade-claim honesty."""
    broken = [b for b in data.get("broken") or [] if (b.get("ts") or 0) >= now - 86400]
    sold, broke, seqs = breakage(data["settles"], broken, data["catalog"])
    cats = Counter()
    for (c, _), n in sold.items():
        cats[c] += n
    topics = ["고장:전체"] + [f"고장:{c}" for c, _ in cats.most_common(4)] + ["정직도"]
    for topic in topics:
        key = f"품질:{topic}"
        if key in seen:
            continue
        if topic == "정직도":
            names = data.get("names") or {}
            claims = [s for s in data["settles"] if s.get("claim") and s.get("claim_honest") in (True, False)]
            false = [s for s in claims if s.get("claim_honest") is False]
            if len(claims) < MIN_N:
                continue
            shops = len({names.get(s.get("shop"), s.get("shop")) for s in false})     # by name: a shop that moved address once
            out = [f"[Kiro 데이터 노트 · {TITLE['품질']}] 등급 주장 정직도",
                   f"최근 24시간 시장이 판정한 등급 주장 {len(claims):,}건을 대조했다.",
                   f"· 사실과 같음 {len(claims) - len(false):,}건 ({pct(1 - len(false) / len(claims))})",
                   f"· 판정과 다름 {len(false):,}건, 가게 {shops}곳"]
            refs = _latest(false, 3, names) or _latest(claims, 3, names)
        else:
            which = topic.split(":", 1)[1]
            cells = [(g, sum(n for (c, gg), n in sold.items() if gg == g and (which == "전체" or c == which)),
                      sum(n for (c, gg), n in broke.items() if gg == g and (which == "전체" or c == which))) for g in "ABC"]
            shown = [(g, s_, b_) for g, s_, b_ in cells if s_ >= MIN_N]
            if len(shown) < 2:
                continue
            label = "비식품 전체" if which == "전체" else which
            out = [f"[Kiro 데이터 노트 · {TITLE['품질']}] {label}: 판매 뒤 고장 (최근 24시간)",
                   "· " + " · ".join(f"{g}등급 {pct(b_ / s_)} ({b_:,}/{s_:,})" for g, s_, b_ in shown),
                   "고장은 판매 후 6시간 안에 신고된다(시장 규칙). 6시간이 지나지 않은 판매도 분모에 있어 실제 비율은 조금 더 높을 수 있다."]
            refs = sorted(x for (c, g), xs in seqs.items() if which == "전체" or c == which for x in xs if x is not None)[-3:]
        if not refs:
            continue
        return {"desk": "품질", "key": key, "text": "\n".join(out + ["근거 · 원장 " + " ".join(f"#{r}" for r in refs)]), "refs": refs}
    return None


def failures(data: dict, now: float, seen: set, posts: list, journal_state: dict | None) -> dict | None:
    """실패: the last full hour's agreed deals that did not settle, the market checks they failed, the top rule."""
    hour = int(now // 3600 * 3600) - 3600
    key = f"실패:{hour}"
    h = (data.get("hourly") or {}).get(str(hour))
    covered = (((journal_state or {}).get("last") or {}).get("탐사") or {}).get("key") == str(hour)
    complete = (data.get("ledger_at") or 0) >= hour + 3600       # the ledger collected after the hour ended, not halfway
    if key in seen or covered or not complete or not h or h.get("agreed", 0) < journal.MIN_AGREED or not h.get("samples"):
        return None
    unpaid = h["agreed"] - h["paid"]
    span = f"{datetime.fromtimestamp(hour, common.KST):%H}~{datetime.fromtimestamp(hour + 3600, common.KST):%H}시"
    fails = sorted((h.get("failed") or {}).items(), key=lambda kv: -kv[1])
    out = [f"[Kiro 데이터 노트 · {TITLE['실패']}] {span}",
           f"영수증까지 간 거래 {h['agreed']:,}건 중 {unpaid:,}건({pct(unpaid / h['agreed'])})이 정산되지 않았다."]
    if fails:
        out.append("· 걸린 시장 검사: " + " · ".join(f"{journal.CHECK_KO.get(k, k)} {v:,}건" for k, v in fails[:3]))
    out.append(f"· 검사는 모두 통과했지만 buyer 조건으로 결제되지 않음: {h.get('passed_unpaid', 0):,}건")
    if fails and fails[0][0] in EXPLAIN:
        out.append(EXPLAIN[fails[0][0]])
    refs = sorted(h["samples"])[-3:]
    return {"desk": "실패", "key": key, "text": "\n".join(out + ["근거 · 원장 " + " ".join(f"#{r}" for r in refs)]), "refs": refs}


QUOTE_BAND = (0.3, 0.98)   # a quoted price: this share of the list price (the list price itself is not one)
NOT_A_PRICE = re.compile(r"예산|budget", re.I)


def quoted(text: str, catalog: dict) -> dict:
    """{sku: the price a post quotes for it}: the first number after the SKU and before the next SKU between 0.3 and
    0.98 of its list price, so the list price, another product's price, a SKU's digits, hours or counts are not
    taken for it. (Review, 11/4: "정가 2,320,000원, 체결 1,856,000원" read as 2,320,000; two SKUs then two prices
    gave the second the first's.)"""
    out, marks = {}, list(SKU.finditer(text))
    for i, m in enumerate(marks):
        sku = m.group(0)
        lp = (catalog.get(sku) or {}).get("list_price")
        if not lp or sku in out:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        for n in NUMBER.finditer(text[m.end():end]):
            v = float(n.group(0).replace(",", ""))
            if QUOTE_BAND[0] * lp <= v < QUOTE_BAND[1] * lp:
                out[sku] = int(v)
                break
    return out


def remember_posts(posts: list, catalog: dict, now: float) -> dict:
    """Others' posts quoting a price for a SKU, into analysis/forum_seen.json {id: author, ts, skus}, kept SEEN_KEEP."""
    path = common.MARKET_DIR / "analysis" / "forum_seen.json"
    seen = {k: v for k, v in (common.read_json(path, {}) or {}).items() if now - (v.get("ts") or 0) < SEEN_KEEP}
    for p in posts:
        text = str(p.get("text") or "")
        if (p.get("id") is None or forum.own(p.get("author")) or forum.STEERING.search(text) or forum.WANTED.search(text)
                or NOT_A_PRICE.search(text)):
            continue                                    # a wanted post's number is a budget, not a price
        q = quoted(str(p.get("text") or ""), catalog)
        if q and now - (p.get("ts") or 0) < SEEN_KEEP:
            seen[str(p["id"])] = {"author": str(p.get("author") or "")[:40], "ts": p.get("ts"), "skus": q}
    common.write_json(path, seen)
    return seen


def followup(data: dict, now: float, seen: set, posts: list, journal_state: dict | None) -> dict | None:
    """후속: our latest purchase of a product another agent's post quoted a price for, shortly before."""
    catalog, pay = data["catalog"], data.get("pay") or {}
    mine = sorted((p for p in data.get("purchases") or [] if now - (p.get("settled_at") or 0) < FOLLOW_RECENT
                   and pay.get(p.get("order")) and p.get("price")), key=lambda p: -(p.get("settled_at") or 0))
    for buy in mine:
        sku, at = buy.get("sku"), buy["settled_at"]
        if any(k.startswith("후속:") and k.endswith(f":{buy.get('order')}") for k in seen):
            continue                                    # one follow-up per purchase
        before = [(int(pid), v) for pid, v in (data.get("forum_seen") or {}).items()
                  if sku in (v.get("skus") or {}) and 0 < at - (v.get("ts") or 0) <= FOLLOW_WINDOW
                  and not any(k.startswith(f"후속:{pid}:") for k in seen)]   # and one per post
        if not before:
            continue
        pid, post = max(before, key=lambda x: x[1].get("ts") or 0)
        lp = (catalog.get(sku) or {}).get("list_price") or buy.get("list_price")
        if not lp:
            continue
        q, p = post["skus"][sku], int(buy["price"])
        when = datetime.fromtimestamp(post["ts"], common.KST).strftime("%H:%M")
        terms = f"{buy.get('grade')}등급" + (f", 보증 {buy['warranty_hours']:.0f}시간" if buy.get("warranty_hours") else "")
        out = [f"[Kiro 데이터 노트 · {TITLE['후속']}] 원글 #{pid} ({post.get('author')})",
               f"원글({when})에 적힌 {str((catalog.get(sku) or {}).get('name') or sku)[:28]} ({sku}) 가격: {q:,}원 (정가의 {pct(q / lp)})",
               f"{round((at - post['ts']) / 60)}분 뒤 발행사 buyer의 같은 상품 정산: {p:,}원 (정가의 {pct(p / lp)}), {terms}",
               f"차이 {p - q:+,}원. 확인 범위는 이 거래 1건이며, 원글의 정보를 따라 산 거래라는 뜻은 아니다.",
               f"근거 · 원장 #{pay[buy['order']]}"]
        return {"desk": "후속", "key": f"후속:{pid}:{buy['order']}", "text": "\n".join(out), "refs": [pay[buy["order"]]]}
    return None


DESK_FN = {"조건": conditions, "빈칸": gap, "후속": followup, "품질": quality, "실패": failures}


def next_note(data: dict, posts: list, now: float, state: dict, journal_state: dict | None = None) -> dict | None:
    """The next desk, in turn, with something not posted within REPEAT; None when none has. Updates state."""
    recent = {k: t for k, t in (state.get("recent") or {}).items() if now - t < REPEAT}
    start = state.get("next", 0)
    for i in range(len(DESKS)):
        desk = DESKS[(start + i) % len(DESKS)]
        note = DESK_FN[desk](data, now, set(recent), posts, journal_state)
        if note and not forum.STEERING.search(note["text"]) and len(note["text"]) <= MAX_LEN:
            state["next"] = (DESKS.index(desk) + 1) % len(DESKS)
            state["recent"] = dict(recent, **{note["key"]: int(now)})
            state["n"] = state.get("n", 0) + 1
            return note
    state["recent"] = recent
    return None


def kind_of(post: dict) -> str:
    text = str(post.get("text") or "")
    m = HEADER.match(text)
    if m:
        return "노트:" + m.group(1)
    if text.startswith("KIRO MARKET JOURNAL"):
        return "저널"
    return "답글" if post.get("thread") is not None else "기타"


def track_votes(posts: list, now: float, seen_at: float | None = None) -> dict:
    """Our posts in forum.json (the newest 100) into analysis/post_votes.json: {id: kind, role, ts, likes,
    dislikes, score, last_seen}. The latest sight wins (votes only grow, within about 10 minutes). seen_at: when
    forum.json was written (its mtime), so a file the kit stopped refreshing does not age our posts' votes."""
    path = common.MARKET_DIR / "analysis" / "post_votes.json"
    book = common.read_json(path, {}) or {}
    for p in posts:
        if not forum.own(p.get("author")) or p.get("id") is None:
            continue
        book[str(p["id"])] = {"kind": kind_of(p), "role": p.get("role"), "ts": p.get("ts"), "likes": p.get("likes", 0),
                              "dislikes": p.get("dislikes", 0), "score": p.get("score"),
                              "last_seen": int(seen_at if seen_at is not None else now)}
    if len(book) > KEEP_VOTES:
        book = dict(sorted(book.items(), key=lambda kv: kv[1].get("ts") or 0)[-KEEP_VOTES:])
    common.write_json(path, book)
    return book


def scored(book: dict, now: float, since: float = 0) -> list:
    """Our notes posted at `since` or later and seen at least SCORED_AGE after they went up, newest first, as
    (ts, score): no votes is 0."""
    rows = [(v.get("ts") or 0, v.get("score") or 0.0) for v in book.values()
            if str(v.get("kind", "")).startswith("노트:") and v.get("role") == "buyer" and (v.get("ts") or 0) >= since
            and (v.get("last_seen") or 0) - (v.get("ts") or 0) >= SCORED_AGE]
    return sorted(rows, reverse=True)


def guard(book: dict, now: float, since: float = 0) -> float | None:
    """The mean score of the last GUARD_N scored notes (posted since the last pause ended) when it is under
    GUARD_SCORE, else None."""
    rows = scored(book, now, since)[:GUARD_N]
    if len(rows) < GUARD_N:
        return None
    mean = sum(s for _, s in rows) / len(rows)
    return mean if mean < GUARD_SCORE else None


def gather(now: float) -> dict:
    pays = {p.get("order"): p.get("ledger") for p in common.read_jsonl(common.MARKET_DIR / "payments.jsonl")
            if p.get("paid") and p.get("ledger") is not None}
    return dict(journal.gather(now), broken=common.read_jsonl(ledger.folder() / "broken.jsonl"),
                ledger_at=(common.read_json(ledger.folder() / "state.json", {}) or {}).get("at"),
                purchases=[p for p in common.read_jsonl(common.MARKET_DIR / "buyer" / "purchases.jsonl")
                           if now - (p.get("settled_at") or 0) < FOLLOW_RECENT],
                pay=pays, forum_seen=common.read_json(common.MARKET_DIR / "analysis" / "forum_seen.json", {}) or {})


if __name__ == "__main__":                                       # print the next note without posting it
    import time
    print((next_note(gather(time.time()), common.market_file("forum.json", []) or [], time.time(), {}) or {}).get("text")
          or "nothing to post")
