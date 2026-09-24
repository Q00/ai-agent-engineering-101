"""LLM negotiation with an auditable offer ledger and explicit references.

PowerShell:
  $py = "<Codex Python runtime path>"
  & $py .\verified_protocol.py --all --fake
  & $py .\verified_protocol.py --all

This is an exploratory protocol experiment. It does not change the graded
Week 04 runner or its results.csv. Every act is JSON. Proposals get unique
IDs; acceptance must name an outstanding offer ID, and the harness checks
that the offer was made by the other party and remains within both limits.
The harness verifies representation and protocol consistency; it cannot prove
that an LLM's natural-language rationale is sincere.
"""
import argparse
import csv
import json
import os
from datetime import datetime
import sys
import traceback
from pathlib import Path

import chat

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios.json"
RESULTS = HERE / "verified_results.csv"
LOGS = HERE / "verified_logs"
TURN_LIMIT = 8
REPEATS = 3
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
          "correct", "violation", "turns", "format_errors", "reader_calls", "note"]

SYSTEMS = {
    "buyer": (
        "You are the buyer negotiating {item}. Your private maximum is {limit}; "
        "never propose above it. Return exactly one JSON object with keys "
        '"act", "offer_id", "price", "responds_to", "reason". Acts are '
        '"propose", "accept", "reject", "walk_away". For propose, set offer_id '
        'to a new short ID, price to your integer offer, responds_to to null. '
        'For accept, set offer_id and price to null and responds_to to the exact '
        'ID of the other party\'s current offer. For reject, offer_id and '
        'responds_to are null; you may include a counter-price in price. For '
        'walk_away all ID/price fields are null. Do not accept without an offer ID.'),
    "seller": (
        "You are the seller negotiating {item}. Your private minimum is {limit}; "
        "never propose below it. Return exactly one JSON object with keys "
        '"act", "offer_id", "price", "responds_to", "reason". Acts are '
        '"propose", "accept", "reject", "walk_away". For propose, set offer_id '
        'to a new short ID, price to your integer offer, responds_to to null. '
        'For accept, set offer_id and price to null and responds_to to the exact '
        'ID of the other party\'s current offer. For reject, offer_id and '
        'responds_to are null; you may include a counter-price in price. For '
        'walk_away all ID/price fields are null. Do not accept without an offer ID.'),
}


def parse_message(raw):
    """Strict protocol parser; never infer acts or prices from rationale text."""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, "invalid JSON"
    if not isinstance(obj, dict):
        return None, "message must be a JSON object"
    required = {"act", "offer_id", "price", "responds_to", "reason"}
    if set(obj) != required:
        return None, f"fields must be exactly {sorted(required)}"
    act = obj["act"]
    if act not in {"propose", "accept", "reject", "walk_away"}:
        return None, "unknown act"
    if not isinstance(obj["reason"], str):
        return None, "reason must be a string"
    price = obj["price"]
    if price is not None and (isinstance(price, bool) or not isinstance(price, int) or price < 0):
        return None, "price must be a non-negative integer or null"
    if act == "propose":
        if not isinstance(obj["offer_id"], str) or not obj["offer_id"].strip():
            return None, "propose requires a non-empty offer_id"
        if price is None or obj["responds_to"] is not None:
            return None, "propose requires price and responds_to=null"
    elif act == "accept":
        if not isinstance(obj["responds_to"], str) or not obj["responds_to"].strip():
            return None, "accept requires responds_to offer ID"
        if obj["offer_id"] is not None or price is not None:
            return None, "accept must not introduce an offer ID or price"
    elif act == "reject":
        if obj["offer_id"] is not None or obj["responds_to"] is not None:
            return None, "reject must not introduce or accept an offer ID"
    elif any(obj[key] is not None for key in ("offer_id", "price", "responds_to")):
        return None, "walk_away requires null ID and price fields"
    return obj, "ok"


def fake_ask(scenario):
    """Offline scripted policy used only to exercise the protocol checks."""
    counts = {"buyer": 0, "seller": 0}
    def ask(system, messages, meter, role=None):
        role = role or ("buyer" if "buyer negotiating" in system.lower() else "seller")
        counts[role] += 1
        n = counts[role]
        view = json.loads(messages[-1]["content"])
        opposing = view["current_offers"]["seller" if role == "buyer" else "buyer"]
        if opposing is not None:
            open_offer = view["offer_ledger"].get(opposing)
            within_limit = (open_offer is not None and
                            scenario["reserve"] <= open_offer["price"] <= scenario["budget"])
            if within_limit:
                obj = {"act": "accept", "offer_id": None, "price": None,
                       "responds_to": opposing, "reason": "accept feasible opposing offer"}
                return json.dumps(obj)
        if role == "buyer":
            price = min(scenario["budget"], 100 + 55 * n)
            obj = {"act": "propose", "offer_id": f"B{n}", "price": price,
                   "responds_to": None, "reason": "scripted buyer offer"}
        else:
            price = max(scenario["reserve"], 500 - 70 * n)
            obj = {"act": "propose", "offer_id": f"S{n}", "price": price,
                   "responds_to": None, "reason": "scripted seller offer"}
        return json.dumps(obj)
    return ask


def run_episode(scenario, ask, meter, log):
    reserve, budget = scenario["reserve"], scenario["budget"]
    systems = {role: template.format(item=scenario["item"],
                                     limit=budget if role == "buyer" else reserve)
               for role, template in SYSTEMS.items()}
    transcripts = {"buyer": [], "seller": []}
    offers = {}
    current = {"buyer": None, "seller": None}
    used_ids = set()
    turns = format_errors = 0
    outcome, deal_price = "open", None
    speaker = "buyer"
    while turns < TURN_LIMIT:
        own_limit = budget if speaker == "buyer" else reserve
        view = {"your_role": speaker, "your_limit": own_limit,
                "current_offers": current,
                "offer_ledger": offers,
                "open_offer_ids": [oid for oid, offer in offers.items() if offer["status"] == "open"],
                "messages_remaining": TURN_LIMIT - turns}
        messages = transcripts[speaker] + [{"role": "user", "content": json.dumps(view)}]
        raw = ask(systems[speaker], messages, meter, role=speaker)
        turns += 1
        obj, why = parse_message(raw)
        log(f"  [{turns}] {speaker}: {raw}")
        if obj is None:
            format_errors += 1
            log(f"       verifier: reject malformed message ({why})")
        else:
            act = obj["act"]
            if act == "propose":
                oid, price = obj["offer_id"], obj["price"]
                if oid in used_ids:
                    format_errors += 1
                    log(f"       verifier: reject reused offer_id {oid}")
                elif speaker == "buyer" and price > budget:
                    format_errors += 1
                    log(f"       verifier: reject buyer offer {price} above own limit {budget}")
                elif speaker == "seller" and price < reserve:
                    format_errors += 1
                    log(f"       verifier: reject seller offer {price} below own limit {reserve}")
                else:
                    used_ids.add(oid)
                    if current[speaker] in offers:
                        offers[current[speaker]]["status"] = "superseded"
                    offers[oid] = {"speaker": speaker, "price": price, "status": "open"}
                    current[speaker] = oid
                    log(f"       verifier: record offer_id={oid} price={price}")
            elif act == "accept":
                oid = obj["responds_to"]
                record = offers.get(oid)
                if record is None or record["status"] != "open":
                    format_errors += 1
                    log(f"       verifier: reject acceptance; {oid} is not an open offer")
                elif record["speaker"] == speaker:
                    format_errors += 1
                    log(f"       verifier: reject acceptance; cannot accept own offer {oid}")
                elif record["price"] < reserve or record["price"] > budget:
                    format_errors += 1
                    log(f"       verifier: reject acceptance; {oid} price violates a private limit")
                else:
                    outcome, deal_price = "deal", record["price"]
                    record["status"] = "accepted"
                    log(f"       verifier: valid accept responds_to={oid}; close at {deal_price}")
                    break
            elif act == "reject":
                if obj["price"] is not None:
                    if speaker == "buyer" and obj["price"] > budget:
                        format_errors += 1
                        log("       verifier: reject counter-price above buyer limit")
                    elif speaker == "seller" and obj["price"] < reserve:
                        format_errors += 1
                        log("       verifier: reject counter-price below seller limit")
                    else:
                        log(f"       verifier: valid rejection with counter-price {obj['price']}")
                else:
                    log("       verifier: valid rejection without counter-price")
            else:
                outcome = "no_deal"
                log("       verifier: walk_away; negotiation ends")
                break
        # Keep the actual message history visible to both agents. The verifier's
        # ledger state is also provided next turn as explicit, machine-readable context.
        other = "seller" if speaker == "buyer" else "buyer"
        transcripts[speaker].append({"role": "assistant", "content": raw})
        transcripts[other].append({"role": "user", "content": raw})
        speaker = other
    possible = int(reserve <= budget)
    violation = int(outcome == "deal" and not reserve <= deal_price <= budget)
    correct = int((outcome == "deal" and possible and not violation) or
                  (outcome != "deal" and not possible))
    return {"deal_possible": possible, "outcome": outcome,
            "price": "" if deal_price is None else deal_price,
            "correct": correct, "violation": violation, "turns": turns,
            "format_errors": format_errors, "reader_calls": 0,
            "note": "offer ledger; accept must reference open opposing offer ID"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="run all four scenarios x three repeats (default if no --repeat)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--only", help="one scenario ID for a pilot")
    parser.add_argument("--fake", action="store_true", help="offline protocol mechanics check")
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--logs", type=Path, default=LOGS)
    args = parser.parse_args()
    if not args.fake and chat.BACKEND == "api":
        try:
            import openai  # noqa: F401 - fail once before writing failed episode rows
        except ImportError:
            parser.error(
                "the selected Python environment has no 'openai' package. "
                "Install it with: python -m pip install -r requirements.txt "
                "(or install it into the interpreter selected by $py)."
            )
        if not os.environ.get("OPENAI_API_KEY"):
            parser.error("OPENAI_API_KEY is not set in this PowerShell session")
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if args.only:
        scenarios = [scenario for scenario in scenarios if str(scenario["id"]) == args.only]
        if not scenarios:
            parser.error(f"unknown scenario ID {args.only!r}")
    run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    repeat_plan = range(1, args.repeats + 1) if args.all or len(sys.argv) == 1 else [args.repeat]
    args.logs.mkdir(parents=True, exist_ok=True)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    with args.results.open("a", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=HEADER)
        if output.tell() == 0:
            writer.writeheader()
        for repeat in repeat_plan:
            run_id = f"verified-{run_stamp}-{repeat}"
            log_lines = [f"run={run_id} turn_limit={TURN_LIMIT}",
                         "protocol=JSON acts + unique offer IDs + explicit acceptance reference",
                         "verification=field schema, ID ownership/status, private limits"]
            for scenario in scenarios:
                sid = str(scenario["id"])
                meter = chat.Meter()
                def log(message=""):
                    print(message, flush=True)
                    log_lines.append(message)
                log(f"scenario {sid}: reserve={scenario['reserve']} budget={scenario['budget']}")
                try:
                    ask = fake_ask(scenario) if args.fake else chat.ask
                    result = run_episode(scenario, ask, meter, log)
                except Exception as error:
                    traceback.print_exc()
                    result = {key: "" for key in HEADER}
                    result["note"] = f"{type(error).__name__}: {str(error)[:160]}"
                result.update(run=run_id, condition="verified", scenario=sid)
                result["note"] += f"; model_calls={meter.calls} tokens={meter.tokens}"
                writer.writerow(result)
                output.flush()
                log(f"  -> outcome={result['outcome']} price={result['price']} correct={result['correct']} violation={result['violation']} turns={result['turns']} format_errors={result['format_errors']}")
            (args.logs / f"{run_id}.txt").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return 0


def self_check():
    """Small deterministic checks for schema and acceptance verification."""
    valid_propose = {"act": "propose", "offer_id": "B1", "price": 200,
                     "responds_to": None, "reason": "offer"}
    parsed, reason = parse_message(json.dumps(valid_propose))
    assert parsed == valid_propose and reason == "ok"
    invalid_accept = {"act": "accept", "offer_id": None, "price": 200,
                      "responds_to": "B1", "reason": "accept"}
    assert parse_message(json.dumps(invalid_accept))[0] is None
    stale = {"act": "accept", "offer_id": None, "price": None,
             "responds_to": "unknown", "reason": "accept"}
    assert parse_message(json.dumps(stale))[0] == stale
    scenario = {"id": "unit", "item": "test item", "reserve": 120, "budget": 300}
    lines = []
    class MeterStub:
        calls = tokens = 0
    meter = MeterStub()
    # Scripted buyer proposal followed by seller acceptance that references it.
    replies = iter([
        json.dumps({"act": "propose", "offer_id": "B1", "price": 200,
                    "responds_to": None, "reason": "offer"}),
        json.dumps({"act": "accept", "offer_id": None, "price": None,
                    "responds_to": "B1", "reason": "accept"}),
    ])
    def ask(_system, _messages, _meter, role=None):
        return next(replies)
    result = run_episode(scenario, ask, meter, lines.append)
    assert result["outcome"] == "deal" and result["price"] == 200
    assert result["violation"] == 0 and result["format_errors"] == 0
    # An acceptance that refers to a nonexistent offer cannot close a deal.
    replies = iter([
        json.dumps({"act": "accept", "offer_id": None, "price": None,
                    "responds_to": "imagined-430", "reason": "accept"}),
    ] * TURN_LIMIT)
    result = run_episode(scenario, ask, meter, lambda _line: None)
    assert result["outcome"] == "open" and result["format_errors"] == TURN_LIMIT
    print("verified protocol self-check passed: schema, valid acceptance, unknown offer rejection")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--self-check":
        self_check()
        sys.exit(0)
    sys.exit(main())
