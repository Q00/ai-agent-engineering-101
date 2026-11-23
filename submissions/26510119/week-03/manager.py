"""Manager: 공고 방송 -> 입찰 수집 -> 확신도 최고에 낙찰."""
from dataclasses import dataclass, field
from contractor import bid


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


def run_round(tasks, team, meter, log=print):
    r = RoundResult(tasks=len(tasks))

    for t in tasks:
        log(f"\n[task {t['id']}] {t['desc']} (gold: {t['gold']})")

        # 공고: contractor 수만큼 메시지
        r.messages += len(team)

        bids = []
        for c in team:
            parsed, err = bid(c, t["id"], t["desc"], meter)
            if parsed is None:
                log(f"  [bid] {c.name}: no valid bid — {err}")
                continue
            log(f"  [bid] {c.name}: bid={parsed['bid']} confidence={parsed['confidence']} reason={parsed.get('reason', '')}")
            if parsed["bid"] is True:
                r.messages += 1  # 입찰 1건 = 메시지 1
                bids.append((parsed["confidence"], c))

        if not bids:
            r.unassigned += 1
            log(f"  [award] no bids — task unassigned")
            continue

        # 확신도 최고, 동점이면 먼저 답한 쪽
        bids.sort(key=lambda x: -x[0])
        winner = bids[0][1]
        r.messages += 1  # 낙찰 메시지

        if winner.name == t["gold"]:
            r.correct += 1
            log(f"  [award] {winner.name} (correct, gold={t['gold']})")
        else:
            r.misawards += 1
            log(f"  [award] {winner.name} (misaward, gold={t['gold']})")

    r.parse_fails = meter.parse_fails
    return r
