"""Preview and approve buyer evidence or seller stock ads; dispatch only inside the kit."""

import argparse
import hashlib
import json
import sqlite3
import time
import sys
from contextlib import closing
from email.utils import parsedate_to_datetime
from pathlib import Path

if __package__:
    from .analyst import DB_NAME, REPORT_NAME, market_origin
    from .storage import lock, read_json, read_jsonl, write_jsonl
else:
    from analyst import DB_NAME, REPORT_NAME, market_origin
    from storage import lock, read_json, read_jsonl, write_jsonl

sys.path.append(str(Path(__file__).resolve().parents[1]))
import shop_ads
import forum_delivery

QUEUE = "forum-approved.jsonl"
JOURNAL = "forum-delivery.jsonl"
COOLDOWN = 300
MAX_REPORT_AGE = 300


def entry_id(entry):
    payload = {key: value for key, value in entry.items() if key != "id"}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def check_exposure(state, market, sku, now):
    report = read_json(state / REPORT_NAME)
    if not isinstance(report, dict) or report.get("market") != market:
        raise ValueError("fresh analysis for this market is required")
    for key in ("generated_at", "forum_snapshot_mtime"):
        stamp = report.get(key)
        if not isinstance(stamp, (int, float)) or not -5 <= now - stamp <= MAX_REPORT_AGE:
            raise ValueError("analysis or forum snapshot is stale")
    candidates = report.get("candidates")
    condition_candidates = report.get("condition_candidates")
    if not isinstance(candidates, list) or not isinstance(condition_candidates, list):
        raise ValueError("invalid candidate report")
    protected = read_json(state / "forum-private-skus.json", [])
    if not isinstance(protected, list) or any(not isinstance(item, str) for item in protected):
        raise ValueError("forum-private-skus.json must be an array of SKU strings")
    if sku in protected or any(isinstance(row, dict) and row.get("sku") == sku
                               for row in candidates + condition_candidates):
        raise ValueError("SKU is protected or currently an analysis candidate")


def preview(state, market, buyer, seq, now=None):
    state = Path(state)
    market = market_origin(market)
    now = time.time() if now is None else now
    if not isinstance(buyer, str) or not buyer.strip() or type(seq) is not int or seq <= 0:
        raise ValueError("buyer name and positive ledger sequence are required")
    database = state / DB_NAME
    if not database.exists():
        raise ValueError("run the analyst before preparing a post")
    with closing(sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)) as db:
        source = db.execute("SELECT value FROM meta WHERE key='market'").fetchone()
        row = db.execute("""SELECT sku, amount, grade, buyer, shop, order_id
                            FROM settlements WHERE seq=?""", (seq,)).fetchone()
    if not source or source[0] != market or not row:
        raise ValueError("ledger settlement not found for this market")
    sku, amount, grade, ledger_buyer, shop, order_id = row
    if ledger_buyer != buyer or grade not in {"A", "B", "C"}:
        raise ValueError("settlement buyer or grade did not match")
    payments = read_jsonl(state / "payments.jsonl")
    matched = any(isinstance(item, dict) and item.get("paid") is True
                  and item.get("ledger") == seq and item.get("price") == amount
                  and item.get("order") == order_id and item.get("shop") == shop
                  for item in payments)
    if not matched:
        raise ValueError("no matching successful local payment")
    catalog = read_json(state / "catalog.json", {})
    if (not isinstance(catalog, dict) or sku not in catalog or not isinstance(sku, str)
            or not sku.isascii() or not sku.isalnum() or not 1 <= len(sku) <= 64):
        raise ValueError("invalid settlement SKU")
    check_exposure(state, market, sku, now)
    post = f"[정산 사례] SKU {sku} · 체결가 {amount:,} · 실제 등급 {grade} · 원장 #{seq}"
    entry = {"as": "buyer", "market": market, "buyer": buyer, "seq": seq,
             "sku": sku, "post": post, "refs": [seq]}
    return {**entry, "id": entry_id(entry)}


def enqueue(state, market, buyer, seq, approved_id, now=None):
    state = Path(state)
    with lock(state):
        entry = preview(state, market, buyer, seq, now)
        if approved_id != entry["id"]:
            raise ValueError("approval does not match the current post, role and destination")
        queue, history = read_jsonl(state / QUEUE), read_jsonl(state / JOURNAL)
        if any(row.get("id") == entry["id"] for row in queue + history):
            return False
        write_jsonl(state / QUEUE, queue + [entry])
    return True


def shop_preview(state, market, shop, unit, price=None, ttl=1800, now=None, seq=None):
    state, market = Path(state), market_origin(market)
    now = time.time() if now is None else now
    entry = shop_ads.request(state, "preview", market=market, shop=shop, unit=unit, price=price, ttl=ttl, seq=seq)
    if entry.get("id") != entry_id(entry) or entry.get("as") != "shop" or (entry.get("market"),entry.get("shop")) != (market,shop):
        raise ValueError("seller preview identity mismatch")
    check_exposure(state, market, entry["sku"], now)
    directory = state / "forum-drafts"
    directory.mkdir(exist_ok=True)
    path = directory / (entry["id"] + ".json")
    if not path.exists():
        with path.open("x") as stream:
            json.dump(entry, stream, ensure_ascii=False, indent=2)
    return entry


def shop_enqueue(state, market, shop, approved_id, now=None):
    state, market = Path(state), market_origin(market)
    now = time.time() if now is None else now
    if not isinstance(approved_id,str) or len(approved_id) != 64 or any(c not in "0123456789abcdef" for c in approved_id):
        raise ValueError("invalid approval id")
    with lock(state):
        entry = read_json(state / "forum-drafts" / (approved_id + ".json"))
        if not isinstance(entry,dict) or entry.get("id") != approved_id or entry_id(entry) != approved_id:
            raise ValueError("approval does not match saved preview")
        if (entry.get("as"),entry.get("market"),entry.get("shop")) != ("shop",market,shop):
            raise ValueError("approval role or destination mismatch")
        check_exposure(state,market,entry["sku"],now)
        shop_ads.request(state,"validate",entry=entry)
        queue, history = read_jsonl(state / QUEUE), read_jsonl(state / JOURNAL)
        if any(row.get("id") == approved_id for row in queue+history):
            return False
        write_jsonl(state / QUEUE, queue+[entry])
    return True


def next_retry(headers, now):
    value = headers.get("Retry-After", "") if headers else ""
    try:
        until = now + int(value)
    except (TypeError, ValueError):
        try:
            until = parsedate_to_datetime(value).timestamp()
        except (TypeError, ValueError, OverflowError):
            until = now
    return max(now + COOLDOWN, until)


def dispatch_role(state, market, role, clock=time.time):
    """Called by the kit bridge with its registered Market, not by the CLI."""
    state = Path(state)
    registered = market.joined.get(role)
    if not market.token.get(role) or not registered:
        return "waiting-for-" + role
    buyer = registered[0]
    account_key = "buyer" if role == "buyer" else "shop"
    base = market_origin(market.base)
    with lock(state):
        queue_path, journal_path = state / QUEUE, state / JOURNAL
        pending, history = read_jsonl(queue_path), read_jsonl(journal_path)
        relevant = [item for item in forum_delivery.resolved(state, history) if item.get("market") == base and item.get("as") == role and item.get(account_key) == buyer]
        latest = {item["id"]: item for item in relevant}
        for index, entry in enumerate(pending):
            if not isinstance(entry, dict) or entry.get("id") != entry_id(entry) or entry.get("as") not in {"buyer", "shop"}:
                raise ValueError("invalid queued entry")
            if (entry.get("as"), entry.get("market"), entry.get(account_key)) != (role, base, buyer):
                continue
            last = latest.get(entry["id"], {})
            if last.get("state") in {"sent", "rejected", "held_unknown"}:
                write_jsonl(queue_path, pending[:index] + pending[index + 1:])
                return "reconciled"
            if any(item["state"] in {"inflight", "unknown"} for item in latest.values()):
                return "review-required"
            now = clock()
            if any(now < item.get("next_at", 0) for item in latest.values()):
                return "cooldown"
            if role == "buyer":
                if preview(state, base, buyer, entry.get("seq"), now) != entry:
                    raise ValueError("approved entry no longer matches evidence")
            else:
                if now >= entry["expires_at"]:
                    write_jsonl(journal_path,history+[{**entry,"at":now,"state":"rejected","reason":"expired"}])
                    write_jsonl(queue_path,pending[:index]+pending[index+1:])
                    return "expired"
                check_exposure(state,base,entry["sku"],now)
                # Activate before POST. Delivery timeout must never revoke an advertised price.
                shop_ads.request(state,"activate",entry=entry)
            inflight = {**entry, "at": now, "state": "inflight"}
            history.append(inflight)
            write_jsonl(journal_path, history)
            try:
                response = market.call("POST", "/api/forum", role,
                                       json={"text": entry["post"], "refs": entry["refs"], "thread": None})
            except Exception:
                result = {**inflight, "state": "unknown"}
            else:
                status = response.status_code
                body = getattr(response, "text", "")
                body = body if isinstance(body, str) else ""
                result = {**inflight, "status": status, "response_body": body[:4096],
                          "response_body_truncated": len(body) > 4096}
                if status == 429:
                    result.update(state="retry", next_at=next_retry(response.headers, clock()))
                elif 200 <= status < 300:
                    sent_at = clock()
                    try:
                        payload = response.json()
                        post_id = forum_delivery.extract_post_id(payload)
                    except (AttributeError, TypeError, ValueError):
                        post_id = None
                    result.update(state="sent", sent_at=sent_at, post_id=post_id, post_id_status="known" if post_id is not None else "missing", next_at=sent_at + COOLDOWN)
                elif 400 <= status < 500 and status != 408:
                    result.update(state="rejected")
                else:
                    result.update(state="unknown")
            write_jsonl(journal_path, history + [result])
            if result["state"] in {"sent", "rejected"}:
                write_jsonl(queue_path, pending[:index] + pending[index + 1:])
            return result["state"]
        return "empty"


def dispatch(state, market, clock=time.time):
    results = []
    for role in ("shop", "buyer"):
        try:
            result = dispatch_role(state, market, role, clock)
        except Exception:
            # A broken/unavailable role must not starve the other account's queue.
            if role == "shop" and market.joined.get("buyer") and market.token.get("buyer"):
                result = "shop-review-required"
            else:
                raise
        results.append(result)
        if result in {"sent", "retry", "unknown", "rejected", "reconciled", "expired"}:
            return result
    active = [r for r in results if not r.startswith("waiting-for-") and r != "empty"]
    if active:
        return active[0]
    return "empty" if "empty" in results else "waiting-for-buyer"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preview", "approve", "shop-preview", "shop-approve", "reconcile"))
    parser.add_argument("--state", type=Path, default=Path(".market"))
    parser.add_argument("--market", required=True)
    parser.add_argument("--delivery-id")
    parser.add_argument("--forum-snapshot", type=Path)
    parser.add_argument("--buyer")
    parser.add_argument("--shop")
    parser.add_argument("--unit")
    parser.add_argument("--price", type=int, help="Fixed ad price; omit to use seller stock pricing")
    parser.add_argument("--ttl-seconds", type=int, default=1800)
    parser.add_argument("--seq", type=int)
    parser.add_argument("--approve-id")
    args = parser.parse_args()
    try:
        if args.action == "reconcile":
            if not args.delivery_id:
                parser.error("reconcile requires --delivery-id")
            with lock(args.state):
                result = forum_delivery.reconcile(args.state,market_origin(args.market),args.delivery_id,
                    args.forum_snapshot or args.state / "forum.json")
            print(json.dumps(result,ensure_ascii=False))
        elif args.action == "shop-preview":
            if not args.shop or not args.unit or args.approve_id:
                parser.error("shop-preview requires --shop --unit (no --approve-id)")
            print(json.dumps(shop_preview(args.state,args.market,args.shop,args.unit,args.price,args.ttl_seconds,seq=args.seq),ensure_ascii=False,indent=2))
        elif args.action == "shop-approve":
            if not args.shop or not args.approve_id:
                parser.error("shop-approve requires --shop --approve-id")
            print(json.dumps({"queued":shop_enqueue(args.state,args.market,args.shop,args.approve_id)}))
        elif args.action == "preview":
            if args.approve_id:
                parser.error("--approve-id is only for approve")
            print(json.dumps(preview(args.state, args.market, args.buyer, args.seq), ensure_ascii=False, indent=2))
        else:
            if not args.approve_id:
                parser.error("approve requires --approve-id from preview")
            queued = enqueue(args.state, args.market, args.buyer, args.seq, args.approve_id)
            print(json.dumps({"queued": queued}, ensure_ascii=False))
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"forum publisher: {exc}\n")


if __name__ == "__main__":
    main()
