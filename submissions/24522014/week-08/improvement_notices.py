"""Short, deduplicated local notices; no network or model calls.

Called under the controller lock. stderr is relayed by the kit without changing
the restock command's final JSON result. This cannot send messages to Codex chat.
"""

from datetime import datetime, timedelta, timezone
import sys
import math
from uuid import uuid4

from market_state import read, write


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def kst(at):
    return datetime.fromtimestamp(at, timezone(timedelta(hours=9))).strftime("%m-%d %H:%M KST")


def publish(md, observation, events, *, phase="unknown", next_check_at=None, change_after=None):
    """Return improvement records; status heartbeats use a separate log/feed."""
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
    objective = observation.get("objective", "player_top3")
    profit_goal = objective == "seller_profit_rank_1"
    rank_key = "profit_rank" if profit_goal else "rank"
    rank_label = "순이익 순위" if profit_goal else "종합 순위"
    if valid:
        reset = (not baseline or observation["deals"] < baseline["deals"]
                 or observation["bankruptcies"] != baseline["bankruptcies"]
                 or state.get("objective", "player_top3") != objective)
        if profit_goal and observation.get("profit_rank") == 1 and state.get("last_profit_rank") != 1:
            messages.append("목표 확인: 공식 순이익 순위 1위. 순위 관측 시각 기준이며 정책 효과로 단정하지 않음.")
        if not reset and now - baseline["at"] >= 7200:
            parts = []
            rank, old_rank = observation.get(rank_key), baseline.get(rank_key)
            if rank is not None and old_rank is not None and old_rank - rank >= 2:
                parts.append(f"{rank_label} {old_rank}→{rank}위")
            profit, old_profit = observation.get("profit"), baseline.get("profit")
            if (profit_goal and number(profit) and number(old_profit)
                    and observation["deals"]-baseline["deals"] >= 10
                    and profit-old_profit >= max(100000, abs(old_profit)*.05)):
                parts.append(f"순이익 {old_profit:,.0f}→{profit:,.0f} (+{profit-old_profit:,.0f})")
            if (observation["reviews"] - baseline["reviews"] >= 5
                    and observation["stars"] - baseline["stars"] >= .1 - 1e-9):
                parts.append(f"별점 {baseline['stars']:.2f}→{observation['stars']:.2f}")
            if parts:
                messages.append("관측 개선: " + ", ".join(parts) + ". 정책 효과로 단정하지 않음.")
            reset = True
        if reset:
            state["baseline"] = {key: observation.get(key) for key in
                                 ("at", "deals", "bankruptcies", "rank", "profit_rank", "profit", "reviews", "stars")}
        state["objective"] = objective
        state["last_profit_rank"] = observation.get("profit_rank")
    state["seen_experiments"] = seen
    records = []
    for message in messages:
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        record = {"at": now, "message": message, "model_calls": 0}
        write(md.parent / "logs/improvement-notices" / (stamp + "-" + uuid4().hex[:8] + ".json"), record)
        records.append(record)
    status_records = []
    if not 0 <= now-state.get("status_at", -86400) < 1800:
        labels = {"cooldown": "정책 변경 대기", "collecting_baseline": "기준선 수집",
                  "testing": "가격 실험 중", "maturing": "효과 평가 대기", "cash_guard": "현금 보호",
                  "waiting_for_comparable_evidence": "비교 표본 대기", "waiting_for_settlements": "확정 정산 대기",
                  "maintaining_profit_lead": "순이익 1위 유지", "disabled": "정책 실험 중지"}
        phase_text = labels.get(phase, phase)
        if valid:
            parts = ["관측 정상", f"자기 확정 정산 {observation['deals']}건"]
            if number(observation.get("profit")):
                parts.append(f"순이익 {observation['profit']:,.0f}")
            rank = observation.get(rank_key)
            parts.append(f"{rank_label} {rank}위" if rank is not None else f"{rank_label} 확인 대기")
        else:
            parts = ["자료 확인 대기(정상 실적으로 판단하지 않음)"]
        parts.append(phase_text)
        if phase == "cooldown" and number(change_after) and change_after > now:
            parts.append(f"변경 가능 {kst(change_after)}")
        if number(next_check_at):
            parts.append(f"다음 진단 {kst(next_check_at)}")
        status_records.append({"at": now, "kind": "status", "message": " · ".join(parts),
                               "valid_observation": bool(valid), "phase": phase, "model_calls": 0})
        state["status_at"] = now
    for record in status_records:
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write(md.parent / "logs/monitor-notices" / (stamp + "-" + uuid4().hex[:8] + ".json"), record)
    all_records = [{**r, "kind": "improvement"} for r in records] + status_records
    if records:
        write(md / "latest-improvement.json", records[-1])
        with (md.parent / "IMPROVEMENTS.md").open("a", encoding="utf8") as stream:
            for record in records:
                stream.write(f"- {kst(now)} {record['message']}\n")
    if all_records:
        write(md / "latest-monitor-notice.json", all_records[-1])
        with (md.parent / "MONITORING-NOTICES.md").open("a", encoding="utf8") as stream:
            for record in all_records:
                label = "개선 알림" if record["kind"] == "improvement" else "모니터링"
                stream.write(f"- {kst(now)} [{label}] {record['message']}\n")
                print(f"[{label}] " + record["message"], file=sys.stderr, flush=True)
    # Confirm after delivery; an output failure stays retryable. A crash can repeat a notice.
    write(path, state)
    return records
