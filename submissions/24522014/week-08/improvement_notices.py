"""Short, deduplicated local notices; no network or model calls.

Called under the controller lock. stderr is relayed by the kit without changing
the restock command's final JSON result. This cannot send messages to Codex chat.
"""

from datetime import datetime, timedelta, timezone
import sys
from uuid import uuid4

from market_state import read, write


def publish(md, observation, events):
    path = md / "improvement-notices.json"
    state = read(path, {})
    now = observation["at"]
    seen = state.get("seen_experiments", [])
    messages = []
    for event in events:
        identity = event.get("experiment")
        if event.get("event") != "keep" or not identity or identity in seen:
            continue
        comparison = event["comparison"]
        before, after = comparison["before"], comparison["after"]
        parts = []
        for key, label in (("deals_per_hour", "판매/시간"), ("gross_per_hour", "원가 차익/시간")):
            if key in before and key in after:
                parts.append(f"{label} {before[key]:,.1f}→{after[key]:,.1f}")
        detail = ", ".join(parts) or "재고 부담·판매 유지 조건 충족"
        messages.append("정책 실험 평가 통과: " + detail + ". 변경 유지(인과관계 확정은 아님).")
        seen.append(identity)
    valid = observation.get("valid") and observation.get("self_evidence") == "local_acp_v1"
    baseline = state.get("baseline")
    if valid:
        reset = (not baseline or observation["deals"] < baseline["deals"]
                 or observation["bankruptcies"] != baseline["bankruptcies"])
        if not reset and now - baseline["at"] >= 7200:
            parts = []
            rank, old_rank = observation.get("rank"), baseline.get("rank")
            if rank is not None and old_rank is not None and old_rank - rank >= 2:
                parts.append(f"순위 {old_rank}→{rank}위")
            if (observation["reviews"] - baseline["reviews"] >= 5
                    and observation["stars"] - baseline["stars"] >= .1 - 1e-9):
                parts.append(f"별점 {baseline['stars']:.2f}→{observation['stars']:.2f}")
            if parts:
                messages.append("관측 개선: " + ", ".join(parts) + ". 정책 효과로 단정하지 않음.")
            reset = True
        if reset:
            state["baseline"] = {key: observation[key] for key in
                                 ("at", "deals", "bankruptcies", "rank", "reviews", "stars")}
    state["seen_experiments"] = seen
    records = []
    for message in messages:
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        record = {"at": now, "message": message, "model_calls": 0}
        write(md.parent / "logs/improvement-notices" / (stamp + "-" + uuid4().hex[:8] + ".json"), record)
        records.append(record)
    # Persist deduplication before terminal output; failures must not repeat keeps.
    write(path, state)
    if records:
        write(md / "latest-improvement.json", records[-1])
        with (md.parent / "IMPROVEMENTS.md").open("a", encoding="utf8") as stream:
            for record in records:
                kst = datetime.fromtimestamp(now, timezone(timedelta(hours=9))).strftime("%m-%d %H:%M KST")
                stream.write(f"- {kst} {record['message']}\n")
                print("[개선 알림] " + record["message"], file=sys.stderr, flush=True)
    return records
