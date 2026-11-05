"""Evidence-based forum replies and outcome accounting. No model/network calls."""

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from forum_demand import requests
from forum_policy import BUYER_CORRECTION, enqueue, message_key, posting_limits
import forum_posts
from market_state import read, write, task_file
from r3_policy import selling_terms
from strategy_policy import effective


ENGAGEMENT_INTERVAL = 600
STOCK_NOTICE_REPEAT, RECORD_REPEAT, COMPARE_REPEAT = 43200, 21600, 21600


def available(md, inventory, now):
    """Units we could really sell now, each with a valid certificate; any grade, since the grade is shown. Food changes grade as it ages."""
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
        food = unit.get("kind") == "food"
        if (unit.get("kind") not in ("goods", "food") or unit.get("grade") not in ("A", "B", "C") or unit.get("unit") in held
                or not unit.get("unit") or not cert.get("sig") or (food and (not unit.get("expires_at") or unit["expires_at"] <= now))
                or any(signed.get(k) != unit.get(k) for k in ("unit", "sku", "kind") + (() if food else ("grade",)))):
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
                       "concurrent_shop_deals_delta": delta((own.get("shops") or {}).get("deals"), before.get("deals")),
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
    enabled = posting_limits(md, now).get("buyer_posts_enabled")
    paused = False
    for key, action in reversed(list(actions.items())):
        if action["kind"] != "buyer_report" or not 21600 <= now-action["queued_at"] <= 86400:
            continue
        if sent.get(action["message_key"], {}).get("status") != 200 or not action.get("unit"):
            continue
        record = read(md / "buyer-deals" / f"{action['ledger']}.json", {})
        if not record or record.get("ledger") != action["ledger"] or record.get("unit") != action.get("unit"):
            continue
        correction = bool(record.get("refund_confirmed") or record.get("fault"))
        if not correction and not enabled:
            # Skipped here, so a no-news follow-up cannot stand in front of a correction further down the list.
            paused = True
            continue
        status = ("Refund settlement confirmed." if record.get("refund_confirmed") else
                  "A unit-matched breakage event was observed." if record.get("fault") else
                  "No unit-matched breakage/refund event in our available local records; continued function is unverified.")
        message = {"post": f"Follow-up to buyer settlement #{action['ledger']}: {status} Partial observation only.",
                   "refs": [action["ledger"]], "as": "buyer"}
        earlier = actions.get(f"followup:{action['ledger']}")
        if correction and earlier and "No unit-matched" not in earlier.get("message", {}).get("post", ""):
            continue  # An older follow-up already reported this fault.
        # A correction has its own key: it must still go out after a no-news follow-up on the same deal.
        outcome = enqueue(md, message, f"{'correction' if correction else 'followup'}:{action['ledger']}", BUYER_CORRECTION if correction else "buyer_followup",
                          {"ledger": action["ledger"], "sku": action["sku"]}, now)
        if outcome != "already_queued":
            return outcome
    return "paused_no_correction" if paused else "none"


def last_queued(actions, kind, **fields):
    return max((a["queued_at"] for a in actions.values() if a["kind"] == kind and all(a.get(k) == v for k, v in fields.items())), default=0)


def priced(units, catalog, signals, runtime, now):
    """Units with the ask the seller would open at. A unit whose terms cannot be read is not advertised."""
    result = []
    for unit in units:
        try:
            result.append((unit, selling_terms(unit, catalog, now, signals.get(unit["sku"]), runtime)))
        except (KeyError, ValueError, TypeError):
            continue
    return result


def general_posts(md, inventory, catalog, offers, rows, actions, now):
    """Posts not answering anyone, most useful first, each kind with its own repeat limit. Yields (message, key, kind, metadata)."""
    local = read(md / "local-settlements.json", {}).get("records") or {}
    own_units = {r.get("unit") for r in (local.values() if isinstance(local, dict) else local) if r.get("unit")}
    board = (read(md / "market-intel.json", {}).get("competition", {}).get("own") or {}).get("shops") or {}
    if now-last_queued(actions, "honest_record") >= RECORD_REPEAT:
        record = forum_posts.honest_record(rows, own_units, board, now)
        if record:
            yield {"post": record[0], "refs": record[1], "as": "shop"}, f"record:{int(now//RECORD_REPEAT)}", "honest_record", {}
    by_category = {}
    for unit, terms in sorted(offers, key=lambda pair: -(pair[0].get("supplied_at") or 0)):
        by_category.setdefault(unit.get("category") or "", []).append((unit, terms))
    for category, group in by_category.items():
        if category and now-last_queued(actions, "stock_notice", category=category) >= STOCK_NOTICE_REPEAT:
            chosen, seen = [], set()
            for unit, terms in group:
                if unit["sku"] not in seen and len(chosen) < forum_posts.UNITS_PER_NOTICE:
                    seen.add(unit["sku"])
                    chosen.append((unit, terms))
            lines = [forum_posts.unit_line(unit, terms, now) for unit, terms in chosen]
            yield ({"post": forum_posts.stock_notice(lines, category, now), "refs": [], "as": "shop"}, f"stock:{category}:{int(now//STOCK_NOTICE_REPEAT)}", "stock_notice",
                   {"category": category, "skus": sorted(seen), "units": [u["unit"] for u, _ in chosen], "unit": chosen[0][0]["unit"], "quotes": {u["sku"]: t["quote"] for u, t in chosen}})
    for unit, terms in offers:
        found = forum_posts.public_median(rows, unit, now)
        if found and terms["quote"] <= found[0] and now-last_queued(actions, "market_compare", sku=unit["sku"]) >= COMPARE_REPEAT:
            yield ({"post": forum_posts.market_compare(unit, terms, found[0], found[1], now), "refs": [], "as": "shop"}, f"compare:{unit['sku']}:{int(now//COMPARE_REPEAT)}", "market_compare",
                   {"sku": unit["sku"], "unit": unit["unit"], "quote": terms["quote"], "median": found[0], "samples": found[1]})


def reply_posts(posts, catalog, offers, actions, now):
    for request in requests(posts, catalog, now):
        if f"reply:{request['id']}" in actions:
            continue
        fits = [pair for pair in offers if pair[0]["sku"] in request["skus"]]
        if fits:
            unit, terms = min(fits, key=lambda pair: (pair[0].get("supplied_at", now), pair[0]["unit"]))
            # The request supplies its id and matched SKUs only; none of its words reach this text.
            yield ({"post": forum_posts.reply(forum_posts.unit_line(unit, terms, now), now), "refs": [], "thread": request["id"], "as": "shop"}, f"reply:{request['id']}", "stock_reply",
                   {"request_id": request["id"], "sku": unit["sku"], "unit": unit["unit"], "quote": terms["quote"], "category": unit.get("category")})


def note_brake(md, previous, limits, now):
    """Say so once when the shop's posting slows down or recovers, in the terminal and a log."""
    brake = bool(limits.get("shop_reaction_brake"))
    if brake == bool(previous.get("shop_reaction_brake")):
        return
    print(f"[포럼] shop 글 빈도 {'기존 수준으로 낮춤: 싫어요 기준 초과' if brake else '설정값으로 복귀'}", file=sys.stderr, flush=True)
    path = md.parent / "logs/forum-posting-brake.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf8") as stream:
        stream.write(json.dumps({"at": now, "shop_reaction_brake": brake}) + "\n")


def _run(md, now):
    previous = read(md / "forum-engagement.json", {})
    if 0 <= now-previous.get("at", -86400) < ENGAGEMENT_INTERVAL:
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
    offers = priced(units, catalog, signals, runtime, now)
    limits = posting_limits(md, now)
    note_brake(md, previous, limits, now)
    rows = snapshot.get("rows", []) if snapshot.get("round") == 3 and 0 <= now-snapshot.get("observed_at", 0) <= 3600 else []
    # A reply first, then one general post; the policy applies the intervals and refuses what is too soon.
    for source_posts in (reply_posts(posts, catalog, offers, actions, now), general_posts(md, inventory, catalog, offers, rows, actions, now)):
        for message, key, kind, metadata in source_posts:
            if not forum_posts.safe(message["post"]):
                outcome = "blocked_content"
                continue
            outcome = enqueue(md, message, key, kind, metadata, now)
            if outcome != "already_queued":
                break
        if outcome == "queued":
            break
    measured = effects(md, posts, now)
    record = {"at": now, "status": outcome, "followup": followup_status, "shop_reaction_brake": bool(limits.get("shop_reaction_brake")),
              "tracked_actions": len(measured), "model_calls": 0}
    write(md / "forum-engagement.json", record)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write(md.parent / "logs/forum-engagement" / (stamp + "-" + uuid4().hex[:8] + ".json"), record)
    return record


def preview(md, now=None):
    """What each kind of post would say from today's records, with nothing queued. A reply is shown against a placeholder thread."""
    md = Path(md)
    now = time.time() if now is None else now
    inventory, catalog = read(md / "inventory.json", {}), read(md / "catalog.json", {})
    snapshot = read(md / "market-intel.json", {})
    offers = priced(available(md, inventory, now), catalog, snapshot.get("skus", {}), effective(md, now), now)
    examples = {}
    for message, _, kind, _ in general_posts(md, inventory, catalog, offers, snapshot.get("rows", []), {}, now):
        examples.setdefault(kind, message)
    if offers:
        examples["stock_reply"] = {"post": forum_posts.reply(forum_posts.unit_line(*offers[0], now), now), "refs": [], "thread": 0, "as": "shop"}
    return {kind: dict(message, safe=forum_posts.safe(message["post"])) for kind, message in examples.items()}


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=preview.__doc__)
    parser.add_argument("--preview", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(preview(Path(__file__).resolve().parent / ".market"), ensure_ascii=False, indent=1))
