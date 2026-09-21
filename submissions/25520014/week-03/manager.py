"""manager: 공고 방송 → 입찰 수집 → 확신도 최고에 낙찰. 지표는 한곳에서 센다.

강의노트의 run_round를 그대로 따른다. 조건 세 개는 team 인자로만 들어오므로
이 파일에는 독립변수가 없다.

메시지 계산 규칙(노트 지정):
  공고   태스크당 contractor 수만큼
  입찰   bid=true 한 건당 1. 거절과 파싱 실패는 세지 않는다.
  낙찰   1. 입찰이 하나도 없으면 0.
"""
from dataclasses import dataclass

from contractor import ANNOUNCEMENT, bid


@dataclass
class RoundResult:
    tasks: int
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0   # note에 적는다. results.csv의 칼럼은 아니다.
    fences: int = 0        # 코드 펜스를 벗겨 구제한 횟수. 파싱 기준의 영향을 남긴다.


def run_round(tasks, team, meter, log=print) -> RoundResult:
    r = RoundResult(tasks=len(tasks))
    for t in tasks:
        log(ANNOUNCEMENT.format(cid=t["id"], desc=t["desc"]))
        r.messages += len(team)          # 방송 공고: contractor 수만큼

        bids = []
        for c in team:
            b, raw = bid(c, t["id"], t["desc"], meter)
            if raw.strip().startswith("```"):
                r.fences += 1
            if b is None:
                r.parse_fails += 1
                log(f"  [bid] {c.name}: UNPARSEABLE -> {raw.strip()[:200]!r}")
                continue
            log(f"  [bid] {c.name}: bid={b['bid']} "
                f"confidence={b['confidence']:g} reason={b['reason']}")
            if b["bid"] is True:
                r.messages += 1          # 입찰 1건 = 메시지 1
                bids.append((b["confidence"], c))

        if not bids:
            r.unassigned += 1
            log(f"  [award] no bid -> unassigned (gold {t['gold']})\n")
            continue

        # 확신도 내림차순. 파이썬 정렬은 안정적이라 동점이면 입찰 순서, 즉
        # 먼저 응답한 쪽이 앞에 남는다. 노트의 "같으면 먼저 답한 쪽" 규칙이다.
        bids.sort(key=lambda x: -x[0])
        winner = bids[0][1]
        r.messages += 1                  # 낙찰 메시지
        if winner.name == t["gold"]:
            r.correct += 1
        else:
            r.misawards += 1
        log(f"  [award] {winner.name} (gold {t['gold']})\n")

    return r
