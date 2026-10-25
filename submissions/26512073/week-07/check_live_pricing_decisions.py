"""Real harness calls on SYNTHETIC fixtures, never evidence of live market trades."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pricing_state
import seller_decision
import seller_memory


BASE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    run = BASE / "logs" / "challenge-1" / ("synthetic-real-harness-" + stamp)
    run.mkdir(parents=True)
    live = [pricing_state.STATE_PATH, seller_memory.HISTORY_PATH, BASE / "buyer_memory.jsonl"]
    before = {str(path): digest(path) for path in live}
    outcomes = []
    print(f"SYNTHETIC evidence, REAL model replies. Directory: {run}", flush=True)
    try:
        for action, expected in [("raise", 118), ("lower", 112), ("hold", 115)]:
            folder = run / action
            folder.mkdir()
            history_path = folder / "synthetic-history.jsonl"
            state_path = folder / "synthetic-state.json"
            reviews_path = folder / "synthetic-reviews.json"
            rows = []
            for i in range(6):
                row = {"task": f"SYNTHETIC-{action}-{i}", "item": "lamp",
                       "source": "synthetic_experiment", "policy": pricing_state.POLICY,
                       "opening_ask": 115, "ask": 115,
                       "time": datetime.now(timezone.utc).isoformat()}
                if action == "hold":
                    row.update(event="rejected", offer=90)
                else:
                    row.update(event="deal", price=115 if action == "raise" and i < 4 else 100)
                rows.append(row)
            reviews = []
            if action == "lower":
                reviews = [{"author": f"SYNTHETIC-reviewer-{i}", "stars": 3,
                            "review": text, "ts": int(datetime.now(timezone.utc).timestamp())}
                           for i, text in enumerate([
                               "Lamp opening ask 115 was high compared with nearby asks of 105. I negotiated to 100.",
                               "This lamp started at 115; similar lamps were offered for 105. We agreed at 100.",
                               "I found the lamp's 115 opening price too high relative to other 105 offers; bought at 100.",
                           ])]
            reviews_path.write_text(json.dumps({"synthetic": True, "data": reviews}, indent=2), encoding="utf-8")
            with patch.object(pricing_state, "STATE_PATH", state_path), \
                    patch.object(seller_memory, "HISTORY_PATH", history_path), \
                    patch.object(seller_memory, "REVIEWS_PATH", reviews_path):
                history_path.write_text("\n".join(json.dumps(row) for row in rows[:5]) + "\n", encoding="utf-8")
                logs_before = set((BASE / "development_logs").glob("harness_*.txt"))
                assert seller_decision.decide_opening("lamp", 100)["price"] == 115
                assert logs_before == set((BASE / "development_logs").glob("harness_*.txt"))
                with history_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(rows[5]) + "\n")
                first = seller_decision.decide_opening("lamp", 100)
                state = pricing_state.load_item_state("lamp", 100)
                logs_after_review = set((BASE / "development_logs").glob("harness_*.txt"))
                again = seller_decision.decide_opening("lamp", 100)
                no_reuse = (again["price"] == first["price"] and
                            logs_after_review == set((BASE / "development_logs").glob("harness_*.txt")))
                outcome = {"scenario": action, "synthetic_evidence": True,
                           "model_reply_is_real": True, "expected_price": expected,
                           "observed": first, "review": state["last_review"],
                           "no_model_call_before_six": True, "no_reuse": no_reuse,
                           "matches_expectation": first["price"] == expected and no_reuse}
                (folder / "result.json").write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
                outcomes.append(outcome)
                print(json.dumps(outcome), flush=True)
    finally:
        unchanged = before == {str(path): digest(path) for path in live}
        summary = {"synthetic_evidence": True, "outcomes": outcomes,
                   "live_memory_unchanged": unchanged,
                   "passed": len(outcomes) == 3 and all(row["matches_expectation"] for row in outcomes) and unchanged}
        (run / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"Live memory unchanged: {unchanged}; scenarios passed: "
              f"{sum(row['matches_expectation'] for row in outcomes)}/3", flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
