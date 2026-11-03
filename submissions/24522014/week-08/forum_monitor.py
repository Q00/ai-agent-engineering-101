"""Bounded forum observations; public statements remain unverified claims."""

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
from uuid import uuid4

from market_state import read, write

REQUEST = re.compile(r"구합니다|구매하고|찾습니다|\b(?:want(?:ed)?|need|looking for|buying)\b", re.I)
ISSUE = re.compile(r"불량|환불|고장|불일치|사기|\b(?:broken|refund|mismatch|dishonest)\b", re.I)


def summarize_forum(posts, catalog, ledger_rows, now, login="kcwarni"):
    settlements = {r["seq"]: r for r in ledger_rows
                   if r.get("round") == 3 and r.get("kind") == "settle"}
    selected = {}
    for post in posts[-100:]:
        if not isinstance(post, dict) or post.get("round") != 3 or type(post.get("id")) is not int:
            continue
        ts = post.get("ts")
        if type(ts) not in (int, float) or not math.isfinite(ts) or not 0 <= now-ts <= 86400:
            continue
        selected[post["id"]] = post
    demand, examples, mentions, own_posts, offers = defaultdict(set), [], [], [], []
    known_refs, unknown_refs = 0, 0
    for post in sorted(selected.values(), key=lambda p: p["ts"], reverse=True):
        text = str(post.get("text", ""))[:2000]
        author = str(post.get("author", ""))[:100]
        own = author.casefold() == login.casefold() or str(post.get("owner", "")).casefold() == login.casefold()
        mentioned = bool(re.search(r"(?<![\w-])"+re.escape(login)+r"(?![\w-])", text, re.I))
        skus = sorted(set(re.findall(r"[\w.-]+", text)) & catalog.keys())[:10]
        refs = post.get("refs") if isinstance(post.get("refs"), list) else []
        matched = [r for r in refs[:20] if type(r) is int and r in settlements]
        unmatched = [r for r in refs[:20] if type(r) is int and r not in settlements]
        known_refs += len(matched); unknown_refs += len(unmatched)
        item = {"id": post["id"], "author": author, "role": post.get("role"),
                "excerpt": text[:160], "skus": skus, "matched_settlement_refs": matched,
                "unmatched_refs": unmatched, "claim_verification": "not_verified",
                "likes": post.get("likes"), "dislikes": post.get("dislikes"), "score": post.get("score"),
                "issue_keywords": bool(ISSUE.search(text))}
        if not own and post.get("role") == "buyer" and REQUEST.search(text):
            for sku in skus:
                demand[sku].add(author)
            if len(examples) < 5:
                examples.append(item)
        if mentioned and not own and len(mentions) < 5:
            mentions.append(item)
        if own and len(own_posts) < 5:
            own_posts.append(item)
        if not own and post.get("role") == "shop" and len(offers) < 5:
            offers.append(item)
    return {"posts_considered": len(selected), "post_ids": sorted(selected),
            "demand_skus": [{"sku": sku, "name": catalog[sku].get("name", sku), "distinct_requesters": len(authors)}
                            for sku, authors in sorted(demand.items(), key=lambda x: (-len(x[1]), x[0]))[:10]],
            "request_examples": examples, "own_shop_mentions": mentions,
            "own_post_reactions": own_posts, "competitor_offer_claims": offers,
            "matched_ref_count": known_refs, "unmatched_ref_count": unknown_refs,
            "note": "A matched ledger reference proves only that the sampled R3 settlement exists, not the post's claims. Missing references may be outside the bounded sample. No automatic trust/rating change."}


def observe(md, now=None, force=False, ledger_rows=None):
    md = Path(md)
    now = time.time() if now is None else now
    try:
        previous = read(md/"forum-intel.json", {})
    except (OSError, ValueError, TypeError):
        previous = {}
    interval = 1800 if previous.get("status") == "ok" else 60
    if not force and 0 <= now-previous.get("observed_at", 0) < interval:
        return previous
    record = {"observed_at": now, "status": "unavailable", "interval_seconds": 1800, "model_calls": 0}
    try:
        path = md/"forum.json"
        age = now-path.stat().st_mtime
        record["source_age_seconds"] = age
        if not -10 <= age <= 120:
            record["status"] = "stale"
        else:
            posts = read(path)
            if not isinstance(posts, list):
                raise ValueError("forum snapshot must be a list")
            rows = ledger_rows if ledger_rows is not None else read(md/"market-intel.json", {}).get("rows", [])
            record.update(status="ok", summary=summarize_forum(posts, read(md/"catalog.json", {}), rows, now))
            old_ids = set((previous.get("summary") or {}).get("post_ids", []))
            record["new_post_ids"] = [p for p in record["summary"]["post_ids"] if p not in old_ids]
    except (OSError, ValueError, TypeError, KeyError) as error:
        record.update(status="error", error=f"{type(error).__name__}: {error}")
    write(md/"forum-intel.json", record)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write(md.parent/"logs/forum-monitor"/(stamp+"-"+uuid4().hex[:8]+".json"), record)
    return record


if __name__ == "__main__":
    result = observe(Path(__file__).resolve().parent/".market", force=True)
    summary = result.get("summary", {})
    print(json.dumps({"status": result["status"], "posts": summary.get("posts_considered"),
                      "demand_skus": summary.get("demand_skus"), "own_mentions": len(summary.get("own_shop_mentions", [])),
                      "matched_refs": summary.get("matched_ref_count"), "model_calls": 0}, ensure_ascii=False, indent=2))
