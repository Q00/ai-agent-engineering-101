"""Reconstruct registered prices at every message without reinterpreting the logs."""
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ITEMS = {1: "자전거", 2: "탁상등", 3: "교재", 4: "키보드"}


def read_csv(path):
    with path.open(newline="") as f: return list(csv.DictReader(f))


def dataset():
    sources = [("en", "8", ROOT / "results.csv"),
               ("en", "none", ROOT / "turn_limit/runs/unlimited-deepseek-20260922/results.csv"),
               ("ko", None, ROOT / "language/runs/korean-deepseek-20260922/results.csv")]
    rows = []
    for language, policy, path in sources:
        rows.extend({**r, "language": language, "turn_policy": policy or r["turn_policy"]} for r in read_csv(path))
    assert len(rows) == 144
    scenarios = {str(s["id"]): s for s in json.loads((ROOT / "scenarios.json").read_text())}
    by_run = {}
    for run in {r["run"] for r in rows}:
        by_run[run] = [(i, json.loads(line)) for i, line in enumerate((ROOT / "logs" / f"{run}.jsonl").read_text().splitlines(), 1)]
    episodes, points, violations = [], [], []
    for row in rows:
        sc = scenarios[row["scenario"]]
        events = [(i, e) for i, e in by_run[row["run"]] if str(e["scenario"]) == row["scenario"]]
        trace, state, origin = [], {"buyer": None, "seller": None}, {"buyer": None, "seller": None}
        for line, e in events:
            if e["event"] == "message":
                trace.append({"run": row["run"], "language": row["language"], "turn_policy": row["turn_policy"],
                    "condition": row["condition"], "scenario": row["scenario"], "turn": e["turn"], "speaker": e["speaker"],
                    "timestamp": e["time"], "message_line": line, "text": e["text"], "parse_line": "", "ok": "",
                    "performative": "", "interpreted_price": None, "error": "", "reader_text": "",
                    "registered_proposal": None, "buyer_price": state["buyer"], "seller_price": state["seller"],
                    "proposal_outside_own_limit": False, "recorded_deal": None, "recorded_violation": False})
            elif e["event"] == "reader_output":
                trace[-1]["reader_text"] = e["text"]
            elif e["event"] == "parse_result":
                point = trace[-1]
                assert point["turn"] == e["turn"]
                point.update(parse_line=line, ok=e["ok"], performative=e.get("performative", ""),
                             interpreted_price=e.get("price"), error=e.get("error", ""))
                role = point["speaker"]
                if e["ok"] and e["performative"] == "propose":
                    state[role], origin[role] = e["price"], dict(point)
                    point["registered_proposal"] = e["price"]
                    point["proposal_outside_own_limit"] = (role == "buyer" and e["price"] > sc["budget"]) or (role == "seller" and e["price"] < sc["reserve"])
                point.update(buyer_price=state["buyer"], seller_price=state["seller"])
        assert len(trace) == int(row["turns"])
        if row["outcome"] == "deal":
            last = trace[-1]
            other = "seller" if last["speaker"] == "buyer" else "buyer"
            assert last["ok"] and last["performative"] == "accept-proposal"
            assert int(row["price"]) == state[other]
            outside = not sc["reserve"] <= state[other] <= sc["budget"]
            assert outside == (row["violation"] == "1")
            last.update(recorded_deal=state[other], recorded_violation=outside)
            if outside:
                source = origin[other]
                violations.append({"run": row["run"], "language": row["language"], "turn_policy": row["turn_policy"],
                    "condition": row["condition"], "scenario": row["scenario"], "reserve": sc["reserve"], "budget": sc["budget"],
                    "violation_turn": last["turn"], "recorded_price": state[other],
                    "violated_limit": "buyer_budget" if state[other] > sc["budget"] else "seller_reserve",
                    "accepting_speaker": last["speaker"], "source_proposal_turn": source["turn"],
                    "source_proposal_speaker": source["speaker"], "source_message_line": source["message_line"],
                    "accept_message_line": last["message_line"], "accept_parse_line": last["parse_line"],
                    "source_text": source["text"], "source_reader": source["reader_text"],
                    "accept_text": last["text"], "accept_reader": last["reader_text"]})
        episodes.append({"row": row, "scenario": sc, "trace": trace})
        points.extend(trace)
    assert len(violations) == sum(r["violation"] == "1" for r in rows)
    return episodes, points, violations


def save_csv(path, rows):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


if __name__ == "__main__":
    episodes, points, violations = dataset()
    save_csv(HERE / "price_events.csv", points)
    save_csv(HERE / "violations.csv", violations)
    (HERE / "episodes.json").write_text(json.dumps(episodes, ensure_ascii=False, indent=2) + "\n")
    print(f"Reconstructed {len(episodes)} episodes, {len(points)} messages, {len(violations)} recorded violations")
