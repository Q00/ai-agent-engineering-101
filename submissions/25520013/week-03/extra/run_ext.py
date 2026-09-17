"""Stage 2 runner: three arms, three rounds, one contract net per round.

    uv run run_ext.py                                  # all arms, 3 rounds
    uv run run_ext.py --arms full --rounds 1           # a trial

The arms are a 2x2 with stage 1 as the shared control: stage 1 is a Python
decider on confidence alone, `llm-judge` adds the reasoning decider, and
`calibrated` adds the verified record, each one step from that control.
`full` is both, which is the cell stage 1's conclusion points at.

Scoring is per gold element, not per fragment. The element list is fixed in
`tasks_ext.json`, so the manager may split a task any way it likes and the
denominator stays at ten.
"""
import argparse
import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import backend                                           # noqa: E402
from contract_net import ANNOUNCEMENT, _collect, parse_bid   # noqa: E402
import manager as mgr                                    # noqa: E402
import orchestrator as orc                               # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ARMS = {
    "llm-judge": {"decider": "llm", "tools": ["ask", "award"]},
    "calibrated": {"decider": "rule", "tools": []},
    "full": {"decider": "llm", "tools": ["get_trajectory", "ask", "award"]},
}
FIELDS = ["arm", "round", "elements", "correct", "done", "messages",
          "unassigned", "misawards", "fragments", "self_awards", "mgr_turns",
          "tool_calls", "tool_refused", "note"]


def cover(elements, fragments):
    """Map each gold element to the first fragment that claims its skill.

    This is the whole of the decomposition measurement: the manager declares
    what each fragment needs, and an element is served by the first fragment
    claiming its need. An element no fragment claims is unassigned — that is
    how a missed split shows up without anyone judging the split.
    """
    return [next((i for i, f in enumerate(fragments) if el["need"] in f["needs"]),
                 None) for el in elements]


def run_round(arm, rnd, tasks, team, record, log):
    spec = ARMS[arm]
    names = [m["name"] for m in team]
    meter = backend.Meter()
    tally = dict(elements=0, correct=0, done=0, messages=0, unassigned=0,
                 misawards=0, fragments=0, self_awards=0, mgr_turns=0,
                 refused=0, tool_calls=0, tool_refused=0)

    for index, task in enumerate(tasks):
        boss = team[index % len(team)]              # the manager rotates
        log(f"\n=== round {rnd} · task {task['id']} · manager {boss['name']} ===")
        log(f"  {task['desc']}")

        fragments = mgr.decompose(boss, task, meter, log)
        tally["fragments"] += len(fragments)
        served = cover(task["elements"], fragments)
        tally["elements"] += len(task["elements"])

        winners, answers, claimed = {}, {}, {}
        for i, frag in enumerate(fragments):
            announcement = ANNOUNCEMENT.format(id=f"{task['id']}.{i}",
                                               desc=frag["text"])
            tally["messages"] += len(team)
            bids = []
            for member, reply in zip(team, _collect(team, announcement, meter)):
                kind, conf, reason = parse_bid(reply)
                if kind == "bid":
                    bids.append((member["name"], conf, reason))
                    tally["messages"] += 1
                    claimed.setdefault(i, {})[member["name"]] = conf
                    log(f"  [bid ] {member['name']} confidence={conf:g} :: {reason[:90]}")
                else:
                    log(f"  [{'pass' if kind == 'pass' else 'fail'}] {member['name']}")

            if spec["decider"] == "rule":
                who, scored, turns = mgr.decide_rule(bids, frag["needs"], record)
                if scored:
                    log("  [score] " + "  ".join(f"{n}={s:.3f}" for n, s in scored))
                refused = 0
            else:
                who, turns, refused = mgr.decide_llm(
                    boss, frag, bids, record, team, spec["tools"], meter, log)
            tally["mgr_turns"] += turns
            tally["refused"] += refused

            if who is None:
                log(f"  [award] fragment {i} — none")
                continue
            tally["messages"] += 1
            winners[i] = who
            if who == boss["name"]:
                tally["self_awards"] += 1
            if i not in [s for s in served if s is not None]:
                log(f"  [award] fragment {i} -> {who} (covers no graded element)")
                continue
            member = next(m for m in team if m["name"] == who)
            specs = [el["verify"]
                     for el, at in zip(task["elements"], served) if at == i]
            answers[i], calls, denied = orc.do_work(
                member, frag["text"], meter, log, specs)
            tally["tool_calls"] += calls
            tally["tool_refused"] += denied

        for el, frag_index in zip(task["elements"], served):
            who = winners.get(frag_index) if frag_index is not None else None
            if who is None:
                tally["unassigned"] += 1
                log(f"  [elem] need={el['need']} gold={el['gold']} -> UNASSIGNED")
                continue
            if who == el["gold"]:
                tally["correct"] += 1
            else:
                tally["misawards"] += 1
            passed = orc.judge(el, answers.get(frag_index, ""), log)
            tally["done"] += int(passed)
            record.note(who, el["need"], passed,
                        claimed.get(frag_index, {}).get(who, 0.0))
            log(f"  [elem] need={el['need']} gold={el['gold']} -> {who} "
                f"{'gold' if who == el['gold'] else 'MISAWARD'} "
                f"{'PASS' if passed else 'FAIL'}")

    log("\n" + record.table(names))
    return tally, meter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", nargs="+", default=list(ARMS))
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    tasks = json.loads((HERE / "tasks_ext.json").read_text(encoding="utf-8"))
    (HERE / "logs").mkdir(exist_ok=True)

    with open(HERE / "results_ext.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for arm in args.arms:
            record = orc.Record()              # each arm starts with no history
            team = orc.build_team()
            for rnd in range(1, args.rounds + 1):
                path = HERE / "logs" / f"{arm}-round{rnd}.txt"
                with open(path, "w", encoding="utf-8") as lf:
                    def log(line):
                        print(line, file=lf, flush=True)
                    log(f"arm={arm} round={rnd} model={backend.MODEL} "
                        f"k={orc.K} max_turns={mgr.MAX_TURNS}")
                    tally, meter = run_round(arm, rnd, tasks, team, record, log)
                    note = (f"model={backend.MODEL} calls={meter.calls} "
                            f"tokens={meter.tokens} "
                            f"mgr_refused={tally['refused']} "
                            f"cli_failures={meter.failures}")
                    log("\n" + note)
                row = {k: tally[k] for k in FIELDS if k in tally}
                row.update(arm=arm, round=rnd, note=note)
                writer.writerow(row)
                fh.flush()
                print(f"{arm} round {rnd}: correct={tally['correct']}/"
                      f"{tally['elements']} done={tally['done']} "
                      f"frags={tally['fragments']} self={tally['self_awards']} "
                      f"tools={tally['tool_calls']}/{tally['tool_refused']}ref "
                      f"calls={meter.calls}")


if __name__ == "__main__":
    main()
