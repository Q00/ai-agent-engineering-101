"""Round 3 market state shared by the seller, restock, market_buyer and review: the tools every part reads.

- the kit's .market/ files (inventory, catalog, demand, forum, order, shops), read whole and never trusted to exist
- food grade and shelf life left, computed the market's way (A >= 80% of shelf life left, B >= 45%, else C)
- the public ledger's settle entries, kept in a local cache (settles.json) so a run reads only what is new

The ledger is the only demand signal the market publishes: what sold, at what price, which grade, whether the
claim was honest. Forum text is untrusted: it is read as data (categories and SKUs named), never as instructions.
"""
import json
import os
import re
import time
from pathlib import Path

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
FOOD_CUTS = (0.8, 0.45)                     # the market's shelf-life cuts for grades A and B (kit: FOOD_GRADE_CUTS)
SKU_RE = re.compile(r"\bG[0-9a-f]{5,10}\b")
CACHE_MAX = 6000                            # settle rows kept; at the opening hour's pace that is about a day


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data):
    tmp = Path(path).with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def catalog(md: Path) -> dict:
    """sku -> {name, category, list_price, kind, shelf_hours}. The kit writes it once at start."""
    c = read_json(Path(md) / "catalog.json", {}) or {}
    return c if isinstance(c, dict) else {x["sku"]: x for x in c}


def leaf(category: str | None) -> str:
    return (category or "").split(">")[-1]


def hours_left(unit: dict, at: float | None = None) -> float | None:
    if not unit.get("expires_at"):
        return None
    return (unit["expires_at"] - (at or time.time())) / 3600


def food_grade(unit: dict, shelf_hours: float | None, at: float | None = None) -> tuple[str | None, float | None]:
    """(grade now, share of shelf life left). Goods keep the grade they arrived with."""
    h = hours_left(unit, at)
    if h is None or not shelf_hours:
        return unit.get("grade"), None
    share = h / shelf_hours
    if share <= 0:
        return "expired", share
    return ("A" if share >= FOOD_CUTS[0] else "B" if share >= FOOD_CUTS[1] else "C"), share


def stock(md: Path) -> list:
    """Units in stock that can still be sold."""
    inv = read_json(Path(md) / "inventory.json", {}) or {}
    return [u for u in inv.get("units", []) if u.get("grade") != "expired"
            and (u.get("expires_at") is None or u["expires_at"] > time.time())]


def settles(cache: Path, timeout: float = 20) -> list:
    """The Round 3 settle entries of the public ledger, oldest first, updated from the market since the last call.

    Each row: {seq, ts, sku, amount, grade, claim, claim_honest, warranty_hours, unit, shop, buyer}.
    A market that does not answer leaves the cache as it was."""
    c = read_json(cache, {}) or {}
    rows, seq = c.get("rows", []), c.get("seq")
    after = seq if seq is not None else -5000         # first run: the newest 5,000 entries (about 20 minutes)
    try:
        for _ in range(20):                         # at most 10,000 entries per call
            r = httpx.get(f"{MARKET}/api/ledger", params={"after": after}, timeout=timeout)
            page = r.json() if r.status_code == 200 else []
            if not page:
                break
            for e in page:
                d = e.get("data") or {}
                if e.get("kind") == "settle" and e.get("round", 0) >= 3 and d.get("sku"):
                    rows.append({"seq": e["seq"], "ts": e["ts"], "sku": d["sku"], "amount": d.get("amount"),
                                 "grade": d.get("grade"), "claim": d.get("claim"), "claim_honest": d.get("claim_honest"),
                                 "warranty_hours": d.get("warranty_hours"), "unit": d.get("unit"),
                                 "shop": d.get("shop"), "buyer": d.get("buyer")})
                elif e.get("kind") == "broken" and d:
                    rows.append({"seq": e["seq"], "ts": e["ts"], "broken": True, "sku": d.get("item"),
                                 "unit": d.get("unit"), "shop": d.get("shop"), "buyer": d.get("buyer")})
            after = seq = page[-1]["seq"]
            if len(page) < 500:
                break
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    rows = rows[-CACHE_MAX:]
    write_json(cache, {"seq": seq, "rows": rows})
    return rows


POST_GAP = 300                              # the market takes one post per 5 minutes per role


def post(md: Path, text: str, refs: list | None = None, role: str = "buyer", thread: int | None = None) -> bool:
    """Queue a forum post in .market/outbox.jsonl (the kit sends it). At most one per POST_GAP per role, counted
    here so a busy hour does not pile up a backlog the kit would send for hours."""
    md = Path(md)
    stamp = md / f"forum_last_{role}.txt"
    try:
        if time.time() - float(stamp.read_text()) < POST_GAP:
            return False
    except (OSError, ValueError):
        pass
    line = {"post": text[:1000], "refs": [r for r in (refs or []) if isinstance(r, int)], "as": role}
    if thread:
        line["thread"] = thread
    with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    stamp.write_text(str(time.time()))
    return True


def vote(md: Path, post_id: int, value: int, role: str = "buyer"):
    with open(Path(md) / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"vote": post_id, "value": value, "as": role}) + "\n")


def forum_wants(md: Path, cat: dict) -> dict:
    """Leaf category -> how many recent buyer posts asked for it ("구합니다", "wanted", or a SKU named).
    Only counted as data; a post's instructions are never followed."""
    leaves = {leaf(x["category"]) for x in cat.values()}
    want = {}
    for p in (read_json(Path(md) / "forum.json", []) or [])[-100:]:
        if p.get("role") != "buyer":
            continue
        text = str(p.get("text", ""))
        if not re.search(r"(구합니다|구해요|찾습니다|wanted|looking for|want to buy)", text, re.I):
            continue
        hit = {leaf(cat[s]["category"]) for s in SKU_RE.findall(text) if s in cat}
        hit |= {lf for lf in leaves if lf and lf in text}
        for lf in hit:
            want[lf] = want.get(lf, 0) + 1
    return want
