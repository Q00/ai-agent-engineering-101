"""에피소드 루프와 러너.

에피소드 하나: buyer가 먼저 말하고 둘이 번갈아 말한다. 자기 메시지는 자기 history에
assistant로, 상대 history에 user로 들어간다. 프로토콜 계층이 메시지를 읽어 행위를
정하고, accept-proposal이면 상대의 마지막 propose 가격으로 거래가 성립한다.

run 하나 = 조건 하나로 시나리오 전체를 한 번. 로그 파일 하나가 run 하나다.

사용법:
  python negotiate.py --condition free --repeat 1
  python negotiate.py --condition free --repeat 1 | tee logs/free-01.txt

results.csv에 이미 있는 (run, scenario) 쌍은 건너뛰므로, 중단된 실행은 같은 명령을
다시 돌리면 이어진다.
"""
import argparse
import csv
import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import acl
import protocol
from model import Meter, call_model, effective_settings, settings_line

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.csv"
SCENARIOS = HERE / "scenarios.json"
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]

MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "8"))


@dataclass
class Episode:
    run: str
    condition: str
    scenario: str
    deal_possible: int
    outcome: str = "open"
    price: int = None
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    notes: list = field(default_factory=list)

    def row(self) -> list:
        return [self.run, self.condition, self.scenario, self.deal_possible, self.outcome,
                "" if self.price is None else self.price, self.correct, self.violation,
                self.turns, self.format_errors, self.reader_calls, "; ".join(self.notes)]


def crashed_row(run, condition, scenario, deal_possible, note) -> list:
    """죽은 에피소드도 지우지 않는다. 수치는 비우고 이유만 note에 적는다."""
    return [run, condition, scenario, deal_possible, "", "", "", "", "", "", "", note]


# ---------------------------------------------------------------- 에피소드 하나


def run_episode(run: str, condition: str, sc: dict, meter: Meter) -> Episode:
    item, reserve, budget = sc["item"], sc["reserve"], sc["budget"]
    ep = Episode(run=run, condition=condition, scenario=str(sc["id"]),
                 deal_possible=int(reserve <= budget))

    systems = {"buyer": acl.system_prompt("buyer", item, budget, condition),
               "seller": acl.system_prompt("seller", item, reserve, condition)}
    history = {"buyer": [{"role": "user", "content": acl.KICKOFF}], "seller": []}
    last_price = {"buyer": None, "seller": None}
    transcript = []

    print(f"--- scenario {sc['id']} ({item}) reserve {reserve} budget {budget} "
          f"deal_possible={ep.deal_possible}", flush=True)

    role, other = "buyer", "seller"
    for _ in range(MAX_TURNS):
        text = call_model(systems[role], history[role], meter).strip()
        ep.turns += 1
        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        transcript.append(f"[{role}] {text}")
        print(f"[{role:6}] {text}", flush=True)

        before = meter.reader_calls
        r = protocol.read(condition, text, "\n".join(transcript), meter)
        ep.reader_calls += meter.reader_calls - before
        print(f"  [read] {r.detail}", flush=True)

        if not r.ok:
            ep.format_errors += 1            # 읽지 못해도 메시지는 상대에게 이미 갔다
        elif r.performative == "propose":
            last_price[role] = r.price
        elif r.performative == "accept-proposal":
            if last_price[other] is None:
                # 상대의 propose 가격이 기록되지 않은 채로 온 수락. 거래로 잡을 수 없다.
                ep.notes.append("accept-proposal with no recorded price")
                print("  [read] accept-proposal but no price recorded for the other side; "
                      "the episode continues", flush=True)
            else:
                ep.outcome, ep.price = "deal", last_price[other]
                break
        elif r.performative == "refuse":
            ep.outcome = "no_deal"
            break

        role, other = other, role

    if ep.outcome == "deal":
        ep.violation = int(ep.price < reserve or ep.price > budget)
        ep.correct = int(ep.deal_possible and not ep.violation)
    elif ep.outcome == "no_deal":
        ep.correct = int(not ep.deal_possible)
    else:
        ep.correct = 0                        # open은 정답이 아니다

    print(f"[result] outcome={ep.outcome} price={'' if ep.price is None else ep.price} "
          f"correct={ep.correct} violation={ep.violation} turns={ep.turns} "
          f"format_errors={ep.format_errors} reader_calls={ep.reader_calls}", flush=True)
    return ep


# ---------------------------------------------------------------- results.csv


def existing_pairs() -> set:
    if not RESULTS.is_file():
        return set()
    with RESULTS.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return {(r[0].strip(), r[2].strip()) for r in rows[1:] if len(r) == len(HEADER)}


def append_row(row: list):
    new = not RESULTS.is_file()
    with RESULTS.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


# ---------------------------------------------------------------- run 하나


def main() -> int:
    ap = argparse.ArgumentParser(description="one run = one condition over every scenario")
    ap.add_argument("--condition", required=True, choices=acl.CONDITIONS)
    ap.add_argument("--repeat", type=int, required=True, help="repeat number, 1..3")
    args = ap.parse_args()

    run = f"{args.condition}-{args.repeat:02d}"
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    done = existing_pairs()
    meter = Meter()

    print(f"run={run} condition={args.condition} {settings_line()} turn_limit={MAX_TURNS}",
          flush=True)
    print(f"scenarios={[s['id'] for s in scenarios]} already_done="
          f"{sorted(s for r, s in done if r == run)}", flush=True)

    for sc in scenarios:
        if (run, str(sc["id"])) in done:
            print(f"--- scenario {sc['id']} already in results.csv, skipped", flush=True)
            continue
        try:
            ep = run_episode(run, args.condition, sc, meter)
            append_row(ep.row())
        except Exception as e:
            note = f"crashed: {type(e).__name__}: {e}".replace("\n", " ")[:200]
            print(f"[result] {note}", flush=True)
            traceback.print_exc()
            append_row(crashed_row(run, args.condition, str(sc["id"]),
                                   int(sc["reserve"] <= sc["budget"]), note))

    print(f"[run] {run} done. agent_calls={meter.agent_calls} "
          f"reader_calls={meter.reader_calls} retries={meter.retries} tokens={meter.tokens}",
          flush=True)
    print(f"[run] {effective_settings()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
