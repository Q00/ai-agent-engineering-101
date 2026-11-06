"""Buyer forum posts (strategy D11): one factual line per settled purchase, rate limited, and a market price
report for the 5-minute slots a deal post did not fill.

    compose         the post text and refs from the settled facts, the receipt and the catalog
    maybe_post      append it to .market/outbox.jsonl (the kit sends it) unless a limit says no
    compose_report  one SKU's public-ledger price report (settles per hour, median paid/list, cheapest shops)
    maybe_report    queue a report when the last post is older than the gap plus a grace minute (restock hook)

Buyer ALL counts our buyer posts (0.1) and the votes on them (0.3), so posts carry only facts other
buyers can check against the ledger: never the budget, never our shop, no judgement beyond the facts.
Limits and dedup live in memory/forum.json.
"""
import unicodedata

from memory.decisions import decision
from memory.store import MEM, append_jsonl, atomic_write_json, read_json

DAY_S = 86400
REPORT_GRACE_S = 60     # a report waits this long past the gap, so a deal post gets the slot first
REPORT_TOP_N = 30       # reports rotate over the market's top SKUs by settles per hour
REPORT_MIN_SHOPS = 2    # a SKU needs this many shops in the price book to be reported
KEEP_LEDGERS = 1000     # ledgers remembered for dedup
NAME_MAX = 40           # shop and product names come from other agents: one short line each
TEXT_MAX = 300


def clean(s, n=NAME_MAX):
    """Untrusted text as one short line: control characters (newlines too) become spaces."""
    s = "".join(" " if unicodedata.category(c).startswith("C") else c for c in str(s))
    return " ".join(s.split())[:n]


def shop_name(shops, url):
    url = str(url or "").rstrip("/")
    for s in shops or []:
        if isinstance(s, dict) and str(s.get("url") or "").rstrip("/") == url and s.get("name"):
            return clean(s["name"])
    return None


def compose(deal, shops, catalog, now):
    """(text, refs) or None when there is no ledger or no paid price. Missing facts are left out."""
    settled, receipt = deal.get("settled") or {}, deal.get("receipt") or {}
    ledger, price = settled.get("ledger"), receipt.get("price")
    if type(ledger) is not int or type(price) is not int:
        return None
    sku = settled.get("sku") or receipt.get("item")
    item = (catalog or {}).get(sku) or {}
    name = clean(settled.get("name") or item.get("name") or "")
    sku_s = clean(sku) if sku else ""
    parts = [" ".join(x for x in ("[구매 기록]", name, f"({sku_s})" if name and sku_s else sku_s) if x)]
    lp = item.get("list_price")
    if isinstance(lp, (int, float)) and lp > 0:
        if price == lp:
            rel = "같은 값"
        else:
            r = abs(round((lp - price) / lp * 100))
            rel = f"{f'{r}%' if r else '1% 미만'} {'저렴' if price < lp else '비쌈'}"
        parts.append(f"{price:,}원, 정가 {int(lp):,}원 대비 {rel}")
    else:
        parts.append(f"{price:,}원")
    if settled.get("grade"):
        g = f"등급 {clean(settled['grade'], 8)}"
        honest = {True: "정직", False: "부정확"}.get(settled.get("claim_honest"))
        if settled.get("claim") and honest:
            g += f" (주장 {clean(settled['claim'], 8)}, {honest})"
        parts.append(g)
    if (item.get("kind") or (deal.get("order") or {}).get("kind")) == "food":
        exp = settled.get("expires_at")
        if isinstance(exp, (int, float)) and exp > now:
            h = (exp - now) / 3600
            parts.append(f"남은 유통기한 {h:.0f}시간" if h >= 1 else "남은 유통기한 1시간 미만")
    else:
        w = settled.get("warranty_hours")
        parts.append(f"보증 {w:g}h" if isinstance(w, (int, float)) and w > 0 else "보증 없음")
    s = shop_name(shops, settled.get("shop"))
    if s:
        parts.append(f"판매: {s}")
    parts.append(f"ledger #{ledger}")
    return " | ".join(parts)[:TEXT_MAX], [ledger]


def _label(names, url):
    """A shop's name from memory/shop_names.json, else the host of its URL."""
    u = str(url or "").rstrip("/")
    n = (names or {}).get(u)
    if n:
        return clean(n)
    host = u.split("//", 1)[-1].split("/", 1)[0]
    return clean(host.split(".")[0] or u, 24)


def compose_report(sku, summary, catalog, names, now):
    """(text, refs) for one SKU from the public ledger folds, or None without enough data. Every number is
    the market's own record: settles per hour over 6 h, the shops' 24 h median paid/list and the cheapest
    shops by that median, with the newest settle seqs as refs."""
    from statistics import median
    from memory import query
    item = (catalog or {}).get(sku) or {}
    rate = query.market_rate(summary, sku)
    book = query.sku_price_book(summary, sku)
    if not rate or len(book) < REPORT_MIN_SHOPS:
        return None
    shares = sorted(((v["share"], v["n"], url) for url, v in book.items()), key=lambda x: (x[0], x[2]))
    med = median([s for s, _, _ in shares])
    name = clean(item.get("name") or sku)
    n48 = query.market_settles_48h(summary, sku)
    parts = [f"[시세] {name} ({clean(sku)})",
             f"최근 6시간 시장 정산 시간당 {rate:.1f}건" + (f", 48시간 {n48}건" if n48 else "") + f", 판매 가게 {len(book)}곳 (공개 원장)",
             f"가게별 24시간 체결가/정가 중앙값의 중앙 {med * 100:.0f}%"]
    lp = item.get("list_price")
    if isinstance(lp, (int, float)) and lp > 0:
        parts[-1] += f" (정가 {int(lp):,}원, 약 {int(round(lp * med)):,}원)"
    cheapest = ", ".join(f"{_label(names, url)} {s * 100:.0f}% ({n}건)" for s, n, url in shares[:3])
    parts.append(f"가장 싼 가게: {cheapest}")
    refs = query.sku_seqs(summary, sku)
    if refs:
        parts.append("근거: " + ", ".join(f"ledger #{r}" for r in refs))
    return " | ".join(parts)[:TEXT_MAX], refs


def pick_report_sku(summary, reported, now, repeat_h):
    """The best-selling SKU with price-book data from REPORT_MIN_SHOPS shops, not reported in repeat_h hours."""
    from memory import query
    rates = (summary or {}).get("market", {}).get("sku_rate") if isinstance(summary, dict) else None
    if not isinstance(rates, dict):
        return None
    top = sorted(((v, k) for k, v in rates.items() if isinstance(v, (int, float)) and v > 0), reverse=True)
    for _, sku in top[:REPORT_TOP_N]:
        last = (reported or {}).get(sku)
        if isinstance(last, (int, float)) and now - last < repeat_h * 3600:
            continue
        if len(query.sku_price_book(summary, sku)) >= REPORT_MIN_SHOPS:
            return sku
    return None


def _skip(policy_ver, ref, why):
    decision("forum", "post", policy_ver=policy_ver, ref=ref, rules=["D11"], choice={"posted": False},
             why=f"skipped: {why}")
    return False


def maybe_post(market_dir, deal, pol, now, policy_ver=None):
    """Append one buyer post for this deal to market_dir/outbox.jsonl. True when posted."""
    f = pol["forum"]
    ledger = (deal.get("settled") or {}).get("ledger")
    ref = {"order": (deal.get("order") or {}).get("order"), "ledger": ledger}
    if not f["enabled"]:
        return _skip(policy_ver, ref, "disabled")
    if type(ledger) is not int:
        return _skip(policy_ver, ref, "no_ledger")
    state = read_json(MEM / "forum.json", {}) or {}
    ledgers = state.get("ledgers") or []
    posts = [t for t in state.get("posts") or [] if now - t < DAY_S]
    if ledger in ledgers:
        return _skip(policy_ver, ref, "duplicate")
    post = compose(deal, read_json(market_dir / "shops.json", []), read_json(market_dir / "catalog.json", {}), now)
    if post is None:
        return _skip(policy_ver, ref, "unpaid")
    if posts and now - posts[-1] < f["min_gap_min"] * 60:
        return _skip(policy_ver, ref, "gap")
    if len(posts) >= f["max_per_day"]:
        return _skip(policy_ver, ref, "daily_cap")
    text, refs = post
    append_jsonl(market_dir / "outbox.jsonl", {"post": text, "refs": refs, "thread": None, "as": "buyer"})
    atomic_write_json(MEM / "forum.json", {"ledgers": (ledgers + [ledger])[-KEEP_LEDGERS:], "posts": posts + [now]})
    decision("forum", "post", policy_ver=policy_ver, ref=ref, inputs={"text": text, "refs": refs},
             rules=["D11"], choice={"posted": True}, why="posted")
    return True


def maybe_report(market_dir, summary, catalog, pol, now, policy_ver=None):
    """Queue one market report as the buyer when no deal post took the slot: the last post is older than
    forum.min_gap_min plus REPORT_GRACE_S. Same daily cap and post record as maybe_post. True when queued."""
    f = pol["forum"]
    if not f["enabled"] or not f.get("report_enabled", True):
        return False
    state = read_json(MEM / "forum.json", {}) or {}
    posts = [t for t in state.get("posts") or [] if now - t < DAY_S]
    if posts and now - posts[-1] < f["min_gap_min"] * 60 + REPORT_GRACE_S:
        return False                                     # a deal post may still take this slot: no row
    ref = {"kind": "report"}
    if len(posts) >= f["max_per_day"]:
        return _skip(policy_ver, ref, "daily_cap")
    reported = {k: v for k, v in (state.get("reported") or {}).items() if isinstance(v, (int, float)) and now - v < 7 * DAY_S}
    sku = pick_report_sku(summary, reported, now, f.get("report_repeat_h", 12))
    if not sku:
        return _skip(policy_ver, ref, "no_sku")
    post = compose_report(sku, summary, catalog, read_json(MEM / "shop_names.json", {}) or {}, now)
    if post is None:
        return _skip(policy_ver, dict(ref, sku=sku), "no_data")
    text, refs = post
    append_jsonl(market_dir / "outbox.jsonl", {"post": text, "refs": refs, "thread": None, "as": "buyer"})
    reported[sku] = now
    atomic_write_json(MEM / "forum.json", {"ledgers": (state.get("ledgers") or [])[-KEEP_LEDGERS:],
                                           "posts": posts + [now], "reported": reported})
    decision("forum", "post", policy_ver=policy_ver, ref=dict(ref, sku=sku), inputs={"text": text, "refs": refs},
             memory_refs=[f"sku:{sku}"], rules=["D11"], choice={"posted": True}, why="posted report")
    return True
