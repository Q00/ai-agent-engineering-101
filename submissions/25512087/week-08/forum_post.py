"""Buyer forum posts (week 08, 11/04): honest reports from our own paid deals, at most one post per FORUM_GAP_S.

Forum votes on posts written as a buyer are 0.3 of buyer ALL. What other buyers can use: what a SKU really cost
against its list price, the grade that arrived, which shop sold it, with ledger refs anyone can check. Two kinds:
- "[구매 기록]": up to 3 of our newest paid deals not posted yet (name, price, % of list, grade, shop, ledger #);
- "[시세]" (every 3rd post): median price / list price per category over our last 120 paid deals (n >= 4).
Our shop's kit reads the same outbox and may send a line as the shop; check_last() notices that in the kit log
and frees the buyer's slot again. Rules we keep: only facts from our ledger entries, no promotion of our own shop, nothing about rivals beyond the
recorded deal, no votes (no vote rings). Called at the end of market_buyer.py; it only appends a line to
.market/outbox.jsonl (the kit sends it) and never prints to stdout.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
from pathlib import Path

FORUM_GAP_S = 330
PAID = re.compile(r"\[acp\] paid (\d+) through (\S+), settled at ledger #(\d+): (.+?), grade (\w)\b")
OWN_SHOPS = {"ggari-shop"}


def _load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _tail(p: Path, nbytes: int = 600_000) -> str:
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - nbytes))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def recent_deals(md: Path, log: Path) -> list[dict]:
    """Our paid deals, newest last: ledger, price, shop url/name, name, grade, sku, list price, category."""
    sku_of = {}
    for raw in (md / "payments.jsonl").read_text(encoding="utf-8").splitlines()[-400:] if (md / "payments.jsonl").exists() else []:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if r.get("paid") and r.get("ledger"):
            sku_of[int(r["ledger"])] = r.get("item")
    names = {}
    for raw in (md / "buyer-memory.jsonl").read_text(encoding="utf-8").splitlines()[-2000:] if (md / "buyer-memory.jsonl").exists() else []:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if r.get("url") and r.get("shop"):
            names[str(r["url"]).rstrip("/")] = r["shop"]
    cat = _load(md / "catalog.json", {})
    out = []
    for m in PAID.finditer(_tail(log)):
        seq = int(m.group(3))
        sku = sku_of.get(seq)
        info = cat.get(sku) or {}
        out.append({"ledger": seq, "price": int(m.group(1)), "url": m.group(2).rsplit("/acp", 1)[0],
                    "shop": names.get(m.group(2).rsplit("/acp", 1)[0].rstrip("/"), "?"), "name": m.group(4).strip(),
                    "grade": m.group(5), "sku": sku, "list": int(info.get("list_price") or 0),
                    "cat": str(info.get("category") or "").split(">")[-1]})
    return out


def compose(deals: list[dict], state: dict) -> dict | None:
    posted = set(state.get("posted", []))
    n = int(state.get("n", 0))
    if n % 3 == 2:                                        # every 3rd post: category price statistics
        by = {}
        for d in deals[-120:]:
            if d["list"] and d["cat"]:
                by.setdefault(d["cat"], []).append(d)
        rows = sorted(((c, v) for c, v in by.items() if len(v) >= 4), key=lambda cv: -len(cv[1]))[:5]
        if rows:
            lines = [f"- {c}: 중앙값 {statistics.median(x['price'] / x['list'] for x in v):.0%} "
                     f"(최저 {min(x['price'] / x['list'] for x in v):.0%}, {len(v)}건)" for c, v in rows]
            refs = [x["ledger"] for _, v in rows for x in v[-1:]]
            return {"post": "[시세] 우리 buyer가 최근 결제한 거래 기준, 정가 대비 실제 지불가\n" + "\n".join(lines)
                    + "\n(각 줄의 최근 거래 원장 번호를 refs에 붙였습니다)", "refs": refs, "as": "buyer"}
    fresh = [d for d in deals if d["ledger"] not in posted and d["list"] and d["shop"] not in OWN_SHOPS][-3:]
    if not fresh:
        return None
    lines = [f"- {(d['name'][:38] + '…') if len(d['name']) > 38 else d['name']}: {d['price']:,}원 (정가 {d['list']:,}원의 {d['price'] / d['list']:.0%}), "
             f"등급 {d['grade']}, 판매 {d['shop']}, 원장 #{d['ledger']}" for d in fresh]
    return {"post": "[구매 기록] 정산 확인된 최근 구매\n" + "\n".join(lines), "refs": [d["ledger"] for d in fresh],
            "as": "buyer"}


POSTED_AS = re.compile(r"\[forum\] post as (\w+): (\d+)")


def check_last(state: dict, log: Path) -> None:
    """11/04: our shop's kit shares .market/ and also sends outbox lines (as the shop). If the last post went out as
    the shop, the buyer's 5-minute slot was not used and those deals were not posted as the buyer: free both."""
    off = state.get("log_off")
    if off is None:
        return
    try:
        with log.open("rb") as f:
            f.seek(int(off))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return
    m = POSTED_AS.search(text)
    if not m:
        return
    state["log_off"] = None
    if m.group(1) == "shop":
        pending = set(state.get("pending", []))
        state["posted"] = [r for r in state.get("posted", []) if r not in pending]
        state["last"] = 0
        state["n"] = max(0, int(state.get("n", 0)) - 1)
        state["as_shop"] = int(state.get("as_shop", 0)) + 1
    else:
        state["as_buyer"] = int(state.get("as_buyer", 0)) + 1


def maybe_post(md: Path = Path(".market"), log: Path = Path("logs/market-kit.log"), now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    sp = md / "memory" / "forum.json"
    state = _load(sp, {})
    box = md / "outbox.jsonl"
    if not (box.exists() and box.stat().st_size > 0) and state.get("log_off") is not None:
        check_last(state, log)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(state), encoding="utf-8")
    if now - float(state.get("last", 0)) < FORUM_GAP_S or (box.exists() and box.stat().st_size > 0):
        return None
    post = compose(recent_deals(md, log), state)
    if post is None:
        return None
    with box.open("a", encoding="utf-8") as f:
        f.write(json.dumps(post, ensure_ascii=False) + "\n")
    state["posted"] = (state.get("posted", []) + [r for r in post["refs"]])[-500:]
    state.update(last=now, n=int(state.get("n", 0)) + 1, pending=post["refs"],
                 log_off=log.stat().st_size if log.exists() else None)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state), encoding="utf-8")
    print(f"[forum] queued: {post['post'][:80]!r}", file=sys.stderr, flush=True)
    return post


if __name__ == "__main__":
    print(json.dumps(compose(recent_deals(Path(".market"), Path("logs/market-kit.log")), {"n": 0}), ensure_ascii=False, indent=1))
