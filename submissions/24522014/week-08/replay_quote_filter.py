"""Read-only replay of the quote filter over logged R3 quotes, joined with the kit's payment records.

This estimates what the filter would have done. It does not show what a seller would have done next.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from buyer_funnel import ROOT, load_runs, read_json, stamp
from quote_terms import check_quote, structured_terms
from settlement_memory import payment_outcomes


def quotes(directory):
    """Input-required replies of one negotiation, unwrapping result.task, in order."""
    found = []
    for path in sorted(directory.glob("*.response.txt")):
        reply = read_json(path)
        result = reply.get("result") if isinstance(reply, dict) else None
        task = result.get("task", result) if isinstance(result, dict) else None
        if isinstance(task, dict) and (task.get("status") or {}).get("state") == "TASK_STATE_INPUT_REQUIRED":
            message = task["status"].get("message") or {}
            text = " ".join(p.get("text", "") for p in message.get("parts", []) if isinstance(p, dict))
            found.append((task, text))
    return found


def refusal_kind(reason):
    reason = str(reason)
    return "shelf_life" if "shelf life" in reason else "grade" if "grade" in reason else "other"


def replay(root, since, until=None):
    root = Path(root)
    payments = payment_outcomes(root / ".market/payments.jsonl")
    tally, by_reason, outcomes, misses = Counter(), Counter(), Counter(), Counter()
    false_blocks, unmatched_refusals = [], []
    shelf, grade, caught = Counter(), Counter(), Counter()
    vague_examples = Counter()
    for name, record in load_runs(root, since, until):
        accept = record.get("accept_conditions")
        if record.get("outcome") == "research_skipped" or not accept or not record.get("buyer_log_dir"):
            continue
        directory = root / "logs/buyer" / Path(str(record["buyer_log_dir"]).replace("\\", "/")).name
        replies = quotes(directory)
        if not replies:
            continue
        tally["negotiations_with_quote"] += 1
        now = datetime.strptime(name, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp()
        blocked = None
        for turn, (task, text) in enumerate(replies):
            tally["quotes_checked"] += 1
            check = check_quote(task, text, accept, now)
            if check["conflicts"]:
                tally["quotes_with_conflict"] += 1
            if turn == 0:  # Coverage on opening quotes: counteroffers usually restate only a price.
                structured_grades, structured_hours = structured_terms(task, now)
                grade["structured" if structured_grades else "text" if check["grade"] else "none"] += 1
                if "min_hours_left" in accept:
                    tally["food_first_quotes"] += 1
                    note = check["text_hours_note"]
                    shelf["structured" if structured_hours else "text_" + note] += 1
                    if not structured_hours and note == "vague":
                        vague_examples[re.sub(r"\d[\d,.]*", "#", " ".join(
                            re.findall(r"[^.,|]*(?:shelf|유통기한|소비기한|close to its date)[^.,|]*", text, re.I)))[:90]] += 1
            if check["reason_code"] and blocked is None:
                blocked = check
        payment = payments.get(record.get("order"))
        if payment and payment.get("paid") is True:
            tally["paid_true_negotiations"] += 1
        refused = payment if payment and payment.get("paid") is not True else None
        if blocked:
            code = blocked["reason_code"]
            tally["would_filter"] += 1
            by_reason[f"{code}|basis={blocked['basis']}"] += 1
            if not payment:
                outcomes["no_payment_row"] += 1
            elif payment.get("paid") is True:
                outcomes["paid_true_false_block"] += 1
                false_blocks.append({"run": name, "log": directory.name, "reason": code, "basis": blocked["basis"],
                                     "grade": blocked["grade"], "hours_left": blocked["hours_left"],
                                     "accept": accept, "ledger": payment.get("ledger"), "item": payment.get("item")})
            else:
                kind = refusal_kind(payment.get("reason"))
                caught[kind] += 1
                wanted = "shelf_life" if code == "quote_shelf_life_short" else "grade"
                outcomes["refused_same_cause" if kind == wanted else f"refused_other_cause:{kind}"] += 1
        elif refused:
            kind = refusal_kind(refused.get("reason"))
            misses[kind] += 1
            if kind != "other" and len(unmatched_refusals) < 25:
                task, text = replies[-1]
                check = check_quote(task, text, accept, now)
                unmatched_refusals.append({"log": directory.name, "kind": kind, "grade_seen": check["grade"],
                                           "hours_seen": check["hours_left"], "note": check["text_hours_note"],
                                           "reason": str(refused.get("reason"))[:90], "text_tail": text[-110:]})
    return {"window": {"since": since, "until": until}, "tally": dict(tally),
            "would_filter_by_reason_and_basis": dict(by_reason.most_common()),
            "would_filter_outcomes": dict(outcomes.most_common()), "false_blocks": false_blocks,
            "refused_and_filtered_by_refusal_kind": dict(caught.most_common()),
            "refused_but_not_filtered": dict(misses.most_common()),
            "refused_but_not_filtered_examples": unmatched_refusals,
            "coverage_first_quotes": {"grade": dict(grade.most_common()),
                                      "shelf_life_food_only": dict(shelf.most_common()),
                                      "vague_shelf_wording": dict(vague_examples.most_common(15))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until")
    args = parser.parse_args()
    report = replay(ROOT, stamp(args.since), stamp(args.until) if args.until else None)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
