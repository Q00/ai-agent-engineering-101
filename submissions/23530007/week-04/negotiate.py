"""에피소드 하나: buyer가 먼저 말하고 둘이 번갈아 말한다.

기록 규칙은 실행 전에 못박는다.
  correct   거래가 가능한 시나리오에서 한도 안의 deal이거나, 불가능한 시나리오에서 no_deal.
            open은 어느 쪽도 정답이 아니다.
  violation deal 가격이 reserve 미만이거나 budget 초과.
  turns     오간 메시지 수.
  format_errors  프로토콜 계층이 읽지 못한 메시지 수. 읽지 못해도 메시지는 상대에게 간다.
  reader_calls   메시지를 읽는 데 쓴 모델 호출 수.

accept-proposal이 왔는데 상대의 마지막 propose 가격이 기록돼 있지 않으면 거래로
잡을 수 없다. 이때는 끊지 않고 계속 진행한다(대개 open으로 끝난다). 참조 실행이
말한 "상대가 accept-proposal을 보냈지만 기록된 가격이 없어 거래로 잡지 못한" 경우이며,
몇 번 일어났는지 unresolved_accepts에 세어 note로 내보낸다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from acl import MAX_TURNS, Meter, call_model, read_message, system_prompt


@dataclass
class Episode:
    scenario: int
    condition: str
    deal_possible: int
    outcome: str = "open"
    price: int | None = None
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    unresolved_accepts: int = 0
    meter: Meter = field(default_factory=Meter)


def run_episode(sc: dict, condition: str, log) -> Episode:
    ep = Episode(scenario=sc["id"], condition=condition,
                 deal_possible=int(sc["reserve"] <= sc["budget"]))
    limits = {"buyer": sc["budget"], "seller": sc["reserve"]}
    systems = {r: system_prompt(r, sc["item"], limits[r], condition) for r in ("buyer", "seller")}
    history = {"buyer": [], "seller": []}
    last_price = {"buyer": None, "seller": None}
    transcript: list[str] = []

    log(f"--- scenario {sc['id']} ({sc['item']}), reserve {sc['reserve']}, budget {sc['budget']}, "
        f"deal_possible={ep.deal_possible}")

    role, other = "buyer", "seller"
    for _ in range(MAX_TURNS):
        msgs = history[role] or [{"role": "user", "content": "Begin the negotiation."}]
        text = call_model(systems[role], msgs, ep.meter)
        ep.turns += 1
        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        transcript.append(f"[{role}] {text}")
        log(f"[{role}] {text}")

        perf, price, ok, label = read_message(condition, text, transcript, ep.meter)
        log(f"  [{label}]")

        if not ok:
            ep.format_errors += 1          # 읽지 못해도 메시지는 이미 상대에게 갔다
        elif perf == "propose":
            last_price[role] = price
        elif perf == "accept-proposal":
            if last_price[other] is None:
                ep.unresolved_accepts += 1
                log("  # accept-proposal이지만 상대의 기록된 가격이 없다 — 거래로 잡지 못함")
            else:
                ep.outcome, ep.price = "deal", last_price[other]
                break
        elif perf == "refuse":
            ep.outcome = "no_deal"
            break
        role, other = other, role

    if ep.outcome == "deal":
        ep.violation = int(ep.price < sc["reserve"] or ep.price > sc["budget"])
        ep.correct = int(ep.deal_possible and not ep.violation)
    elif ep.outcome == "no_deal":
        ep.correct = int(not ep.deal_possible)

    ep.reader_calls = ep.meter.reader_calls
    log(f"[result] outcome={ep.outcome} price={ep.price if ep.price is not None else ''} "
        f"correct={ep.correct} violation={ep.violation} turns={ep.turns} "
        f"format_errors={ep.format_errors} reader_calls={ep.reader_calls}")
    log("")
    return ep
