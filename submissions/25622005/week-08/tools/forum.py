"""Buyer forum posts (strategy D11): one factual line per settled purchase, rate limited.

    compose    the post text and refs from the settled facts, the receipt and the catalog
    maybe_post append it to .market/outbox.jsonl (the kit sends it) unless a limit says no

Buyer ALL counts our buyer posts (0.1) and the votes on them (0.3), so posts carry only facts other
buyers can check against the ledger: never the budget, never our shop, no judgement beyond the facts.
Limits and dedup live in memory/forum.json.
"""
import unicodedata

from memory.decisions import decision
from memory.store import MEM, append_jsonl, atomic_write_json, read_json

DAY_S = 86400
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
