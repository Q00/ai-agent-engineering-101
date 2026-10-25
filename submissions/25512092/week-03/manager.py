"""manager: 공고 방송 -> 입찰 수집 -> 확신도 최고에게 낙찰.

manager 는 LLM 이 아니라 코드다. 지표는 여기 한곳에서 센다.
메시지 수 = 공고(contractor 수만큼) + 입찰(bid=true 인 답) + 낙찰(1).
"""
from dataclasses import dataclass

from contractor import bid


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


def _short(text: str, n: int = 160) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[:n] + "..."


def run_round(tasks, team, meter, log=print) -> RoundResult:
    """전체 태스크를 한 번씩 처리한다 (= 1 라운드 = 1 실행)."""
    r = RoundResult(tasks=len(tasks))
    names = ", ".join(c.name for c in team)

    for t in tasks:
        cid, gold = t["id"], t["gold"]
        log("")
        log(f"[announce] task {cid} -> {names} | {t['desc']}")
        r.messages += len(team)                       # 공고: contractor 수만큼

        bids = []                                     # (confidence, contractor), 답한 순서대로
        for c in team:
            parsed, raw = bid(c, cid, t["desc"], meter)
            if parsed is None:
                r.parse_fails += 1
                log(f"  [parse-fail] {c.name}: {_short(raw) or '(empty reply)'}")
                continue
            log(f"  [bid] {c.name}: bid={parsed['bid']} "
                f"confidence={parsed['confidence']} reason={_short(parsed['reason'])}")
            if parsed["bid"] is True:
                r.messages += 1                       # 입찰 1건 = 메시지 1
                bids.append((parsed["confidence"], c))

        if not bids:
            r.unassigned += 1                         # 유찰
            log(f"  [unassigned] task {cid} (gold {gold})")
            continue

        bids.sort(key=lambda x: -x[0])                # 정렬이 순서를 유지하므로 동점이면 먼저 답한 쪽
        conf, winner = bids[0]
        r.messages += 1                               # 낙찰 1
        if winner.name == gold:
            r.correct += 1
            verdict = "correct"
        else:
            r.misawards += 1
            verdict = "MISAWARD"
        log(f"  [award] task {cid} -> {winner.name} "
            f"(confidence {conf}, gold {gold}) {verdict}")

    return r