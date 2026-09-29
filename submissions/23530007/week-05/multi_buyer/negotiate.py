"""에피소드 하나: buyer 3명과 seller 1명의 공개 협상.

발화 순서는 buyer_1, buyer_2, buyer_3, seller를 반복한다. 모든 메시지는 4명이 다 본다.
buyer가 refuse하면 그 buyer만 빠지고 순서에서 건너뛴다. seller가 refuse하거나 buyer가
전부 빠지면 no_deal이다.

기록 규칙은 실행 전에 못박는다.
  deal_possible  reserve가 buyer 3명의 budget 중 최댓값 이하.
  correct   거래가 가능한 시나리오에서 한도 안의 deal이거나, 불가능한 시나리오에서 no_deal.
            open은 어느 쪽도 정답이 아니다.
  winner    거래를 성사시킨 buyer. no_deal이나 open이면 비어 있다.
  violation deal 가격이 reserve 미만이거나 winner의 budget 초과.
  turns     오간 메시지 수.
  format_errors  프로토콜 계층이 읽지 못한 메시지 수. 읽지 못해도 메시지는 모두에게 간다.
  reader_calls   메시지를 읽는 데 쓴 모델 호출 수.

거래 성립 규칙:
  seller의 accept-proposal은 지목한 buyer의 마지막 propose 가격으로 거래한다.
  buyer의 accept-proposal은 seller의 마지막 propose(요구가) 가격으로 거래한다.
  해당 가격이 기록돼 있지 않으면 거래로 잡지 않고 계속 진행하며 unresolved_accepts에 센다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from acl import BUYERS, MAX_TURNS, ROLES, Meter, call_model, read_message, system_prompt


@dataclass
class Episode:
    scenario: int
    condition: str
    deal_possible: int
    outcome: str = "open"
    price: int | None = None
    winner: str = ""
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    unresolved_accepts: int = 0
    meter: Meter = field(default_factory=Meter)


def run_episode(sc: dict, condition: str, log) -> Episode:
    budgets = dict(zip(BUYERS, sc["budgets"]))
    ep = Episode(scenario=sc["id"], condition=condition,
                 deal_possible=int(sc["reserve"] <= max(budgets.values())))
    limits = {**budgets, "seller": sc["reserve"]}
    systems = {r: system_prompt(r, sc["item"], limits[r], condition) for r in ROLES}
    pending = {r: [] for r in ROLES}      # 내가 마지막으로 말한 뒤 올라온 공개 메시지
    history = {r: [] for r in ROLES}
    last_offer = {b: None for b in BUYERS}    # buyer별 마지막 propose 가격
    last_ask = None                           # seller의 마지막 propose 가격
    active = list(BUYERS)
    transcript: list[str] = []

    log(f"--- scenario {sc['id']} ({sc['item']}), reserve {sc['reserve']}, budgets {sc['budgets']}, "
        f"deal_possible={ep.deal_possible}")

    order = 0
    for _ in range(MAX_TURNS):
        # 순서상 다음 화자. 떠난 buyer는 건너뛴다.
        role = ROLES[order % len(ROLES)]
        order += 1
        if role != "seller" and role not in active:
            continue

        content = "\n".join(pending[role]) or "Begin the negotiation."
        pending[role].clear()
        history[role].append({"role": "user", "content": content})
        text = call_model(systems[role], history[role], ep.meter)
        ep.turns += 1
        history[role].append({"role": "assistant", "content": text})
        line = f"[{role}] {text}"
        transcript.append(line)
        for r in ROLES:
            if r != role:
                pending[r].append(line)
        log(line)

        perf, price, target, ok, label = read_message(condition, role, text, transcript, ep.meter)
        log(f"  [{label}]")

        if not ok:
            ep.format_errors += 1          # 읽지 못해도 메시지는 이미 모두에게 갔다
        elif perf == "propose":
            if role == "seller":
                last_ask = price
            else:
                last_offer[role] = price
        elif perf == "accept-proposal":
            deal_price = last_ask if role != "seller" else last_offer.get(target)
            if deal_price is None:
                ep.unresolved_accepts += 1
                log("  # accept-proposal이지만 대상의 기록된 가격이 없다 — 거래로 잡지 못함")
            else:
                ep.outcome, ep.price = "deal", deal_price
                ep.winner = role if role != "seller" else target
                break
        elif perf == "refuse":
            if role == "seller":
                ep.outcome = "no_deal"
                break
            active.remove(role)
            log(f"  # {role} 이탈, 남은 buyer: {active}")
            if not active:
                ep.outcome = "no_deal"
                break

    if ep.outcome == "deal":
        ep.violation = int(ep.price < sc["reserve"] or ep.price > budgets[ep.winner])
        ep.correct = int(ep.deal_possible and not ep.violation)
    elif ep.outcome == "no_deal":
        ep.correct = int(not ep.deal_possible)

    ep.reader_calls = ep.meter.reader_calls
    log(f"[result] outcome={ep.outcome} price={ep.price if ep.price is not None else ''} "
        f"winner={ep.winner} correct={ep.correct} violation={ep.violation} turns={ep.turns} "
        f"format_errors={ep.format_errors} reader_calls={ep.reader_calls}")
    log("")
    return ep
