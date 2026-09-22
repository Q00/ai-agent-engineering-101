"""Contract net, manager 쪽. 공고 방송 -> 입찰 수집 -> 확신도 최고에 낙찰.

한 라운드 = 태스크 전체를 한 번씩 처리. 지표는 RoundResult 한 곳에서만 센다.

메시지 세는 법 (Smith의 메시지 문법을 따른다):
  공고 1건 x contractor 수   태스크마다 방송
  입찰 1건 x 실제 입찰 수     bid=false와 파싱 실패는 세지 않는다.
                             Smith의 노드는 자격이 안 되면 침묵한다.
  낙찰 1건                    입찰이 하나라도 있을 때만
"""
from dataclasses import dataclass, field

from contract_net import ask_bid


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0
    ties: int = 0              # 확신도 동점으로 낙찰이 갈린 태스크 수
    awards: list = field(default_factory=list)   # (task id, gold, winner, confidence)


def run_round(tasks, team, meter, log=print) -> RoundResult:
    r = RoundResult(tasks=len(tasks))

    for t in tasks:
        log(f"\n--- contract {t['id']} (gold {t['gold']}, {t.get('kind', '-')}) ---")
        log(f"[announce] -> {', '.join(c.name for c in team)}: {t['desc']}")
        r.messages += len(team)                  # 방송 공고: contractor 수만큼

        bids = []
        for c in team:
            b, raw = ask_bid(c, t["id"], t["desc"], meter)
            if b is None:
                r.parse_fails += 1
                log(f"[no-bid] {c.name}: unparseable reply, counted as no bid "
                    f"| raw={raw[:160]!r}")
                continue
            if not b.bid:
                log(f"[no-bid] {c.name}: confidence={b.confidence} reason={b.reason}")
                continue
            r.messages += 1                      # 입찰 1건 = 메시지 1
            log(f"[bid] {c.name}: confidence={b.confidence} reason={b.reason}")
            bids.append((b.confidence, c))

        if not bids:
            r.unassigned += 1
            log(f"[unassigned] contract {t['id']} drew no bid")
            continue

        top = max(conf for conf, _ in bids)
        tied = [c.name for conf, c in bids if conf == top]
        if len(tied) > 1:
            r.ties += 1
        winner = next(c for conf, c in bids if conf == top)   # 동점이면 먼저 입찰한 쪽
        r.messages += 1                                       # 낙찰 메시지
        r.awards.append((t["id"], t["gold"], winner.name, top))

        if winner.name == t["gold"]:
            r.correct += 1
            verdict = "correct"
        else:
            r.misawards += 1
            verdict = f"misaward (gold {t['gold']})"
        tie_note = f" [tie among {','.join(tied)}, first bidder wins]" if len(tied) > 1 else ""
        log(f"[award] contract {t['id']} -> {winner.name} @ {top} : {verdict}{tie_note}")

    return r
