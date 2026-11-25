"""Watch verified 1-2 star reviews of our shop on the public ledger. Records and notifies only; no model, no reply, no refusal.

Reads the ledger pages that begin at our own new sales, known from local receipts: a review came at most 193 rows and 47 s after
its sale across 922 reviews, so a 500-row page holds it. Those pages also show how each buyer treats other shops, which
is what tells a rule applied to everyone from something about us. A class describes a pattern, never an intent.

Reviews are written by other agents and are untrusted: the text is cleaned and stored only in the alert log. It never reaches
a notice, the terminal, a prompt or a policy.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import ssl
import sys
import tempfile
import time
import unicodedata

ROOT = Path(__file__).resolve().parent
BASE = "https://market.ourolabs.ai"
LOW = 2
INTERVAL = 300
PAGES, BEHIND_PAGES = 2, 4
BEHIND_SALES, BEHIND_SECONDS = 6, 900
PAGE_ROWS_ENOUGH, SETTLE_WAIT = 300, 600
TIMEOUT, BUDGET_SECONDS = 4, 8
TEXT_LIMIT = 300
STATS_PER_BUYER = 100
ALL_SHOPS_MIN, ALL_SHOPS_SHOPS, ALL_SHOPS_SHARE = 10, 3, .9
PRICE_SHARE = .85
SKU = re.compile(r"^G[0-9a-f]{8}$")
FORMAT_TEXT = re.compile(r"receipt names|forges? receipts?", re.I)
ACCUSES = re.compile(r"scam|forge|fake|fraud|dishonest|lied|mismatch|사기|위조|거짓", re.I)
PRICE_TEXT = re.compile(r"paid\s+\d+(\.\d+)?%\s+of\s+list|expensive|overpric|비싸", re.I)
KST = timezone(timedelta(hours=9))
LABELS = {"all_shops_low": "전 가게 일괄 저평점형", "receipt_format": "영수증 형식 판정형", "quality_evidence": "고장·품질 근거 있음", "price_ratio": "가격 비율형", "unclassified": "미분류"}


def clean(text, limit=TEXT_LIMIT):
    """Drop control and formatting characters (escape codes, bidi overrides, line breaks) from text someone else wrote."""
    kept = "".join(" " if c in "\r\n\t" else c for c in str(text or "") if c in "\r\n\t" or unicodedata.category(c)[0] != "C")
    return kept[:limit]


def state_path(root):
    return Path(root) / ".market/low-star-monitor.json"


def read_json(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError):
        return default


def write_state(root, state):
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".low-star.", suffix=".tmp")
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False)
    os.replace(temporary, path)


def own_sales(root):
    records = read_json(Path(root) / ".market/local-settlements.json", {}).get("records") or {}
    records = records.values() if isinstance(records, dict) else records
    return sorted(((int(r["ledger"]), r.get("ts") or 0, r.get("unit")) for r in records
                   if r.get("source") == "local_acp_settled" and r.get("round") == 3 and r.get("ledger") is not None))


def alerted_seqs(root):
    """The alert log itself is the record of what was already raised, so a crash cannot repeat or lose one."""
    found = set()
    try:
        with (Path(root) / "logs/low-star-alerts.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    found.add(json.loads(line)["seq"])
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return found


def buyer_pattern(stats):
    """What this buyer's recent reviews of other shops show."""
    rows = stats or []
    low = [r for r in rows if r[2]]
    return {"n": len(rows), "low_share": len(low) / len(rows) if rows else None, "shops": len({r[1] for r in rows}), "shops_with_low": len({r[1] for r in low})}


def classify(review, sale, stats, catalog):
    """Returns (class, whether the text contradicts what the market recorded)."""
    data = review["data"]
    text = str(data.get("review") or "")
    item = str((data.get("receipt") or {}).get("item") or "")
    honest = sale.get("claim_honest") is not False
    accuses = bool(ACCUSES.search(text)) and honest and bool(data.get("verified"))
    pattern = buyer_pattern(stats)
    listed = (catalog.get(sale.get("item")) or {}).get("list_price")
    share = sale["amount"] / listed if listed and type(sale.get("amount")) in (int, float) else None
    if FORMAT_TEXT.search(text) and SKU.match(item):
        return "receipt_format", accuses
    if pattern["n"] >= ALL_SHOPS_MIN and pattern["low_share"] >= ALL_SHOPS_SHARE and pattern["shops_with_low"] >= ALL_SHOPS_SHOPS:
        return "all_shops_low", accuses
    if sale.get("broken") or sale.get("refund") or not honest:
        return "quality_evidence", accuses
    if share is not None and share >= PRICE_SHARE and (PRICE_TEXT.search(text) or (pattern["n"] >= ALL_SHOPS_MIN and pattern["low_share"] > 0)):
        return "price_ratio", accuses
    return "unclassified", accuses


def process_rows(rows, state, units, catalog, already, now):
    """Update the state from one page and return new alerts. Safe to see the same row twice."""
    orders, stats = state.setdefault("orders", {}), state.setdefault("stats", {})
    last = state.get("stats_seq", 0)
    alerts = []
    for row in sorted(rows, key=lambda r: r.get("seq", 0)):
        kind, data, seq = row.get("kind"), row.get("data") or {}, row.get("seq", 0)
        order = data.get("order")
        if kind == "settle" and data.get("unit") in units and order:
            orders.setdefault(order, {"seq": seq, "item": data.get("item"), "amount": data.get("amount"), "grade": data.get("grade"), "claim_honest": data.get("claim_honest")})
        elif kind in ("broken", "refund") and order in orders:
            orders[order]["broken" if kind == "broken" else "refund"] = True
        elif kind == "result" and data.get("verified") and type(data.get("stars")) in (int, float):
            if order in orders:
                state["own_reviews"] = state.get("own_reviews", 0) + (seq > last)
                if data["stars"] <= LOW and seq not in already:
                    sale = orders[order]
                    label, contradicts = classify(row, sale, stats.get(str(data.get("buyer"))), catalog)
                    listed = (catalog.get(sale.get("item")) or {}).get("list_price")
                    alerts.append({"at": now, "seq": seq, "settle_seq": sale["seq"], "buyer": clean(data.get("buyer"), 60), "stars": data["stars"], "price": sale.get("amount"), "sku": sale.get("item"),
                                   "grade": sale.get("grade"), "list_price": listed, "broken": bool(sale.get("broken")), "refund": bool(sale.get("refund")), "claim_honest": sale.get("claim_honest"),
                                   "receipt_item": clean((data.get("receipt") or {}).get("item"), 60), "review": clean(data.get("review")), "classification": label, "contradicts_record": contradicts})
                    already.add(seq)
            elif data.get("shop") and seq > last:
                bucket = stats.setdefault(str(data.get("buyer")), [])
                bucket.append([seq, data["shop"], data["stars"] <= LOW])
                del bucket[:-STATS_PER_BUYER]
        if kind == "result":
            state["stats_seq"] = max(state.get("stats_seq", 0), seq)
    if len(orders) > 300:
        for key in sorted(orders, key=lambda k: orders[k]["seq"])[:-300]:
            del orders[key]
    return alerts


def notify(root, alerts, state, now):
    """One line per buyer in a run; a new buyer, an unclassified review or text at odds with the record is marked at once. No review text."""
    known = set(state.setdefault("buyers_alerted", []))
    groups = {}
    for alert in alerts:
        groups.setdefault(alert["buyer"], []).append(alert)
    lines = []
    for buyer, rows in groups.items():
        labels = Counter(r["classification"] for r in rows)
        urgent = [word for word, flag in (("새 buyer", buyer not in known), ("미분류", "unclassified" in labels), ("기록과 어긋난 후기", any(r["contradicts_record"] for r in rows))) if flag]
        kinds = ", ".join(f"{LABELS.get(k, k)} {n}" for k, n in labels.most_common())
        lines.append(("[별점] " + ("주의(" + ", ".join(urgent) + ") " if urgent else "")
                      + f"{buyer} 저평점 {len(rows)}건: {kinds}; 최근 seq {rows[-1]['seq']}, SKU {clean(rows[-1]['sku'], 20)}, 등급 {clean(rows[-1]['grade'], 4)}"))
        known.add(buyer)
    state["buyers_alerted"] = sorted(known)
    if not lines:
        return []
    stamp = datetime.fromtimestamp(now, KST).strftime("%m-%d %H:%M")
    for line in lines:
        print(clean(line, 400), file=sys.stderr, flush=True)
    with (Path(root) / "MONITORING-NOTICES.md").open("a", encoding="utf-8") as stream:
        for line in lines:
            stream.write(f"- {stamp} {clean(line, 400)}\n")
    return lines


def observe(root=ROOT, now=None, client=None, clock=time.monotonic):
    """One bounded pass, at most every INTERVAL seconds. Never raises: an unavailable ledger just waits for the next pass."""
    now = time.time() if now is None else now
    try:
        return _observe(Path(root), now, client, clock)
    except Exception as error:  # A monitor must never disturb the hook that calls it.
        print(f"[별점] 확인 보류: {type(error).__name__}", file=sys.stderr, flush=True)
        return {"status": "deferred", "error": type(error).__name__}


def _observe(root, now, client, clock):
    state = read_json(state_path(root), {})
    if type(state.get("last_run_at")) in (int, float) and 0 <= now - state["last_run_at"] < INTERVAL:
        return {"status": "waiting"}
    sales = own_sales(root)
    if "cursor" not in state:
        # First run: only sales from now on, so earlier reviews are not raised again.
        state.update(cursor=sales[-1][0] if sales else 0, started_at=now, start_sale_seq=sales[-1][0] if sales else 0, last_run_at=now)
        write_state(root, state)
        return {"status": "started", "cursor": state["cursor"]}
    state["last_run_at"] = now
    pending = [s for s in sales if s[0] > state["cursor"]]
    behind = len(pending) > BEHIND_SALES or (pending and now - pending[0][1] > BEHIND_SECONDS)
    limit = BEHIND_PAGES if behind else PAGES
    units = {s[2] for s in sales if s[2]}
    catalog = read_json(root / ".market/catalog.json", {})
    already = alerted_seqs(root)
    day = datetime.fromtimestamp(now, KST).strftime("%Y-%m-%d")
    calls = state.setdefault("calls", {})
    alerts, used, started = [], 0, clock()
    own_client = client is None
    if own_client and pending:
        import httpx
        client = httpx.Client(verify=ssl.create_default_context(), timeout=TIMEOUT, trust_env=False)
    try:
        while pending and used < limit and clock() - started < BUDGET_SECONDS:
            response = client.get(BASE + "/api/ledger", params={"after": pending[0][0] - 1})
            used += 1
            if response.status_code != 200:
                state["last_error"] = f"http_{response.status_code}"
                break
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                break
            alerts += process_rows(rows, state, units, catalog, already, now)
            covered = max(r.get("seq", 0) for r in rows)
            done = [s for s in pending if covered - s[0] >= PAGE_ROWS_ENOUGH or (s[0] <= covered and now - s[1] >= SETTLE_WAIT)]
            if not done:
                break  # The newest sales at the end of the ledger: their reviews may still be coming.
            state["cursor"] = max(s[0] for s in done)
            pending = [s for s in pending if s[0] > state["cursor"]]
    finally:
        if own_client and client is not None:
            client.close()
    calls[day] = calls.get(day, 0) + used
    for key in sorted(calls)[:-7]:
        del calls[key]
    state["pending_sales"] = len(pending)
    if alerts:
        path = root / "logs/low-star-alerts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            for alert in alerts:
                stream.write(json.dumps(alert, ensure_ascii=False) + "\n")
    notify(root, alerts, state, now)
    write_state(root, state)
    return {"status": "ok", "calls": used, "alerts": len(alerts), "pending_sales": len(pending)}


def status(root=ROOT, now=None):
    root = Path(root)
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, KST).strftime("%Y-%m-%d")
    alerts = []
    try:
        with (root / "logs/low-star-alerts.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    alerts.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    today = [a for a in alerts if datetime.fromtimestamp(a["at"], KST).strftime("%Y-%m-%d") == day]
    state = read_json(state_path(root), {})
    summary = {}
    for alert in alerts:
        row = summary.setdefault(alert["buyer"], {"total": 0, "today": 0, "classes": Counter(), "last_seq": None})
        row["total"] += 1
        row["today"] += alert in today
        row["classes"][alert["classification"]] += 1
        row["last_seq"] = alert["seq"]
    return {"today": day, "low_reviews_today": len(today), "low_reviews_total": len(alerts), "by_buyer": {k: dict(v, classes=dict(v["classes"])) for k, v in summary.items()},
            "own_reviews_seen": state.get("own_reviews", 0), "started_at": state.get("started_at"), "start_sale_seq": state.get("start_sale_seq"), "cursor": state.get("cursor"),
            "pending_sales": state.get("pending_sales"), "last_run_at": state.get("last_run_at"), "last_error": state.get("last_error"), "calls_by_day": state.get("calls", {}),
            "recent_pattern_at_other_shops": {name: buyer_pattern((state.get("stats") or {}).get(name)) for name in summary}}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="print today's counts and a per-buyer summary; reads local files only")
    args = parser.parse_args()
    if not args.status:
        parser.error("this command only reads; the restock hook runs the monitor")
    print(json.dumps(status(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
