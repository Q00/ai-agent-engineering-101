from __future__ import annotations
import asyncio


from dataclasses import dataclass, field

from chat import Chat
from contractor import Contractor, bid, execute

@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0
    execution_success: int = 0
    execution_fail: int = 0
    execution_messages: int = 0
    execution_attempted: int = 0
    gold_ok_exec_ok: int = 0
    gold_ok_exec_fail: int = 0
    misaward_exec_ok: int = 0
    misaward_exec_fail: int = 0
    note: str = ""

def run_round(tasks: list[dict], team: list[Contractor], chat: Chat, log=print, execute_awards: bool = True) -> RoundResult:
    return asyncio.run(_run_round_async(tasks, team, chat, log, execute_awards))

async def _run_round_async(tasks: list[dict], team: list[Contractor], chat: Chat, log, execute_awards: bool) -> RoundResult:
    r = RoundResult(tasks=len(tasks))
    for t in tasks:
        log(f"[cfp] task {t['id']} gold = {t['gold']}")
        r.messages += len(team)

        async def one_bid(c: Contractor):
            # if async to thread : no execution (in coroutine)
            # return c, await asyncio.to_thread(bid, c, t["id"], t["desc"], chat)
            return c, await bid(c, t["id"], t["desc"], chat)
        
        pairs = await asyncio.gather(*[one_bid(c) for c in team])

        bids: list[tuple[float, Contractor, dict]] = []

        for c, b in pairs:
            if b is None:
                r.parse_fails += 1
                log(f" [bid] {c.name}: PARSE_FAIL")
                continue
            log(f" [bid] {c.name}: bid = {b['bid']} confidence={b['confidence']} reason={b['reason']}")
            
            if b['bid'] is True:
                r.messages += 1
                bids.append((b["confidence"], c, b))
            
        if not bids:
            r.unassigned += 1
            log(f" [award] none (unassigned)")
            continue

        bids.sort(key=lambda x: -x[0])
        winner = bids[0][1]
        r.messages += 1
        awarded_gold = winner.name == t["gold"]

        if awarded_gold:
            r.correct += 1
        else:
            r.misawards += 1
        log(f" [award] {winner.name} (gold {t['gold']})")

        if not execute_awards:
            continue
        result = await execute(winner, t, chat)
        r.execution_attempted += 1
        r.execution_messages += 1

        if result["success"]:
            r.execution_success += 1
            if awarded_gold:
                r.gold_ok_exec_ok += 1
            else:
                r.misaward_exec_ok += 1
        else:
            r.execution_fail += 1
            if awared_gold:
                r.gold_ok_exec_fail += 1
            else:
                r.misaward_exec_fail += 1
            
        snippet = str(result["output"]).replace("\n", " ")[:160]
        log(
            f"  [inform] {winner.name} backend={result['backend']} "
            f"success={result['success']} "
            f"cell={'gold_ok' if awarded_gold else 'misaward'}x"
            f"{'exec_ok' if result['success'] else 'exec_fail'} "
            f"output={snippet}"
        )
    
    return r
