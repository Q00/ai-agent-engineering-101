"""Read-only: buyer posts since a forum step began: count, spacing, reactions, topics left under the 24 h rule, and the board before and after. No network, no model, nothing queued.

Run from week-08 with --since (KST), --board-before, --board-after, --out. Reactions come from the saved forum buffer, so they lag and young posts have had little time. Prints the same JSON.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from statistics import median
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import forum_buyer_posts as posts  # noqa: E402
import forum_policy  # noqa: E402

KST = timezone(timedelta(hours=9))


def own_row(path):
    board = json.loads(Path(path).read_text(encoding="utf-8"))
    buyer = next(r for r in board["buyers"] if r.get("name") == "kcwarni")
    player = next(p for p in board["players"] if p.get("player") == "kcwarni")
    students = [p for p in board["players"] if p.get("student")]
    return {**{k: buyer.get(k) for k in ("saved", "forum", "posts", "deals")}, **{k: player.get(k) for k in ("student_buyer", "student_total", "student_pct")}, "student_rank": 1+sum((p.get("student_total") or 0) > player["student_total"] for p in students), "students": len(students)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("--since", "--board-before", "--board-after"):
        parser.add_argument(name, required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    md = ROOT / ".market"
    start = datetime.fromisoformat(args.since).replace(tzinfo=KST).timestamp()
    now = time.time()
    actions = json.loads((md / "forum-actions.json").read_text(encoding="utf-8"))
    mine = sorted((a for a in actions.values() if a.get("role", "buyer") == "buyer" and a.get("kind") in (posts.KIND, "buyer_report") and a.get("queued_at", 0) >= start), key=lambda a: a["queued_at"])
    gaps = [(b["queued_at"]-a["queued_at"])/60 for a, b in zip(mine, mine[1:])]
    buffer = json.loads((md / "forum-buffer.json").read_text(encoding="utf-8")).get("posts") or []
    seen = [p for p in buffer if p.get("author") == "kcwarni" and p.get("role") == "buyer" and type(p.get("ts")) in (int, float) and p["ts"] >= start]
    older = [p for p in seen if now-p["ts"] >= 1800]
    reacted = lambda group: {"posts": len(group), "likes": sum(p.get("likes") or 0 for p in group), "dislikes": sum(p.get("dislikes") or 0 for p in group)}
    snapshot = json.loads((md / "market-intel.json").read_text(encoding="utf-8"))
    units, urls = posts.own_shop(md, snapshot)
    left = list(posts.candidates(snapshot.get("rows", []), json.loads((md / "catalog.json").read_text(encoding="utf-8")), units, urls, actions, now))
    usable = [c for c in left if posts.safe(c[0]["post"])]
    day = [a for a in actions.values() if a.get("kind") == posts.KIND and 0 <= now-a.get("queued_at", -1) < posts.SKU_REPEAT]
    limits = forum_policy.posting_limits(md, now)
    report = {"since_kst": args.since, "checked_kst": datetime.fromtimestamp(now, KST).isoformat(timespec="seconds"), "hours": round((now-start)/3600, 2), "queued": len(mine), "queued_per_hour": round(len(mine)/((now-start)/3600), 2), "by_kind": dict(Counter(a.get("kind") for a in mine)), "by_status": dict(Counter(str(a.get("status")) for a in mine)),
              "gap_minutes": {"min": round(min(gaps), 2), "median": round(median(gaps), 2), "max": round(max(gaps), 2)} if gaps else None, "topics": {"sku": sum(bool(a.get("sku")) for a in mine), "category": sum(bool(a.get("category")) for a in mine)},
              "repeats_within_24h": {"sku": sum(n > 1 for n in Counter(a["sku"] for a in day if a.get("sku")).values()), "category": sum(n > 1 for n in Counter(a["category"] for a in day if a.get("category")).values())}, "notes_in_the_last_24h": len(day),
              "topics_left_now": {"sku": sum("sku" in c[3] for c in usable), "category": sum("category" in c[3] for c in usable), "unsafe_dropped": len(left)-len(usable), "lowest_samples_sku": min((c[3]["samples"] for c in usable if "sku" in c[3]), default=None), "median_samples_sku": median([c[3]["samples"] for c in usable if "sku" in c[3]] or [0])},
              "samples_of_posted": {"median": median([a.get("samples") or 0 for a in mine] or [0]), "lowest": min((a.get("samples") or 0 for a in mine), default=None)}, "reactions_all_seen": reacted(seen), "reactions_older_than_30_min": reacted(older),
              "stop_file": (ROOT / "logs/forum-buyer-stop.jsonl").exists(), "limits": {k: limits.get(k) for k in ("buyer_posts_enabled", "buyer_daily_limit", "buyer_report_limit", "buyer_min_interval_seconds", "buyer_resume_token")}, "board_before": own_row(args.board_before), "board_after": own_row(args.board_after)}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
