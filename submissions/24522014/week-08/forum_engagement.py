"""Evidence-based forum replies and outcome accounting. No model/network calls."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

from forum_demand import requests
from forum_policy import enqueue, message_key
from market_state import read, write, task_file
from r3_policy import selling_terms
from strategy_policy import effective


def available(md, inventory, now):
    held = set()
    for path in (md / "signed-offers").glob("*.json"):
        offer = read(path)
        event = read(task_file(md, "checkout-events", offer["task"]), {})
        status = event.get("status")
        if status not in ("canceled", "refused", "expired") and (
                status in ("processing", "uncertain", "completed") or now < offer["expires_at"] + 90):
            held.add(offer["unit"])
    result = []
    for unit in inventory.get("units", []):
        cert = unit.get("cert") or {}
        signed = cert.get("cert") or {}
        if (unit.get("kind") != "goods" or unit.get("grade") not in ("A", "B") or unit.get("unit") in held
                or not unit.get("unit") or not cert.get("sig")
                or any(signed.get(k) != unit.get(k) for k in ("unit", "sku", "grade", "kind"))):
            continue
        result.append(unit)
    return result


def sent_messages(md):
    path = md / "outbox.sent.jsonl"
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 1048576))
        lines = stream.read().decode("utf8", errors="replace").splitlines()
    sent = {}
    for line in lines:
        try:
            record = json.loads(line)
            if isinstance(record.get("sent"), dict):
                sent[message_key(record["sent"])] = record
        except ValueError:
            continue
    return sent


def effects(md, posts, now):
    """Concurrent outcomes, never causal attribution; sales only from local ACP."""
    actions = read(md / "forum-actions.json", {})
    sent = sent_messages(md)
    by_id = {p["id"]: p for p in posts if isinstance(p, dict) and type(p.get("id")) is int}
    local = read(md / "local-settlements.json", {})
    trustworthy = (local.get("r3_anchor_found") and local.get("active_round") == 3
                   and not local.get("conflicting_ledgers") and 0 <= now-local.get("collected_at", 0) <= 3600)
    local_rows = list(local.get("records", {}).values()) if trustworthy else []
    result = []
    board = read(md / "market-intel.json", {})
    own = board.get("competition", {}).get("own", {})
    for key, action in list(actions.items())[-1000:]:
        ack = sent.get(action["message_key"], {})
        status = ack.get("status")
        try:
            post_id = json.loads(ack.get("answer", "{}" )).get("post")
        except (ValueError, AttributeError):
            post_id = None
        if type(post_id) is not int:
            post_id = None
        post = by_id.get(post_id, {})
        start = post.get("ts", action["queued_at"])
        end = min(now, start + 7200)
        sales = [r for r in local_rows if r.get("source") == "local_acp_settled" and r.get("round") == 3
                 and action.get("unit") and r.get("unit") == action["unit"] and start < r["ts"] <= end]
        before = action.get("board_before", {})
        visits = (own.get("shops") or {}).get("visits")
        forum = (own.get("buyers") or {}).get("forum")
        board_fresh = board.get("round") == 3 and 0 <= now-board.get("observed_at", 0) <= 3600
        def delta(value, old):
            return value-old if board_fresh and type(value) in (int, float) and type(old) in (int, float) else None
        result.append({"key": key, "kind": action["kind"], "status": status or "queued_or_unobserved",
                       "post_id": post_id, "likes": post.get("likes"), "dislikes": post.get("dislikes"),
                       "reaction_observed": bool(post), "window_start": start,
                       "window_basis": "post_timestamp" if post else "queue_timestamp",
                       "window_complete": bool(trustworthy and now >= start+7200 and local.get("collected_at", 0) >= start+7200),
                       "local_sales_evidence_ready": bool(trustworthy),
                       "advertised_unit_settlements": len(sales) if trustworthy and status == 200 and action["role"] == "shop" else None,
                       "concurrent_shop_visits_delta": delta(visits, before.get("visits")),
                       "concurrent_buyer_forum_delta": delta(forum, before.get("forum")),
                       "board_window_start": before.get("at"), "board_observed_at": board.get("observed_at"),
                       "attribution": "concurrent_observation_only"})
    by_kind = {}
    for row in result:
        group = by_kind.setdefault(row["kind"], {"queued": 0, "posted": 0, "failed": 0,
                    "reaction_observations": 0, "likes_observed": 0, "dislikes_observed": 0,
                    "posts_with_concurrent_unit_sale": 0})
        group["queued"] += 1
        group["posted"] += row["status"] == 200
        group["failed"] += type(row["status"]) is int and row["status"] != 200
        if row["reaction_observed"]:
            group["reaction_observations"] += 1
            group["likes_observed"] += row["likes"] or 0
            group["dislikes_observed"] += row["dislikes"] or 0
        group["posts_with_concurrent_unit_sale"] += bool(row["advertised_unit_settlements"])
    write(md / "forum-effects.json", {"at": now, "actions": result, "by_kind": by_kind, "model_calls": 0,
          "assessment": "Observational evidence only; no automatic frequency or policy increase.",
          "note": "Missing reactions are unknown, not zero. Unit sales after posts do not establish causality."})
    return result


def followup(md, actions, now):
    # Only our own preserved purchase records can justify a later-fault report.
    sent = sent_messages(md)
    for key, action in reversed(list(actions.items())):
        if action["kind"] != "buyer_report" or not 21600 <= now-action["queued_at"] <= 86400:
            continue
        if sent.get(action["message_key"], {}).get("status") != 200 or not action.get("unit"):
            continue
        record = read(md / "buyer-deals" / f"{action['ledger']}.json", {})
        if not record or record.get("ledger") != action["ledger"] or record.get("unit") != action.get("unit"):
            continue
        status = ("Refund settlement confirmed." if record.get("refund_confirmed") else
                  "A unit-matched breakage event was observed." if record.get("fault") else
                  "No unit-matched breakage/refund event in our available local records; continued function is unverified.")
        message = {"post": f"Follow-up to buyer settlement #{action['ledger']}: {status} Partial observation only.",
                   "refs": [action["ledger"]], "as": "buyer"}
        outcome = enqueue(md, message, f"followup:{action['ledger']}", "buyer_followup",
                          {"ledger": action["ledger"], "sku": action["sku"]}, now)
        if outcome != "already_queued":
            return outcome
    return "none"


def _run(md, now):
    previous = read(md / "forum-engagement.json", {})
    if 0 <= now-previous.get("at", -86400) < 1800:
        return {"status": "interval_limited", "model_calls": 0}
    source = md / "forum.json"
    inv_path = md / "inventory.json"
    inventory = read(inv_path, {})
    if (inventory.get("round") != 3 or not -10 <= now-source.stat().st_mtime <= 120
            or not -10 <= now-inv_path.stat().st_mtime <= 120):
        return {"status": "stale_or_non_r3", "model_calls": 0}
    catalog = read(md / "catalog.json", {})
    posts = read(md / "forum-buffer.json", {}).get("posts") or read(source, [])
    actions = read(md / "forum-actions.json", {})
    followup_status = followup(md, actions, now)
    units = available(md, inventory, now)
    snapshot = read(md / "market-intel.json", {})
    signals = snapshot.get("skus", {}) if snapshot.get("round") == 3 and 0 <= now-snapshot.get("observed_at", 0) <= 3600 else {}
    runtime = effective(md, now)
    outcome = "no_eligible_request_or_stock"
    for request in requests(posts, catalog, now):
        if f"reply:{request['id']}" in actions:
            continue
        fits = [u for u in units if u["sku"] in request["skus"]]
        if not fits:
            continue
        unit = min(fits, key=lambda u: (u.get("supplied_at", now), u["unit"]))
        terms = selling_terms(unit, catalog, now, signals.get(unit["sku"]), runtime)
        sku = unit["sku"]
        message = {"post": f"kcwarni: {unit.get('name', sku)[:100]} (SKU {sku}), 현재 재고 1개 이상, "
                   f"인증 {unit['grade']}등급, 제안 기준가 {terms['quote']} credits, 보증 0시간. "
                   "요청 카테고리와 일치합니다. 게시 후 품절·가격 변경 가능. 등록 가게에서 최종 재고·조건 확인 후 협상해 주세요.",
                   "refs": [], "thread": request["id"], "as": "shop"}
        outcome = enqueue(md, message, f"reply:{request['id']}", "stock_reply",
                          {"request_id": request["id"], "sku": sku, "unit": unit["unit"], "quote": terms["quote"]}, now)
        break
    # A standalone promotion requires observed demand plus actual price comparison.
    if outcome == "no_eligible_request_or_stock":
        purchase = read(md / "purchase-intel.json", {})
        products = {p["sku"]: p for p in purchase.get("summary", {}).get("products", [])} if (
            purchase.get("status") == "ok" and 0 <= now-purchase.get("observed_at", 0) <= 3600) else {}
        for unit in units:
            signal = signals.get(unit["sku"], {})
            if signal.get("distinct_buyers", 0) < 3 or signal.get("settles_6h", 0) < 3:
                continue
            terms = selling_terms(unit, catalog, now, signal, runtime)
            groups = products.get(unit["sku"], {}).get("conditions", [])
            comparable = next((g["prices"] for g in groups if g.get("grade") == unit["grade"]
                               and g.get("warranty_hours") == 0 and g.get("prices", {}).get("count", 0) >= 3), {})
            median = comparable.get("median")
            if type(median) not in (int, float) or terms["quote"] > median:
                continue
            sku = unit["sku"]
            message = {"post": f"kcwarni 재고 안내: {unit.get('name', sku)[:100]}, SKU {sku}, "
                       f"인증 {unit['grade']}등급, 제안 기준가 {terms['quote']} credits, 보증 0시간. "
                       f"재고 확인 시각 {datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='minutes')}. "
                       "최근 거래 수요가 관측된 상품입니다. 품절·가격 변경 가능하므로 등록 가게에서 최종 조건을 확인하세요.",
                       "refs": [], "as": "shop"}
            outcome = enqueue(md, message, f"promotion:{sku}:{int(now//21600)}", "stock_promotion",
                              {"sku": sku, "unit": unit["unit"], "quote": terms["quote"]}, now)
            if outcome != "already_queued":
                break
    measured = effects(md, posts, now)
    record = {"at": now, "status": outcome, "followup": followup_status,
              "tracked_actions": len(measured), "model_calls": 0}
    write(md / "forum-engagement.json", record)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write(md.parent / "logs/forum-engagement" / (stamp + "-" + uuid4().hex[:8] + ".json"), record)
    return record


def run(md, now=None):
    from strategy_controller import acquire, release
    md = Path(md)
    handle = acquire(md / "forum-engagement.lock")
    if handle is None:
        return {"status": "busy", "model_calls": 0}
    try:
        return _run(md, time.time() if now is None else now)
    finally:
        release(handle)
