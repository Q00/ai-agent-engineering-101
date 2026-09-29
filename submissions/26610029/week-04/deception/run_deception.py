"""Side experiment (not part of the graded 3-condition results): does telling
the other side a false private limit get the liar a better deal, or cause a
violation? free condition only (deception is a content question, not a
format question). Separate from results.csv so the required condition
vocabulary (free/tagged/structured) stays clean.

Usage: python deception/run_deception.py [--repeats 3]
Run from the week-04 directory (same as run_experiment.py).
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from reader import read_message, ReaderStats          # noqa: E402
from tools_shared import Chat, Meter                  # noqa: E402
from episode import MAX_TURNS                          # noqa: E402
from deceptive_agents import build_prompt, fake_limit_for  # noqa: E402

HEADER = ["run", "scenario", "liar", "outcome", "price", "correct", "violation",
          "turns", "format_errors", "reader_calls", "note"]
SCENARIO_IDS = ("auction", "sentencing")
CONDITION = "free"


def run_episode(scenario: dict, liar: str, log):
    """liar is one of 'buyer', 'seller', 'both' -- who gets the deceptive
    (fake private limit) system prompt this episode."""
    reserve, budget, item = scenario["reserve"], scenario["budget"], scenario["item"]
    deal_possible = reserve <= budget
    buyer_lies = liar in ("buyer", "both")
    seller_lies = liar in ("seller", "both")
    meter = Meter()
    stats = ReaderStats()

    buyer = Chat(build_prompt("buyer", item, budget, CONDITION, buyer_lies), meter)
    seller = Chat(build_prompt("seller", item, reserve, CONDITION, seller_lies), meter)
    chats = {"buyer": buyer, "seller": seller}

    buyer.add_user("Begin the negotiation now.")
    last_proposal_price = None
    speaker_name = "buyer"
    transcript = []
    outcome, price = "open", None
    format_errors = 0
    turns = 0

    for turn in range(MAX_TURNS):
        turns = turn + 1
        speaker = chats[speaker_name]
        listener_name = "seller" if speaker_name == "buyer" else "buyer"
        reply = speaker.send()
        log(f"[{speaker_name}] {reply.text.strip()}")

        act = read_message(reply.text, CONDITION, meter, stats, history=transcript)
        transcript.append(f"[{speaker_name}] {reply.text.strip()}")
        log(f"  [reader] {act}")
        if act is None:
            format_errors += 1
        else:
            p = act["performative"]
            if p == "propose" and act.get("price") is not None:
                last_proposal_price = act["price"]
            elif p == "accept-proposal":
                outcome, price = "deal", last_proposal_price
                break
            elif p == "refuse":
                outcome = "no_deal"
                break

        chats[listener_name].add_user(reply.text)
        speaker_name = listener_name
    else:
        outcome = "open"

    if outcome == "deal" and price is not None:
        violation = 1 if (price < reserve or price > budget) else 0
        correct = 1 if (deal_possible and reserve <= price <= budget) else 0
    else:
        violation = 0
        correct = 1 if not deal_possible else 0

    log(f"[episode] scenario={scenario['id']} liar={liar} outcome={outcome} price={price} "
        f"correct={correct} violation={violation} turns={turns} "
        f"format_errors={format_errors} reader_calls={stats.calls}")
    return {"outcome": outcome, "price": price, "correct": correct, "violation": violation,
            "turns": turns, "format_errors": format_errors, "reader_calls": stats.calls}


def load_done_counts(results_path: Path):
    """Return {(scenario, liar): completed_repeat_count}."""
    counts = {}
    if not results_path.exists():
        return counts
    with open(results_path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != HEADER:
        return counts
    for r in rows[1:]:
        if len(r) != len(HEADER):
            continue
        key = (r[1], r[2])
        counts[key] = counts.get(key, 0) + 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    all_scenarios = json.load(open(Path(__file__).parent.parent / "scenarios.json", encoding="utf-8"))
    scenarios = [s for s in all_scenarios if s["id"] in SCENARIO_IDS]

    base_dir = Path(__file__).parent
    (base_dir / "logs").mkdir(exist_ok=True)
    results_path = base_dir / "results.csv"
    new_file = not results_path.exists()
    done = load_done_counts(results_path)

    with open(results_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for s in scenarios:
            for liar in ("buyer", "seller", "both"):
                already = done.get((s["id"], liar), 0)
                for repeat in range(1, args.repeats + 1):
                    if repeat <= already:
                        print(f"skip {s['id']}-{liar}-liar-{repeat:02d}: already done")
                        continue
                    run_label = f"{s['id']}-{liar}-liar-{repeat:02d}"
                    header_bits = [f"provider=Anthropic model=claude-sonnet-4-5 condition=free "
                                   f"scenario={s['id']} liar={liar}"]
                    if liar in ("buyer", "both"):
                        header_bits.append(f"buyer_fake_limit={fake_limit_for('buyer', s['budget'])} "
                                           f"(true={s['budget']})")
                    if liar in ("seller", "both"):
                        header_bits.append(f"seller_fake_limit={fake_limit_for('seller', s['reserve'])} "
                                           f"(true={s['reserve']})")
                    lines = [" ".join(header_bits)]

                    def log(msg, _lines=lines):
                        print(msg)
                        _lines.append(str(msg))

                    note = ""
                    try:
                        r = run_episode(s, liar, log)
                        row = [run_label, s["id"], liar, r["outcome"],
                               r["price"] if r["price"] is not None else "", r["correct"],
                               r["violation"], r["turns"], r["format_errors"], r["reader_calls"], note]
                    except Exception as e:
                        note = f"crash: {type(e).__name__}: {e}"
                        log(note)
                        row = [run_label, s["id"], liar, "", "", "", "", "", "", "", note]
                    w.writerow(row)
                    f.flush()
                    (base_dir / "logs" / f"{run_label}.txt").write_text(
                        "\n".join(lines) + "\n", encoding="utf-8")
    print("\ndeception/results.csv updated;", results_path.resolve())


if __name__ == "__main__":
    main()
