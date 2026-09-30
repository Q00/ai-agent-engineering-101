"""Week-04 runner: buyer vs seller price negotiation in three message formats (free, tagged, structured).

  $env:OPENROUTER_API_KEY = "..."            # never committed
  python negotiate.py                        # 3 conditions x 3 repeats x all scenarios
  python negotiate.py --conditions structured --repeats 1

One log per run (one condition, one repeat, all scenarios): logs/<condition>-<repeat:02>.txt
Rows already in results.csv are skipped (by run, scenario), so an interrupted run continues.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import acl
import llm
from llm import LLMError, Meter, call_model

if hasattr(sys.stdout, "reconfigure"):          # Windows console (cp949) safety
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct", "violation",
          "turns", "format_errors", "reader_calls", "note"]
CONDITIONS = ["free", "tagged", "structured"]
MAX_TURNS = 8
OPENER = "The seller is waiting. Write your first message to the seller."   # buyer's first user turn


def episode(sc: dict, condition: str, log) -> dict:
    limits = {"buyer": sc["budget"], "seller": sc["reserve"]}
    systems = {r: acl.system_prompt(r, sc["item"], limits[r], condition) for r in ("buyer", "seller")}
    history = {"buyer": [{"role": "user", "content": OPENER}], "seller": []}
    transcript, last_price = [], {"buyer": None, "seller": None}
    agent_m, reader_m = Meter(), Meter()
    ep = {"outcome": "open", "price": None, "turns": 0, "format_errors": 0,
          "accept_without_price": 0, "trailing_text": 0}
    role, other = "buyer", "seller"
    log(f"=== scenario {sc['id']} ({sc['item']}) reserve={sc['reserve']} budget={sc['budget']} condition={condition}")
    for _ in range(MAX_TURNS):
        text = call_model(systems[role], history[role], agent_m)
        ep["turns"] += 1
        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        transcript.append((role, text))
        log(f"[{role}] {text}")
        r = acl.read(condition, text, transcript, reader_m)
        label = {"performative": r["perf"], "price": r["price"]} if r["ok"] else None
        log(f"  [{'parse' if condition == 'structured' else 'reader' if condition == 'free' else 'tag'}] "
            f"{label}   ({r['raw'][:160]})")
        if r["trailing"]:
            ep["trailing_text"] += 1
            log(f"  [trailing text ignored by the parser] {r['trailing'][:160]}")
        if not r["ok"]:
            ep["format_errors"] += 1                  # the message still reaches the other side
        elif r["perf"] == "propose":
            last_price[role] = r["price"]
        elif r["perf"] == "accept-proposal":
            if last_price[other] is None:
                ep["accept_without_price"] += 1       # nothing recorded to accept: episode goes on
                log("  [harness] accept-proposal but no recorded price from the other side; continuing")
            else:
                ep["outcome"], ep["price"] = "deal", last_price[other]
                break
        elif r["perf"] == "refuse":
            ep["outcome"] = "no_deal"
            break
        role, other = other, role
    possible = sc["reserve"] <= sc["budget"]
    violation = int(ep["outcome"] == "deal" and not (sc["reserve"] <= ep["price"] <= sc["budget"]))
    correct = int((ep["outcome"] == "deal" and not violation) if possible else ep["outcome"] == "no_deal")
    row = {"condition": condition, "scenario": str(sc["id"]), "deal_possible": int(possible),
           "outcome": ep["outcome"], "price": "" if ep["price"] is None else ep["price"],
           "correct": correct, "violation": violation, "turns": ep["turns"],
           "format_errors": ep["format_errors"], "reader_calls": reader_m.calls,
           "note": (f"model={llm.MODEL} provider={llm.PROVIDER} temp={llm.TEMPERATURE} effort={llm.REASONING_EFFORT} "
                    f"agent_calls={agent_m.calls} tokens={agent_m.prompt_tokens + reader_m.prompt_tokens}"
                    f"+{agent_m.completion_tokens + reader_m.completion_tokens} "
                    f"accept_without_price={ep['accept_without_price']} trailing_text={ep['trailing_text']}")}
    log(f"[result] outcome={row['outcome']} price={row['price']} correct={correct} violation={violation} "
        f"turns={row['turns']} format_errors={row['format_errors']} reader_calls={row['reader_calls']}")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", nargs="+", default=CONDITIONS, choices=CONDITIONS)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--scenarios", default=str(HERE / "scenarios.json"))
    ap.add_argument("--results", default=str(HERE / "results.csv"))
    ap.add_argument("--logs", default=str(HERE / "logs"))
    a = ap.parse_args()

    scenarios = json.loads(Path(a.scenarios).read_text(encoding="utf-8"))
    res, logdir = Path(a.results), Path(a.logs)
    logdir.mkdir(exist_ok=True)
    if not res.exists():
        with res.open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(HEADER)
    with res.open(encoding="utf-8", newline="") as f:
        done = {(r["run"], r["scenario"]) for r in csv.DictReader(f)}

    for cond in a.conditions:
        for rep in range(1, a.repeats + 1):
            run = f"{cond}-{rep:02d}"
            todo = [s for s in scenarios if (run, str(s["id"])) not in done]
            if not todo:
                continue
            path = logdir / f"{run}.txt"
            with path.open("a", encoding="utf-8") as lf:
                def log(s):
                    print(s, flush=True)
                    lf.write(s + "\n")
                    lf.flush()
                log(f"# run={run} provider={llm.PROVIDER} base_url={llm.BASE_URL} model={llm.MODEL} "
                    f"temperature={llm.TEMPERATURE} reasoning_effort={llm.REASONING_EFFORT} turn_limit={MAX_TURNS}")
                for sc in todo:
                    try:
                        row = episode(sc, cond, log)
                    except LLMError as e:
                        log(f"[result] ABORT (LLM error, not recorded) {e}")
                        raise SystemExit(f"LLM error: {e}")
                    except Exception as e:           # crashed episode stays, blank fields
                        log(f"[result] CRASH {type(e).__name__}: {e}")
                        row = {k: "" for k in HEADER}
                        row.update(condition=cond, scenario=str(sc["id"]),
                                   deal_possible=int(sc["reserve"] <= sc["budget"]),
                                   note=f"crash: {type(e).__name__}: {str(e)[:150]}")
                    row["run"] = run
                    with res.open("a", encoding="utf-8", newline="") as f:
                        csv.DictWriter(f, fieldnames=HEADER).writerow(row)


if __name__ == "__main__":
    main()
