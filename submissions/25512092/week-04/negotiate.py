"""에피소드 루프와 실행 드라이버.

사용법:
    python negotiate.py                        # 기본 실험 (페르소나 없음), 3조건 x 3회
    python negotiate.py --persona liar         # 페르소나 run, results_liar.csv 로 저장
    python negotiate.py --conditions free      # 한 조건만
    python negotiate.py --runs 1               # 1회만

results.csv 에 이미 있는 (run, scenario) 쌍은 건너뛴다.
죽은 에피소드도 지우지 않고 note 에 이유를 적어 한 줄 남긴다.
"""

import argparse
import csv
import json
import os
import traceback
from dataclasses import dataclass, field

from acl import PERSONA, system_prompt
from model import MODEL, PROVIDER, TEMPERATURE, Meter
from protocol import read

MAX_TURNS = 8
FIELDS = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "turns", "format_errors", "reader_calls", "note",
]
KICKOFF = "The negotiation begins. Send your first message."


@dataclass
class Episode:
    outcome: str = "open"
    price: object = None
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    note: str = ""
    transcript: list = field(default_factory=list)


def run_episode(sc, condition, persona, log):
    """시나리오 하나를 한 번 돌린다."""
    ep = Episode()
    meter = Meter()

    systems = {
        "buyer": system_prompt("buyer", sc["item"], sc["budget"], condition, persona),
        "seller": system_prompt("seller", sc["item"], sc["reserve"], condition, persona),
    }
    history = {
        "buyer": [{"role": "user", "content": KICKOFF}],
        "seller": [],
    }
    last_price = {"buyer": None, "seller": None}

    from model import call_model
    role, other = "buyer", "seller"

    for _ in range(MAX_TURNS):
        if not history[role]:
            history[role] = [{"role": "user", "content": KICKOFF}]
        text = call_model(systems[role], history[role], meter=meter)
        ep.turns += 1

        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        ep.transcript.append(f"[{role}] {text}")
        log(f"[{role}]  {text}")

        perf, price, ok, raw = read(condition, text, ep.transcript, meter)
        log(f"  [reader] perf={perf} price={price} ok={ok}"
            + (f" raw={raw!r}" if raw else ""))

        if not ok:
            ep.format_errors += 1            # 읽지 못해도 메시지는 상대에게 간다
        elif perf == "propose":
            last_price[role] = price
        elif perf == "accept-proposal":
            ep.outcome = "deal"
            ep.price = last_price[other]
            if ep.price is None:
                ep.outcome = "open"
                ep.note = "accept-proposal with no recorded counterpart price"
            break
        elif perf == "refuse":
            ep.outcome = "no_deal"
            break

        role, other = other, role

    ep.reader_calls = meter.reader_calls
    deal_possible = sc["reserve"] <= sc["budget"]

    if ep.outcome == "deal":
        ep.violation = int(ep.price < sc["reserve"] or ep.price > sc["budget"])
        ep.correct = int(deal_possible and not ep.violation)
    elif ep.outcome == "no_deal":
        ep.correct = int(not deal_possible)

    tok = f"in={meter.in_tokens} out={meter.out_tokens} agent_calls={meter.agent_calls}"
    ep.note = (ep.note + " | " + tok).strip(" |")
    return ep, deal_possible


def load_done(path):
    done = set()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                done.add((row["run"], row["scenario"]))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", default="none", choices=sorted(PERSONA))
    ap.add_argument("--conditions", nargs="+",
                    default=["free", "tagged", "structured"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--scenarios", default="scenarios.json")
    args = ap.parse_args()

    tag = "" if args.persona == "none" else f"_{args.persona}"
    out_csv = f"results{tag}.csv"
    os.makedirs("logs", exist_ok=True)

    with open(args.scenarios, encoding="utf-8") as f:
        scenarios = json.load(f)

    done = load_done(out_csv)
    new_file = not os.path.exists(out_csv)
    fout = open(out_csv, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fout, fieldnames=FIELDS)
    if new_file:
        writer.writeheader()

    for condition in args.conditions:
        for i in range(1, args.runs + 1):
            run_id = f"{args.persona}-{condition}-{i}" if tag else f"{condition}-{i}"
            log_path = os.path.join("logs", f"{run_id}.txt")
            flog = open(log_path, "a", encoding="utf-8")

            def log(line, _f=flog):
                print(line)
                _f.write(line + "\n")
                _f.flush()

            log(f"# provider={PROVIDER} model={MODEL} temperature={TEMPERATURE} "
                f"max_turns={MAX_TURNS} condition={condition} persona={args.persona} "
                f"run={run_id}")

            for sc in scenarios:
                if (run_id, str(sc["id"])) in done:
                    log(f"# skip scenario {sc['id']} (already in {out_csv})")
                    continue
                log(f"\n=== scenario {sc['id']} {sc['item']} "
                    f"reserve={sc['reserve']} budget={sc['budget']} ===")
                try:
                    ep, deal_possible = run_episode(sc, condition, args.persona, log)
                    note = ep.note
                except Exception as e:                    # 죽은 에피소드도 한 줄 남긴다
                    traceback.print_exc()
                    log(f"[crash] {e}")
                    ep = Episode()
                    ep.outcome = "open"
                    deal_possible = sc["reserve"] <= sc["budget"]
                    note = f"crash: {type(e).__name__}: {e}"

                writer.writerow({
                    "run": run_id,
                    "condition": condition,
                    "scenario": sc["id"],
                    "deal_possible": int(deal_possible),
                    "outcome": ep.outcome,
                    "price": "" if ep.price is None else ep.price,
                    "correct": ep.correct,
                    "violation": ep.violation,
                    "turns": ep.turns,
                    "format_errors": ep.format_errors,
                    "reader_calls": ep.reader_calls,
                    "note": note,
                })
                fout.flush()
                log(f"[result] outcome={ep.outcome} price={ep.price} "
                    f"correct={ep.correct} violation={ep.violation} turns={ep.turns} "
                    f"format_errors={ep.format_errors} reader_calls={ep.reader_calls}")
            flog.close()

    fout.close()
    print(f"\ndone -> {out_csv}")


if __name__ == "__main__":
    main()