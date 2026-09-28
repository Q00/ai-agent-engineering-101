"""The three message formats under three model settings, on the original 8-message rule.

DeepSeek with reasoning off is the submitted 8-turn run. DeepSeek with reasoning low (the shotgun control)
and Luna low ran 30 messages; their stored transcripts are re-read with the untouched 8-message runner,
so every row uses the same limit. Failure counts over the full 30 messages are listed separately.
"""
from collections import Counter
import csv
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "lab"))
import experiment as english  # MAX_TURNS = 8

SCENARIOS = {str(s["id"]): s for s in json.loads((ROOT / "scenarios.json").read_text())}
SETTINGS = [
    ("DeepSeek · 추론 끔", ROOT / "results.csv", lambda r: ROOT / "logs" / f"{r['run']}.jsonl", False, lambda r: True),
    ("DeepSeek · 추론 low", HERE / "runs/deepseek-control-ep-20260928/results.csv",
     lambda r: ROOT / "logs" / f"{r['run']}-s{r['scenario']}.jsonl", True, lambda r: True),
    ("Luna · 추론 low", ROOT / "reasoning_effort/runs/luna-effort-20260928/results.csv",
     lambda r: ROOT / "logs" / f"{r['run']}.jsonl", True, lambda r: r["reasoning_effort"] == "low"),
]
KINDS = {"Missing leading performative tag": "tag_missing",
         "Acceptance without a recorded proposal from the other party": "accept_without_proposal",
         "Proposal has no integer price": "propose_without_price"}


def events(path, scenario):
    return [e for e in map(json.loads, path.read_text().splitlines()) if str(e["scenario"]) == scenario]


def eight(condition, row, evs):
    replies = iter(e for e in evs if e["event"] in ("message", "reader_output"))
    result = {"deal_possible": int(row["deal_possible"])}
    english.negotiate(SCENARIOS[row["scenario"]], condition, lambda *a, **k: next(replies)["text"],
                      lambda *a, **k: None, result)
    return result


def main():
    out = []
    for label, csv_path, log_of, replay, keep in SETTINGS:
        with csv_path.open(newline="") as f:
            rows = [r for r in csv.DictReader(f) if keep(r)]
        for condition in english.CONDITIONS:
            group = [r for r in rows if r["condition"] == condition]
            correct = fe = opened = empty = 0
            kinds = Counter()
            for r in group:
                evs = events(log_of(r), r["scenario"])
                res = eight(condition, r, evs) if replay else r
                correct += int(res["correct"] or 0)
                fe += int(res["format_errors"])
                opened += res["outcome"] == "open"
                empty += sum(e["event"] == "message" and not e["text"].strip() for e in evs)
                for e in evs:
                    if e["event"] == "parse_result" and not e["ok"]:
                        kinds[KINDS.get(e["error"], "unparsable_json")] += 1
            out.append({"setting": label, "condition": condition, "episodes": len(group), "correct_8": correct,
                        "format_errors_8": fe, "open_8": opened, "empty_messages_all": empty,
                        **{k: kinds[k] for k in ("tag_missing", "accept_without_proposal",
                                                   "propose_without_price", "unparsable_json")}})
    path = HERE / "runs" / "format-by-model.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]), lineterminator="\n")
        w.writeheader(); w.writerows(out)
    for o in out:
        print(o)


if __name__ == "__main__":
    main()
