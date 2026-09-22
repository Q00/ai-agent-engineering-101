"""에피소드 루프와 러너.

한 run = 한 조건으로 전 시나리오를 한 번. 3조건 x 3리핏 = 9 run.

  python negotiate.py free 1          # run 하나
  python negotiate.py --all           # 9 run 순서대로

results.csv에 append하고, 이미 있는 (run, scenario) 쌍은 건너뛴다. 중단된 실행을 이어
돌릴 수 있어야 하고(claude -p는 간헐적으로 죽는다), 나중에 시나리오를 추가했을 때 기존
줄을 건드리지 않고 빠진 조합만 채울 수 있어야 하기 때문이다.

정답 판정:
  deal_possible(reserve <= budget)이면 정답은 두 한도 안의 가격으로 성사된 deal,
  아니면 정답은 no_deal. open은 어느 쪽에서도 정답이 아니다.
  violation은 deal의 가격이 reserve 밑이거나 budget 위인 경우다.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import acl
import model
import protocol

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios.json"
RESULTS = HERE / "results.csv"
LOGS = HERE / "logs"

MAX_TURNS = 8          # 메시지 8개가 오가면 open
TEMPERATURE = "not settable (claude -p exposes no temperature flag)"
PROVIDER = "Claude Code CLI (claude -p)"

HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]


class Log:
    """stdout과 logs/<run>.txt에 동시에 쓴다.

    run 하나가 파일 하나여야 하는데 tee로 거는 방식은 재개할 때 파일이 갈라진다.
    러너가 직접 append 모드로 열어 두면 이어 돌려도 같은 파일에 쌓인다.
    """

    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8")

    def __call__(self, line=""):
        print(line)
        self.f.write(line + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


def episode(scenario, condition, log):
    """에피소드 하나. buyer가 먼저, 번갈아 말한다."""
    item, reserve, budget = scenario["item"], scenario["reserve"], scenario["budget"]
    deal_possible = int(reserve <= budget)

    systems = {
        "buyer": acl.system_prompt("buyer", item, budget, condition),
        "seller": acl.system_prompt("seller", item, reserve, condition),
    }
    history = {"buyer": [], "seller": []}
    transcript = []
    last_price = {"buyer": None, "seller": None}

    agent_meter, reader_meter = model.Meter(), model.Meter()
    outcome, price = "open", None
    format_errors = 0
    unmatched_accepts = 0     # accept-proposal은 왔는데 기록된 상대 가격이 없는 경우
    flags = []

    log(f"--- scenario {scenario['id']} ({item}) reserve={reserve} budget={budget} "
        f"deal_possible={deal_possible} ---")

    role, other = "buyer", "seller"
    for _ in range(MAX_TURNS):
        text = model.call_model(systems[role], history[role], agent_meter)
        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        transcript.append({"speaker": role, "text": text})
        log(f"[{role}] {text}")

        r = protocol.read(condition, text, transcript, reader_meter)
        flags.extend(r.flags)
        log(f"  [read] {r}" + (f"  # {r.detail}" if r.detail else "")
            + (f"  flags={r.flags}" if r.flags else ""))

        if not r.ok:
            # 읽지 못해도 메시지는 원문 그대로 상대에게 간다. 읽기는 하네스의 일이다.
            format_errors += 1
        elif r.performative == "propose":
            last_price[role] = r.price
        elif r.performative == "accept-proposal":
            if last_price[other] is None:
                # 상대의 가격이 기록된 적이 없다. 수락할 대상이 없으므로 거래로 잡지
                # 못하고 협상은 계속된다. 참조 실행에서 open으로 끝난 원인 중 하나다.
                unmatched_accepts += 1
                log(f"  [protocol] accept-proposal with no recorded price from {other}; "
                    f"cannot close a deal, continuing")
            else:
                outcome, price = "deal", last_price[other]
                break
        elif r.performative == "refuse":
            outcome = "no_deal"
            break

        role, other = other, role

    if outcome == "deal":
        violation = int(price < reserve or price > budget)
        correct = int(deal_possible and not violation)
    else:
        violation = 0
        correct = int(outcome == "no_deal" and not deal_possible)

    note = (f"agent_calls={agent_meter.calls} agent_tokens={agent_meter.tokens} "
            f"reader_tokens={reader_meter.tokens} unmatched_accepts={unmatched_accepts}")
    if flags:
        note += " flags=" + "|".join(sorted(set(f.split(":")[0] for f in flags)))

    log(f"[result] outcome={outcome} price={price} correct={correct} violation={violation} "
        f"turns={len(transcript)} format_errors={format_errors} "
        f"reader_calls={reader_meter.calls}")
    log(f"         {note}")
    log()

    return {"deal_possible": deal_possible, "outcome": outcome, "price": price,
            "correct": correct, "violation": violation, "turns": len(transcript),
            "format_errors": format_errors, "reader_calls": reader_meter.calls, "note": note}


def done_pairs():
    """results.csv에 이미 있는 (run, scenario)."""
    if not RESULTS.is_file():
        return set()
    with RESULTS.open(encoding="utf-8", newline="") as f:
        return {(r["run"], r["scenario"]) for r in csv.DictReader(f)}


def append_row(row):
    new = not RESULTS.is_file()
    with RESULTS.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row[k]) for k in HEADER})


def run(condition, repeat, scenarios):
    run_id = f"{condition}-{repeat}"
    LOGS.mkdir(exist_ok=True)
    log = Log(LOGS / f"{run_id}.txt")
    log(f"# run={run_id} provider={PROVIDER} model={model.MODEL} "
        f"temperature={TEMPERATURE} max_turns={MAX_TURNS}")
    log()

    done = done_pairs()
    for sc in scenarios:
        sid = str(sc["id"])
        if (run_id, sid) in done:
            log(f"--- scenario {sid}: already in results.csv, skipping ---")
            log()
            continue
        base = {"run": run_id, "condition": condition, "scenario": sid}
        try:
            base.update(episode(sc, condition, log))
        except model.ModelError as e:
            log(f"[crash] {e}")
            log()
            base.update({"deal_possible": int(sc["reserve"] <= sc["budget"]),
                         "note": f"crash: {e}"})
        append_row(base)
    log.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("condition", nargs="?", choices=acl.CONDITIONS)
    ap.add_argument("repeat", nargs="?", type=int)
    ap.add_argument("--all", action="store_true", help="9 run 전부")
    a = ap.parse_args()

    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if a.all:
        for c in acl.CONDITIONS:
            for r in (1, 2, 3):
                run(c, r, scenarios)
    elif a.condition and a.repeat:
        run(a.condition, a.repeat, scenarios)
    else:
        ap.error("조건과 리핏을 주거나 --all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
